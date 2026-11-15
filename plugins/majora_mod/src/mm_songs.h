#pragma once
#include <cstdint>
#include "platform.h"
namespace song {
// Majora's Mask's songs as new song types (RESEARCH_NOTES.md §79, §79k): score book, matcher, flows, names and text,
// replay sound, effect. Registers its changed files; call before rstb::Start() and vfs::Start().
bool Start();
// The added songs' score objects ready (each with its type), once the score book is loaded. The songbook calls it
// when it opens (its notes are found by type).
bool EnsureScores(plat::GuestCtx* c);
// (development) after its replay starts, an added song answers `value` to the flows' melody query instead of 13
// (e.g. 15=11: the Song of Soaring takes the Prelude of Light's warp path). Read from <mod>/majora/dev_flow.txt.
void SetFlowAnswer(uint32_t type, uint32_t value);
}
