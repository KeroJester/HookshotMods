#!/bin/sh
# Packages the Item Controls mod for players: dist/050-ItemControls/ (both layers + the mod's files) and
# dist/bundled_mods/ItemControls.zip, the zip that ships inside Hookshot Mods and on the Releases page.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
OUT=$ROOT/dist/050-ItemControls
rm -rf "$OUT"
ITEMCTL_MOD_DIR=$OUT sh "$HERE/build.sh" all
mkdir -p "$ROOT/dist/bundled_mods"
rm -f "$ROOT/dist/bundled_mods/ItemControls.zip"
(cd "$ROOT/dist" && python3 -m zipfile -c bundled_mods/ItemControls.zip 050-ItemControls)
echo "package: $ROOT/dist/bundled_mods/ItemControls.zip"
