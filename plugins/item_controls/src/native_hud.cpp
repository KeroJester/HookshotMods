// The six item boxes as part of the game's own HUD (RESEARCH_NOTES.md sections 69-73 have how each step was found).
//
// 1. While the HUD screen is being built, right after the game builds its RT item box (the part PaItemBtn_00 for the
//    pane Pa_SelectItem_00, through the layout builder's virtual f_80459f90), the same build call is made six more
//    times and each new part (and its nested parts) is added to the screen's layout list (f_8156aa50), as the game's
//    generic builder f_8154f990 does. The screen's open pass then sizes their draw data (text included) and gives
//    them their animation tables like the game's own.
// 2. After the HUD's setup (f_83415d20, x0 = HUD screen), each new part's root pane is attached under the RT box's
//    pane (f_80235dc0), placed in a 2x3 grid, and given an item-button controller made as the HUD makes the RT box's
//    (0x110 bytes, then f_8045efe0). The RT box's own content is hidden (its node at scale 0) and the RT box pane,
//    which the HUD file patch hid, gets its scale back so the six show.
// 3. Every frame of the HUD (f_802ef010, x0 = HUD screen), each box follows its slot: the item through the button's
//    setter f_80a95ef0 (as the Quick Select list), the ammo count as the RT box's update f_80a95770 shows it, and the
//    labels (LB/RB + X/Y/B glyphs from the game's button names, in T_Assign_00).
// Guest functions are called from the hooks on the calling thread (save the context, set x0.., x30 = 0, run the
// lifted function, restore), as Hookshot's host/oot_menu.c guest_call does.
#include "native_hud.h"
#include "hook.h"
#include "item_controls.h"
#include "item_files.h"
#include "platform.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace nativehud {
using namespace plat;

// ---- game addresses (OoT remake, Hookshot v2.0.14 image) -------------------------------------------------------------
constexpr uint64_t kLayoutVt = 0x84c41970;          // a part layout
constexpr uint64_t kItemDb = 0x84c9e0d0;            // ** item database
constexpr uint64_t kCtrlBaseVt = 0x84cb73b0, kCtrlItemVt = 0x84cacb78;   // item-button controller vtables (+0x10)
enum : uint64_t {
    fAlloc = 0x8005ebe0,        // operator new(size, heap, align)
    fBuild = 0x80459f90,        // layout builder: part from (ctx, description) -> layout + 0x40
    fSetup = 0x83415d20,        // HUD screen setup (x0 screen, x1 heap)
    fFrame = 0x802ef010,        // HUD screen per-frame update (x0 screen)
    fRegister = 0x8156aa50,     // add a layout's node to a layout list
    fPaneAt = 0x8045b1f0,       // a layout root's pane by index
    fNode = 0x80229650,         // a layout's tree node
    fParent = 0x80229690,       // a tree node's parent
    fAttach = 0x80235dc0,       // attach a root pane under a pane (container, root, parent)
    fBind = 0x8045efe0,         // bind a controller to its layout (states, icon sub-controller)
    fKind = 0x802efb20,         // item kind from (db, item hash)
    fSetItem = 0x80a95ef0,      // item button: show an item (ctrl, &name, kind, 1, 0, -1, 0)
    fItemRec = 0x805aed10,      // the item's record from &name ([+0xfa] == 1: it shows a count)
    fCount = 0x805b63e0, fMax = 0x805b5cf0, fCountFlag = 0x80a94910,
    fShowCount = 0x80a95df0,    // (ctrl, count, max, flag): the count text and its NumMax/NumMin states
    fPlay = 0x802f0f00, fRewind = 0x802f0fe0,   // play a state forward / back
    fStrInit = 0x80a86f70,      // message string {ptr, s32 len, s64 -1}
    fButtonGlyph = 0x80a8ae50,  // (&str, button): the glyph of a button (Switch names: A 1, B 2, X 3, Y 4, L 5, R 7)
    fSetText = 0x80459520,      // (text pane, &str, 0): the text box keeps the POINTER to the string
};
static const uint64_t kFns[] = {fAlloc, fBuild, fSetup, fFrame, fRegister, fPaneAt, fNode, fParent, fAttach, fBind,
                                fKind, fSetItem, fItemRec, fCount, fMax, fCountFlag, fShowCount, fPlay, fRewind,
                                fStrInit, fButtonGlyph, fSetText};
