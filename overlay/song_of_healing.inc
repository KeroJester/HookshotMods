// -------------------------------------------------------------------------------------------------------------------
// Song of Healing (mod 040-SongOfHealing): Majora's Mask's Song of Healing as a playable ocarina song.
// -------------------------------------------------------------------------------------------------------------------
// The game's songs are a fixed native enum (MelodyType ZeldaLullaby 0 .. ScarecrowSong 12, 13 = none), and every part
// of a song's presentation is looked up by that type: the staff notes (MelodyScoreBook), the title (LayoutMsg
// Ocarina_00 Title_%02d), the replay ("Melody_<TypeName>" -> Stream/Ocarina_<TypeName>.bwav) and the event
// (Dm_SY_Ocarina switches on it). A value outside the enum gets no presentation at all. So the Song of Healing is
// presented by the game itself through one existing type's slot (the "host": Zelda's Lullaby), and while it is being
// presented the overlay swaps what that slot shows and plays:
//
//   OcarinaMgr (vtable 0x84bc1978; f_832b4df0 = per-frame update, x0 = this, x1 = pad state):
//     +0x38   u32     notes played this performance
//     +0x138  u32[8]  note ring (1 + index into the pitch table LowB..HighF: D 4, F 7, A 11, B 13, HighD 16), write
//                     index +0x258; +0x158 / +0x25c: a second ring
//     +0x220  u32     the matched MelodyType (13 = none)
//     +0x264  u8[13]  learned flags
//   f_832b7400(mgr, MelodyType* out) -> matched: the matcher (learned scores against both rings).
//   f_8337b730(sound, type, kind): starts "Melody_" / "ExampleMelody_" / "ScoreExampleMelody_" + type name.
//   MelodyScoreBook singleton *0x84e8c7a8: +0x20 -> f_832b3f20 -> f_832b3f70 = score list; a score's type via
//   f_82fe3df0, its notes via f_832b3fd0 (-> +0x18 u32 note numbers).
//   Controller: A = A, X = HighD, Y = B, L = D, R = F.
//
// Files (mods/<enabled mod>/song_of_healing/): song.cfg; the replay is the mod's own romfs stream
// Sound/Resource/Stream/Ocarina_HealingSong.bwav, opened in place of the host's while the song is presented.
// HOOKSHOT_SOH=0 turns it off; HOOKSHOT_SOH_DEBUG=1 logs.

