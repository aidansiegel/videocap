"""Settings changed on the web page: which ones may be, what each may be set to, and writing them into the settings
file in place (its comments and order kept, a backup beside it).

The page has no login (it's for your home network), so it can change only what's listed in FIELDS. Anything that
runs a program (HOOK), reads credentials (RCLONE_CONFIG) or picks devices by hand stays in the settings file.
"""
import glob
import os
import re
import subprocess
import threading

from . import config, hardware

# key: (kind, label). Kinds: a tuple of allowed values, "text", "name" (letters, digits, -), "path", "remote", "bool".
FIELDS = {
    "STANDARD": (("NTSC", "PAL"), "TV standard"),
    "PROFILE": ("profile", "Capture stick"),
    "INPUT": (("auto", "svideo", "composite"), "Input"),
    "CARD_LABEL": ("label", "Capture disk"),
    "ASPECT": (("4:3", "16:9"), "Picture shape"),
    "COLLECTION": ("text", "Collection name"),
    "DIGITIZED_BY": ("text", "Digitized by"),
    "MASTER_REVIEW": ("bool", "Review masters before they're sent"),
    "DEFAULT_DESTINATION": ("dest", "Where captures go"),
}
DEST_FIELDS = {"LABEL": "text", "MASTERS_DIR": "path", "MP4_REMOTE": "remote", "MASTER_REMOTE": "remote"}
# never somewhere a mistake could damage the system (the station runs as root)
SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var",
               "/private")  # (/private: where /etc and /var really are on a Mac)
SETUP_DONE = os.path.join(config.STATE_DIR, "setup-done")


def profiles():
    """[{"id", "label", "match"}]: the built-in profiles and any in /etc/videocap/profiles, each with its "# label:" (or
    first comment line) and its "# match:" pattern (what the stick calls itself), if any."""
    seen, out = set(), []
    for d in config.PROFILE_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "*.conf"))):
            pid = os.path.basename(p)[:-5]
            if pid in seen:
                continue
            seen.add(pid)
            label, match = pid, ""
            try:
                with open(p) as f:
                    for line in f:
                        if line.startswith("# match:"):
                            match = line.split(":", 1)[1].strip()
                        elif line.startswith("# label:"):
                            label = line.split(":", 1)[1].strip()
                        elif line.startswith("# ") and label == pid:
                            label = line[2:].strip().rstrip(".")
            except OSError:
                continue
            out.append({"id": pid, "label": label, "match": match})
    return out


def suggest_profile(card_name):
    """The profile whose "# match:" pattern fits what the capture stick calls itself; generic if none does."""
    for p in profiles():
        if p["match"] and card_name and re.search(p["match"], card_name, re.I):
            return p["id"]
    return "generic"


def disks():
    """USB partitions the station could record to: label, size and free space (when mounted)."""
    out = []
    for p in hardware.usb_partitions():
        free = None
        if p.get("mountpoint"):
            try:
                st = os.statvfs(p["mountpoint"])
                free = round(st.f_bavail * st.f_frsize / 1e9, 1)
            except OSError:
                pass
        out.append({"label": p.get("label") or "", "fstype": p.get("fstype"), "path": p.get("path"),
                    "size_gb": round(int(p.get("size") or 0) / 1e9), "free_gb": free})
    return out


def remotes(conf):
    """The rclone remotes set up on this machine (names without the colon)."""
    cmd = ["rclone", "listremotes"] + (["--config", conf["RCLONE_CONFIG"]] if conf.get("RCLONE_CONFIG") else [])
    return [r.rstrip(":") for r in hardware.sh(*cmd).split() if r.endswith(":")]


def timezones():
    return hardware.sh("timedatectl", "list-timezones", timeout=10).split()


