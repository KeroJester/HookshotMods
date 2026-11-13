#pragma once
#include <cstdint>
#include <string>

namespace itemctl {
// modDir: the mod folder (mods/<name>), holding item_controls/ (items, icons, slots). Installs the game hooks.
bool Init(const std::string& modDir);
bool HudVisible();   // whether the six boxes are to be drawn this frame
void DrawHud();      // inside an ImGui frame
uint32_t SlotItem(int slot);             // the GameData hash of the item in slot 0..5 (LB+X, LB+Y, LB+B, RB+X, ...)
std::string SlotIcon(int slot);          // its icon's name in UI/Tex/Icon ("" for none); a bottle: what is in it
}
