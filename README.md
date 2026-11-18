# Videocap

> **0.0.1 alpha.** The whole capture path runs end to end in its tests and demo, but this version hasn't yet
> recorded a real tape on real hardware. Expect rough edges, and keep your tapes until you've checked the masters.

**Unattended capture from analog video.** Plug a VCR, camcorder, LaserDisc player or any other composite or S-Video
source into a USB capture stick on a Raspberry Pi (or any Linux machine), press play, and walk away. videocap records
each take to a lossless master and an MP4, stops by itself when the source ends, names it from your list, checks
what the capture lost, and moves it on to your library and your archive.

It's built for working through a whole collection (dozens or hundreds of tapes or discs, over weeks) without losing
anything, without wasting playback time, and ending with something people can find things in. It works just as well
for a single tape, or for recording a live analog feed.

- **Lossless master:** FFV1 + PCM in Matroska, the whole 720×480/576 frame, interlaced as captured, tagged with the
  right field order, colour and pixel shape. FFV1 in Matroska is the preservation format of choice for many archives
  (RFC 9043).
- **An MP4 for watching:** H.264, deinterlaced to 59.94p/50p (bwdif), the blank edges and head-switching noise
  cropped, the wait before play trimmed, ready for Jellyfin, Plex, Kodi or a phone.
- **Hands-off:** it records in standby while it waits, so the first frame is kept when you press play; it stops when
  the picture freezes on a player's blue screen or the signal goes; false starts are held, not filed as the next item.
- **A damage report for each take:** frames the stick never delivered, sound overruns, clipping, and a channel gone
  quiet (a cable in the wrong jack), live while recording and in a JSON report beside the master, so you know what to
  capture again.
