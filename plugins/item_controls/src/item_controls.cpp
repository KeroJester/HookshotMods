// Item Controls: N64-style item buttons for the OoT remake running under Hookshot. Xbox labels as the game shows
// them; nn::hid bits in parentheses:
//   Xbox B = A (0) action/dash, A = B (1) jump, Y = X (2) put away, X = Y (3) attack, LB = L (6), RB = R (7),
//   LT = ZL (8) target, RT = ZR (9) use item (vanilla).
// Behaviour:
//   gameplay: LB/RB + X/Y/B uses one of 6 slots: the slot's item is written to the game's RT item (GameData Enum
//             PlayerStatus.AssignableItem.Child/.Adult) and RT is reported held for the game, so its own item use
//             runs. Aimed items (aim.txt) latch RT until the bumper is released or the face button pressed again.
//             LB/RB alone are hidden (no Quick Select wheel). RT is reported as the action bit (roll/dash).
//   pause menu, Items tab: LB/RB + X/Y/B assigns the hovered item (RT item cleared, Select pressed for the game, the
//             item it writes goes to the slot). LB/RB never switch tabs.
//   anything else (dialogue, ocarina, cutscenes, map): buttons pass through.
// The research behind every constant is in RESEARCH_NOTES.md section 63 of the project.
#include "item_controls.h"
#include "hook.h"
#include "platform.h"
#include "imgui.h"
#include "imgui_internal.h"   // RegisterUserTexture
#include <algorithm>
#include <atomic>
#include <cfloat>
#include <cstdio>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <unordered_set>

