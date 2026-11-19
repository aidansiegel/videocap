# Videocap desktop

A small app for your Mac, Windows or Linux computer that finds the Videocap stations on your network by itself,
shows what each one is doing, opens a station's page in its own window, and tells you (with a desktop notification)
when a capture finishes, is waiting for you to watch it, or a station needs attention. The capture itself still
happens on the station.

## Install

Download it from the [latest release](https://github.com/aidansiegel/videocap/releases):

- **Mac:** `Videocap-…-mac-universal.dmg`. Open it and drag Videocap to Applications. The app isn't signed by
  Apple yet, so the first time, **right-click it → Open → Open** (or System Settings → Privacy & Security →
  *Open Anyway*).
- **Windows:** `Videocap-…-win-x64.exe`. Windows may say it protected your PC: **More info → Run anyway**. Allow
  it on private networks if Windows asks, so it can find stations.
- **Linux:** `Videocap-…-linux-….AppImage` (x64 or arm64): make it executable (`chmod +x`), then run it.

Stations show up by themselves when they're on the same network. If one doesn't, type its address (like
`videocap.local` or `192.168.1.20`) under *Add a station by its address*. On a Mac the app keeps running in the Dock
with its windows closed, so notifications still arrive.

## Develop

```bash
cd desktop
npm install
npm start                                  # run it
node --test test/stations.test.js          # the logic tests (no Electron needed)
npm run dist                               # build an installer for this computer, in dist/
```

Stations advertise themselves as `_videocap._tcp` (Avahi, from the station's `videocap/mdns.py`); the app finds them
with [bonjour-service](https://github.com/onlxltd/bonjour-service) and reads each one's `/status.json`. Station pages
open in sandboxed windows with no access to the computer; only the app's own list window has a bridge to the app
(`preload.js`: the list of stations, open, add, remove).