namespace soh {

// (the overlay's constructor installs this before the C++ globals below this point are initialised: everything with
// a constructor lives in G(), built on first use)
// A swap in the game's memory while the song is presented (patches.bin): where `find` is found, `repl` is written at
// +off, and put back afterwards.
struct Patch {
    std::string name;
    std::vector<uint8_t> find, repl;
    uint32_t off = 0;
    std::vector<uint64_t> addrs;                      // guest addresses of `find`
    std::vector<std::vector<uint8_t>> saved;          // what was there, per address (while applied)
};
struct Globals {
    std::string dir;
    std::vector<int> song;                            // note numbers
    std::string hostStream = "Ocarina_ZeldaLullaby.bwav", ourStream = "Ocarina_HealingSong.bwav";
    std::vector<Patch> patches;
};
static Globals& G() { static Globals g; return g; }
static bool g_On = false, g_Dbg = false;
static constexpr int kHostType = 0;                   // ZeldaLullaby
static float g_ReplaySeconds = 7.0f;

static GuestFn o_OcarinaUpdate = nullptr;             // f_832b4df0
static GuestFn o_OcarinaMatch = nullptr;              // f_832b7400
static GuestFn o_MelodySound = nullptr;               // f_8337b730
static GuestFn o_OpenFile = nullptr;                  // nn::fs::OpenFile (Hookshot HLE)
static GuestFn g_fBookGet = nullptr, g_fScoreList = nullptr, g_fListCount = nullptr, g_fScoreType = nullptr,
               g_fScoreNotes = nullptr;

static std::atomic<bool> g_LastWasOurs{false};         // the last song played was the Song of Healing
static std::atomic<bool> g_ReplayOpened{false};        // the replay stream has been opened
static std::chrono::steady_clock::time_point g_ReplayAt;
static uint64_t g_PresentNodes[8];                    // query nodes that belong to the presentation itself
static int g_PresentNodeCount = 0;
static std::atomic<bool> g_Active{false};             // the Song of Healing is being presented (as the host type)
static std::chrono::steady_clock::time_point g_ActiveAt;
static std::atomic<uint64_t> g_Mgr{0};
static std::mutex g_Mx;

static double Since() { return std::chrono::duration<double>(std::chrono::steady_clock::now() - g_ActiveAt).count(); }

static uint64_t GuestCall(GuestCtx* c, GuestFn fn, uint64_t a0, uint64_t a1 = 0) {
    GuestCtx saved = *c;
    c->x[31] = (c->x[31] - 256) & ~0xFULL;
    c->x[0] = a0; c->x[1] = a1;
    fn(c);
    uint64_t r = c->x[0];
    *c = saved;
    return r;
}

// ---- the host score's notes ----------------------------------------------------------------------------------------
static uint64_t g_HostNotes = 0;                      // guest address of the host score's u32 notes
static uint32_t g_HostNotesSaved[8];
static bool g_NotesSwapped = false;

static uint64_t FindHostNotes(GuestCtx* c) {
    if (g_HostNotes) return g_HostNotes;
    if (!g_fBookGet || !g_fScoreList || !g_fListCount || !g_fScoreType || !g_fScoreNotes) return 0;
    uint64_t book = GRead<uint64_t>(0x84e8c7a8ULL);
    if (!book) return 0;
    uint64_t res = GuestCall(c, g_fBookGet, book + 0x20);
    if (!res) return 0;
    uint64_t list = GuestCall(c, g_fScoreList, res);
    if (!list) return 0;
    int n = (int)GuestCall(c, g_fListCount, list);
    for (int i = 0; i < n && i < 32; i++) {
        uint64_t node = list; int idx = i; uint64_t score = 0;        // (as f_832b7400 walks it)
        for (int guard = 0; guard < 8; guard++) {
            uint64_t next = GRead<uint64_t>(node + 0x18);
            if (!next) { score = GRead<uint64_t>(GRead<uint64_t>(node + 0x10) + 8ULL * idx); break; }
            int cnt = (int)GuestCall(c, g_fListCount, next);
            if (idx - cnt < 0) { node = next; continue; }
            score = GRead<uint64_t>(GRead<uint64_t>(node + 0x10) + 8ULL * (idx - cnt));
            break;
        }
        if (!score) continue;
        uint64_t tp = GuestCall(c, g_fScoreType, score);
        if (!tp || (int)GRead<uint32_t>(tp) != kHostType) continue;
        uint64_t notes = GuestCall(c, g_fScoreNotes, score);
        uint64_t data = notes ? GRead<uint64_t>(notes + 0x18) : 0;
        if (data) {
            g_HostNotes = data;
            OverlayLog("[soh] host score notes at 0x%llx: %u %u %u %u %u %u\n", (unsigned long long)data,
                       GRead<uint32_t>(data), GRead<uint32_t>(data + 4), GRead<uint32_t>(data + 8),
                       GRead<uint32_t>(data + 12), GRead<uint32_t>(data + 16), GRead<uint32_t>(data + 20));
        }
        break;
    }
    return g_HostNotes;
}
static void SwapNotes(bool on) {
    const size_t n = std::min<size_t>(G().song.size(), 8);
    if (!g_HostNotes || on == g_NotesSwapped) return;
    for (size_t i = 0; i < n; i++) {
        if (on) { g_HostNotesSaved[i] = GRead<uint32_t>(g_HostNotes + 4 * i); GWrite<uint32_t>(g_HostNotes + 4 * i, (uint32_t)G().song[i]); }
        else GWrite<uint32_t>(g_HostNotes + 4 * i, g_HostNotesSaved[i]);
    }
    g_NotesSwapped = on;
}

// ---- memory patches ------------------------------------------------------------------------------------------------
static std::atomic<bool> g_Scanning{false};
static std::mutex g_PatchMx;

// Find every patch's `find` bytes in the resident guest memory (a few seconds, on its own thread).
static void ScanThread() {
    std::vector<std::pair<uintptr_t, uintptr_t>> regions;
    if (FILE* m = fopen("/proc/self/maps", "r")) {
        char line[512];
        while (fgets(line, sizeof line, m)) {
            unsigned long a, b; char perms[8];
            if (sscanf(line, "%lx-%lx %7s", &a, &b, perms) != 3 || perms[0] != 'r') continue;
            if (a < GUEST_RAM_BASE || a >= GUEST_RAM_BASE + (1ULL << 40)) continue;
            regions.push_back({a, b});
        }
        fclose(m);
    }
    struct Want { size_t patch; std::vector<uint8_t> find; };
    std::vector<Want> want;
    {
        std::lock_guard<std::mutex> lk(g_PatchMx);
        for (size_t i = 0; i < G().patches.size(); i++)
            if (G().patches[i].addrs.empty()) want.push_back({i, G().patches[i].find});
    }
    std::vector<std::vector<uint64_t>> hits(want.size());
    std::vector<unsigned char> res;
    auto t0 = std::chrono::steady_clock::now();
    for (auto& r : regions) {
        for (uintptr_t c = r.first; c < r.second; c += (64ULL << 20)) {
            size_t len = std::min<uintptr_t>(64ULL << 20, r.second - c);
            if (!tuner::PageResident(c, len, res)) continue;
            for (size_t p = 0; p < res.size();) {                     // runs of resident pages
                if (!(res[p] & 1)) { p++; continue; }
                size_t q = p;
                while (q < res.size() && (res[q] & 1)) q++;
                const uint8_t* base = (const uint8_t*)(c + p * 4096);
                size_t n = (q - p) * 4096;
                for (size_t w = 0; w < want.size(); w++) {
                    const auto& f = want[w].find;
                    const uint8_t* at = base;
                    while (hits[w].size() < 8 && (at = (const uint8_t*)memmem(at, n - (at - base), f.data(), f.size()))) {
                        hits[w].push_back((uint64_t)((uintptr_t)at - GUEST_RAM_BASE));
                        at++;
                    }
                }
                p = q;
            }
        }
    }
    double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    {
        std::lock_guard<std::mutex> lk(g_PatchMx);
        for (size_t w = 0; w < want.size(); w++) {
            Patch& pt = G().patches[want[w].patch];
            if (pt.saved.empty()) pt.addrs = hits[w];
            OverlayLog("[soh] %s: %zu place(s) in memory%s (scan %.1f s)\n", pt.name.c_str(), hits[w].size(),
                       hits[w].empty() ? " (not loaded?)" : "", secs);
        }
    }
    g_Scanning = false;
}
static void StartScan() {
    bool need = false;
    {
        std::lock_guard<std::mutex> lk(g_PatchMx);
        for (auto& p : G().patches) if (p.addrs.empty()) need = true;
    }
    if (need && !g_Scanning.exchange(true)) std::thread(ScanThread).detach();
}
static void ApplyPatches(bool on) {
    std::lock_guard<std::mutex> lk(g_PatchMx);
    for (auto& p : G().patches) {
        if (on) {
            if (!p.saved.empty()) continue;
            std::vector<uint64_t> ok;
            for (uint64_t a : p.addrs)                                // still what was found there?
                if (!memcmp(GPtr(a), p.find.data(), p.find.size())) ok.push_back(a);
            p.addrs = ok;
            for (uint64_t a : p.addrs) {
                p.saved.emplace_back(GPtr(a + p.off), GPtr(a + p.off) + p.repl.size());
                memcpy(GPtr(a + p.off), p.repl.data(), p.repl.size());
            }
            if (p.addrs.empty()) OverlayLog("[soh] %s: not found in memory; the host's own is used\n", p.name.c_str());
        } else {
            for (size_t i = 0; i < p.saved.size() && i < p.addrs.size(); i++)
                memcpy(GPtr(p.addrs[i] + p.off), p.saved[i].data(), p.saved[i].size());
            p.saved.clear();
        }
    }
}

static void Begin() {
    g_ActiveAt = std::chrono::steady_clock::now();
    g_ReplayOpened = false;
    g_PresentNodeCount = 0;
    g_LastWasOurs = true;
    g_Active = true;
    SwapNotes(true);
    ApplyPatches(true);
    OverlayLog("[soh] Song of Healing played: presented through MelodyType %d\n", kHostType);
}
static void End(const char* why) {
    if (!g_Active.exchange(false)) return;
    SwapNotes(false);
    ApplyPatches(false);
    OverlayLog("[soh] presentation over (%s, %.1f s)\n", why, Since());
    StartScan();                                          // (anything that was not found this time)
}

// ---- the matcher ---------------------------------------------------------------------------------------------------
static bool RingEndsWithSong(uint64_t mgr, uint32_t ring, uint32_t idxOff) {
    const size_t n = G().song.size();
    if (!n || n > 8 || GRead<uint32_t>(mgr + 0x38) < n) return false;
    uint32_t idx = GRead<uint32_t>(mgr + idxOff);
    for (size_t k = 0; k < n; k++) {
        uint32_t slot = (idx + 8 - 1 - (uint32_t)k) & 7;
        if ((int)GRead<uint32_t>(mgr + ring + 4 * slot) != G().song[n - 1 - k]) return false;
    }
    return true;
}
static void HookOcarinaMatch(GuestCtx* c) {
    uint64_t mgr = c->x[0], out = c->x[1];
    if (g_Active.load()) { std::lock_guard<std::mutex> lk(g_Mx); End("a new performance"); }   // (before it matches)
    g_LastWasOurs = false;
    o_OcarinaMatch(c);
    if ((c->x[0] & 1) || !mgr || !out) return;
    if (!RingEndsWithSong(mgr, 0x138, 0x258) && !RingEndsWithSong(mgr, 0x158, 0x25c)) return;
    std::lock_guard<std::mutex> lk(g_Mx);
    if (!FindHostNotes(c)) { OverlayLog("[soh] the host score was not found; not presented\n"); return; }
    GWrite<uint32_t>(out, (uint32_t)kHostType);
    c->x[0] = 1;
    Begin();
}

// QueryMelodyTypeForNormal (f_82a7c980, x0 = the node): what flowcharts and AI graphs switch on; it reads mgr+0x220
// live. Two nodes ask during a free-play presentation and need the host type throughout, or the presentation is cut
// short: Dm_SY_Ocarina's opening switch (once, ~0.6 s after the match) and one polled every frame from then on. An
// ocarina point's event that decides what the played song does there runs Dm_SY_Ocarina as a sub-flow and asks
// afterwards, with its own node. So: nodes that first ask before the replay stream is opened get the host type; a
// node that first asks later is told "none", and nothing that waits for the host song reacts to this one.
// (Spots that prompt for one song use the other matcher, f_832b7600, which is left alone: there this song is just
// unknown notes.)
static GuestFn o_MelodyQuery = nullptr;
static void HookMelodyQuery(GuestCtx* c) {
    static const bool never = getenv("HOOKSHOT_SOH_NOHIDE") != nullptr;
    uint64_t mgr = g_Mgr.load(), node = c->x[0];
    if (!mgr || never || !g_LastWasOurs.load() || (int)GRead<uint32_t>(mgr + 0x220) != kHostType) { o_MelodyQuery(c); return; }
    bool known = false;
    for (int i = 0; i < g_PresentNodeCount; i++) known |= g_PresentNodes[i] == node;
    if (!known && g_Active.load() && !g_ReplayOpened.load() && g_PresentNodeCount < 8) {
        g_PresentNodes[g_PresentNodeCount++] = node;
        known = true;
        if (g_Dbg) OverlayLog("[soh] presentation node 0x%llx (%.2f s)\n", (unsigned long long)node, Since());
    }
    if (known) { o_MelodyQuery(c); return; }
    static int logged = 0;
    if (logged++ < 20) OverlayLog("[soh] a flow asked which song was played (node 0x%llx, %.2f s): none\n", (unsigned long long)node, Since());
    GWrite<uint32_t>(mgr + 0x220, 13);
    o_MelodyQuery(c);
    GWrite<uint32_t>(mgr + 0x220, (uint32_t)kHostType);
}

static void HookMelodySound(GuestCtx* c) {
    if (g_Dbg) OverlayLog("[soh] melody sound: type %d kind %d (active %d, %.2f s)\n", (int)c->x[1], (int)c->x[2],
                          (int)g_Active.load(), g_Active.load() ? Since() : 0.0);
    o_MelodySound(c);
}

// The replay stream is opened when it starts: ours in place of the host's.
static void HookOpenFile(GuestCtx* c) {
    uint64_t p = c->x[1];
    char saved[64]; char* tail = nullptr;
    if (p && g_Active.load()) {
        char* s = (char*)GPtr(p);
        const std::string& host = G().hostStream;
        size_t n = strnlen(s, 512), fl = host.size();
        if (n >= fl && n < 512 && fl < sizeof saved && G().ourStream.size() <= fl && !strcmp(s + n - fl, host.c_str())) {
            tail = s + n - fl;
            memcpy(saved, tail, fl + 1);
            strcpy(tail, G().ourStream.c_str());
            g_ReplayAt = std::chrono::steady_clock::now();
            g_ReplayOpened = true;
            OverlayLog("[soh] replay stream: %s (%.2f s)\n", s, Since());
        }
    }
    o_OpenFile(c);
    if (tail) memcpy(tail, saved, G().hostStream.size() + 1);
}

static void HookOcarinaUpdate(GuestCtx* c) {
    uint64_t mgr = c->x[0];
    o_OcarinaUpdate(c);
    if (!mgr) return;
    if (!g_Mgr.exchange(mgr)) StartScan();                // the ocarina is out for the first time: locate the patches
    if (g_Dbg) {
        static const uint32_t offs[] = {0x38, 0x114, 0x118, 0x11c, 0x220, 0x224, 0x228, 0x22c, 0x230, 0x244, 0x278};
        static uint32_t prev[sizeof offs / sizeof offs[0]];
        char b[256]; int o = 0; bool ch = false;
        for (size_t i = 0; i < sizeof offs / sizeof offs[0]; i++) {
            uint32_t v = GRead<uint32_t>(mgr + offs[i]);
            if (v != prev[i] && offs[i] != 0x244) ch = true;
            prev[i] = v;
            o += snprintf(b + o, sizeof b - o, " +%x=%u", offs[i], v);
        }
        if (ch) OverlayLog("[soh] mgr%s\n", b);
    }
}

// On every controller read: the safety net.
static void Frame() {
    {   // locate the patches ahead of time (their data is loaded during boot); retried while anything is missing
        static std::chrono::steady_clock::time_point next = std::chrono::steady_clock::now() + std::chrono::seconds(20);
        auto now = std::chrono::steady_clock::now();
        if (now >= next) { next = now + std::chrono::seconds(15); StartScan(); }
    }
    if (!g_Active.load()) return;
    if (g_ReplayOpened.load() &&
        std::chrono::duration<double>(std::chrono::steady_clock::now() - g_ReplayAt).count() > g_ReplaySeconds + 1.0) {
        std::lock_guard<std::mutex> lk(g_Mx); End("the replay ended");
    } else if (Since() > 40.0) {
        std::lock_guard<std::mutex> lk(g_Mx); End("timeout");
    }
}

static bool LoadPatches() {
    FILE* f = fopen((G().dir + "/patches.bin").c_str(), "rb");
    if (!f) return false;
    char magic[4]; uint32_t n = 0;
    bool ok = fread(magic, 1, 4, f) == 4 && !memcmp(magic, "SOHP", 4) && fread(&n, 4, 1, f) == 1 && n <= 16;
    for (uint32_t i = 0; ok && i < n; i++) {
        Patch p; char name[32]; uint32_t fl = 0, rl = 0;
        ok = fread(name, 1, 32, f) == 32 && fread(&fl, 4, 1, f) == 1 && fl >= 8 && fl <= 4096;
        if (!ok) break;
        name[31] = 0; p.name = name; p.find.resize(fl);
        ok = fread(p.find.data(), 1, fl, f) == fl && fread(&p.off, 4, 1, f) == 1 && fread(&rl, 4, 1, f) == 1 && rl <= (1u << 20);
        if (!ok) break;
        p.repl.resize(rl);
        ok = fread(p.repl.data(), 1, rl, f) == rl;
        if (ok) G().patches.push_back(std::move(p));
    }
    fclose(f);
    return ok;
}

static bool LoadConfig() {
    FILE* f = fopen((G().dir + "/song.cfg").c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "notes ", 6)) {
            G().song.clear();
            for (char* t = strtok(line + 6, " "); t; t = strtok(nullptr, " ")) G().song.push_back(atoi(t));
        } else if (!strncmp(line, "host_stream ", 12)) {
            G().hostStream = line + 12;
        } else if (!strncmp(line, "stream ", 7)) {
            G().ourStream = line + 7;
        } else if (!strncmp(line, "replay_seconds ", 15)) {
            g_ReplaySeconds = (float)atof(line + 15);
        }
    }
    fclose(f);
    return !G().song.empty();
}

