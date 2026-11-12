#!/bin/sh
# Builds the Item Controls Vulkan layer for Linux and Windows into the mod folder:
#   mods/050-ItemControls/layer/linux/libVkLayer_hookshot_item_controls.so  + VkLayer_hookshot_item_controls.json
#   mods/050-ItemControls/layer/windows/VkLayer_hookshot_item_controls.dll  + VkLayer_hookshot_item_controls.json
# Needs g++ and the mingw-w64 cross compiler (x86_64-w64-mingw32-g++). Usage: plugins/item_controls/build.sh [linux|windows]
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
MOD=${ITEMCTL_MOD_DIR:-$ROOT/mods/050-ItemControls}
IMGUI=$ROOT/imgui_src
SRCS="$HERE/src/layer.cpp $HERE/src/item_controls.cpp $HERE/src/hook.cpp $HERE/src/platform.cpp
      $IMGUI/imgui.cpp $IMGUI/imgui_draw.cpp $IMGUI/imgui_tables.cpp $IMGUI/imgui_widgets.cpp $IMGUI/backends/imgui_impl_vulkan.cpp"
FLAGS="-O2 -std=c++17 -DVK_NO_PROTOTYPES -DIMGUI_DISABLE_DEFAULT_SHELL_FUNCTIONS -I$ROOT/vulkan_headers_src/include -I$IMGUI -I$IMGUI/backends -I$HERE/src"
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
if [ "$what" = all ] || [ "$what" = linux ]; then
    mkdir -p "$MOD/layer/linux"
    g++ $FLAGS -fPIC -shared -fvisibility=hidden -Wl,--no-undefined -o "$MOD/layer/linux/lib$NAME.so" $SRCS -ldl -lpthread
    manifest "./lib$NAME.so" > "$MOD/layer/linux/$NAME.json"
    echo "linux: $MOD/layer/linux/lib$NAME.so"
fi
if [ "$what" = all ] || [ "$what" = windows ]; then
    mkdir -p "$MOD/layer/windows"
    x86_64-w64-mingw32-g++ $FLAGS -shared -static -static-libgcc -static-libstdc++ -o "$MOD/layer/windows/$NAME.dll" $SRCS
    manifest ".\\\\$NAME.dll" > "$MOD/layer/windows/$NAME.json"
    echo "windows: $MOD/layer/windows/$NAME.dll"
fi
