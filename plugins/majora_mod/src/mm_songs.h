#pragma once
namespace song {
// Majora's Mask's songs as new song types (RESEARCH_NOTES.md §79, §79k): score book, matcher, flows, names and text,
// replay sound, effect. Registers its changed files; call before rstb::Start() and vfs::Start().
bool Start();
}
