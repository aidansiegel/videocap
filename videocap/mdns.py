"""Announce the station on the local network as videocap.local (Avahi's avahi-publish), whatever the machine is called.

If another station already holds the name, this one takes videocap-2.local, then -3, and so on. Follows the
machine's address if it changes. Does nothing (and says so once) where avahi-publish isn't installed.
"""
import shutil
import socket
import subprocess
import threading
import time

from .hardware import log, sh


def own_address():
    """This machine's address on the local network (the one its default route leaves from)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # a documentation address: nothing is sent
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    for ip in sh("hostname", "-I").split():
        if "." in ip and not ip.startswith(("127.", "169.254.")):
            return ip
    return None


class Announcer:
    def __init__(self, name):
        self.base = name.strip().lower().removesuffix(".local")
        self.name = None  # the name actually held, e.g. videocap-2.local
        self.proc = None
        self.first = ""  # avahi-publish's first line

    def start(self):
        if not self.base:
            return
        if socket.gethostname().split(".")[0].lower() == self.base:
            self.name = self.base + ".local"  # the machine is already called that
            return
        if not shutil.which("avahi-publish"):
            log("not announcing %s.local: avahi-publish isn't installed (apt install avahi-utils)" % self.base)
            return
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        n, ip = 1, None
        while True:
            now = own_address()
            if self.proc and (self.proc.poll() is not None or now != ip):
                exited = self.proc.poll() is not None  # not stopped by us for a new address
                err = self.stop()
                if "collision" in err.lower():
                    n += 1
                elif exited:
                    log("couldn't announce %s.local: %s" % (self.base, err.strip()))
                    time.sleep(30)
                self.name = None
            if not self.proc and now:
                ip, name = now, "%s%s.local" % (self.base, "-%d" % n if n > 1 else "")
                self.proc = subprocess.Popen(["avahi-publish", "-a", "-R", name, ip], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                # it says "Established under name ..." once the name is ours, or "... Local name collision" and exits
                self.first = self.proc.stderr.readline() or ""
                if "Established" in self.first:
                    self.name = name
                    log("on the network as %s (%s)" % (name, ip))
                continue
            time.sleep(30)

    def stop(self):
        p, self.proc = self.proc, None
        if not p:
            return ""
        if p.poll() is None:
            p.terminate()
        first, self.first = ("" if "Established" in self.first else self.first), ""
        try:
            return first + (p.communicate(timeout=5)[1] or "")
        except subprocess.TimeoutExpired:
            p.kill()
            return ""


class Service:
    """Advertise the station as a _videocap._tcp service (DNS-SD), so the desktop app and other tools find every
    station on the network by itself, whatever its hostname. Avahi renames it if the name is taken."""

    def __init__(self, station, port, version):
        self.args = ["avahi-publish", "-s", "Videocap on %s" % station, "_videocap._tcp", str(port),
                     "path=/", "station=%s" % station, "version=%s" % version]
        self.proc = None
        self.quit = False

    def start(self):
        if shutil.which("avahi-publish"):
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.quit:
            self.proc = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
            self.proc.wait()
            if not self.quit:
                time.sleep(30)  # avahi-daemon restarted or went away: try again

    def stop(self):
        self.quit = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
