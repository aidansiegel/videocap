// node --test desktop/test : what the list says, and which changes notify
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { describe, events, normalize } = require('../stations');

test('describe', () => {
  assert.equal(describe(null).tone, 'bad');
  assert.equal(describe({ step: 'recording', recording: { elapsed: 6175 }, item: 'B002_V001: Tape 1' }).label, 'Recording 1:42:55');
  const ready = describe({ step: 'press_play', details: { id: 'A2', title: 'Summer 1994' } });
  assert.equal(ready.tone, 'go');
  assert.equal(ready.detail, 'Next: A2 · Summer 1994');
  assert.equal(describe({ step: 'error', message: 'Disk is full' }).detail, 'Disk is full');
});

test('events', () => {
  const rec = { step: 'recording', held: [] };
  const done = { step: 'press_play', held: ['A1 - x'], last_result: 'A1: source ended' };
  const ev = events(rec, done, 'den');
  assert.deepEqual(ev.map((e) => e.title), ['Capture finished on den', 'A capture is waiting for you']);
  assert.equal(events(done, done, 'den').length, 0);                       // nothing changed
  assert.equal(events(null, rec, 'den').length, 0);                        // first sight: no noise
  assert.equal(events(rec, { step: 'error', message: 'x', held: [] }, 'den')[0].title, 'den needs attention');
  assert.equal(events(rec, null, 'den')[0].title, 'den stopped answering');
});

test('normalize', () => {
  assert.equal(normalize('videocap.local'), 'http://videocap.local/');
  assert.equal(normalize('10.0.0.5:8091'), 'http://10.0.0.5:8091/');
  assert.equal(normalize(' http://portable.local:8091/status.json '), 'http://portable.local:8091/');
  assert.equal(normalize(''), null);
  assert.equal(normalize('http://[bad'), null);
});
