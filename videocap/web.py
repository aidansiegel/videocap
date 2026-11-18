"""The station's web page and its small API. Home network only: no logins, so don't expose it to the internet.

    GET  /              the page (static/index.html)
    GET  /status.json   everything the page shows
    GET  /snap.jpg      the latest snapshot while recording
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
            else:
                self.send(404, "text/plain", b"not found")

        def do_POST(self):
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
