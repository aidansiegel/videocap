# AGENTS.md: helping someone set up and run Videocap

This file is for an AI coding agent (Codex, Claude Code, or similar) helping a person install, configure, run or
fix a Videocap station. The person may know nothing about Linux, ffmpeg or video. Do the technical work for them,
explain in plain words what you did and why, and ask before anything that could lose a recording.

Videocap records analog video (VHS, Hi8, LaserDisc...) from a USB capture stick on a Raspberry Pi or other Linux
machine: a lossless master (FFV1 in Matroska) and an MP4 of each tape, named from the person's list, with a web page
to run it. The README says what it is; [docs/guide.md](docs/guide.md) has the details.

## Rules that protect recordings (read these first)

1. **Recordings are often irreplaceable.** Tapes wear out; a capture may be the only copy. Never delete, move or
   overwrite anything under the capture disk's `captures/` folder, the masters folder, or a destination, unless the
   person asks for that exact file to go, by name.
2. **Check before you restart or stop anything.** A restart ends the recording in progress. First read
   `http://localhost/status.json` (or `curl -s localhost/status.json` on the station): don't restart if
   `recording` is not null or `state` is `"finishing"`. `install.sh` checks this too and refuses.
3. **No wildcard deletes in `captures/` while the station runs.** In standby the station has already created the next
   capture's files, tiny and empty-looking, before anyone presses play. A glob like `rm B01*` can delete a live
   recording. Stop the station first (`sudo systemctl stop videocap`, after rule 2), or delete exact file names.
4. **One heavy job at a time.** Copying big files, scanning or re-encoding on the station while it records loses
   frames. Do heavy work after the capture finishes, or on another computer.
5. **Secrets stay out of chat, commands and git.** Cloud and NAS passwords live only in rclone's own config
   (`rclone config` asks for them interactively). Never paste a token into a command line or a file in this repo.
6. **The web page has no login.** It's for a home network. Never expose the station's port to the internet (no port
   forwarding); for remote access use a VPN such as Tailscale.

## Install, from nothing

What's needed: a Raspberry Pi 5 (4 GB is plenty) with its 27 W power supply and a microSD card, a USB capture stick,
a USB disk for captures, and the video player (VCR, camcorder...).

1. **The SD card.** On the person's computer, Raspberry Pi Imager → Raspberry Pi OS Lite (64-bit). In its settings,
   set a hostname (`videocap`), a user and password, Wi-Fi if no network cable, and turn on SSH. Write the card, put
   it in the Pi, power on, wait two minutes.
2. **Log in:** `ssh <user>@videocap.local` from the computer.
3. **Install:**
   ```bash
   sudo apt update && sudo apt install -y git
   git clone https://github.com/aidansiegel/videocap.git && cd videocap
   sudo ./install.sh
   ```
   It installs ffmpeg, rclone, v4l-utils, alsa-utils and Avahi, copies Videocap to `/opt/videocap`, puts settings at
   `/etc/videocap/videocap.conf` (never overwriting existing ones) and starts the `videocap` service.
4. **The capture stick.** Plug it in, then `python3 -m videocap --check` (run from `/opt/videocap`). It prints the
   settings in effect and `# capture device:` and `# audio:` lines.
   - `capture device: none found` with a **Hauppauge USB-Live2 Rev E1** (`lsusb` shows `2040:c220`): Linux's stock
     driver doesn't support that revision. Install the em28xx patches from
     https://github.com/NursePandaz/usblive2-e1-linux7 with DKMS, following that project's README, then reboot.
     See [docs/profiles.md](docs/profiles.md).
   - Other sticks: most UVC and em28xx/cx231xx sticks work with `PROFILE=generic`.
5. **The capture disk.** Plug in a USB disk. exFAT is best (any computer can read it). If the person agrees to
   erase it, format it with a name:
   ```bash
   lsblk -o NAME,SIZE,LABEL,FSTYPE,MOUNTPOINT       # find it, e.g. sda1; check the size matches
   sudo mkfs.exfat -L CAPTURES /dev/sda1             # ERASES the disk: confirm with the person first
   ```
6. **Setup on the page.** Open `http://videocap.local/` on a phone or computer on the same network. The setup screen
   opens by itself: it shows what's plugged in, and the person picks the TV standard (NTSC in the Americas and
   Japan, PAL in most other places), the capture stick, input, disk and time zone. Every change saves itself.
   If `videocap.local` doesn't open, use the Pi's IP address (`hostname -I` on the Pi).

## Settings

