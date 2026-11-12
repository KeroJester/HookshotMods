#include <algorithm>
#include <mutex>
#include <unordered_set>
#include "imgui_internal.h"   // RegisterUserTexture
// Item Controls (mod 050-ItemControls): N64-style item buttons for the OoT remake.
// Independent of every other mod: its own controller hook (chained by HookHost onto any other), gated on its own mod
// folder (mods/[!_-]*/item_controls). Xbox labels as the game shows them; nn::hid bits in parentheses:
//   Xbox B = A (0) action/dash, A = B (1) jump, Y = X (2) put away, X = Y (3) attack, LB = L (6), RB = R (7),
//   LT = ZL (8) target, RT = ZR (9) use item (vanilla).
// Behaviour:
//   gameplay: LB/RB + X/Y/B uses one of 6 slots: the slot's item is written to the game's RT item (GameData Enum
//             PlayerStatus.AssignableItem.Child/.Adult) and RT is reported held while the face button is held, so the
//             game's own item use runs (aiming, holding, bottles...). LB/RB alone are hidden (no Quick Select wheel).
//             RT is reported as Xbox B's action bit (roll/dash); the real B is unchanged.
//   pause menu, Items tab: LB/RB + X/Y/B assigns the hovered item: the RT item is cleared to Empty, Select is pressed
//             for the game (it writes the hovered item to the RT item), and whatever it wrote goes to the slot.
//             LB/RB never switch tabs (LT/RT still do, vanilla).
//   anything else (dialogue, ocarina, cutscenes, map): buttons pass through.
// Facts (RESEARCH_NOTES.md section 63):
//   - the RT item lives in a GameData enum store: key table {u32 name hash, u32 entry index}, entries of 0x38 bytes
//     (header 0x84c40898, +0x18 default = Empty, +0x1C current, +0x20 copy). Writing +0x1C/+0x20 changes the item.
//   - f_802f1380 = per-frame base UI screen method; x0 is the pause screen (vtable 0x84c0baa8) while it is open.
//     Its current tab: u32 at [[screen+0x198]+0x19c] (0 Collection, 3 Equipment, 4 Items, 5 System).
//   - gameplay = the HUD screens update (also mid-air) and no pause menu, map, dialogue or ocarina (see Context).

