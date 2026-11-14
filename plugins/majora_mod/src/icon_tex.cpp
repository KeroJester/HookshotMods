// The added songs' icons as textures of the UI's texture archive (RESEARCH_NOTES.md §79n):
// UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.bntx gets OcarinaMelodyIcon_<type> for every icon.
//
// BNTX 4.1 (this file: target "Ounc", 878 textures, 244 MB): header +0x18 u32 relocation table offset, +0x1c u32 file
// size; NX header at 0x20: +0x24 u32 texture count, +0x28 u64 the texture list (u64 per texture -> BRTI), +0x30 u64
// BRTD (the texture data, 4096-aligned after its 0x10 header), +0x38 u64 the name dictionary (_DIC). All pointers are
// file offsets listed in _RLT: section 0 (everything before BRTD) and section 1 (pointers into BRTD), entries
// {u32 offset, u16 struct count, u8 pointers, u8 skipped} (each struct: that many u64 pointers, then that many u64
// skipped). A BRTI is a 0x318 block: +0x10 flags/dim/tile/swizzle, +0x16 mips, +0x1c format, +0x24 width, +0x28 height,
// +0x34 layout (low 3 bits: block height log2), +0x50 image size, +0x54 alignment, +0x58 channel sources, +0x60 name
// (-> u16 length + text), +0x2a0 the mip offset (into BRTD); pointers at +0x60..+0x88, +0x98, +0x2a8, +0x2b0, +0x300,
// +0x310 (section 0) and +0x2a0 (section 1).
//
// The new textures are copies of OcarinaMelodyIcon_00's BRTI, in RGBA8 sRGB (0x0b06; the game's icons are ASTC). The
// archive is rebuilt as: everything before BRTD, then a block of new data (the new BRTIs, a texture list of all
// textures, their names, a name dictionary of all textures; 4096-aligned so BRTD stays aligned), BRTD with the new
// texture data appended, and the relocation table with the new pointers. The old list and dictionary stay unused.
// The base is the runtime's own copy when it has one (prompts/Common.bntx.zs: its button glyphs), else the RomFS one.
#include "icons.h"
#include "gamefiles.h"
#include "platform.h"
#include "rstb.h"
#include "vfs.h"
#include <atomic>
#include <cstring>
#include <string>
#include <vector>

namespace icons {
using namespace plat;
using gamefiles::Bytes;

constexpr const char* kArchive = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.bntx";
constexpr const char* kTemplate = "OcarinaMelodyIcon_00";
constexpr uint32_t kFormat = 0x0b06;          // R8G8B8A8 sRGB
constexpr int kBlockHeightLog2 = 4;           // 16 GOBs
constexpr uint32_t kExtra = 1u << 20;         // (size table: the archive grows by about 0.55 MB)
static std::atomic<bool> g_Served{false}, g_Patched{false};

static uint32_t U32(const Bytes& b, size_t o) { uint32_t v; memcpy(&v, &b[o], 4); return v; }
static uint64_t U64(const Bytes& b, size_t o) { uint64_t v; memcpy(&v, &b[o], 8); return v; }
static uint16_t U16(const Bytes& b, size_t o) { uint16_t v; memcpy(&v, &b[o], 2); return v; }
static void P32(Bytes& b, size_t o, uint32_t v) { memcpy(&b[o], &v, 4); }
static void P64(Bytes& b, size_t o, uint64_t v) { memcpy(&b[o], &v, 8); }
static void P16(Bytes& b, size_t o, uint16_t v) { memcpy(&b[o], &v, 2); }
static size_t Align(size_t v, size_t a) { return (v + a - 1) / a * a; }

static std::string Name(const Bytes& b, uint64_t p) {
    if (p + 2 > b.size()) return std::string();
    const uint16_t n = U16(b, p);
    return p + 2 + n <= b.size() ? std::string((const char*)&b[p + 2], n) : std::string();
}

// ---- the name dictionary: a PATRICIA trie (bits counted from the last character's lowest bit) ------------------------
struct Node { int32_t ref; uint16_t left, right; std::string key; };
static int Bit(const std::string& k, int i) {
    if (i < 0) return 0;
    const size_t j = (size_t)i >> 3;
    if (j >= k.size()) return 0;
    return ((uint8_t)k[k.size() - 1 - j] >> (i & 7)) & 1;
}
static int FirstDiff(const std::string& a, const std::string& b) {
    const int n = (int)std::max(a.size(), b.size()) * 8;
    for (int i = 0; i < n; i++) if (Bit(a, i) != Bit(b, i)) return i;
    return -1;
}
static std::vector<Node> BuildDic(const std::vector<std::string>& names) {
    std::vector<Node> nodes{{-1, 0, 0, std::string()}};
    for (const std::string& key : names) {
        size_t p = 0, c = nodes[0].left;
        while (nodes[c].ref > nodes[p].ref) { p = c; c = Bit(key, nodes[c].ref) ? nodes[c].right : nodes[c].left; }
        const int crit = FirstDiff(key, nodes[c].key);
        p = 0, c = nodes[0].left;
        int side = 1;
        while (nodes[c].ref > nodes[p].ref && nodes[c].ref < crit) {
            p = c;
            if (Bit(key, nodes[c].ref)) c = nodes[c].right, side = 2; else c = nodes[c].left, side = 1;
        }
        const uint16_t n = (uint16_t)nodes.size();
        if (Bit(key, crit)) nodes.push_back({crit, (uint16_t)c, n, key});
        else nodes.push_back({crit, n, (uint16_t)c, key});
        if (p == 0) nodes[0].left = n;
        else if (side == 2) nodes[p].right = n;
        else nodes[p].left = n;
    }
    return nodes;
}

// ---- block-linear swizzle (Switch 2 GOB layout, as Hookshot's texture reader and mod_manager/bntx.py) -----------------
static size_t Swizzle(const uint8_t* rgba, int w, int h, Bytes& out) {
    const int bpp = 4, bh = 1 << kBlockHeightLog2;
    const size_t gobsPerRow = Align((size_t)w * bpp, 64) / 64;
    const size_t rowsOfBlocks = Align((size_t)h, 8 * bh) / (8 * bh);
    const size_t size = gobsPerRow * rowsOfBlocks * bh * 512;
    out.assign(size, 0);
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            const size_t X = (size_t)x * bpp, Y = (size_t)y;
            const size_t gob = (((X & 63) >> 5) << 8) | (((Y & 7) >> 2) << 7) | (((X & 31) >> 4) << 6) | (((Y >> 1) & 1) << 5) | ((Y & 1) << 4) | (X & 15);
            const size_t block = (Y / (8 * bh)) * gobsPerRow * bh + (X / 64) * bh + (Y % (8 * bh)) / 8;
            memcpy(&out[block * 512 + gob], rgba + ((size_t)y * w + x) * 4, 4);
        }
    return size;
}

