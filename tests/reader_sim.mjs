// Reads videocap's Morse light through a simulated phone camera: frames at a given rate, starting anywhere, each
// frame's brightness the share of its exposure the light was on (so edges blur), with timing jitter and noise.
// Usage: node tests/reader_sim.mjs '{"seq": [[on, off], ...], "fps": 30, "trials": 20, "seed": 1}'
import { Decoder } from "../docs/reader/morse.js";

const { seq, fps, trials, seed } = JSON.parse(process.argv[2]);
let s = seed || 1;
const rand = () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648);
const edges = [];  // [start, end] of each flash, ms
let t = 1500;      // dark before it starts
for (const [on, off] of seq) { edges.push([t, t + on]); t += on + off; }
const total = t + 1000;
const litShare = (a, b) => edges.reduce((sum, [x, y]) => sum + Math.max(0, Math.min(b, y) - Math.max(a, x)), 0) / (b - a);

const out = [];
for (let k = 0; k < trials; k++) {
  const d = new Decoder(), frame = 1000 / fps;
  let time = rand() * frame, prev = null, since = 0;
  while (time < total && !d.done) {
    const ts = time + (rand() - 0.5) * 6;                 // when the phone says the frame was taken
    const v = litShare(time, time + frame * 0.9) + (rand() - 0.5) * 0.2;
    const lit = v > 0.5;
    if (prev === null) { prev = lit; since = ts; }
    else if (lit !== prev) { d.run(prev, ts - since); prev = lit; since = ts; }
    time += frame;
  }
  if (!d.done && prev !== null) d.run(prev, total - since + 5000);
  out.push(d.text);
}
console.log(JSON.stringify(out));
