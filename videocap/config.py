"""Settings: built-in defaults, then a device profile, then the settings file (later wins).

The settings file is plain KEY=value lines with # comments (see videocap.conf.example). It is read again before each
recording, so most changes apply without a restart.
"""
import os
import re

CONF_PATH = os.environ.get("VIDEOCAP_CONF", "/etc/videocap/videocap.conf")
RUN = os.environ.get("VIDEOCAP_RUN", "/run/videocap")
STATE_DIR = os.environ.get("VIDEOCAP_STATE", "/var/lib/videocap")
PROFILE_DIRS = [os.path.join(os.path.dirname(CONF_PATH), "profiles"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")]

DEFAULTS = {
    # --- the station
    "STATION_NAME": "",            # names this station in reports and hooks; blank = this machine's host name
    "WEB_PORT": "80",
    "NETWORK_NAME": "videocap",    # the page is at http://videocap.local/ (videocap-2.local if taken); blank = don't announce
    "LEDS": "auto",                # auto = use a Raspberry Pi's ACT/PWR LEDs if there are any; none = leave them alone
    "PAUSE_UNITS": "",             # systemd units frozen while recording (other heavy jobs on the same machine)

    # --- the capture device (a profile sets most of these for a known stick; see profiles/)
    "PROFILE": "generic",
    "STANDARD": "NTSC",            # NTSC or PAL
    "INPUT": "auto",               # auto (S-Video first, then composite), svideo, composite
    "VIDEO_DEVICE": "",            # blank = the first USB video capture device, e.g. /dev/video0
    "AUDIO_DEVICE": "",            # blank = the sound card on the same USB device, e.g. hw:CARD=Audio,DEV=0
    "V4L2_INPUT_FORMAT": "yuyv422",
    "FIELD_ORDER": "tff",          # which field comes first in time: tff or bff
    "MP4_CROP": "",                # left:right:top:bottom pixels cut from the MP4 only; blank = none
    "AUDIO_DELAY_MS": "0",         # sound held back this much beyond lining it up by the clock
    "AUDIO_GAIN": "",              # mixer control=dB set before each recording; blank = leave the device alone
    "SIGNAL_CONTROL": "",          # a V4L2 control that reports picture lock (e.g. lock_status); blank = input status
    "SIGNAL_HOLD_SEC": "0",        # a lock seen within this many seconds still counts (for flickery lock bits)

    # --- what to record
    "ASPECT": "4:3",               # picture shape unless the item list or the page says otherwise: 4:3 or 16:9
    "MODE": "both",                # both = lossless master + MP4; master = lossless only; access = MP4 only
    "FOLDER": "captures",          # folder on the capture disk
    "CARD_LABEL": "",              # record only to a USB partition with this label (any case); blank = the largest
    "DISK_OWNER": "1000:1000",     # uid:gid owning files on exFAT/FAT/NTFS disks; blank = root
    "MASTER_MIN_FREE_GB": "80",    # below this, no room for a master (about 25-40 GB per hour of video): see NO_ROOM
    "NO_ROOM": "refuse",           # refuse = don't record; mp4 = record the MP4 only
    "MIN_FREE_GB": "1",            # stop before the disk fills completely
    "MAX_MINUTES": "180",          # longest take; 0 = no limit (a live source)

    # --- starting and stopping by itself
    "STANDBY_SEC": "300",          # record while waiting for play, starting over this often; 0 = off
    "STANDBY_HOURS": "3",          # stand by this long after the last take (or start-up), then wait for Start
    "PREPLAY_SEC": "180",          # after Start with no picture yet: this long to press play
    "STOP_AFTER_FROZEN_MIN": "3",  # the source played, then the picture froze (blue screen) this long: stop; 0 = never
    "GIVE_UP_NO_MOTION_MIN": "30", # the picture never moved: stop; 0 = never
    "SIGNAL_LOST_SEC": "60",       # no picture lock this long (source off, or a long gap on a tape): stop
    "MIN_TAKE_MINUTES": "5",       # a shorter take is a false start: held, and the next item stays the same
    "PREVIEW_UDP": "",             # host:port for a live MPEG-TS preview (VLC: udp://@:port); blank = none

    # --- the collection
    "ITEM_LIST": "",               # CSV or JSON list of items in order (id,label[,title,format]); blank = capture001, ...
    "COLLECTION": "",              # collection name, used as a tag in library .nfo files
    "DIGITIZED_BY": "",            # "Digitized by <name> in <month year>" in library descriptions; blank = no name
    "NFO": "1",                    # 1 = write a Kodi/Jellyfin/Emby .nfo beside each MP4 of a listed item

    # --- where captures go (the web page picks one of the destinations; see below)
    "DEFAULT_DESTINATION": "",     # used until one is picked on the page; blank = the keys below if set, else disk only
    "DESTINATION_LABEL": "Settings file",  # what the page calls the destination set by the keys below, if any

    # --- after each take
    "MASTERS_DIR": "",             # move each master here (checked byte for byte) to free the capture disk
    "MASTERS_SPARE_GB": "10",      # always leave this much free on MASTERS_DIR
    "MP4_REMOTE": "",              # rclone destination for MP4s (remote:path or a local path); blank = none
    "MASTER_REMOTE": "",           # rclone destination for masters, logs, reports, checksums; blank = none
    "MASTER_REVIEW": "1",          # 1 = masters wait until approved on the web page; 0 = send straight away
    "REPLACE_EXISTING": "0",       # 1 = a recapture replaces the MP4 already at MP4_REMOTE (old one moved aside)
    "REMOTE_MIN_FREE_GB": "0",     # skip uploads that would leave less than this free (if the remote reports it)
    "UPLOAD_BWLIMIT": "",          # e.g. 4M; blank = no limit
    "RCLONE_CONFIG": "",           # rclone.conf to use; blank = rclone's own default
    "HOOK": "",                    # program run after each take (finished, approved, discarded); see hooks/

    # --- testing
    "TEST_SOURCE": "",             # an ffmpeg lavfi graph used instead of a capture device
    "TEST_CARD_DIR": "",           # a folder used instead of a USB disk
}


def parse(path):
    """KEY=value lines from a file ({} if it's missing). Quotes around a value are dropped; # starts a comment."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def profile_path(name):
    """A profile is a path, or a name looked up in /etc/videocap/profiles then the built-in profiles."""
    if not name:
        return None
    if os.sep in name or name.endswith(".conf"):
        return name if os.path.exists(name) else None
    for d in PROFILE_DIRS:
        p = os.path.join(d, name + ".conf")
        if os.path.exists(p):
            return p
    return None


def load(path=None):
    path = path or CONF_PATH
    user = parse(path)
    conf = dict(DEFAULTS)
    name = user.get("PROFILE", DEFAULTS["PROFILE"])
    p = profile_path(name)
    if p:
        conf.update(parse(p))
    conf.update(user)
    conf["PROFILE"] = name if p else "%s (not found)" % name
    return conf


def unknown_keys(conf):
    return sorted(k for k in set(conf) - set(DEFAULTS) if not DEST_KEY.match(k))


# --- destinations ----------------------------------------------------------------
# A destination is a named set of the keys in DEST_KEYS, picked on the web page:
#
#   DEST.aws.LABEL=AWS
#   DEST.aws.MASTER_REMOTE=aws:my-bucket/masters
#
# A destination starts from nothing (no masters disk, no uploads) and sets what it needs; the other keys in DEST_KEYS
# fall back to the settings file's own. "This disk only" (id disk) is always there, and is the default when the
# settings file sends captures nowhere itself.
DEST_KEYS = ("MASTERS_DIR", "MP4_REMOTE", "MASTER_REMOTE", "MASTER_REVIEW", "REPLACE_EXISTING", "REMOTE_MIN_FREE_GB",
             "UPLOAD_BWLIMIT")
WHERE = ("MASTERS_DIR", "MP4_REMOTE", "MASTER_REMOTE")
DEST_KEY = re.compile(r"^DEST\.([A-Za-z0-9_-]+)\.([A-Z0-9_]+)$")


def destinations(conf):
    """[{"id", "label", "set": {key: value}}], in the order they appear in the settings file, after the built-ins."""
    out = [{"id": "disk", "label": "This disk only", "set": {k: "" for k in WHERE}}]
    if any(conf.get(k) for k in WHERE):  # the settings file's own MASTERS_DIR / MP4_REMOTE / MASTER_REMOTE
        out.append({"id": "settings", "label": conf.get("DESTINATION_LABEL") or "Settings file", "set": {}})
    named = {}
    for k, v in conf.items():
        m = DEST_KEY.match(k)
        if not m or m.group(1) in ("disk", "settings"):
            continue
        d = named.setdefault(m.group(1), {"id": m.group(1), "label": m.group(1), "set": {k2: "" for k2 in WHERE}})
        if m.group(2) == "LABEL":
            d["label"] = v or m.group(1)
        elif m.group(2) in DEST_KEYS:
            d["set"][m.group(2)] = v
    return out + list(named.values())


def apply_destination(conf, dest_id):
    """The settings with a destination applied. No pick yet (or one since removed from the settings file): then
    DEFAULT_DESTINATION, then the settings file's own MASTERS_DIR / MP4_REMOTE / MASTER_REMOTE if any, then disk only."""
    dests = {d["id"]: d for d in destinations(conf)}
    d = dests.get(dest_id) or dests.get(conf.get("DEFAULT_DESTINATION")) or dests.get("settings") or dests["disk"]
    out = dict(conf)
    out.update(d["set"])
    out["DESTINATION"], out["DESTINATION_NAME"] = d["id"], d["label"]
    return out