- **Prefer the web page** (Setup, top right). It checks values and writes `/etc/videocap/videocap.conf` for you,
  keeping a `.bak` of the previous file.
- The file itself is `KEY=value` lines, explained in [videocap.conf.example](videocap.conf.example). Edit it with
  `sudo nano /etc/videocap/videocap.conf`. Most settings apply from the next recording without a restart; after
  changing `WEB_PORT` or `NETWORK_NAME`, restart (rule 2): `sudo systemctl restart videocap`.
- Some settings are deliberately file-only, because the page has no login: `HOOK` (runs a program after each
  capture), `RCLONE_CONFIG`, `VIDEO_DEVICE`, `AUDIO_DEVICE`, `PAUSE_UNITS`, `TEST_*`.
- `python3 -m videocap --check` prints every setting in effect and flags unknown ones (typos).

## Sending captures somewhere (destinations)

Videocap uses rclone, so anything rclone reaches works. Set up the rclone remote on the station, then add a
destination on the page (Setup → Where captures go → Add a destination) using it, like `nas:Home Videos`.

- **A NAS or another computer over SFTP:** `rclone config` → new remote → `sftp`, host, user; use an SSH key, not a
  password, if the person can. Test: `rclone lsd nas:`.
- **A Windows or NAS shared folder (SMB):** `rclone config` → `smb`.
- **Dropbox, Google Drive, OneDrive:** these need a browser sign-in. On the station run `rclone config`, pick the
  service, and answer **No** to "Use web browser to automatically authenticate"; rclone prints an
  `rclone authorize "dropbox"` command. The person runs that on their own computer (with rclone installed), signs
  in, and pastes the result back into the station's prompt themselves.
- **Backblaze B2, S3, Glacier:** `rclone config` with the person's keys, typed into rclone's prompt by the person.
- **A media server (Jellyfin, Plex):** point "Send MP4s to" at its library folder; each MP4 gets a `.nfo` with its
  title. Recipes: [docs/recipes.md](docs/recipes.md).

Masters are big (about 30 GB per hour of tape). Check the destination has room before a big batch.

## Running it day to day

The person's routine: type a title on the page (or load their list with *Item list*), put the tape in, press play,
walk away. Recording starts when the picture appears and stops by itself at the end. Then they watch it (Video
selector → Captures) and press *Keep* or *Bad take*. Help is on the page (top right).

## Diagnosing

| Symptom | Look at | Usual cause and fix |
| --- | --- | --- |
| Page says "Plug in the capture stick" | `--check`, `lsusb`, `dmesg | tail -30` | Not plugged in, a charge-only cable/hub, or the USB-Live2 Rev E1 driver (install step 4) |
| No picture with the tape playing | Setup → What's plugged in; `v4l2-ctl -d /dev/video0 --get-input` | Wrong input: set Input to the plug used (S-Video or composite). Camcorders must be in playback (VCR/VTR) mode |
| Picture but no sound, or one channel silent | the capture's damage line, `arecord -l` | The audio cables in the stick's jacks the wrong way round, or a mono source: swap plugs |
| "Connect the capture disk" with one plugged in | `lsblk -o NAME,LABEL,FSTYPE,MOUNTPOINT` | Its name doesn't match `CARD_LABEL` (Setup → Capture disk), or a filesystem it can't write (use exFAT) |
| "Not recording: only N GB free" | `df -h` | Not enough room for a master (`MASTER_MIN_FREE_GB`): make room or swap the disk |
| Captures stuck "Uploading" or "Problem: …" | the capture's card on the page; `journalctl -u videocap -n 100` | Destination unreachable or full. Fix it, then press *Retry* on the card |
| Frames or sound lost in many captures | the capture's damage line | Other heavy work on the station during capture (rule 4), a weak power supply, or the stick on a hub |

Logs: `journalctl -u videocap -f` (live) or `-n 200`. Each capture also has a `.log` (the exact ffmpeg command and
what happened) and a `.json` report beside its master. The page's data is at `/status.json` and `/settings.json`.

## Updating

```bash
cd ~/videocap && git pull && sudo ./install.sh
```

The installer keeps the person's settings, and refuses while a recording is in progress.

## Working on the code

Python standard library only (plus ffmpeg and rclone as programs). Tests:

```bash
python3 -m unittest discover tests                      # unit tests anywhere; the end-to-end test needs ffmpeg and rclone
./demo.sh /tmp/videocap-demo 8080                       # the whole station with no hardware, at http://localhost:8080/
```

The demo writes about 1.5 GB per capture; delete its folder afterwards. Code map: README → Development, and
[docs/guide.md](docs/guide.md) → Development.
