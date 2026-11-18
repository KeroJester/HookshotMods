// BlenderMod: what maps made with the Blender map tools need from the running game, without MajoraMod's content.
// Maps add files the game's resource size table does not know (new models, textures, packs) and grow shared tables
// (the map list, start positions, actor tables). Every enabled mod may list the sizes it needs in <mod>/rstb.txt
// ("romfs/path=size" per line); BlenderMod raises those entries, adds missing ones, and serves / writes the table
// (common/src/rstb.cpp). The game reads ONE size table and the last mod in load order wins whole files, so when an
// enabled MajoraMod 0.4.2 or later does the same job, BlenderMod steps aside and removes its own table copy.
// RESEARCH_NOTES.md §89.
#include "blendermod.h"
#include "gamefiles.h"
#include "platform.h"
#include "rstb.h"
#include "vfs.h"
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>

namespace blendermod {
using namespace plat;
using gamefiles::Bytes;
namespace fs = std::filesystem;

static const char* kTableRel = "romfs/System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs";

// "0.4.2" >= "0.4.2"? (from the mod's info.json "version")
static bool VersionAtLeast(const std::string& info, int a, int b, int c) {
    const size_t k = info.find("\"version\"");
    if (k == std::string::npos) return false;
    const size_t q = info.find('"', info.find(':', k) + 1);
    int x = 0, y = 0, z = 0;
    if (q == std::string::npos || sscanf(info.c_str() + q + 1, "%d.%d.%d", &x, &y, &z) < 2) return false;
    return x != a ? x > a : y != b ? y > b : z >= c;
}

static bool Enabled(const fs::path& p) {
    const std::string n = p.filename().string();
    return !n.empty() && n[0] != '_' && n[0] != '-';
}

bool Init(const std::string& modDir) {
    Log("[blender] BlenderMod in %s\n", modDir.c_str());
    const fs::path mods = fs::path(modDir).parent_path();
    try {
        for (const auto& e : fs::directory_iterator(mods)) {
            const std::string n = e.path().filename().string();
            if (!e.is_directory() || !Enabled(e.path()) || n.size() < 9 || n.compare(n.size() - 9, 9, "MajoraMod")) continue;
            Bytes info;
            if (gamefiles::ReadFile((e.path() / "info.json").string(), info) &&
                VersionAtLeast(std::string(info.begin(), info.end()), 0, 4, 2)) {
                const fs::path mine = fs::path(modDir) / kTableRel;
                std::error_code ec;
                if (fs::remove(mine, ec)) Log("[blender] removed this mod's size table copy\n");
                Log("[blender] %s handles the size table (it reads every mod's rstb.txt): nothing to do\n", n.c_str());
                return true;
            }
        }
    } catch (const std::exception& ex) {
        Log("[blender] mods folder not readable: %s\n", ex.what());
    }
    if (!gamefiles::Init()) { Log("[blender] the game's files cannot be read: off\n"); return false; }
    int n = 0;
    try {
        for (const auto& e : fs::directory_iterator(mods)) {
            if (!e.is_directory() || !Enabled(e.path())) continue;
            Bytes f;
            if (!gamefiles::ReadFile((e.path() / "rstb.txt").string(), f)) continue;
            std::string all(f.begin(), f.end());
            for (size_t at = 0; at < all.size();) {
                size_t end = all.find_first_of("\r\n", at);
                if (end == std::string::npos) end = all.size();
                const std::string item = all.substr(at, end - at);
                const size_t eq = item.find('=');
                if (eq != std::string::npos) {
                    rstb::Require(item.substr(0, eq), (uint32_t)strtoul(item.c_str() + eq + 1, nullptr, 0));
                    n++;
                }
                at = end + 1;
            }
            Log("[blender] size requests from %s\n", e.path().filename().string().c_str());
        }
    } catch (const std::exception& ex) {
        Log("[blender] mods folder not readable: %s\n", ex.what());
    }
    Log("[blender] %d size requests\n", n);
    rstb::Start(modDir);
    vfs::Start();
    return true;
}

}  // namespace blendermod
