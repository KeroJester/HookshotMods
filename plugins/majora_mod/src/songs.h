// Majora's Mask's songs as new song types of the game (RESEARCH_NOTES.md §79, §79k). The game's types are 0 Zelda's
// Lullaby .. 12 Scarecrow's Song, 13 = none; the mod adds 14.. in the order of kSongs.
#pragma once
#include <cstdint>
#include <vector>

namespace songs {

// MIDI keys of the notes in a replay (the game has a recording of each: Sound/Resource/Stream/Ocarina_<pitch>.bwav)
const char* PitchName(int key);   // "D", "Asharp", "HighD", ... or null

struct Note { float start; int key, velocity; float length; };   // seconds, MIDI key, 0..127, seconds

// A title as written per language: plain text, with "{kanji|reading}" for Japanese reading aids (furigana).
// grammar: the 4 parameter bytes of the title's grammar tag (group 201, tag 0) that the game's sentences use for the
// article, gender and case ("You played the ..."); null = no tag. italic13: Italian titles also carry tag 201.13.
struct Text { const char* lang; const char* title; const uint8_t* grammar; const char* caption; };

struct Song {
    uint32_t type;                // the mod's song type (14..)
    const char* id;               // the type name ("HealingSong"): its murmur3 is the score book's MelodyType
    const char* english;          // for the log
    std::vector<int> notes;       // what the player plays: MIDI keys 62 D, 65 F, 69 A, 71 B, 74 HighD
    std::vector<Note> replay;     // Majora's Mask 3D's replay of the song (its NA_BGM_OCA_* sequence): its own sound,
                                  // whose first notes.size() notes are the staff's (song_audio.cpp)
    uint32_t effectFrom;          // the native song whose effect it shows (§79i)
    std::vector<Text> texts;
};

const std::vector<Song>& All();
const Song* ByType(uint32_t type);   // null for native types
constexpr uint32_t kFirst = 14;      // the first added type (12 and 13 are placeholders in the score book)

}  // namespace songs
