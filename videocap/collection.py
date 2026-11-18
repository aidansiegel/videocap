"""The collection: an optional list of items (tapes, discs, anything you capture) in the order you'll capture them,
file names, and library .nfo files.

An item list is CSV with a header row (id,label and optionally title, format), or JSON (a list of objects with the
same keys). `id` is whatever you write on the item or its box (B01-T07, 0042, ...); `label` is what's written on it; `title`
is the library title (default: the label); `aspect` is 16:9 for a widescreen (anamorphic) item, blank for the default. Without a list, takes are numbered capture001, capture002, ...
"""
import csv
import html
import json
import re
from datetime import datetime

BAD_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')


def aspect(text):
    """'16:9' or '4:3' from what people write (16x9, wide, anamorphic, 4/3, ...); '' when it says nothing."""
    t = str(text or "").strip().lower()
    if t in ("16:9", "16x9", "16/9", "wide", "widescreen", "anamorphic", "1.78", "1.78:1"):
        return "16:9"
    if t in ("4:3", "4x3", "4/3", "standard", "1.33", "1.33:1"):
        return "4:3"
    return ""


def safe_name(text, limit=150):
    """A file name that works on exFAT, FAT32, NTFS, ext4 and SMB shares."""
    text = text.replace("/", "-").replace("\u2192", "to").replace("\u2190", "from")
    text = BAD_CHARS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:limit].rstrip(" .") or "untitled"


def load(path):
    """The item list, as dicts with id, label, title, format and file (the base of every file name)."""
    if not path:
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = json.load(f) if path.lower().endswith(".json") else list(csv.DictReader(f))
    items, seen = [], set()
    for r in rows:
        r = {k.strip().lower(): (v or "").strip() if isinstance(v, str) else v for k, v in r.items() if k}
        tid = str(r.get("id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        label = str(r.get("label") or "")
        title = str(r.get("title") or label or tid)
        items.append({"id": tid, "label": label, "title": title, "format": str(r.get("format") or ""),
                      "aspect": aspect(r.get("aspect")),
                      "file": safe_name(r.get("file") or ("%s - %s" % (tid, title) if title != tid else tid))})
    return items


def nfo(item, captured, by="", collection=""):
    """A Kodi/Jellyfin/Emby movie .nfo: the title, the item ID as the sort title, and a short description."""
    e = lambda v: html.escape(str(v), quote=False)
    when = datetime.strptime(captured, "%Y-%m-%d").strftime("%B %Y")
    plot = "Item %s." % item["id"]
    if item["label"] and item["label"] != item["title"]:
        plot += " Label: \u201c%s\u201d." % item["label"]
    if item.get("format"):
        plot += " %s." % item["format"]
    plot += " Digitized%s in %s." % (" by " + by if by else "", when)
    tags = [collection] if collection else []
    return ('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<movie>\n'
            "  <title>%s</title>\n  <sorttitle>%s</sorttitle>\n  <plot>%s</plot>\n%s</movie>\n"
            % (e(item["title"]), e(item["id"]), e(plot), "".join("  <tag>%s</tag>\n" % e(t) for t in tags)))
