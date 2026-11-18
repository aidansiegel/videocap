# Changelog

## 0.0.1 alpha 2 (2026-11-18)

- The station announces itself as `videocap.local` (Avahi), so the page has the same address whatever the machine is
  called; a second station takes `videocap-2.local`. Setting: `NETWORK_NAME`.
- Name and describe each capture on the page (title, number, format, date, notes), before or while it records;
  the files are renamed when it finishes. Notes and the date go into the library .nfo.
- Watch any capture in the page's preview window, with seeking: a Live/Captures switch box over the picture,
  arrows to step through captures, and a click on a capture's card to play it.
- Load the item list on the page: paste cells from a spreadsheet, open a CSV, or type one title per line.
- No more cassette animation or REVIEW badge on the display.
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
