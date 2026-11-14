// The songbook icons of the added songs (RESEARCH_NOTES.md §79n): the mod's own art (art/icons/*.png, generated into
// icons_data.cpp by art/make_icons.py), added to the UI's texture archive as OcarinaMelodyIcon_<type> (icon_tex.cpp).
#pragma once
#include <cstddef>
#include <cstdint>

namespace icons {
extern const int kSize;                  // width = height
extern const uint32_t kTypes[];          // the song type of each icon, in order
extern const size_t kCount, kRawSize;    // icons; bytes of linear RGBA (kCount x kSize x kSize x 4)
extern const size_t kZstdSize;
extern const uint8_t kZstd[];            // the RGBA, zstd-compressed

// Registers the changed texture archive (call before rstb::Start / vfs::Start)
void Start();
// The textures are in the archive the game loaded this session (the songbook may name them)
bool Served();
}  // namespace icons