static std::unordered_map<uint64_t, uintptr_t> g_Fn;   // (filled once in Start, read-only after)
static GuestFn o_Build, o_Setup, o_Frame;

// ---- the boxes ------------------------------------------------------------------------------------------------------
constexpr int kBoxes = 6;
constexpr float kBoxScale = 0.55f, kStep = 230.0f, kRight = -260.0f, kTop = -230.0f;   // in N_ScaleR_00's units
static const int kFace[3] = {4, 3, 1};   // Xbox X, Y, B (the game shows Switch Y, X, A with Xbox labels)
static const int kBumper[2] = {5, 7};    // LB, RB (Switch L, R)

static std::mutex g_Mu;                  // (the build may run on a loading thread, the frames on the game's)
static uint64_t g_BuiltScreen, g_Built[kBoxes];   // made during the screen's build, waiting for its setup
static std::atomic<bool> g_Ready{false};
static std::atomic<int64_t> g_LastFrame{0};
static uint64_t g_Screen;                // the HUD screen the boxes belong to (game thread only from here down)
struct Box {
    uint64_t ctrl = 0, name = 0, label = 0;   // controller; {char*, s32 cap} + buffer; the joined label glyphs
    uint32_t item = 0xffffffff;
    std::string icon;
    int count = -2, max = -2;                 // (-1: no count shown)
    bool labelled = false;
};
static Box g_Box[kBoxes];
static int g_Frames;

static int64_t NowNs() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static uint64_t Q(uint64_t a) { return GuestPtrOk(a) ? GRead<uint64_t>(a) : 0; }
template <class T> static void W(uint64_t a, T v) { GWrite<T>(a, v); }
static std::string GStr(uint64_t a, size_t max = 64) {
    std::string s;
    for (size_t i = 0; GuestPtrOk(a + i) && i < max; i++) {
        char ch = (char)GRead<uint8_t>(a + i);
        if (!ch) break;
        s.push_back(ch);
    }
    return s;
}
static uint64_t Call(GuestCtx* c, uint64_t fn, uint64_t a0 = 0, uint64_t a1 = 0, uint64_t a2 = 0, uint64_t a3 = 0,
                     uint64_t a4 = 0, uint64_t a5 = 0, uint64_t a6 = 0) {
    const GuestCtx saved = *c;
    c->x[0] = a0, c->x[1] = a1, c->x[2] = a2, c->x[3] = a3, c->x[4] = a4, c->x[5] = a5, c->x[6] = a6, c->x[30] = 0;
    ((GuestFn)g_Fn.at(fn))(c);
    const uint64_t r = c->x[0];
    *c = saved;
    return r;
}

// ---- 1. during the screen's build -------------------------------------------------------------------------------------
// A layout and its nested part layouts into the screen's layout list (part panes, flag 0x1800000 at +0xa8: their
// nested root is [[pane+8]+8], its layout at root+0xb0)
static void RegisterTree(GuestCtx* c, uint64_t list, uint64_t lay, int depth = 0) {
    if (Q(lay) != kLayoutVt || depth > 3) return;
    Call(c, fRegister, list, lay + 0x40);
    const uint64_t root = Q(lay + 0x80);
    const int n = GuestPtrOk(root) ? GRead<uint16_t>(root + 0x13c) : 0;
    for (int q = 0; q < n && q < 256; q++) {
        const uint64_t pane = Call(c, fPaneAt, root, q);
        if (!GuestPtrOk(pane) || (GRead<uint32_t>(pane + 0xa8) & 0x3c00000) != 0x1800000) continue;
        const uint64_t nroot = Q(Q(Q(pane + 8) + 8));
        if (GuestPtrOk(nroot) && Q(nroot + 0xb0) != lay) RegisterTree(c, list, Q(nroot + 0xb0), depth + 1);
    }
}

