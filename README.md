# Videocap

> **0.0.1 alpha.** Runs end to end in its tests and demo, but hasn't yet recorded a real tape on real hardware.
> Keep your tapes until you've checked the masters.

**Unattended capture from analog video.** Plug a VCR, camcorder, LaserDisc player or any composite or S-Video
source into a USB capture stick on a Raspberry Pi or other Linux machine, press play, and walk away. Each capture
becomes a lossless master and an MP4, named from your list, with a report of anything the capture lost.

- **Lossless master** (FFV1 + PCM in Matroska) and an **MP4 for watching** (H.264, deinterlaced, cropped).
- **Hands-off:** it's already recording when you press play, and stops by itself when the source ends.
- **Damage report** for each capture: dropped frames, sound overruns, clipping, a silent channel.
- **Name as you go,** or paste your list from a spreadsheet: titles, dates and notes go into your media library.
- **Review before archive:** masters wait for your OK on the web page.
- **Goes where you choose:** the disk you plugged in, or a NAS, AWS, a VPS, Dropbox and anything else rclone reaches.

NTSC and PAL, standard definition: VHS, Hi8, Betamax, LaserDisc, analog cameras and more.

<img src="https://raw.githubusercontent.com/aidansiegel/videocap/main/docs/web-page.png" width="360" alt="The web page while recording: live picture, audio meter, elapsed time, and the capture's title, number, format, date and notes">

## What you need

- A **Raspberry Pi 5** (4 GB) or any Linux machine
- A **USB capture stick** (see [docs/profiles.md](https://github.com/aidansiegel/videocap/blob/main/docs/profiles.md))
- A **USB disk** for captures

About $225 before the disk: a Pi 5 4 GB ($110), its 27 W power supply ($12), a case with a fan ($20) and a microSD card
($25) at Micro Center in November 2026, plus a Hauppauge USB-Live2 (about $55).

## Install

### The easy way: a ready-made Raspberry Pi card

No terminal, no settings file. You need a Raspberry Pi 5, its power supply, a microSD card (16 GB or more), a USB
capture stick and a USB disk.

1. **Download** the `videocap-…img.xz` file from the
   [latest release](https://github.com/aidansiegel/videocap/releases) (about 800 MB). Don't unzip it.
2. **Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)** on your computer and open it.
3. **Choose device:** Raspberry Pi 5. **Choose OS:** scroll to the bottom, *Use custom*, and pick the file you
   downloaded. **Choose storage:** your microSD card.
4. **Next → Edit settings** (when Imager asks whether to apply OS customization). Set these, or the Pi can't get
   online by itself:
   - **Hostname:** `videocap` (this becomes the address, `videocap.local`)
   - **Username and password:** anything you'll remember (only needed if you ever log in to fix something)
   - **Wireless LAN:** your Wi-Fi name and password, and your country (skip this if you'll plug in a network cable)
   - **Locale:** your time zone
5. **Save → Yes**, and wait for Imager to write and check the card.
6. **Put the card in the Pi**, plug in the capture stick and the USB disk, then the power. Give it about 3 minutes
   the first time.
7. **Open http://videocap.local/** on a phone or computer on the same Wi-Fi. The setup screen opens by itself:
   it shows what's plugged in, and you pick the TV standard, the capture stick, and where captures go.

If `videocap.local` doesn't open: wait a minute and try again, try `http://videocap/`, or find the Pi's address in
your router's list of devices. A Hauppauge USB-Live2 **Rev E1** needs an extra driver; see
[docs/profiles.md](https://github.com/aidansiegel/videocap/blob/main/docs/profiles.md).

### On a Raspberry Pi OS, Debian or Ubuntu machine you already have

```bash
git clone https://github.com/aidansiegel/videocap.git && cd videocap
sudo ./install.sh
```

Then open **http://videocap.local/** on a phone or computer on the same network: a setup screen walks you through
the rest (TV standard, capture stick, disk, where captures go), with no settings file to edit.

To try it without hardware, run `./demo.sh` on Linux or a Mac (needs ffmpeg and rclone).

**Not a Linux person?** Ask an AI coding agent (Codex, Claude Code...) to set it up for you: point it at
[AGENTS.md](https://github.com/aidansiegel/videocap/blob/main/AGENTS.md), which tells it how, and what never to touch.

## More

- [Guide](https://github.com/aidansiegel/videocap/blob/main/docs/guide.md): day to day, settings, live sources, widescreen, what it writes
- [Destinations](https://github.com/aidansiegel/videocap/blob/main/docs/recipes.md): NAS, AWS, VPS, Dropbox
- [Capture sticks](https://github.com/aidansiegel/videocap/blob/main/docs/profiles.md): profiles, and measuring your own

## License

GPL-3.0-or-later. See [LICENSE](https://github.com/aidansiegel/videocap/blob/main/LICENSE).
