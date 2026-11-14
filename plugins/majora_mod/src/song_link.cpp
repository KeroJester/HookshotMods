// Which sound a melody plays (RESEARCH_NOTES.md §79m, §79n). The melody sound f_8337b730 searches SLink2 for an asset
// named after the type: "Melody_<Type>" (the presentation), "ExampleMelody_<Type>" / "ScoreExampleMelody_<Type>"
// (the songbook's listening). Each asset names its sound in the ocarina BARS in the parameter after its own name in
// SLink2's binary (SLink2/slink2.Product.111.bslnk, XLNK version 0x22): a u32 whose top byte is 1 (a string) and whose
// low 24 bits are an offset into the name table (header +0x58, running to the end of the file). For Zelda's Lullaby:
// Ocarina_ZeldaLullaby (+0x29b4c), ExampleMelody_ZeldaLullaby (+0x29ad0, +0x2a08c).
//   - The file gets the added songs' sound names appended to its name table (unused by the game itself).
//   - While an added song's melody sound is made, those parameters in the loaded XLNK point at the song's own name,
//     so Zelda's Lullaby's assets (the type the call is given) play the song's BARS entry; they are put back right
//     after.
// The loaded XLNK is found once from the SLink search's first argument (pointers followed to its header).
#include "song_link.h"
#include "gamefiles.h"
#include "guest.h"
#include "platform.h"
#include "rstb.h"
#include "vfs.h"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <map>
#include <mutex>

namespace songlink {
using namespace plat;
using guest::D;
using guest::Q;
using gamefiles::Bytes;

constexpr const char* kFile = "SLink2/slink2.Product.111.bslnk";
// type 0's assets and the sounds they name
static const char* const kAssets[] = {"Melody_ZeldaLullaby", "ExampleMelody_ZeldaLullaby", "ScoreExampleMelody_ZeldaLullaby"};
static const char* const kSounds[] = {"Ocarina_ZeldaLullaby", "ExampleMelody_ZeldaLullaby"};
constexpr uint32_t kMagic = 0x4b4e4c58;                    // "XLNK"
constexpr uint32_t kExtra = 1024;

static std::vector<std::string> g_Want;                    // names to append
static std::map<std::string, uint32_t> g_Rel;              // name -> offset in the name table (patched file)
struct Param { uint32_t off, value; };
static std::vector<Param> g_Params;                        // the sound-name parameters (offset, game's value)
static uint32_t g_Size = 0;                                // the patched file's size
static std::atomic<bool> g_Ready{false};                   // the file was patched (g_Rel, g_Params final)
static uint64_t g_Base = 0;                                // the loaded XLNK (game thread)
static bool g_Searched = false;
static bool g_Changed = false;                             // the parameters point at an added song's name now

static uint32_t U32(const Bytes& b, size_t o) { uint32_t v; memcpy(&v, &b[o], 4); return v; }

static bool Patch(Bytes& file) {
    Bytes x;
    if (!gamefiles::Unzstd(file, x) || x.size() < 0x60 || U32(x, 0) != kMagic || U32(x, 8) != 0x22 || U32(x, 4) != x.size()) {
        Log("[link] %s: not the expected XLNK\n", kFile);
        return false;
    }
    const uint32_t names = U32(x, 0x58);
    if (names >= x.size()) return false;
    // the name table's offsets of each name (as an entry or as the tail of a longer one)
    auto offsets = [&](const char* name) {
        std::vector<uint32_t> out;
        const std::string n = std::string(name) + '\0';
        const std::string_view all((const char*)x.data(), x.size());
        for (size_t f = all.find(n, names); f != std::string::npos; f = all.find(n, f + 1)) out.push_back((uint32_t)(f - names));
        return out;
    };
    auto refers = [&](uint32_t v, const char* const* list, size_t n) {
        if ((v >> 24) != 1) return false;
        for (size_t i = 0; i < n; i++) for (uint32_t o : offsets(list[i])) if ((v & 0xffffff) == o) return true;
        return false;
    };
    std::vector<uint32_t> assetValues;
    for (const char* a : kAssets) for (uint32_t o : offsets(a)) assetValues.push_back(0x01000000u | o);
    g_Params.clear();
    for (size_t o = 0; o + 8 <= names; o += 4) {
        const uint32_t v = U32(x, o);
        if (std::find(assetValues.begin(), assetValues.end(), v) == assetValues.end()) continue;
        const uint32_t next = U32(x, o + 4);
        if (refers(next, kSounds, 2)) g_Params.push_back({(uint32_t)(o + 4), next});
    }
    if (g_Params.size() != 3) { Log("[link] %s: %zu sound parameters for Zelda's Lullaby's assets (3 expected)\n", kFile, g_Params.size()); return false; }
    for (const std::string& n : g_Want) {
        g_Rel[n] = (uint32_t)(x.size() - names);
        x.insert(x.end(), n.begin(), n.end());
        x.push_back(0);
    }
    while (x.size() % 4) x.push_back(0);
    if (x.size() - names >= (1u << 24)) return false;
    g_Size = (uint32_t)x.size();
    memcpy(&x[4], &g_Size, 4);
    file = gamefiles::RawZstd(x.data(), x.size());
    g_Ready = true;
    Log("[link] %s: %zu sound names added; Zelda's Lullaby's sound names at +%#x +%#x +%#x\n", kFile, g_Want.size(),
        g_Params[0].off, g_Params[1].off, g_Params[2].off);
    return true;
}

void Start(const std::vector<std::string>& names) {
    g_Want = names;
    vfs::Register(std::string(kFile) + ".zs", Patch, rstb::Applied);
    rstb::Grow(kFile, kExtra);
}

static bool IsXlnk(uint64_t p) {
    if (!p || (p & 3) || !GuestPtrOk(p) || !GuestPtrOk(p + 0x60) || D(p) != kMagic || D(p + 8) != 0x22 || D(p + 4) != g_Size) return false;
    for (const Param& q : g_Params) if (D(p + q.off) != q.value) return false;
    return true;
}

// Pointers from the search's argument, up to three deep, to the XLNK's header
static uint64_t FindXlnk(uint64_t p, int depth) {
    if (!p || !GuestPtrOk(p)) return 0;
    for (uint64_t o = 0; o < 0x100; o += 8) {
        const uint64_t q = Q(p + o);
        if (IsXlnk(q)) return q;
    }
    if (depth == 0) return 0;
    for (uint64_t o = 0; o < 0x100; o += 8) {
        const uint64_t q = Q(p + o);
        if (q && q != p && !(q & 7))
            if (uint64_t r = FindXlnk(q, depth - 1)) return r;
    }
    return 0;
}

bool Redirect(uint64_t searchArg, const std::string& name) {
    if (!g_Ready) return false;
    auto it = g_Rel.find(name);
    if (it == g_Rel.end()) return false;
    if (!g_Base && !g_Searched) {
        g_Searched = true;
        g_Base = FindXlnk(searchArg, 3);
        Log(g_Base ? "[link] the loaded XLNK is at %#llx\n" : "[link] the loaded XLNK was not found (from %#llx): the songs keep their host sounds\n",
            (unsigned long long)(g_Base ? g_Base : searchArg));
    }
    if (!g_Base || !IsXlnk(g_Base)) return false;
    for (const Param& q : g_Params) GWrite<uint32_t>(g_Base + q.off, 0x01000000u | it->second);
    g_Changed = true;
    return true;
}

void Restore() {
    if (g_Changed) for (const Param& q : g_Params) GWrite<uint32_t>(g_Base + q.off, q.value);
    g_Changed = false;
}

}  // namespace songlink