struct Reloc { uint32_t off; uint16_t structs; uint8_t ptrs, skip; };

static bool Patch(Bytes& file) {
    // the runtime's own copy, if it keeps one (its button glyphs)
    const std::string romfs = gamefiles::RomfsDir();
    if (!romfs.empty()) {
        const std::string s(1, PathSep());
        Bytes own;
        if (gamefiles::ReadFile(romfs.substr(0, romfs.find_last_of("/\\")) + s + "prompts" + s + "Common.bntx.zs", own)) file.swap(own);
    }
    Bytes b;
    if (!gamefiles::Unzstd(file, b) || b.size() < 0x60 || memcmp(b.data(), "BNTX", 4)) { Log("[icons] %s: not readable\n", kArchive); return false; }
    const uint32_t rlt = U32(b, 0x18), count = U32(b, 0x24);
    const uint64_t list = U64(b, 0x28), brtd = U64(b, 0x30), dic = U64(b, 0x38);
    if (rlt + 0x10 > b.size() || memcmp(&b[rlt], "_RLT", 4) || brtd >= rlt || memcmp(&b[brtd], "BRTD", 4) || list + 8ull * count > brtd ||
        (brtd + 0x10) % 4096 || U32(b, rlt + 8) != 2) { Log("[icons] %s: layout not as expected\n", kArchive); return false; }
    // the textures' names, the template
    std::vector<std::string> names;
    uint64_t templ = 0;
    for (uint32_t i = 0; i < count; i++) {
        const uint64_t t = U64(b, list + 8ull * i);
        if (t + 0x318 > brtd || memcmp(&b[t], "BRTI", 4)) { Log("[icons] texture %u: not a BRTI\n", i); return false; }
        names.push_back(Name(b, U64(b, t + 0x60)));
        if (names.back() == kTemplate) templ = t;
    }
    if (!templ || U32(b, templ + 4) != 0x318) { Log("[icons] %s not found\n", kTemplate); return false; }
    // (the dictionary as built here must be the file's own, or the format is not what this code knows)
    {
        const std::vector<Node> check = BuildDic(names);
        for (uint32_t i = 0; i <= count; i++) {
            const size_t n = dic + 8 + 16ull * i;
            if (U32(b, n) != (uint32_t)check[i].ref || U16(b, n + 4) != check[i].left || U16(b, n + 6) != check[i].right) {
                Log("[icons] the name dictionary differs from the one built here (node %u): not changed\n", i);
                return false;
            }
        }
    }
    // the relocation table
    struct Sec { uint32_t off, size, first, count; } sec[2];
    for (int i = 0; i < 2; i++) {
        const size_t s = rlt + 0x10 + 0x18 * i;
        sec[i] = {U32(b, s + 8), U32(b, s + 12), U32(b, s + 16), U32(b, s + 20)};
    }
    std::vector<Reloc> rel[2];
    const size_t entries = rlt + 0x10 + 0x18 * 2;
    for (int i = 0; i < 2; i++)
        for (uint32_t k = 0; k < sec[i].count; k++) {
            const size_t e = entries + 8ull * (sec[i].first + k);
            rel[i].push_back({U32(b, e), U16(b, e + 4), b[e + 6], b[e + 7]});
        }
    // the icons, decompressed
    Bytes z(kZstd, kZstd + kZstdSize), raw;
    if (!gamefiles::Unzstd(z, raw) || raw.size() != kRawSize) { Log("[icons] the icon data is not readable\n"); return false; }
    // ---- the new block (before BRTD): BRTIs, texture list, names, dictionary
    const size_t n = kCount, at = brtd;
    Bytes blk;
    std::vector<size_t> brti(n), nameAt(n);
    for (size_t i = 0; i < n; i++) { brti[i] = blk.size(); blk.insert(blk.end(), b.begin() + templ, b.begin() + templ + 0x318); }
    const size_t listAt = blk.size();
    blk.resize(listAt + 8 * (count + n), 0);
    for (size_t i = 0; i < n; i++) {
        blk.resize(Align(blk.size(), 4), 0);
        nameAt[i] = blk.size();
        char nm[32];
        snprintf(nm, sizeof nm, "OcarinaMelodyIcon_%02u", kTypes[i]);
        const uint16_t len = (uint16_t)strlen(nm);
        blk.resize(blk.size() + 2, 0);
        P16(blk, nameAt[i], len);
        blk.insert(blk.end(), nm, nm + len + 1);
        names.push_back(nm);
    }
    blk.resize(Align(blk.size(), 8), 0);
    const size_t dicAt = blk.size();
    const std::vector<Node> nodes = BuildDic(names);
    blk.resize(dicAt + 8 + 16 * nodes.size(), 0);
    memcpy(&blk[dicAt], "_DIC", 4);
    P32(blk, dicAt + 4, (uint32_t)(nodes.size() - 1));
    const size_t N = Align(blk.size(), 4096);
    blk.resize(N, 0);
    // ---- the new texture data (after BRTD's), each 512-aligned
    Bytes data;
    std::vector<size_t> dataAt(n), dataSize(n);
    const size_t px = (size_t)kSize * kSize * 4;
    for (size_t i = 0; i < n; i++) {
        data.resize(Align(data.size(), 512), 0);
        Bytes sw;
        dataSize[i] = Swizzle(raw.data() + px * i, kSize, kSize, sw);
        dataAt[i] = data.size();
        data.insert(data.end(), sw.begin(), sw.end());
    }
    const size_t D = Align(data.size(), 4096);
    data.resize(D, 0);
    // final offsets
    auto Map = [&](uint64_t o) -> uint64_t { return o < at ? o : o + N; };
    const uint64_t newBrtd = brtd + N, newData0 = rlt + N, newRlt = rlt + N + D;
    const uint64_t blkFile = at;
    // the BRTIs: internal pointers moved, name, format, size, data
    for (size_t i = 0; i < n; i++) {
        const size_t o = brti[i];
        for (size_t f : {0x70, 0x78, 0x80, 0x88, 0x98, 0x2a8, 0x2b0, 0x300, 0x310}) {
            const uint64_t v = U64(blk, o + f);
            if (v >= templ && v < templ + 0x318) P64(blk, o + f, v - templ + blkFile + o);
        }
        P64(blk, o + 0x60, blkFile + nameAt[i]);
        P32(blk, o + 0x1c, kFormat);
        P32(blk, o + 0x24, (uint32_t)kSize), P32(blk, o + 0x28, (uint32_t)kSize);
        P16(blk, o + 0x16, 1);
        P32(blk, o + 0x34, (U32(blk, o + 0x34) & ~7u) | kBlockHeightLog2);
        P32(blk, o + 0x50, (uint32_t)dataSize[i]);
        P32(blk, o + 0x54, 512);
        blk[o + 0x58] = 2, blk[o + 0x59] = 3, blk[o + 0x5a] = 4, blk[o + 0x5b] = 5;   // (R G B A)
        P64(blk, o + 0x2a0, newData0 + dataAt[i]);
        const uint32_t next = (uint32_t)(i + 1 < n ? 0x318 : N - o);                  // (the last one reaches BRTD)
        P32(blk, o + 4, next), P32(blk, o + 8, next);
    }
    // the texture list and the dictionary
    for (uint32_t i = 0; i < count; i++) P64(blk, listAt + 8ull * i, U64(b, list + 8ull * i));
    for (size_t i = 0; i < n; i++) P64(blk, listAt + 8 * (count + i), blkFile + brti[i]);
    const uint64_t rootName = U64(b, dic + 8 + 8);
    for (size_t i = 0; i < nodes.size(); i++) {
        const size_t nd = dicAt + 8 + 16 * i;
        P32(blk, nd, (uint32_t)nodes[i].ref);
        P16(blk, nd + 4, nodes[i].left), P16(blk, nd + 6, nodes[i].right);
        uint64_t np = rootName;
        if (i >= 1 && i <= count) np = U64(b, U64(b, list + 8ull * (i - 1)) + 0x60);
        else if (i > count) np = blkFile + nameAt[i - 1 - count];
        P64(blk, nd + 8, np);
    }
    // ---- the old file's pointers moved (every listed pointer that points at BRTD or later)
    for (int s = 0; s < 2; s++)
        for (const Reloc& r : rel[s]) {
            uint64_t p = r.off;
            for (uint32_t k = 0; k < r.structs; k++, p += 8ull * (r.ptrs + r.skip))
                for (uint32_t j = 0; j < r.ptrs; j++) {
                    const uint64_t f = p + 8ull * j;
                    if (f + 8 > b.size()) continue;
                    const uint64_t v = U64(b, f);
                    if (v >= at) P64(b, f, v + N);
                }
        }
    // ---- the new file
    Bytes out;
    out.reserve(newRlt + 0x10 + 0x18 * 2 + 8 * (sec[0].count + sec[1].count + 6 * n + 2));
    out.insert(out.end(), b.begin(), b.begin() + at);
    out.insert(out.end(), blk.begin(), blk.end());
    out.insert(out.end(), b.begin() + at, b.begin() + rlt);
    out.insert(out.end(), data.begin(), data.end());
    P32(out, 0x24, (uint32_t)(count + n));
    P64(out, 0x28, blkFile + listAt);
    P64(out, 0x38, blkFile + dicAt);
    P32(out, newBrtd + 8, (uint32_t)(newRlt - newBrtd));
    // the relocation table
    std::vector<Reloc> r0 = rel[0], r1 = rel[1];
    for (size_t i = 0; i < n; i++) {
        const uint32_t o = (uint32_t)(blkFile + brti[i]);
        r0.push_back({o + 0x60, 1, 6, 0});
        r0.push_back({o + 0x98, 1, 1, 0});
        r0.push_back({o + 0x2a8, 1, 2, 0});
        r0.push_back({o + 0x300, 2, 1, 1});
        r1.push_back({o + 0x2a0, 1, 1, 0});
    }
    r0.push_back({(uint32_t)(blkFile + listAt), (uint16_t)(count + n), 1, 0});
    r0.push_back({(uint32_t)(blkFile + dicAt + 16), (uint16_t)nodes.size(), 1, 1});
    const size_t rltAt = out.size();
    out.resize(rltAt + 0x10 + 0x18 * 2 + 8 * (r0.size() + r1.size()), 0);
    memcpy(&out[rltAt], "_RLT", 4);
    P32(out, rltAt + 4, (uint32_t)rltAt);
    P32(out, rltAt + 8, 2);
    const uint32_t s0size = (uint32_t)(at + N), s1off = (uint32_t)newBrtd, s1size = (uint32_t)(newRlt - newBrtd);
    const size_t s0 = rltAt + 0x10, s1 = s0 + 0x18;
    P32(out, s0 + 8, 0), P32(out, s0 + 12, s0size), P32(out, s0 + 16, 0), P32(out, s0 + 20, (uint32_t)r0.size());
    P32(out, s1 + 8, s1off), P32(out, s1 + 12, s1size), P32(out, s1 + 16, (uint32_t)r0.size()), P32(out, s1 + 20, (uint32_t)r1.size());
    size_t e = s1 + 0x18;
    for (const std::vector<Reloc>* rs : {&r0, &r1})
        for (const Reloc& r : *rs) { P32(out, e, r.off), P16(out, e + 4, r.structs), out[e + 6] = r.ptrs, out[e + 7] = r.skip; e += 8; }
    P32(out, 0x18, (uint32_t)rltAt);
    P32(out, 0x1c, (uint32_t)out.size());
    if (rltAt != newRlt) Log("[icons] (relocation table at %#zx, planned %#llx)\n", rltAt, (unsigned long long)newRlt);
    file = gamefiles::RawZstd(out.data(), out.size());
    g_Patched = true;
    Log("[icons] %s: %zu icons added (%zu -> %zu bytes)\n", kArchive, n, b.size(), out.size());
    return true;
}

void Start() {
    vfs::Register(std::string(kArchive) + ".zs", Patch, [] { const bool ok = rstb::Applied(); if (ok) g_Served = true; return ok; });
    rstb::Grow(kArchive, kExtra);
}

bool Served() { return g_Served && g_Patched; }

}  // namespace icons
