"""End to end, with ffmpeg's test pattern standing in for a deck and folders standing in for the disks and the
destinations: record a "tape" that plays for 14 s and then freezes, stop by itself, move the master, upload the MP4
and .nfo, hold the master for review, approve it, and check what arrived.

Needs ffmpeg, rclone and cmp; takes about a minute and writes about 1 GB. Set VIDEOCAP_TEST_DIR to a folder on a disk
with room (default: a new temporary folder).
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "rclone", "cmp")), "needs ffmpeg, rclone, cmp")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        parent = os.environ.get("VIDEOCAP_TEST_DIR") or None
        self.dir = tempfile.mkdtemp(prefix="videocap-test-", dir=parent)
        d = lambda n: os.makedirs(os.path.join(self.dir, n)) or os.path.join(self.dir, n)
        self.card, self.masters, self.mp4s, self.archive = d("card"), d("masters"), d("library"), d("archive")
        self.port = free_port()
        with open(os.path.join(self.dir, "items.csv"), "w") as f:
            f.write("id,label,title,format,aspect\nT01,Test item: one,Test item,VHS,16:9\nT02,Second,,VHS,\n")
        settings = {
            "TEST_SOURCE": "testsrc2=s=720x480:r=30000/1001:d=14,tpad=stop=-1:stop_mode=clone",
            "TEST_CARD_DIR": self.card, "WEB_PORT": self.port, "LEDS": "none", "STATION_NAME": "test-station",
            "STANDBY_SEC": 0, "STOP_AFTER_FROZEN_MIN": 0.1, "MIN_TAKE_MINUTES": 0.1, "MASTER_MIN_FREE_GB": 0,
            "MASTERS_SPARE_GB": 0, "MIN_FREE_GB": 0.2, "ITEM_LIST": os.path.join(self.dir, "items.csv"),
            "MASTERS_DIR": self.masters, "MP4_REMOTE": self.mp4s, "MASTER_REMOTE": self.archive + "/",
            "COLLECTION": "Test collection", "DIGITIZED_BY": "Tester",
        }
        self.conf = os.path.join(self.dir, "videocap.conf")
        with open(self.conf, "w") as f:
            f.write("".join("%s=%s\n" % kv for kv in settings.items()))
        env = dict(os.environ, VIDEOCAP_RUN=os.path.join(self.dir, "run"), VIDEOCAP_STATE=os.path.join(self.dir, "state"),
                   PYTHONPATH=ROOT)
        self.out = open(os.path.join(self.dir, "station.log"), "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "videocap", "--conf", self.conf], cwd=self.dir, env=env,
                                     stdout=self.out, stderr=subprocess.STDOUT)

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.out.close()
        if not os.environ.get("VIDEOCAP_KEEP"):
            shutil.rmtree(self.dir)

    def status(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/status.json" % self.port, timeout=5) as r:
            return json.load(r)

    def post(self, path, body=b""):
        req = urllib.request.Request("http://127.0.0.1:%d/%s" % (self.port, path), data=body, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status

    def wait_for(self, what, test, timeout=150):
        end, last = time.time() + timeout, None
        while time.time() < end:
            try:
                last = self.status()
                if test(last):
                    return last
            except OSError:
                pass
            time.sleep(1)
        with open(os.path.join(self.dir, "station.log")) as f:
            log = f.read()[-3000:]
        self.fail("timed out waiting for %s; last status %s\n--- station log ---\n%s" % (what, last, log))

    def test_take_to_archive(self):
        self.assertEqual(self.wait_for("the page", lambda s: True)["station"], "test-station")
        page = urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5).read()
        self.assertIn(b"videocap", page)
        s = self.wait_for("recording", lambda s: s["recording"])
        self.assertEqual(s["item"], "T01: Test item")
        # named on the page while it records: the files are renamed when it finishes
        self.assertEqual(self.post("details", json.dumps({"id": "T01", "title": "Typed title", "format": "VHS",
                                                          "date": "July 1994", "notes": "Typed on the page"}).encode()), 204)
        self.assertEqual(self.status()["item"], "T01: Typed title")
        s = self.wait_for("the take held for review",
                          lambda s: s["uploads"] and any(i["kind"] == "master" and i["state"] == "held"
                                                         for i in s["uploads"][0]["items"])
                          and all(i["state"] in ("done", "held") for i in s["uploads"][0]["items"]))
        self.assertEqual(s["next"], "T02")  # moved on to the next item
        self.assertEqual(s["details"]["title"], "Second")
        job = s["uploads"][0]
        name = job["name"]
        self.assertTrue(name.startswith("T01 - Typed title - captured "))
        self.assertIn("frames missing", job["damage"])

        # it can be watched on the page, from the start or from anywhere in it (seeking); nothing else is served
        url = "http://127.0.0.1:%d/watch/%s" % (self.port, urllib.parse.quote(name))
        req = urllib.request.Request(url, headers={"Range": "bytes=1000-1999"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual((r.status, len(r.read())), (206, 1000))
            self.assertTrue(r.headers["Content-Range"].startswith("bytes 1000-1999/"))
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen("http://127.0.0.1:%d/watch/..%%2Fvideocap.conf" % self.port, timeout=10)

        # the MP4 and its .nfo are in the library, under the item's name; the master moved to the masters disk
        self.assertTrue(os.path.getsize(os.path.join(self.mp4s, "T01 - Typed title.mp4")) > 100000)
        with open(os.path.join(self.mp4s, "T01 - Typed title.nfo")) as f:
            nfo = f.read()
        self.assertIn("<title>Typed title</title>", nfo)
        self.assertIn("Typed on the page. Recorded July 1994.", nfo)
        self.assertIn("<year>1994</year>", nfo)
        self.assertIn("Test collection", nfo)
        self.assertTrue(os.path.exists(os.path.join(self.masters, name + "_master.mkv")))
        self.assertFalse(os.path.exists(os.path.join(self.card, "captures", name + "_master.mkv")))
        self.assertTrue(os.path.exists(os.path.join(self.card, "captures", name + ".log")))
        with open(os.path.join(self.masters, name + ".json")) as f:
            report = json.load(f)
        self.assertEqual(report["item"]["id"], "T01")
        self.assertIn("source ended", report["stopped_because"])
        self.assertGreater(report["seconds"], 10)
        self.assertEqual(os.listdir(self.archive), [])  # nothing archived before review

        # the MP4 starts at the picture and plays; the master is FFV1, interlaced, with sound
        probe = lambda p, e: subprocess.run(["ffprobe", "-v", "error", "-show_entries", e, "-of", "json", p],
                                            capture_output=True, text=True).stdout
        m = json.loads(probe(os.path.join(self.masters, name + "_master.mkv"), "stream=codec_name"))
        self.assertEqual([x["codec_name"] for x in m["streams"]], ["ffv1", "pcm_s16le"])
        # each frame is flagged interlaced, top field first (ffmpeg tags the stream itself "tb" for that: its own
        # convention for interleaved top-first frames, and it overrides -field_order)
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#5",
                            "-show_entries", "frame=interlaced_frame,top_field_first", "-of", "csv=p=0",
                            os.path.join(self.masters, name + "_master.mkv")], capture_output=True, text=True)
        self.assertEqual(set(r.stdout.split()), {"1,1"})
        # T01 is marked 16:9 in the item list: both copies carry the widescreen pixel shape
        sar = lambda f: json.loads(probe(f, "stream=sample_aspect_ratio"))["streams"][0]["sample_aspect_ratio"]
        self.assertEqual(sar(os.path.join(self.masters, name + "_master.mkv")), "40:33")
        self.assertEqual(sar(os.path.join(self.mp4s, "T01 - Typed title.mp4")), "40:33")
        self.assertEqual(report["aspect"], "16:9")
        a = json.loads(probe(os.path.join(self.mp4s, "T01 - Typed title.mp4"), "format=duration"))
        self.assertGreater(float(a["format"]["duration"]), 15)

        # approve: master, log, report, then checksums last
        self.assertEqual(self.post("approve", name.encode()), 204)
        self.wait_for("the archive copy", lambda s: all(i["state"] == "done" for i in s["uploads"][0]["items"]))
        sent = sorted(os.listdir(os.path.join(self.archive, name)))
        self.assertEqual(sent, sorted([name + "_master.mkv", name + ".log", name + ".json", name + ".md5"]))
        with open(os.path.join(self.archive, name, name + ".md5")) as f:
            sums = dict(reversed(line.rstrip("\n").split("  ", 1)) for line in f)
        self.assertEqual(set(sums), {name + "_master.mkv", name + ".log", name + ".json"})
        for fn, digest in sums.items():
            with open(os.path.join(self.archive, name, fn), "rb") as f:
                self.assertEqual(hashlib.md5(f.read()).hexdigest(), digest, fn)

        # a second approve of the same take is harmless; discarding it now is refused (it's already sent)
        self.assertEqual(self.post("approve", name.encode()), 204)
        with self.assertRaises(urllib.error.HTTPError):
            self.post("discard", name.encode())

    def test_made_safe_to_unplug_mid_take(self):
        """The button (POST /eject) during a take: the take is finished and kept, nothing is mounted or moving, the
        light says safe; REC carries on."""
        self.wait_for("recording with a moving picture", lambda s: s["recording"] and s["recording"]["motion_seen"]
                      and s["recording"]["elapsed"] >= 6)
        self.assertEqual(self.post("eject"), 204)
        s = self.wait_for("safe to unplug", lambda s: s["light"] == "done" and "Safe to unplug" in s["message"])
        self.assertIsNone(s["recording"])
        held = s["uploads"][0]
        self.assertTrue(any(i["kind"] == "master" and i["state"] == "held" for i in held["items"]))  # kept, for review
        time.sleep(4)  # it stays safe: nothing starts again by itself
        s = self.status()
        self.assertEqual((s["light"], s["recording"]), ("done", None))
        self.assertTrue(os.path.exists(os.path.join(self.card, "captures", held["name"] + "_master.mkv")))
        self.assertEqual(self.post("start"), 204)  # REC carries on
        self.wait_for("recording again", lambda s: s["recording"] and s["light"] == "recording")


if __name__ == "__main__":
    unittest.main()
