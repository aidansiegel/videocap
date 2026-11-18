#!/bin/sh
# Example HOOK: keep a running inventory of takes in a CSV beside the masters.
#
#   HOOK=/opt/videocap/hooks/inventory-csv.sh
#
# videocap runs the hook with the event as its argument (finished, approved or discarded) and these variables:
#   VIDEOCAP_NAME     the take's name (the base of its file names)
#   VIDEOCAP_ID, VIDEOCAP_LABEL, VIDEOCAP_TITLE   from the item list (blank for an unlisted take)
#   VIDEOCAP_MASTERS  the master file(s), separated by ":" (where they are now)
#   VIDEOCAP_MP4, VIDEOCAP_LOG, VIDEOCAP_REPORT   the MP4, the capture log and the JSON report on the capture disk
#   VIDEOCAP_DAMAGE   e.g. "12 frames missing (0.4 s), 0 sound overruns, 3 clipped moments"
#   VIDEOCAP_STATION  the station's name
# It runs at the lowest priority, possibly while the next take records: keep it light.
set -eu
out="${INVENTORY_CSV:-/var/lib/videocap/inventory.csv}"
[ -e "$out" ] || echo 'when,event,station,id,title,take,damage' > "$out"
q() { printf '"%s"' "$(printf '%s' "$1" | sed 's/"/""/g')"; }
printf '%s,%s,%s,%s,%s,%s,%s\n' "$(date -Iseconds)" "$1" "$(q "$VIDEOCAP_STATION")" "$(q "$VIDEOCAP_ID")" \
    "$(q "$VIDEOCAP_TITLE")" "$(q "$VIDEOCAP_NAME")" "$(q "$VIDEOCAP_DAMAGE")" >> "$out"
