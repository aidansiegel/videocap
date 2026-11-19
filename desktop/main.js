// Videocap desktop: finds the stations on the network (their _videocap._tcp service), lists them with what each is
// doing, opens a station's page in its own window, and sends a desktop notification when a capture finishes, waits
// for review, or a station needs attention. The capture itself happens on the station.
'use strict';
const { app, BrowserWindow, ipcMain, Notification, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { Bonjour } = require('bonjour-service');
const { describe, events, normalize } = require('./stations');

const stations = new Map();   // url -> {url, name, found: 'network'|'added', status, ok}
let listWin = null;
const pageWins = new Map();   // url -> BrowserWindow
const savedFile = () => path.join(app.getPath('userData'), 'stations.json');

function loadSaved() {
  try {
    for (const s of JSON.parse(fs.readFileSync(savedFile(), 'utf8'))) {
      const url = normalize(s.url);
      if (url) stations.set(url, { url, name: s.name || new URL(url).hostname, found: 'added', status: null, ok: false });
    }
  } catch (e) { /* none yet */ }
}
function save() {
  const added = [...stations.values()].filter((s) => s.found === 'added').map((s) => ({ url: s.url, name: s.name }));
  const tmp = savedFile() + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(added, null, 1));
  fs.renameSync(tmp, savedFile());
}

function send() {
  if (!listWin || listWin.isDestroyed()) return;
  listWin.webContents.send('stations', [...stations.values()].map((s) => ({
    url: s.url, name: s.name, found: s.found, ok: s.ok, state: describe(s.ok ? s.status : null),
  })));
}

// --- finding stations on the network
function discover() {
  const bonjour = new Bonjour();
  const browser = bonjour.find({ type: 'videocap' });
  browser.on('up', (svc) => {
    const ip = (svc.addresses || []).find((a) => /^\d+\.\d+\.\d+\.\d+$/.test(a)) || svc.host;
    const url = normalize(ip + ':' + svc.port);
    if (!url) return;
    const name = (svc.txt && svc.txt.station) || svc.name.replace(/^Videocap on /, '');
    const old = stations.get(url);
    stations.set(url, Object.assign(old || { status: null, ok: false }, { url, name, found: old ? old.found : 'network' }));
    poll(url);
  });
  browser.on('down', (svc) => {
    for (const [url, s] of stations) if (s.found === 'network' && s.name === ((svc.txt && svc.txt.station) || svc.name)) stations.delete(url);
    send();
  });
  setInterval(() => browser.update(), 30000);  // ask again now and then: a station that just started
}

// --- what each station is doing, and notifications when it changes
async function poll(url) {
  const s = stations.get(url);
  if (!s) return;
  let st = null;
  try {
    const r = await fetch(url + 'status.json', { signal: AbortSignal.timeout(4000) });
    if (r.ok) st = await r.json();
  } catch (e) { /* not answering */ }
  const prev = s.ok ? s.status : null;
  if (Notification.isSupported() && (s.ok || st)) {
    for (const ev of events(prev, st, s.name)) {
      const n = new Notification({ title: ev.title, body: ev.body });
      n.on('click', () => openStation(url));
      n.show();
    }
  }
  s.status = st || s.status;
  s.ok = !!st;
  send();
}
setInterval(() => { for (const url of stations.keys()) poll(url); }, 3000);

// --- windows
function createList() {
  listWin = new BrowserWindow({
    width: 520, height: 640, minWidth: 380, minHeight: 360, title: 'Videocap', backgroundColor: '#141518',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  listWin.loadFile(path.join(__dirname, 'index.html'));
  listWin.webContents.on('did-finish-load', send);
  listWin.on('closed', () => { listWin = null; });
}

function openStation(url) {
  const old = pageWins.get(url);
  if (old && !old.isDestroyed()) { old.show(); old.focus(); return; }
  const s = stations.get(url);
  const win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 360, minHeight: 480, title: 'Videocap: ' + (s ? s.name : url),
    backgroundColor: '#141518',
    // the station's own page: no access to this computer at all
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.loadURL(url);
  // links that leave the station open in the browser; the page never navigates away from its station
  win.webContents.setWindowOpenHandler(({ url: to }) => { shell.openExternal(to); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, to) => { if (!to.startsWith(url)) { e.preventDefault(); shell.openExternal(to); } });
  win.on('page-title-updated', (e) => e.preventDefault());
  pageWins.set(url, win);
  win.on('closed', () => pageWins.delete(url));
}

ipcMain.handle('open', (e, url) => { if (stations.has(url)) openStation(url); });
ipcMain.handle('add', async (e, addr) => {
  const url = normalize(addr);
  if (!url) return { error: 'That doesn’t look like an address. Try videocap.local, or the station’s IP address like 192.168.1.20.' };
  try {
    const r = await fetch(url + 'status.json', { signal: AbortSignal.timeout(5000) });
    const st = await r.json();
    if (!r.ok || !('step' in st)) throw new Error('not a station');
    stations.set(url, { url, name: st.station || new URL(url).hostname, found: 'added', status: st, ok: true });
    save();
    send();
    return { ok: true };
  } catch (err) {
    return { error: 'No Videocap station answered at ' + url + '. Check the address, and that the station is on and on this network.' };
  }
});
ipcMain.handle('remove', (e, url) => {
  const s = stations.get(url);
  if (s && s.found === 'added') { stations.delete(url); save(); send(); }
});

app.setAppUserModelId('com.aidansiegel.videocap');  // Windows notifications
app.whenReady().then(() => {
  loadSaved();
  createList();
  discover();
  for (const url of stations.keys()) poll(url);
  app.on('activate', () => { if (!listWin) createList(); });
});
// on a Mac the app keeps running (and notifying) with its windows closed, as Mac apps do
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
