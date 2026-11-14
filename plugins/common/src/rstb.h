// The game's resource size table (System/Resource/ResourceSizeTable...rsizetable.zs): the memory the resource system
// sets aside for each file. A changed file that grows needs its entry raised, or the game fails to load it (an event
// flow that fails to load ends its event at once).
//
// The table is read once, early in the boot. Hookshot reads it after the layer starts, so a changed copy served
// through vfs works at once. Longshot reads it before (RESEARCH_NOTES.md §79j), but loads a resource size table that
// a mod folder holds (<mod>/romfs/System/Resource/...) before the game starts. So Start() does both: it serves the
// raised table through vfs and writes it into the mod's own folder, built from the game folder's table (never shipped;
// made from the player's files). A table written for the first time takes effect at the next launch; Applied() says
// whether the raised sizes are in effect in this session, and files that need them should wait for it.
#pragma once
#include <cstdint>
#include <string>

namespace rstb {
// romfsRel without ".zs" (as the table keys files), e.g. "Event/EventFlow/Dm_SY_Ocarina.bfevfl"
void Require(const std::string& romfsRel, uint32_t minSize);
// Raises an entry by `extra` bytes over the game's own value (for files whose vanilla size differs by language)
void Grow(const std::string& romfsRel, uint32_t extra);
// After every Require / Grow, before vfs::Start(). modDir: the mod's folder (the table is written under it).
void Start(const std::string& modDir);
// The raised sizes are in effect in this session: the game read the vfs copy, or the table it loaded at boot already
// had them.
bool Applied();
uint32_t Crc32(const std::string& s);
}