static void BuildHook(GuestCtx* c) {
    const uint64_t ctx = c->x[0], desc = c->x[1];
    uint64_t k[8], d[8];
    for (int i = 0; i < 8; i++) k[i] = Q(ctx + 8 * i), d[i] = Q(desc + 8 * i);
    o_Build(c);
    // the HUD's RT box: part PaItemBtn_00 for the pane Pa_SelectItem_00 (ctx: vt, layout, heap, screen, parent layout,
    // resource; description: part name, len, instance name, len, layout list, 0, 0, 1)
    if (GStr(d[0]) != "PaItemBtn_00" || GStr(d[2]) != "Pa_SelectItem_00" || !GuestPtrOk(d[4])) return;
    g_Ready = false;
    const GuestCtx saved = *c;
    uint64_t built[kBoxes] = {};
    int n = 0;
    for (; n < kBoxes; n++) {
        const uint64_t ctxm = Call(c, fAlloc, 0x40, k[2], 8), descm = Call(c, fAlloc, 0x40, k[2], 8);
        if (!ctxm || !descm) break;
        for (int i = 0; i < 8; i++) { W<uint64_t>(ctxm + 8 * i, k[i]); W<uint64_t>(descm + 8 * i, d[i]); }
        W<uint64_t>(ctxm + 8, 0);                          // (no layout given: the builder makes one)
        c->x[0] = ctxm, c->x[1] = descm, c->x[30] = 0;
        o_Build(c);
        const uint64_t lay = c->x[0] - 0x40;
        *c = saved;
        if (Q(lay) != kLayoutVt || !GuestPtrOk(Q(lay + 0x80))) break;
        RegisterTree(c, d[4], lay);
        built[n] = lay;
    }
    std::lock_guard<std::mutex> lk(g_Mu);
    g_BuiltScreen = n == kBoxes ? k[3] : 0;
    memcpy(g_Built, built, sizeof built);
    Log("[hud] native item boxes: %d of %d built with the HUD\n", n, kBoxes);
}

// ---- 2. after the HUD's setup -----------------------------------------------------------------------------------------
// An item-button controller for a part layout, as the HUD's setup makes the RT box's (f_83415d20 at 0x834160a4)
static uint64_t MakeController(GuestCtx* c, uint64_t heap, uint64_t lay) {
    const uint64_t obj = Call(c, fAlloc, 0x110, heap, 8);
    if (!obj) return 0;
    memset(GPtr(obj), 0, 0x110);
    W<uint64_t>(obj, Q(kCtrlBaseVt) + 0x10);
    const uint64_t sys = Q(lay + 0x28);
    W<uint16_t>(obj + 0x28, 0x100);
    W<uint64_t>(obj + 0x18, sys);
    W<uint64_t>(obj + 0x20, lay);
    const bool alt = GRead<uint8_t>(lay + 0x3a) != 0;
    W<uint8_t>(obj + 0x2a, alt ? 1 : 0);
    const uint64_t head = sys + (alt ? 0x138 : 0x150);   // (the layout system's controller list)
    const uint64_t node = obj + (int64_t)GRead<int32_t>(sys + (alt ? 0x14c : 0x164));
    const uint64_t old = Q(head);
    W<uint64_t>(head, node);
    W<uint64_t>(node, old);
    W<uint64_t>(node + 8, head);
    if (old) W<uint64_t>(old + 8, node);
    const uint64_t cnt = sys + (alt ? 0x148 : 0x160);
    W<uint32_t>(cnt, GRead<uint32_t>(cnt) + 1);
    W<uint64_t>(obj, Q(kCtrlItemVt) + 0x10);
    W<uint64_t>(obj + 0x88, obj + 0x94);                 // item name buffer, cap 0x40
    W<uint32_t>(obj + 0x90, 0x40);
    W<int32_t>(obj + 0xd8, -1);
    W<uint64_t>(obj + 0xf0, obj + 0x100);                // count text buffer, cap 0x10
    W<uint64_t>(obj + 0xf8, 0x1000000000ull);
    Call(c, fBind, obj, heap, 1);
    return obj;
}

static bool IsIdentity(uint64_t n) {
    return GuestPtrOk(n) && GRead<float>(n + 0x60) == 1.0f && GRead<float>(n + 0x64) == 1.0f &&
           GRead<float>(n + 0x6c) == 0.0f && GRead<float>(n + 0x70) == 0.0f;
}