static void Install() {
    const char* e = getenv("HOOKSHOT_SOH");
    if (e && !strcmp(e, "0")) return;
    g_Dbg = getenv("HOOKSHOT_SOH_DEBUG") != nullptr;
    // only an enabled mod folder (the mod manager disables a mod by prefixing '_' or '-')
    if (FILE* p = popen("ls -d mods/[!_-]*/song_of_healing 2>/dev/null | head -1", "r")) {
        char b[512];
        if (fgets(b, sizeof b, p)) { b[strcspn(b, "\n")] = 0; G().dir = b; }
        pclose(p);
    }
    if (G().dir.empty()) return;
    if (!LoadConfig()) { OverlayLog("[soh] %s/song.cfg missing or empty; off\n", G().dir.c_str()); return; }
    if (!LoadPatches()) OverlayLog("[soh] %s/patches.bin missing or bad: the host's title and replay start stay\n", G().dir.c_str());
    auto f = ResolveGuestFns({0x832b3f20, 0x832b3f70, 0x80021a20, 0x82fe3df0, 0x832b3fd0});
    g_fBookGet = (GuestFn)f[0x832b3f20]; g_fScoreList = (GuestFn)f[0x832b3f70]; g_fListCount = (GuestFn)f[0x80021a20];
    g_fScoreType = (GuestFn)f[0x82fe3df0]; g_fScoreNotes = (GuestFn)f[0x832b3fd0];
    InstallInputHooks();
    g_SohFrame = Frame;
    if (!InstallGuestHooks({{0x832b7400, HookOcarinaMatch, &o_OcarinaMatch, "ocarina match"},
                            {0x832b4df0, HookOcarinaUpdate, &o_OcarinaUpdate, "ocarina update"},
                            {0x8337b730, HookMelodySound, &o_MelodySound, "melody sound"},
                            {0x82a7c980, HookMelodyQuery, &o_MelodyQuery, "melody type query"}})) return;
    if (uintptr_t a = ResolveHostSymbol("imp__ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci"))
        o_OpenFile = HookHost(a, HookOpenFile, "nn::fs::OpenFile");
    if (!o_OpenFile) OverlayLog("[soh] nn::fs::OpenFile not hooked: the host song's replay will sound\n");
    g_On = true;
    OverlayLog("[soh] Song of Healing on (%s, %zu notes, %zu memory patches)\n", G().dir.c_str(), G().song.size(),
               G().patches.size());
}

}  // namespace soh

static void SohInstall() { soh::Install(); }
