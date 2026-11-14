// The added songs' own replay sounds: entries added to the game's ocarina sound archive (see song_audio.cpp).
#pragma once
#include <cstdint>
#include <string>

namespace songaudio {
void Start();                              // registers the changed BARS and the new stream files (built with the BARS)
void Asked(uint32_t type, uint64_t kind);  // the melody sound for a song was asked for (log)
std::string SoundName(uint32_t type);      // the song's own sound ("Ocarina_Majora17_"), or "" (not in this session)
}  // namespace songaudio
