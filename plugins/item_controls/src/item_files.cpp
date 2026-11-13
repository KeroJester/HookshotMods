// The game files Item Controls changes, made in memory from the player's own files (vfs.h):
//   - HUD layout (UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs, PlayerStatus_00.buil): the vanilla
//     RT item box and its header (panes N_Shortcut_00, Pa_SelectItem_00, both under N_ScaleR_00) scaled to 0, where
//     our six boxes go
//   - button prompts (UI/CtrlGuideSetTable/...bgyml): the RT entries left out of every item's prompt set, except
//     fishing and carried objects (RT still throws, reels and brakes there)
// RESEARCH_NOTES.md section 63 has how these were found; mod_manager/build_item_controls.py did the same on disk.
#include "item_files.h"
#include "gamefiles.h"
#include "platform.h"
#include "vfs.h"
#include <algorithm>
#include <cstring>

namespace itemctl {
using namespace plat;
using gamefiles::Bytes;

template <class T> static T Rd(const uint8_t* b, size_t o) { T v; memcpy(&v, b + o, sizeof(T)); return v; }
template <class T> static void Wr(uint8_t* b, size_t o, T v) { memcpy(b + o, &v, sizeof(T)); }

// ---- HUD layout ---------------------------------------------------------------------------------------------------
static const char* kHudArchive = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs";

// PlayerStatus_00.buil (BFFH LayoutResData): pane count u32 at 0x318, u64 record offsets at 0x320; records 0x44
// bytes (+0x10/+0x14 scale x/y, +0x28 parent index); pane names in record order in the string pool from "RootPane".
static bool HidePanes(Bytes& sarc, size_t at, size_t size) {
    const uint8_t* b = sarc.data() + at;
    if (size < 0x320) return false;
    const uint32_t count = Rd<uint32_t>(b, 0x318);
    if (!count || count > 4096 || 0x320 + 8ull * count > size) return false;
    static const char kRoot[] = "RootPane";
    const uint8_t* root = std::search(b, b + size, (const uint8_t*)kRoot, (const uint8_t*)kRoot + sizeof kRoot);
    if (root == b + size) return false;
    std::vector<std::string> names;
    for (const uint8_t* p = root; names.size() < count && p < b + size; p += names.back().size() + 1)
        names.emplace_back((const char*)p, strnlen((const char*)p, (size_t)(b + size - p)));
    if (names.size() != count) return false;
    auto index = [&](const char* n) { return (int)(std::find(names.begin(), names.end(), n) - names.begin()); };
    std::vector<uint64_t> recs;
    for (const char* pane : {"N_Shortcut_00", "Pa_SelectItem_00"}) {
        int i = index(pane);
        if (i >= (int)count) { Log("[hud] pane %s not found\n", pane); return false; }
        uint64_t rec = Rd<uint64_t>(b, 0x320 + 8ull * i);
        if (rec + 0x44 > size) return false;
        uint32_t parent = Rd<uint32_t>(b, rec + 0x28);
        if (parent >= count || names[parent] != "N_ScaleR_00") { Log("[hud] %s: unexpected parent\n", pane); return false; }
        recs.push_back(rec);
    }
    // the archive holds the layout's bytes more than once: change every copy
    const Bytes layout(sarc.begin() + at, sarc.begin() + at + size);
    int copies = 0;
    for (auto it = sarc.begin(); (it = std::search(it, sarc.end(), layout.begin(), layout.end())) != sarc.end(); it += size) {
        for (uint64_t rec : recs) {
            Wr<float>(&*it, rec + 0x10, 0.0f);
            Wr<float>(&*it, rec + 0x14, 0.0f);
        }
        copies++;
    }
    Log("[hud] vanilla RT item box hidden (%d copies of PlayerStatus_00.buil)\n", copies);
    return copies > 0;
}

static bool PatchHud(Bytes& file) {
    Bytes sarc;
    if (!gamefiles::Unzstd(file, sarc)) { Log("[hud] %s: cannot decompress\n", kHudArchive); return false; }
    for (auto& e : gamefiles::SarcList(sarc))
        if (e.name == "PlayerStatus_00.buil") {
            if (!HidePanes(sarc, e.offset, e.size)) return false;
            file = gamefiles::RawZstd(sarc.data(), sarc.size());
            return true;
        }
    Log("[hud] PlayerStatus_00.buil not in the archive\n");
    return false;
}

// ---- button prompts (BYML v7, little endian) ------------------------------------------------------------------------
static const char* kGuideTable = "UI/CtrlGuideSetTable/CtrlGuideSetTable.game__ui__CtrlGuideSetTable.bgyml";
constexpr uint32_t kGuideKeyRT = 3432537025u;   // GuideKey of RT (the item button)
// prompt sets (GuideType hashes) that keep RT: fishing (ThrowLure, lure/rod RollUp, Brake, Hook, RollUp), carrying
// (Throw, Throw + Put)
static const uint32_t kKeepRT[] = {2771367494u, 3948661679u, 141619505u, 1887781522u, 1708067740u, 3280769329u,
                                   1880103375u, 2190618626u};

struct Byml {
    uint8_t* d;
    size_t n;
    std::vector<std::string> keys;
    bool Load(Bytes& b) {
        d = b.data(), n = b.size();
        if (n < 16 || memcmp(d, "YB", 2) != 0) return false;
        uint32_t kt = Rd<uint32_t>(d, 4);
        if (!kt || kt + 4 > n || d[kt] != 0xC2) return false;
        uint32_t cnt = Rd<uint32_t>(d, kt) >> 8;
        if (kt + 4 + 4ull * (cnt + 1) > n) return false;
        for (uint32_t i = 0; i < cnt; i++) {
            uint32_t o = kt + Rd<uint32_t>(d, kt + 4 + 4 * i);
            if (o >= n) return false;
            keys.emplace_back((const char*)d + o, strnlen((const char*)d + o, n - o));
        }
        return true;
    }
    uint32_t Root() const { return Rd<uint32_t>(d, 12); }
    uint32_t Count(uint32_t node) const { return node + 4 <= n ? Rd<uint32_t>(d, node) >> 8 : 0; }
    // offset of a dict's entry for key (8 bytes: u24 key index, u8 type, u32 value), or 0 if absent
    size_t Entry(uint32_t dict, const char* key) const {
        if (dict + 4 > n || d[dict] != 0xC1) return 0;
        for (uint32_t i = 0, c = Count(dict); i < c; i++) {
            size_t e = dict + 4 + 8ull * i;
            if (e + 8 > n) return 0;
            uint32_t k = Rd<uint32_t>(d, e) & 0xFFFFFF;
            if (k < keys.size() && keys[k] == key) return e;
        }
        return 0;
    }
    bool Get(uint32_t dict, const char* key, uint8_t& type, uint32_t& val) const {
        size_t e = Entry(dict, key);
        if (!e) return false;
        type = d[e + 3];
        val = Rd<uint32_t>(d, e + 4);
        return true;
    }
    size_t Values(uint32_t arr) const { return arr + ((4 + Count(arr) + 3) & ~3u); }
};

static bool PatchGuides(Bytes& file) {
    Byml y;
    if (!y.Load(file)) { Log("[prompts] %s: not a BYML\n", kGuideTable); return false; }
    uint8_t t; uint32_t sets;
    if (!y.Get(y.Root(), "GuideSetArray", t, sets) || t != 0xC0) { Log("[prompts] GuideSetArray not found\n"); return false; }
    int removed = 0;
    for (uint32_t i = 0, ns = y.Count(sets); i < ns; i++) {
        uint32_t set = Rd<uint32_t>(y.d, y.Values(sets) + 4 * i), type = 0, arr;
        uint8_t tt;
        if (y.d[sets + 4 + i] != 0xC1) continue;
        y.Get(set, "GuideType", tt, type);
        if (std::find(std::begin(kKeepRT), std::end(kKeepRT), type) != std::end(kKeepRT)) continue;
        size_t entry = y.Entry(set, "GuideArray");
        if (!entry || y.d[entry + 3] != 0xC0) continue;
        arr = Rd<uint32_t>(y.d, entry + 4);
        // the entries whose GuideKey is not RT go into a new array at the end of the file, and this set points to it:
        // the file stores equal nodes once, so the old array may be shared with a set that keeps RT
        std::vector<std::pair<uint8_t, uint32_t>> keep;
        const uint32_t cnt = y.Count(arr);
        for (uint32_t k = 0; k < cnt; k++) {
            uint8_t et = y.d[arr + 4 + k];
            uint32_t ev = Rd<uint32_t>(y.d, y.Values(arr) + 4 * k), key = 0;
            uint8_t kt;
            if (et == 0xC1 && y.Get(ev, "GuideKey", kt, key) && key == kGuideKeyRT) continue;
            keep.push_back({et, ev});
        }
        if (keep.size() == cnt) continue;
        removed += (int)(cnt - keep.size());
        const size_t at = (file.size() + 3) & ~(size_t)3, vals = (4 + keep.size() + 3) & ~(size_t)3;
        file.resize(at + vals + 4 * keep.size(), 0);
        y.d = file.data(), y.n = file.size();
        Wr<uint32_t>(y.d, at, 0xC0u | ((uint32_t)keep.size() << 8));
        for (size_t k = 0; k < keep.size(); k++) {
            y.d[at + 4 + k] = keep[k].first;
            Wr<uint32_t>(y.d, at + vals + 4 * k, keep[k].second);
        }
        Wr<uint32_t>(y.d, entry + 4, (uint32_t)at);
    }
    Log("[prompts] %d RT button prompts left out\n", removed);
    return removed > 0;
}

void RegisterFilePatches() {
    vfs::Register(kHudArchive, PatchHud);
    vfs::Register(kGuideTable, PatchGuides);
}

}  // namespace itemctl
