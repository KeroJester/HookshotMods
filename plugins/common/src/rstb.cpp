// See rstb.h. Format (mod_manager/rstb.py): "RESTBL", u32 version, u32 name length, u32 crc count, u32 name count;
// {u32 crc32(path), u32 size} sorted by crc; {char name[name length], u32 size} for paths whose crc collides.
#include "rstb.h"
#include "gamefiles.h"
#include "platform.h"
#include "vfs.h"
#include <cstring>
#include <map>
#include <mutex>

namespace rstb {
using plat::Log;

static const char* kTable = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs";
static std::mutex g_Mu;
static std::map<std::string, uint32_t> g_Want;

uint32_t Crc32(const std::string& s) {
    uint32_t c = 0xffffffffu;
    for (unsigned char ch : s) {
        c ^= ch;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1)));
    }
    return ~c;
}

static bool Patch(gamefiles::Bytes& file) {
    gamefiles::Bytes t;
    if (!gamefiles::Unzstd(file, t) || t.size() < 22 || memcmp(t.data(), "RESTBL", 6)) { Log("[rstb] not a size table\n"); return false; }
    uint32_t slen, ncrc, nname;
    memcpy(&slen, &t[10], 4), memcpy(&ncrc, &t[14], 4), memcpy(&nname, &t[18], 4);
    if (22 + 8ull * ncrc + (slen + 4ull) * nname > t.size()) { Log("[rstb] size table truncated\n"); return false; }
    std::lock_guard<std::mutex> lk(g_Mu);
    int changed = 0;
    for (auto& [path, size] : g_Want) {
        bool found = false;
        const uint32_t crc = Crc32(path);
        for (uint32_t i = 0; i < ncrc; i++) {
            uint32_t c, s;
            memcpy(&c, &t[22 + 8 * i], 4), memcpy(&s, &t[26 + 8 * i], 4);
            if (c != crc) continue;
            found = true;
            if (s < size) { memcpy(&t[26 + 8 * i], &size, 4); changed++; Log("[rstb] %s: %u -> %u\n", path.c_str(), s, size); }
        }
        for (uint32_t i = 0; i < nname; i++) {   // (paths whose crc collides with another's)
            const size_t at = 22 + 8ull * ncrc + (slen + 4ull) * i;
            if (strncmp((const char*)&t[at], path.c_str(), slen)) continue;
            found = true;
            uint32_t s;
            memcpy(&s, &t[at + slen], 4);
            if (s < size) { memcpy(&t[at + slen], &size, 4); changed++; Log("[rstb] %s (named): %u -> %u\n", path.c_str(), s, size); }
        }
        if (!found) Log("[rstb] %s: no entry (the game sizes it itself)\n", path.c_str());
    }
    if (!changed) return false;
    file = gamefiles::RawZstd(t.data(), t.size());
    return true;
}

void Require(const std::string& romfsRel, uint32_t minSize) {
    bool first;
    {
        std::lock_guard<std::mutex> lk(g_Mu);
        first = g_Want.empty();
        uint32_t& v = g_Want[romfsRel];
        if (v < minSize) v = minSize;
    }
    if (first) vfs::Register(kTable, Patch);
}

}  // namespace rstb
