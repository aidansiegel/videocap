# Videocap guide

What happens day to day, what it writes, the settings, and why it works the way it does. Start with the
[README](../README.md).

## Day to day

1. **Say what it is** under *Next capture* on the web page: type a title (and a number, format, date and notes if
   you like), or load your list once with *Item list* and each capture takes the next item. You can also fill it in
   while it records; the files are renamed when it finishes.
2. **Put it in, cue it up, press play** while the page says *Ready*, and walk away. Recording stops a few minutes
   after it ends.
3. **After each take, by itself:** it goes where *Send captures to* says. As shipped that's just the disk you
   plugged in; with a destination set up, the master moves to a masters disk or uploads, and the MP4 goes to your
   library.
4. **Watch it:** flip the *Video selector* over the preview window from *Live* to *Captures*, and the newest
   capture plays there; click any capture's card (or use the arrows under the picture) to play another, and flip
   back to *Live* for the live picture. Then press *Keep* (the master goes to your archive) or *Bad take* (moved to a `discarded` folder,
   never sent, nothing deleted) and capture it again. Captures play from the capture disk, so its disk has to be
   plugged in.

The page always says what to do next in plain words, including *Wait* while the last master is being moved and
checked (a recording started then can lose frames).

Things that look wrong but aren't:
- **A take under 5 minutes** is taken as a false start (the picture dropped out just after play, so you started
  again): held, and the item number doesn't move on. It stays listed with a button in case it really was short.
- **A take stopped by hand** is held whole, MP4 too, until you approve it: it's usually a do-over.
- **A master starts with up to 5 minutes of nothing:** that's the standby wait before you pressed play, kept so
  nothing is ever cut. The MP4 has it trimmed; the log says where the picture began.

## Finding the page

The station announces itself on your network, so its page is at **http://videocap.local/** from a phone or
computer on the same network, whatever the machine is called. A second station takes `videocap-2.local`, and so on.
To use another name (two stations, say), set `NETWORK_NAME` in the settings file; blank turns it off.

If a device can't open `.local` names (some Android phones, some Windows setups), use the machine's address
instead: `hostname -I` on the station prints it. Give the station a fixed address so that one never changes,
with a DHCP reservation in your router's settings (often called "reserved IP" or "static lease").

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
switch on the page before pressing play) and the master and MP4 are labeled with the widescreen pixel shape, so every
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

The easy way is **Setup** on the web page (top right; it opens by itself the first time): it shows what's plugged
in, and sets the TV standard, capture stick, input, capture disk, time zone, collection name, review,
and where captures go. Each change is checked and saved at once into `/etc/videocap/videocap.conf`, keeping your
comments and a `.bak` of the previous file.

Everything is in that file, explained in [videocap.conf.example](../videocap.conf.example). A few settings are
file-only, because the page has no login: `HOOK`, `RCLONE_CONFIG`, `VIDEO_DEVICE`, `AUDIO_DEVICE`, `PAUSE_UNITS`.
Most apply from the next recording without a restart. Logs: `journalctl -u videocap`.

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
  [docs/recipes.md](recipes.md).
