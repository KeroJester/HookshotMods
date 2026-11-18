#!/bin/sh
# Builds the Blender add-on zip (dist/blender/ootr_map_tools-<version>.zip) with BlenderMod (plugins/blender_mod)
# freshly built for Linux and Windows inside it (ootr_map_tools/blendermod/, not in git).
#   blender/build_addon.sh [blender executable]
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
BLENDER=${1:-${BLENDER:-blender}}
OUT=$HERE/ootr_map_tools/blendermod
rm -rf "$OUT"
BLENDER_MOD_DIR="$OUT" sh "$ROOT/plugins/blender_mod/build.sh"
strip "$OUT"/layer/linux/*.so
x86_64-w64-mingw32-strip "$OUT"/layer/windows/*.dll
rmdir "$OUT/blender" 2>/dev/null || true
find "$HERE/ootr_map_tools" -name __pycache__ -prune -exec rm -rf {} +
"$BLENDER" --command extension build --source-dir "$HERE/ootr_map_tools" --output-dir "$ROOT/dist/blender"
