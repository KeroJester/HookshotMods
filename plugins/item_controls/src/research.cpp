// Research probes for the native HUD boxes (RESEARCH_NOTES.md section 69). Off unless ITEMCTL_RESEARCH is set in the
// game's environment; never part of normal play.
//   ITEMCTL_RESEARCH=panes   log the game's find-pane-by-name calls (f_8045c4a0(name, root, &out)) for part panes
//                            ("Pa_..."): the result, its vtable, the caller
//   ITEMCTL_RESEARCH=trace   count calls of every guest function in ITEMCTL_TRACE ("lo-hi,lo-hi" hex ranges, function
//                            starts from the f_ symbols) and log the counts every 250 ms (at most 1024 functions)
//   ITEMCTL_RESEARCH=watch   log every change in the HUD's RT box controller ([screen+0x328], 0x110 bytes) and its icon
//                            sub-controller ([ctrl+0x80], 0x40 bytes), polled every 100 ms
//   ITEMCTL_RESEARCH=copy    hook f_8575cdc0 (the copy that writes the RT box's icon name, found by a data breakpoint)
//                            and log its callers when the destination is inside the RT box controller
//   ITEMCTL_RESEARCH=builder log the layout builder's part creations (f_80459f90, a virtual: x0 builder context,
//                            x1 part description; returns the new layout + 0x40): caller, name, the description
//   ITEMCTL_RESEARCH=clone   after the HUD's setup (f_83415d20), clone the RT box part as the dungeon map clones its
//                            icons (f_80f9b1d0): f_80791f70(new layout, template, name, 0, 0), then
//                            f_80235dc0(parent's container, new root pane, parent pane); logs what it finds
#include "research.h"
#include "item_controls.h"
#include "hook.h"
#include "platform.h"
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <cstdio>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <thread>
#include <utility>
#include <vector>