- **Item list:** the easiest way is *Item list* on the web page: copy the cells from a spreadsheet and paste them,
  open a CSV, or type one title per line. A header row names the columns: number (or ID, Tape #), title (or name),
  label, format, aspect, date and notes (or description). Without numbers, items are numbered 001, 002, ... in order.
  A list loaded on the page replaces `ITEM_LIST`, the same thing as a file:
  ```csv
  id,label,title,format,date,notes
  B01-T01,"Xmas '93 / Florida",Christmas 1993 and Florida,Hi8,Dec 1993,At Grandma's
  B01-T02,Summer 94,,Hi8,,
  DV-07,Beach week,,Digital8,,
  ```
  Notes and the date go into the library's .nfo description (and the year, when there is one).
- **Picture shape:** 4:3 unless the item list's `aspect` column, the `ASPECT` setting or the page's *Picture shape*
  switch says 16:9. See [Widescreen](#widescreen).
- **Device profiles** (`PROFILE`): field order, crop, audio delay and picture-lock detection for a stick.
  [docs/profiles.md](profiles.md) shows how to measure your own.
- **Hooks** (`HOOK`): a program run after each take is finished, approved or discarded, for checksum manifests,
  transcripts, notifications or a spreadsheet. Example: [hooks/inventory-csv.sh](../hooks/inventory-csv.sh).

## Lights and the button (no screen needed)

On a Raspberry Pi, the two lights next to the power socket show what the station is doing, so you can record with no
screen, phone or network. Count the blinks. **Print a card** under Help on the page keeps a copy with the station.

| Light | Meaning |
| --- | --- |
| Red, 1 second on, 1 second off | Recording |
| Green, 3 blinks, pause | Ready: everything is plugged in. Press play (it records from the first frame) |
| Green, 2 blinks, pause | Connect the capture disk |
| Green, 1 blink, pause | Plug in the capture device |
| Green, fast flicker | Finishing the files: don't unplug anything |
| Green, on with a short blink off | Moving or uploading: leave the disk connected |
| Green, steady | Finished, or made safe with the button: safe to unplug the disks |
| Red, 1 blink, pause | Disk problem: it's full, or it couldn't be read |
| Red, 2 blinks, pause | The recording couldn't start: check the capture device |
| Red, 3 blinks, pause | A recording stopped on an error: see the page when you can |
| Both, fast flicker | Shutting down: wait for the red light to stay on |

**Swapping disks without a screen:** a disk is safe to pull at 1 or 2 green blinks or steady green. Any other time,
press the Raspberry Pi 5's power button once (both lights flicker to say it heard you). It stops a recording and
finishes its files, stops standing by, pauses moves and uploads (a cut-off copy is simply done again later), and
unmounts everything on a USB disk, its own capture disks and any other (a masters disk the system mounts, say); then
the light goes steady green. If something won't let go of a disk, it says what (on the page, and with one red blink)
and keeps trying instead of saying it's safe. Nothing records again until the swap.

A disk the system mounts at boot (in `/etc/fstab`) doesn't come back by itself after a swap. Give it
`x-systemd.automount,x-systemd.idle-timeout=120` in its options, so it's mounted when something opens it and
unmounted when nothing has for two minutes:

```
UUID=... /mnt/masters btrfs defaults,noatime,nofail,x-systemd.automount,x-systemd.idle-timeout=120 0 0
```
 Swap the disks: a disk going in or coming out carries on
by itself, as does a second press or REC on the page. A take stopped this way is held for review, like one stopped on
the page.

**A disk pulled out anyway** is looked over the next time it's plugged in, before anything is written to it (FAT and
exFAT, which are left half-written; fixed by itself where that's safe, or reported on the page). A recording cut off
this way keeps what it had.

**Asking it what's going on:** press the power button twice, and the green light spells out the status in Morse
code (red keeps showing whether it's recording): `REC B004 V001 23M FREE 85G`, `READY B004 V002`, `ERR DISK FULL`,
`SAFE`... Press three times for its network address (`IP 192.168.1.23 PORT 8091`), to find its page on a network you
don't know. Each message starts with a one-second flash and ends with `.-.-.`; it's 12 words a minute
(`MORSE_UNIT_MS=100`).

Read it with the **light reader** on your phone: [aidansiegel.github.io/videocap/reader](https://aidansiegel.github.io/videocap/reader/).
Open it once at home and it keeps working with no network. Point the camera at the light from a hand's width away;
it finds the blinking light by itself (or tap it), and shows the message and what it means. Phones only allow the
camera on https pages, which is why it lives there and not on the station.

**Its status over Bluetooth:** the station also broadcasts its status as its Bluetooth name, a line every 6 seconds:
`V0 REC 23M B004V001 85G`, `V1 NOW FRENCH TV ON MUSIC BEAT`, `V2 TIME 23M FREE 85G`, `V3 CODES NONE`,
`V4 IP 10.0.0.5:8091`... V0 is always the gist (8 characters of what it's doing, then the detail that matters most);
the rest repeat within a minute. No pairing, network or button: any phone's Bluetooth scanner app (nRF Connect,
LightBlue) shows it from about 10 m away, and on a computer `python3 -m videocap.btreader` collects a whole round and
prints the report (it needs `pip install bleak`; on a Mac, run it in Terminal, which asks for Bluetooth permission).
It's a broadcast only: nothing can connect to the station this way. `BLUETOOTH=none` turns it off.

**Codes**, the same on the light, over Bluetooth and on the page:

| Code | Meaning |
| --- | --- |
| E01 | A disk couldn't be read |
| E02 | Disk full |
| E03 | No room for a master |
| E04 | The recording couldn't start |
| E05 | A recording stopped on an error |
| E06 | Disk unplugged while recording |
| E07 | Not safe to unplug yet: a disk is in use |
| E08 | Power supply too weak |
| E09 | Masters disk nearly full |
| E10 | An upload or move failed |

**Capture devices:** the ones with a profile in `videocap/profiles` are tested (so far the Hauppauge USB-Live2 Rev
E1). Any other still records, with a note on the page about what might be off: it may not notice play (so standby can
miss the start), the picture may comb on motion, or the sound may drift. [profiles.md](profiles.md) shows how to check
one and write its profile.

**Reading the light with a computer's camera:** `python3 -m videocap.reader` (on a Mac, run it in Terminal, which
asks for camera permission). Point the camera at the station and press its button twice or three times.

**Shutting down:** hold the power button 3 seconds. It finishes any recording, then powers off; unplug the power once
the red light stays on. `BUTTON=none` leaves the button as the power button.

**What this takes from the Pi:** nothing it needs. Normally the green light flickers when the SD card is busy, and the
red one shows the Pi is off (or, on some models, a weak power supply). The Pi's boot error codes and its "off" light
come from its firmware, so videocap can't hide them; a weak supply shows as a warning on the page instead. When
videocap stops, both lights and the button go back to what they did before.

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