namespace itemctl {

constexpr uint32_t kHashAssignChild = 0xa0029837, kHashAssignAdult = 0xbc32ce24;
constexpr uint32_t kEnumHeader = 0x84c40898, kHashEmpty = 0xbe69de49;
constexpr uint64_t kPauseScreenVt = 0x84c0baa8;
constexpr int kTabItems = 4;
constexpr uint64_t kBtnA = 1 << 0, kBtnX = 1 << 2, kBtnY = 1 << 3, kBtnL = 1 << 6, kBtnR = 1 << 7, kBtnZR = 1 << 9;
// slot order: LB+X, LB+Y, LB+B, RB+X, RB+Y, RB+B  (face bits: Xbox X = Y, Xbox Y = X, Xbox B = A)
constexpr uint64_t kFace[3] = {kBtnY, kBtnX, kBtnA};
static const char* kSlotName[6] = {"LB+X", "LB+Y", "LB+B", "RB+X", "RB+Y", "RB+B"};

static bool g_On = false, g_Dbg = false;
// (function-local: Install runs from the overlay's load-time constructor, before this file's globals are built)
static std::string& Dir() { static std::string d; return d; }
#define g_Dir Dir()
using Clock = std::chrono::steady_clock;

// ---- slots + item names ----------------------------------------------------------------------------------------
static std::atomic<uint32_t> g_Slot[6];
static std::unordered_map<uint32_t, std::string>& ItemNames() { static std::unordered_map<uint32_t, std::string> m; return m; }
#define g_ItemName ItemNames()
static std::mutex g_SaveMu;
static std::unordered_set<uint32_t>& AimItems() { static std::unordered_set<uint32_t> s; return s; }
// icons: item hash -> file (bottle contents keyed hash ^ 0x80000000), bottle item hash -> bottle index
static std::unordered_map<uint32_t, std::string>& IconFiles() { static std::unordered_map<uint32_t, std::string> m; return m; }
static std::unordered_map<uint32_t, int>& BottleItems() { static std::unordered_map<uint32_t, int> m; return m; }
static uint32_t g_BottleKey[5];                     // contents enum key per bottle (icons.txt)
static std::vector<uint32_t>& ContentHashes() { static std::vector<uint32_t> v; return v; }

static const char* ItemName(uint32_t h) {
    auto it = g_ItemName.find(h);
    return it == g_ItemName.end() ? "?" : it->second.c_str();
}
static void LoadFiles() {
    for (auto& s : g_Slot) s = kHashEmpty;
    if (FILE* f = fopen((g_Dir + "/items.txt").c_str(), "r")) {
        char line[256];
        while (fgets(line, sizeof line, f)) {
            unsigned h; char name[200];
            if (sscanf(line, "%x %199[^\n]", &h, name) == 2) g_ItemName[h] = name;
        }
        fclose(f);
    }
    if (FILE* f = fopen((g_Dir + "/icons.txt").c_str(), "r")) {      // (textures are made on the render thread)
        char line[256];
        int nb = 0;
        while (fgets(line, sizeof line, f)) {
            char kind[16], file[128]; unsigned h, k;
            if (sscanf(line, "item %x %127s", &h, file) == 2) IconFiles()[h] = file;
            else if (sscanf(line, "content %x %127s", &h, file) == 2) IconFiles()[h ^ 0x80000000u] = file, ContentHashes().push_back(h);
            else if (sscanf(line, "bottle %x %x", &h, &k) == 2 && nb < 5) { BottleItems()[h] = nb; g_BottleKey[nb++] = k; }
            (void)kind;
        }
        fclose(f);
    }
    if (FILE* f = fopen((g_Dir + "/aim.txt").c_str(), "r")) {
        char line[256];
        while (fgets(line, sizeof line, f)) { unsigned h; if (sscanf(line, "%x", &h) == 1) AimItems().insert(h); }
        fclose(f);
    }
    if (FILE* f = fopen((g_Dir + "/slots.txt").c_str(), "r")) {
        char line[256];
        int i = 0;
        while (i < 6 && fgets(line, sizeof line, f)) {
            unsigned h; char nm[16];
            if (sscanf(line, "%15s %x", nm, &h) == 2) g_Slot[i++] = h;
        }
        fclose(f);
    }
}
static void SaveSlots() {
    std::lock_guard<std::mutex> lk(g_SaveMu);
    std::string tmp = g_Dir + "/slots.txt.tmp";
    if (FILE* f = fopen(tmp.c_str(), "w")) {
        for (int i = 0; i < 6; i++) fprintf(f, "%s %08x %s\n", kSlotName[i], g_Slot[i].load(), ItemName(g_Slot[i]));
        fclose(f);
        rename(tmp.c_str(), (g_Dir + "/slots.txt").c_str());
    }
}

// ---- the RT item entries (GameData) ----------------------------------------------------------------------------
static std::atomic<uint64_t> g_EntryChild{0}, g_EntryAdult{0};
static std::atomic<uint64_t> g_BottleEntry[5];      // bottle contents entries (keys: g_BottleKey)
static std::atomic<bool> g_Scanning{false};

static bool EntryValid(uint64_t e, uint32_t def = kHashEmpty) {
    return e && e < (1ULL << 40) && GRead<uint32_t>(e) == kEnumHeader && GRead<uint32_t>(e + 0x18) == def;
}
// HUD option (option.sav): GameData Enum 0x4e767f10 {Normal, Pro, ProMaster}; ProMaster = the menu's "Hero"
constexpr uint32_t kHashHudMode = 0x4e767f10, kHudNormal = 0x390b012d, kHudPro = 0xd4b93848, kHudHero = 0xbfae48cb;
static std::atomic<uint64_t> g_HudModeEntry{0};

// key -> entry: the entry array starts at the first header of a run of 0x38-byte entries near the key table
static uint64_t ResolveEntry(uint64_t key, uint32_t idx, uint32_t def = kHashEmpty) {
    if (idx > 4096) return 0;
    for (uint64_t a = (key > 0x20000 ? key - 0x20000 : 0) & ~7ULL; a < key + 0x20000; a += 8) {
        if (GRead<uint32_t>(a) != kEnumHeader || GRead<uint32_t>(a - 0x38) == kEnumHeader) continue;
        uint64_t e = a + (uint64_t)idx * 0x38;
        if (EntryValid(e, def)) return e;
    }
    return 0;
}

static void ScanEntries() {
    const uint64_t base = 0x100000000000ULL;
    uint64_t child = 0, adult = 0;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    while (m && fgets(line, sizeof line, m)) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r' || perms[1] != 'w') continue;
        lo = std::max<unsigned long>(lo, base); hi = std::min<unsigned long>(hi, base + (1ULL << 39));
        for (unsigned long w = lo; w < hi; w += (1UL << 30)) {
            unsigned long we = std::min<unsigned long>(hi, w + (1UL << 30));
            vec.resize((we - w) >> 12);
            if (mincore((void*)w, we - w, vec.data()) != 0) continue;
            for (size_t pg = 0; pg < vec.size(); pg++) {
                if (!(vec[pg] & 1)) continue;
                const uint32_t* p = (const uint32_t*)(w + (pg << 12));
                for (int i = 0; i + 1 < 1024; i += 2) {
                    int bottle = -1;
                    for (int k = 0; k < 5; k++) if (g_BottleKey[k] && p[i] == g_BottleKey[k]) bottle = k;
                    if (p[i] == kHashHudMode) {
                        uint64_t e = ResolveEntry((uint64_t)(p + i) - base, p[i + 1], kHudNormal);
                        if (g_Dbg) OverlayLog("[itemctl] HUD option key at 0x%llx idx %u -> %llx\n", (unsigned long long)((uint64_t)(p + i) - base), p[i + 1], (unsigned long long)e);
                        if (e) g_HudModeEntry = e;
                        continue;
                    }
                    if (p[i] != kHashAssignChild && p[i] != kHashAssignAdult && bottle < 0) continue;
                    uint64_t e = ResolveEntry((uint64_t)(p + i) - base, p[i + 1]);
                    if (!e) continue;
                    if (bottle >= 0) g_BottleEntry[bottle] = e;
                    else (p[i] == kHashAssignChild ? child : adult) = e;
                }
            }
        }
    }
    if (m) fclose(m);
    if (uint64_t h = g_HudModeEntry) OverlayLog("[itemctl] HUD option entry 0x%llx = %08x\n", (unsigned long long)h, GRead<uint32_t>(h + 0x1C));
    if (child && adult) {
        g_EntryChild = child; g_EntryAdult = adult;
        OverlayLog("[itemctl] RT item entries: child 0x%llx (%s), adult 0x%llx (%s)\n", (unsigned long long)child,
                   ItemName(GRead<uint32_t>(child + 0x1C)), (unsigned long long)adult, ItemName(GRead<uint32_t>(adult + 0x1C)));
    }
}
// Valid entries, or false (then a background scan is started: the save may not be loaded yet)
static bool Entries(uint64_t& c, uint64_t& a) {
    c = g_EntryChild; a = g_EntryAdult;
    if (EntryValid(c) && EntryValid(a)) return true;
    if (!g_Scanning.exchange(true))
        std::thread([] { ScanEntries(); std::this_thread::sleep_for(std::chrono::seconds(2)); g_Scanning = false; }).detach();
    return false;
}
static void WriteEntry(uint64_t e, uint32_t v) { GWrite<uint32_t>(e + 0x1C, v); GWrite<uint32_t>(e + 0x20, v); }

