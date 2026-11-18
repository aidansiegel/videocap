"""One recording: the ffmpeg command, and what its output says about the source (motion, freezes, damage)."""
import re
import signal
import subprocess
import threading
import time
from datetime import datetime


def build_command(conf, dev, audio, base, mode, vfat, snap):
    """(ffmpeg arguments, files written). Pure: no side effects, so it can be tested anywhere.

    One ffmpeg process writes everything: the lossless master (FFV1 + PCM in Matroska, the whole frame, interlaced
    as captured), the MP4 (H.264, deinterlaced to 59.94p/50p, cropped), a snapshot for the web page, an optional live
    preview, and analysis output (one line per frame, freeze detection, sound levels) read from its stderr.
    """
    ntsc = conf["STANDARD"].upper() != "PAL"
    wide = conf.get("ASPECT", "4:3") == "16:9"
    size = "720x480" if ntsc else "720x576"
    color = "smpte170m" if ntsc else "bt470bg"
    colors = ["-color_primaries", color, "-color_trc", color if ntsc else "gamma28",
              "-colorspace", color, "-color_range", "tv"]
    master = mode in ("both", "master")
    access = mode in ("both", "access")
    files = []

    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-y"]
    if dev == "test":
        cmd += ["-re", "-f", "lavfi", "-i", conf["TEST_SOURCE"]]
        audio = audio or "test"
        cmd += ["-re", "-f", "lavfi", "-i", "sine=f=440:r=48000,aformat=channel_layouts=stereo"]
    else:
        # The standard fixes the frame rate (some drivers refuse -framerate outright). Frame times go on the wall
        # clock ALSA uses, and -isync lines the sound up against the picture's start: otherwise each starts at
        # zero, and sound opened a second after the picture plays a second early. That only works with the
        # analysis outputs below kept quick; see there.
        cmd += ["-f", "v4l2", "-thread_queue_size", "2048"]
        if conf["V4L2_INPUT_FORMAT"]:
            cmd += ["-input_format", conf["V4L2_INPUT_FORMAT"]]
        cmd += ["-standard", "NTSC" if ntsc else "PAL", "-video_size", size, "-timestamps", "abs", "-i", dev]
        if audio:
            cmd += ["-isync", "0", "-f", "alsa", "-thread_queue_size", "2048", "-sample_rate", "48000",
                    "-channels", "2", "-i", audio]

    preview = conf["PREVIEW_UDP"].strip()
    vouts = (["[m]"] if master else []) + (["[a0]"] if access else []) + ["[d]"] + (["[pv]"] if preview else [])
    # Rec. 601 pixel shape: the picture is the middle 704 samples, not all 720. A widescreen (anamorphic) picture is
    # the same pixels shown 4/3 wider.
    sar = {(True, False): "10/11", (False, False): "12/11", (True, True): "40/33", (False, True): "16/11"}[(ntsc, wide)]
    graph = ["[0:v]setfield=%s,setsar=%s,split=%d%s" % (conf["FIELD_ORDER"], sar, len(vouts), "".join(vouts))]
    if access:
        crop = ""
        if conf["MP4_CROP"].strip():
            l, r, t, b = (int(x) for x in conf["MP4_CROP"].split(":"))
            crop = "crop=iw-%d:ih-%d:%d:%d," % (l + r, t + b, l, t)
        graph.append("[a0]bwdif=mode=send_field:parity=auto,%sformat=yuv420p[ao]" % crop)
    # Analysis: freeze detection, sound level and a snapshot, to a null output (nullsink filters trip an assertion
    # in ffmpeg 7.1 at shutdown). Every output must keep up within a fraction of a second: ffmpeg stops reading an
    # input whose outputs run ahead of the slowest one, and a USB sound card overruns after ~0.7 s unread. A
    # snapshot every 2 s silenced most of a tape's sound once it was in sync.
    graph.append("[d]scale=320:240,split[d1][d2];"
                 "[d1]scale=160:120,showinfo=checksum=0,fps=10,freezedetect=n=0.003:d=2[frz];"
                 "[d2]fps=5[snap]")
    if audio:
        aouts = (["[am]"] if master else []) + (["[aa]"] if access else []) + ["[ax]"] + (["[ap]"] if preview else [])
        # first_pts=0 fills the gap before the sound opened with silence: the live MP4 has no edit list, so a late
        # start would be lost there
        delay = int(conf["AUDIO_DELAY_MS"] or 0)
        graph.append("[1:a]aresample=async=1000:first_pts=0,%sasplit=%d%s"
                     % ("adelay=%d:all=1," % delay if delay > 0 else "", len(aouts), "".join(aouts)))
        graph.append("[ax]asetnsamples=n=9600,astats=metadata=1:reset=1:measure_perchannel=RMS_level+DC_offset:"
                     "measure_overall=Peak_level,ametadata=mode=print[lvl]")
    cmd += ["-filter_complex", ";".join(graph)]

    if master:
        cmd += ["-map", "[m]"] + (["-map", "[am]", "-c:a", "pcm_s16le"] if audio else [])
        cmd += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "16", "-slicecrc", "1",
                "-pix_fmt", "yuv422p"] + colors
        if vfat:  # FAT32 can't hold files over 4 GB
            path = base + "_master_%03d.mkv"
            cmd += ["-f", "segment", "-segment_time", "240", "-segment_format", "matroska",
                    "-reset_timestamps", "1", path]
        else:
            path = base + "_master.mkv"
            cmd += [path]
        files.append(path)
    if access:
        path = base + "_access.mp4"
        cmd += ["-map", "[ao]"] + (["-map", "[aa]", "-c:a", "aac", "-b:a", "192k"] if audio else [])
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-fps_mode", "cfr", "-r", "60000/1001" if ntsc else "50"] + colors
        # fragmented while recording, so a crash leaves it playable; rewritten with the index in front afterwards
        cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", path]
        files.append(path)
    cmd += ["-map", "[snap]", "-f", "image2", "-update", "1", "-q:v", "5", snap]
    if preview:  # UDP never blocks; nobody listening is fine
        cmd += ["-map", "[pv]"] + (["-map", "[ap]", "-c:a", "mp2", "-b:a", "192k"] if audio else [])
        cmd += ["-c:v", "mpeg2video", "-q:v", "4", "-g", "15", "-f", "mpegts", "udp://%s?pkt_size=1316" % preview]
    cmd += ["-map", "[frz]"] + (["-map", "[lvl]"] if audio else []) + ["-f", "null", "-"]
    return cmd, files


