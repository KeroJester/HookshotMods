#pragma once
#include "platform.h"
namespace song {
// Majora's Mask's songs as new song types (RESEARCH_NOTES.md §79, §79k): score book, matcher, flows, names and text,
// replay sound, effect. Registers its changed files; call before rstb::Start() and vfs::Start().
bool Start();
// The added songs' score objects ready (each with its type), once the score book is loaded. The songbook calls it
// when it opens (its notes are found by type).
bool EnsureScores(plat::GuestCtx* c);
}
