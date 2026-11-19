// What a station's status means for the person at the computer: a one-line state for the list, and the events worth
// a desktop notification. No Electron here, so it can be tested on its own (node --test).
'use strict';

function fmt(s) {
  s = Math.max(0, Math.floor(s || 0));
  return Math.floor(s / 3600) + ':' + String(Math.floor((s % 3600) / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

// {label, tone, detail}: tone is rec, go, wait, bad or idle, for the list's lamp
function describe(st) {
  if (!st) return { label: 'Not answering', tone: 'bad', detail: 'Is the station on, and on this network?' };
  const r = st.recording;
  switch (st.step) {
    case 'recording': return { label: 'Recording ' + fmt(r && r.elapsed), tone: 'rec', detail: st.item || '' };
    case 'finishing': return { label: 'Finishing the files', tone: 'wait', detail: '' };
    case 'standby':
    case 'press_play': return { label: 'Ready: press play', tone: 'go', detail: nextName(st) };
    case 'press_start': return { label: 'Press REC, then play', tone: 'go', detail: nextName(st) };
    case 'wait': return { label: 'Wait: moving the last master', tone: 'wait', detail: '' };
    case 'plug_device': return { label: 'Plug in the capture stick', tone: 'bad', detail: '' };
    case 'connect_disk': return { label: 'Connect the capture disk', tone: 'bad', detail: '' };
    case 'error': return { label: 'Needs attention', tone: 'bad', detail: st.message || '' };
    default: return { label: st.message || 'Idle', tone: 'idle', detail: '' };
  }
}

function nextName(st) {
  const d = st.details || {};
  if (!d.title) return '';
  return 'Next: ' + (d.id && d.id !== d.title ? d.id + ' · ' : '') + d.title;
}

// The notifications between two statuses of one station: [{title, body}]
function events(prev, cur, name) {
  const out = [];
  if (!cur) {
    if (prev) out.push({ title: name + ' stopped answering', body: 'Check that the station is on and on this network.' });
    return out;
  }
  if (!prev) return out;
  const was = prev.step, now = cur.step;
  if ((was === 'recording' || was === 'finishing') && now !== 'recording' && now !== 'finishing' && now !== 'error') {
    out.push({ title: 'Capture finished on ' + name, body: cur.last_result || 'The capture is done.' });
  }
  if (now === 'error' && was !== 'error') out.push({ title: name + ' needs attention', body: cur.message || '' });
  const before = new Set(prev.held || []);
  const fresh = (cur.held || []).filter((h) => !before.has(h));
  if (fresh.length) {
    out.push({ title: fresh.length === 1 ? 'A capture is waiting for you' : fresh.length + ' captures are waiting for you',
               body: 'Watch ' + (fresh.length === 1 ? 'it' : 'them') + ' on ' + name + ', then press Keep or Bad take.' });
  }
  return out;
}

// A typed address as the station's page URL: "videocap.local", "10.0.0.5:8091", "http://x/" ...
function normalize(addr) {
  let a = String(addr || '').trim();
  if (!a) return null;
  if (!/^https?:\/\//i.test(a)) a = 'http://' + a;
  try {
    const u = new URL(a);
    return u.protocol + '//' + u.host + '/';
  } catch (e) {
    return null;
  }
}

module.exports = { describe, events, normalize, fmt };