// ---- context ---------------------------------------------------------------------------------------------------
// f_802f1380 runs every frame for each visible UI screen (x0 = screen). Screen vtables seen (RESEARCH_NOTES 63):
//   HUD in play (also mid-air, while throwing): 84c0afe0, 84c0b710 (gone in dialogue, the map, cutscenes)
//   pause menu: 84c0baa8 (+ 84c04fb0, 84c08aa0);  map: 84c04fb0, 84c08aa0;  dialogue: 84c091f8, 84c09a10
// Gameplay = HUD up, and no pause menu, map, dialogue or ocarina (f_832b4df0 runs while the ocarina is out).
static GuestFn o_ScreenFrame = nullptr;   // f_802f1380
static GuestFn o_Ocarina = nullptr;       // f_832b4df0
static std::atomic<uint64_t> g_Screen{0};
static std::atomic<int64_t> g_PauseSeen{0}, g_HudSeen{0}, g_MenuSeen{0}, g_OcarinaSeen{0};
static std::atomic<uint64_t> g_HudScrA{0}, g_HudScrB{0};   // the two HUD screens (84c0afe0, 84c0b710)
static int64_t NowNs() { return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count(); }

static std::mutex g_VtMu;
static std::unordered_map<uint64_t, int>& SeenVts() { static std::unordered_map<uint64_t, int> m; return m; }
static void ScreenFrameHook(GuestCtx* c) {
    uint64_t s = c->x[0];
    if (s && s < (1ULL << 40)) {
        uint64_t vt = GRead<uint64_t>(s);
        if (g_Dbg) { std::lock_guard<std::mutex> lk(g_VtMu); SeenVts()[vt]++; }
        if (vt == kPauseScreenVt) { g_Screen = s; g_PauseSeen = NowNs(); }
        else if (vt == 0x84c0afe0 || vt == 0x84c0b710) { g_HudSeen = NowNs(); (vt == 0x84c0afe0 ? g_HudScrA : g_HudScrB) = s; }
        else if (vt == 0x84c04fb0 || vt == 0x84c08aa0 || vt == 0x84c091f8 || vt == 0x84c09a10) g_MenuSeen = NowNs();
    }
    o_ScreenFrame(c);
}
static void OcarinaHook(GuestCtx* c) { g_OcarinaSeen = NowNs(); o_Ocarina(c); }
static bool Recent(const std::atomic<int64_t>& t) { return NowNs() - t.load() < 100000000; }
static bool PauseOpen() { return Recent(g_PauseSeen); }
static bool InGameplay() { return !PauseOpen() && !Recent(g_OcarinaSeen) && !Recent(g_MenuSeen) && Recent(g_HudSeen); }
static int PauseTab() {
    uint64_t s = g_Screen;
    uint64_t t = s ? GRead<uint64_t>(s + 0x198) : 0;
    return t && t < (1ULL << 40) ? (int)GRead<uint32_t>(t + 0x19c) : -1;
}

