#!/usr/bin/env bash
set -e

# This source checkout (the mod manager finds the game folder itself)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Launch GUI mod manager by default, or pass through CLI arguments
if [ $# -eq 0 ]; then
    exec python3 "$SCRIPT_DIR/hookshot-mods" gui
else
    exec python3 "$SCRIPT_DIR/hookshot-mods" "$@"
fi
