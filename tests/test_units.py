"""Unit tests that run anywhere (no capture device, ffmpeg or rclone needed): python3 -m unittest discover tests"""
import json
import os
import tempfile
import time
import unittest

from videocap import collection, config, mdns, settings
from videocap.queue import join
from videocap.recording import Damage, build_command


def conf(**kw):
    c = dict(config.DEFAULTS)
    c.update(PROFILE="generic")
    c.update({k: str(v) for k, v in kw.items()})
    return c


class ConfigTest(unittest.TestCase):
    def test_profile_then_user_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "videocap.conf")
            with open(p, "w") as f:
                f.write("PROFILE=usblive2-e1\nAUDIO_DELAY_MS=50  # measured on my deck\nSTANDARD='PAL'\nNOT_A_SETTING=1\n")
            c = config.load(p)
            self.assertEqual(c["MP4_CROP"], "12:4:0:8")        # from the profile
            self.assertEqual(c["SIGNAL_CONTROL"], "lock_status")
            self.assertEqual(c["AUDIO_DELAY_MS"], "50")        # the settings file wins over the profile
            self.assertEqual(c["STANDARD"], "PAL")
            self.assertEqual(config.unknown_keys(c), ["NOT_A_SETTING"])

    def test_missing_profile_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "videocap.conf")
            with open(p, "w") as f:
                f.write("PROFILE=no-such-stick\n")
            self.assertIn("not found", config.load(p)["PROFILE"])

    def test_missing_file_gives_defaults(self):
        c = config.load("/nonexistent/videocap.conf")
        self.assertEqual(c["MODE"], "both")


class DestinationTest(unittest.TestCase):
    def test_ships_disk_only(self):
        d = config.apply_destination(conf(), "")
        self.assertEqual(d["DESTINATION"], "disk")
        self.assertEqual((d["MASTERS_DIR"], d["MP4_REMOTE"], d["MASTER_REMOTE"]), ("", "", ""))
        self.assertEqual([x["id"] for x in config.destinations(conf())], ["disk"])

    def test_settings_file_uploads_are_the_default_when_set(self):
        c = conf(MP4_REMOTE="lib:videos")
        self.assertEqual(config.apply_destination(c, "")["DESTINATION"], "settings")
        self.assertEqual(config.apply_destination(c, "disk")["MP4_REMOTE"], "")  # picking disk turns uploads off

    def test_named_destinations(self):
        c = conf(**{"DEST.aws.LABEL": "AWS", "DEST.aws.MASTER_REMOTE": "aws:bucket/masters",
                    "DEST.box.MP4_REMOTE": "dropbox:Home Videos", "DEST.box.UPLOAD_BWLIMIT": "4M",
                    "MASTER_REVIEW": "1", "DEFAULT_DESTINATION": "box"})
        self.assertEqual([(x["id"], x["label"]) for x in config.destinations(c)],
                         [("disk", "This disk only"), ("aws", "AWS"), ("box", "box")])
        aws = config.apply_destination(c, "aws")
        self.assertEqual((aws["MASTER_REMOTE"], aws["MP4_REMOTE"]), ("aws:bucket/masters", ""))  # starts from nothing
        self.assertEqual(aws["MASTER_REVIEW"], "1")  # other keys come from the settings file
        self.assertEqual(config.apply_destination(c, "")["DESTINATION"], "box")
        self.assertEqual(config.apply_destination(c, "gone")["UPLOAD_BWLIMIT"], "4M")  # a removed pick: the default
        self.assertEqual(config.unknown_keys(c), [])