// ---- input --------------------------------------------------------------------------------------------------------
// The game reads a 16-sample history (newest first, keyed by sampling number) and derives presses from consecutive
// samples, so each sample is transformed once, in order, and re-reported identically from a cache.
static GuestFn o_Npad = nullptr;
struct Cached { uint64_t n, out; };
static Cached g_Cache[64];
static int g_CacheHead = 0;
static uint64_t g_PrevRaw = 0;

// kUse: RT held while the face button is held.  kAim (aimed items): RT latched until the bumper is released or the
// same face button is pressed again.  kAssign: pause-menu assignment running.
enum Mode { kIdle, kUse, kAim, kAssign };
static Mode g_Mode = kIdle;
static int g_ModeSlot = -1;                  // -1: nothing to use (empty slot), the press is only swallowed
static uint64_t g_ModeFace = 0, g_ModeShoulder = 0, g_SwallowFace = 0;
static int64_t g_ModeT0 = 0, g_AimEnd = 0;   // g_AimEnd: when RT goes up (0 = still latched)
static uint32_t g_PrevChild = 0, g_PrevAdult = 0;
constexpr int64_t kPickUpNs = 34000000, kMinPressNs = 50000000;   // game sees a new item after ~2 frames; RT >= 3 frames

static int SlotFor(uint64_t raw, uint64_t pressed) {
    int sh = (raw & kBtnL) ? 0 : (raw & kBtnR) ? 3 : -1;
    if (sh < 0) return -1;
    for (int f = 0; f < 3; f++) if (pressed & kFace[f]) return sh + f;
    return -1;
}

static void StartUse(int slot) {
    uint32_t v = g_Slot[slot];
    g_ModeFace = kFace[slot % 3]; g_ModeShoulder = slot < 3 ? kBtnL : kBtnR;
    g_Mode = AimItems().count(v) ? kAim : kUse; g_ModeSlot = -1; g_AimEnd = 0;
    uint64_t c, a;
    if (v == kHashEmpty || !Entries(c, a)) { g_Mode = kUse; return; }   // empty slot: swallowed until the face is up
    bool same = GRead<uint32_t>(c + 0x1C) == v && GRead<uint32_t>(a + 0x1C) == v;
    if (!same) { WriteEntry(c, v); WriteEntry(a, v); }
    g_ModeSlot = slot;
    g_ModeT0 = NowNs() - (same ? kPickUpNs : 0);           // a freshly written item: give the game 2 frames to pick it up
    if (g_Dbg) OverlayLog("[itemctl] %s %s: %s\n", g_Mode == kAim ? "aim" : "use", kSlotName[slot], ItemName(v));
}

static void StartAssign(int slot) {
    uint64_t c, a;
    if (!Entries(c, a)) return;
    g_PrevChild = GRead<uint32_t>(c + 0x1C); g_PrevAdult = GRead<uint32_t>(a + 0x1C);
    WriteEntry(c, kHashEmpty); WriteEntry(a, kHashEmpty);
    g_Mode = kAssign; g_ModeSlot = slot; g_ModeT0 = NowNs();
}

