# Device profiles

A profile is a small settings file describing one capture stick's quirks. videocap reads the built-in defaults,
then the profile named by `PROFILE`, then your `/etc/videocap/videocap.conf` (later wins), so anything in a profile
can still be overridden.

Profiles live in `videocap/profiles/` (built in) or `/etc/videocap/profiles/NAME.conf` (yours). `PROFILE` can also
be a path.

| Setting | What it describes |
| --- | --- |
| `FIELD_ORDER` | `tff` or `bff`: which field the stick sends first. Wrong = juddery motion in the MP4. |
| `MP4_CROP` | `left:right:top:bottom` pixels cut from the MP4 only: blank side margins and head-switching noise. |
| `AUDIO_DELAY_MS` | How much later the picture arrives than the sound, beyond the clocks. |
| `SIGNAL_CONTROL` | A V4L2 control that reports picture lock, for drivers whose input status always says "ok". |
| `SIGNAL_HOLD_SEC` | How long a lock still counts after it was last seen (for flickery lock bits). |
| `V4L2_INPUT_FORMAT` | The pixel format to ask for (`yuyv422` for nearly every stick). |
| `AUDIO_GAIN` | A mixer `control=dB` to set before each recording, for sticks where it works. |

## Measuring a new stick

Record a test take (a tape with cuts and speech is ideal) with `PROFILE=generic` and `MODE=master`, then measure from the master.

**Field order.** Let ffmpeg's `idet` filter count. Most sticks send top field first, whatever the driver claims.

```bash
ffmpeg -i take_master.mkv -t 300 -vf idet -an -f null - 2>&1 | grep 'Multi frame detection'
```

Use the larger of TFF and BFF.

**Crop.** Grab a frame and look at the edges: most sticks leave a few black columns at the sides, and every
VHS/Hi8 deck makes a ragged strip in the last 6–10 lines (head switching, which old TVs hid).

```bash
ffmpeg -ss 60 -i take_master.mkv -frames:v 1 frame.png
```

**Audio delay.** Find a hard camera cut with sound on it (a door, a clap, a scene change mid-word), and compare
where the picture changes with where the sound does, in an editor or with `ffplay -vf showinfo`. Camera cuts on the
USB-Live2 E1 put the sound 70–100 ms early, hence its 80 ms.

**Picture lock.** With the deck off, check whether the driver claims a signal anyway:

```bash
v4l2-ctl -d /dev/video0 --get-input      # "ok" with nothing playing = the status is useless
v4l2-ctl -d /dev/video0 -l               # look for a lock control, e.g. lock_status
```

If the status lies and there's a lock control, set `SIGNAL_CONTROL` to it, and watch it during playback
(`watch -n 0.5 v4l2-ctl -d /dev/video0 -C lock_status`): if it drops out now and then on a steady picture, set
`SIGNAL_HOLD_SEC` to a few seconds.

Then send the profile upstream, with the stick's name, USB ID (`lsusb`) and what you tested it with.

## Hauppauge USB-Live2 Rev E1 (2040:c220)

Linux supports the original USB-Live2 (2040:c200) but not the newer Rev E1: with the stock em28xx driver only its
sound works. The community patches in [usblive2-e1-linux7](https://github.com/NursePandaz/usblive2-e1-linux7) add it
to the kernel's em28xx driver; build and install them with DKMS following that project's instructions, so the driver
is rebuilt after kernel updates. If a kernel update breaks the build, the stock driver loads and videocap's page
shows no capture device.

What the `usblive2-e1` profile handles:
- the driver reports every input as "ok", so videocap reads the decoder's `lock_status` instead, holding a lock for
  4 s because the bit drops out on rough playback;
- the driver labels frames plain "interlaced" (bottom first, by the V4L2 spec), but the stick sends top field first;
- the driver starts in PAL on composite and rejects ffmpeg's `-framerate`, so videocap always sets the standard
  explicitly and leaves the frame rate to it;
- its "Line" capture volume does nothing to the level, and each change upsets the input for a few seconds, so it's
  left alone. For more headroom on hot tapes, use inline RCA attenuators.

Cabling note from the field: a camcorder with a single 3.5 mm AV jack and a generic 3.5 mm-to-RCA cable often has
the plug colors wrong (the cable's yellow plug carrying left audio). Plugged in by color, one channel is just buzz,
and the damage report says "left channel nearly silent". Check with headphones before the first take.
