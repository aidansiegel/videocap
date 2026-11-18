"""After each take: move the master off the capture disk, then upload to rclone destinations, with review.

For each take, in order:
1. **store** (with MASTERS_DIR): the master(s), log and report are copied to MASTERS_DIR, compared byte for byte, and
   only then is the master removed from the capture disk, so the next take has room. Checksums of what's on
   MASTERS_DIR (read back from it) go beside them.
2. **titles** and **MP4** (with MP4_REMOTE): the .nfo first (libraries read an .nfo only if it's there before the
   video), then the MP4, each under a temporary name until it's complete.
3. **master**, **log**, **report**, then **checksums** last (with MASTER_REMOTE): the checksum file arriving tells a
   receiving server the take is complete. With MASTER_REVIEW these wait ("held") until approved on the web page.

The queue is kept on disk, so an unplugged disk or a dropped connection just pauses it. A take stopped by hand or
shorter than MIN_TAKE_MINUTES is held whole (MP4 too) until approved: it's usually a do-over.
"""
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import collection, config
from .hardware import log, lowprio

VIDEO_EXT = {"mp4", "mkv", "mov", "m4v", "avi", "mpg", "mpeg", "ts"}
SUBTITLE_EXT = {"srt", "vtt", "ass", "ssa", "sub", "idx"}
MASTER_KINDS = ("master", "log", "report", "checksums")


def join(root, path):
    """An rclone path under a destination root: `remote:` + path, `remote:dir` + /path, or a local folder."""
    if root.endswith(":") or root.endswith("/"):
        return root + path
    return root + "/" + path


