#pragma once
#include <string>

namespace majora {
// modDir: the mod folder (mods/<name>). Starts every part of the mod.
bool Init(const std::string& modDir);
}
