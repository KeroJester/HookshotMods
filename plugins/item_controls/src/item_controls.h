#pragma once
#include <string>

namespace itemctl {
// modDir: the mod folder (mods/<name>), holding item_controls/ (items, icons, slots). Installs the game hooks.
bool Init(const std::string& modDir);
bool HudVisible();   // whether the six boxes are to be drawn this frame
void DrawHud();      // inside an ImGui frame
}