namespace research {
using namespace plat;

static GuestFn o_FindPane, o_HudSetup, o_RtUpdate;
static std::atomic<int> g_RtCalls{0};
static uint64_t g_SlotCtrl[6], g_SlotName[6];   // per box: its item-button controller; a {char*, s32 cap} string + buffer
static std::string g_SlotShown[6];
static std::unordered_map<std::string, uintptr_t> g_Sym;

// a guest function called from a hook on the game's thread, as Hookshot's host/oot_menu.c guest_call: the context saved,
// x0.. set, x30 = 0, the lifted function run, the context restored; returns x0
static uint64_t Call(GuestCtx* c, const char* fn, uint64_t a0 = 0, uint64_t a1 = 0, uint64_t a2 = 0, uint64_t a3 = 0,
                     uint64_t a4 = 0, uint64_t a5 = 0, uint64_t a6 = 0) {
    auto it = g_Sym.find(fn);
    if (it == g_Sym.end()) { Log("[research] %s not resolved\n", fn); return 0; }
    const GuestCtx saved = *c;
    c->x[0] = a0, c->x[1] = a1, c->x[2] = a2, c->x[3] = a3, c->x[4] = a4, c->x[5] = a5, c->x[6] = a6, c->x[30] = 0;
    ((GuestFn)it->second)(c);
    const uint64_t r = c->x[0];
    *c = saved;
    return r;
}
static uint64_t Q(uint64_t a) { return GuestPtrOk(a) ? GRead<uint64_t>(a) : 0; }

static std::string GStr(uint64_t a, size_t max = 96) {
    std::string s;
    if (!GuestPtrOk(a)) return s;
    for (size_t i = 0; i < max; i++) {
        char ch = (char)GRead<uint8_t>(a + i);
        if (!ch) break;
        s.push_back(ch);
    }
    return s;
}

static void FindPaneHook(GuestCtx* c) {
    const uint64_t name = c->x[0], root = c->x[1], outp = c->x[2], lr = c->x[30];
    o_FindPane(c);
    std::string n = GStr(name);
    if (n.compare(0, 3, "Pa_") != 0) return;
    const uint64_t r = c->x[0], out = GuestPtrOk(outp) ? GRead<uint64_t>(outp) : 0;
    const uint64_t vt = GuestPtrOk(r) ? GRead<uint64_t>(r) : 0;
    const uint64_t outvt = GuestPtrOk(out) ? GRead<uint64_t>(out) : 0;
    Log("[research] find %s root %#llx -> %#llx (vt %#llx) out %#llx (vt %#llx) from %#llx\n", n.c_str(),
        (unsigned long long)root, (unsigned long long)r, (unsigned long long)vt, (unsigned long long)out,
        (unsigned long long)outvt, (unsigned long long)lr);
}

static std::string PaneName(uint64_t pane) {   // (the layouts' pane lists compare names at +0x148: f_83415d20)
    return GStr(Q(pane + 0x148), 48);
}

static void DumpFloats(const char* what, uint64_t a, size_t n) {
    std::string line;
    char b[64];
    for (size_t o = 0; o < n; o += 4) {
        float f = GRead<float>(a + o);
        uint32_t u = GRead<uint32_t>(a + o);
        if (f != 0.0f && f > -5000.0f && f < 5000.0f && (u & 0x7f800000) != 0) { snprintf(b, sizeof b, " +%#zx=%g", o, f); line += b; }
    }
    Log("[research] %s %#llx floats:%s\n", what, (unsigned long long)a, line.c_str());
}

static std::atomic<uint64_t> g_Hud{0};
static GuestFn o_Copy;
static void CopyHook(GuestCtx* c) {
    const uint64_t dst = c->x[0], src = c->x[1], n = c->x[2], lr = c->x[30];
    const uint64_t ctrl = Q(g_Hud + 0x328);
    if (ctrl && dst >= ctrl && dst < ctrl + 0x110) {
        static int logged;
        if (logged++ < 40)
            Log("[research] copy to ctrl+%#llx from %#llx '%s' n %llu, called from %#llx (x19 %#llx x20 %#llx x21 %#llx)\n",
                (unsigned long long)(dst - ctrl), (unsigned long long)src, GStr(src, 40).c_str(), (unsigned long long)n,
                (unsigned long long)lr, (unsigned long long)c->x[19], (unsigned long long)c->x[20], (unsigned long long)c->x[21]);
    }
    o_Copy(c);
}

static GuestFn o_Build;
static void BuildHook(GuestCtx* c) {
    const uint64_t ctx = c->x[0], desc = c->x[1], lr = c->x[30];
    uint64_t d[8], k[8];
    for (int i = 0; i < 8; i++) d[i] = Q(desc + 8 * i), k[i] = Q(ctx + 8 * i);
    o_Build(c);
    const uint64_t lay = c->x[0] - 0x40;
    static int n;
    if (n++ < 400)
        Log("[research] build %s from %#llx: lay %#llx ctx[%llx %llx %llx %llx %llx %llx %llx] desc[%llx %llx %llx %llx %llx %llx %llx %llx] anims %u\n",
            PaneName(Q(lay + 0x80)).c_str(), (unsigned long long)lr, (unsigned long long)lay,
            (unsigned long long)k[0], (unsigned long long)k[1], (unsigned long long)k[2], (unsigned long long)k[3],
            (unsigned long long)k[4], (unsigned long long)k[5], (unsigned long long)k[6],
            (unsigned long long)d[0], (unsigned long long)d[1], (unsigned long long)d[2], (unsigned long long)d[3],
            (unsigned long long)d[4], (unsigned long long)d[5], (unsigned long long)d[6], (unsigned long long)d[7],
            GRead<uint32_t>(lay + 0x88));
}

static void WatchThread() {
    std::vector<uint8_t> prev;
    for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        const uint64_t ctrl = Q(g_Hud + 0x328), icon = Q(ctrl + 0x80);
        if (!GuestPtrOk(ctrl) || !GuestPtrOk(icon)) continue;
        std::vector<uint8_t> cur(0x150);
        memcpy(cur.data(), GPtr(ctrl), 0x110);
        memcpy(cur.data() + 0x110, GPtr(icon), 0x40);
        if (!prev.empty()) {
            std::string line; char b[96];
            for (size_t o = 0; o < cur.size(); o += 4) {
                uint32_t a, z; memcpy(&a, &prev[o], 4); memcpy(&z, &cur[o], 4);
                if (a != z) { snprintf(b, sizeof b, " %s+%#zx %08x->%08x", o < 0x110 ? "ctrl" : "icon", o < 0x110 ? o : o - 0x110, a, z); line += b; }
            }
            if (!line.empty()) Log("[watch %.2f] ctrl %#llx icon %#llx:%s\n", NowNs() / 1e9, (unsigned long long)ctrl, (unsigned long long)icon, line.c_str());
        }
        prev = cur;
    }
}

