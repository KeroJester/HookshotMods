#!/bin/sh
# Headless test run of the game (no window): screenshots, file trace, scripted input, overlay.
#   overlay/tools/run_headless.sh <out_dir> [seconds] [shot_every_frames]
# In <out_dir>:
#   shots/      PNG every <shot_every> presents (RX_VK_SHOTS)
#   live.txt    append HOS_INPUT_LIVE presses, e.g.  echo "0:A,2:LUP/3000" >> live.txt
#   tuner.txt   append Param Tuner commands (scan / select 0 / set RollingMaxSpeed 20 / dump)
#   watch.txt   write up to 4 guest addresses (hex) to arm CPU data breakpoints (HOS_WATCH);
#               results land in watch.txt.out, then watch.txt is renamed watch.txt.done
#   user/       a COPY of the game folder's user/ (saves), so test runs never touch real saves
#   stdout.txt  the game's output; ends with "exit <code>"
# The overlay is preloaded, so its log (overlay/overlay.log) gets [tuner] lines.
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)            # this source checkout
GAME=${HOOKSHOT_GAME_DIR:-$(python3 "$REPO/hookshot-mods" gamedir)}  # the game folder
OUT=${1:?out dir}
SECS=${2:-180}
EVERY=${3:-60}
mkdir -p "$OUT/shots"
[ -d "$OUT/user" ] || cp -a "${HOS_TEST_USER:-$GAME/user}" "$OUT/user"   # HOS_TEST_USER: a fixed test profile
: > "$OUT/live.txt"
: > "$OUT/tuner.txt"
cd "$GAME"
eval "$(python3 "$REPO/hookshot-mods" env)"   # the enabled code mods' Vulkan layers, as the manager's Launch sets them
# Guard: screenshots go to /tmp (RAM-backed here). Stop the game if a run produces too many.
( while sleep 3; do
    n=$(ls "$OUT/shots" 2>/dev/null | wc -l); kb=$(du -sk "$OUT/shots" 2>/dev/null | cut -f1)
    if [ "${n:-0}" -gt "${HOS_SHOT_GUARD_FILES:-600}" ] || [ "${kb:-0}" -gt "${HOS_SHOT_GUARD_KB:-1500000}" ]; then
        echo "run_headless: screenshot guard tripped ($n files, ${kb} KB); stopping the game" >> "$OUT/stdout.txt"
        pkill -f -- "--user $OUT/user"; break
    fi
    grep -q "^exit done" "$OUT/stdout.txt" 2>/dev/null && break   # (not pgrep: other hookshot processes may exist)
  done ) &
HOS_HEADLESS=1 HOS_AUDIO=${HOS_AUDIO_RUN:-0} HOS_FS_TRACE=1 HOOKSHOT_NO_UPDATE_CHECK=1 \
HOS_INPUT_LIVE="$OUT/live.txt" HOOKSHOT_TUNER_CMD="$OUT/tuner.txt" HOS_WATCH="$OUT/watch.txt" \
RX_VK_SHOTS="$OUT/shots" RX_VK_SHOT_EVERY="$EVERY" \
LD_PRELOAD="$REPO/overlay/libhookshot_overlay.so" \
timeout "$SECS" ./bin/hookshot --user "$OUT/user" > "$OUT/stdout.txt" 2>&1 || true
echo "exit done" >> "$OUT/stdout.txt"
