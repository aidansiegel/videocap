"""The station's web page and its small API. Home network only: no logins, so don't expose it to the internet.

    GET  /              the page (static/index.html)
    GET  /status.json   everything the page shows
    GET  /snap.jpg      the latest snapshot while recording
    GET  /settings.json the setup screen: settings the page may change, their choices, and what's plugged in
    POST /settings      body: JSON {KEY: value}: written to the settings file, all or none; answers {"errors": {}}
    POST /setup-done    the first-run setup is finished (the page stops opening it by itself)
    POST /timezone      body: a time zone name (America/New_York): set this machine's time zone
    GET  /live.mjpg     the live picture while recording, ~30 frames a second (multipart JPEG); ends when it stops
    GET  /watch/NAME    a listed capture's MP4 (with byte ranges, for seeking); 404 if its disk isn't here
    POST /start         record now (Start, then press play)
    POST /stop          stop recording (a take stopped by hand is held for review)
    POST /next          body: an item ID (blank = unlisted): the item the next capture is of (or the one recording)
    POST /details       body: JSON id, title, format, date, notes: name and describe the next capture (or this one)
    POST /items         body: an item list (CSV, pasted spreadsheet cells, JSON, or a title per line); empty =
                        back to ITEM_LIST. Answers {"count": n}
    POST /aspect        body: 4:3 or 16:9: the next capture's picture shape
    POST /destination   body: a destination ID (from status.json "destinations"): where captures started from now go
    POST /approve       body: a take's name: send what's held of it
    POST /discard       body: a take's name: set its master aside, never uploaded
    POST /retry         put skipped and failed steps back in the queue
"""
import json
import os
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import settings

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")


def handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self, limit):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(min(n, limit)).decode(errors="replace").strip()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    self.send(200, "text/html; charset=utf-8", f.read())
            elif path == "/status.json":
                self.send(200, "application/json", json.dumps(app.status()).encode())
            elif path == "/snap.jpg":
                from .app import SNAP
                try:
                    with open(SNAP, "rb") as f:
                        self.send(200, "image/jpeg", f.read())
                except OSError:
                    self.send(404, "text/plain", b"no picture yet")
            elif path == "/settings.json":
                self.send(200, "application/json", json.dumps(app.settings_view()).encode())
            elif path == "/live.mjpg":
                self.live()
            elif path.startswith("/watch/"):
                self.watch(urllib.parse.unquote(path[len("/watch/"):]))
            else:
                self.send(404, "text/plain", b"not found")

        def live(self):
            """The live picture as multipart JPEG (an <img> plays it), until the recording stops sending frames."""
            first = app.live.next(-1 if app.live.fresh() else app.live.seq, timeout=3)
            if not first:
                return self.send(404, "text/plain", b"no live picture right now")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            got = first
            try:
                while got:
                    seq, jpeg = got
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg)
                                     + jpeg + b"\r\n")
                    got = app.live.next(seq)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the page went away

        def watch(self, name):
            """Play a capture's MP4: whole, or the byte range the browser asks for (seeking)."""
            p = app.watch_path(name)
            if not p:
                return self.send(404, "text/plain", b"that capture's disk isn't connected")
            size = os.path.getsize(p)
            m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
            start, end = 0, size - 1
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start, end = int(m.group(1)), int(m.group(2) or size - 1)
                else:  # the last N bytes
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.end_headers()
                    return
            self.send_response(206 if m else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if m:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            try:
                with open(p, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                        app.view_until = time.time() + 90
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser had what it wanted

        def do_POST(self):
            # only this page may change things: a browser sends Origin on a POST from another site's page, so a page
            # elsewhere can't press buttons or change settings here behind the viewer's back
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).netloc != (self.headers.get("Host") or ""):
                return self.send(403, "text/plain", b"only the station's own page can do that")
            ok = True
            if self.path == "/stop" and app.rec:
                app.stop_requested = True
            elif self.path == "/start" and not app.rec:
                app.armed = app.force = True
                app.standby_until = time.time() + app.f("STANDBY_HOURS") * 3600
            elif self.path == "/retry":
                app.queue.retry()
            elif self.path == "/approve":
                ok = app.queue.approve(self.body(400))
            elif self.path == "/discard":
                ok = app.queue.discard(self.body(400))
            elif self.path == "/aspect" and (not app.rec or app.rec.standby):
                ok = app.set_aspect(self.body(20))
            elif self.path == "/destination":
                ok = app.set_destination(self.body(100))
            elif self.path == "/next":
                rec = app.rec
                if app.set_next(self.body(200)) and rec and rec.standby:
                    app.restart_standby = True  # the picture shape is set when a recording starts
                if rec and not rec.standby:  # recording: this is what it's of
                    app.set_details(app.details)
            elif self.path == "/details":
                try:
                    ok = app.set_details(json.loads(self.body(20000) or "{}"))
                except ValueError:
                    ok = False
            elif self.path == "/settings":
                try:
                    res = app.change_settings(json.loads(self.body(20000) or "{}"))
                except ValueError:
                    res = {"errors": {"": "That wasn't a list of settings."}}
                return self.send(200 if not res["errors"] else 422, "application/json", json.dumps(res).encode())
            elif self.path == "/setup-done":
                settings.mark_setup_done()
            elif self.path == "/timezone":
                err = settings.set_timezone(self.body(100))
                return self.send(422 if err else 200, "application/json", json.dumps({"error": err}).encode())
            elif self.path == "/items":
                n = app.set_items(self.body(2_000_000))
                body = json.dumps({"count": n}).encode()
                return self.send(200 if n is not None else 422, "application/json", body)
            else:
                ok = False
            self.send(204 if ok else 409, "text/plain", b"")

    return H


def serve(app, port=None):
    ThreadingHTTPServer(("", int(port or app.conf["WEB_PORT"])), handler(app)).serve_forever()