class CollectionTest(unittest.TestCase):
    def test_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "items.csv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("ID,Label,Title,Format\nT01,Summer '93 / beach,,Hi8\nT02,Xmas → New Year,Christmas,VHS\n"
                        "T01,duplicate,,\n,no id,,\n")
            items = collection.load(p)
        self.assertEqual([t["id"] for t in items], ["T01", "T02"])
        self.assertEqual(items[0]["title"], "Summer '93 / beach")
        self.assertEqual(items[0]["file"], "T01 - Summer '93 - beach")
        self.assertEqual(items[1]["file"], "T02 - Christmas")
        self.assertEqual(items[1]["format"], "VHS")

    def test_pasted_spreadsheet(self):
        items = collection.parse("Tape #\tName\tDescription\tYear\n7\tSummer 94\tBeach, with Liam\t1994\n8\t\t\t\n")
        self.assertEqual(items[1]["title"], "8")  # nothing else to call it
        t = items[0]
        self.assertEqual((t["id"], t["title"], t["notes"], t["date"]), ("7", "Summer 94", "Beach, with Liam", "1994"))
        nfo = collection.nfo(t, "2026-11-18")
        self.assertIn("<year>1994</year>", nfo)
        self.assertIn("Beach, with Liam.", nfo)

    def test_one_title_per_line(self):
        items = collection.parse("Christmas 1993\n\nGrandpa's 75th, Oct '94\n")
        self.assertEqual([(t["id"], t["title"]) for t in items],
                         [("001", "Christmas 1993"), ("003", "Grandpa's 75th, Oct '94")])

    def test_odd_lists_and_names(self):
        self.assertEqual([t["title"] for t in collection.parse("[Home movies] 1990\nSummer")],
                         ["[Home movies] 1990", "Summer"])  # brackets, not JSON
        self.assertNotIn("%", collection.safe_name("100% Family"))
        self.assertLessEqual(len(collection.safe_name("\u0436" * 200).encode()), 120)  # ext4 counts bytes

    def test_typed_title_only(self):
        t = collection.make_item({"id": "", "title": "Dad's retirement party", "notes": ""})
        self.assertEqual(t["file"], "Dad's retirement party")
        self.assertIsNone(collection.make_item({"id": "", "title": "", "notes": "just notes"}))

    def test_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "items.json")
            with open(p, "w") as f:
                json.dump([{"id": "0007", "label": "Wedding: part 1?"}], f)
            t = collection.load(p)[0]
        self.assertEqual(t["file"], "0007 - Wedding part 1")

    def test_aspect_column(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "items.csv")
            with open(p, "w") as f:
                f.write("id,label,aspect\nA,one,16x9\nB,two,\nC,three,Widescreen\nD,four,4:3\n")
            self.assertEqual([t["aspect"] for t in collection.load(p)], ["16:9", "", "16:9", "4:3"])

    def test_no_list(self):
        self.assertEqual(collection.load(""), [])

    def test_safe_name(self):
        self.assertEqual(collection.safe_name('a<b>c:"d"|e?*  f. '), "abcde f")
        self.assertEqual(collection.safe_name("..."), "untitled")
        self.assertLessEqual(len(collection.safe_name("x" * 400)), 150)

    def test_nfo_escapes_and_describes(self):
        t = {"id": "T9", "label": "Mom & Dad <3", "title": "Anniversary", "format": "VHS"}
        x = collection.nfo(t, "2026-11-17", by="A. Person", collection="Our items")
        self.assertIn("<title>Anniversary</title>", x)
        self.assertIn("<sorttitle>T9</sorttitle>", x)
        self.assertIn("Mom &amp; Dad &lt;3", x)
        self.assertIn("Digitized by A. Person in November 2026.", x)
        self.assertIn("<tag>Our items</tag>", x)


class CommandTest(unittest.TestCase):
    def cmd(self, mode="both", vfat=False, **kw):
        return build_command(conf(**kw), "/dev/video0", "hw:CARD=Audio,DEV=0", "/c/t", mode, vfat, "/run/snap.jpg")

    def test_both(self):
        cmd, files = self.cmd(MP4_CROP="12:4:0:8", AUDIO_DELAY_MS=80)
        s = " ".join(cmd)
        self.assertEqual(files, ["/c/t_master.mkv", "/c/t_access.mp4"])
        self.assertIn("-c:v ffv1", s)
        self.assertIn("-c:v libx264", s)
        self.assertIn("crop=iw-16:ih-8:12:0", s)
        self.assertIn("adelay=80:all=1", s)
        self.assertIn("-standard NTSC", s)
        self.assertIn("-input_format yuyv422", s)
        self.assertIn("setsar=10/11", s)
        self.assertIn("-isync 0", s)

    def test_master_only_pal_on_fat32(self):
        cmd, files = self.cmd(mode="master", vfat=True, STANDARD="PAL")
        s = " ".join(cmd)
        self.assertEqual(files, ["/c/t_master_%03d.mkv"])
        self.assertNotIn("libx264", s)
        self.assertIn("-f segment", s)
        self.assertIn("-standard PAL", s)
        self.assertIn("-video_size 720x576", s)
        self.assertIn("setsar=12/11", s)

    def test_access_only_with_preview(self):
        cmd, files = self.cmd(mode="access", PREVIEW_UDP="10.0.0.5:5555")
        s = " ".join(cmd)
        self.assertEqual(files, ["/c/t_access.mp4"])
        self.assertNotIn("ffv1", s)
        self.assertIn("udp://10.0.0.5:5555", s)
        self.assertIn("split=3[a0][d][pv]", s)

    def test_widescreen_pixel_shape(self):
        self.assertIn("setsar=40/33", " ".join(self.cmd(ASPECT="16:9")[0]))
        self.assertIn("setsar=16/11", " ".join(self.cmd(ASPECT="16:9", STANDARD="PAL")[0]))
        self.assertIn("setsar=10/11", " ".join(self.cmd(ASPECT="4:3")[0]))

    def test_no_audio(self):
        cmd, _ = build_command(conf(), "/dev/video0", None, "/c/t", "both", False, "/s.jpg")
        s = " ".join(cmd)
        self.assertNotIn("alsa", s)
        self.assertNotIn("[lvl]", s)


