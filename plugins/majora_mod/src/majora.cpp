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
using gamefiles::Bytes;

bool Init(const std::string& modDir) {
    Log("[majora] MajoraMod in %s\n", modDir.c_str());
    if (!gamefiles::Init()) { Log("[majora] the game's files cannot be read: off\n"); return false; }
    song::Start();                                         // (registers its changed files before the server starts)
    {   // (development) <mod>/majora/dev_flow.txt: "type=value" lines for song::SetFlowAnswer
        Bytes f;
        const std::string s(1, PathSep());
        if (gamefiles::ReadFile(modDir + s + "majora" + s + "dev_flow.txt", f)) {
            std::string all(f.begin(), f.end());
            for (size_t at = 0; at < all.size();) {
                size_t end = all.find_first_of("\r\n", at);
                if (end == std::string::npos) end = all.size();
                const std::string line = all.substr(at, end - at);
                const size_t eq = line.find('=');
                if (eq != std::string::npos)
                    song::SetFlowAnswer((uint32_t)strtoul(line.c_str(), nullptr, 0), (uint32_t)strtoul(line.c_str() + eq + 1, nullptr, 0));
                at = end + 1;
            }
        }
    }
    songbook::Start();
    icons::Start();
    // (development: MAJORA_DEV_RSTB="romfs/path=size;..." raises size-table entries for files under test)
    // (or the same list in <mod>/majora/dev_rstb.txt, one "path=size" per line, for games started from the manager)
    std::string devList;
    if (const char* dev = getenv("MAJORA_DEV_RSTB")) devList = dev;
    {
        Bytes f;
        const std::string s(1, PathSep());
        if (gamefiles::ReadFile(modDir + s + "majora" + s + "dev_rstb.txt", f))
            for (uint8_t c : f) devList += (c == '\n' || c == '\r') ? ';' : (char)c;
    }
    if (!devList.empty()) {
        std::string all = devList;
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