class Queue:
    def __init__(self, app, path):
        self.app, self.path = app, path
        self.lock = threading.Lock()
        self.jobs = []
        self.progress = ""
        self.retry_at = 0
        try:
            with open(path) as f:
                self.jobs = json.load(f)
        except (OSError, ValueError):
            pass

    @property
    def conf(self):
        return self.app.conf

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w") as f:
            json.dump(self.jobs, f, indent=1, ensure_ascii=False)
        os.replace(self.path + ".tmp", self.path)

    def settings(self, job):
        """The settings a capture was queued with: the destination chosen when it started, over today's file."""
        s = dict(self.conf)
        s.update(job.get("settings") or {})
        return s

    # --- adding and reviewing takes ------------------------------------------

    def add(self, card_id, card_dir, rec, hold_all=False):
        """Queue a finished capture with the destination it started with (rec.dest). A capture with nowhere to go
        is listed too, as saved on this disk."""
        c = getattr(rec, "dest", None) or self.conf
        name = os.path.basename(rec.base)
        rel = lambda p: os.path.relpath(p, card_dir)
        entry = rec.item
        items = []
        mp4 = rec.base + "_access.mp4"
        if c["MP4_REMOTE"] and os.path.exists(mp4):
            stem = entry["file"] if entry else name
            if entry and c["NFO"] == "1":
                items.append({"src": rel(rec.base + ".nfo"), "kind": "titles", "to": "mp4", "dest": stem + ".nfo"})
            items.append({"src": rel(mp4), "kind": "MP4", "to": "mp4", "dest": stem + ".mp4",
                          "replace": bool(entry) and c["REPLACE_EXISTING"] == "1"})
        masters = sorted(glob.glob(glob.escape(rec.base) + "_master*.mkv"))
        if c["MASTER_REMOTE"] and masters:
            for p in masters + [rec.base + ".log", rec.base + ".json", rec.base + ".md5"]:
                kind = {"mkv": "master", "log": "log", "json": "report", "md5": "checksums"}[p.rsplit(".", 1)[1]]
                if kind == "checksums" or os.path.exists(p):
                    items.append({"src": rel(p), "kind": kind, "to": "master",
                                  "dest": "%s/%s" % (name, os.path.basename(p))})
        if c["MASTERS_DIR"] and masters and not hold_all:  # first: the next take needs the room
            items.insert(0, {"src": "", "kind": "store", "dest": c["MASTERS_DIR"]})
        review = c["MASTER_REVIEW"] == "1"
        for it in items:
            held = hold_all or review and it.get("to") == "master"
            it.update(state="held" if held else "pending", note="", tries=0)
        side = [rel(p) for p in (rec.base + ".log", rec.base + ".json") if os.path.exists(p)]
        with self.lock:
            self.jobs.append({"card": card_id, "name": name, "items": items, "entry": entry,
                              "masters": [rel(p) for p in masters], "sidecars": side, "mp4": os.path.exists(mp4),
                              "damage": rec.damage.summary(), "counts": rec.damage.as_dict(),
                              "settings": {k: c[k] for k in config.DEST_KEYS},
                              "destination": c.get("DESTINATION_NAME", ""),
                              "captured": datetime.fromtimestamp(rec.started).strftime("%Y-%m-%d"),
                              "added": datetime.now().isoformat(timespec="seconds")})
            self.jobs = self.jobs[-100:]
            self.save()
        log("queued %s (%d steps)" % (name, len(items)))

    def find(self, name):
        return next((j for j in self.jobs if j["name"] == name), None)

    def approve(self, name):
        """A reviewed take: send whatever of it is held."""
        with self.lock:
            j = self.find(name)
            if not j:
                return False
            mdir = self.settings(j)["MASTERS_DIR"]
            if not any(it["kind"] == "store" for it in j["items"]) and mdir and j["masters"] \
                    and not any(os.path.isabs(m) for m in j["masters"]):
                j["items"].insert(0, {"src": "", "kind": "store", "dest": mdir,
                                      "state": "pending", "note": "", "tries": 0})
            for it in j["items"]:
                if it["state"] == "held":
                    it["state"] = "pending"
            self.save()
        log("approved %s" % name)
        self.retry_at = 0
        self.app.hook("approved", j)
        return True

    def discard(self, name):
        """Set a bad take's master aside: moved (never deleted) to a "discarded" folder beside MASTERS_DIR, or
        beside it on the capture disk, and none of it is uploaded. False if part of it has already gone."""
        with self.lock:
            j = self.find(name)
            if not j or not j["masters"]:
                return False
            mitems = [it for it in j["items"] if it["kind"] in MASTER_KINDS]
            if any(it["state"] in ("pending", "done") for it in mitems):
                return False  # already on its way
            if any(not os.path.isabs(s) for s in j["masters"]) and not self.app.open_card(j["card"]):
                return False  # the disk isn't here: marking it set aside would leave the master where it was
            moved = []
            for src in j["masters"]:
                p = os.path.join(self.app.card_dir(j["card"]), src)
                if not os.path.exists(p):
                    continue
                if os.path.isabs(src):  # on MASTERS_DIR: same filesystem, so a rename
                    aside = os.path.join(os.path.dirname(os.path.dirname(src)), "discarded")
                else:
                    aside = os.path.join(os.path.dirname(p), "discarded")
                os.makedirs(aside, exist_ok=True)
                os.replace(p, os.path.join(aside, os.path.basename(p)))
                moved.append(aside)
            for it in j["items"]:
                if it["state"] != "done":
                    it["state"], it["note"] = "discarded", ""
            j["discarded"] = moved[0] if moved else True
            self.save()
        log("set aside %s's master%s" % (name, " in " + moved[0] if moved else ""))
        self.app.hook("discarded", j)
        return True

    def retry(self):
        """Put skipped and failed steps back in the queue (after freeing space, say)."""
        with self.lock:
            for j in self.jobs:
                for it in j["items"]:
                    if it["state"] in ("skipped", "failed"):
                        it.update(state="pending", note="", tries=0)
            self.save()
        self.retry_at = 0

    # --- what the station needs to know ----------------------------------------

    def pending(self, card_id):
        return any(it["state"] == "pending" for j in self.jobs if j["card"] == card_id for it in j["items"])

    def moving(self, card_id):
        """The take whose master is still to move off this disk, or None. A recording started while a master is being
        copied and read back loses frames, or won't start at all; uploads go at network speed and don't count."""
        with self.lock:
            for j in self.jobs:
                if j["card"] == card_id and any(it["kind"] == "store" and it["state"] == "pending"
                                                for it in j["items"]):
                    return j["name"]
        return None

    def freeing(self, card_id):
        """Bytes the waiting moves to MASTERS_DIR will free on this disk (0 if MASTERS_DIR can't take them)."""
        total = 0
        with self.lock:
            for j in self.jobs:
                st = next((it for it in j["items"] if it["kind"] == "store" and it["state"] == "pending"), None)
                if j["card"] != card_id or not st:
                    continue
                size = sum(os.path.getsize(os.path.join(self.app.card_dir(card_id), src)) for src in j["masters"]
                           if not os.path.isabs(src) and os.path.exists(os.path.join(self.app.card_dir(card_id), src)))
                dest = st["dest"]
                if size and os.path.isdir(dest) and shutil.disk_usage(dest).free >= size + self.spare():
                    total += size
        return total

    def spare(self):
        return float(self.conf["MASTERS_SPARE_GB"] or 0) * 1e9

    def held(self):
        return [j["name"] for j in self.jobs if any(it["state"] == "held" for it in j["items"])]

    def any_jobs(self):
        return bool(self.jobs)

    def recent(self):
        """The last eight takes, plus any older one still waiting for review, newest first."""
        jobs = [j for j in self.jobs[:-8] if any(it["state"] == "held" for it in j["items"])] + self.jobs[-8:]
        out = []
        for j in jobs:
            e = j.get("entry") or {}
            out.append({"name": j["name"], "discarded": bool(j.get("discarded")), "id": e.get("id", ""), "title": e.get("title", ""), "added": j.get("added", ""), "mp4": j.get("mp4", True),
                        "destination": j.get("destination", ""), "damage": j.get("damage"), "counts": j.get("counts"),
                        "items": [{k: it[k] for k in ("kind", "state", "note")} for it in j["items"]]})
        return out[::-1]

    # --- doing the work ----------------------------------------------------------

    def mark(self, item, state, note):
        item["state"], item["note"] = state, note
        if state != "done":
            log("%s %s: %s" % (state, item["dest"], note))
        with self.lock:
            self.save()

    def store(self, job, item):
        dest, card = item["dest"], self.app.card_dir(job["card"])
        srcs = [s for s in job["masters"] + job.get("sidecars", []) if not os.path.isabs(s)]
        if not os.path.isdir(dest):  # never created here: missing usually means that disk isn't mounted
            return self.mark(item, "failed", "%s isn't there (disk not mounted?); the master stays on the capture "
                                             "disk" % dest)
        todo = [s for s in srcs if os.path.exists(os.path.join(card, s))]
        for s in todo:  # a copy cut off by a restart is never complete: drop it, or its room counts against the move
            with contextlib.suppress(OSError):
                os.remove(os.path.join(dest, os.path.basename(s)) + ".part")
        size = sum(os.path.getsize(os.path.join(card, s)) for s in todo)
        free = shutil.disk_usage(dest).free
        if free < size + self.spare():
            return self.mark(item, "failed", "the masters disk is full (needs %.0f GB, %.0f GB free); the master "
                                             "stays on the capture disk" % ((size + self.spare()) / 1e9, free / 1e9))
        done = 0
        for s in todo:
            p, q = os.path.join(card, s), os.path.join(dest, os.path.basename(s))
            if os.path.exists(q) and subprocess.run(lowprio(["cmp", "-s", "--", p, q])).returncode == 0:
                done += os.path.getsize(p)
                continue  # copied before a restart
            cp = subprocess.Popen(lowprio(["cp", "--", p, q + ".part"]), stdin=subprocess.DEVNULL)
            while cp.poll() is None:
                time.sleep(2)
                got = os.path.getsize(q + ".part") if os.path.exists(q + ".part") else 0
                self.progress = "%s: moving the master, %.1f of %.1f GB" % (job["name"], (done + got) / 1e9,
                                                                           size / 1e9)
            self.progress = "%s: checking the copy" % job["name"]
            if cp.returncode or subprocess.run(lowprio(["cmp", "-s", "--", p, q + ".part"])).returncode:
                with contextlib.suppress(OSError):
                    os.remove(q + ".part")
                item["tries"] += 1
                if item["tries"] >= 3:
                    return self.mark(item, "failed", "copying %s failed; the master stays on the capture disk" % s)
                self.retry_at = time.time() + 120
                return
            os.replace(q + ".part", q)
            done += os.path.getsize(p)
        names = [os.path.basename(s) for s in job["masters"] + job.get("sidecars", [])]
        md5 = os.path.join(dest, job["name"] + ".md5")
        if names and not os.path.exists(md5):
            self.progress = "%s: checksums of the copy" % job["name"]
            try:
                self.write_checksums(dest, names, md5)
            except OSError as e:
                return self.mark(item, "failed", str(e))
        with self.lock:
            for it in job["items"]:
                if it["kind"] in MASTER_KINDS:
                    it["src"] = os.path.join(dest, os.path.basename(it["src"]))
            job["masters"] = [os.path.join(dest, os.path.basename(s)) for s in job["masters"]]
            job["sidecars"] = [os.path.join(dest, os.path.basename(s)) for s in job.get("sidecars", [])]
            for s in todo:
                if s.endswith(".mkv"):
                    os.remove(os.path.join(card, s))  # the log and report stay on the capture disk as well
        log("moved %s's master to %s" % (job["name"], dest))
        self.mark(item, "done", "moved to the masters disk")

    def write_checksums(self, folder, names, path):
        """MD5 of files in folder, read back from the disk, in md5sum format."""
        r = subprocess.run(lowprio([sys.executable, "-m", "videocap.checksum", "--"] + list(dict.fromkeys(names))),
                           cwd=folder, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(__file__))))
        if r.returncode or len(r.stdout.splitlines()) != len(dict.fromkeys(names)):
            raise OSError("checksums failed: " + r.stderr.strip()[-200:])
        with open(path + ".tmp", "w") as f:
            f.write(r.stdout)
        os.replace(path + ".tmp", path)

    def rclone(self, *args):
        cfg = ["--config", self.conf["RCLONE_CONFIG"]] if self.conf["RCLONE_CONFIG"] else []
        return ["rclone"] + cfg + list(args)

    def root(self, job, to):
        return self.settings(job)["MP4_REMOTE" if to == "mp4" else "MASTER_REMOTE"]

    def remote(self, job, path, to):
        return join(self.root(job, to), path)

    def run_rclone(self, *args, timeout=600):
        return subprocess.run(self.rclone(*args), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout)

    def exists(self, job, path, to):
        """True/False whether a file is already at the destination; None if it can't be reached."""
        r = self.run_rclone("lsjson", "--stat", self.remote(job, path, to), timeout=120)
        if r.returncode == 0:
            return True
        return False if re.search(r"not found|doesn't exist|no such file", r.stderr, re.I) else None

    def free(self, job, to):
        """(ok, bytes free): bytes is None if the destination doesn't report it. ok False = unreachable."""
        root = self.root(job, to)
        r = self.run_rclone("about", "--json", root, timeout=120)
        if r.returncode:
            return ("support" in r.stderr.lower() or "not implemented" in r.stderr.lower()), None
        try:
            return True, json.loads(r.stdout).get("free")
        except ValueError:
            return True, None

    def earlier_copy(self, job, dest):
        """Files beside dest that belong to an earlier copy of that video: the video under any extension, and
        subtitles (timed to it). Descriptions, posters and transcripts still fit and stay. None if unreachable."""
        folder, _, base = dest.rpartition("/")
        stem = base.rsplit(".", 1)[0]
        r = self.run_rclone("lsf", "--files-only", self.remote(job, folder, "mp4") if folder else
                            self.root(job, "mp4"), timeout=120)
        if r.returncode:
            return None
        return [n for n in r.stdout.splitlines()
                if n.startswith(stem + ".") and n.rsplit(".", 1)[-1].lower() in VIDEO_EXT | SUBTITLE_EXT
                and not n.endswith(".partial")]

    def retire(self, job, dest):
        """Move the earlier copy of dest into .replaced/<this take>/ beside it. Nothing is deleted."""
        old = self.earlier_copy(job, dest)
        if old is None:
            return False
        folder = dest.rpartition("/")[0]
        pre = folder + "/" if folder else ""
        for n in old:
            if self.run_rclone("moveto", self.remote(job, pre + n, "mp4"),
                               self.remote(job, "%s.replaced/%s/%s" % (pre, job["name"], n), "mp4")).returncode:
                return False
            log("moved %s%s aside to %s.replaced/%s/" % (pre, n, pre, job["name"]))
        return True

    def upload(self, job, item):
        if item["kind"] == "store":
            return self.store(job, item)
        c, to = self.settings(job), item["to"]
        path = os.path.join(self.app.card_dir(job["card"]), item["src"])  # moved files have absolute paths
        label = "%s %s" % (job["name"], item["kind"])
        where = self.root(job, to)
        if item["kind"] == "checksums":
            others = [it for it in job["items"] if it.get("to") == "master" and it is not item]
            if any(it["state"] != "done" for it in others):
                return self.mark(item, "skipped", "waits until the other files are uploaded")
            if not os.path.exists(path):
                self.progress = "%s: checksums" % job["name"]
                self.write_checksums(os.path.dirname(path), [os.path.basename(it["src"]) for it in others], path)
        if item["kind"] == "titles":
            if item.get("replace") or any(it.get("replace") for it in job["items"]):
                exists = self.exists(job, item["dest"], to)
                if exists is None:
                    self.retry_at = time.time() + 120
                    item["note"] = "can't reach %s, retrying" % where
                    return
                if exists:  # the library's own description stays
                    return self.mark(item, "done", "kept the description already there")
            with open(path, "w") as f:
                f.write(collection.nfo(job["entry"], job["captured"], c["DIGITIZED_BY"], c["COLLECTION"]))
        if not os.path.exists(path):
            if not self.app.card_ready(job["card"]):
                return  # the disk went away; try again when it's back
            return self.mark(item, "missing", "file not found")
        size = os.path.getsize(path)
        margin = float(c["REMOTE_MIN_FREE_GB"] or 0) * 1e9
        if margin > 0:
            ok, free = self.free(job, to)
            if not ok:
                self.retry_at = time.time() + 120
                item["note"] = "can't reach %s, retrying" % where
                return
            if free is not None and size + margin > free:
                return self.mark(item, "skipped", "destination too full: needs %.1f GB, %.1f GB free (keeping "
                                                  "%.0f GB spare)" % (size / 1e9, free / 1e9, margin / 1e9))
        # under a temporary name, so a library never sees half a file
        cmd = self.rclone("copyto", path, self.remote(job, item["dest"] + ".partial", to), "--stats", "5s",
                          "--stats-one-line", "--stats-log-level", "NOTICE", "--retries", "2")
        if c["UPLOAD_BWLIMIT"]:
            cmd += ["--bwlimit", c["UPLOAD_BWLIMIT"]]
        log("uploading %s (%.1f GB) to %s" % (label, size / 1e9, self.remote(job, item["dest"], to)))
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True, errors="replace")
        err = []
        for line in p.stderr:
            m = re.search(r"([\d.]+ \S*B) / ([\d.]+ \S*B), (\d+)%, ([\d.]+ \S*B/s)(?:, ETA (\S+))?", line)
            if m:
                self.progress = "%s: %s%% of %s at %s, %s left" % (label, m.group(3), m.group(2), m.group(4),
                                                                  m.group(5) or "?")
            elif "Can't save config" not in line:
                err.append(line.strip())
        # only once the new copy is whole does an earlier one make way for it
        ok = (p.wait() == 0 and (not item.get("replace") or self.retire(job, item["dest"]))
              and self.run_rclone("moveto", self.remote(job, item["dest"] + ".partial", to),
                                  self.remote(job, item["dest"], to)).returncode == 0)
        if ok:
            log("uploaded " + label)
            return self.mark(item, "done", "")
        item["tries"] += 1
        msg = (err[-1] if err else "rclone failed")[-200:]
        log("upload of %s failed: %s" % (label, msg))
        if item["tries"] >= 5:
            return self.mark(item, "failed", msg)
        item["note"] = "retrying: " + msg
        self.retry_at = time.time() + 120
        with self.lock:
            self.save()

    def next_step(self, ready):
        """The next (job, item) to work on. A move off the capture disk goes first, whichever take it belongs to:
        otherwise approving an older take would hold the newest take's move behind an hour-long upload while the
        next item fills the disk."""
        with self.lock:
            for j in self.jobs:
                it = next((it for it in j["items"] if it["kind"] == "store" and it["state"] == "pending"), None)
                if j["card"] in ready and it:
                    return j, it
            for j in self.jobs:
                if j["card"] in ready:
                    it = next((it for it in j["items"] if it["state"] == "pending"), None)
                    if it:
                        return j, it
        return None, None

    def run(self):
        while True:
            time.sleep(2)
            ready = {c for c in [self.app.upload_card] + list(self.app.other_cards) if c}
            if not ready or time.time() < self.retry_at:
                continue
            job, item = self.next_step(ready)
            if item:
                try:
                    self.upload(job, item)
                except Exception as e:  # keep the queue alive
                    log("upload error: %r" % e)
                    self.retry_at = time.time() + 60
                self.progress = ""