class DamageTest(unittest.TestCase):
    def test_missing_frames(self):
        d = Damage(25)
        for t in (0, 0.04, 0.08, 0.20, 0.24):  # 0.08 -> 0.20 skips two frames
            d.frame(t)
        self.assertEqual(d.missing_frames, 2)

    def test_clipping_and_silent_channel(self):
        d = Damage(25)
        for _ in range(30):
            d.sound("1", "RMS_level", "-60")
            d.sound("2", "RMS_level", "-12")
            d.sound("Overall", "Peak_level", "0.0")
        self.assertEqual(d.clipped, 30)
        self.assertIn("left channel nearly silent", d.summary())
        d.reset()
        self.assertEqual(d.clipped, 0)


class RemoteTest(unittest.TestCase):
    def test_join(self):
        self.assertEqual(join("nas:", "a/b.mp4"), "nas:a/b.mp4")
        self.assertEqual(join("nas:videos", "b.mp4"), "nas:videos/b.mp4")
        self.assertEqual(join("/mnt/lib/", "b.mp4"), "/mnt/lib/b.mp4")
        self.assertEqual(join("/mnt/lib", "b.mp4"), "/mnt/lib/b.mp4")


class AnnounceTest(unittest.TestCase):
    def test_takes_the_next_name_when_taken(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "avahi-publish")  # holds any name but videocap.local, like a second station
            with open(fake, "w") as f:
                f.write('#!/bin/sh\n[ "$3" = videocap.local ] && { echo "Failed to add address: Local name collision" >&2;'
                        ' exit 1; }\necho "$3 $4" > "%s/held"\necho "Established under name \'$3\'" >&2\nexec sleep 60\n' % d)
            os.chmod(fake, 0o755)
            path = os.environ["PATH"]
            os.environ["PATH"] = d + os.pathsep + path
            a = mdns.Announcer("videocap")
            try:
                a.start()
                for _ in range(50):
                    if a.name and os.path.exists(os.path.join(d, "held")):
                        break
                    time.sleep(0.1)
                self.assertEqual(a.name, "videocap-2.local")
                with open(os.path.join(d, "held")) as f:
                    self.assertTrue(f.read().startswith("videocap-2.local "))
            finally:
                os.environ["PATH"] = path
                a.stop()


