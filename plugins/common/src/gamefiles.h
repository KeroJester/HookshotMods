// Reading the player's own game files from inside the game (nothing of the game ships with the mod):
//   - the file the game would load for a RomFS path: a mod's copy (Hookshot's mod index) or the game's own
//   - .zs files: zstd, with the game's dictionaries (Pack/ZsDic.pack.zs)
//   - SARC archives (.blarc, .pack): where each file's bytes are
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace gamefiles {

using Bytes = std::vector<uint8_t>;

// Finds Hookshot's mod index and RomFS folder (host symbols). False: game files cannot be read.
bool Init();

// Host path of the file the game reads for a RomFS path ("UI/Tex/Icon/Item_Bow.bntx.zs"), or "" if none
std::string RomfsPath(const std::string& rel);

// The game's RomFS folder (no mod's files), "" if unknown
std::string RomfsDir();

bool ReadFile(const std::string& hostPath, Bytes& out);

// Decompresses a .zs file (dictionary chosen by the frame's dictionary ID). False on error.
bool Unzstd(const Bytes& in, Bytes& out);
// A zstd frame that stores data as it is (raw blocks): any zstd decoder reads it, no dictionary needed
Bytes RawZstd(const uint8_t* data, size_t size);

struct SarcEntry {
    std::string name;
    size_t offset, size;      // within the archive
};
std::vector<SarcEntry> SarcList(const Bytes& sarc);
// Replaces one file of a SARC archive with data of any size: the header, file table and names stay, the data area is
// laid out again in the same order, each file keeping its alignment (the largest power of two, up to 0x1000, that its
// old start was on). False when the file is not in the archive.
bool SarcReplace(Bytes& sarc, const std::string& name, const Bytes& data);

}  // namespace gamefiles
