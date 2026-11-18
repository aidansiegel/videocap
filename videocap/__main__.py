"""python3 -m videocap [--conf FILE]: run the capture station (as root, usually from systemd)."""
import argparse
import os

from . import __version__


def main():
    ap = argparse.ArgumentParser(prog="videocap", description="Unattended analog video capture station.")
    ap.add_argument("--conf", help="settings file (default /etc/videocap/videocap.conf, or $VIDEOCAP_CONF)")
    ap.add_argument("--check", action="store_true", help="print the settings in effect and what's plugged in, then exit")
    ap.add_argument("--version", action="version", version="videocap " + __version__)
    a = ap.parse_args()
    if a.conf:
        os.environ["VIDEOCAP_CONF"] = a.conf
    from . import config, hardware  # after VIDEOCAP_CONF is set
    if a.check:
        conf = config.load(a.conf)
        for k in sorted(conf):
            print("%s=%s" % (k, conf[k]))
        for k in config.unknown_keys(conf):
            print("# unknown setting: %s" % k)
        dev = hardware.find_video(conf)
        print("# capture device:", dev and "%s (%s)" % (dev[2], dev[0]) or "none found")
        print("# audio:", (dev and hardware.find_audio(conf, dev[1])) or "none found")
        return
    os.makedirs(config.RUN, exist_ok=True)
    from .app import App
    App(a.conf).run()


if __name__ == "__main__":
    main()