def set_timezone(tz):
    if tz not in timezones():
        return "That isn't a time zone this machine knows."
    r = subprocess.run(["timedatectl", "set-timezone", tz], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stderr.strip() or None


def check(key, value, conf):
    """The value to store, or raises ValueError with a sentence saying what's wrong."""
    value = "" if value is None else str(value).strip()
    # one printable line, no #: anything else could add a line (a HOOK=, which runs as root) or be cut short
    if not value.isprintable() or "#" in value:
        raise ValueError("Keep it on one line, without # or special characters.")
    m = config.DEST_KEY.fullmatch(key)
    if m:
        if m.group(1) in ("disk", "settings"):
            raise ValueError("That name is taken by a built-in destination.")
        kind = DEST_FIELDS.get(m.group(2))
        if not kind:
            raise ValueError("%s can't be set from the page." % key)
    elif key in FIELDS:
        kind = FIELDS[key][0]
    else:
        raise ValueError("%s can't be set from the page; it stays in the settings file." % key)
    if value == "":
        return ""
    if isinstance(kind, tuple):
        if value not in kind:
            raise ValueError("Pick one of: %s." % ", ".join(kind))
    elif kind == "profile":
        if value not in {p["id"] for p in profiles()}:
            raise ValueError("There's no capture stick profile called %s." % value)
    elif kind == "dest":
        if value not in {d["id"] for d in config.destinations(conf)}:
            raise ValueError("There's no destination called %s." % value)
    elif kind == "bool":
        value = "1" if value in ("1", "true", "on", "yes") else "0"
    elif kind == "name":
        value = value.lower().removesuffix(".local")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", value):
            raise ValueError("Use letters, digits and dashes only, like videocap or den-vcr (no spaces).")
    elif kind in ("text", "label"):
        if len(value) > 100 or re.search(r"[\x00-\x1f#]", value):
            raise ValueError("Keep it under 100 characters, on one line, without #.")
    elif kind == "path":
        value = check_path(value)
    elif kind == "remote":
        if value.startswith("/"):
            value = check_path(value)
        else:
            name = value.split(":", 1)[0]
            if ":" not in value or name not in remotes(conf):
                raise ValueError("Use a folder on this machine (starting with /) or an rclone remote set up here, like "
                                 "nas:videos. Remotes here: %s." % (", ".join(remotes(conf)) or "none yet"))
    return value


def check_path(value):
    if not value.startswith("/") or ".." in value.split("/"):
        raise ValueError("Use a full path, starting with /, like /mnt/masters.")
    p = os.path.realpath("/" + os.path.normpath(value).lstrip("/"))
    if p == "/" or any(p == d or p.startswith(d + "/") for d in SYSTEM_DIRS):
        raise ValueError("Pick a folder on a data disk, like /mnt/masters, not a system folder.")
    if not os.path.isdir(p):
        raise ValueError("There's no folder %s on this machine. Plug in its disk, or create the folder first." % p)
    return p


_save_lock = threading.Lock()


def save(path, updates):
    with _save_lock:
        _save(path, updates)


def _save(path, updates):
    """Write KEY=value updates into the settings file: an existing (uncommented) line is changed where it is, a new
    one goes at the end, and "" removes a destination's line (other keys keep the line, empty). Atomic, with the
    previous file kept as <file>.bak."""
    try:
        with open(path) as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        lines = ["# videocap settings (see videocap.conf.example)"]
    done = set()
    out = []
    for line in lines:
        bare = line.split("#", 1)[0].strip()
        key = bare.split("=", 1)[0].strip() if "=" in bare else None
        if not line and out and not out[-1]:
            continue  # no runs of blank lines (and none from the file's final newline)
        if key in updates:
            if key in done:
                continue  # an earlier duplicate was already set: drop this one, so it can't win
            done.add(key)
            if updates[key] == "" and config.DEST_KEY.match(key):
                continue
            line = "%s=%s" % (key, updates[key])
        out.append(line)
    while out and not out[-1]:
        out.pop()
    new = [k for k in updates if k not in done and not (updates[k] == "" and config.DEST_KEY.match(k))]
    if new:
        if not any(line.startswith("# --- Set on the web page") for line in out):
            out += ["", "# --- Set on the web page"]
        out += ["%s=%s" % (k, updates[k]) for k in new]
    if os.path.exists(path):
        with open(path) as f:
            hardware.write_atomic(path + ".bak", f.read())
    hardware.write_atomic(path, "\n".join(out) + "\n")


def setup_done():
    return os.path.exists(SETUP_DONE)


def mark_setup_done():
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(SETUP_DONE, "w") as f:
        f.write("1\n")
