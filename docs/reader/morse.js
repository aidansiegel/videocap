// Decodes videocap's Morse light (videocap/morse.py): a sync flash 10 units long, a gap, the message, then AR (.-.-.).
// Feed it runs of the light, on or off with how long they lasted; it learns the speed from the sync flash.

export const CODE = {
  ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H", "..": "I",
  ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
  "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
  "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7",
  "---..": "8", "----.": "9", ".-.-.-": ".", "-..-.": "/", "-....-": "-",
};
const END = ".-.-.";
const SYNC = 10;

export class Decoder {
  constructor() { this.reset(); }

  reset() {
    this.unit = 0;        // ms per dot, once the sync flash is seen
    this.letter = "";     // dots and dashes of the letter so far
    this.text = "";
    this.done = false;
    this.started = false;
  }

  // one run of the light: lit (true/false) for ms. Returns true when the message is complete.
  run(lit, ms) {
    if (this.done) return true;
    if (lit && ms > 400 && ms < 3500 && (!this.started || ms > 6 * this.unit)) {
      // a sync flash (or a new one: the message started over)
      this.reset();
      this.unit = ms / SYNC;
      this.started = true;
      return false;
    }
    if (!this.started) return false;
    const u = this.unit;
    if (lit) {
      const dash = ms > 2 * u;
      this.letter += dash ? "-" : ".";
      // follow the real speed: a camera's frames round every run, so average over the elements seen
      const est = dash ? ms / 3 : ms;
      if (est > 0.5 * u && est < 1.6 * u) this.unit = 0.85 * u + 0.15 * est;
      return false;
    }
    if (ms < 2 * u) return false;            // between the parts of a letter
    this.endLetter();
    if (this.done) return true;
    if (ms >= 5 * u && this.text && !this.text.endsWith(" ")) this.text += " ";  // between words
    if (ms > 25 * u && this.text) { this.done = true; this.text = this.text.trim(); }  // gone quiet
    return this.done;
  }

  endLetter() {
    if (!this.letter) return;
    if (this.letter === END) { this.done = true; this.text = this.text.trim(); }
    else this.text += CODE[this.letter] || "?";
    this.letter = "";
  }

  // the text so far, with the letter being read shown as its dots and dashes
  get partial() { return this.text + (this.letter ? "[" + this.letter + "]" : ""); }
  get wpm() { return this.unit ? Math.round(1200 / this.unit) : 0; }
}

// what a message means, in plain words
export function explain(text) {
  const w = text.trim().split(/\s+/);
  const rest = w.slice(1).join(" ");
  const free = text.match(/FREE (\d+)G/);
  const mins = text.match(/ (\d+)M\b/);
  switch (w[0]) {
    case "REC": {
      const item = w.slice(1).filter(x => !/^\d+M$/.test(x) && x !== "FREE" && !/^\d+G$/.test(x)).join(" ");
      return "Recording" + (item ? " " + item : "") + (mins ? ", " + mins[1] + " min so far" : "") +
        (free ? ", " + free[1] + " GB free on the disk" : "") + ".";
    }
    case "READY": return "Ready: everything's plugged in. Press play" + (rest ? " (next: " + rest + ")" : "") + ".";
    case "NO": return w[1] === "DISK" ? "No capture disk: plug one in." : w[1] === "DEVICE" ? "No capture device: plug it in."
      : w[1] === "NETWORK" ? "Not on a network." : text;
    case "DONE": return "Finished: safe to unplug the disk.";
    case "SAFE": return "Safe to unplug the disks. Press the button again, or plug in a disk, to carry on.";
    case "NOT": return "Not safe to unplug yet: something is using a disk. Check the page when you can.";
    case "BUSY": return "Busy finishing files: don't unplug anything.";
    case "UPLOADING": return "Moving or uploading" + (w[1] ? ", " + w[1] + "% done" : "") + ": leave the disk connected.";
    case "ERR": return (/E\d\d/.test(text) ? "Code " + text.match(/E\d\d/)[0] + ". " : "") + "Something went wrong: " + ({ DISK: "a disk problem", START: "the recording couldn't start",
      STOPPED: "a recording stopped on an error" }[w[1]] || "see the page") + (/FULL/.test(text) ? " (disk full)" :
      /NO ROOM/.test(text) ? " (no room for a master)" : "") + ".";
    case "IP": {
      const port = text.match(/PORT (\d+)/), ips = w.slice(1).filter(a => /^\d+\.\d+\.\d+\.\d+$/.test(a));
      return ips.length ? "Its page: " + ips.map(a => "http://" + a + (port ? ":" + port[1] : "") + "/").join(" or ") : text;
    }
    default: return "";
  }
}
