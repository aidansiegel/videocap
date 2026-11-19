"""The station: waits for a disk and a picture, records each take by itself, and hands it to the queue.

Every second, tick() looks at what's plugged in and decides. While waiting for play it records in standby,
starting over every STANDBY_SEC, so the source's first frame lands in a recording that's already running (a camera
sends no picture until its tape moves, and a recording started by the picture misses the first seconds).
"""
import contextlib
import csv
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime

from . import collection, config, hardware, mdns, settings
from .hardware import log
from .queue import Queue
from .recording import Recording

MOUNT = os.path.join(config.RUN, "card")
OTHER = os.path.join(config.RUN, "other")  # earlier capture disks, while their queued work finishes
SNAP = os.path.join(config.RUN, "snap.jpg")
PAGE_LIST = os.path.join(config.STATE_DIR, "items-from-page.txt")  # an item list pasted or loaded on the page
DETAIL_KEYS = ("id", "title", "format", "date", "notes")  # what the page can type for a capture


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


class App:
    def __init__(self, conf_path=None):
        self.conf_path = conf_path
        self.conf = config.load(conf_path)
        for k in config.unknown_keys(self.conf):
            log("unknown setting %s (ignored)" % k)
        self.led = hardware.Led(self.conf["LEDS"])
        self.sig = hardware.Signal()
        self.state, self.message = "waiting", "Starting"
        self.rec = None
        self.armed = True            # a new picture or disk starts a recording
        self.force = False           # Start pressed
        self.stop_requested = False
        self.last_card_id = None
        self.last_signal = False
        self.signal = None
        self.lost_since = None
        self.input_name = ""
        self.inputs_tried = 0
        self.dev = self.card = self.audio = None
        self.last_result = ""
        self.quit = False
        self.queue = Queue(self, os.path.join(config.STATE_DIR, "queue.json"))
        self.upload_card = None      # disk the queue may read from right now
        self.other_cards = {}        # earlier capture disks still plugged in with queued work: id -> mount point
        self.standby_until = time.time() + self.f("STANDBY_HOURS") * 3600
        self.standby_retry = 0       # after a standby recording failed to start, try again from this time
        self.restart_standby = False # the next item changed: start the standby recording over under its name
        self.view_until = 0          # a capture is being watched on the page: keep its disk mounted until then
        self.viewed = False          # the disk was kept mounted for watching
        self.others_frozen = True    # so the first pause_others(False) thaws them, in case a crash left them frozen
        self.items = self.load_items()
        state = self.load_state()
        self.next_item = state.get("next", self.items[0]["id"] if self.items else "")
        if self.next_item and not self.item(self.next_item):
            self.next_item = self.items[0]["id"] if self.items else ""
        # what's typed on the page for the next capture (id, title, format, date, notes): starts as the next listed
        # item's, and names the capture
        self.details = state.get("details") or self.item_details(self.item(self.next_item))
        self.destination = state.get("destination", "")  # "" = not picked yet: see config.apply_destination
        self.next_aspect = state.get("aspect") or self.item_aspect(self.next_item)
        os.makedirs(MOUNT, exist_ok=True)

    def f(self, key):
        return float(self.conf[key] or 0)

    def station(self):
        return self.conf["STATION_NAME"] or socket.gethostname().split(".")[0]

    def load_items(self):
        """The list given on the page if there is one, else ITEM_LIST."""
        path = PAGE_LIST if os.path.exists(PAGE_LIST) else self.conf["ITEM_LIST"]
        try:
            return collection.load(path)
        except (OSError, ValueError, csv.Error) as e:
            log("couldn't read the item list %s: %s" % (path, e))
            return []

    def set_items(self, text):
        """A list pasted or loaded on the page replaces ITEM_LIST; an empty one goes back to ITEM_LIST."""
        try:
            items = collection.parse(text)
        except (ValueError, csv.Error) as e:
            log("couldn't read the item list from the page: %s" % e)
            return None
        if text.strip() and not items:
            return None
        if items:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with open(PAGE_LIST + ".new", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(PAGE_LIST + ".new", PAGE_LIST)
        else:
            with contextlib.suppress(OSError):
                os.remove(PAGE_LIST)
        self.items = self.load_items()
        if not self.item(self.next_item):
            self.set_next(self.items[0]["id"] if self.items else "")
        log("item list from the page: %d items" % len(self.items) if items else "item list: back to ITEM_LIST")
        return len(self.items)

    @staticmethod
    def item_details(item):
        return {k: (item or {}).get(k, "") for k in DETAIL_KEYS}

    def capture_item(self):
        """The item a capture is of: what's typed on the page, over the chosen list item if it's still that one."""
        listed = self.item(self.next_item)
        t = dict(listed) if listed and self.details.get("id") == listed["id"] else {}
        t.pop("file", None)  # named from the title as typed
        t.update({k: v for k, v in self.details.items() if k in DETAIL_KEYS})
        return collection.make_item(t)

    def set_details(self, d):
        """Name and describe the next capture, or the one recording now (its files are renamed when it finishes)."""
        if not isinstance(d, dict):
            return False
        self.details = {k: str(d.get(k, self.details.get(k, "")) or "")[:2000].strip() for k in DETAIL_KEYS}
        self.save_state()
        rec = self.rec
        if rec:
            rec.item = self.capture_item()
            rec.label = rec.item and self.item_label(rec.item) or os.path.basename(rec.base)
        return True

    @staticmethod
    def item_label(t):
        return "%s: %s" % (t["id"], t["title"]) if t["id"] and t["id"] != t["title"] else t["title"]

    def load_state(self):
        try:
            with open(os.path.join(config.STATE_DIR, "state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def item(self, tid):
        return next((t for t in self.items if t["id"] == tid), None) if tid else None

    def set_next(self, tid, keep=()):
        """Which item the next recording is (blank = unlisted); kept across restarts. Its details replace what was
        typed, except the keys in keep. True if the picture shape changed (a standby recording must start over)."""
        self.next_item = tid if self.item(tid) else ""
        old = self.next_aspect if hasattr(self, "next_aspect") else None
        self.next_aspect = self.item_aspect(self.next_item)  # each item starts from its own shape
        fresh = self.item_details(self.item(self.next_item))
        self.details = {k: (self.details.get(k, "") if k in keep and not fresh[k] else fresh[k]) for k in DETAIL_KEYS}
        self.save_state()
        log("next item: %s" % (self.next_item or "unlisted"))
        return old != self.next_aspect

    def save_state(self):
        save_json(os.path.join(config.STATE_DIR, "state.json"),
                  {"next": self.next_item, "destination": self.destination, "aspect": self.next_aspect,
                   "details": self.details})

    def item_aspect(self, tid):
        """An item's picture shape: its aspect column, else the ASPECT setting (4:3 unless set)."""
        t = self.item(tid)
        return (t and t.get("aspect")) or collection.aspect(self.conf["ASPECT"]) or "4:3"

    def set_aspect(self, a):
        """The next capture's picture shape, from the page. A standby recording starts over with it, since it becomes
        the capture when play is pressed."""
        a = collection.aspect(a)
        if not a:
            return False
        if a != self.next_aspect:
            self.next_aspect = a
            self.save_state()
            self.restart_standby = True
            log("next capture: %s" % a)
        return True

    def dest_conf(self):
        """The settings with the chosen destination applied (DEFAULT_DESTINATION until one is picked)."""
        return config.apply_destination(self.conf, self.destination)

    def set_destination(self, dest_id):
        """Where captures started from now on go; kept across restarts. Captures already queued keep theirs."""
        if dest_id not in {d["id"] for d in config.destinations(self.conf)}:
            return False
        self.destination = dest_id
        self.save_state()
        if self.rec and self.rec.standby:  # a standby recording hasn't started anything yet: it takes the new one
            self.rec.dest = self.dest_conf()
        log("destination: %s" % self.dest_conf()["DESTINATION_NAME"])
        return True

    def set_state(self, state, message, led=None):
        if (state, message) != (self.state, self.message):
            log("%s: %s" % (state, message))
        self.state, self.message = state, message
        self.led.set(led or state)

    # --- input and signal ---------------------------------------------------------

    def choose_input(self, dev):
        inputs = hardware.list_inputs(dev)
        svideo = [i for i in inputs if re.search(r"s-?video", i[1], re.I)]
        comp = [i for i in inputs if re.search(r"composite", i[1], re.I)]
        want = self.conf["INPUT"].lower()
        order = svideo if want == "svideo" else comp if want == "composite" else svideo + comp
        return order or inputs[:1]

    def check_signal(self, dev):
        """True/False, or None when the driver doesn't say. Without a picture, tries the other inputs now and then
        (INPUT=auto); forcing the input the camera is on is better, since a tape that starts while the other input
        is selected loses seconds."""
        if dev == "test":
            self.input_name = "test"
            return True
        sig = self.sig.get(dev, self.conf)
        if sig:
            return True
        self.inputs_tried += 1
        if self.inputs_tried % 3 != 1:  # switching inputs isn't free: every few seconds at most
            return sig
        cands = self.choose_input(dev)
        if len(cands) == 1 and cands[0][1] == self.input_name:
            return sig  # nothing else to try, and setting it again resets the decoder's lock
        for idx, name in cands:
            hardware.sh("v4l2-ctl", "-d", dev, "-i", str(idx))
            hardware.sh("v4l2-ctl", "-d", dev, "-s", self.conf["STANDARD"].lower())
            self.sig.forget(dev)  # a lock on the last input says nothing about this one
            self.input_name = name
            for _ in range(4):  # a decoder lock can take a second to settle
                time.sleep(0.4)
                sig = self.sig.get(dev, self.conf)
                if sig is not False:
                    return sig
        return False

    # --- disks ----------------------------------------------------------------------

    def card_dir(self, card_id=None):
        """Where a disk's files are: the capture disk, or an earlier capture disk still plugged in."""
        if card_id and card_id != self.last_card_id and card_id in self.other_cards:
            return self.other_cards[card_id]
        return self.conf["TEST_CARD_DIR"] or MOUNT

    def card_ready(self, card_id):
        return bool(card_id) and (card_id == self.upload_card or card_id in self.other_cards)

    def mount(self, card, where=MOUNT):
        if self.conf["TEST_CARD_DIR"]:
            return True
        return hardware.mount(card, where, self.conf["DISK_OWNER"])

    def open_card(self, card_id):
        """Make a disk's files reachable now, if that disk is plugged in."""
        return card_id in self.other_cards or (card_id == self.last_card_id and bool(self.card)
                                               and self.mount(self.card))

    def unmount(self):
        if time.time() < self.view_until:  # someone's watching a capture from this disk: unmount when they stop
            self.viewed = True
            return
        if self.conf["TEST_CARD_DIR"] or not os.path.ismount(MOUNT):
            os.sync()
            return
        hardware.umount(MOUNT, tries=5)

    def watch_path(self, name):
        """The MP4 of a listed capture, to watch on the page: its disk is mounted (if it's plugged in) and kept
        mounted while it's watched. None if it isn't here."""
        job = self.queue.find(name)
        if not job:
            return None
        card = job["card"]
        here = card in self.other_cards or (card == self.last_card_id and self.card)
        if not here:
            return None
        self.view_until = time.time() + 90
        path = os.path.join(self.card_dir(card), self.conf["FOLDER"], name + "_access.mp4")
        for _ in range(10):  # the main loop mounts the disk within a second or two
            if os.path.exists(path):
                return path
            time.sleep(0.5)
        return None

    def keep_other_cards(self, card_id):
        """Mount earlier capture disks that are still plugged in and still have queued work, each at
        OTHER/<id>, so a full disk can be swapped for an empty one while its takes keep moving and uploading."""
        want = {j["card"] for j in self.queue.jobs if j["card"] and j["card"] != card_id
                and any(it["state"] == "pending" for it in j["items"])}
        if self.conf["TEST_CARD_DIR"]:
            want = set()
        parts = {p.get("uuid"): p for p in hardware.usb_partitions()} if want else {}
        keep = {}
        for cid in want:
            p = parts.get(cid)
            if not p or p.get("mountpoint") not in (None, MOUNT, os.path.join(OTHER, cid)):
                continue
            where = os.path.join(OTHER, cid)
            if p.get("mountpoint") == MOUNT:  # it was the capture disk until now
                hardware.umount(MOUNT)
            if self.mount(p, where):
                keep[cid] = where
        for cid, where in self.other_cards.items():
            if cid not in keep and os.path.ismount(where):
                hardware.umount(where)
        self.other_cards = keep

    # --- recording ------------------------------------------------------------------

    def take_base(self, folder, item, when=None):
        when = datetime.fromtimestamp(when) if when else datetime.now()
        if item:  # the capture time keeps every take of an item apart
            return os.path.join(folder, "%s - captured %s" % (item["file"], when.strftime("%Y-%m-%d %H%M")))
        nums = [int(m.group(1)) for m in (re.match(r"capture(\d+)_", n) for n in os.listdir(folder)) if m]
        return os.path.join(folder, "capture%03d_%s" % (max(nums, default=0) + 1, datetime.now().strftime("%Y-%m-%d_%H%M")))

    def start(self, dev, card, audio, standby=False):
        if not self.mount(card):
            self.armed = False
            return self.set_state("error", "Couldn't mount the disk (%s)" % card.get("fstype"), "error")
        folder = os.path.join(self.card_dir(), self.conf["FOLDER"])
        os.makedirs(folder, exist_ok=True)
        item = self.capture_item()
        base = self.take_base(folder, item)
        if standby and os.path.exists(base + ".log"):
            # a take already has this minute's name (one just stopped by hand): a standby recording would write over
            # it, then delete it. Stand by from the next minute.
            self.standby_retry = time.time() + 61 - datetime.now().second
            return
        free = shutil.disk_usage(folder).free / 1e9
        mode, note = self.conf["MODE"], ""
        if free < self.f("MIN_FREE_GB") + 1:
            self.unmount()
            self.armed = False
            return self.set_state("error", "Disk is full (%.1f GB free)" % free, "error")
        # the last take's master moving off the disk frees its room within minutes, so it counts as free
        if mode != "access" and free + self.queue.freeing(self.last_card_id) / 1e9 < self.f("MASTER_MIN_FREE_GB"):
            if self.conf["NO_ROOM"] != "mp4":
                self.armed = False
                return self.set_state("error", "Not recording: only %.0f GB free on %s, and a master needs %.0f GB. "
                                      "Make room, then press Start or turn the source off and on."
                                      % (free, card.get("label") or "the disk", self.f("MASTER_MIN_FREE_GB")), "error")
            mode, note = "access", " (only %.0f GB free, so MP4 only, no lossless master)" % free
        self.armed = self.force = self.stop_requested = False
        self.pause_others(True)
        hardware.set_gain(self.conf, audio)
        hardware.compact_memory()
        try:
            rconf = dict(self.conf, ASPECT=self.next_aspect)
            self.rec = Recording(rconf, dev, audio, base, mode, card.get("fstype") == "vfat", SNAP)
            self.rec.aspect = self.next_aspect
        except OSError as e:
            self.pause_others(False)
            self.unmount()
            return self.set_state("error", "Couldn't start recording: %s" % e, "error")
        self.rec.item = item
        self.rec.dest = self.dest_conf()  # where this capture goes, whatever is picked later
        self.lost_since = None
        self.rec.label = (self.item_label(item) if item else os.path.basename(base)) + note
        if standby:
            self.rec.standby = True
            self.set_state("ready", "Ready: standing by, so recording begins with the first frame when you "
                           "press play", "ready")
        else:
            self.set_state("recording", "Recording %s" % self.rec.label)

    def pause_others(self, frozen):
        """Freeze other heavy jobs on this machine (PAUSE_UNITS) while recording, and resume them after. Even at the
        lowest priority they can starve a capture of frames and sound."""
        if frozen == self.others_frozen:
            return  # standing by keeps them frozen across its restarts
        self.others_frozen = frozen
        for unit in self.conf["PAUSE_UNITS"].split():
            r = subprocess.run(["systemctl", "freeze" if frozen else "thaw", unit],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                log("%s %s" % ("froze" if frozen else "resumed", unit))

    def write_report(self, rec, reason, error, held):
        """A sidecar JSON beside the master: what the take is, how it ended, and what it lost."""
        report = {"name": os.path.basename(rec.base), "station": self.station(),
                  "item": rec.item and {k: rec.item[k] for k in ("id", "label", "title", "format", "date", "notes") if rec.item.get(k)},
                  "started": datetime.fromtimestamp(rec.started).isoformat(timespec="seconds"),
                  "picture_locked": rec.signal_at and datetime.fromtimestamp(rec.signal_at).isoformat(timespec="seconds"),
                  "seconds": round(rec.duration()), "stopped_because": reason, "error": error,
                  "held_for_review": held, "mode": rec.mode, "aspect": getattr(rec, "aspect", "4:3"), "profile": self.conf["PROFILE"],
                  "standard": self.conf["STANDARD"], "input": self.input_name,
                  "damage": rec.damage.as_dict(), "damage_summary": rec.damage.summary(),
                  "files": [os.path.basename(p) for p in glob.glob(glob.escape(rec.base) + "_*")],
                  "software": "videocap"}
        with contextlib.suppress(OSError):
            save_json(rec.base + ".json", report)

    def rename_take(self, rec):
        """Files are named when recording starts; a name typed or changed since renames them now."""
        if not rec.item:
            return
        folder = os.path.dirname(rec.base)
        new = self.take_base(folder, rec.item, rec.started)
        if new == rec.base or glob.glob(glob.escape(new) + "[._]*"):
            return
        try:
            for p in glob.glob(glob.escape(rec.base) + "_*") + glob.glob(glob.escape(rec.base) + ".*"):
                os.rename(p, new + p[len(rec.base):])
        except OSError as e:
            log("couldn't rename %s: %s" % (os.path.basename(rec.base), e))
            return
        log("renamed %s to %s" % (os.path.basename(rec.base), os.path.basename(new)))
        rec.base = new

    def finish(self, reason, error=False):
        self.set_state("finishing", "Finishing files: " + reason)
        rec, self.rec = self.rec, None
        rec.stop(reason)
        self.rename_take(rec)
        by_hand = reason == "stopped from the web page"
        # a take of a few seconds (the picture dropped out just after play, and it was started again) is a
        # false start, as is a picture that never moved (Stop before play, the wrong input)
        short = rec.motion_seen and not error and not by_hand and rec.duration() < self.f("MIN_TAKE_MINUTES") * 60
        if not rec.motion_seen and not error:
            reason += "; the picture never moved, so it isn't kept for the queue and the next item stays the same"
        elif os.path.exists(rec.base + "_access.mp4"):
            self.set_state("finishing", "Finishing files: " + reason + ". Preparing the MP4")
            mp4, cut = rec.base + "_access.mp4", 0.0
            # standby, or Start before play, recorded the wait: the MP4 starts at the keyframe before (picture lock
            # - 3 s), so nothing of the recording is cut; the master keeps it all
            wait = rec.signal_at and rec.first_frame_at and rec.signal_at - rec.first_frame_at
            if wait and wait > 15:
                cut = hardware.keyframe_before(mp4, wait - 3)
            if hardware.faststart(mp4, cut) and cut:
                with contextlib.suppress(OSError), open(rec.base + ".log", "a") as f:
                    f.write("videocap MP4 starts %.3f s into the recording (the wait before the picture is cut; the "
                            "master keeps it)\n" % cut)
        self.pause_others(False)
        held = by_hand or short
        if rec.motion_seen:
            self.write_report(rec, reason, error, held)
            if rec.item and self.conf["NFO"] == "1" and os.path.exists(rec.base + "_access.mp4"):
                with contextlib.suppress(OSError), open(rec.base + ".nfo", "w") as f:
                    f.write(collection.nfo(rec.item, datetime.fromtimestamp(rec.started).strftime("%Y-%m-%d"),
                                           self.conf["DIGITIZED_BY"], self.conf["COLLECTION"]))
        os.sync()
        if self.card and self.last_card_id and rec.motion_seen:
            self.queue.add(self.last_card_id, self.card_dir(), rec, hold_all=held)
        uploading = self.card and self.queue.pending(self.last_card_id)
        if not uploading:
            self.unmount()
        if by_hand and rec.motion_seen:
            reason += "; held until you approve it, and the next item stays the same"
        elif short:
            reason += ("; only %d s long, so it's taken as a false start: held until you approve it, and the next "
                       "item stays the same" % rec.duration())
        name = (rec.item["id"] or rec.item["title"]) if rec.item else os.path.basename(rec.base)
        self.last_result = "%s: %s. Capture: %s" % (name, reason, rec.damage.summary())
        log("%s capture: %s" % (name, rec.damage.summary()))
        if not error and rec.motion_seen and not held:  # on to the next: the listed item after this one, if any
            ids = [t["id"] for t in self.items]
            done = rec.item["id"] if rec.item and rec.item["id"] in ids else self.next_item
            i = ids.index(done) + 1 if done in ids else len(ids)
            self.set_next(ids[i] if i < len(ids) else "", keep=("format",))  # the next one is likely the same format
        if self.signal is not None:  # the next take needs a fresh picture or disk
            self.last_signal = self.signal
        self.standby_until = time.time() + self.f("STANDBY_HOURS") * 3600
        if rec.motion_seen:
            self.hook("finished", {"name": os.path.basename(rec.base), "entry": rec.item, "card": self.last_card_id,
                                   "base": rec.base, "damage": rec.damage.summary(), "held": held, "error": error})
        if error:
            self.set_state("error", "%s stopped: %s" % (name, reason), "error")
        else:
            self.set_state("done", "%s finished (%s).%s" % (name, reason, "" if uploading else
                                                             " Safe to unplug the disk."), "ready")

    def hook(self, event, job):
        """Run HOOK with the event (finished, approved, discarded) and the take's files in the environment, at the
        lowest priority. Keep it light, or have it hand heavy work to another machine: it can run during a take."""
        prog = self.conf["HOOK"].strip()
        if not prog:
            return
        if "base" in job:
            base = job["base"]
            masters = sorted(glob.glob(glob.escape(base) + "_master*.mkv"))
        else:
            folder = os.path.join(self.card_dir(job.get("card")), self.conf["FOLDER"])
            base = os.path.join(folder, job["name"])
            masters = [os.path.join(self.card_dir(job.get("card")), m) for m in job.get("masters", [])]
        t = job.get("entry") or {}
        env = dict(os.environ, VIDEOCAP_EVENT=event, VIDEOCAP_NAME=job["name"], VIDEOCAP_ID=t.get("id", ""),
                   VIDEOCAP_LABEL=t.get("label", ""), VIDEOCAP_TITLE=t.get("title", ""),
                   VIDEOCAP_FORMAT=t.get("format", ""), VIDEOCAP_DATE=t.get("date", ""), VIDEOCAP_NOTES=t.get("notes", ""),
                   VIDEOCAP_MASTERS=os.pathsep.join(masters), VIDEOCAP_MP4=base + "_access.mp4",
                   VIDEOCAP_LOG=base + ".log", VIDEOCAP_REPORT=base + ".json",
                   VIDEOCAP_DAMAGE=job.get("damage") or "", VIDEOCAP_STATION=self.station())

        def run():
            try:
                p = subprocess.run(hardware.lowprio([prog, event]), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=6 * 3600)
                out = (p.stdout + p.stderr).strip()
                log("hook %s for %s: exit %d%s" % (event, job["name"], p.returncode, (": " + out[-300:]) if out else ""))
            except (OSError, subprocess.TimeoutExpired) as e:
                log("hook %s for %s failed: %s" % (event, job["name"], e))
        threading.Thread(target=run, daemon=True).start()

    # --- the loop ---------------------------------------------------------------------

    def idle(self, state, message, led=None):
        """Set an idle state, unless uploads are what's really going on."""
        if self.upload_card:
            self.set_state("uploading", "Uploading%s. Leave the disk connected."
                           % (": " + self.queue.progress if self.queue.progress else ""), "uploading")
        else:
            self.set_state(state, message, led)

    def tick(self):
        if not self.rec or self.rec.standby:
            self.conf = config.load(self.conf_path)
        dev_info = hardware.find_video(self.conf)
        dev = dev_info[0] if dev_info else None
        card = ({"path": "test", "fstype": "test", "label": "test folder"} if self.conf["TEST_CARD_DIR"]
                else hardware.find_card(self.conf["CARD_LABEL"], MOUNT))
        self.dev, self.card = dev_info, card
        self.audio = hardware.find_audio(self.conf, dev_info[1]) if dev_info else None
        card_id = card and (card.get("uuid") or card.get("path"))
        if card_id != self.last_card_id:
            self.last_card_id = card_id
            if card_id:
                log("disk connected: %s %s" % (card.get("path"), card.get("label") or ""))
                self.armed = True

        # the queue keeps the disk mounted, and carries on while the next take records
        was_uploading = self.upload_card is not None
        uploading = bool(card_id and self.queue.pending(card_id))
        if uploading and not self.mount(card):
            uploading = False
        self.upload_card = card_id if uploading else None
        self.keep_other_cards(card_id)
        if not card and not self.conf["TEST_CARD_DIR"] and os.path.ismount(MOUNT):
            subprocess.run(["umount", "-l", MOUNT])  # pulled without waiting

        if time.time() < self.view_until:  # a capture is being watched: its disk stays mounted
            if card and not self.conf["TEST_CARD_DIR"] and not os.path.ismount(MOUNT):
                self.viewed = self.mount(card) or self.viewed
        elif self.viewed and not self.rec and not uploading:
            self.viewed = False
            self.unmount()
        if self.rec:
            return self.tick_recording(dev, card)
        if was_uploading and not uploading:
            self.unmount()
            if card:
                self.set_state("done", "Uploads finished. Safe to unplug the disk.", "ready")
        if not dev:
            if card and self.state == "done" and not self.upload_card:
                return  # source unplugged after a take: keep "safe to unplug" showing
            return self.idle("waiting", "Plug in the capture device")
        if not card:
            want = self.conf["CARD_LABEL"]
            return self.idle("waiting", "Connect the capture disk" + (" (partition named %s)" % want if want else ""))
        sig = self.check_signal(dev)
        self.signal = sig
        if sig is True and self.last_signal is False:
            self.armed = True
        if sig is not None:
            self.last_signal = sig
        if sig is False and not self.force:
            if self.standby_ok():
                self.start(dev, card, self.audio, standby=True)
                if not self.rec and self.standby_retry <= time.time():  # no room for a master, disk trouble, ...
                    self.standby_retry = time.time() + 300
                return
            if self.state == "error" and time.time() < self.standby_retry:
                return  # keep standby's error on screen
            return self.idle("ready", "Disk and capture device ready. Waiting for a picture")
        if not self.armed and not self.force:
            if self.upload_card or self.state not in ("done", "error"):  # keep the result on screen
                self.idle("done", "Ready. Turn the source off and on, reconnect the disk, or press Start to record "
                          "again", "ready")
            return
        self.start(dev, card, self.audio)

    def standby_ok(self):
        now = time.time()
        return (self.f("STANDBY_SEC") > 0 and self.signal is False and now < self.standby_until
                and now >= self.standby_retry and not self.queue.moving(self.last_card_id))

    def drop_standby(self, again=False):
        """Throw away a standby recording that never got a picture (it holds only the wait before play), and start
        the next one at once so the source's first frame lands in it."""
        rec, self.rec = self.rec, None
        rec.kill()
        killed = time.time()
        for p in rec.files + [rec.base + ".log"]:
            a, pct, b = p.partition("%03d")  # FAT32 segments
            for f in glob.glob(glob.escape(a) + "[0-9][0-9][0-9]" + glob.escape(b)) if pct else [p]:
                with contextlib.suppress(OSError):
                    os.remove(f)
        if again and self.standby_ok() and self.dev and self.card:
            self.start(self.dev[0], self.card, self.audio, standby=True)
            if self.rec:
                self.rec.after_kill = killed
                return
            self.standby_retry = max(self.standby_retry, time.time() + 300 if self.state == "error" else 0)
        if not self.rec:
            self.pause_others(False)
            self.idle("ready", "Disk and capture device ready. Waiting for a picture")

    def tick_standby(self, dev, card):
        rec, now = self.rec, time.time()
        if self.stop_requested:  # Stop while standing by: stop standing by until the next take
            self.stop_requested = False
            self.standby_until = now
            return self.drop_standby()
        if rec.proc.poll() is not None or not card or not dev:
            self.standby_retry = now + 60
            return self.drop_standby()
        if rec.after_kill and rec.first_frame_at:
            log("standby restart: %.2f s from the last frame of one to the first of the next"
                % (rec.first_frame_at - rec.after_kill))
            rec.after_kill = None
        sig = True if dev == "test" else self.sig.get(dev, self.conf)
        self.signal = sig
        if sig is None or sig:  # a picture (or a driver that can't say): it's a real recording now, from before play
            rec.standby = False
            rec.dest = self.dest_conf()  # the destination picked by the time play was pressed
            rec.item = self.capture_item()  # and the name typed by then
            rec.label = rec.item and self.item_label(rec.item) or os.path.basename(rec.base)
            rec.signal_at = now
            rec.damage.reset()  # what the device did while there was nothing to capture isn't damage to the recording
            rec.note("picture locked on %s %.1f s after the standby recording started"
                     % (self.input_name or "?", now - rec.started))
            log("picture locked %.1f s after the standby recording started" % (now - rec.started))
            self.lost_since = None
            self.armed = False
            return self.set_state("recording", "Recording %s" % rec.label)
        if self.restart_standby:
            self.restart_standby = False
            return self.drop_standby(again=True)
        # start over only on a still picture: a changing one may be the source starting before its lock is reported
        if now - rec.started >= self.f("STANDBY_SEC") and (rec.frozen or not rec.ever_frozen):
            return self.drop_standby(again=now < self.standby_until)

    def tick_recording(self, dev, card):
        if self.rec.standby:
            return self.tick_standby(dev, card)
        rec, now = self.rec, time.time()
        rec.update()
        if rec.proc.poll() is not None:
            return self.finish("capture stopped unexpectedly (ffmpeg exit %s); see the .log on the disk"
                               % rec.proc.returncode, error=True)
        if self.stop_requested:
            return self.finish("stopped from the web page")
        if not card:
            return self.finish("disk unplugged while recording", error=True)
        if dev != "test":
            sig = self.sig.get(dev, self.conf)
            self.signal = sig
            if sig is False:
                if rec.signal_at is None:  # Start, before play: a camera sends nothing until its tape moves
                    if now - rec.started >= self.f("PREPLAY_SEC"):
                        return self.finish("no picture within %g s of Start" % self.f("PREPLAY_SEC"))
                else:
                    self.lost_since = self.lost_since or now
                    if now - self.lost_since >= self.f("SIGNAL_LOST_SEC"):
                        return self.finish("signal lost (source off or unplugged)")
            else:
                self.lost_since = None
        if rec.signal_at is None and (dev == "test" or self.signal is not False):
            rec.signal_at = now
            rec.note("picture locked on %s %.1f s after the recording started" % (self.input_name or "?",
                                                                                  now - rec.started))
        # each of these is off at 0, for a live source (a camera feed, a console) that has no end of its own
        frozen_min, still_min, max_min = (self.f(k) for k in ("STOP_AFTER_FROZEN_MIN", "GIVE_UP_NO_MOTION_MIN",
                                                              "MAX_MINUTES"))
        if frozen_min > 0 and rec.motion_seen and rec.frozen and now - rec.frozen_since >= frozen_min * 60:
            return self.finish("source ended (picture frozen %g min)" % frozen_min)
        if still_min > 0 and not rec.motion_seen and now - (rec.signal_at or rec.started) >= still_min * 60:
            return self.finish("no moving picture for %g min" % still_min)
        if max_min > 0 and rec.duration() >= max_min * 60:
            return self.finish("reached the %g minute limit" % self.f("MAX_MINUTES"))
        if shutil.disk_usage(os.path.dirname(rec.base)).free / 1e9 < self.f("MIN_FREE_GB"):
            return self.finish("disk full")

    # --- what the web page shows --------------------------------------------------------

    def masters_free(self):
        dest = self.dest_conf()["MASTERS_DIR"]
        try:
            return shutil.disk_usage(dest).free / 1e9 if dest and os.path.isdir(dest) else None
        except OSError:
            return None

    def step(self):
        """The one thing that matters now, for the page to build its "do this" panel around."""
        if self.rec and not self.rec.standby:
            return "recording"
        if self.state == "finishing":
            return "finishing"
        if not self.dev:
            return "plug_device"
        if not self.card:
            return "connect_disk"
        if self.state == "error":
            return "error"
        if self.queue.moving(self.last_card_id):
            return "wait"
        if self.rec:
            return "standby"  # the page splits this into Ready and Not ready by the standby clock
        if self.f("STANDBY_SEC") <= 0 or time.time() < self.standby_until:
            return "press_play"
        return "press_start"

    def warnings(self, masters_free):
        """Things to act on soon, whatever the step: running out of room for masters."""
        need = self.f("MASTER_MIN_FREE_GB")
        if masters_free is None or need <= 0 or masters_free >= 2 * need:
            return []
        n = int(max(masters_free - self.queue.spare() / 1e9, 0) // need)
        room = "one more master" if n == 1 else "%d more masters" % n if n else "no more masters"
        return ["Masters disk nearly full: room for %s." % room]

    def advice(self, masters_free):
        """What to do now, in a sentence or three, for whoever is at the deck."""
        if self.rec and not self.rec.standby:
            return "Recording. Leave it: it stops by itself when the source ends."
        if self.state == "finishing":
            return "Finishing the files. Don't turn anything off."
        if not self.dev:
            return "Plug the capture device into this machine."
        if not self.card:
            return "Connect the capture disk."
        t = self.item(self.next_item)
        nxt = "%s (%s)" % (t["id"], t["title"]) if t else "the next item"
        moving = self.queue.moving(self.last_card_id)
        if self.state == "error":
            s = "Something went wrong: see the message above. Check the cables and the disk, then turn the source off and on."
        elif moving:
            s = ("Wait: still moving %s's master off the capture disk. A recording started now can lose frames, or not start "
                 "at all. Put %s in and cue it up, but don't press play until this says Ready." % (moving, nxt))
        elif self.rec:  # standing by
            cyc = self.f("STANDBY_SEC")
            s = ("Put in %s, cue it up, and press play while the ring at the top is green and says Ready. Every %s it "
                 "turns red for about 3 s: wait for it to turn green again." % (nxt, "%g minutes" % (cyc / 60)
                                                                                 if cyc >= 120 else "%d s" % cyc))
        elif self.f("STANDBY_SEC") <= 0 or time.time() < self.standby_until:
            s = "Ready: put in %s, cue it up, and press play." % nxt
        else:  # standby ran out: Start first keeps the opening
            s = ("Ready: put in %s and cue it up. Press Start recording below, then press play within %d minutes; "
                 "starting first keeps the first seconds." % (nxt, self.f("PREPLAY_SEC") // 60))
        if self.items:
            s += " Wrong item? Pick the right one under Next item before pressing play."
        held = self.queue.held()
        if held:
            s += " Waiting for your OK: %s. Check each, then press its button below." % ", ".join(held[:12])
            if len(held) > 12:
                s += " (and %d more)" % (len(held) - 12)
        need = self.f("MASTER_MIN_FREE_GB")
        if masters_free is not None and need > 0 and masters_free < 2 * need:
            n = int(max(masters_free - 10, 0) // need)
            s += (" The masters disk has room for %s; after that masters stay on the capture disk, and once that is "
                  "full too, recording stops." % ("one more master" if n == 1 else "%d more masters" % n if n
                                                  else "no more masters"))
        return s

    # --- settings on the page -----------------------------------------------------------

    def conf_file(self):
        return self.conf_path or config.CONF_PATH

    def settings_view(self):
        """Everything the page's setup screen shows: the settings it may change, the choices for each, and what's
        plugged in."""
        c = self.conf
        named = [d for d in config.destinations(c) if d["id"] not in ("disk", "settings")]
        dev_name = self.dev and self.dev[2] or ""
        folder = os.path.dirname(os.path.abspath(self.conf_file()))
        return {
            "values": {k: c.get(k, "") for k in settings.FIELDS},
            "destinations": [dict({"id": d["id"], "label": d["label"]}, **{k: d["set"].get(k, "") for k in config.WHERE})
                             for d in named],
            "all_destinations": [{"id": d["id"], "label": d["label"]} for d in config.destinations(c)],
            "profiles": [{"id": p["id"], "label": p["label"]} for p in settings.profiles()],
            "suggested_profile": settings.suggest_profile(dev_name) if dev_name else "",
            "disks": settings.disks() if not c["TEST_CARD_DIR"] else [{"label": "test folder", "fstype": "folder",
                                                                       "path": c["TEST_CARD_DIR"], "size_gb": None,
                                                                       "free_gb": None}],
            "remotes": settings.remotes(c),
            "timezones": settings.timezones(),
            "time_zone": self.time_zone()[0],
            "device": dev_name, "audio": bool(self.audio or c["TEST_SOURCE"]), "signal": self.signal,
            "card": self.card and {"label": self.card.get("label") or "", "fstype": self.card.get("fstype")},
            "address": self.address(), "setup_done": settings.setup_done(),
            "writable": os.access(self.conf_file() if os.path.exists(self.conf_file()) else folder, os.W_OK),
            "test": bool(c["TEST_SOURCE"]),
        }

    def change_settings(self, updates):
        """Check every value, then write them all to the settings file, or none: {"errors": {key: sentence}}."""
        if not isinstance(updates, dict) or not updates:
            return {"errors": {"": "Nothing to change."}}
        clean, errors = {}, {}
        for k, v in updates.items():
            try:
                clean[k] = settings.check(k, v, self.conf)
            except ValueError as e:
                errors[k] = str(e)
        if errors:
            return {"errors": errors}
        try:
            settings.save(self.conf_file(), clean)
        except OSError as e:
            return {"errors": {"": "Couldn't save the settings file (%s): %s" % (self.conf_file(), e.strerror)}}
        log("settings changed on the web page: %s" % ", ".join("%s=%s" % kv for kv in clean.items()))
        if self.rec and not self.rec.standby:
            return {"errors": {}}  # a recording keeps the settings it started with: the loop reloads them after it
        self.conf = config.load(self.conf_path)
        if self.rec:
            self.restart_standby = True  # a standby recording becomes the capture: start it over with the new settings
        return {"errors": {}}

    @staticmethod
    def time_zone():
        """This machine's time zone: its name (America/New_York) if it has one, and its abbreviation now (EST)."""
        name = os.environ.get("TZ", "").lstrip(":")
        if not name:
            name = hardware.read("/etc/timezone")
        if not name:
            with contextlib.suppress(OSError):
                link = os.path.realpath("/etc/localtime")
                name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
        return name, datetime.now().astimezone().tzname()

    def address(self):
        """The page's address on the network, once it's announced."""
        a = getattr(self, "announcer", None)
        port = self.conf["WEB_PORT"]
        return a and a.name and "http://%s%s/" % (a.name, "" if port == "80" else ":" + port) or None

    def status(self):
        rec = self.rec
        mfree = self.masters_free()
        tz, tz_abbr = self.time_zone()
        d = {"station": self.station(), "address": self.address(), "now": time.time(), "tz": tz, "tz_abbr": tz_abbr, "step": self.step(), "held": self.queue.held(), "warnings": self.warnings(mfree),
             "state": self.state, "message": self.message, "last": self.last_result,
             "advice": self.advice(mfree), "masters_free_gb": mfree and round(mfree),
             "master_gb": self.f("MASTER_MIN_FREE_GB"), "profile": self.conf["PROFILE"],
             "device": self.dev and "%s (%s)" % (self.dev[2], self.dev[0]),
             "audio": self.audio or (self.dev and self.dev[0] == "test" and "test tone") or None, "input": self.input_name, "signal": self.signal,
             "card": self.card and "%s %s" % (self.card.get("label") or "", self.card.get("fstype")),
             "recording": None, "upload_progress": self.queue.progress,
             "uploads": self.queue.recent(),
             "destinations": [{"id": x["id"], "label": x["label"]} for x in config.destinations(self.conf)],
             "destination": self.dest_conf()["DESTINATION"],
             "next": self.next_item, "aspect": self.next_aspect, "items": [{"id": t["id"], "title": t["title"], "format": t["format"]} for t in self.items],
             "items_from": "page" if os.path.exists(PAGE_LIST) else "file" if self.items else "",
             "details": self.details,
             "item": rec and not rec.standby and rec.item and self.item_label(rec.item) or None}
        # standing by: how far into this standby recording, so the page can say Not ready in the 2 s before it starts
        # over (two recordings can't hold the device at once) and until the next one is running
        d["standby"] = rec and rec.standby and {"age": time.time() - rec.started, "cycle": self.f("STANDBY_SEC")} or None
        if rec and not rec.standby:
            try:
                free = shutil.disk_usage(os.path.dirname(rec.base)).free / 1e9
            except OSError:
                free = None
            d["recording"] = {"name": os.path.basename(rec.base), "mode": rec.mode, "aspect": getattr(rec, "aspect", "4:3"), "elapsed": int(rec.duration()),
                              "free_gb": free and round(free, 1), "motion_seen": rec.motion_seen,
                              "frozen": rec.frozen, "peak_db": rec.damage.peak_db, "damage": rec.damage.summary()}
        return d

    def run(self):
        from .web import serve
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "quit", True))
        signal.signal(signal.SIGINT, lambda *a: setattr(self, "quit", True))
        threading.Thread(target=serve, args=(self,), daemon=True).start()
        threading.Thread(target=self.queue.run, daemon=True).start()
        self.pause_others(False)  # in case a crash left them frozen
        self.announcer = mdns.Announcer(self.conf["NETWORK_NAME"])
        self.announcer.start()
        log("videocap on %s: profile %s, %d items listed, web page on port %s"
            % (self.station(), self.conf["PROFILE"], len(self.items), self.conf["WEB_PORT"]))
        try:
            while not self.quit:
                try:
                    self.tick()
                except Exception as e:  # keep the station alive
                    log("tick error: %r" % e)
                time.sleep(1)
        finally:
            if self.rec and self.rec.standby:
                self.drop_standby()
            elif self.rec:
                self.finish("videocap shutting down")
            self.pause_others(False)
            self.announcer.stop()
            self.led.restore()