// one new sample: raw buttons -> what the game sees
static uint64_t Transform(uint64_t raw) {
    const bool play = InGameplay(), pause = PauseOpen();
    if (g_Dbg) {
        static int last = -1;
        int ctx = (pause ? 1 : 0) | (play ? 2 : 0) | (Recent(g_HudSeen) ? 4 : 0) | (Recent(g_MenuSeen) ? 8 : 0) | (Recent(g_OcarinaSeen) ? 16 : 0);
        if (ctx != last) { OverlayLog("[itemctl] ctx pause=%d play=%d hud=%d menu=%d ocarina=%d\n", ctx & 1, (ctx >> 1) & 1,
                                      (ctx >> 2) & 1, (ctx >> 3) & 1, (ctx >> 4) & 1); last = ctx; }
    }
    const uint64_t pressed = raw & ~g_PrevRaw;
    g_PrevRaw = raw;
    const int64_t now = NowNs();
    uint64_t out = raw;
    if (g_SwallowFace && !(raw & g_SwallowFace)) g_SwallowFace = 0;
    out &= ~g_SwallowFace;                                // a face press that ended an aim does nothing else

    if (g_Mode == kUse) {                                 // RT held while the face button is held
        if (!(raw & g_ModeFace)) g_Mode = kIdle;
        else {
            out &= ~(g_ModeFace | kBtnL | kBtnR | kBtnZR);
            if (g_ModeSlot >= 0 && now - g_ModeT0 > kPickUpNs) out |= kBtnZR;
            if (raw & kBtnZR) out |= kBtnA;               // RT stays the roll button
            return out;
        }
    }
    if (g_Mode == kAim) {                                 // RT latched: thumbs are free for the camera
        const int64_t zrOn = g_ModeT0 + kPickUpNs;
        if (!g_AimEnd && (!(raw & g_ModeShoulder) || (pressed & g_ModeFace))) {
            g_AimEnd = std::max(now, zrOn + kMinPressNs);
            if (pressed & g_ModeFace) g_SwallowFace = g_ModeFace;
            if (g_Dbg) OverlayLog("[itemctl] release (%s)\n", (raw & g_ModeShoulder) ? "face" : "bumper");
        }
        out &= ~(kBtnL | kBtnR | kBtnZR | ((raw & (kBtnL | kBtnR)) ? (kBtnX | kBtnY | kBtnA) : 0) | g_ModeFace);
        if (raw & kBtnZR) out |= kBtnA;
        if (g_AimEnd && now >= g_AimEnd) g_Mode = kIdle;  // RT up; (this sample's face press is not a new combo)
        else if (g_ModeSlot >= 0 && now > zrOn) out |= kBtnZR;
        return out;
    }
    if (g_Mode == kAssign) {
        uint64_t c, a;
        bool ok = Entries(c, a);
        uint32_t vc = ok ? GRead<uint32_t>(c + 0x1C) : kHashEmpty, va = ok ? GRead<uint32_t>(a + 0x1C) : kHashEmpty;
        uint32_t got = vc != kHashEmpty ? vc : va;
        out &= ~(kBtnL | kBtnR | kBtnX | kBtnY);
        if (now - g_ModeT0 < kMinPressNs) out |= kBtnA;  // Select, ~3 frames
        else out &= ~kBtnA;
        if (got != kHashEmpty) {
            g_Slot[g_ModeSlot] = got;
            for (int i = 0; i < 6; i++) if (i != g_ModeSlot && g_Slot[i] == got) g_Slot[i] = kHashEmpty;   // one slot per item
            OverlayLog("[itemctl] assigned %s = %s\n", kSlotName[g_ModeSlot], ItemName(got));
            std::thread(SaveSlots).detach();
            g_Mode = kIdle;
        } else if (now - g_ModeT0 > 400000000) {          // nothing assignable there: put the RT item back
            if (ok) { WriteEntry(c, g_PrevChild); WriteEntry(a, g_PrevAdult); }
            if (g_Dbg) OverlayLog("[itemctl] assign %s: nothing selected\n", kSlotName[g_ModeSlot]);
            g_Mode = kIdle;
        }
        return out;
    }

    if (pause) {
        out &= ~(kBtnL | kBtnR);                          // no tab switching on LB/RB
        if (raw & (kBtnL | kBtnR)) {
            out &= ~(kBtnX | kBtnY | kBtnA);              // LB/RB + face never reaches the menu
            int slot = SlotFor(raw, pressed);
            if (slot >= 0 && PauseTab() == kTabItems) StartAssign(slot);
        }
        return out;
    }
    if (play) {
        out &= ~(kBtnL | kBtnR | kBtnZR);                 // bumpers never reach the game (no Quick Select wheel)
        if (raw & kBtnZR) out |= kBtnA;                   // RT = roll / dash
        if (raw & (kBtnL | kBtnR)) {
            int slot = SlotFor(raw, pressed);
            if (slot >= 0) { StartUse(slot); return out & ~(kBtnX | kBtnY | kBtnA); }
            out &= ~(kBtnX | kBtnY | (pressed & kBtnA));   // face presses while LB/RB are held belong to combos
        }
        return out;
    }
    return out;                                           // dialogue, ocarina, cutscenes, map: untouched
}

