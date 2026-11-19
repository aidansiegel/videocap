"""The live picture for the web page: ffmpeg sends a motion-JPEG copy of the picture over UDP to this process, which
keeps the latest frame and hands frames to every page that's watching (multipart JPEG, which an <img> plays).

UDP never blocks the recording: with nobody reading, or a reader that falls behind, frames are simply dropped.
"""
import socket
import threading
import time


class LiveFeed:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.cond = threading.Condition()
        self.frame, self.seq, self.at = None, 0, 0.0
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        """Split the byte stream into JPEG frames (start FF D8 ... end FF D9; FF D9 can't occur inside one)."""
        buf = b""
        while True:
            try:
                buf += self.sock.recv(65536)
            except OSError:
                time.sleep(1)
                continue
            while True:
                s = buf.find(b"\xff\xd8")
                if s < 0:
                    buf = b""
                    break
                e = buf.find(b"\xff\xd9", s + 2)
                if e < 0:
                    buf = buf[s:] if len(buf) - s < 2_000_000 else b""  # a lost packet: start over at the next frame
                    break
                frame, buf = buf[s:e + 2], buf[e + 2:]
                with self.cond:
                    self.frame, self.seq, self.at = frame, self.seq + 1, time.time()
                    self.cond.notify_all()

    def fresh(self):
        return time.time() - self.at < 2

    def next(self, seq, timeout=3.0):
        """The first frame after frame number seq, as (seq, jpeg); None if none comes within timeout (it stopped)."""
        with self.cond:
            if not self.cond.wait_for(lambda: self.seq != seq and self.fresh(), timeout):
                return None
            return self.seq, self.frame
