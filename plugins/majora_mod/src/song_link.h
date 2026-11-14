// Which sound a melody plays: the added songs' BARS entries reached through SLink2 (see song_link.cpp).
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace songlink {
void Start(const std::vector<std::string>& names);   // registers the changed SLink2 file (with these sound names)
// In the SLink search for a melody sound of type 0 (searchArg: its first argument): Melody_ZeldaLullaby plays `name`
// until Restore(). False: the native sound plays.
bool Redirect(uint64_t searchArg, const std::string& name);
void Restore();
}  // namespace songlink
