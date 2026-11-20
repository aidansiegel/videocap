"""The Raspberry Pi 5's power button as the station's one button, for running it without a screen.

Presses are counted (a pause of GAP_SEC ends a count): one stops everything and makes the disks safe to unplug (one
more, or a disk going in or out, carries on); two and three have the green light spell out the status and the
network address in Morse; held for HOLD_SEC it finishes the files and shuts the machine down. While videocap runs it holds logind's
handle-power-key lock (systemd-inhibit), so a press doesn't power the Pi off; when videocap stops, the button goes back
to what it did before. The Pi's firmware still cuts the power on a 7-second hold.
"""
import glob
import os
import select
import shutil
import struct
import subprocess
import threading
import time

EV_KEY = 1
KEY_POWER = 116
EVENT = struct.Struct("llHHi")  # struct input_event: a timeval, then type, code, value
HOLD_SEC = 3.0
GAP_SEC = 0.5   # this long with no new press ends a count


def find(name="pwr_button"):
    """/dev/input/eventN of the input device with this name, or None."""
    for path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(path) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + path.split("/")[4]
        except OSError:
            pass
    return None


class Button:
    def __init__(self, mode, on_presses, on_hold, log=print):
        self.on_presses, self.on_hold, self.log = on_presses, on_hold, log
        self.dev = find() if mode != "none" else None
        self.inhibit = None
        self.stopped = False

    def start(self):
        if not self.dev:
            return False
        if shutil.which("systemd-inhibit"):
            # logind leaves the button alone while this runs; it ends with videocap (or when videocap is killed, since
            # it's in the same service)
            self.inhibit = subprocess.Popen(
                ["systemd-inhibit", "--what=handle-power-key", "--who=videocap", "--mode=block",
                 "--why=The button makes the disks safe to unplug; hold it to shut down", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=self.run, daemon=True).start()
        self.log("button: %s (1 press: safe to unplug; 2: status in Morse; 3: address; hold %g s: shut down)"
                 % (self.dev, HOLD_SEC))
        return True

    def stop(self):
        self.stopped = True
        if self.inhibit:
            self.inhibit.terminate()

    def run(self):
        try:
            fd = os.open(self.dev, os.O_RDONLY)
        except OSError as e:
            self.log("button: can't read %s: %s" % (self.dev, e))
            return
        down = None   # when it was pressed, while it's held
        held = False  # the hold already counted, so its release does nothing
        count, last = 0, 0.0  # presses so far, and when the last one was let go
        while not self.stopped:
            now = time.monotonic()
            waits = []
            if down is not None and not held:
                waits.append(down + HOLD_SEC - now)
            if count and down is None:
                waits.append(last + GAP_SEC - now)
            r, _, _ = select.select([fd], [], [], max(0.0, min(waits)) if waits else None)
            if not r:
                now = time.monotonic()
                if down is not None and not held and now >= down + HOLD_SEC:
                    held, count = True, 0  # act now, so the lights answer while it's still held
                    self.safe(self.on_hold)
                elif count and down is None and now >= last + GAP_SEC:
                    n, count = count, 0
                    self.safe(lambda: self.on_presses(n))
                continue
            data = os.read(fd, EVENT.size * 16)
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(data, i)
                if typ != EV_KEY or code != KEY_POWER:
                    continue
                if value == 1:
                    down, held = time.monotonic(), False
                elif value == 0 and down is not None:
                    if not held:
                        count += 1
                        last = time.monotonic()
                    down = None

    def safe(self, fn):
        try:
            fn()
        except Exception as e:  # the button must never take the station down
            self.log("button: %r" % e)
