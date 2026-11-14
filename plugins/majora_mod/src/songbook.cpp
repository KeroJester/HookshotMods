// The songbook's buttons for the added songs (RESEARCH_NOTES.md §79n).
//
// The songbook (layout Ocarina_00, screen vt 0x84c0a4f8) has 13 buttons Pa_MelodyBtn_00..12, each a part
// PaOcarinaMelodyTypeBtn_00 with a UniteButton controller (made from the part's own controller definition by the
// screen's layout setup f_8045bac0). Its build f_8340ecf0 keeps per button the part layout (+0x518), its animations
// (+0x310 + 0x28 i: Type, Decide, Failure, CursorLoop, PlayOut) and the controller (+0x580). The cursor is the UI
// focus system; the screen's vt method f_83410c70(screen, controller) takes the focus: it stores the button's index
// in GameData (0x8566048e) and shows that song's title, caption and notes (f_83410d90 / e80 / f70, by index).
//
// The mod builds one more button per added song with the same build call (instance Pa_MelodyBtn_<type>), so the
// layout setup makes their controllers as for the game's own, places them, and answers the focus for them with the
// song's type as the index.
#include "songbook.h"
#include "mm_songs.h"
#include "guest.h"
#include "hook.h"
#include "platform.h"
#include "songs.h"
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace songbook {
using namespace plat;
using guest::D;
using guest::Q;

constexpr uint64_t kLayoutVt = 0x84c41970;   // a part layout
constexpr uint64_t kBookVt = 0x84c0a4f8;     // the songbook screen
enum : uint64_t {
    fAlloc = 0x8005ebe0,     // operator new(size, heap, align)
    fPartBuild = 0x80459f90, // layout builder: part from (ctx, description) -> layout + 0x40
    fRegister = 0x8156aa50,  // add a layout's node to a layout list
    fPaneAt = 0x8045b1f0,    // a layout root's pane by index
    fNode = 0x80229650,      // a layout's tree node
    fParent = 0x80229690,    // a tree node's parent
    fAttach = 0x80235dc0,    // attach a root pane under a pane (container, root, parent)
    fBookBuild = 0x8340ecf0, // songbook screen build (x0 screen)
    fFocus = 0x83410c70,     // songbook vt: a controller got the focus (x0 screen, x1 controller)
    fTranslate = 0x8022a3e0, // pane SetTranslate (x0 pane, s0 x, s1 y, s2 z)
    fDirty = 0x815457d0,     // (pane): queue a pane for its update (after setting its pending bits at +0)
    fVisible = 0x8022aea0,   // (pane, bool): pane SetVisible (flag bit 0 at +0xa8)
    fShow = 0x83412b00,      // songbook (x0 screen, x1 mode): 1 opens it (title, icons), 2 the same for listening, 0 closes
    fAnim = 0x8045c7a0,      // (layout, name): a layout animation's table entry
    fAnimFrame = 0x802f2d90, // (anim, s0 frame): show one frame
    fAnimStop = 0x802f0fe0,  // (anim): stop / rewind
    fAnimSpeed = 0x802f2b70, // (anim) -> s0 speed
    fAnimPlay = 0x80bcce80,  // (anim, s0 speed): play
    fCtlState = 0x80df53e0,  // (controller + 0x48 object, state, anim): the animation a controller state plays
    fLayShow = 0x80a89190,   // (layout, 0): show a button (the songbook's open)
    fLayHide = 0x80aa3b00,   // (layout, 0): hide it (the songbook's close)
    fOm = 0x80db6710,        // (holder, 0x20) -> OcarinaMgr
    fGdSetInt = 0x80183b70,  // (GameData, value, hash)
    fTitle = 0x83410d90,     // (screen, index): the panel's title
    fCaption = 0x83410e80,   // (screen, index): the panel's caption
    fNotes = 0x83410f70,     // (screen, index): the panel's notes
    fListenState = 0x833e7e00, // (screen + 0x610, index)
    fListen = 0x832b7880,    // (OcarinaMgr, index): play the song's example melody
    fFocusSet = 0x8379c010,  // (screen, layout, flag): give a button the focus
    fGdGetInt = 0x80857070,  // (GameData, &out, hash) -> found
    fPlayOut = 0x834138e0,   // (screen, op, index): a button's PlayOut animation (the song being played): 0 stop, 1 play, 2 play if idle
    fAnimFwd = 0x802f0f00,   // (anim): play forward
    fAnimEnd = 0x802f1340,   // (anim + 0x20 object) -> last frame (low 16 bits)
};
constexpr uint64_t kOmHolder = 0x84c9e178, kGameData = 0x84c9e180;   // ** (the songbook's own loads)
constexpr uint32_t kCursorHash = 0x8566048e;                         // GameData: the songbook's selected button
constexpr uint64_t kStrType = 0x83f368f0, kStrDecide = 0x83f368f6, kStrFailure = 0x83f368fe, kStrCursorLoop = 0x83f36907,
                   kStrPlayOut = 0x83f36913;
static guest::Functions g_Fn;
static GuestFn o_PartBuild, o_BookBuild, o_Focus, o_Show, o_FocusSet, o_PlayOut;
static const bool kProbe = getenv("MAJORA_PROBE_BOOK") != nullptr;

struct Button { uint32_t type = 0; uint64_t lay = 0, ctl = 0, type_ = 0, decide = 0, failure = 0, cursor = 0, playout = 0; };
// the part's animations that a shown button has applied (the game's buttons have them from their setup; an added
// button copies their frames when the songbook opens, or its panes keep the layout's raw state: glows lit)
static const char* const kRestAnims[] = {"Decide", "Failure", "CursorLoop", "PlayOut", "Loop", "Rotate", "Select", "UnSelect"};
static std::vector<Button> g_Built;      // made during the songbook's layout build (game thread)
static std::mutex g_Mu;

template <class T> static void W(uint64_t a, T v) { if (GuestPtrOk(a)) GWrite<T>(a, v); }

// a guest call with one float argument (s0)
static uint64_t CallF(GuestCtx* c, uint64_t fn, uint64_t a0, float f) {
    const GuestCtx saved = *c;
    c->x[0] = a0, c->x[30] = 0;
    memset(c->vreg[0], 0, 16);
    memcpy(c->vreg[0], &f, 4);
    ((GuestFn)g_Fn.Host(fn))(c);
    const uint64_t r = c->x[0];
    float out;
    memcpy(&out, c->vreg[0], 4);
    *c = saved;
    memcpy(c->vreg[31], &out, 4);   // (scratch: a float result, for AnimSpeed)
    return r;
}

static std::string GStr(uint64_t p) {
    std::string s;
    for (int i = 0; p && GuestPtrOk(p + i) && i < 96; i++) { char ch = (char)GRead<uint8_t>(p + i); if (!ch) break; s += ch; }
    return s;
}

static uint64_t GuestString(GuestCtx* c, uint64_t heap, const std::string& str) {
    const uint64_t p = g_Fn.Call(c, fAlloc, (str.size() + 16) & ~7ull, heap, 8);
    if (p) { memcpy(GPtr(p), str.c_str(), str.size() + 1); }
    return p;
}

// A layout and its nested part layouts into the screen's layout list (as Item Controls' native_hud.cpp)
static void RegisterTree(GuestCtx* c, uint64_t list, uint64_t lay, int depth = 0) {
    if (Q(lay) != kLayoutVt || depth > 3) return;
    g_Fn.Call(c, fRegister, list, lay + 0x40);
    const uint64_t root = Q(lay + 0x80);
    const int n = GuestPtrOk(root) ? GRead<uint16_t>(root + 0x13c) : 0;
    for (int q = 0; q < n && q < 256; q++) {
        const uint64_t pane = g_Fn.Call(c, fPaneAt, root, q);
        if (!GuestPtrOk(pane) || (GRead<uint32_t>(pane + 0xa8) & 0x3c00000) != 0x1800000) continue;
        const uint64_t nroot = Q(Q(Q(pane + 8) + 8));
        if (GuestPtrOk(nroot) && Q(nroot + 0xb0) != lay) RegisterTree(c, list, Q(nroot + 0xb0), depth + 1);
    }
}

// ---- 1. the layout build: one more button per added song ----------------------------------------------------------
// ctx: vt, layout, heap, screen, parent layout, resource; description: part name, len, instance name, len, layout
// list, 0, 0, 1 (native_hud.cpp). The songbook's last button is Pa_MelodyBtn_10 (the panes' order).
static void PartBuildHook(GuestCtx* c) {
    const uint64_t ctx = c->x[0], desc = c->x[1];
    uint64_t k[8], d[8];
    for (int i = 0; i < 8; i++) k[i] = Q(ctx + 8 * i), d[i] = Q(desc + 8 * i);
    o_PartBuild(c);
    if (!GuestPtrOk(d[4]) || GStr(d[0]) != "PaOcarinaMelodyTypeBtn_00" || GStr(d[2]) != "Pa_MelodyBtn_10") return;
    const GuestCtx saved = *c;
    std::vector<Button> built;
    for (const songs::Song& s : songs::All()) {
        char inst[32];
        snprintf(inst, sizeof inst, "Pa_MelodyBtn_%02u", s.type);
        const uint64_t ctxm = g_Fn.Call(c, fAlloc, 0x40, k[2], 8), descm = g_Fn.Call(c, fAlloc, 0x40, k[2], 8);
        const uint64_t name = GuestString(c, k[2], inst);
        if (!ctxm || !descm || !name) break;
        for (int i = 0; i < 8; i++) { W<uint64_t>(ctxm + 8 * i, k[i]); W<uint64_t>(descm + 8 * i, d[i]); }
        W<uint64_t>(ctxm + 8, 0);                        // (no layout given: the builder makes one)
        W<uint64_t>(descm + 0x10, name);
        W<uint64_t>(descm + 0x18, strlen(inst));
        c->x[0] = ctxm, c->x[1] = descm, c->x[30] = 0;
        o_PartBuild(c);
        const uint64_t lay = c->x[0] - 0x40;
        *c = saved;
        if (Q(lay) != kLayoutVt || !GuestPtrOk(Q(lay + 0x80))) break;
        RegisterTree(c, d[4], lay);
        built.push_back({s.type, lay, 0});
    }
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Built = built;
    Log("[book] %zu buttons built with the songbook (screen %#llx, list %#llx)\n", built.size(), (unsigned long long)k[3],
        (unsigned long long)d[4]);
}

// ---- 2. after the songbook's build: controllers, placement ---------------------------------------------------------
// In the rows' units (N_MelodyBtnL_00 at y 180 holds the top seven, N_MelodyBtnR_00 at y 0 the bottom six; the block
// N_MelodyBtn_00 above them scales by 1.4): the bottom row takes seven (the Song of Healing last) on the top row's arc,
// and a third row at y -180 holds the other eight on a gentler, closer arc, in the space above the panel.
// What the songbook draws are world positions computed once, early: they put the block one row (180 x 1.4) above
// where the layout has it (y 240, drawn as 492). Panes re-placed here are computed again from the layout values, so
// the block is set to where it is drawn and every button's part pane is placed (and queued for its update). The
// added buttons hang under the Minuet's part pane (a pane under a group node of the songbook is never updated),
// placed relative to it. This runs at the first focus after the songbook opens (changes made while it is not shown
// are not drawn).
constexpr float kRowStep = 180.0f;
static const float kArc7X[7] = {-540, -360, -180, 0, 180, 360, 540}, kArc7Y[7] = {-40, 22, 60, 72, 60, 22, -40};
static const float kArc8X[8] = {-560, -400, -240, -80, 80, 240, 400, 560}, kArc8Y[8] = {-40, 14, 48, 64, 64, 48, 14, -40};
static const int kTopRow[7] = {0, 4, 3, 5, 1, 2, 12};      // Zelda's Lullaby .. Scarecrow's Song, left to right
static const int kBottomRow[6] = {6, 7, 8, 9, 10, 11};     // Minuet .. Prelude
constexpr int kHolder = 6;                                 // the added buttons hang under the Minuet's pane


// The game's SetTranslate, then the pane's updates queued again (a pane whose pending bits are already set is not
// queued by it); alpha left alone (the songbook fades in later)
static void SetAt(GuestCtx* c, uint64_t pane, float x, float y) {
    if (!GuestPtrOk(pane)) return;
    W<uint32_t>(pane, GRead<uint32_t>(pane) & ~0x40u);
    const GuestCtx saved = *c;
    c->x[0] = pane, c->x[30] = 0;
    memset(c->vreg[0], 0, 16), memset(c->vreg[1], 0, 16), memset(c->vreg[2], 0, 16);
    memcpy(c->vreg[0], &x, 4), memcpy(c->vreg[1], &y, 4);
    ((GuestFn)g_Fn.Host(fTranslate))(c);
    *c = saved;
    W<uint32_t>(pane, 0);
    W<uint32_t>(pane, GRead<uint32_t>(pane + 4) & ~0x20u);
    g_Fn.Call(c, fDirty, pane);
}

struct Book {
    uint64_t screen = 0;
    uint64_t pane[13] = {};                // the game's buttons' part panes
    float x[13] = {}, y[13] = {};          // as the layout has them
    uint64_t block = 0;                    // N_MelodyBtn_00
    float blockX = 0, blockY = 0, blockScale = 0;
    std::vector<Button> buttons;
};
static Book g_Book;                        // (game thread)

static void Place(GuestCtx* c);

static uint64_t PartPane(GuestCtx* c, uint64_t screen, int i) {
    return g_Fn.Call(c, fParent, g_Fn.Call(c, fNode, Q(screen + 0x518 + 8 * i) + 0x40));
}

static void BookBuildHook(GuestCtx* c) {
    const uint64_t screen = c->x[0];
    o_BookBuild(c);
    std::vector<Button> built;
    { std::lock_guard<std::mutex> lk(g_Mu); built = g_Built; g_Built.clear(); }
    if (Q(screen) != kBookVt || built.empty()) return;
    Book book;
    book.screen = screen;
    for (int i = 0; i < 13; i++) {
        book.pane[i] = PartPane(c, screen, i);
        if (!GuestPtrOk(book.pane[i])) { Log("[book] the songbook's button %d has no pane: no buttons for the added songs\n", i); return; }
        book.x[i] = GRead<float>(book.pane[i] + 0x6c), book.y[i] = GRead<float>(book.pane[i] + 0x70);
    }
    book.block = g_Fn.Call(c, fParent, g_Fn.Call(c, fParent, book.pane[kHolder]));
    if (GuestPtrOk(book.block)) book.blockX = GRead<float>(book.block + 0x6c), book.blockY = GRead<float>(book.block + 0x70), book.blockScale = GRead<float>(book.block + 0x60);
    if (book.x[kHolder] != -450.0f || book.blockScale != 1.4f) { Log("[book] the songbook's layout is not as expected: no buttons for the added songs\n"); return; }
    // the controllers the layout setup made (the screen's list +0x38: nodes at controller + 8, name at node + 0x10)
    const uint64_t head = Q(screen + 0x38);
    for (Button& b : built) {
        char inst[32];
        snprintf(inst, sizeof inst, "Pa_MelodyBtn_%02u", b.type);
        for (uint64_t n = Q(head + 0x10), k = 0; n && n != head + 8 && k < 64; n = Q(n + 8), k++)
            if (GStr(Q(n + 0x10)) == inst) b.ctl = n - 8;
        if (!b.ctl) Log("[book] %s has no controller\n", inst);
        b.type_ = g_Fn.Call(c, fAnim, b.lay, kStrType);
        b.decide = g_Fn.Call(c, fAnim, b.lay, kStrDecide);
        b.failure = g_Fn.Call(c, fAnim, b.lay, kStrFailure);
        b.cursor = g_Fn.Call(c, fAnim, b.lay, kStrCursorLoop);
        b.playout = g_Fn.Call(c, fAnim, b.lay, kStrPlayOut);
        const uint64_t holder = book.pane[kHolder];
        g_Fn.Call(c, fAttach, Q(holder + 0x18), Q(b.lay + 0x80), holder);
        g_Fn.Call(c, fVisible, g_Fn.Call(c, fNode, b.lay + 0x40), 1);   // (a part's root pane is built hidden; its part pane shows it)
    }
    book.buttons = built;
    g_Book = book;
    Log("[book] %zu buttons attached\n", built.size());

}

static void Place(GuestCtx* c) {
    Book& k = g_Book;
    SetAt(c, k.block, k.blockX, k.blockY + kRowStep * k.blockScale);
    for (int i = 0; i < 7; i++) SetAt(c, k.pane[kTopRow[i]], k.x[kTopRow[i]], k.y[kTopRow[i]]);
    for (int i = 0; i < 6; i++) SetAt(c, k.pane[kBottomRow[i]], kArc7X[i], kArc7Y[i]);
    const float hx = kArc7X[0], hy = kArc7Y[0];              // (the holder: the Minuet)
    int third = 0;
    for (Button& b : k.buttons) {
        const uint64_t node = g_Fn.Call(c, fNode, b.lay + 0x40);
        if (b.type == songs::kFirst) SetAt(c, node, kArc7X[6] - hx, kArc7Y[6] - hy);   // the Song of Healing
        else if (third < 8) { SetAt(c, node, kArc8X[third] - hx, kArc8Y[third] - kRowStep - hy); third++; }
    }
}

// (placed at the first focus after the songbook opens: changes made while it is not shown yet are not drawn)
static bool g_PlaceNext = false;
// The songbook gives the focus to the selected button (GameData index) when it opens or starts playing a song; for an
// index past 12 it takes button 0. While an added song is the selected one, that call gets its button instead.
static uint64_t g_FocusTo = 0;
static void FocusSetHook(GuestCtx* c) {
    if (g_FocusTo && c->x[0] == g_Book.screen && c->x[1] == Q(g_Book.screen + 0x518)) c->x[1] = g_FocusTo;
    o_FocusSet(c);
}

// The song being played in the songbook (the ocarina update f_832b4af0) lights its button; an added song's own
static void PlayOutHook(GuestCtx* c) {
    const uint64_t screen = c->x[0], op = c->x[1];
    const uint32_t index = (uint32_t)c->x[2];
    const Button* on = nullptr;
    if (screen == g_Book.screen && screen) for (const Button& b : g_Book.buttons) if (b.type == index) on = &b;
    if (!on) return o_PlayOut(c);
    if (op == 0) g_Fn.Call(c, fAnimStop, on->playout);
    else if (op == 1) g_Fn.Call(c, fAnimFwd, on->playout);
    else if (op == 2) {
        CallF(c, fAnimSpeed, Q(on->playout + 0x20), 0);
        float speed;
        memcpy(&speed, c->vreg[31], 4);
        if (speed == 0.0f && GRead<float>(on->playout + 0x10) == 0.0f) CallF(c, fAnimPlay, on->playout, 1.0f);
    }
}

static uint32_t SelectedIndex(GuestCtx* c) {
    guest::Scratch s(c);
    GWrite<uint32_t>(s.at, 0);
    if (!(g_Fn.Call(c, fGdGetInt, Q(Q(kGameData)), s.at, kCursorHash) & 1)) return 0;
    return GRead<uint32_t>(s.at);
}

static void ShowHook(GuestCtx* c) {
    const uint64_t screen = c->x[0], mode = c->x[1];
    if (screen == g_Book.screen && screen && (mode == 1 || mode == 2)) {
        const uint32_t sel = SelectedIndex(c);
        for (const Button& b : g_Book.buttons) if (b.type == sel) g_FocusTo = b.lay;
    }
    o_Show(c);
    g_FocusTo = 0;
    if (screen != g_Book.screen) return;
    if (mode == 1 || mode == 2) song::EnsureScores(c);
    for (Button& b : g_Book.buttons) {
        if (mode == 0) { g_Fn.Call(c, fLayHide, b.lay, 0); continue; }
        if (mode != 1 && mode != 2) continue;
        CallF(c, fAnimFrame, b.type_, 0.0f);            // (the icon: none yet)
        const uint64_t model = Q(screen + 0x518 + 8 * kHolder);
        for (const char* n : kRestAnims) {
            guest::Scratch sc(c);
            memcpy(GPtr(sc.at), n, strlen(n) + 1);
            const uint64_t mine = g_Fn.Call(c, fAnim, b.lay, sc.at), theirs = g_Fn.Call(c, fAnim, model, sc.at);
            if (!GuestPtrOk(mine) || mine == theirs) continue;
            float frame = 0.0f;                          // (the cursor's animations at rest; the others as the game's button)
            if (!strcmp(n, "UnSelect")) frame = (float)(g_Fn.Call(c, fAnimEnd, Q(mine + 0x20)) & 0xffff);   // (unfocused)
            else if (strcmp(n, "CursorLoop") && strcmp(n, "Select")) {
                CallF(c, fAnimSpeed, Q(theirs + 0x20), 0);
                memcpy(&frame, c->vreg[31], 4);
            }
            CallF(c, fAnimFrame, mine, frame);
        }
        if (b.ctl) g_Fn.Call(c, fCtlState, Q(b.ctl + 0x48), 4, b.decide);   // (as a learned song's)
        g_Fn.Call(c, fLayShow, b.lay, 0);
    }
    if (mode == 1 || mode == 2) g_PlaceNext = true;
}

// The focus on an added song's button, as f_83410c70 does for the game's: the selected index (the song's type) in
// GameData, then the panel (mode 1) or the song played (mode 2: listening). The panel's readers check the learned
// flag of position index < 13 ? index : 0 (Zelda's Lullaby's); the added songs are known, so it is set meanwhile.
static void Focused(GuestCtx* c, uint64_t screen, const Button& on) {
    const uint64_t om = g_Fn.Call(c, fOm, Q(Q(kOmHolder)), 0x20);
    if (!GuestPtrOk(om)) { Log("[book] focus: no OcarinaMgr\n"); return; }
    g_Fn.Call(c, fGdSetInt, Q(Q(kGameData)), on.type, kCursorHash);
    const uint32_t mode = D(om + 0x278);
    if (kProbe) Log("[book] focus on the %u button: OcarinaMgr %#llx mode %u\n", on.type, (unsigned long long)om, mode);
    const uint8_t learned = GRead<uint8_t>(om + 0x264);
    W<uint8_t>(om + 0x264, 1);
    if (mode == 1) {
        g_Fn.Call(c, fTitle, screen, on.type);
        g_Fn.Call(c, fCaption, screen, on.type);
        g_Fn.Call(c, fNotes, screen, on.type);
    } else if (mode == 2) {
        g_Fn.Call(c, fListenState, screen + 0x610, on.type);
        g_Fn.Call(c, fListen, om, on.type);
    }
    W<uint8_t>(om + 0x264, learned);
    if (mode != 1 && GRead<float>(on.cursor + 0x10) == 0.0f) CallF(c, fAnimPlay, on.cursor, 1.0f);   // (as f_83410c70)
}

static void FocusHook(GuestCtx* c) {
    const uint64_t screen = c->x[0], ctl = c->x[1];
    if (screen != g_Book.screen || !screen) return o_Focus(c);
    if (g_PlaceNext) { g_PlaceNext = false; Place(c); }
    const Button* on = nullptr;
    for (const Button& b : g_Book.buttons) {
        if (b.ctl == ctl) on = &b;
        else g_Fn.Call(c, fAnimStop, b.cursor);
    }
    if (kProbe) Log("[book] focus %#llx (%s)\n", (unsigned long long)ctl, on ? "an added song" : "the game's");
    o_Focus(c);                                          // (the game's buttons lose the focus; for an added song it finds none of its own)
    if (on) Focused(c, screen, *on);
}

void Start() {
    if (!g_Fn.Resolve({fAlloc, fPartBuild, fRegister, fPaneAt, fNode, fParent, fAttach, fBookBuild, fFocus, fTranslate, fDirty, fShow, fVisible, fAnim, fAnimFrame, fAnimStop, fAnimSpeed, fAnimPlay, fCtlState, fLayShow, fLayHide, fOm, fGdSetInt, fTitle, fCaption, fNotes, fListenState, fListen, fFocusSet, fGdGetInt, fPlayOut, fAnimFwd, fAnimEnd}, "book")) return;
    o_PartBuild = hook::Install(g_Fn.Host(fPartBuild), PartBuildHook, "layout part build f_80459f90 (songbook)");
    o_BookBuild = hook::Install(g_Fn.Host(fBookBuild), BookBuildHook, "songbook build f_8340ecf0");
    o_Focus = hook::Install(g_Fn.Host(fFocus), FocusHook, "songbook focus f_83410c70");
    o_Show = hook::Install(g_Fn.Host(fShow), ShowHook, "songbook show f_83412b00");
    o_FocusSet = hook::Install(g_Fn.Host(fFocusSet), FocusSetHook, "songbook focus set f_8379c010");
    o_PlayOut = hook::Install(g_Fn.Host(fPlayOut), PlayOutHook, "songbook play out f_834138e0");
}

}  // namespace songbook
