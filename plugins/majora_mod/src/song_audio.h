// The added songs' own replay sounds, served in place of their host sounds' streams (see song_audio.cpp).
#pragma once
#include <cstdint>
#include <string>

namespace songs { struct Song; }

namespace songaudio {
void Start();                              // registers the stream overrides and builds the sounds on a worker thread
void Asked(uint32_t type, uint64_t kind);  // the melody sound for a song was asked for: its host's next stream open is ours
float Seconds(uint32_t type);              // the song's replay length in seconds; 0 while its host's sound would play
std::string HostStream(const songs::Song& s);   // the host's stream name ("Ocarina_ZeldaLullaby", "Ocarina_F")
}  // namespace songaudio
