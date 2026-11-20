"""Read a station's Morse light with a computer's camera: python3 -m videocap.reader

Point the camera at the station (a hand's width or two from the green light) and press its power button twice for
the status, three times for the address. Prints what it reads as it goes, then the message. Needs ffmpeg; on a Mac
it uses the built-in camera (avfoundation), on Linux /dev/video0 (v4l2). The same steps as the phone reader
(docs/reader): find the spot that blinks most, replay the last few seconds so the start of the sync flash isn't
missed, then threshold halfway between dark and lit and decode.
"""
import argparse
import collections
import json
import subprocess
import sys
import time

from .morse import Decoder

W, H = 96, 72
FRAME = W * H * 3


def camera(device, fps):
    if sys.platform == "darwin":
        src = ["-f", "avfoundation", "-framerate", str(fps), "-video_size", "1280x720", "-i", "%s:none" % device]
    else:
        src = ["-f", "v4l2", "-framerate", str(fps), "-i", device if device.startswith("/") else "/dev/video" + device]
    return subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-fflags", "nobuffer"] + src +
                            ["-vf", "scale=%d:%d" % (W, H), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)


class Reader:
    def __init__(self, say=lambda m: None):
        self.say = say
        self.mean = [0.0] * (W * H)
        self.var = [0.0] * (W * H)
        self.history = collections.deque()   # (t ms, green channel bytes) for the last 4 s
        self.spot = None
        self.samples = collections.deque()
        self.lit, self.since, self.frames = None, 0.0, 0
        self.dec = Decoder()
        self.runs = []                       # what the light did, for checking by hand

    def frame(self, t, rgb):
        green = rgb[1::3]
        self.frames += 1
        self.history.append((t, green))
        while self.history and self.history[0][0] < t - 4000:
            self.history.popleft()
        if not self.dec.started:
            self.find(rgb, green)
        if self.spot is not None:
            self.sample(t, self.level(green))
        return self.dec.done

    def find(self, rgb, green):
        """The spot that changes most is the blinking light: green, not the red light beside it."""
        a, best, bi = 0.08, 0.0, -1
        mean, var, red = self.mean, self.var, rgb[0::3]
        for i in range(W * H):
            g = green[i]
            d = g - mean[i]
            mean[i] += a * d
            var[i] += a * (d * d - var[i])
            score = var[i] if g >= red[i] * 0.8 else var[i] * 0.2
            if score > best:
                best, bi = score, i
        if bi >= 0 and best > 150 and self.frames % 10 == 0:
            spot = (bi % W, bi // W)
            if self.spot is None or abs(spot[0] - self.spot[0]) + abs(spot[1] - self.spot[1]) > 3:
                self.watch(spot)
                self.say("found a light at %d,%d; waiting for the long flash that starts a message" % spot)

    def level(self, green):
        x0, y0 = self.spot
        vals = [green[y * W + x] for y in range(y0 - 1, y0 + 2) for x in range(x0 - 1, x0 + 2) if 0 <= x < W and 0 <= y < H]
        return sum(vals) / len(vals) if vals else 0

    def watch(self, spot):
        self.spot = spot
        self.samples.clear()
        self.lit = None
        self.dec.reset()
        self.runs = []
        for t, g in list(self.history)[:-1]:
            self.sample(t, self.level(g))

    def sample(self, t, v):
        self.samples.append((t, v))
        while self.samples and self.samples[0][0] < t - 4000:
            self.samples.popleft()
        lo = min(s[1] for s in self.samples)
        hi = max(s[1] for s in self.samples)
        if hi - lo < 18:
            return
        mid, band = lo + (hi - lo) / 2, (hi - lo) * 0.08
        now = v > mid if self.lit is None else v > mid - band if self.lit else v > mid + band
        if self.lit is None:
            self.lit, self.since = now, t
        elif now != self.lit:
            self.run(self.lit, t - self.since)
            self.lit, self.since = now, t
        elif (not self.lit and self.dec.started and not self.dec.done
              and t - self.since >= 8 * self.dec.unit):  # dark longer than any gap in a message: it's over
            self.run(False, t - self.since)
            self.since = t

    def run(self, lit, ms):
        before = self.dec.partial
        self.runs.append([lit, round(ms)])
        was = self.dec.started
        self.dec.run(lit, ms)
        if self.dec.started and not was:
            self.say("reading (%d words a minute)" % round(1200 / self.dec.unit))
        if self.dec.partial != before and self.dec.text:
            self.say("  " + self.dec.partial)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m videocap.reader", description=__doc__.split("\n\n")[0])
    ap.add_argument("--camera", default="0", help="camera number (Mac: 0 is the built-in one) or /dev/videoN")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=120, help="give up after this long")
    ap.add_argument("--runs", help="also save what the light did (on/off and ms) to this JSON file")
    args = ap.parse_args(argv)
    say = lambda m: print(m, file=sys.stderr, flush=True)
    say("watching camera %s for up to %d s: press the station's button twice (status) or three times (address)"
        % (args.camera, args.seconds))
    cam = camera(args.camera, args.fps)
    r = Reader(say)
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            buf = cam.stdout.read(FRAME)
            if len(buf) < FRAME:
                say("the camera stopped (is it allowed? macOS: System Settings > Privacy & Security > Camera)")
                break
            if r.frame(time.monotonic() * 1000, buf):
                break
    except KeyboardInterrupt:
        pass
    finally:
        cam.terminate()
    if args.runs:
        with open(args.runs, "w") as f:
            json.dump(r.runs, f)
    if r.dec.done:
        print(r.dec.text)
        return 0
    say("no complete message" + (" (got: %s)" % r.dec.partial if r.dec.partial else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
