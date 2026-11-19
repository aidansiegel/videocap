# Changelog

## 0.0.1 alpha 3 (2026-11-18)

- **A ready-made Raspberry Pi card:** download the image, write it with Raspberry Pi Imager, boot, open
  http://videocap.local/. Built on GitHub with pi-gen and attached to each release. (Built and checked in the build
  log; not yet booted on a real Pi.)
- **Setup on the page:** what's plugged in and the page's own address, then the settings (TV standard, capture stick
  suggested from what it calls itself, input, disk, time zone, destinations...), each saved as it changes. Opens by
  itself the first time; no settings file to edit.
- **AGENTS.md:** how an AI agent (Codex, Claude Code) installs, sets up, runs and fixes a station for someone, and
  what it must never touch.
- **A live 30 fps picture** on the page while recording (motion JPEG, never able to hold up the recording).
- **The panel beside the display:** two matching slide switches, VIDEO SELECTOR (live picture or your captures) and
  PICTURE SHAPE (4:3 or 16:9), centered between the display and the instructions.
- **On real hardware:** a Raspberry Pi 5 with a Hauppauge USB-Live2 Rev E1 (Debian 13). Device and disk detection,
  setup and recording checked; the first full tape, a Hi8 recapture, was still recording at release time.
- **Hardening** from a failure-case review: no way to add settings-file lines from the page (a hidden line break could
  have added a HOOK= run as root), no POSTs from other sites' pages, moves checked against the disk before the
  original is deleted, crash-safe state files, unmounted masters disks caught, odd titles and lists handled, and more.
- Setup and Help as full-size buttons, the clock with AM/PM, REC and STOP under the picture.

## 0.0.1 alpha 2 (2026-11-18)

- The station announces itself as `videocap.local` (Avahi), so the page has the same address whatever the machine is
  called; a second station takes `videocap-2.local`. Setting: `NETWORK_NAME`.
- Name and describe each capture on the page (title, number, format, date, notes), before or while it records;
  the files are renamed when it finishes. Notes and the date go into the library .nfo.
- Watch any capture in the page's preview window, with seeking: a Live/Captures switch box over the picture,
  arrows to step through captures, and a click on a capture's card to play it.
- Load the item list on the page: paste cells from a spreadsheet, open a CSV, or type one title per line.
- No more cassette animation, REVIEW badge or REVIEW button; Help is an overlay (the Help button, top right).
- A shorter README; the details moved to `docs/guide.md`.
- American spelling throughout.

## 0.0.1 alpha (2026-11-17)

First version as a standalone project, generalized from a working single-station capture appliance that
digitized a family's Hi8 and VHS collection in November 2026.

- Destinations are rclone remotes (`MP4_REMOTE`, `MASTER_REMOTE`) instead of fixed SFTP hosts and keys.
- Optional item list as CSV or JSON; numbered takes (capture001, ...) without one.
- The freeze, no-motion and length limits can each be turned off (0), for live sources.
- Wording for any analog source, not only tapes.
- Device profiles (`PROFILE`), starting with `generic` and the Hauppauge USB-Live2 Rev E1.
- A JSON report beside each master; checksums cover master, log and report.
- `HOOK` for programs run after each take is finished, approved or discarded.
- Approving a held take (stopped by hand, or short) moves its master to the masters disk too.
- The web page is a separate file, with no mention of any particular library or archive.
- Unit tests and an end-to-end test that runs without capture hardware.