static void NpadHook(GuestCtx* c) {
    uint64_t outp = c->x[0];
    int count = (int)c->x[1];
    o_Npad(c);
    int got = (int)c->x[0];
    if (!g_On || got <= 0 || got > count || got > 64) return;
    for (int i = got - 1; i >= 0; i--) {                  // oldest first
        uint64_t e = outp + 0x28 * i;
        uint64_t n = GRead<uint64_t>(e), raw = GRead<uint64_t>(e + 8), out = 0;
        bool hit = false;
        for (auto& k : g_Cache) if (k.n == n && n) { out = k.out; hit = true; break; }
        if (!hit) {
            out = Transform(raw);
            g_Cache[g_CacheHead++ & 63] = {n, out};
            if (g_Dbg && out != raw) OverlayLog("[itemctl] sample %llu: %llx -> %llx\n", (unsigned long long)n,
                                                (unsigned long long)raw, (unsigned long long)out);
        }
        GWrite<uint64_t>(e + 8, out);
    }
}

#include "item_controls_research.inc"

// ---- HUD: two rows of three item boxes (top LB, bottom RB), the game's pouch icons ----------------------------------
static std::unordered_map<std::string, ImTextureData*>& Textures() { static std::unordered_map<std::string, ImTextureData*> m; return m; }
static ImTextureData* Texture(const std::string& file) {
    auto& T = Textures();
    auto it = T.find(file);
    if (it != T.end()) return it->second;
    ImTextureData* t = nullptr;
    if (FILE* f = fopen((g_Dir + "/icons/" + file).c_str(), "rb")) {
        t = IM_NEW(ImTextureData)();
        t->Create(ImTextureFormat_RGBA32, 64, 64);
        if (fread(t->GetPixels(), 1, 64 * 64 * 4, f) == 64 * 64 * 4) { t->UseColors = true; ImGui::RegisterUserTexture(t); }
        else { IM_DELETE(t); t = nullptr; }
        fclose(f);
    }
    return T[file] = t;                                  // (nullptr remembered too: no retry every frame)
}
static ImTextureData* IconFor(uint32_t item) {
    if (item == kHashEmpty) return nullptr;
    auto b = BottleItems().find(item);
    if (b != BottleItems().end()) {                      // a bottle shows its contents
        uint64_t e = g_BottleEntry[b->second];
        if (EntryValid(e)) {
            auto it = IconFiles().find(GRead<uint32_t>(e + 0x1C) ^ 0x80000000u);
            if (it != IconFiles().end()) return Texture(it->second);
        }
    }
    auto it = IconFiles().find(item);
    return it == IconFiles().end() ? nullptr : Texture(it->second);
}

// Shown on the pause menu's Items tab, and in play while the game shows its own player-status HUD: screen
// 84c0afe0 (hearts, magic, the old item box), whose +0x1a8 is 1 while shown. The game clears it for the HUD
// option (Pro: outside combat; Hero = ProMaster: always), in cutscenes and while paused, so the boxes follow it.
static bool HudVisible() {
    if (!g_On) return false;
    if (PauseOpen()) return PauseTab() == kTabItems;
    uint64_t a = g_HudScrA;
    return InGameplay() && a && GRead<uint32_t>(a + 0x1a8) != 0;
}

