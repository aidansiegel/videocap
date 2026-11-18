# Changelog

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
