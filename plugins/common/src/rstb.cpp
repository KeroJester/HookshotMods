// See rstb.h. Format (mod_manager/rstb.py): "RESTBL", u32 version, u32 name length, u32 crc count, u32 name count;
// {u32 crc32(path), u32 size} sorted by crc; {char name[name length], u32 size} for paths whose crc collides.
#include "rstb.h"
#include "gamefiles.h"
#include "platform.h"
#include "vfs.h"
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>

namespace rstb {
using plat::Log;
using gamefiles::Bytes;

static const char* kTable = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs";
static std::mutex g_Mu;
static std::map<std::string, uint32_t> g_Want, g_Grow;
static std::shared_ptr<const Bytes> g_Raised;      // the raised table (zstd), served through vfs
static std::atomic<bool> g_Applied{false};

uint32_t Crc32(const std::string& s) {
    uint32_t c = 0xffffffffu;
    for (unsigned char ch : s) {
        c ^= ch;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1)));
    }
    return ~c;
}

void Grow(const std::string& romfsRel, uint32_t extra) {
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Grow[romfsRel] = std::max(g_Grow[romfsRel], extra);
}

void Require(const std::string& romfsRel, uint32_t minSize) {
    std::lock_guard<std::mutex> lk(g_Mu);
    uint32_t& v = g_Want[romfsRel];
    if (v < minSize) v = minSize;
}

bool Applied() { return g_Applied; }

struct Table {                                        // a decompressed table
    Bytes t;
    uint32_t slen = 0, ncrc = 0, nname = 0;
    bool Load(const Bytes& zs) {
        if (!gamefiles::Unzstd(zs, t) || t.size() < 22 || memcmp(t.data(), "RESTBL", 6)) return false;
        memcpy(&slen, &t[10], 4), memcpy(&ncrc, &t[14], 4), memcpy(&nname, &t[18], 4);
        return 22 + 8ull * ncrc + (slen + 4ull) * nname <= t.size();
    }
    uint8_t* Find(const std::string& path) {          // where the entry's size is, or null
        for (uint32_t i = 0; i < nname; i++) {        // (paths whose crc collides with another's are listed by name)
            const size_t at = 22 + 8ull * ncrc + (slen + 4ull) * i;
            if (!strncmp((const char*)&t[at], path.c_str(), slen)) return &t[at + slen];
        }
        const uint32_t crc = Crc32(path);
        uint32_t lo = 0, hi = ncrc;                   // (sorted by crc)
        while (lo < hi) {
            const uint32_t mid = lo + (hi - lo) / 2;
            uint32_t c;
            memcpy(&c, &t[22 + 8ull * mid], 4);
            if (c == crc) return &t[26 + 8ull * mid];
            if (c < crc) lo = mid + 1; else hi = mid;
        }
        return nullptr;
    }
    // A new file's entry {crc32(path), size}, inserted in crc order (files added by mods have no entry: the game then
    // guesses their heap, too small for a new model: "model buffer ... failed"). -> where the size is.
    uint8_t* Add(const std::string& path, uint32_t size) {
        const uint32_t crc = Crc32(path);
        uint32_t lo = 0, hi = ncrc;
        while (lo < hi) {
            const uint32_t mid = lo + (hi - lo) / 2;
            uint32_t c;
            memcpy(&c, &t[22 + 8ull * mid], 4);
            if (c < crc) lo = mid + 1; else hi = mid;
        }
        uint8_t e[8];
        memcpy(e, &crc, 4), memcpy(e + 4, &size, 4);
        t.insert(t.begin() + 22 + 8ull * lo, e, e + 8);
        ncrc++;
        memcpy(&t[14], &ncrc, 4);
        return &t[26 + 8ull * lo];
    }
};
static uint32_t Get(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }

// The game folder's own table (the runtime's prompts/rsizetable.zs, which already holds its button-prompt sizes, or
// the RomFS one), never a mod's: the mod's table is rebuilt from it each launch.
static bool BaseTable(Bytes& out, std::string& from) {
    const std::string romfs = gamefiles::RomfsDir();
    if (romfs.empty()) return false;
    const std::string game = romfs.substr(0, romfs.find_last_of("/\\"));
    const std::string s(1, plat::PathSep());
    for (const std::string& p : {game + s + "prompts" + s + "rsizetable.zs",
                                 romfs + s + "System" + s + "Resource" + s + "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"})
        if (gamefiles::ReadFile(p, out)) { from = p; return true; }
    return false;
}