static void DrawHud() {
    if (!HudVisible()) return;

    ImGuiIO& io = ImGui::GetIO();
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const float S = io.DisplaySize.y / 1080.0f;
    const float box = 58 * S, gap = 7 * S, pad = 4.5f * S, round = 10 * S, label = 38 * S;
    const float x1 = io.DisplaySize.x - 52 * S;                        // right edge, in line with the minimap
    const float x0 = x1 - 3 * box - 2 * gap, y0 = 48 * S;    // where the game's RT item box was (hidden by the mod's HUD layout)
    ImFont* font = ImGui::GetFont();
    static const char* face[3] = {"X", "Y", "B"};
    static const ImU32 faceCol[3] = {IM_COL32(64, 132, 255, 255), IM_COL32(255, 196, 36, 255), IM_COL32(236, 64, 52, 255)};
    const int active = (g_Mode == kUse || g_Mode == kAim) ? g_ModeSlot : -1;

    for (int row = 0; row < 2; row++) {
        const float y = y0 + row * (box + gap);
        // bumper label
        ImVec2 lp0(x0 - gap - label, y + box * 0.5f - 12.5f * S), lp1(x0 - gap, y + box * 0.5f + 12.5f * S);
        dl->AddRectFilled(lp0, lp1, IM_COL32(20, 16, 10, 170), 12.5f * S);
        const char* lb = row ? "RB" : "LB";
        float fs = 18 * S;
        ImVec2 ts = font->CalcTextSizeA(fs, FLT_MAX, 0, lb);
        dl->AddText(font, fs, ImVec2((lp0.x + lp1.x - ts.x) * 0.5f, (lp0.y + lp1.y - ts.y) * 0.5f), IM_COL32(240, 220, 170, 255), lb);
        for (int f = 0; f < 3; f++) {
            const int slot = row * 3 + f;
            ImVec2 p0(x0 + f * (box + gap), y), p1(p0.x + box, p0.y + box);
            const bool on = slot == active;
            dl->AddRectFilled(p0, p1, on ? IM_COL32(70, 56, 24, 200) : IM_COL32(20, 16, 10, 165), round);
            dl->AddRect(p0, p1, on ? IM_COL32(255, 226, 120, 255) : IM_COL32(196, 164, 92, 190), round, 0, (on ? 2.5f : 1.5f) * S);
            if (ImTextureData* t = IconFor(g_Slot[slot]))
                dl->AddImage(t->GetTexRef(), ImVec2(p0.x + pad, p0.y + pad), ImVec2(p1.x - pad, p1.y - pad));
            // face badge, bottom-right corner
            const float r = 10 * S;
            ImVec2 c(p1.x - r * 0.7f, p1.y - r * 0.7f);
            dl->AddCircleFilled(c, r + 1.2f * S, IM_COL32(0, 0, 0, 200));
            dl->AddCircleFilled(c, r, faceCol[f]);
            float bs = 15 * S;
            ImVec2 bt = font->CalcTextSizeA(bs, FLT_MAX, 0, face[f]);
            dl->AddText(font, bs, ImVec2(c.x - bt.x * 0.5f, c.y - bt.y * 0.5f), IM_COL32(255, 255, 255, 255), face[f]);
        }
    }
    if (PauseOpen()) {
        const char* hint = "Hold LB/RB + X/Y/B on an item to assign";
        float fs = 15 * S;
        ImVec2 ts = font->CalcTextSizeA(fs, FLT_MAX, 0, hint);
        ImVec2 tp(x1 - ts.x, y0 + 2 * box + gap + 8 * S);
        dl->AddRectFilled(ImVec2(tp.x - 8 * S, tp.y - 4 * S), ImVec2(x1 + 4 * S, tp.y + ts.y + 4 * S), IM_COL32(20, 16, 10, 170), 6 * S);
        dl->AddText(font, fs, tp, IM_COL32(240, 220, 170, 255), hint);
    }
}

