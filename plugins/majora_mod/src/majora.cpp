// MajoraMod: Majora's Mask content for the OoT remake running under Hookshot or Longshot. Everything is done in the
// game (its own presentation, menus and files); nothing is drawn over it. The research behind each part is in
// RESEARCH_NOTES.md (section 79 onwards).
#include "majora.h"
#include "gamefiles.h"
#include "platform.h"
#include "rstb.h"
#include "mm_songs.h"
#include "songbook.h"
#include "icons.h"
#include "vfs.h"
#include <cstdlib>
#include <string>

namespace majora {
using namespace plat;

bool Init(const std::string& modDir) {
    Log("[majora] MajoraMod in %s\n", modDir.c_str());
    if (!gamefiles::Init()) { Log("[majora] the game's files cannot be read: off\n"); return false; }
    song::Start();                                         // (registers its changed files before the server starts)
    songbook::Start();
    icons::Start();
    // (development: MAJORA_DEV_RSTB="romfs/path=size;..." raises size-table entries for files under test)
    if (const char* dev = getenv("MAJORA_DEV_RSTB")) {
        std::string all = dev;
        for (size_t at = 0; at < all.size();) {
            size_t end = all.find(';', at);
            if (end == std::string::npos) end = all.size();
            const std::string item = all.substr(at, end - at);
            const size_t eq = item.find('=');
            if (eq != std::string::npos) {
                rstb::Require(item.substr(0, eq), (uint32_t)strtoul(item.c_str() + eq + 1, nullptr, 0));
                Log("[majora] dev size: %s\n", item.c_str());
            }
            at = end + 1;
        }
    }
    rstb::Start(modDir);                                   // (the grown files' sizes; see rstb.h)
    vfs::Start();
    return true;
}

}  // namespace majora
