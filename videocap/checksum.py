"""md5sum-compatible checksums, so they can be checked anywhere with `md5sum -c` (or `md5 -r` on a Mac).

Run as a separate low-priority process (`python3 -m videocap.checksum FILE...`, in the files' folder) so reading a
100 GB master back never competes with a capture.
"""
import hashlib
import sys


def md5(path, block=8 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def main(names):
    if names[:1] == ["--"]:  # file names after it may start with "-"
        names = names[1:]
    for n in names:
        print("%s  %s" % (md5(n), n), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