static void Install() {
    if (FILE* p = popen("ls -d mods/[!_-]*/item_controls 2>/dev/null | head -1", "r")) {
        char b[512];
        if (fgets(b, sizeof b, p)) { b[strcspn(b, "\n")] = 0; g_Dir = b; }
        pclose(p);
    }
    if (g_Dir.empty()) return;                              // mod disabled or absent: nothing of this runs
    g_Dbg = getenv("HOOKSHOT_ITEMCTL_DEBUG") != nullptr;
    LoadFiles();
    uintptr_t a = ResolveHostSymbol("npad_states");
    o_Npad = a ? HookHost(a, NpadHook, "itemctl npad_states") : nullptr;
    if (!o_Npad) { OverlayLog("[itemctl] controller hook failed; off\n"); return; }
    if (!InstallGuestHooks({{0x802f1380, ScreenFrameHook, &o_ScreenFrame, "itemctl screen frame"},
                            {0x832b4df0, OcarinaHook, &o_Ocarina, "itemctl ocarina update"}})) {
        OverlayLog("[itemctl] context hooks failed; controls stay vanilla\n");
        return;                                             // (g_On stays false: the npad hook passes everything through)
    }
    g_On = true;
    std::thread([] {                                    // keep the RT item entries located (they appear with the save)
        for (;;) {
            uint64_t c = g_EntryChild, a = g_EntryAdult;
            if (!(EntryValid(c) && EntryValid(a) && EntryValid(g_HudModeEntry, kHudNormal) ? true : false) && !g_Scanning.exchange(true)) { ScanEntries(); g_Scanning = false; }
            std::this_thread::sleep_for(std::chrono::seconds(3));
        }
    }).detach();
    ResearchStart();
    if (g_Dbg) std::thread([] {                         // research: force the HUD option, log the HUD screens' state
        const char* m = getenv("HOOKSHOT_ITEMCTL_HUDMODE");
        uint32_t want = !m ? 0 : !strcmp(m, "Pro") ? kHudPro : !strcmp(m, "Hero") ? kHudHero : kHudNormal;
        uint64_t la = ~0ULL, lb = ~0ULL;
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            uint64_t h = g_HudModeEntry;
            if (want && EntryValid(h, kHudNormal) && GRead<uint32_t>(h + 0x1C) != want) {
                WriteEntry(h, want); OverlayLog("[itemctl] HUD option forced to %s\n", m);
            }
            static int lastVis = -1;
            if (int v = HudVisible(); v != lastVis) { OverlayLog("[itemctl] item boxes %s (pause %d tab %d)\n", v ? "shown" : "hidden", (int)PauseOpen(), PauseTab()); lastVis = v; }
            uint64_t A = g_HudScrA, B = g_HudScrB;
            static int dumped = 0;
            if (const char* dt = getenv("HOOKSHOT_ITEMCTL_HUDDUMP"); dt && A && B && Recent(g_HudSeen) && !PauseOpen() && dumped++ == 150) {
                for (int k = 0; k < 2; k++) {
                    char path[160]; snprintf(path, sizeof path, "re/runs/hudscr_%s_%c.bin", dt, k ? 'B' : 'A');
                    if (FILE* f = fopen(path, "wb")) { fwrite(GPtr(k ? B : A), 1, 0x1000, f); fclose(f); }
                }
                OverlayLog("[itemctl] hud screens dumped (%s)\n", dt);
            }
            uint64_t va = A ? GRead<uint64_t>(A + 0x1a8) : 0, vb = B ? GRead<uint64_t>(B + 0x1a8) : 0;
            if (va != la || vb != lb) { OverlayLog("[itemctl] hud screens A %llx +1a8 %016llx | B %llx +1a8 %016llx | seen %d\n",
                (unsigned long long)A, (unsigned long long)va, (unsigned long long)B, (unsigned long long)vb, (int)Recent(g_HudSeen)); la = va; lb = vb; }
        }
    }).detach();
    if (g_Dbg) std::thread([] {
        std::string last;
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
            std::vector<uint64_t> v;
            { std::lock_guard<std::mutex> lk(g_VtMu); for (auto& kv : SeenVts()) v.push_back(kv.first); SeenVts().clear(); }
            std::sort(v.begin(), v.end());
            std::string t;
            for (uint64_t x : v) { char b[24]; snprintf(b, sizeof b, " %llx", (unsigned long long)x); t += b; }
            if (t != last) { OverlayLog("[itemctl] screens:%s\n", t.c_str()); last = t; }
        }
    }).detach();
    OverlayLog("[itemctl] Item Controls on (%s); slots: %s, %s, %s / %s, %s, %s\n", g_Dir.c_str(), ItemName(g_Slot[0]),
               ItemName(g_Slot[1]), ItemName(g_Slot[2]), ItemName(g_Slot[3]), ItemName(g_Slot[4]), ItemName(g_Slot[5]));
}

#undef g_Dir
#undef g_ItemName
}  // namespace itemctl

static void ItemCtlInstall() { itemctl::Install(); }
static void ItemCtlDrawHud() { itemctl::DrawHud(); }
static bool ItemCtlHudWanted() { return itemctl::HudVisible(); }
