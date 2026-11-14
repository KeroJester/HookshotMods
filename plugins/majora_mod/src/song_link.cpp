// Which sound a melody plays (RESEARCH_NOTES.md §79m). The melody sound f_8337b730 searches SLink2 for "Melody_" +
// the type's name; that asset names its sound in the ocarina BARS through one parameter of SLink2's binary
// (SLink2/slink2.Product.111.bslnk, XLNK version 0x22): a u32 whose top byte is 1 (a string) and whose low 24 bits are
// an offset into the name table (header +0x58, running to the end of the file). Melody_ZeldaLullaby's is the only
// reference to "Ocarina_ZeldaLullaby".
//   - The file gets the added songs' sound names appended to its name table (unused by the game itself).
//   - While an added song's melody sound is made, that parameter in the loaded XLNK points at the song's own name, so
//     Melody_ZeldaLullaby (the type the call is given) plays the song's BARS entry; it is put back right after.
// The loaded XLNK is found once from the SLink search's first argument (pointers followed to its header).
#include "song_link.h"
#include "gamefiles.h"
#include "guest.h"
#include "platform.h"
#include "rstb.h"
#include "vfs.h"
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
constexpr const char* kHostName = "Ocarina_ZeldaLullaby";   // the sound of type 0, whose parameter is redirected
constexpr uint32_t kMagic = 0x4b4e4c58;                    // "XLNK"
constexpr uint32_t kExtra = 1024;

static std::vector<std::string> g_Want;                    // names to append
static std::map<std::string, uint32_t> g_Rel;              // name -> offset in the name table (patched file)
static uint32_t g_ParamOff = 0, g_HostValue = 0, g_Size = 0;   // the parameter's offset and value; the patched file's size
static std::atomic<bool> g_Ready{false};                   // the file was patched (g_Rel, g_ParamOff final)
static uint64_t g_Base = 0;                                // the loaded XLNK (game thread)
static bool g_Searched = false;
static uint64_t g_Restore = 0;                             // where the parameter was changed (0: nothing to put back)

static uint32_t U32(const Bytes& b, size_t o) { uint32_t v; memcpy(&v, &b[o], 4); return v; }

static bool Patch(Bytes& file) {
    Bytes x;
    if (!gamefiles::Unzstd(file, x) || x.size() < 0x60 || U32(x, 0) != kMagic || U32(x, 8) != 0x22 || U32(x, 4) != x.size()) {
        Log("[link] %s: not the expected XLNK\n", kFile);
        return false;
    }
    const uint32_t names = U32(x, 0x58);
    if (names >= x.size()) return false;
    const std::string host = std::string(kHostName) + '\0';
    size_t at = 0;
    for (size_t o = names; o + host.size() <= x.size(); ) {         // the name as a whole entry of the table
        const size_t f = std::string_view((const char*)x.data(), x.size()).find(host, o);
        if (f == std::string::npos) break;
        if (f == names || x[f - 1] == 0) { at = f; break; }
        o = f + 1;
    }
    if (!at) { Log("[link] %s not in the name table\n", kHostName); return false; }
    const uint32_t value = 0x01000000u | (uint32_t)(at - names);
    int refs = 0;
    for (size_t o = 0; o + 4 <= names; o += 4)
        if (U32(x, o) == value) g_ParamOff = (uint32_t)o, refs++;
    if (refs != 1) { Log("[link] %s: %d references (one expected)\n", kHostName, refs); return false; }
    g_HostValue = value;
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
    Log("[link] %s: %zu sound names added; Melody_ZeldaLullaby's sound name at +%#x\n", kFile, g_Want.size(), g_ParamOff);
    return true;
}

void Start(const std::vector<std::string>& names) {
    g_Want = names;
    vfs::Register(std::string(kFile) + ".zs", Patch, rstb::Applied);
    rstb::Grow(kFile, kExtra);
}

static bool IsXlnk(uint64_t p) {
    return p && !(p & 3) && GuestPtrOk(p) && GuestPtrOk(p + 0x60) && D(p) == kMagic && D(p + 8) == 0x22 && D(p + 4) == g_Size &&
           D(p + g_ParamOff) == g_HostValue;
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
    g_Restore = g_Base + g_ParamOff;
    GWrite<uint32_t>(g_Restore, 0x01000000u | it->second);
    return true;
}

void Restore() {
    if (g_Restore) GWrite<uint32_t>(g_Restore, g_HostValue);
    g_Restore = 0;
}

}  // namespace songlink