class Damage:
    """What a capture lost, counted from ffmpeg's analysis output: frames the device never delivered (gaps in the
    picture's timestamps), sound overruns (each loses up to a second), 0.2 s stretches at full scale, and one
    channel gone quiet while the other has sound (usually a cable plugged into the wrong jack)."""

    def __init__(self, rate):
        self.rate = rate
        self.reset()

    def reset(self):
        self.missing_frames = 0
        self.xruns = 0
        self.last_pts = None
        self.chan = {}                     # this 0.2 s of sound: channel+measure -> value, from astats
        self.sound_windows = 0             # 0.2 s windows with sound on either channel
        self.one_sided = {"1": 0, "2": 0}  # ... where that channel was 20 dB below the other
        self.clipped = 0                   # ... that reached full scale
        self.peak_db = None

    def frame(self, t):
        if self.last_pts is not None and t - self.last_pts > 1.5 / self.rate:
            self.missing_frames += round((t - self.last_pts) * self.rate) - 1
        self.last_pts = t

    def sound(self, chan, key, value):
        try:
            v = float(value)
        except ValueError:
            v = float("-inf")
        if chan != "Overall":
            self.chan[chan + key] = v
            return
        self.peak_db = v
        if v >= -0.1:
            self.clipped += 1
        ac = {}  # level per channel without the device's constant DC offset
        for c in ("1", "2"):
            rms = 10 ** (self.chan.get(c + "RMS_level", float("-inf")) / 20)
            ac[c] = max(rms * rms - self.chan.get(c + "DC_offset", 0) ** 2, 1e-12) ** 0.5
        if max(ac.values()) > 0.01:  # louder than -40 dB on at least one side
            self.sound_windows += 1
            for c, other in (("1", "2"), ("2", "1")):
                if ac[c] < ac[other] / 10:
                    self.one_sided[c] += 1

    def summary(self):
        text = "%d frames missing (%.1f s), %d sound overruns, %d clipped moments" % (
            self.missing_frames, self.missing_frames / self.rate, self.xruns, self.clipped)
        for c, side in (("1", "left"), ("2", "right")):
            if self.sound_windows >= 25 and self.one_sided[c] > self.sound_windows * 0.2:
                text += "; %s channel nearly silent %d%% of the time: check the audio cable" % (
                    side, 100 * self.one_sided[c] // self.sound_windows)
        return text

    def as_dict(self):
        return {"missing_frames": self.missing_frames, "missing_seconds": round(self.missing_frames / self.rate, 2),
                "sound_overruns": self.xruns, "clipped_moments": self.clipped,
                "one_sided_windows": dict(self.one_sided), "sound_windows": self.sound_windows}


class Recording:
    def __init__(self, conf, dev, audio, base, mode, vfat, snap):
        self.base, self.mode = base, mode
        self.started = time.time()
        self.frozen = False
        self.frozen_since = None
        self.unfrozen_since = self.started
        self.motion_seen = False
        self.ever_frozen = False
        self.signal_at = None      # when the picture first locked; None while waiting for play
        self.standby = False       # recording ahead of play, thrown away and started over until a picture comes
        self.first_frame_at = None
        self.after_kill = None     # standby: when the recording before this one was killed
        self.stopping = False
        self.label = ""
        self.item = None
        self.reason = ""
        ntsc = conf["STANDARD"].upper() != "PAL"
        self.damage = Damage(30000 / 1001 if ntsc else 25)
        cmd, self.files = build_command(conf, dev, audio, base, mode, vfat, snap)
        self.logf = open(base + ".log", "w")
        self.logf.write("videocap recording started %s\nvideo: %s  audio: %s  standard: %s  mode: %s  profile: %s\n\n"
                        "%s\n\n" % (datetime.now().isoformat(timespec="seconds"), dev, audio or "NONE",
                                    conf["STANDARD"], mode, conf["PROFILE"], " ".join(cmd)))
        self.logf.flush()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, text=True, errors="replace")
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def note(self, text):
        try:
            self.logf.write("videocap %s\n" % text)
            self.logf.flush()
        except ValueError:
            pass

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.parse(line)

    def parse(self, line):
        if "[Parsed_showinfo" in line:  # one line per frame as it arrived from the device
            m = re.search(r"pts_time:(-?[\d.]+)", line)
            if m:
                if self.first_frame_at is None:
                    self.first_frame_at = time.time()
                self.damage.frame(float(m.group(1)))
            return
        m = re.search(r"lavfi\.astats\.(1|2|Overall)\.(RMS_level|DC_offset|Peak_level)=(\S+)", line)
        if m:
            return self.damage.sound(*m.groups())
        if line.startswith("frame:") or "[Parsed_ametadata" in line or re.match(r"\w+=\S*$", line.strip()):
            return
        if "ALSA buffer xrun" in line:
            self.damage.xruns += 1
        if "freeze_start" in line:
            self.frozen, self.frozen_since, self.ever_frozen = True, time.time(), True
        elif "freeze_end" in line:
            self.frozen, self.unfrozen_since = False, time.time()
        try:
            self.logf.write(line)
        except ValueError:
            pass

    def update(self):
        now = time.time()
        if not self.frozen and now - self.unfrozen_since >= 8 and now - self.started >= 8:
            self.motion_seen = True

    def kill(self):
        """End at once, finalizing nothing (a standby recording that's thrown away)."""
        self.stopping = True
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        try:
            self.logf.close()
        except OSError:
            pass

    def stop(self, reason):
        self.stopping, self.reason = True, reason
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)  # ffmpeg finalizes its files on SIGINT
            try:
                self.proc.wait(60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        time.sleep(0.5)  # let the stderr reader drain
        dur = int(time.time() - self.started)
        self.note("recording stopped %s after %d:%02d:%02d: %s (ffmpeg exit %s)"
                  % (datetime.now().isoformat(timespec="seconds"), dur // 3600, dur % 3600 // 60, dur % 60,
                     reason, self.proc.returncode))
        self.logf.write("damage: %s\n" % self.damage.summary())
        self.logf.close()

    def duration(self):
        return time.time() - (self.signal_at or self.started)
