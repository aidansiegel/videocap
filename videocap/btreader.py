"""Collect a station's status from its Bluetooth name: python3 -m videocap.btreader

The station broadcasts its status a line at a time as its Bluetooth name (V0 ... V9, a line every 6 seconds; see
beacon.py). This listens, prints each line as it's seen, and once it has seen the whole round prints the report.
No pairing or network needed. Needs the bleak library (pip install bleak) and, on a Mac, Bluetooth permission for the
app it runs in (System Settings > Privacy & Security > Bluetooth).
"""
import argparse
import asyncio
import re
import sys

LINE = re.compile(r"^V(\d) (.*)$")


class Collector:
    """Lines from the station, until a round is complete: every line from V0 up to the highest number seen, with the
    numbering having started over at least once. A broadcast-only station takes a new random Bluetooth address each
    time its name changes, so lines are put together by their numbers, not their address: one station at a time."""

    def __init__(self):
        self.stations = {}   # address -> {"lines": {n: text}, "max": n, "wrapped": bool, "last": n}

    def add(self, address, name):
        m = LINE.match(name or "")
        if not m:
            return None
        n, text = int(m.group(1)), m.group(2)
        st = self.stations.setdefault(address, {"lines": {}, "max": -1, "wrapped": False, "last": None})
        new = st["lines"].get(n) != text
        if st["last"] is not None and n < st["last"]:
            st["wrapped"] = True
        st["last"] = n
        st["lines"][n] = text
        st["max"] = max(st["max"], n)
        return new

    def complete(self, address):
        st = self.stations.get(address)
        return bool(st and st["wrapped"] and all(i in st["lines"] for i in range(st["max"] + 1)))

    def report(self, address):
        st = self.stations[address]
        return [st["lines"][i] for i in sorted(st["lines"])]


async def listen(seconds, say):
    try:
        from bleak import BleakScanner
    except ImportError:
        say("needs the bleak library: pip install bleak")
        return None
    col = Collector()
    done = asyncio.Event()
    found = {}

    def seen(dev, adv):
        name = adv.local_name or dev.name or ""
        new = col.add("station", name)
        if new is None:
            return
        if not found:
            found["station"] = True
            say("found a station (signal %d dBm)" % adv.rssi)
        if new:
            say("  %s  (%d dBm)" % (name, adv.rssi))
        if col.complete("station"):
            found["done"] = "station"
            done.set()

    async with BleakScanner(seen):
        try:
            await asyncio.wait_for(done.wait(), seconds)
        except asyncio.TimeoutError:
            pass
    addr = found.get("done") or next((a for a in col.stations), None)
    return col.report(addr) if addr else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m videocap.btreader", description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=75, help="give up after this long (a full round takes up to 60)")
    args = ap.parse_args(argv)
    say = lambda m: print(m, file=sys.stderr, flush=True)
    say("listening for a station's Bluetooth status for up to %d s..." % args.seconds)
    lines = asyncio.run(listen(args.seconds, say))
    if not lines:
        say("no station heard (is it on, with BLUETOOTH=auto, within about 10 m?)")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
