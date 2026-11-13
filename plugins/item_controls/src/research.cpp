// Research probes for the native HUD boxes (RESEARCH_NOTES.md section 69). Off unless ITEMCTL_RESEARCH is set in the
// game's environment; never part of normal play.
//   ITEMCTL_RESEARCH=panes   log the game's find-pane-by-name calls (f_8045c4a0(name, root, &out)) for part panes
//                            ("Pa_..."): the result, its vtable, the caller
//   ITEMCTL_RESEARCH=clone   after the HUD's setup (f_83415d20), clone the RT box part as the dungeon map clones its
//                            icons (f_80f9b1d0): f_80791f70(new layout, template, name, 0, 0), then
//                            f_80235dc0(parent's container, new root pane, parent pane); logs what it finds
#include "research.h"
#include "hook.h"
#include "platform.h"
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <cstdio>

namespace research {
using namespace plat;

static GuestFn o_FindPane, o_HudSetup;
static std::unordered_map<std::string, uintptr_t> g_Sym;

// a guest function called from a hook on the game's thread, as Hookshot's host/oot_menu.c guest_call: the context saved,
// x0.. set, x30 = 0, the lifted function run, the context restored; returns x0
static uint64_t Call(GuestCtx* c, const char* fn, uint64_t a0 = 0, uint64_t a1 = 0, uint64_t a2 = 0, uint64_t a3 = 0,
                     uint64_t a4 = 0) {
    auto it = g_Sym.find(fn);
    if (it == g_Sym.end()) { Log("[research] %s not resolved\n", fn); return 0; }
    const GuestCtx saved = *c;
    c->x[0] = a0, c->x[1] = a1, c->x[2] = a2, c->x[3] = a3, c->x[4] = a4, c->x[30] = 0;
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

static void HudSetupHook(GuestCtx* c) {
    const uint64_t hud = c->x[0], heap = c->x[1];
    o_HudSetup(c);
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
    }
    GWrite<float>(tnode + 0x60, 0.0f); GWrite<float>(tnode + 0x64, 0.0f);   // (the template's content hidden)
    Log("[research] six clones attached under the holder; the template's content hidden\n");
}

void Start() {
    const char* mode = getenv("ITEMCTL_RESEARCH");
    if (!mode || !*mode) return;
    Log("[research] mode %s\n", mode);
    g_Sym = ResolveSymbols({"f_8045c4a0", "f_83415d20", "f_80229650", "f_80229690", "f_8005ebe0", "f_80791f70",
                            "f_80235dc0"});
    if (strstr(mode, "clone") && g_Sym.count("f_83415d20"))
        o_HudSetup = hook::Install(g_Sym["f_83415d20"], HudSetupHook, "research: HUD setup f_83415d20");
    if (strstr(mode, "panes")) {
        if (g_Sym.count("f_8045c4a0")) o_FindPane = hook::Install(g_Sym["f_8045c4a0"], FindPaneHook, "research: find pane f_8045c4a0");
    }
}

}  // namespace research