class SettingsTest(unittest.TestCase):
    def test_save_keeps_comments_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "videocap.conf")
            with open(p, "w") as f:
                f.write("# my station\nSTANDARD=NTSC\n# where\nCARD_LABEL=OLD\nCARD_LABEL=OLDER\nDEST.nas.LABEL=NAS\n"
                        "DEST.nas.MP4_REMOTE=/tmp\n")
            settings.save(p, {"STANDARD": "PAL", "CARD_LABEL": "CAPTURES", "COLLECTION": "Family tapes",
                              "DEST.nas.LABEL": "", "DEST.nas.MP4_REMOTE": ""})
            with open(p) as f:
                text = f.read()
            with open(p + ".bak") as f:
                self.assertIn("STANDARD=NTSC", f.read())  # the old file is kept
        self.assertEqual(text, "# my station\nSTANDARD=PAL\n# where\nCARD_LABEL=CAPTURES\n\n"
                               "# --- Set on the web page\nCOLLECTION=Family tapes\n")

    def test_check_refuses_what_the_page_may_not_set(self):
        c = conf()
        with self.assertRaises(ValueError):
            settings.check("HOOK", "/bin/sh -c 'rm -rf /'", c)  # runs a program: settings file only
        with self.assertRaises(ValueError):
            settings.check("RCLONE_CONFIG", "/tmp/x", c)
        with self.assertRaises(ValueError):
            settings.check("STANDARD", "SECAM", c)
        with self.assertRaises(ValueError):
            settings.check("COLLECTION", "two\nlines", c)
        with self.assertRaises(ValueError):
            settings.check("DEST.nas.MASTERS_DIR", "/etc", c)  # a system folder
        with self.assertRaises(ValueError):
            settings.check("DEST.nas.MASTERS_DIR", "/mnt/../etc", c)
        with self.assertRaises(ValueError):
            settings.check("DEST.disk.LABEL", "mine", c)  # built-in
        for sneaky in ("a\nHOOK=/tmp/x.sh", "a\u2028HOOK=/tmp/x.sh", "a\x85HOOK=/x", "a\rHOOK=/x", "a # b"):
            with self.assertRaises(ValueError):  # a second line would be a HOOK=, run as root
                settings.check("COLLECTION", sneaky, c)
        with self.assertRaises(ValueError):
            settings.check("DEST.nas.MP4_REMOTE", "nas:v\nHOOK=/tmp/x.sh", c)
        with self.assertRaises(ValueError):
            settings.check("DEST.nas.MASTERS_DIR", "//etc", c)
        with self.assertRaises(ValueError):
            settings.check("DEST.a.LABEL\n", "x", c)
        with self.assertRaises(ValueError):
            settings.check("NETWORK_NAME", "den-vcr", c)  # settings file only now
        self.assertEqual(settings.check("MASTER_REVIEW", "on", c), "1")
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(__file__))) as d:  # not under /var
            self.assertEqual(settings.check("DEST.nas.MP4_REMOTE", d + "/", c), os.path.normpath(d))


if __name__ == "__main__":
    unittest.main()


class LedTest(unittest.TestCase):
    """The lights, on a fake /sys/class/leds."""

    def make(self, d):
        from videocap import hardware
        for name, trig in (("ACT", "mmc0"), ("PWR", "default-on")):
            os.makedirs(os.path.join(d, name))
            for f, v in (("trigger", "none [%s] timer heartbeat" % trig), ("max_brightness", "255"), ("brightness", "0")):
                with open(os.path.join(d, name, f), "w") as fh:
                    fh.write(v)
        cls = type("FakeLed", (hardware.Led,), {"ACT": os.path.join(d, "ACT"), "PWR": os.path.join(d, "PWR")})
        return cls("auto")

    def lit(self, d, name):
        with open(os.path.join(d, name, "brightness")) as f:
            return f.read() != "0"

    def test_recording_is_red_one_second_on_one_off(self):
        with tempfile.TemporaryDirectory() as d:
            led = self.make(d)
            led.set("recording")
            red = []
            for _ in range(28):  # 2.8 s: red on about 1 s, off about 1 s; green never comes on
                time.sleep(0.1)
                red.append(self.lit(d, "PWR"))
                self.assertFalse(self.lit(d, "ACT"))
            led.restore()
            runs = [len(list(g)) for _, g in __import__("itertools").groupby(red)]
            self.assertTrue(red[0] and red[-1])                # on, off, on again
            self.assertTrue(all(8 <= n <= 11 for n in runs[:2]), runs)
            with open(os.path.join(d, "ACT", "trigger")) as f:
                self.assertEqual(f.read(), "mmc0")  # given back as it was

    def test_counted_blinks(self):
        with tempfile.TemporaryDirectory() as d:
            led = self.make(d)
            led.set("no-disk")  # two blinks, then a pause
            samples = []
            t0 = time.time()
            while time.time() - t0 < 2.2:
                samples.append(self.lit(d, "ACT"))
                time.sleep(0.02)
            led.restore()
            ons = sum(1 for a, b in zip(samples, samples[1:]) if b and not a) + samples[0]
            self.assertEqual(ons, 2)
            self.assertNotIn(True, [self.lit(d, "PWR")])

    def test_states_map_to_patterns(self):
        from videocap import hardware
        led = hardware.Led("none")  # no lights: set() still keeps track, for the page
        led.set("ready")
        self.assertEqual(led.cur, "no-picture")
        led.set("error-stopped")
        self.assertEqual(led.cur, "error-stopped")
        led.set("something new")
        self.assertEqual(led.cur, "done")