static void SetupHook(GuestCtx* c) {
    const uint64_t hud = c->x[0], heap = c->x[1];
    g_Ready = false;
    o_Setup(c);
    uint64_t built[kBoxes];
    {
        std::lock_guard<std::mutex> lk(g_Mu);
        if (g_BuiltScreen != hud) return;                 // (not this screen's: overlay)
        memcpy(built, g_Built, sizeof built);
        g_BuiltScreen = 0;
    }
    // everything checked before anything is changed: on a surprise the RT box stays hidden and the overlay is used
    const uint64_t rt = Q(hud + 0x328), tmpl = Q(rt + 0x20);
    if (Q(tmpl) != kLayoutVt) { Log("[hud] native boxes: the RT box's layout not found\n"); return; }
    const uint64_t tnode = Call(c, fNode, tmpl + 0x40), holder = Call(c, fParent, tnode);
    if (!IsIdentity(tnode) || !GuestPtrOk(holder) || !GuestPtrOk(Q(holder + 0x18))) { Log("[hud] native boxes: unexpected RT box tree\n"); return; }
    float hs = GRead<float>(holder + 0x60);
    if (hs == 0.0f) hs = itemctl::OriginalBoxScale();     // (hidden by the HUD file patch: its own scale comes back)
    if (!(hs > 0.05f && hs < 10.0f)) { Log("[hud] native boxes: RT box scale %g unknown\n", hs); return; }
    for (uint64_t lay : built)
        if (Q(lay) != kLayoutVt || !GuestPtrOk(Q(lay + 0x80))) { Log("[hud] native boxes: a box went missing\n"); return; }
    const float hx = GRead<float>(holder + 0x6c), hy = GRead<float>(holder + 0x70);
    for (int i = 0; i < kBoxes; i++) {
        const uint64_t lay = built[i];
        Call(c, fAttach, Q(holder + 0x18), Q(lay + 0x80), holder);
        const uint64_t node = Call(c, fNode, lay + 0x40);
        if (IsIdentity(node)) {   // (in the RT box pane's units)
            const float x = kRight - (2 - i % 3) * kStep, y = kTop - (i / 3) * kStep;
            W<float>(node + 0x60, kBoxScale / hs); W<float>(node + 0x64, kBoxScale / hs);
            W<float>(node + 0x6c, (x - hx) / hs); W<float>(node + 0x70, (y - hy) / hs);
        }
        Box& b = g_Box[i];
        b = Box();
        b.ctrl = MakeController(c, heap, lay);
        b.name = Call(c, fAlloc, 0x50, heap, 8);
        b.label = Call(c, fAlloc, 0x20, heap, 8);
        if (b.name) { W<uint64_t>(b.name, b.name + 0x10); W<int32_t>(b.name + 8, 0x40); W<uint8_t>(b.name + 0x10, 0); }
    }
    W<float>(tnode + 0x60, 0.0f); W<float>(tnode + 0x64, 0.0f);   // the RT box's own content hidden
    W<float>(holder + 0x60, hs); W<float>(holder + 0x64, hs);     // its pane shown: the six live under it
    for (auto& b : g_Box) if (!b.ctrl || !b.name || !b.label) { Log("[hud] native boxes: allocation failed\n"); return; }
    g_Screen = hud;
    g_Frames = 0;
    g_Ready = true;
    Log("[hud] native item boxes set up (RT box scale %g)\n", hs);
}

// ---- 3. every HUD frame -----------------------------------------------------------------------------------------------
// Scratch memory below the guest stack for a call's arguments (the calls then run below it)
struct Scratch {
    GuestCtx* c;
    uint64_t at;
    explicit Scratch(GuestCtx* cc) : c(cc), at(cc->x[31] - 0x100) { c->x[31] -= 0x400; }
    ~Scratch() { c->x[31] += 0x400; }
};

static void SetLabel(GuestCtx* c, Box& b, int bumper, int face) {
    Scratch s(c);
    const uint64_t str = s.at;
    std::string glyphs;
    for (int button : {bumper, face}) {
        Call(c, fStrInit, str);
        if (!Call(c, fButtonGlyph, str, (uint64_t)button)) return;
        const uint64_t p = Q(str);
        for (int k = 0, n = GRead<int32_t>(str + 8); k < n && k < 12 && GuestPtrOk(p + k); k++) glyphs.push_back((char)GRead<uint8_t>(p + k));
    }
    if (glyphs.empty() || glyphs.size() > 0x1f) return;
    memcpy(GPtr(b.label), glyphs.data(), glyphs.size());   // (kept: the text box points at it)
    W<uint8_t>(b.label + glyphs.size(), 0);
    W<uint64_t>(str, b.label);
    W<int32_t>(str + 8, (int32_t)glyphs.size());
    // T_Assign_00 is the box's text pane 14 (its source text is no "@name" tag, so the by-name setters skip it)
    const uint64_t root = Q(Q(b.ctrl + 0x20) + 0x80), pane = Call(c, fPaneAt, root, 14);
    if (GuestPtrOk(pane) && (GRead<uint32_t>(pane + 0xa8) & 0x3c00000) == 0x800000) {
        Call(c, fSetText, pane, str, 0);
        b.labelled = true;
    }
}

