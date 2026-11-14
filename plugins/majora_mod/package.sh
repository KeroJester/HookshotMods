#!/bin/sh
# Packages MajoraMod for players: dist/060-MajoraMod/ (both layers + the mod's files) and dist/MajoraMod.zip.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
OUT=$ROOT/dist/060-MajoraMod
rm -rf "$OUT"
MAJORA_MOD_DIR=$OUT sh "$HERE/build.sh" all
rm -f "$ROOT/dist/MajoraMod.zip"
(cd "$ROOT/dist" && python3 -m zipfile -c MajoraMod.zip 060-MajoraMod)
echo "package: $ROOT/dist/MajoraMod.zip"
