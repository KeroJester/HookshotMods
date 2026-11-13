#!/bin/sh
# Builds the Item Controls mod (its files from mod/ plus the Vulkan layer for Linux and Windows) into the mod folder in
# the game folder
# (HOOKSHOT_GAME_DIR, or the one the mod manager uses; ITEMCTL_MOD_DIR overrides the mod folder):
#   mods/050-ItemControls/layer/linux/libVkLayer_hookshot_item_controls.so  + VkLayer_hookshot_item_controls.json
#   mods/050-ItemControls/layer/windows/VkLayer_hookshot_item_controls.dll  + VkLayer_hookshot_item_controls.json
# Needs g++/gcc and the mingw-w64 cross compilers (posix threads). Usage: plugins/item_controls/build.sh [linux|windows]
# Sources besides src/: imgui, Vulkan-Headers, zstd's decoder (third_party/zstd) and suyu's ASTC decoder from the
# Hookshot source (reference/hookshot-src): git submodule update --init
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
if [ -z "${ITEMCTL_MOD_DIR:-}" ]; then
    GAME=${HOOKSHOT_GAME_DIR:-$(python3 "$ROOT/hookshot-mods" gamedir)}
    ITEMCTL_MOD_DIR=$GAME/mods/050-ItemControls
fi
MOD=$ITEMCTL_MOD_DIR
IMGUI=$ROOT/imgui_src
SUYU=$ROOT/reference/hookshot-src/gpu/third_party/suyu
ZSTD=$ROOT/third_party/zstd/lib
SRCS="$HERE/src/layer.cpp $HERE/src/item_controls.cpp $HERE/src/hook.cpp $HERE/src/platform.cpp
      $HERE/src/gamefiles.cpp $HERE/src/vfs.cpp $HERE/src/item_files.cpp $HERE/src/research.cpp $SUYU/video_core/textures/astc.cpp
      $IMGUI/imgui.cpp $IMGUI/imgui_draw.cpp $IMGUI/imgui_tables.cpp $IMGUI/imgui_widgets.cpp $IMGUI/backends/imgui_impl_vulkan.cpp"
CSRCS="$ZSTD/common/entropy_common.c $ZSTD/common/error_private.c $ZSTD/common/fse_decompress.c
       $ZSTD/common/xxhash.c $ZSTD/common/zstd_common.c $ZSTD/decompress/huf_decompress.c
       $ZSTD/decompress/zstd_ddict.c $ZSTD/decompress/zstd_decompress.c $ZSTD/decompress/zstd_decompress_block.c"
CFLAGS="-O2 -DZSTD_DISABLE_ASM -DZSTD_LEGACY_SUPPORT=0 -I$ZSTD -I$ZSTD/common"
for f in "$SUYU/video_core/textures/astc.cpp" "$ZSTD/zstd.h" "$IMGUI/imgui.cpp"; do
    [ -f "$f" ] || { echo "missing $f: run git submodule update --init" >&2; exit 1; }
done
OBJ=$(mktemp -d)
trap 'rm -rf "$OBJ"' EXIT
cobjs() {   # $1 C compiler, $2 extra flags: zstd's decoder as objects (C, not C++)
    objs=""
    for c in $CSRCS; do
        o="$OBJ/$(basename "$c" .c)-$1.o"
        $1 $CFLAGS $2 -c "$c" -o "$o"
        objs="$objs $o"
    done
    echo "$objs"
}
FLAGS="-O2 -std=c++20 -DVK_NO_PROTOTYPES -DIMGUI_DISABLE_DEFAULT_SHELL_FUNCTIONS -I$ROOT/vulkan_headers_src/include -I$IMGUI -I$IMGUI/backends -I$HERE/src -I$HERE/shim -I$SUYU -I$ZSTD"
NAME=VkLayer_hookshot_item_controls
manifest() {   # $1 library path relative to the manifest
    cat <<JSON
{
    "file_format_version": "1.2.0",
    "layer": {
        "name": "VK_LAYER_HOOKSHOT_item_controls",
        "type": "GLOBAL",
        "library_path": "$1",
        "api_version": "1.3.0",
        "implementation_version": "1",
        "description": "Hookshot mod: Item Controls (N64-style item buttons)",
        "functions": {
            "vkNegotiateLoaderLayerInterfaceVersion": "HookshotItemCtl_NegotiateLoaderLayerInterfaceVersion",
            "vkGetInstanceProcAddr": "HookshotItemCtl_GetInstanceProcAddr",
            "vkGetDeviceProcAddr": "HookshotItemCtl_GetDeviceProcAddr"
        }
    }
}
JSON
}
what=${1:-all}
# the mod's own files (info.json, item/aim/icon lists); the player's slots.txt is never touched
mkdir -p "$MOD/item_controls"
cp "$HERE/mod/info.json" "$MOD/info.json"
cp "$HERE/mod/item_controls/items.txt" "$HERE/mod/item_controls/aim.txt" "$HERE/mod/item_controls/icons.txt" "$MOD/item_controls/"
if [ "$what" = all ] || [ "$what" = linux ]; then
    mkdir -p "$MOD/layer/linux"
    g++ $FLAGS -fPIC -shared -fvisibility=hidden -Wl,--no-undefined -o "$MOD/layer/linux/lib$NAME.so" $SRCS $(cobjs gcc -fPIC) -ldl -lpthread
    manifest "./lib$NAME.so" > "$MOD/layer/linux/$NAME.json"
    echo "linux: $MOD/layer/linux/lib$NAME.so"
fi
if [ "$what" = all ] || [ "$what" = windows ]; then
    mkdir -p "$MOD/layer/windows"
    x86_64-w64-mingw32-g++ $FLAGS -shared -static -static-libgcc -static-libstdc++ -o "$MOD/layer/windows/$NAME.dll" $SRCS $(cobjs x86_64-w64-mingw32-gcc "")
    manifest ".\\\\$NAME.dll" > "$MOD/layer/windows/$NAME.json"
    echo "windows: $MOD/layer/windows/$NAME.dll"
fi
