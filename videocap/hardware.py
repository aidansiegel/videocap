"""Finding the capture device, its sound card and the capture disk; picture lock; LEDs; small ffmpeg helpers."""
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import time

WRITABLE_FS = ("exfat", "vfat", "ext4", "ntfs")


def log(msg):
    print(msg, flush=True)


def sh(*args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def lowprio(cmd):
    """Run a disk-heavy command at the lowest CPU and disk priority, so it never starves a capture."""
    pre = ["nice", "-n", "19"] if shutil.which("nice") else []
    if shutil.which("ionice"):
        pre += ["ionice", "-c", "3"]
    return pre + list(cmd)


# --- LEDs -------------------------------------------------------------------

class Led:
    """A Raspberry Pi's green ACT LED shows the state; the red PWR LED blinks on an error.

    heartbeat: waiting for the capture device or disk; solid: ready or finished (safe to unplug the disk);
    slow blink: recording; fast blink: finishing the files; mostly on: uploading; red blinking: something went wrong.
    """
    ACT = "/sys/class/leds/ACT"
    PWR = "/sys/class/leds/PWR"

    def __init__(self, mode="auto"):
        self.on = mode != "none" and os.path.isdir(self.ACT)
        self.orig = {led: self._trigger(led) for led in (self.ACT, self.PWR)} if self.on else {}
        self.cur = None

    def _trigger(self, led):
        m = re.search(r"\[([^\]]+)\]", read(led + "/trigger"))
        return m.group(1) if m else "none"

    def _w(self, path, val):
        try:
            with open(path, "w") as f:
                f.write(str(val))
        except OSError:
            pass

    def _set(self, led, mode):
        if mode in ("slow", "fast", "err", "blip"):
            on, off = {"slow": (500, 500), "fast": (80, 80), "err": (250, 250), "blip": (1400, 120)}[mode]
            self._w(led + "/trigger", "timer")
            self._w(led + "/delay_on", on)
            self._w(led + "/delay_off", off)
        elif mode == "off":
            self._w(led + "/trigger", "none")
            self._w(led + "/brightness", 0)
        else:
            self._w(led + "/trigger", mode)

    def set(self, pattern):
        if not self.on or pattern == self.cur:
            return
        self.cur = pattern
        if pattern == "error":
            self._set(self.ACT, "off")
            self._set(self.PWR, "err")
            return
        self._set(self.PWR, self.orig[self.PWR])
        self._set(self.ACT, {"waiting": "heartbeat", "ready": "default-on", "recording": "slow",
                             "finishing": "fast", "uploading": "blip"}.get(pattern, "default-on"))

    def restore(self):
        for led, trig in self.orig.items():
            self._set(led, trig)


# --- capture device ---------------------------------------------------------

def usb_root(path):
    """sysfs path of the USB device that owns an interface path."""
    while ":" in os.path.basename(path):
        path = os.path.dirname(path)
    return path


def find_video(conf):
    """(device node, owning USB device path, driver name), or None."""
    if conf["TEST_SOURCE"]:
        return ("test", None, "test source")
    candidates = sorted(glob.glob("/sys/class/video4linux/video*"))
    if conf["VIDEO_DEVICE"]:
        candidates = ["/sys/class/video4linux/" + os.path.basename(conf["VIDEO_DEVICE"])]
    for p in candidates:
        real = os.path.realpath(p + "/device")
        if "/usb" not in real or read(p + "/index") not in ("", "0"):
            continue
        return ("/dev/" + os.path.basename(p), usb_root(real), read(p + "/name"))
    return None


def find_audio(conf, usbdev):
    """The ALSA device on the same USB device as the picture (or AUDIO_DEVICE)."""
    if conf["AUDIO_DEVICE"]:
        return conf["AUDIO_DEVICE"]
    if usbdev is None:
        return None
    for c in glob.glob("/sys/class/sound/card*"):
        if usb_root(os.path.realpath(c + "/device")) == usbdev:
            return "hw:CARD=%s,DEV=0" % read(c + "/id")
    return None


def set_gain(conf, audio):
    """Set a capture mixer control before a recording (AUDIO_GAIN=control=dB), for devices where it works."""
    m = re.match(r"hw:CARD=([^,]+)", audio or "")
    if not conf["AUDIO_GAIN"].strip() or not m:
        return
    control, _, db = conf["AUDIO_GAIN"].partition("=")
    r = subprocess.run(["amixer", "-c", m.group(1), "sset", control.strip(), "%sdB" % db.strip(), "cap"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    got = re.search(r"\[(-?[\d.]+dB)\]", r.stdout)
    log("capture level %s: %s" % (control.strip(), got.group(1) if got else
                                  "not set: " + (r.stderr.strip() or r.stdout.strip())[-120:]))


def list_inputs(dev):
    out = sh("v4l2-ctl", "-d", dev, "--list-inputs")
    return [(int(i), n.strip()) for i, n in re.findall(r"Input\s*:\s*(\d+)\s*\n\s*Name\s*:\s*(.+)", out)]


class Signal:
    """Whether the device sees a picture: True/False, or None if the driver doesn't say.

    Some drivers report every input as "ok" whatever is plugged in; for those a profile names a lock control
    (SIGNAL_CONTROL) to read instead, and a lock bit that flickers can be held for SIGNAL_HOLD_SEC."""

    def __init__(self):
        self.seen = {}  # device -> last time it reported lock

    def forget(self, dev):
        self.seen.pop(dev, None)

    def get(self, dev, conf):
        control = conf["SIGNAL_CONTROL"].strip()
        hold = float(conf["SIGNAL_HOLD_SEC"] or 0)
        if control:
            m = re.search(r"%s:\s*(\d)" % re.escape(control), sh("v4l2-ctl", "-d", dev, "-C", control))
            if m:
                now = time.time()
                if m.group(1) != "0":
                    self.seen[dev] = now
                    return True
                return now - self.seen.get(dev, 0) < hold
        m = re.search(r"Video input\s*:\s*\d+ \((.*): (.*)\)", sh("v4l2-ctl", "-d", dev, "--get-input"))
        if not m:
            return None
        return m.group(2).strip() == "ok"


# --- capture disk -----------------------------------------------------------

def usb_partitions():
    """Writable filesystems on USB disks, as lsblk dicts."""
    try:
        data = json.loads(sh("lsblk", "-J", "-b", "-o", "NAME,PATH,TRAN,FSTYPE,SIZE,MOUNTPOINT,LABEL,UUID"))
    except ValueError:
        return []
    return [p for d in data.get("blockdevices", []) if d.get("tran") == "usb"
            for p in (d.get("children") or [d]) if p.get("fstype") in WRITABLE_FS]


def find_card(label, mount_point):
    """The largest writable USB filesystem not in use elsewhere (only ones labelled `label`, if set)."""
    best = None
    for p in usb_partitions():
        if p.get("mountpoint") not in (None, mount_point):
            continue  # somebody else is using it
        if label and (p.get("label") or "").lower() != label.lower():
            continue
        if best is None or int(p["size"]) > int(best["size"]):
            best = p
    return best


def mount(card, where, owner=""):
    """Mount a partition at `where`; True if it's mounted there now. Never leaves another disk mounted there."""
    if os.path.ismount(where):
        src = sh("findmnt", "-no", "SOURCE", where).strip()
        if src == card["path"]:
            return True
        log("%s holds %s, not %s: unmounting it" % (where, src, card["path"]))
        umount(where)
    os.makedirs(where, exist_ok=True)
    fs = card["fstype"]
    opts = "noatime"
    if fs in ("vfat", "exfat", "ntfs") and owner:
        uid, _, gid = owner.partition(":")
        opts += ",uid=%s,gid=%s,umask=022" % (uid, gid or uid)
    r = subprocess.run(["mount", "-t", "ntfs3" if fs == "ntfs" else fs, "-o", opts, card["path"], where],
                       capture_output=True, text=True)
    if r.returncode:
        log("mount failed: " + r.stderr.strip())
    return r.returncode == 0


def umount(where, tries=1):
    os.sync()
    for _ in range(tries):
        if subprocess.run(["umount", where]).returncode == 0:
            return
        time.sleep(1)
    subprocess.run(["umount", "-l", where])


def compact_memory():
    """USB capture drivers need contiguous memory for their buffers; a busy page cache can leave none
    ("Cannot allocate memory" at start). Ask the kernel to defragment it first (Linux, root)."""
    with contextlib.suppress(OSError):
        with open("/proc/sys/vm/compact_memory", "w") as f:
            f.write("1")


# --- ffmpeg helpers ---------------------------------------------------------

def keyframe_before(path, t):
    """Seconds into an MP4 of its last keyframe at or before t (0 if none, or it can't be read)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%%+%d" % (t + 10),
                        "-show_entries", "format=start_time:packet=pts_time,flags", "-of", "csv=p=0", path],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    start, keys = 0.0, []
    for line in r.stdout.splitlines():
        f = line.split(",")
        with contextlib.suppress(ValueError):
            if len(f) == 2 and "K" in f[1]:
                keys.append(float(f[0]))
            elif len(f) == 1:
                start = float(f[0])
    return max([k - start for k in keys if k - start <= t] or [0.0])


def faststart(path, cut=0.0):
    """Rewrite a recorded MP4 with its index at the front (stream copy, a minute or two for a 2-hour tape).

    It's recorded fragmented so a crash leaves it playable, but some servers can't read a fragmented file's length
    over a network mount. cut: start this many seconds in (a keyframe), dropping the wait before play. The original
    stays if anything fails."""
    tmp = path + ".faststart.mp4"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
                       + (["-ss", "%.6f" % cut] if cut > 0 else [])
                       + ["-i", path, "-map", "0", "-c", "copy", "-movflags", "+faststart", tmp],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    if r.returncode == 0:
        os.replace(tmp, path)
        return True
    log("couldn't rewrite %s: %s" % (os.path.basename(path), r.stderr.strip()[-200:]))
    with contextlib.suppress(OSError):
        os.remove(tmp)
    return False
