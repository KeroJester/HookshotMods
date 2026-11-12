#!/usr/bin/env bash
set -e

# This source checkout (the overlay) and the game folder
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="${HOOKSHOT_GAME_DIR:-$(python3 "$HERE/hookshot-mods" gamedir)}"

# Always ensure overlay library is up-to-date
OVERLAY_LIB="$HERE/overlay/libhookshot_overlay.so"
(cd "$HERE/overlay" && make)

# Ensure Wayland and X11 display sockets are detected if running from standard shell
if [ -z "$WAYLAND_DISPLAY" ]; then
    if [ -e "/run/user/$(id -u)/wayland-1" ]; then
        export WAYLAND_DISPLAY="wayland-1"
    elif [ -e "/run/user/$(id -u)/wayland-0" ]; then
        export WAYLAND_DISPLAY="wayland-0"
    fi
fi
if [ -z "$DISPLAY" ]; then
    export DISPLAY=":0"
fi

echo "Launching Hookshot with Dev Playground Overlay..."
echo "Press [F2] anytime in-game to toggle the Mod Playground / Cheats Menu!"

export LD_PRELOAD="$OVERLAY_LIB${LD_PRELOAD:+:$LD_PRELOAD}"

cd "$GAME"
exec "$GAME/bin/hookshot" "$@"