static void UpdateBox(GuestCtx* c, int i, bool counts) {
    Box& b = g_Box[i];
    const uint32_t item = itemctl::SlotItem(i);
    const std::string icon = itemctl::SlotIcon(i);
    const uint64_t db = Q(Q(kItemDb));
    const bool changed = item != b.item || icon != b.icon;
    if (changed) {
        snprintf((char*)GPtr(b.name + 0x10), 0x40, "%s", icon.c_str());
        const uint64_t kind = Call(c, fKind, db, item);
        Call(c, fSetItem, b.ctrl, b.name, kind, 1, 0, (uint64_t)(int64_t)-1, 0);
        b.item = item, b.icon = icon;
    }
    if (!changed && !counts) return;
    int count = -1, max = -1;
    uint64_t flag = 0;
    const uint64_t rec = icon.empty() ? 0 : Call(c, fItemRec, b.name);
    if (GuestPtrOk(rec) && GRead<uint8_t>(rec + 0xfa) == 1) {
        count = (int)(uint32_t)Call(c, fCount, db, b.name, 1, 0);
        max = (int)(uint32_t)Call(c, fMax, db, b.name, 0, 0);
        flag = Call(c, fCountFlag, db, b.name) & 1;
    }
    if (count == b.count && max == b.max && !changed) return;
    if (count >= 0) {
        Call(c, fShowCount, b.ctrl, (uint32_t)count, (uint32_t)max, flag);
        Call(c, fPlay, Q(b.ctrl + 0x30));                  // (StockNum shown)
    } else if (b.count != -1) {
        Call(c, fRewind, Q(b.ctrl + 0x30));                // (hidden)
    }
    b.count = count, b.max = max;
}

static void FrameHook(GuestCtx* c) {
    const uint64_t screen = c->x[0];
    o_Frame(c);
    if (!g_Ready || screen != g_Screen) return;
    g_LastFrame = NowNs();
    const bool counts = (g_Frames++ % 10) == 0;            // (counts checked six times a second, items every frame)
    for (int i = 0; i < kBoxes; i++) {
        if (!g_Box[i].labelled) SetLabel(c, g_Box[i], kBumper[i / 3], kFace[i % 3]);
        UpdateBox(c, i, counts);
    }
}

// ---- start ------------------------------------------------------------------------------------------------------------
bool Active() { return g_Ready && NowNs() - g_LastFrame.load() < 500000000ll; }

bool Start() {
    const char* off = getenv("ITEMCTL_NATIVE");
    if (off && !strcmp(off, "0")) { Log("[hud] native boxes off (ITEMCTL_NATIVE=0): overlay HUD\n"); return false; }
    const char* r = getenv("ITEMCTL_RESEARCH");             // (the research probes hook the same functions)
    if (r && (strstr(r, "clone") || strstr(r, "build") || strstr(r, "early"))) return false;
    std::vector<std::string> names;
    char n[32];
    for (uint64_t a : kFns) { snprintf(n, sizeof n, "f_%llx", (unsigned long long)a); names.push_back(n); }
    auto sym = ResolveSymbols(names);
    for (size_t i = 0; i < names.size(); i++) {
        if (!sym.count(names[i])) { Log("[hud] %s not found: overlay HUD\n", names[i].c_str()); return false; }
        g_Fn[kFns[i]] = sym[names[i]];
    }
    // the frame and setup hooks first, the builder last: boxes are only built once everything that uses them is in
    o_Frame = hook::Install(g_Fn[fFrame], FrameHook, "HUD frame f_802ef010");
    o_Setup = o_Frame ? hook::Install(g_Fn[fSetup], SetupHook, "HUD setup f_83415d20") : nullptr;
    o_Build = o_Setup ? hook::Install(g_Fn[fBuild], BuildHook, "layout builder f_80459f90") : nullptr;
    if (!o_Build) { Log("[hud] hooks failed: overlay HUD\n"); return false; }
    Log("[hud] native item boxes on\n");
    return true;
}

}  // namespace nativehud