class ButtonTest(unittest.TestCase):
    """The button, fed key events through a pipe."""

    def test_counts_and_hold(self):
        from videocap import button
        r, w = os.pipe()
        counts, holds = [], []
        b = button.Button("none", counts.append, lambda: holds.append(1), log=lambda m: None)
        b.dev = "/dev/fd/%d" % r
        old = button.HOLD_SEC, button.GAP_SEC
        button.HOLD_SEC, button.GAP_SEC = 0.6, 0.25
        try:
            import threading
            threading.Thread(target=b.run, daemon=True).start()
            key = lambda v: os.write(w, button.EVENT.pack(0, 0, button.EV_KEY, button.KEY_POWER, v))
            tap = lambda: (key(1), time.sleep(0.05), key(0), time.sleep(0.08))
            os.write(w, button.EVENT.pack(0, 0, 0, 0, 0))  # a sync event: ignored
            tap(); time.sleep(0.4)                         # one press
            tap(); tap(); time.sleep(0.4)                  # two
            tap(); tap(); tap(); time.sleep(0.4)           # three
            self.assertEqual((counts, holds), ([1, 2, 3], []))
            key(1); time.sleep(0.9)                        # held: counts while still held
            self.assertEqual(holds, [1])
            key(0); time.sleep(0.4)                        # and its release does nothing more
            self.assertEqual((counts, holds), ([1, 2, 3], [1]))
        finally:
            button.HOLD_SEC, button.GAP_SEC = old
            b.stopped = True
            os.close(w)


class MorseTest(unittest.TestCase):
    def test_timings(self):
        from videocap import morse
        u = morse.UNIT_MS
        self.assertEqual(morse.clean("rec B004_V001, 23m!"), "REC B004 V001 23M")
        seq = morse.timings("ET A")
        self.assertEqual(seq[0], (10 * u, 5 * u))           # sync flash
        self.assertEqual(seq[1], (u, 3 * u))                # E ., then a letter gap
        self.assertEqual(seq[2], (3 * u, 7 * u))            # T -, then a word gap
        self.assertEqual(seq[3:5], [(u, u), (3 * u, 7 * u)])  # A .-, then the gap before the end sign
        self.assertEqual(seq[5:], [(u, u), (3 * u, u), (u, u), (3 * u, u), (u, 10 * u)])  # AR .-.-.


class RepairTest(unittest.TestCase):
    """A disk pulled out without unmounting is looked over before it's mounted."""

    def test_clean_fixed_and_beyond_repair(self):
        from videocap import hardware
        with tempfile.TemporaryDirectory() as d:
            fsck = os.path.join(d, "fake-fsck")
            old = dict(hardware.REPAIR)
            try:
                hardware.REPAIR["exfat"] = [fsck]
                for code, ok in ((0, True), (1, True), (4, False)):
                    with open(fsck, "w") as f:
                        f.write("#!/bin/sh\necho 'checked %s'\nexit %d\n" % ("$1", code))
                    os.chmod(fsck, 0o755)
                    self.assertEqual(hardware.repair({"fstype": "exfat", "path": "/dev/sdz1", "label": "T"}), ok, code)
                self.assertTrue(hardware.repair({"fstype": "btrfs", "path": "/dev/sdz2"}))  # journaled: left alone
            finally:
                hardware.REPAIR.clear()
                hardware.REPAIR.update(old)