template <class T> static void W(uint64_t a, T v) { GWrite<T>(a, v); }

// An item-button controller for a part layout, made as the HUD's setup makes the RT box's (f_83415d20 at 0x834160a4):
// 0x110 bytes, the base vtable then the item button's (0x84c01400), linked into the layout system's controller list,
// then bound by f_8045efe0(ctrl, heap, 1) (its states, its icon sub-controller at +0x80)
static uint64_t MakeItemButton(GuestCtx* c, uint64_t heap, uint64_t lay) {
    const uint64_t obj = Call(c, "f_8005ebe0", 0x110, heap, 8);
    if (!obj) return 0;
    memset(GPtr(obj), 0, 0x110);
    W<uint64_t>(obj, Q(0x84cb73b0) + 0x10);
    const uint64_t sys = Q(lay + 0x28);
    W<uint16_t>(obj + 0x28, 0x100);
    W<uint64_t>(obj + 0x18, sys);
    W<uint64_t>(obj + 0x20, lay);
    const bool alt = GRead<uint8_t>(lay + 0x3a) != 0;
    W<uint8_t>(obj + 0x2a, alt ? 1 : 0);
    const uint64_t head = sys + (alt ? 0x138 : 0x150);
    const uint64_t node = obj + (int64_t)GRead<int32_t>(sys + (alt ? 0x14c : 0x164));
    const uint64_t old = Q(head);
    W<uint64_t>(head, node);
    W<uint64_t>(node, old);
    W<uint64_t>(node + 8, head);
    if (old) W<uint64_t>(old + 8, node);
    const uint64_t cnt = sys + (alt ? 0x148 : 0x160);
    W<uint32_t>(cnt, GRead<uint32_t>(cnt) + 1);
    W<uint64_t>(obj, Q(0x84cacb78) + 0x10);
    W<uint64_t>(obj + 0x88, obj + 0x94);
    W<uint32_t>(obj + 0x90, 0x40);
    W<int32_t>(obj + 0xd8, -1);
    W<uint64_t>(obj + 0xf0, obj + 0x100);
    W<uint64_t>(obj + 0xf8, 0x1000000000ull);
    Call(c, "f_8045efe0", obj, heap, 1);
    return obj;
}

