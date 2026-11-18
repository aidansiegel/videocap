"""The collection: an optional list of items (tapes, discs, anything you capture) in the order you'll capture them,
file names, and library .nfo files.

An item list is CSV, tab-separated cells pasted from a spreadsheet, or JSON, with columns id, label, title, format,
aspect, date and notes (all optional, and spreadsheet names like "Tape #", "Name" and "Description" work too), or
just one title per line. `id` is whatever you write on the item or its box (B01-T07, 0042, ...); `label` is what's
written on it; `title` is the library title (default: the label); `aspect` is 16:9 for a widescreen (anamorphic)
item. Without a list or a name typed on the page, captures are numbered capture001, capture002, ...
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


# what people call each column, in spreadsheets they already have
COLUMNS = {
    "id": ("id", "no", "no.", "number", "num", "#", "item", "tape", "tape #", "tape no", "code", "box"),
    "label": ("label", "written on it", "label text"),
    "title": ("title", "name"),
    "format": ("format", "type", "media", "medium"),
    "aspect": ("aspect", "shape", "aspect ratio"),
    "date": ("date", "year", "when", "recorded"),
    "notes": ("notes", "note", "description", "desc", "comments", "comment", "contents", "who"),
}
HEADER = {name: key for key, names in COLUMNS.items() for name in names}


def make_item(r):
    """A clean item from whatever fields are given (id, label, title, format, aspect, date, notes), or None when it has
    neither an ID nor a title. `file` is the start of every file name of its captures."""
    r = {k: str(v if v is not None else "").strip() for k, v in r.items() if k}
    tid, label = r.get("id", ""), r.get("label", "")
    title = r.get("title") or label or tid
    if not title:
        return None
    name = "%s - %s" % (tid, title) if tid and title != tid else title
    return {"id": tid, "label": label, "title": title, "format": r.get("format", ""),
            "aspect": aspect(r.get("aspect")), "date": r.get("date", ""), "notes": r.get("notes", ""),
            "file": safe_name(r.get("file") or name)}


def parse(text):
    """Items from text: a JSON list, CSV or tab-separated cells pasted from a spreadsheet with a header row (column
    names as in COLUMNS), or, with no header row, one item per line: its title, or with tabs, ID, title, format,
    notes. A list with no IDs at all is numbered 001, 002, ...; in one with IDs, a row without one is skipped."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    if text.startswith("["):
        rows = [{HEADER.get(str(k).strip().lower(), str(k).strip().lower()): v for k, v in r.items()}
                for r in json.loads(text) if isinstance(r, dict)]
    else:
        lines = text.splitlines()
        tabs = "\t" in lines[0]
        cells = list(csv.reader(lines, delimiter="\t" if tabs else ","))
        head = [HEADER.get(c.strip().lower()) for c in cells[0]]
        if any(head):
            rows = [{k: v for k, v in zip(head, row) if k} for row in cells[1:]]
        elif tabs:
            rows = [dict(zip(("id", "title", "format", "notes"), row)) for row in cells]
        else:
            rows = [{"title": line} for line in lines]
    width = max(3, len(str(len(rows))))
    number = not any(str(r.get("id") or "").strip() for r in rows)  # a list with no IDs at all is numbered
    items, seen = [], set()
    for n, r in enumerate(rows, 1):
        r = dict(r)
        if number and any(str(v or "").strip() for v in r.values()):
            r["id"] = str(n).zfill(width)
        elif not str(r.get("id") or "").strip():
            continue  # a row without an ID in a list that has them
        t = make_item(r)
        if t and t["id"] not in seen:
            seen.add(t["id"])
            items.append(t)
    return items


def load(path):
    """The item list in a file (see parse)."""
    if not path:
        return []
    with open(path, encoding="utf-8-sig") as f:
        return parse(f.read())


def nfo(item, captured, by="", collection=""):
    """A Kodi/Jellyfin/Emby movie .nfo: the title, the item ID as the sort title, and a short description."""
    e = lambda v: html.escape(str(v), quote=False)
    when = datetime.strptime(captured, "%Y-%m-%d").strftime("%B %Y")
    bits = []
    if item.get("notes"):
        bits.append(item["notes"].rstrip(".") + ".")
    if item.get("date"):
        bits.append("Recorded %s." % item["date"])
    if item.get("id"):
        bits.append("Item %s." % item["id"])
    if item.get("label") and item["label"] != item["title"]:
        bits.append("Label: \u201c%s\u201d." % item["label"])
    if item.get("format"):
        bits.append("%s." % item["format"])
    bits.append("Digitized%s in %s." % (" by " + by if by else "", when))
    year = re.search(r"\b(19|20)\d\d\b", item.get("date") or "")
    tags = [collection] if collection else []
    return ('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<movie>\n'
            "  <title>%s</title>\n  <sorttitle>%s</sorttitle>\n  <plot>%s</plot>\n%s%s</movie>\n"
            % (e(item["title"]), e(item.get("id") or item["title"]), e(" ".join(bits)),
               "  <year>%s</year>\n" % year.group(0) if year else "",
               "".join("  <tag>%s</tag>\n" % e(t) for t in tags)))
