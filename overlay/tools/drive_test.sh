#!/bin/sh
# Headless scripted session: boot, load the latest save, then run the steps.
#   overlay/tools/drive_test.sh <run_dir> [secs] [shot_every] -- step...
# Steps: "sleep:<s>"  wait;  "touch:<file>"  create a file (e.g. the overlay teleport trigger);  "write:<file>=<text>"  write a file;  "tuner:<cmd>"  overlay tuner command (e.g. "snap <path>");  "clean:"  delete the screenshots so far;  "mark:<text>"  write "[mark] <text> <time>" to overlay.log;  anything else is one
# HOS_INPUT_LIVE line (e.g. "0:LUP/3500,1.0:A/1500"). The game is stopped after the last step.
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)            # this source checkout
R=$1; shift
SECS=300; EVERY=30
[ "${1:-}" != "--" ] && { SECS=$1; shift; }
[ "${1:-}" != "--" ] && { EVERY=$1; shift; }
[ "${1:-}" = "--" ] && shift
rm -rf "$R"
"$REPO/overlay/tools/run_headless.sh" "$R" "$SECS" "$EVERY" &
shots() { ls "$R/shots" 2>/dev/null | wc -l; }
need=$((1920 / EVERY))
until [ "$(shots)" -ge "$need" ] || grep -q "^exit" "$R/stdout.txt" 2>/dev/null; do sleep 1; done
echo "0:A,3:A,6:A" >> "$R/live.txt"
n=$(shots); need=$((1680 / EVERY))
until [ "$(shots)" -ge $((n + need)) ] || grep -q "^exit" "$R/stdout.txt"; do sleep 1; done
sleep 2
for step in "$@"; do
    case "$step" in
        sleep:*) sleep "${step#sleep:}" ;;
        touch:*) : > "${step#touch:}" ;;
        write:*) w=${step#write:}; printf '%s' "${w#*=}" > "${w%%=*}" ;;
        tuner:*) echo "${step#tuner:}" >> "$R/tuner.txt" ;;
        clean:*) find "$R/shots" -name "*.png" -delete ;;
        mark:*) echo "[mark] ${step#mark:} $(date +%s.%N) shot=$(ls -t "$R/shots" | head -1)" >> "$REPO/overlay/overlay.log" ;;
        *) echo "$step" >> "$R/live.txt" ;;
    esac
done
pkill -f -- "--user $R/user" || true; sleep 3   # only this run's game
echo "drive_test: done ($(shots) shots)"