// The RT box's update (f_80a95770, x0 = the HUD screen): after it, each box shows its slot's item through the item
// button's setter f_80a95ef0(ctrl, &name, kind, 1, 0, -1, 0), the call the Quick Select list makes; the kind is
// f_802efb20(item database **0x84c9e0d0, item hash)
static void RtUpdateHook(GuestCtx* c) {
    o_RtUpdate(c);
    static int calls;
    calls++;
    g_RtCalls = calls;
    const uint64_t db = Q(Q(0x84c9e0d0));
    for (int i = 0; i < 6; i++) {
        if (!g_SlotCtrl[i] || !g_SlotName[i]) continue;
        const std::string icon = itemctl::SlotIcon(i);
        if (icon == g_SlotShown[i] && calls % 30) continue;   // (now and then anyway: the counts)
        const uint32_t hash = itemctl::SlotItem(i);
        const uint64_t kind = Call(c, "f_802efb20", db, hash);
        char* buf = (char*)GPtr(g_SlotName[i] + 0x10);
        snprintf(buf, 0x40, "%s", icon.c_str());
        Call(c, "f_80a95ef0", g_SlotCtrl[i], g_SlotName[i], kind, 1, 0, (uint64_t)(int64_t)-1, 0);
        if (icon != g_SlotShown[i]) Log("[research] box %d: %s (kind %d)\n", i, icon.empty() ? "-" : icon.c_str(), (int)kind);
        if (false) {
            const uint64_t rt = Q(GRead<uint64_t>(c->x[0]) ? c->x[0] + 0x328 : 0);
            const uint64_t a = Q(c->x[0] + 0x328), b = g_SlotCtrl[0];
            std::string line; char t[96];
            for (size_t o = 0; o < 0x110; o += 4) {
                uint32_t x = GRead<uint32_t>(a + o), y = GRead<uint32_t>(b + o);
                if (x != y) { snprintf(t, sizeof t, " +%#zx %08x|%08x", o, x, y); line += t; }
            }
            Log("[research] RT ctrl %#llx vs box 0 %#llx:%s\n", (unsigned long long)a, (unsigned long long)b, line.c_str());
            const uint64_t ia = Q(a + 0x80), ib = Q(b + 0x80);
            line.clear();
            for (size_t o = 0; o < 0x40; o += 4) { snprintf(t, sizeof t, " +%#zx %08x|%08x", o, GRead<uint32_t>(ia + o), GRead<uint32_t>(ib + o)); line += t; }
            Log("[research] icon sub %#llx vs %#llx:%s\n", (unsigned long long)ia, (unsigned long long)ib, line.c_str());
            (void)rt;
        }
        g_SlotShown[i] = icon;
    }
}

static void DiffLater() {   // (10 s after the HUD's setup: the RT box's controller and box 0's side by side)
    std::this_thread::sleep_for(std::chrono::seconds(10));
    const uint64_t a = Q(g_Hud + 0x328), b = g_SlotCtrl[0];
    if (!a || !b) return;
    std::string line; char t[96];
    for (size_t o = 0; o < 0x110; o += 4) {
        uint32_t x = GRead<uint32_t>(a + o), y = GRead<uint32_t>(b + o);
        if (x != y) { snprintf(t, sizeof t, " +%#zx %08x|%08x", o, x, y); line += t; }
    }
    Log("[research] RT update calls so far %d; RT ctrl %#llx vs box 0 %#llx:%s\n", g_RtCalls.load(), (unsigned long long)a,
        (unsigned long long)b, line.c_str());
    const uint64_t ia = Q(a + 0x80), ib = Q(b + 0x80);
    line.clear();
    for (size_t o = 0; o < 0x40; o += 4) { snprintf(t, sizeof t, " +%#zx %08x|%08x", o, GRead<uint32_t>(ia + o), GRead<uint32_t>(ib + o)); line += t; }
    Log("[research] icon sub %#llx vs %#llx:%s\n", (unsigned long long)ia, (unsigned long long)ib, line.c_str());
}

