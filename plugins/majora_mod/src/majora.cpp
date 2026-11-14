// MajoraMod: Majora's Mask content for the OoT remake running under Hookshot or Longshot. Everything is done in the
// game (its own presentation, menus and files); nothing is drawn over it. The research behind each part is in
// RESEARCH_NOTES.md (section 79 onwards).
#include "majora.h"
#include "gamefiles.h"
#include "platform.h"
#include "song_healing.h"
#include "vfs.h"

namespace majora {
using namespace plat;

bool Init(const std::string& modDir) {
    Log("[majora] MajoraMod in %s\n", modDir.c_str());
    if (!gamefiles::Init()) { Log("[majora] the game's files cannot be read: off\n"); return false; }
    song::Start();                                         // (registers its changed files before the server starts)
    vfs::Start();
    return true;
}

}  // namespace majora
