#!/bin/sh
# Builds BlenderMod (runtime support for maps made with the Blender map tools) into the mod folder in the game folder
# (HOOKSHOT_GAME_DIR, or the one the mod manager uses; BLENDER_MOD_DIR overrides the mod folder):
#   mods/065-BlenderMod/layer/linux/libVkLayer_hookshot_blender_mod.so  + VkLayer_hookshot_blender_mod.json
#   mods/065-BlenderMod/layer/windows/VkLayer_hookshot_blender_mod.dll  + VkLayer_hookshot_blender_mod.json
# Needs g++/gcc and the mingw-w64 cross compilers (posix threads). Usage: plugins/blender_mod/build.sh [linux|windows]
# Sources besides src/: plugins/common/src, Vulkan-Headers and zstd's decoder (git submodule update --init)
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
if [ -z "${BLENDER_MOD_DIR:-}" ]; then
    GAME=${HOOKSHOT_GAME_DIR:-$(python3 "$ROOT/hookshot-mods" gamedir)}
    BLENDER_MOD_DIR=$GAME/mods/065-BlenderMod
fi
MOD=$BLENDER_MOD_DIR
COMMON=$ROOT/plugins/common/src
ZSTD=$ROOT/third_party/zstd/lib
SRCS="$HERE/src/layer.cpp $HERE/src/blendermod.cpp
      $COMMON/hook.cpp $COMMON/platform.cpp $COMMON/gamefiles.cpp $COMMON/vfs.cpp $COMMON/byml.cpp $COMMON/rstb.cpp"
CSRCS="$ZSTD/common/entropy_common.c $ZSTD/common/error_private.c $ZSTD/common/fse_decompress.c
       $ZSTD/common/xxhash.c $ZSTD/common/zstd_common.c $ZSTD/decompress/huf_decompress.c
       $ZSTD/decompress/zstd_ddict.c $ZSTD/decompress/zstd_decompress.c $ZSTD/decompress/zstd_decompress_block.c"
CFLAGS="-O2 -DZSTD_DISABLE_ASM -DZSTD_LEGACY_SUPPORT=0 -I$ZSTD -I$ZSTD/common"
for f in "$ZSTD/zstd.h" "$ROOT/vulkan_headers_src/include/vulkan/vk_layer.h"; do
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
FLAGS="-O2 -std=c++20 -DVK_NO_PROTOTYPES -I$ROOT/vulkan_headers_src/include -I$HERE/src -I$COMMON -I$ZSTD"
NAME=VkLayer_hookshot_blender_mod
manifest() {   # $1 library path relative to the manifest
    cat <<JSON
{
    "file_format_version": "1.2.0",
    "layer": {
        "name": "VK_LAYER_HOOKSHOT_blender_mod",
        "type": "GLOBAL",
        "library_path": "$1",
        "api_version": "1.3.0",
        "implementation_version": "1",
        "description": "Hookshot mod: BlenderMod (Blender map tools runtime)",
        "functions": {
            "vkNegotiateLoaderLayerInterfaceVersion": "BlenderMod_NegotiateLoaderLayerInterfaceVersion",
            "vkGetInstanceProcAddr": "BlenderMod_GetInstanceProcAddr",
            "vkGetDeviceProcAddr": "BlenderMod_GetDeviceProcAddr"
        }
    }
}
JSON
}
what=${1:-all}
mkdir -p "$MOD/blender"
cp "$HERE/mod/info.json" "$MOD/info.json"
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