static void HudSetupHook(GuestCtx* c) {
    const uint64_t hud = c->x[0], heap = c->x[1];
    o_HudSetup(c);
    g_Hud = hud;
    if (strstr(getenv("ITEMCTL_RESEARCH"), "copy") && !o_Copy) {
        auto cs = ResolveSymbols({"f_8575cdc0"});
        if (cs.count("f_8575cdc0")) o_Copy = hook::Install(cs["f_8575cdc0"], CopyHook, "research: copy f_8575cdc0");
    }
    if (strstr(getenv("ITEMCTL_RESEARCH"), "watch")) {
        static bool started;
        if (!started) { started = true; std::thread(WatchThread).detach(); }
    }
    if (!strstr(getenv("ITEMCTL_RESEARCH"), "clone")) return;
    static int done;
    if (done++) return;
    const uint64_t ctrl = Q(hud + 0x328), tmpl = Q(ctrl + 0x20);
    Log("[research] HUD setup: screen %#llx (vt %#llx) heap %#llx; +0x20 %#llx (vt %#llx) +0x28 %#llx (vt %#llx); "
        "RT box controller %#llx (vt %#llx) template layout %#llx (vt %#llx)\n",
        (unsigned long long)hud, (unsigned long long)Q(hud), (unsigned long long)heap,
        (unsigned long long)Q(hud + 0x20), (unsigned long long)Q(Q(hud + 0x20)), (unsigned long long)Q(hud + 0x28),
        (unsigned long long)Q(Q(hud + 0x28)), (unsigned long long)ctrl, (unsigned long long)Q(ctrl),
        (unsigned long long)tmpl, (unsigned long long)Q(tmpl));
    if (Q(tmpl) != 0x84c41970) { Log("[research] template is not a layout: stop\n"); return; }
    const uint64_t root = Q(tmpl + 0x80);
    const uint64_t parent = Call(c, "f_80229690", Call(c, "f_80229650", tmpl + 0x40));
    Log("[research] template root pane %#llx '%s' (vt %#llx); parent pane %#llx '%s' (vt %#llx), its container %#llx\n",
        (unsigned long long)root, PaneName(root).c_str(), (unsigned long long)Q(root), (unsigned long long)parent,
        PaneName(parent).c_str(), (unsigned long long)Q(parent), (unsigned long long)Q(parent + 0x18));
    DumpFloats("template root", root, 0x400);
    {   // where the box's translation (-294, -258) and scale 0.85 live: the pane and the objects it points to
        const float want[3] = {-294.0f, -258.0f, 0.85f};
        for (size_t po = 0; po < 0x200; po += 8) {
            uint64_t base = po ? Q(root + po) : root;
            if (!GuestPtrOk(base)) continue;
            for (size_t o = 0; o < 0x400; o += 4)
                for (float w : want)
                    if (GRead<float>(base + o) == w)
                        Log("[research] %g at %s+%#zx (+%#zx)\n", w, po ? "[pane+..]" : "pane", po, o);
        }
    }
    {   // the pointers in the pane, for its structure
        std::string line; char b[64];
        for (size_t o = 0; o < 0x200; o += 8) { uint64_t v = Q(root + o); if (v) { snprintf(b, sizeof b, " +%#zx=%#llx", o, (unsigned long long)v); line += b; } }
        Log("[research] template root qwords:%s\n", line.c_str());
    }
    // The clones stay children of the template's holder (the HUD's Pa_SelectItem_00 pane: translation +0x6c/+0x70,
    // scale +0x60/+0x64), as the game's own clones do (attached elsewhere, the UI's draw crashed). The template's own
    // content (its part's root pane) is hidden instead, at scale 0; the clones are placed in the holder's units.
    const uint64_t holder = parent;
    const float hx = GRead<float>(holder + 0x6c), hy = GRead<float>(holder + 0x70), hs = GRead<float>(holder + 0x60);
    Log("[research] holder %#llx at (%g, %g) scale %g\n", (unsigned long long)holder, hx, hy, hs);
    if (!(hs > 0.0f)) { Log("[research] holder hidden (the HUD file patch?): run with keepbox\n"); return; }
    // the tree nodes (the kind the holder is) carry the transforms; a layout's own node is f_80229650(layout + 0x40)
    auto isNode = [](uint64_t n) {
        return GuestPtrOk(n) && GRead<float>(n + 0x60) == 1.0f && GRead<float>(n + 0x64) == 1.0f &&
               GRead<float>(n + 0x6c) == 0.0f && GRead<float>(n + 0x70) == 0.0f;
    };
    const uint64_t tnode = Call(c, "f_80229650", tmpl + 0x40);
    Log("[research] template node %#llx: scale %g, %g at (%g, %g)\n", (unsigned long long)tnode,
        GRead<float>(tnode + 0x60), GRead<float>(tnode + 0x64), GRead<float>(tnode + 0x6c), GRead<float>(tnode + 0x70));
    if (!isNode(tnode)) { Log("[research] the template node is not an identity transform: stop\n"); return; }
    for (int i = 0; i < 6; i++) {
        const uint64_t name = Call(c, "f_8005ebe0", 32, heap, 8);
        const uint64_t lay = Call(c, "f_8005ebe0", 0x98, heap, 8);
        if (!name || !lay) { Log("[research] allocation failed\n"); return; }
        char nm[32];
        snprintf(nm, sizeof nm, "Pa_ItemSlot_%02d", i);
        memcpy(GPtr(name), nm, strlen(nm) + 1);
        Call(c, "f_80791f70", lay, tmpl, name, 0, 0);
        const uint64_t nroot = Q(lay + 0x80);
        if (!GuestPtrOk(nroot)) { Log("[research] clone %d: no root pane\n", i); return; }
        const float sc = 0.55f, step = 230.0f;                       // (wanted, in N_ScaleR_00's units)
        const float x = -260.0f - (2 - i % 3) * step, y = -230.0f - (i / 3) * step;
        Call(c, "f_80235dc0", Q(holder + 0x18), nroot, holder);
        const uint64_t node = Call(c, "f_80229650", lay + 0x40);
        if (!isNode(node)) { Log("[research] clone %d: node %#llx is not an identity transform: left as it is\n", i, (unsigned long long)node); continue; }
        GWrite<float>(node + 0x60, sc / hs); GWrite<float>(node + 0x64, sc / hs);
        GWrite<float>(node + 0x6c, (x - hx) / hs); GWrite<float>(node + 0x70, (y - hy) / hs);
        Log("[research] clone %d '%s' node %#llx at (%g, %g) scale %g in the holder\n", i, PaneName(nroot).c_str(),
            (unsigned long long)node, GRead<float>(node + 0x6c), GRead<float>(node + 0x70), GRead<float>(node + 0x60));
        if (strstr(getenv("ITEMCTL_RESEARCH"), "shareanims")) {   // (experiment: the template's animation table)
            W<uint32_t>(lay + 0x88, GRead<uint32_t>(tmpl + 0x88));
            W<uint64_t>(lay + 0x90, Q(tmpl + 0x90));
        }
        g_SlotCtrl[i] = MakeItemButton(c, heap, lay);
        g_SlotName[i] = Call(c, "f_8005ebe0", 0x50, heap, 8);
        if (g_SlotName[i]) { W<uint64_t>(g_SlotName[i], g_SlotName[i] + 0x10); W<int32_t>(g_SlotName[i] + 8, 0x40); *(char*)GPtr(g_SlotName[i] + 0x10) = 0; }
        Log("[research] clone %d controller %#llx (vt %#llx), icon sub-controller %#llx\n", i, (unsigned long long)g_SlotCtrl[i],
            (unsigned long long)Q(g_SlotCtrl[i]), (unsigned long long)Q(g_SlotCtrl[i] + 0x80));
    }
    GWrite<float>(tnode + 0x60, 0.0f); GWrite<float>(tnode + 0x64, 0.0f);   // (the template's content hidden)
    Log("[research] six clones attached under the holder; the template's content hidden\n");
    std::thread(DiffLater).detach();
}

