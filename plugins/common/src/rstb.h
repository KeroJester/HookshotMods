// The game's resource size table (System/Resource/ResourceSizeTable...rsizetable.zs): the memory the resource system
// sets aside for each file. A changed file that grows needs its entry raised, or the game skips the file or stalls
// loading. Require() raises an entry through a changed copy of the table (vfs), so call it before vfs::Start().
#pragma once
#include <cstdint>
#include <string>

namespace rstb {
// romfsRel without ".zs" (as the table keys files), e.g. "Event/EventFlow/Dm_SY_Ocarina.bfevfl"
void Require(const std::string& romfsRel, uint32_t minSize);
// Raises an entry by `extra` bytes over the table's own value (for files whose vanilla size differs by language)
void Grow(const std::string& romfsRel, uint32_t extra);
uint32_t Crc32(const std::string& s);
}
