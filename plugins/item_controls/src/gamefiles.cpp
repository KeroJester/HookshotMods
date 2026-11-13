// See gamefiles.h. Formats as the project's Python tools read them (mod_manager/zstd_helper.py, bntx.py).
#include "gamefiles.h"
#include "platform.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <span>
#include "video_core/textures/astc.h"   // suyu's ASTC decoder as Hookshot bundles it (GPL-2.0-or-later)
#define ZSTD_STATIC_LINKING_ONLY
#include "zstd.h"

namespace gamefiles {
using namespace plat;

typedef int (*ModsRomfsFn)(const char* rel, char* out, size_t cap);   // host/mods.c hos_mods_romfs
static ModsRomfsFn g_ModsRomfs;
static const char* const* g_RomfsDir;                                  // host/nn_fs.c const char* hos_romfs_dir

bool Init() {
    auto sym = ResolveSymbols({"hos_mods_romfs", "hos_romfs_dir"});
    if (sym.count("hos_mods_romfs")) g_ModsRomfs = (ModsRomfsFn)sym["hos_mods_romfs"];
    if (sym.count("hos_romfs_dir")) g_RomfsDir = (const char* const*)sym["hos_romfs_dir"];
    if (!g_RomfsDir) Log("[files] hos_romfs_dir not found: game files cannot be read\n");
    if (!g_ModsRomfs) Log("[files] hos_mods_romfs not found: other mods' files are not seen\n");
    return g_RomfsDir != nullptr;
}

static std::string HostSeps(std::string p) {   // (Hookshot keeps host paths with '\'; its POSIX layer maps them)
#ifndef _WIN32
    std::replace(p.begin(), p.end(), '\\', '/');
#endif
    return p;
}

std::string RomfsPath(const std::string& rel) {
    char out[1024];
    if (g_ModsRomfs && g_ModsRomfs(rel.c_str(), out, sizeof out)) return HostSeps(out);
    if (!g_RomfsDir || !*g_RomfsDir) return "";
    std::string p = HostSeps(std::string(*g_RomfsDir) + "/" + rel);
    if (FILE* f = fopen(p.c_str(), "rb")) { fclose(f); return p; }
    return "";
}

bool ReadFile(const std::string& path, Bytes& out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    out.resize(n > 0 ? (size_t)n : 0);
    bool ok = n >= 0 && fread(out.data(), 1, out.size(), f) == out.size();
    fclose(f);
    return ok;
}

// ---- zstd -------------------------------------------------------------------------------------------------------
static std::mutex g_DictMu;
static std::vector<Bytes> g_Dicts;
static bool g_DictsLoaded;

static bool UnzstdWith(const Bytes& in, Bytes& out, const Bytes* dict) {
    unsigned long long size = ZSTD_getFrameContentSize(in.data(), in.size());
    if (size == ZSTD_CONTENTSIZE_ERROR || size == ZSTD_CONTENTSIZE_UNKNOWN || size > (1ull << 31)) return false;
    out.resize((size_t)size);
    ZSTD_DCtx* d = ZSTD_createDCtx();
    size_t r = dict ? ZSTD_decompress_usingDict(d, out.data(), out.size(), in.data(), in.size(), dict->data(), dict->size())
                    : ZSTD_decompressDCtx(d, out.data(), out.size(), in.data(), in.size());
    ZSTD_freeDCtx(d);
    return !ZSTD_isError(r) && r == out.size();
}

static void LoadDicts() {   // Pack/ZsDic.pack.zs (plain zstd) = SARC of *.zsdic
    g_DictsLoaded = true;
    Bytes zs, sarc;
    std::string p = RomfsPath("Pack/ZsDic.pack.zs");
    if (p.empty() || !ReadFile(p, zs) || !UnzstdWith(zs, sarc, nullptr)) { Log("[files] Pack/ZsDic.pack.zs unreadable\n"); return; }
    for (auto& e : SarcList(sarc))
        if (e.name.size() > 6 && e.name.compare(e.name.size() - 6, 6, ".zsdic") == 0)
            g_Dicts.emplace_back(sarc.begin() + e.offset, sarc.begin() + e.offset + e.size);
}

bool Unzstd(const Bytes& in, Bytes& out) {
    unsigned id = ZSTD_getDictID_fromFrame(in.data(), in.size());
    if (!id) return UnzstdWith(in, out, nullptr);
    std::lock_guard<std::mutex> lk(g_DictMu);
    if (!g_DictsLoaded) LoadDicts();
    for (auto& d : g_Dicts)
        if (ZSTD_getDictID_fromDict(d.data(), d.size()) == id) return UnzstdWith(in, out, &d);
    Log("[files] no zstd dictionary %u\n", id);
    return false;
}

Bytes RawZstd(const uint8_t* data, size_t size) {
    Bytes o;
    auto put = [&](uint64_t v, int n) { for (int i = 0; i < n; i++) o.push_back((uint8_t)(v >> (8 * i))); };
    put(0xFD2FB528u, 4);                 // magic
    o.push_back(0xE0);                   // frame header: 8-byte content size, single segment, no checksum, no dict
    put(size, 8);
    const size_t kMax = 128 * 1024;      // (largest block)
    size_t at = 0;
    do {
        size_t n = std::min(kMax, size - at);
        bool last = at + n == size;
        put((uint32_t)((n << 3) | (0 << 1) | (last ? 1 : 0)), 3);   // block header: size, Raw_Block, last
        o.insert(o.end(), data + at, data + at + n);
        at += n;
    } while (at < size);
    return o;
}

// ---- SARC -------------------------------------------------------------------------------------------------------
template <class T> static T Rd(const Bytes& b, size_t o) { T v{}; if (o + sizeof(T) <= b.size()) memcpy(&v, &b[o], sizeof(T)); return v; }

std::vector<SarcEntry> SarcList(const Bytes& s) {
    std::vector<SarcEntry> out;
    if (s.size() < 0x20 || memcmp(s.data(), "SARC", 4) != 0) return out;
    uint16_t hdr = Rd<uint16_t>(s, 4);
    uint32_t data = Rd<uint32_t>(s, 0xC);
    size_t sfat = hdr;
    if (memcmp(&s[sfat], "SFAT", 4) != 0) return out;
    uint16_t sfatHdr = Rd<uint16_t>(s, sfat + 4), count = Rd<uint16_t>(s, sfat + 6);
    size_t nodes = sfat + sfatHdr, sfnt = nodes + 16u * count;
    if (sfnt + 8 > s.size() || memcmp(&s[sfnt], "SFNT", 4) != 0) return out;
    size_t names = sfnt + Rd<uint16_t>(s, sfnt + 4);
    for (unsigned i = 0; i < count; i++) {
        size_t n = nodes + 16u * i;
        uint32_t attr = Rd<uint32_t>(s, n + 4), b = Rd<uint32_t>(s, n + 8), e = Rd<uint32_t>(s, n + 12);
        std::string name;
        if (attr & 0x01000000) {
            size_t at = names + 4u * (attr & 0xFFFF);
            while (at < s.size() && s[at]) name.push_back((char)s[at++]);
        }
        if (data + (size_t)e <= s.size() && b <= e) out.push_back({name, data + (size_t)b, (size_t)(e - b)});
    }
    return out;
}

// ---- BNTX icons -------------------------------------------------------------------------------------------------
bool BntxIconRgba(const Bytes& d, int size, Bytes& rgba) {
    if (d.size() < 0x100 || memcmp(d.data(), "BNTX", 4) != 0) return false;
    auto it = std::search(d.begin(), d.end(), (const uint8_t*)"BRTI", (const uint8_t*)"BRTI" + 4);
    if (it == d.end()) return false;
    size_t o = (size_t)(it - d.begin());
    uint16_t tile = Rd<uint16_t>(d, o + 0x12), mips = Rd<uint16_t>(d, o + 0x16);
    uint32_t format = Rd<uint32_t>(d, o + 0x1C);
    int w = Rd<int32_t>(d, o + 0x24), h = Rd<int32_t>(d, o + 0x28), depth = Rd<int32_t>(d, o + 0x2C);
    int array = Rd<int32_t>(d, o + 0x30), layout = Rd<int32_t>(d, o + 0x34);
    uint64_t mip0 = Rd<uint64_t>(d, (size_t)Rd<uint64_t>(d, o + 0x70));
    if ((format >> 8) != 0x2D || tile != 0 || depth != 1 || array != 1 || !mips || w <= 0 || h <= 0 || w > 4096 || h > 4096)
        return false;                                     // (only ASTC 4x4, plain 2D block-linear, as the icons are)
    const int bpb = 16, wb = (w + 3) / 4, hb = (h + 3) / 4;
    int bh = 1 << (layout & 7);
    while (bh > 1 && (hb + 7) / 8 <= bh / 2) bh /= 2;     // (block height shrinks for small textures)
    const int gobsPerRow = (wb * bpb + 63) / 64;
    Bytes blocks((size_t)wb * hb * bpb);
    for (int y = 0; y < hb; y++)
        for (int bx = 0; bx < wb; bx++) {
            const int x = bx * bpb;   // Switch 2 GOB layout (bntx.py, from Hookshot's vk_texture.cpp)
            const size_t gob = (((x & 63) >> 5) << 8) | (((y & 7) >> 2) << 7) | (((x & 31) >> 4) << 6) |
                               (((y >> 1) & 1) << 5) | ((y & 1) << 4) | (x & 15);
            const size_t blk = (size_t)(y / (8 * bh)) * gobsPerRow * bh + (x / 64) * bh + (y % (8 * bh)) / 8;
            const size_t src = mip0 + blk * 512 + gob;
            if (src + bpb > d.size()) return false;
            memcpy(&blocks[((size_t)y * wb + bx) * bpb], &d[src], bpb);
        }
    Bytes full((size_t)w * h * 4);
    Tegra::Texture::ASTC::DecompressSerial(std::span<const uint8_t>(blocks), (uint32_t)w, (uint32_t)h, 1, 4, 4,
                                           std::span<uint8_t>(full));
    rgba.assign((size_t)size * size * 4, 0);              // box filter to size x size
    for (int ty = 0; ty < size; ty++)
        for (int tx = 0; tx < size; tx++) {
            int x0 = tx * w / size, x1 = std::max(x0 + 1, (tx + 1) * w / size);
            int y0 = ty * h / size, y1 = std::max(y0 + 1, (ty + 1) * h / size);
            uint32_t acc[4] = {0, 0, 0, 0}, n = 0;
            for (int y = y0; y < y1; y++)
                for (int x = x0; x < x1; x++, n++) {
                    const uint8_t* p = &full[((size_t)y * w + x) * 4];
                    acc[0] += p[0] * p[3]; acc[1] += p[1] * p[3]; acc[2] += p[2] * p[3]; acc[3] += p[3];
                }
            uint8_t* q = &rgba[((size_t)ty * size + tx) * 4];
            q[3] = (uint8_t)(acc[3] / n);
            for (int c = 0; c < 3; c++) q[c] = acc[3] ? (uint8_t)std::min<uint32_t>(255, acc[c] / acc[3]) : 0;   // (alpha-weighted)
        }
    return true;
}

}  // namespace gamefiles