@unittest.skipUnless(__import__("shutil").which("node"), "needs node")
class ReaderTest(unittest.TestCase):
    """The phone reader's decoder (docs/reader/morse.js) reads what the light sends, through a simulated camera."""

    def read(self, text, fps, unit=None, trials=25):
        import subprocess
        from videocap import morse
        seq = morse.timings(text, unit or morse.UNIT_MS)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(["node", os.path.join(root, "tests", "reader_sim.mjs"),
                            json.dumps({"seq": seq, "fps": fps, "trials": trials, "seed": fps * 7 + len(text)})],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_status_at_30_fps(self):
        msg = "REC B004 V001 23M FREE 85G"
        self.assertEqual(set(self.read(msg, 30)), {msg})

    def test_address_at_24_fps(self):
        msg = "IP 192.168.1.23 PORT 8091"
        self.assertEqual(set(self.read(msg, 24)), {msg})

    def test_every_character_at_60_fps(self):
        from videocap import morse
        msg = " ".join("".join(sorted(morse.CODE))[i:i + 6] for i in range(0, len(morse.CODE), 6))
        self.assertEqual(set(self.read(msg, 60, trials=10)), {morse.clean(msg)})


class CameraReaderTest(unittest.TestCase):
    """python3 -m videocap.reader, fed synthetic camera frames: a dark scene, a steady red light, and the green light
    sending a message at 30 fps, starting at a random moment."""

    def test_reads_a_message(self):
        import random
        from videocap import morse, reader
        rnd = random.Random(4)
        msg = "IP 10.0.0.5 PORT 8091"
        edges, t = [], 1800.0
        for on, off in morse.timings(msg):
            edges.append((t, t + on))
            t += on + off
        r = reader.Reader()
        ft, frame = rnd.random() * 33, 1000 / 30
        base = bytearray([30, 32, 34] * (reader.W * reader.H))
        while ft < t + 1500:
            px = bytearray(base)
            for _ in range(20):  # noise
                i = rnd.randrange(reader.W * reader.H) * 3
                px[i + 1] = min(255, px[i + 1] + rnd.randrange(12))
            share = sum(max(0, min(ft + frame * 0.9, b) - max(ft, a)) for a, b in edges) / (frame * 0.9)
            for (x, y, rgb) in ((60, 30, (255, 40, 30)), (64, 30, (80, 255, 120) if share > 0.5 else (40, 60, 45))):
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        i = ((y + dy) * reader.W + x + dx) * 3
                        px[i:i + 3] = bytes(rgb)
            if r.frame(ft + rnd.uniform(-3, 3), bytes(px)):
                break
            ft += frame
        self.assertTrue(r.dec.done, r.dec.partial)
        self.assertEqual(r.dec.text, msg)
        self.assertEqual(r.spot[0] in (63, 64, 65), True)  # the green light, not the red one


class BluetoothTest(unittest.TestCase):
    def test_name_data(self):
        from videocap import beacon
        self.assertEqual(beacon.name_data("V0 OK"), "060956302" + "04f4b")
        self.assertEqual(len(bytes.fromhex(beacon.name_data("V0 " + "X" * 60))), 31)  # never more than fits

    def test_collects_a_round(self):
        from videocap.btreader import Collector
        c = Collector()
        for n in ("V2 MASTERS 120G FREE", "V3 CODES NONE"):  # joined partway through a round
            c.add("aa", n)
        self.assertFalse(c.complete("aa"))
        c.add("aa", "something else")  # other devices' names are ignored
        for n in ("V0 REC 23M B004V001 85G", "V1 NOW FRENCH TV"):
            c.add("aa", n)
        self.assertTrue(c.complete("aa"))
        self.assertEqual(c.report("aa"), ["REC 23M B004V001 85G", "NOW FRENCH TV", "MASTERS 120G FREE", "CODES NONE"])


class DatedNameTest(unittest.TestCase):
    def test_untitled_take_is_named_by_its_start(self):
        from datetime import datetime
        from videocap.app import App
        when = datetime(2026, 11, 19, 21, 42, 30).timestamp()
        item = App.dated_item(when)
        self.assertEqual(item["title"], "Nov 19, 2026, 9:42 PM")
        self.assertEqual(item["file"], "2026-11-19 2142")
        self.assertEqual(App.take_base(None, "/x", item), "/x/2026-11-19 2142")
        self.assertEqual(App.dated_item(datetime(2026, 1, 5, 0, 7).timestamp())["title"], "Jan 5, 2026, 12:07 AM")


class PaperTest(unittest.TestCase):
    """Finding the circled word on a sheet (the geometry; Tesseract's reading is checked on the station)."""

    def test_finds_the_circle_and_whats_inside(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("needs numpy")
        from videocap import paper
        h, w = paper.H // paper.S, paper.W // paper.S
        g = np.full((h, w), 40, np.uint8)            # a dark room
        g[20:160, 30:300] = 230                      # the sheet
        yy, xx = np.mgrid[0:h, 0:w]
        r = np.sqrt(((xx - 110) / 55.0) ** 2 + ((yy - 90) / 30.0) ** 2)
        g[(r > 0.92) & (r < 1.05)] = 30             # a pen circle
        g[84:96, 80:140] = 25                        # writing inside it
        g[82:98, 200:215] = 25                       # and an uncircled mark after it
        region = paper.paper_region(g.astype(float))
        self.assertIsNotNone(region)
        inside = paper.find_circle(paper.ink(g.astype(float)) & region)
        self.assertIsNotNone(inside)
        ct, cb, cl, cr = inside
        self.assertTrue(ct < 84 and cb > 96 and cl < 80 and cr > 140, inside)   # the writing is inside
        self.assertTrue(cr < 200, inside)                                       # the mark after it isn't
