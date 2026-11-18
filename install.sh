#!/usr/bin/env bash
# Install or update videocap on Raspberry Pi OS, Debian or Ubuntu, as root:
#
#   sudo ./install.sh [--no-apt] [--no-start]
#
# Installs the packages it needs (ffmpeg, rclone, v4l-utils, alsa-utils), copies videocap to /opt/videocap, puts the
# example settings at /etc/videocap/videocap.conf if there are none yet (an existing file is never touched), and
# installs and starts the systemd service. Refuses to restart a station that is recording.
set -euo pipefail

apt=yes start=yes
for a in "$@"; do
    case "$a" in
        --no-apt) apt=no ;;
        --no-start) start=no ;;
        *) echo "usage: sudo $0 [--no-apt] [--no-start]" >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run as root: sudo $0" >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$here/videocap/app.py" ]] || { echo "run this from the videocap folder" >&2; exit 1; }

if systemctl is-active --quiet videocap; then
    port="$(sed -n 's/^WEB_PORT=\([0-9]*\).*/\1/p' /etc/videocap/videocap.conf 2>/dev/null | tail -1)"
    if status="$(curl -fsS -m 5 "http://127.0.0.1:${port:-80}/status.json" 2>/dev/null)"; then
        if python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("recording") or d["state"]=="finishing" else 1)' <<<"$status"; then
            echo "videocap is recording. Run this again once it has finished (restarting would end it)." >&2
            exit 1
        fi
    fi
fi

if [[ $apt == yes ]]; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 ffmpeg rclone v4l-utils alsa-utils curl
fi
for t in python3 ffmpeg ffprobe rclone v4l2-ctl cmp lsblk findmnt; do
    command -v "$t" >/dev/null || echo "warning: $t not found" >&2
done

install -d -m 755 /opt/videocap/videocap/static /opt/videocap/videocap/profiles /opt/videocap/hooks /etc/videocap /etc/videocap/profiles
install -m 644 "$here"/videocap/*.py /opt/videocap/videocap/
install -m 644 "$here"/videocap/static/* /opt/videocap/videocap/static/
install -m 644 "$here"/videocap/profiles/*.conf /opt/videocap/videocap/profiles/
install -m 755 "$here"/hooks/*.sh /opt/videocap/hooks/
install -m 644 "$here/README.md" "$here/LICENSE" /opt/videocap/
if [[ ! -e /etc/videocap/videocap.conf ]]; then
    install -m 644 "$here/videocap.conf.example" /etc/videocap/videocap.conf
    echo "Settings: /etc/videocap/videocap.conf (edit it: at least STANDARD, PROFILE and CARD_LABEL)"
else
    echo "Kept your /etc/videocap/videocap.conf (the latest example is in $here/videocap.conf.example)"
fi
install -m 644 "$here/systemd/videocap.service" /etc/systemd/system/videocap.service
systemctl daemon-reload

if [[ $start == yes ]]; then
    systemctl enable videocap >/dev/null
    systemctl restart videocap
    sleep 2
    systemctl --no-pager --lines=5 status videocap || true
    echo "Web page: http://$(hostname).local/ (or this machine's address)"
else
    echo "Installed. Start it with: sudo systemctl enable --now videocap"
fi