// ---- call counter ---------------------------------------------------------------------------------------------------
constexpr int kMaxCount = 1024;
static uint32_t g_CntAddr[kMaxCount];
static GuestFn g_CntOrig[kMaxCount];
static std::atomic<uint64_t> g_Cnt[kMaxCount];
static int g_CntN;
template <int I> static void CountHook(GuestCtx* c) { g_Cnt[I]++; g_CntOrig[I](c); }
template <int... I> static GuestFn CountStub(int i, std::integer_sequence<int, I...>) {
    static const GuestFn stubs[] = {CountHook<I>...};
    return stubs[i];
}

static void StartTrace() {
    const char* spec = getenv("ITEMCTL_TRACE");
    if (!spec) { Log("[research] trace: ITEMCTL_TRACE not set\n"); return; }
    std::vector<std::pair<uint64_t, uint64_t>> ranges;
    for (const char* p = spec; *p;) {
        unsigned long long lo = 0, hi = 0; int n = 0;
        if (sscanf(p, "%llx-%llx%n", &lo, &hi, &n) != 2) break;
        ranges.push_back({lo, hi});
        p += n;
        if (*p == ',') p++;
    }
    // every f_ symbol inside the ranges: resolve all candidate names
    std::vector<std::string> names;
    for (auto& r : ranges)
        for (uint64_t a = r.first; a < r.second; a += 4) { char b[24]; snprintf(b, sizeof b, "f_%llx", (unsigned long long)a); names.push_back(b); }
    auto sym = ResolveSymbols(names);
    std::vector<std::pair<uint64_t, uintptr_t>> fns;
    for (auto& kv : sym) fns.push_back({strtoull(kv.first.c_str() + 2, nullptr, 16), kv.second});
    std::sort(fns.begin(), fns.end());
    // (a 14-byte hook jump must fit: tiny functions, e.g. a load and a return, would spill into the next function)
    std::vector<uintptr_t> hosts;
    for (auto& f : fns) hosts.push_back(f.second);
    std::sort(hosts.begin(), hosts.end());
    int skipped = 0;
    for (size_t k = 0; k < fns.size(); k++) {
        auto& f = fns[k];
        const uint64_t gsize = k + 1 < fns.size() ? fns[k + 1].first - f.first : 0x1000;
        auto h = std::upper_bound(hosts.begin(), hosts.end(), f.second);
        const uintptr_t hsize = h == hosts.end() ? 0x1000 : *h - f.second;
        if (gsize < 0x40 || hsize < 0x20) { skipped++; continue; }
        if (g_CntN >= kMaxCount) { Log("[research] trace: more than %d functions, the rest left out\n", kMaxCount); break; }
        int i = g_CntN;
        GuestFn o = hook::Install(f.second, CountStub(i, std::make_integer_sequence<int, kMaxCount>{}), "research: count");
        if (!o) continue;
        g_CntAddr[i] = (uint32_t)f.first, g_CntOrig[i] = o;
        g_CntN++;
    }
    Log("[research] trace: counting %d functions (%d small ones left out)\n", g_CntN, skipped);
    std::thread([] {
        uint64_t last[kMaxCount] = {};
        const int64_t t0 = NowNs();
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            std::string line; char b[48];
            for (int i = 0; i < g_CntN; i++) {
                uint64_t v = g_Cnt[i].load();
                if (v != last[i]) { snprintf(b, sizeof b, " %08x:%llu", g_CntAddr[i], (unsigned long long)(v - last[i])); line += b; }
                last[i] = v;
            }
            if (!line.empty()) Log("[trace %.2f]%s\n", (NowNs() - t0) / 1e9, line.c_str());
        }
    }).detach();
}

