"""Control with pen and paper, read by an optional webcam: write a command word and circle it, then anything it needs
after it, uncircled. NOTE (circled) A puts "_A" on the end of the take's file name; STOP, SAFE and STATUS (circled)
do what the button does. Offline: numpy finds the paper, the ink and the circle; Tesseract reads the words.

It looks closely only when the view has changed and then held still (a sheet held up), so it costs next to nothing
the rest of the time, and acts only when two readings in a row agree. Never needed: the station works without it.
"""
import collections
import difflib
import os
import re
import shutil
import subprocess
import threading
import time

from .config import RUN
from .hardware import lowprio

COMMANDS = ("NOTE", "STOP", "SAFE", "STATUS")
LOOK_SEC = 0.5
W, H = 1280, 720     # the frame read for words
S = 4                # geometry is worked out at 1/S of that


def gray(path, w, h):
    """A JPEG as a w x h grayscale numpy array (decoded by ffmpeg; no image library needed)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path, "-vf",
                        "scale=%d:%d,format=gray" % (w, h), "-f", "rawvideo", "-"], capture_output=True, timeout=10)
    if len(r.stdout) != w * h:
        return None
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, w)


def box_mean(a, k):
    """Mean over a (2k+1)-square around each pixel (from cumulative sums)."""
    import numpy as np
    p = np.pad(a.astype(np.float64), k + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    n = 2 * k + 1
    s = c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    return (s / (n * n))[:a.shape[0], :a.shape[1]]


def dilate(m):
    import numpy as np
    out = m.copy()
    out[1:] |= m[:-1]; out[:-1] |= m[1:]; out[:, 1:] |= m[:, :-1]; out[:, :-1] |= m[:, 1:]
    return out


def paper_region(g):
    """Where the sheet is: the largest bright patch (paper is brighter than a couch or a desk), its convex outline with
    what's written on it included, and a little in from its edge. A mask, or None if there's no sheet in view."""
    import numpy as np
    lo, hi = np.percentile(g, (5, 95))
    bright = g > max(100, (lo + hi) / 2)  # halfway between the room and the paper
    comps = components(bright)
    if not comps:
        return None
    big = max(comps, key=len)
    if len(big) < g.size * 0.05:
        return None
    region = hull_fill(big, g.shape)  # paper is a rectangle: its outline is convex, writing and all
    for _ in range(2):  # a little in from the edge, where the paper meets the room
        region &= ~dilate(~region)
    return region


def hull_fill(points, shape):
    """A mask of the convex outline around (y, x) points, the way a rubber band would wrap them."""
    import numpy as np
    rows = collections.defaultdict(list)
    for y, x in points:
        rows[y].append(x)
    pts = sorted({(min(xs), y) for y, xs in rows.items()} | {(max(xs), y) for y, xs in rows.items()})

    def half(ps):
        out = []
        for p in ps:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    hull = half(pts)[:-1] + half(pts[::-1])[:-1]  # (x, y) corners, in order
    mask = np.zeros(shape, bool)
    n = len(hull)
    for y in range(shape[0]):
        xs = []
        for i in range(n):
            (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % n]
            if y1 == y2:
                if y == y1:
                    xs += [x1, x2]
            elif min(y1, y2) <= y <= max(y1, y2):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        if xs:
            mask[y, max(0, int(min(xs))):min(shape[1], int(max(xs)) + 1)] = True
    return mask


def ink(g):
    """Pen marks: clearly darker than the paper around them."""
    return g < box_mean(g, 7) * 0.78


def components(mask):
    """Connected regions of True (4-neighbors), as lists of (y, x)."""
    import numpy as np
    seen = np.zeros_like(mask)
    h, w = mask.shape
    out = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        q, comp = collections.deque([(y0, x0)]), []
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            comp.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        out.append(comp)
    return out


def find_circle(m):
    """In an ink mask (small scale), the circled writing: fill in everything ink closes in, and the circle is the
    biggest solid blob that's mostly closed-in space (a circle around a word), not mostly ink (a letter on its own,
    even an A or an O). (top, bottom, left, right) of the blob, or None."""
    import numpy as np
    closed = dilate(m)  # a pen line drawn quickly can leave small gaps
    blank = ~closed
    outside = np.zeros_like(blank)
    outside[0, :] = blank[0, :]; outside[-1, :] = blank[-1, :]; outside[:, 0] = blank[:, 0]; outside[:, -1] = blank[:, -1]
    while True:  # flood the blank paper from the edges; what it can't reach is closed in
        grown = dilate(outside) & blank
        if (grown == outside).all():
            break
        outside = grown
    filled = ~outside
    best = None
    for comp in components(filled):
        if len(comp) < m.size * 0.004:
            continue
        ys, xs = zip(*comp)
        inked = closed[list(ys), list(xs)].mean()
        if inked > 0.85:
            continue  # mostly ink: a letter or a scribble, not a circle around something
        t, b, l, r = min(ys), max(ys) + 1, min(xs), max(xs) + 1
        if best is None or len(comp) > best[0]:
            best = (len(comp), (t, b, l, r))
    return best and best[1]


def ocr(crop, chars, psm=7):
    """Tesseract on a grayscale crop: the text, keeping only the given characters."""
    path = os.path.join(RUN, "paper-crop.pgm")
    h, w = crop.shape
    with open(path, "wb") as f:
        f.write(b"P5 %d %d 255\n" % (w, h))
        f.write(crop.tobytes())
    r = subprocess.run(lowprio(["tesseract", path, "-", "--psm", str(psm), "-c", "tessedit_char_whitelist=" + chars]),
                       capture_output=True, text=True, errors="replace", timeout=20)
    return re.sub(r"[^" + chars + "]", "", r.stdout.upper())


def read_sheet(g):
    """(command, argument) written on the sheet in a W x H grayscale frame, or None."""
    import numpy as np
    small = g.reshape(H // S, S, W // S, S).mean((1, 3))
    region = paper_region(small)
    if region is None:
        return None
    m = ink(small) & region
    circle = find_circle(m)
    if not circle:
        return None
    ct, cb, cl, cr = (v * S for v in circle)
    pad = (cb - ct) // 6  # inside the circle's line
    word = ocr(g[ct + pad:cb - pad, cl + pad:cr - pad], "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    cmd = difflib.get_close_matches(word, COMMANDS, n=1, cutoff=0.6)
    if not cmd:
        return None
    # what's written after it: pen marks on the paper to the right of the circle, in the same band, cropped tight
    # (so the sheet's edge and the room beyond it aren't read as letters)
    t, b, l, rr = circle
    hh = b - t
    band = np.zeros_like(m)
    band[max(0, t - hh // 2):b + hh // 2, rr + 1:] = True
    edge = dilate(dilate(~region))
    marks = [c for c in components(m & band) if len(c) >= 6 and max(y for y, _ in c) - min(y for y, _ in c) >= hh // 4
             and not any(edge[y, x] for y, x in c)]  # specks, and shading where the sheet meets the room, aren't writing
    # the note is the first marks after the circle and whatever runs on from them, like the letters of a word
    marks.sort(key=lambda c: min(x for _, x in c))
    group = marks[:1]
    for c in marks[1:]:
        if min(x for _, x in c) - max(x for g_ in group for _, x in g_) <= hh:
            group.append(c)
    marks = group
    arg = ""
    if marks:
        ys = [y for c in marks for y, _ in c]
        xs = [x for c in marks for _, x in c]
        y0, y1 = max(0, (min(ys) - 3) * S), min(H, (max(ys) + 4) * S)
        x0, x1 = max(0, (min(xs) - 3) * S), min(W, (max(xs) + 4) * S)
        crop = g[y0:y1, x0:x1]
        arg = ocr(crop, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") or \
            ocr(crop, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", psm=8)
    return cmd[0], arg


class Paper:
    def __init__(self, camera, on_paper, log=print):
        self.camera, self.on_paper, self.log = camera, on_paper, log
        self.frame = os.path.join(RUN, "camera.jpg")  # the webcam's latest picture (in memory: RUN is a tmpfs)
        self.proc = None
        self.stopped = False
        self.last = None      # the last reading acted on, and when, for the page

    def start(self):
        missing = [t for t in ("tesseract", "ffmpeg") if not shutil.which(t)]
        try:
            import numpy  # noqa: F401
        except ImportError:
            missing.append("python3-numpy")
        if missing:
            self.log("paper: a webcam is plugged in, but reading paper needs %s" % ", ".join(missing))
            return False
        threading.Thread(target=self.run, daemon=True).start()
        self.log("paper: watching %s for circled words (NOTE, STOP, SAFE, STATUS)" % self.camera)
        return True

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def run(self):
        import numpy as np
        os.makedirs(RUN, exist_ok=True)
        while not self.stopped:
            # the camera's JPEGs copied as they come, not decoded: cheap at the 30 a second it insists on
            self.proc = subprocess.Popen(lowprio(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                                                  "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "%dx%d" % (W, H),
                                                  "-i", self.camera, "-c:v", "copy", "-f", "image2", "-update", "1",
                                                  "-atomic_writing", "1", "-y", self.frame]),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            prev, changed, still, pending = None, True, 0, None  # one look at what's already in view
            while not self.stopped and self.proc.poll() is None:
                time.sleep(LOOK_SEC)
                tiny = gray(self.frame, 64, 36) if os.path.exists(self.frame) else None
                if tiny is None:
                    continue
                diff = np.abs(tiny.astype(int) - prev).mean() if prev is not None else 0
                prev = tiny.astype(int)
                if diff > 6:
                    changed, still, pending = True, 0, None
                    continue
                still += 1
                if not changed or still < 2:
                    continue
                g = gray(self.frame, W, H)
                got = read_sheet(g) if g is not None else None
                if got is None:
                    if still > 6:
                        changed = False  # nothing there: wait for the next change
                    continue
                if got != pending:
                    pending = got  # once more, to be sure
                    continue
                changed, pending = False, None
                self.last = (got, time.time())
                try:
                    self.on_paper(*got)
                except Exception as e:  # never take the station down
                    self.log("paper: %r" % e)
            if not self.stopped:  # the camera was unplugged: try again in a while
                time.sleep(10)
