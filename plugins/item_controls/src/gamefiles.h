// Reading the player's own game files from inside the game (nothing of the game ships with the mod):
//   - the file the game would load for a RomFS path: a mod's copy (Hookshot's mod index) or the game's own
//   - .zs files: zstd, with the game's dictionaries (Pack/ZsDic.pack.zs)
//   - SARC archives (.blarc, .pack): where each file's bytes are
//   - BNTX textures (ASTC 4x4, Switch 2 block-linear): an icon as RGBA
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

// A BNTX's first texture (ASTC) decoded and box-filtered to size x size RGBA. False if not decodable.
bool BntxIconRgba(const Bytes& bntx, int size, Bytes& rgba);

}  // namespace gamefiles