void Start() {
    const char* mode = getenv("ITEMCTL_RESEARCH");
    if (!mode || !*mode) return;
    Log("[research] mode %s\n", mode);
    g_Sym = ResolveSymbols({"f_8045c4a0", "f_83415d20", "f_80229650", "f_80229690", "f_8005ebe0", "f_80791f70",
                            "f_80235dc0", "f_8045efe0", "f_80a95770", "f_80a95ef0", "f_802efb20"});
    if (strstr(mode, "builder")) {
        auto bs = ResolveSymbols({"f_80459f90"});
        if (bs.count("f_80459f90")) o_Build = hook::Install(bs["f_80459f90"], BuildHook, "research: part builder f_80459f90");
    }
    if (strstr(mode, "items") && g_Sym.count("f_80a95770"))
        o_RtUpdate = hook::Install(g_Sym["f_80a95770"], RtUpdateHook, "research: RT box update f_80a95770");
    if (strstr(mode, "trace")) StartTrace();
    if ((strstr(mode, "clone") || strstr(mode, "watch") || strstr(mode, "copy")) && g_Sym.count("f_83415d20"))
        o_HudSetup = hook::Install(g_Sym["f_83415d20"], HudSetupHook, "research: HUD setup f_83415d20");
    if (strstr(mode, "panes")) {
        if (g_Sym.count("f_8045c4a0")) o_FindPane = hook::Install(g_Sym["f_8045c4a0"], FindPaneHook, "research: find pane f_8045c4a0");
    }
}

}  // namespace research
