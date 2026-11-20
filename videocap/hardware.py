"""Finding the capture device, its sound card and the capture disk; picture lock; LEDs; small ffmpeg helpers."""
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import threading
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


def write_atomic(path, text):
    """Write a small file so that a power cut leaves the old one or the new one, never an empty or half one."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(path + ".tmp", path)
    with contextlib.suppress(OSError):
        fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def on_disk(path):
    """Flush a file to its disk and drop it from memory, so that reading it back reads the disk, not the cache."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def unmounted_mount_point(path):
    """The mount point in /etc/fstab that path is under but that isn't mounted now (so writing there would fill the
    system disk instead), or None."""
    path = os.path.realpath(path)
    try:
        with open("/etc/fstab") as f:
            points = [line.split()[1] for line in f if line.strip() and not line.lstrip().startswith("#")
                      and len(line.split()) > 1]
    except OSError:
        return None
    for mp in sorted(points, key=len, reverse=True):
        if mp not in ("/", "none", "swap") and (path == mp or path.startswith(mp.rstrip("/") + "/")):
            return None if os.path.ismount(mp) else mp
    return None


def lowprio(cmd):
    """Run a disk-heavy command at the lowest CPU and disk priority, so it never starves a capture."""
    pre = ["nice", "-n", "19"] if shutil.which("nice") else []
    if shutil.which("ionice"):
        pre += ["ionice", "-c", "3"]
    return pre + list(cmd)


# --- LEDs -------------------------------------------------------------------

def blinks(n, on=200, gap=350, pause=1600):
    """n short blinks, then a pause: a code you count."""
    return [(on, gap)] * (n - 1) + [(on, pause)]


class Led:
    """A Raspberry Pi's lights say what the station is doing, so it can run without a screen.

    Red is recording (1 s on, 1 s off) or an error (short blinks, counted; steady red is the Pi off). Green blinks a count for what's missing, then pauses:
    1 = the capture device, 2 = the disk, 3 = the picture (everything is plugged in: press play). Fast green flicker:
    finishing the files, don't unplug anything. Mostly on: uploading. Solid green: done, safe to unplug the disk.
    """
    ACT = "/sys/class/leds/ACT"
    PWR = "/sys/class/leds/PWR"
    ON, OFF = [(1000, 0)], [(0, 1000)]
    # pattern: (green, red); each a repeating list of (on ms, off ms)
    PATTERNS = {
        "no-device": (blinks(1), OFF),
        "no-disk": (blinks(2), OFF),
        "no-picture": (blinks(3), OFF),
        "recording": (OFF, [(1000, 1000)]),  # 1 s on, 1 s off: steady red is left to mean the Pi is off
        "finishing": ([(80, 80)], OFF),
        "uploading": ([(1400, 120)], OFF),
        "done": (ON, OFF),
        "shutdown": ([(80, 80)], [(80, 80)]),
        "error-disk": (OFF, blinks(1)),        # couldn't mount it, or it's full
        "error-start": (OFF, blinks(2)),       # the recording couldn't start
        "error-stopped": (OFF, blinks(3)),     # a recording stopped on an error
        "error": (OFF, [(250, 250)]),
    }
    STATES = {"waiting": "no-device", "ready": "no-picture"}  # a state given without a pattern of its own
    FLASH = [(60, 60)] * 3  # a button press was heard

    def __init__(self, mode="auto"):
        self.on = mode != "none" and os.path.isdir(self.ACT)
        self.leds = [led for led in (self.ACT, self.PWR) if self.on and os.path.isdir(led)]
        self.orig = {led: self._trigger(led) for led in self.leds}
        self.cur = None
        self.seq = {}            # led -> its pattern now
        self.seq_start = time.time()
        self.over = {}           # led -> (a one-off sequence, when it started, when it ends)
        self.wake = threading.Event()
        self.stopped = False
        self.lit = {}
        if self.leds:
            for led in self.leds:
                self._w(led + "/trigger", "none")
            threading.Thread(target=self._play, daemon=True).start()

    def _trigger(self, led):
        m = re.search(r"\[([^\]]+)\]", read(led + "/trigger"))
        return m.group(1) if m else "none"

    def _w(self, path, val):
        try:
            with open(path, "w") as f:
                f.write(str(val))
        except OSError:
            pass

    def _light(self, led, lit):
        if self.lit.get(led) == lit:
            return
        self.lit[led] = lit
        self._w(led + "/brightness", (read(led + "/max_brightness").strip() or "1") if lit else 0)

    def set(self, pattern):
        pattern = self.STATES.get(pattern, pattern)
        if pattern not in self.PATTERNS:
            pattern = "done"
        if pattern == self.cur:
            return
        self.cur = pattern
        green, red = self.PATTERNS[pattern]
        self.seq = {self.ACT: green, self.PWR: red}
        self.seq_start = time.time()
        self.wake.set()

    def overlay(self, seqs):
        """Play a one-off sequence on some lights ({led: [(on ms, off ms), ...]}) over the pattern, then go back."""
        now = time.time()
        for led, seq in seqs.items():
            self.over[led] = (seq, now, now + sum(a + b for a, b in seq) / 1000)
        self.lit = {}
        self.wake.set()

    def flash(self):
        """Both lights flicker for a moment: the button was heard."""
        self.overlay({led: self.FLASH for led in (self.ACT, self.PWR)})

    def morse(self, seq):
        """Send a message (morse.timings) on the green light; red keeps saying whether it's recording. Seconds it
        takes."""
        self.overlay({self.ACT: seq})
        return sum(a + b for a, b in seq) / 1000

    @staticmethod
    def _at(seq, k):
        """Lit or not at k ms into one round of a pattern, and how long until that changes."""
        t = 0
        for on, off in seq:
            if k < t + on:
                return True, t + on - k
            if k < t + on + off:
                return False, t + on + off - k
            t += on + off
        return False, 1000

    def _play(self):
        """Steps each light through its pattern (or a one-off sequence laid over it), waking for each change."""
        while not self.stopped:
            self.wake.clear()
            now = time.time()
            wait = 1000.0
            for led in self.leds:
                ov = self.over.get(led)
                if ov and now >= ov[2]:
                    self.over.pop(led, None)
                    self.lit.pop(led, None)
                    ov = None
                if ov:
                    lit, left = self._at(ov[0], (now - ov[1]) * 1000)
                    left = min(left, (ov[2] - now) * 1000)
                else:
                    seq = self.seq.get(led, self.OFF)
                    length = sum(a + b for a, b in seq) or 1000
                    lit, left = self._at(seq, ((now - self.seq_start) * 1000) % length)
                self._light(led, lit)
                wait = min(wait, left)
            self.wake.wait(max(0.005, wait / 1000))

    def restore(self):
        self.stopped = True
        self.wake.set()
        for led, trig in self.orig.items():
            self._w(led + "/trigger", trig)


