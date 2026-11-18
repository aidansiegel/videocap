#!/usr/bin/env bash
# Try videocap with no capture hardware: ffmpeg's test pattern stands in for a VCR, and folders stand in for the
# capture disk, the masters disk, your media library and your archive. Runs on Linux or a Mac; needs python3,
# ffmpeg and rclone.
#
#   ./demo.sh [FOLDER] [PORT]      (default: ./videocap-demo, port 8080), then open http://localhost:PORT/
#
# Each "tape" plays for a minute, then freezes like a deck's blue screen, and the station stops by itself 15 s later.
# Then: the master moves to masters/ and is checked, the MP4 and .nfo go to library/, and the master waits for your
# OK on the page before going to archive/. Press REC on the page for the next item, and try "Send captures to".
# Ctrl-C to stop.
# The folder holds about 1.5 GB per take; delete it when you're done.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dir="$(mkdir -p "${1:-videocap-demo}" && cd "${1:-videocap-demo}" && pwd)"
port="${2:-8080}"
for t in python3 ffmpeg ffprobe rclone; do
    command -v "$t" >/dev/null || { echo "needs $t" >&2; exit 1; }
done
mkdir -p "$dir/capture-disk" "$dir/masters" "$dir/library" "$dir/archive"

[[ -e "$dir/items.csv" ]] || cat > "$dir/items.csv" <<'EOF'
id,label,title,format,aspect
B01-T01,"Xmas '93 / Florida",Christmas 1993 and Florida,Hi8,
B01-T02,Summer 94,,Hi8,
B01-T03,"Grandpa's 75th, Oct '94",Grandpa's 75th birthday,VHS,
DV-01,Beach week,,Digital8,16:9
EOF

cat > "$dir/videocap.conf" <<EOF
STATION_NAME=demo-station
WEB_PORT=$port
LEDS=none
TEST_SOURCE=testsrc2=s=720x480:r=30000/1001:d=60,tpad=stop=-1:stop_mode=clone
TEST_CARD_DIR=$dir/capture-disk
ITEM_LIST=$dir/items.csv
COLLECTION=Demo collection
DIGITIZED_BY=videocap demo
MASTER_REVIEW=1
# Destinations to pick from on the page ("This disk only" is always there too)
DEFAULT_DESTINATION=everything
DEST.everything.LABEL=Library and archive
DEST.everything.MASTERS_DIR=$dir/masters
DEST.everything.MP4_REMOTE=$dir/library
DEST.everything.MASTER_REMOTE=$dir/archive/
DEST.library.LABEL=Library only (MP4s)
DEST.library.MP4_REMOTE=$dir/library
STOP_AFTER_FROZEN_MIN=0.25
MIN_TAKE_MINUTES=0.5
MASTER_MIN_FREE_GB=3
MASTERS_SPARE_GB=1
MIN_FREE_GB=1
EOF

echo "videocap demo: http://localhost:$port/   (files in $dir; Ctrl-C to stop)"
cd "$dir"
VIDEOCAP_RUN="$dir/.run" VIDEOCAP_STATE="$dir/.state" PYTHONPATH="$here" exec python3 -m videocap --conf "$dir/videocap.conf"