static bool WriteIfChanged(const std::string& path, const Bytes& data) {
    Bytes old;
    if (gamefiles::ReadFile(path, old) && old == data) return true;
    if (!plat::MakeDirs(path.substr(0, path.find_last_of("/\\")))) return false;
    const std::string tmp = path + ".tmp";
    FILE* f = fopen(tmp.c_str(), "wb");
    if (!f) return false;
    const bool ok = fwrite(data.data(), 1, data.size(), f) == data.size();
    fclose(f);
    if (!ok) { remove(tmp.c_str()); return false; }
    remove(path.c_str());
    return rename(tmp.c_str(), path.c_str()) == 0;
}

void Start(const std::string& modDir) {
    std::map<std::string, uint32_t> want, grow;
    { std::lock_guard<std::mutex> lk(g_Mu); want = g_Want, grow = g_Grow; }
    for (auto& [path, extra] : grow) if (!want.count(path)) want[path] = 0;
    if (want.empty()) { g_Applied = true; return; }
    Bytes zs;
    std::string from;
    Table raised;
    if (!BaseTable(zs, from) || !raised.Load(zs)) { Log("[rstb] the game's size table is not readable: grown files stay unchanged\n"); return; }
    std::map<std::string, uint32_t> target;           // path -> the size it should have
    for (auto& [path, w] : want) {
        uint8_t* p = raised.Find(path);
        if (!p) {
            if (!w) { Log("[rstb] %s: no entry (the game sizes it itself)\n", path.c_str()); continue; }
            raised.Add(path, w);                      // (a new file with a requested size: it gets an entry)
            target[path] = w;
            Log("[rstb] %s: new entry %u\n", path.c_str(), w);
            continue;
        }
        const uint32_t s = Get(p), size = std::max(w, grow.count(path) ? s + grow[path] : s);
        target[path] = size;
        if (size != s) { memcpy(p, &size, 4); Log("[rstb] %s: %u -> %u\n", path.c_str(), s, size); }
    }
    auto data = std::make_shared<Bytes>(gamefiles::RawZstd(raised.t.data(), raised.t.size()));
    { std::lock_guard<std::mutex> lk(g_Mu); g_Raised = data; }
    Log("[rstb] raised table made from %s\n", from.c_str());
    // did the game load a table with these sizes at boot? (the runtime's choice: a mod folder's table or its own)
    Bytes served;
    Table now;
    const std::string servedPath = gamefiles::RomfsPath(kTable);
    if (!servedPath.empty() && gamefiles::ReadFile(servedPath, served) && now.Load(served)) {
        bool all = true;
        for (auto& [path, size] : target) { uint8_t* p = now.Find(path); if (!p || Get(p) < size) all = false; }
        if (all) { g_Applied = true; Log("[rstb] the table the game loaded at boot has the raised sizes (%s)\n", servedPath.c_str()); }
    }
    // the mod folder's copy, for runtimes that read the table before the layer starts (Longshot)
    const std::string s(1, plat::PathSep());
    const std::string mine = modDir + s + "romfs" + s + "System" + s + "Resource" + s + "ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs";
    if (!WriteIfChanged(mine, *data)) Log("[rstb] %s could not be written\n", mine.c_str());
    // and the copy served when the game opens the table from now on (Hookshot)
    vfs::RegisterLive(kTable, [] () -> std::shared_ptr<const Bytes> {
        std::shared_ptr<const Bytes> d;
        { std::lock_guard<std::mutex> lk(g_Mu); d = g_Raised; }
        if (d && !g_Applied.exchange(true)) Log("[rstb] the game read the raised table\n");
        return d;
    });
    if (!g_Applied) Log("[rstb] not in effect yet (the game may have loaded its table before the mod started): the grown files "
                        "wait for it; the mod folder's copy is read at the next launch\n");
}

}  // namespace rstb