def undervoltage():
    """True while a Raspberry Pi reports its supply voltage too low (the kernel's rpi_volt alarm)."""
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read(d + "/name").strip() == "rpi_volt":
            return read(d + "/in0_lcrit_alarm").strip() == "1"
    return False


# --- capture device ---------------------------------------------------------

def usb_root(path):
    """sysfs path of the USB device that owns an interface path."""
    while ":" in os.path.basename(path):
        path = os.path.dirname(path)
    return path


def usb_video():
    """USB video devices (the first node of each), as dicts: dev, sys, usb (its USB device), driver, name."""
    out = []
    for p in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        real = os.path.realpath(p + "/device")
        if "/usb" not in real or read(p + "/index") not in ("", "0"):
            continue
        out.append({"dev": "/dev/" + os.path.basename(p), "sys": p, "usb": usb_root(real), "name": read(p + "/name"),
                    "driver": os.path.basename(os.path.realpath(p + "/device/driver"))})
    return out


def is_webcam(v):
    """A USB webcam (UVC, calling itself a camera), as opposed to a capture stick. Some cheap capture sticks are UVC
    too, but they don't call themselves cameras."""
    return v["driver"] == "uvcvideo" and re.search(r"cam", v["name"], re.I) is not None


def find_camera(conf):
    """The station's optional webcam (for add-ons): CAMERA, or the first USB webcam; None if
    CAMERA=none or there isn't one. Never the capture device."""
    want = conf.get("CAMERA", "auto")
    if want == "none":
        return None
    if want not in ("", "auto"):
        return want
    cap = conf["VIDEO_DEVICE"]
    return next((v["dev"] for v in usb_video() if is_webcam(v) and v["dev"] != cap), None)