- **Review before archive:** masters wait for your OK on the web page (after you've watched the MP4) before they go
  anywhere you can't easily take them back from.
- **Storage across many terabytes:** each master moves from the capture disk to a bigger masters disk, checked byte
  for byte, with checksums; it refuses to record when there's no room for a master; a full capture disk can be
  swapped while its uploads finish.
- **Uploads anywhere rclone goes:** a NAS, another computer, Backblaze B2, S3 and Glacier, Google Drive. The queue
  survives restarts and unplugged disks.
- **Small and proven:** Python's standard library, ffmpeg, rclone, systemd. About 1,900 lines.

It runs on about $150 of hardware per station.

## Sources

Anything with a composite (yellow RCA) or S-Video output and analog sound:

- **Tapes:** VHS, S-VHS, VHS-C, Video8, Hi8, Betamax, and MiniDV/Digital8 through the camcorder's analog output.
  This is what videocap was first built for, and where the standby recording, freeze detection and false-start
  handling come from.
- **Discs:** LaserDisc, CED, VHD.
- **Film transfers** from a projector or telecine with a video output.
- **Live sources:** an analog camera, a security system, a retro console or computer. Turn off the stop rules that
  assume a tape (see [Live sources](#live-sources)).

NTSC and PAL, at standard definition.

## What you need

- A **Raspberry Pi 5** (4 GB is plenty) with its official 27 W power supply, or any Linux machine. One capture uses
  about two of a Pi 5's four cores.
- A **USB capture stick**. videocap works with any V4L2 device with a USB sound card; a *profile* describes a stick's
  quirks. Built in: `usblive2-e1` (Hauppauge USB-Live2 Rev E1, which needs a patched driver; see
  [docs/profiles.md](docs/profiles.md)) and `generic`. Measuring and adding your stick takes one test recording.
- A **USB SSD or hard disk** with a partition for captures (exFAT is best; name it, for example, `CAPTURES`), and
  ideally a second, bigger disk or folder for masters.
- Your **source**, connected by S-Video if it has it (sharper than composite), and its audio cables.

## Install

On Raspberry Pi OS (Bookworm or later), Debian or Ubuntu:

```bash
git clone https://github.com/aidansiegel/videocap.git && cd videocap
sudo ./install.sh
sudo nano /etc/videocap/videocap.conf     # STANDARD, PROFILE, CARD_LABEL, and where captures go
python3 -m videocap --check               # (from /opt/videocap) the settings in effect and what's plugged in
```

The web page is at `http://<the machine's name>.local/` from a phone or computer on the same network.

<img src="https://raw.githubusercontent.com/aidansiegel/videocap/main/docs/web-page.png" width="360" alt="The station's web page while recording: state, live picture, audio meter, item, elapsed time and damage counts">

(Here recording ffmpeg's test pattern.)

## Day to day

1. **Check the item number** on the web page: it shows *Next item* from your list. Pick another if what's in your
   hand is a different one.
2. **Put it in, cue it up, press play** while the page says *Ready*, and walk away. Recording stops a few minutes
   after it ends.
3. **After each take, by itself:** it goes where *Send captures to* says. As shipped that's just the disk you
   plugged in; with a destination set up, the master moves to a masters disk or uploads, and the MP4 goes to your
   library.
4. **Watch the MP4,** then press *Master is good: send it* (it goes to your archive) or *Bad take: set it aside*
   (moved to a `discarded` folder, never sent, nothing deleted) and capture it again.

The page always says what to do next in plain words, including *Wait* while the last master is being moved and
checked (a recording started then can lose frames).

Things that look wrong but aren't:
- **A take under 5 minutes** is taken as a false start (the picture dropped out just after play, so you started
  again): held, and the item number doesn't move on. It stays listed with a button in case it really was short.
- **A take stopped by hand** is held whole, MP4 too, until you approve it: it's usually a do-over.
- **A master starts with up to 5 minutes of nothing:** that's the standby wait before you pressed play, kept so
  nothing is ever cut. The MP4 has it trimmed; the log says where the picture began.

## Live sources

For a source with no end of its own, start and stop from the web page and turn off the rules that assume a tape:

```ini
STANDBY_SEC=0             # no standby recording: press Start
STOP_AFTER_FROZEN_MIN=0   # a still picture doesn't end the take
GIVE_UP_NO_MOTION_MIN=0
MAX_MINUTES=0             # no length limit (MIN_FREE_GB still stops it before the disk fills)
MIN_TAKE_MINUTES=0        # no false starts
```

It still stops when the signal goes for `SIGNAL_LOST_SEC`.

## Widescreen

Most home recordings are 4:3, and so are letterboxed widescreen ones (black bars above and below, in a 4:3 picture):
leave those alone. A source recorded in a camcorder's 16:9 mode (some Hi8, Digital8 and MiniDV models) is *anamorphic*:
the full width carries a horizontally squeezed widescreen picture. Mark those 16:9 (in the item list, or with the
switch on the page before pressing play) and the master and MP4 are labelled with the widescreen pixel shape, so every
player stretches them back. Capture sticks don't pass on the camcorder's own widescreen signal, so this can't be
detected by itself.

Captured one at the wrong shape? The pixels are all there; only the label is wrong. Relabel a master in seconds, with
no re-encoding (mkvtoolnix), then make its MP4 again:

```bash
mkvpropedit "master.mkv" --edit track:v1 --set display-unit=3 --set display-width=16 --set display-height=9
```

## What it writes

On the capture disk, in `captures/` (for a listed item, `T07 - Summer 1994 - captured 2026-11-17 1430`; without a
list, `capture007_2026-11-17_1430`):

| File | What |
| --- | --- |
| `…_master.mkv` | Lossless master: FFV1 + 16-bit PCM, whole frame, interlaced as captured. Opens in VLC, DaVinci Resolve, ffmpeg (not QuickTime). On FAT32, 4-minute pieces. |
| `…_access.mp4` | H.264 + AAC, deinterlaced, cropped. Written fragmented while recording (a crash leaves it playable), then rewritten with its index first. |
| `….log` | The ffmpeg command, what happened, why it stopped, and a `damage:` line. |
| `….json` | Report: item, times, why it stopped, damage counts, files, profile. |
| `….nfo` | For listed items: title, sort title (the item ID), description, collection tag, for Kodi/Jellyfin/Emby. |
| `….md5` | Checksums of the master, log and report, read back from the masters disk (`md5sum -c` checks them). |

ffprobe and MediaInfo report the master's stream field order as `tb` / "bff (swapped)": that's how ffmpeg tags
interleaved top-field-first frames, and every frame carries the correct top-field-first flag, which is what decoders
use.

## Settings

All in `/etc/videocap/videocap.conf`, explained in [videocap.conf.example](videocap.conf.example). Most apply from
the next recording without a restart. Logs: `journalctl -u videocap`.

- **Where captures go:** as shipped, everything stays on the disk you plug in. Name destinations in the settings
  file and pick one on the page under *Send captures to*; each capture goes where the picker pointed when it
  started.
  ```ini
  DEST.aws.LABEL=AWS
  DEST.aws.MASTER_REMOTE=aws:my-video-bucket/masters
  DEST.dropbox.LABEL=Dropbox
  DEST.dropbox.MP4_REMOTE=dropbox:Home Videos
  ```
  Remotes are rclone destinations (AWS, a VPS over SFTP, Dropbox, B2, a NAS...) or local folders. Recipes:
  [docs/recipes.md](docs/recipes.md).
- **Item list** (`ITEM_LIST`): CSV with `id,label` and optionally `title,format`, in the order you'll capture them.
  ```csv
  id,label,title,format,aspect
  B01-T01,"Xmas '93 / Florida",Christmas 1993 and Florida,Hi8,
  B01-T02,Summer 94,,Hi8,
  DV-07,Beach week,,Digital8,16:9
  ```
- **Picture shape:** 4:3 unless the item list's `aspect` column, the `ASPECT` setting or the page's *Picture shape*
  switch says 16:9. See [Widescreen](#widescreen).
- **Device profiles** (`PROFILE`): field order, crop, audio delay and picture-lock detection for a stick.
  [docs/profiles.md](docs/profiles.md) shows how to measure your own.
- **Hooks** (`HOOK`): a program run after each take is finished, approved or discarded, for checksum manifests,
  transcripts, notifications or a spreadsheet. Example: [hooks/inventory-csv.sh](hooks/inventory-csv.sh).

## LEDs (Raspberry Pi)

| Green LED | Meaning |
| --- | --- |
| Heartbeat | Waiting for the capture stick or the disk |
| Solid | Ready, or finished: safe to unplug the disk |
| Slow blink | Recording |
| Fast blink | Finishing the files: don't unplug anything |
| Mostly on, short blink off | Moving or uploading: leave the disk connected |
| Off, red blinking | Something went wrong: see the web page |

## Lessons built in

Every one of these cost real recordings before it was handled:

- **A camcorder or VCR sends no picture until its tape moves,** so a recording started by the picture misses the
  first seconds (often a fade-in). Hence standby recording.
- **Picture and sound come from different clocks** on USB sticks, and the sound device opens later. videocap puts the
  picture on the wall clock and lines the sound up against it; a profile adds the stick's own delay.
- **ffmpeg stops reading an input whose outputs fall behind,** and a USB sound card overruns after about 0.7 s
  unread. Every analysis output (snapshot, freeze detection, meter) runs fast enough to keep up.
- **Heavy disk work during a capture loses frames,** including copying the previous master off the same disk, so the
  page says *Wait* until that's done, and other jobs listed in `PAUSE_UNITS` are frozen while recording.
- **Some drivers say "signal ok" with nothing plugged in;** profiles name a lock control to read instead.
- **A short take is usually a false start,** not the next item.

## Development

```bash
python3 -m unittest discover tests          # unit tests anywhere; the end-to-end test needs ffmpeg and rclone
VIDEOCAP_TEST_DIR=/some/roomy/disk python3 -m unittest tests.test_end_to_end -v
```

The end-to-end test records a 14-second test pattern that then freezes, as a tape would, through the whole path:
stop on freeze, MP4 rewrite, move to the masters disk, uploads to folder "remotes", review, approval and checksums.
Settings `TEST_SOURCE` (an ffmpeg lavfi graph) and `TEST_CARD_DIR` (a folder) run a station with no hardware at all.

Code map: `app.py` (the station's state machine), `recording.py` (the ffmpeg command and what its output says),
`queue.py` (moves, uploads, review), `hardware.py` (devices, disks, LEDs), `collection.py` (item list, names, .nfo),
`web.py` + `static/index.html` (the page), `config.py` (settings and profiles).

## Roadmap

- **Several stations at once:** one page for every source in the house, a shared item list so two stations never get
  the same item, master-only capture with the MP4 made afterwards so two sticks fit on one Pi.
- **More profiles:** other em28xx and cx231xx sticks, Elgato, AverMedia, I-O Data.
- **Ready-made Pi image** with a first-boot setup page.
- DV/MiniDV over FireWire (dvgrab), with the same queue and review.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
