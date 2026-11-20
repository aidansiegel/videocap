"""The station's status as its Bluetooth name, for reading with no network, pairing or setup.

Every SEC seconds the name becomes the next line of the status (App.status_lines), as "V<n> <line>": V0 is the gist,
the rest the details, up to 10 lines, so a reader that keeps listening for a minute has the whole report. Any phone's
Bluetooth scanner app shows it; `python3 -m videocap.btreader` collects and prints it on a computer. A broadcast
only: nothing can connect to the station this way. Uses BlueZ's btmgmt (root), on the first adapter.
"""
import shutil
import subprocess
import threading
import time

SEC = 6.0
INSTANCE = "1"
MAX_NAME = 29  # 31 bytes of advertising data, less the name's own 2-byte header (no flags: it's broadcast only)


def name_data(name):
    """Advertising data holding just a complete local name, as btmgmt wants it (hex)."""
    raw = name.encode("ascii", "replace")[:MAX_NAME]
    return "%02x09%s" % (len(raw) + 1, raw.hex())


class Beacon:
    def __init__(self, mode, lines, log=print, adapter="hci0"):
        self.lines, self.log, self.adapter = lines, log, adapter
        self.on = mode != "none" and bool(shutil.which("btmgmt")) and _present(adapter)
        self.stopped = False
        self.i = 0
        self.now = ""        # the name being broadcast

    def start(self):
        if not self.on:
            return False
        self.btmgmt("power", "on")
        threading.Thread(target=self.run, daemon=True).start()
        self.log("bluetooth: the status is broadcast as this station's name (V0...V9), a line every %g s" % SEC)
        return True

    def stop(self):
        self.stopped = True
        if self.on:
            self.btmgmt("rm-adv", INSTANCE)

    def btmgmt(self, *args):
        try:
            # input from a pipe: given /dev/null, btmgmt waits forever instead of exiting after the command
            return subprocess.run(["btmgmt", "-i", self.adapter] + list(args), capture_output=True, text=True,
                                  timeout=10, input="\n")
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log("bluetooth: %s" % e)
            return None

    def run(self):
        failed = 0
        while not self.stopped:
            try:
                lines = self.lines() or ["?"]
            except Exception as e:  # never take the station down
                lines = ["ERR %r" % e]
            self.i = self.i % len(lines)
            name = "V%d %s" % (self.i, lines[self.i])
            r = self.btmgmt("add-adv", "-d", name_data(name), INSTANCE)  # the same instance: replaces the last one
            if r is None or r.returncode:
                failed += 1
                if failed in (1, 10):
                    self.log("bluetooth: couldn't broadcast (%s)" % ((r and (r.stderr or r.stdout).strip()) or "?"))
            else:
                failed = 0
                self.now = name
            self.i += 1
            time.sleep(SEC)


def _present(adapter):
    import os
    return os.path.isdir("/sys/class/bluetooth/" + adapter)