def find_video(conf):
    """(device node, owning USB device path, driver name), or None. Never the station's webcam: with both plugged
    in, the capture device can't be mistaken for the camera, whatever order they're found in. A proper capture chip
    (em28xx, cx231xx, ...) comes before a UVC device."""
    if conf["TEST_SOURCE"]:
        return ("test", None, "test source")
    if conf["VIDEO_DEVICE"]:
        p = "/sys/class/video4linux/" + os.path.basename(conf["VIDEO_DEVICE"])
        if not os.path.exists(p):
            return None
        dev = "/dev/" + os.path.basename(p)
        return (dev, usb_root(os.path.realpath(p + "/device")), card_name(dev) or read(p + "/name"))
    cam = find_camera(conf)
    found = [v for v in usb_video() if v["dev"] != cam and not is_webcam(v)]
    found.sort(key=lambda v: v["driver"] == "uvcvideo")
    for v in found:
        return (v["dev"], v["usb"], card_name(v["dev"]) or v["name"])
    return None


def card_name(dev):
    """What the capture device calls itself (Hauppauge USB Live2), from v4l2-ctl; "" if it can't say."""
    m = re.search(r"Card type\s*:\s*(.+)", sh("v4l2-ctl", "-d", dev, "--info"))
    return m.group(1).strip() if m else ""


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


SYSTEM_MOUNTS = ("/", "/boot", "/boot/firmware", "/usr", "/var", "/home")


def usb_mounts():
    """Everything mounted from USB disks, as (mount point, name) pairs, whoever mounted it (a capture disk, or a
    disk the system mounts, like a masters disk). A disk the system runs from is left out: it can't be unplugged."""
    try:
        data = json.loads(sh("lsblk", "-J", "-o", "NAME,PATH,TRAN,LABEL,MOUNTPOINTS"))
    except ValueError:
        return []
    out = []
    for d in data.get("blockdevices", []):
        if d.get("tran") != "usb":
            continue
        parts = d.get("children") or [d]
        points = [(m, p.get("label") or p.get("path")) for p in parts for m in (p.get("mountpoints") or []) if m]
        if any(m in SYSTEM_MOUNTS for m, _ in points):
            continue
        out += [(m, name) for m, name in points if m != "[SWAP]"]
    return out


def umount_now(where):
    """Unmount for real, or say what's holding it: never the lazy kind, which only hides a mount that may still be
    writing. None when it's unmounted, else the reason."""
    os.sync()
    for _ in range(3):
        r = subprocess.run(["umount", where], capture_output=True, text=True, errors="replace")
        if r.returncode == 0 or not os.path.ismount(where):
            return None
        time.sleep(1)
    who = sh("fuser", "-m", where).split()
    names = sorted({read("/proc/%s/comm" % pid.rstrip("cefFrm")) for pid in who} - {""})
    return "in use by %s" % ", ".join(names) if names else (r.stderr.strip() or "busy")


def find_card(label, mount_point):
    """The largest writable USB filesystem not in use elsewhere (only ones labeled `label`, if set)."""
    best = None
    for p in usb_partitions():
        if p.get("mountpoint") not in (None, mount_point):
            continue  # somebody else is using it
        if label and (p.get("label") or "").lower() != label.lower():
            continue
        if best is None or int(p["size"]) > int(best["size"]):
            best = p
    return best


REPAIR = {"exfat": ["fsck.exfat", "-p"], "vfat": ["fsck.vfat", "-a"]}


def repair(card):
    """Check a FAT or exFAT disk, and fix what's safe to fix by itself, before it's mounted: one pulled out without
    being unmounted can be left half-written, and writing more to it can make that worse. True unless it's beyond
    an automatic fix. (ext4, btrfs and NTFS keep journals and look after themselves.)"""
    cmd = REPAIR.get(card.get("fstype"))
    if not cmd or not shutil.which(cmd[0]):
        return True
    r = subprocess.run(cmd + [card["path"]], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       errors="replace", timeout=900)
    what = (r.stdout + r.stderr).strip().splitlines()
    if r.returncode == 0:
        return True
    if r.returncode == 1:  # fixed
        log("repaired %s %s (it was pulled out without being unmounted?): %s"
            % (card["path"], card.get("label") or "", what[-1] if what else ""))
        return True
    log("%s %s needs repair on a computer (fsck exit %d): %s"
        % (card["path"], card.get("label") or "", r.returncode, " / ".join(what[-3:])))
    return False


def mount(card, where, owner="", check=False):
    """Mount a partition at `where`; True if it's mounted there now. Never leaves another disk mounted there.
    check: look it over first (see repair), for a disk just plugged in."""
    if os.path.ismount(where):
        src = sh("findmnt", "-no", "SOURCE", where).strip()
        if src == card["path"]:
            return True
        log("%s holds %s, not %s: unmounting it" % (where, src, card["path"]))
        umount(where)
    os.makedirs(where, exist_ok=True)
    fs = card["fstype"]
    if check and not repair(card):
        return False
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