namespace itemctl {
using namespace plat;

constexpr uint32_t kHashAssignChild = 0xa0029837, kHashAssignAdult = 0xbc32ce24;
constexpr uint32_t kEnumHeader = 0x84c40898, kHashEmpty = 0xbe69de49;
// HUD option (option.sav): Enum 0x4e767f10 {Normal, Pro, ProMaster}; only its default is needed to validate the entry
constexpr uint64_t kPauseScreenVt = 0x84c0baa8, kHudScreenVt = 0x84c0afe0, kHudScreen2Vt = 0x84c0b710;
constexpr int kTabItems = 4;
constexpr uint64_t kBtnA = 1 << 0, kBtnX = 1 << 2, kBtnY = 1 << 3, kBtnL = 1 << 6, kBtnR = 1 << 7, kBtnZR = 1 << 9;
// slot order: LB+X, LB+Y, LB+B, RB+X, RB+Y, RB+B  (face bits: Xbox X = Y, Xbox Y = X, Xbox B = A)
constexpr uint64_t kFace[3] = {kBtnY, kBtnX, kBtnA};
static const char* kSlotName[6] = {"LB+X", "LB+Y", "LB+B", "RB+X", "RB+Y", "RB+B"};

static bool g_On = false;
static std::string g_Dir;                     // <mod>/item_controls

// ---- slots, names, icons ------------------------------------------------------------------------------------------
static std::atomic<uint32_t> g_Slot[6];
static std::unordered_map<uint32_t, std::string> g_ItemName;
static std::unordered_set<uint32_t> g_Aim;
static std::unordered_map<uint32_t, std::string> g_IconFile;     // item hash -> file; bottle contents: hash ^ 0x80000000
static std::unordered_map<uint32_t, int> g_BottleItem;           // bottle item hash -> bottle index
static uint32_t g_BottleKey[5];
static std::mutex g_SaveMu;

static const char* ItemName(uint32_t h) {
    auto it = g_ItemName.find(h);
    return it == g_ItemName.end() ? "?" : it->second.c_str();
}
static std::string P(const char* file) { return g_Dir + PathSep() + file; }

static void LoadFiles() {
    for (auto& s : g_Slot) s = kHashEmpty;
    char line[256];
    if (FILE* f = fopen(P("items.txt").c_str(), "r")) {
        while (fgets(line, sizeof line, f)) {
            unsigned h; char name[200];
            if (sscanf(line, "%x %199[^\r\n]", &h, name) == 2) g_ItemName[h] = name;
        }
        fclose(f);
    }
    if (FILE* f = fopen(P("aim.txt").c_str(), "r")) {
        while (fgets(line, sizeof line, f)) { unsigned h; if (sscanf(line, "%x", &h) == 1) g_Aim.insert(h); }
        fclose(f);
    }
    if (FILE* f = fopen(P("icons.txt").c_str(), "r")) {
        int nb = 0;
        while (fgets(line, sizeof line, f)) {
            char file[128]; unsigned h, k;
            if (sscanf(line, "item %x %127s", &h, file) == 2) g_IconFile[h] = file;
            else if (sscanf(line, "content %x %127s", &h, file) == 2) g_IconFile[h ^ 0x80000000u] = file;
            else if (sscanf(line, "bottle %x %x", &h, &k) == 2 && nb < 5) { g_BottleItem[h] = nb; g_BottleKey[nb++] = k; }
        }
        fclose(f);
    }
    if (FILE* f = fopen(P("slots.txt").c_str(), "r")) {
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
    std::string tmp = P("slots.txt.tmp"), dst = P("slots.txt");
    if (FILE* f = fopen(tmp.c_str(), "w")) {
        for (int i = 0; i < 6; i++) fprintf(f, "%s %08x %s\n", kSlotName[i], g_Slot[i].load(), ItemName(g_Slot[i]));
        fclose(f);
        remove(dst.c_str());                                  // (Windows rename does not replace)
        rename(tmp.c_str(), dst.c_str());
    }
}

// ---- GameData enum entries ----------------------------------------------------------------------------------------
// Store: key table of {u32 name hash, u32 entry index}, entries of 0x38 bytes (header 0x84c40898, +0x18 default,
// +0x1C current, +0x20 copy). Writing +0x1C/+0x20 changes the value.
static std::atomic<uint64_t> g_EntryChild{0}, g_EntryAdult{0}, g_BottleEntry[5];

static bool EntryValid(uint64_t e, uint32_t def = kHashEmpty) {
    return GuestPtrOk(e) && GRead<uint32_t>(e) == kEnumHeader && GRead<uint32_t>(e + 0x18) == def;
}
static uint64_t ResolveEntry(uint64_t key, uint32_t idx) {
    if (idx > 4096) return 0;
    for (uint64_t a = (key > 0x20000 ? key - 0x20000 : 0) & ~7ULL; a < key + 0x20000; a += 8) {
        if (GRead<uint32_t>(a) != kEnumHeader || GRead<uint32_t>(a - 0x38) == kEnumHeader) continue;
        uint64_t e = a + (uint64_t)idx * 0x38;
        if (EntryValid(e)) return e;
    }
    return 0;
}
static void ScanEntries() {
    uint64_t child = 0, adult = 0;
    ForEachWritableGuestRange([&](uintptr_t lo, uintptr_t hi) {
        for (uintptr_t pg = lo; pg + 4096 <= hi; pg += 4096) {
            const uint32_t* p = (const uint32_t*)pg;
            for (int i = 0; i + 1 < 1024; i += 2) {
                int bottle = -1;
                for (int k = 0; k < 5; k++) if (g_BottleKey[k] && p[i] == g_BottleKey[k]) bottle = k;
                if (p[i] != kHashAssignChild && p[i] != kHashAssignAdult && bottle < 0) continue;
                uint64_t e = ResolveEntry((uint64_t)(p + i) - kGuestBase, p[i + 1]);
                if (!e) continue;
                if (bottle >= 0) g_BottleEntry[bottle] = e;
                else (p[i] == kHashAssignChild ? child : adult) = e;
            }
        }
    });
    if (child && adult && (child != g_EntryChild || adult != g_EntryAdult)) {
        g_EntryChild = child; g_EntryAdult = adult;
        Log("[itemctl] RT item entries: child 0x%llx (%s), adult 0x%llx (%s)\n", (unsigned long long)child,
            ItemName(GRead<uint32_t>(child + 0x1C)), (unsigned long long)adult, ItemName(GRead<uint32_t>(adult + 0x1C)));
    }
}
static bool Entries(uint64_t& c, uint64_t& a) {
    c = g_EntryChild; a = g_EntryAdult;
    return EntryValid(c) && EntryValid(a);
}
static void WriteEntry(uint64_t e, uint32_t v) { GWrite<uint32_t>(e + 0x1C, v); GWrite<uint32_t>(e + 0x20, v); }

// ---- context ---------------------------------------------------------------------------------------------------
// f_802f1380 runs every frame for each visible UI screen (x0 = screen). Screen vtables:
//   HUD in play (also mid-air, throwing): 84c0afe0 (player status), 84c0b710; pause: 84c0baa8 (+ 84c04fb0, 84c08aa0);
//   map: 84c04fb0, 84c08aa0; dialogue: 84c091f8, 84c09a10. f_832b4df0 runs while the ocarina is out.
static GuestFn o_ScreenFrame = nullptr, o_Ocarina = nullptr;
static std::atomic<uint64_t> g_Screen{0}, g_HudScr{0};
static std::atomic<int64_t> g_PauseSeen{0}, g_HudSeen{0}, g_MenuSeen{0}, g_OcarinaSeen{0};

static void ScreenFrameHook(GuestCtx* c) {
    uint64_t s = c->x[0];
    if (GuestPtrOk(s)) {
        uint64_t vt = GRead<uint64_t>(s);
        if (vt == kPauseScreenVt) { g_Screen = s; g_PauseSeen = NowNs(); }
        else if (vt == kHudScreenVt) { g_HudScr = s; g_HudSeen = NowNs(); }
        else if (vt == kHudScreen2Vt) g_HudSeen = NowNs();
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
    uint64_t t = GuestPtrOk(s) ? GRead<uint64_t>(s + 0x198) : 0;
    return GuestPtrOk(t) ? (int)GRead<uint32_t>(t + 0x19c) : -1;
}

// ---- input --------------------------------------------------------------------------------------------------------
// The game reads a 16-sample history (newest first, keyed by sampling number) and derives presses from consecutive
// samples, so each sample is transformed once, in order, and re-reported identically from a cache.
struct Cached { uint64_t n, out; };
static Cached g_Cache[64];
static int g_CacheHead = 0;
static uint64_t g_PrevRaw = 0;
static std::mutex g_InMu;

enum Mode { kIdle, kUse, kAim, kAssign };
static Mode g_Mode = kIdle;
static int g_ModeSlot = -1;
static uint64_t g_ModeFace = 0, g_ModeShoulder = 0, g_SwallowFace = 0;
static int64_t g_ModeT0 = 0, g_AimEnd = 0;
static uint32_t g_PrevChild = 0, g_PrevAdult = 0;
constexpr int64_t kPickUpNs = 34000000, kMinPressNs = 50000000;

static int SlotFor(uint64_t raw, uint64_t pressed) {
    int sh = (raw & kBtnL) ? 0 : (raw & kBtnR) ? 3 : -1;
    if (sh < 0) return -1;
    for (int f = 0; f < 3; f++) if (pressed & kFace[f]) return sh + f;
    return -1;
}

static void StartUse(int slot) {
    uint32_t v = g_Slot[slot];
    g_ModeFace = kFace[slot % 3]; g_ModeShoulder = slot < 3 ? kBtnL : kBtnR;
    g_Mode = g_Aim.count(v) ? kAim : kUse; g_ModeSlot = -1; g_AimEnd = 0;
    uint64_t c, a;
    if (v == kHashEmpty || !Entries(c, a)) { g_Mode = kUse; return; }   // empty slot: swallowed until the face is up
    bool same = GRead<uint32_t>(c + 0x1C) == v && GRead<uint32_t>(a + 0x1C) == v;
    if (!same) { WriteEntry(c, v); WriteEntry(a, v); }
    g_ModeSlot = slot;
    g_ModeT0 = NowNs() - (same ? kPickUpNs : 0);          // a freshly written item: give the game 2 frames to pick it up
}

static void StartAssign(int slot) {
    uint64_t c, a;
    if (!Entries(c, a)) return;
    g_PrevChild = GRead<uint32_t>(c + 0x1C); g_PrevAdult = GRead<uint32_t>(a + 0x1C);
    WriteEntry(c, kHashEmpty); WriteEntry(a, kHashEmpty);
    g_Mode = kAssign; g_ModeSlot = slot; g_ModeT0 = NowNs();
}

static uint64_t Transform(uint64_t raw) {
    const bool play = InGameplay(), pause = PauseOpen();
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
            if (raw & kBtnZR) out |= kBtnA;
            return out;
        }
    }
    if (g_Mode == kAim) {                                 // RT latched: thumbs are free for the camera
        const int64_t zrOn = g_ModeT0 + kPickUpNs;
        if (!g_AimEnd && (!(raw & g_ModeShoulder) || (pressed & g_ModeFace))) {
            g_AimEnd = std::max(now, zrOn + kMinPressNs);
            if (pressed & g_ModeFace) g_SwallowFace = g_ModeFace;
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
            for (int i = 0; i < 6; i++) if (i != g_ModeSlot && g_Slot[i] == got) g_Slot[i] = kHashEmpty;
            Log("[itemctl] assigned %s = %s\n", kSlotName[g_ModeSlot], ItemName(got));
            std::thread(SaveSlots).detach();
            g_Mode = kIdle;
        } else if (now - g_ModeT0 > 400000000) {          // nothing assignable there: put the RT item back
            if (ok) { WriteEntry(c, g_PrevChild); WriteEntry(a, g_PrevAdult); }
            g_Mode = kIdle;
        }
        return out;
    }
    if (pause) {
        out &= ~(kBtnL | kBtnR);                          // no tab switching on LB/RB
        if (raw & (kBtnL | kBtnR)) {
            out &= ~(kBtnX | kBtnY | kBtnA);
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
            out &= ~(kBtnX | kBtnY | (pressed & kBtnA));
        }
        return out;
    }
    return out;
}

// GetNpadStates(state* out, int count, const u32& id) -> count written; NpadState = 0x28 bytes:
// i64 sampling number, u64 buttons, i32 sticks[4], u32 attributes, u32 pad
static void FilterNpad(GuestCtx* c, GuestFn orig) {
    uint64_t outp = c->x[0];
    int count = (int)c->x[1];
    orig(c);
    int got = (int)c->x[0];
    if (!g_On || got <= 0 || got > count || got > 64) return;
    std::lock_guard<std::mutex> lk(g_InMu);
    for (int i = got - 1; i >= 0; i--) {                  // oldest first
        uint64_t e = outp + 0x28 * i;
        uint64_t n = GRead<uint64_t>(e), raw = GRead<uint64_t>(e + 8), out = 0;
        bool hit = false;
        for (auto& k : g_Cache) if (k.n == n && n) { out = k.out; hit = true; break; }
        if (!hit) { out = Transform(raw); g_Cache[g_CacheHead++ & 63] = {n, out}; }
        GWrite<uint64_t>(e + 8, out);
    }
}
// one hook per distinct function behind the five GetNpadStates entries (on Linux they all jump to npad_states)
static GuestFn o_Npad[5];
template <int I> static void NpadHook(GuestCtx* c) { FilterNpad(c, o_Npad[I]); }
static GuestFn kNpadHooks[5] = {NpadHook<0>, NpadHook<1>, NpadHook<2>, NpadHook<3>, NpadHook<4>};

// ---- HUD: two rows of three boxes (top LB, bottom RB), the game's pouch icons --------------------------------------
static std::unordered_map<std::string, ImTextureData*> g_Tex;
static ImTextureData* Texture(const std::string& file) {
    auto it = g_Tex.find(file);
    if (it != g_Tex.end()) return it->second;
    ImTextureData* t = nullptr;
    std::string path = g_Dir + PathSep() + "icons" + PathSep() + file;
    if (FILE* f = fopen(path.c_str(), "rb")) {
        t = IM_NEW(ImTextureData)();
        t->Create(ImTextureFormat_RGBA32, 64, 64);
        if (fread(t->GetPixels(), 1, 64 * 64 * 4, f) == 64 * 64 * 4) { t->UseColors = true; ImGui::RegisterUserTexture(t); }
        else { IM_DELETE(t); t = nullptr; }
        fclose(f);
    }
    return g_Tex[file] = t;
}
static ImTextureData* IconFor(uint32_t item) {
    if (item == kHashEmpty) return nullptr;
    auto b = g_BottleItem.find(item);
    if (b != g_BottleItem.end()) {                        // a bottle shows its contents
        uint64_t e = g_BottleEntry[b->second];
        if (EntryValid(e)) {
            auto it = g_IconFile.find(GRead<uint32_t>(e + 0x1C) ^ 0x80000000u);
            if (it != g_IconFile.end()) return Texture(it->second);
        }
    }
    auto it = g_IconFile.find(item);
    return it == g_IconFile.end() ? nullptr : Texture(it->second);
}

// Shown on the pause menu's Items tab, and in play while the game shows its player-status HUD (screen 84c0afe0,
// +0x1a8 = 1 while shown; cleared by the HUD option Pro outside combat / Hero always, in cutscenes, while paused).
bool HudVisible() {
    if (!g_On) return false;
    if (PauseOpen()) return PauseTab() == kTabItems;
    uint64_t a = g_HudScr;
    return InGameplay() && GuestPtrOk(a) && GRead<uint32_t>(a + 0x1a8) != 0;
}

void DrawHud() {
    if (!HudVisible()) return;
    ImGuiIO& io = ImGui::GetIO();
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const float S = io.DisplaySize.y / 1080.0f;
    const float box = 58 * S, gap = 7 * S, pad = 4.5f * S, round = 10 * S, label = 38 * S;
    const float x1 = io.DisplaySize.x - 52 * S;                        // right edge, in line with the minimap
    const float x0 = x1 - 3 * box - 2 * gap, y0 = 48 * S;              // where the game's RT item box was
    ImFont* font = ImGui::GetFont();
    static const char* face[3] = {"X", "Y", "B"};
    static const ImU32 faceCol[3] = {IM_COL32(64, 132, 255, 255), IM_COL32(255, 196, 36, 255), IM_COL32(236, 64, 52, 255)};
    const int active = (g_Mode == kUse || g_Mode == kAim) ? g_ModeSlot : -1;
    for (int row = 0; row < 2; row++) {
        const float y = y0 + row * (box + gap);
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

// ---- start -------------------------------------------------------------------------------------------------------
bool Init(const std::string& modDir) {
    g_Dir = modDir + PathSep() + "item_controls";
    LoadFiles();
    if (g_ItemName.empty()) { Log("[itemctl] %s/items.txt missing: run the mod manager's setup for this mod\n", g_Dir.c_str()); return false; }
    static const char* kNpad[5] = {"imp__ZN2nn3hid13GetNpadStatesEPNS0_16NpadFullKeyStateEiRKj",
                                   "imp__ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj",
                                   "imp__ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyDualStateEiRKj",
                                   "imp__ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyLeftStateEiRKj",
                                   "imp__ZN2nn3hid13GetNpadStatesEPNS0_17NpadJoyRightStateEiRKj"};
    std::vector<std::string> names(kNpad, kNpad + 5);
    names.push_back("f_802f1380");
    names.push_back("f_832b4df0");
    auto sym = ResolveSymbols(names);
    for (auto& n : names) if (!sym.count(n)) Log("[itemctl] symbol %s not found\n", n.c_str());
    // context hooks first: without them the controls must stay vanilla
    o_ScreenFrame = hook::Install(sym.count("f_802f1380") ? sym["f_802f1380"] : 0, ScreenFrameHook, "screen frame f_802f1380");
    o_Ocarina = hook::Install(sym.count("f_832b4df0") ? sym["f_832b4df0"] : 0, OcarinaHook, "ocarina update f_832b4df0");
    if (!o_ScreenFrame || !o_Ocarina) { Log("[itemctl] context hooks failed: controls stay vanilla\n"); return false; }
    std::vector<uintptr_t> done;
    int nh = 0;
    for (int i = 0; i < 5; i++) {
        if (!sym.count(kNpad[i])) continue;
        uintptr_t t = hook::FollowStubs(sym[kNpad[i]]);
        if (std::find(done.begin(), done.end(), t) != done.end()) continue;
        done.push_back(t);
        if ((o_Npad[nh] = hook::Install(t, kNpadHooks[nh], kNpad[i]))) nh++;
    }
    if (!nh) { Log("[itemctl] controller hook failed: controls stay vanilla\n"); return false; }
    g_On = true;
    std::thread([] {                                      // keep the GameData entries located (they appear with the save)
        for (;;) {
            uint64_t c, a;
            if (!Entries(c, a)) ScanEntries();               // (a full scan takes a moment: only while missing)
            std::this_thread::sleep_for(std::chrono::seconds(3));
        }
    }).detach();
    Log("[itemctl] Item Controls on; slots: %s, %s, %s / %s, %s, %s\n", ItemName(g_Slot[0]), ItemName(g_Slot[1]),
        ItemName(g_Slot[2]), ItemName(g_Slot[3]), ItemName(g_Slot[4]), ItemName(g_Slot[5]));
    return true;
}

}  // namespace itemctl
