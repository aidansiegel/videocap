"""Unit tests that run anywhere (no capture device, ffmpeg or rclone needed): python3 -m unittest discover tests"""
import json
import os
import tempfile
import unittest

from videocap import collection, config
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


if __name__ == "__main__":
    unittest.main()
