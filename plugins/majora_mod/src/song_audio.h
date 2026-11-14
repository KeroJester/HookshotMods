// The Song of Healing's own replay sound, served in place of the host song's stream (see song_audio.cpp).
#pragma once
#include <cstdint>

namespace songaudio {
void Start();               // registers the stream override and builds the sound on a worker thread
void Asked(uint64_t kind);  // the melody sound for type 14 was asked for: the host's next stream open is ours
float Seconds();            // the replay's length in seconds; 0 while the stand-in (host) sound would play
}  // namespace songaudio
