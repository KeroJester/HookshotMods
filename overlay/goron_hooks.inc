// -------------------------------------------------------------
// Goron Link: native gameplay hooks (MM 1:1 roll and punch combo)
// -------------------------------------------------------------
// Player action nodes (AI/Player.action.<name>.module.ainb -> native node classes), found 2026-11-09:
//   ExecutePlayerRolling (name getter f_828258f0, vtable around guest 0x849e9598):
//     enter  f_806d9a30, f_80950670, f_809509f0, f_809509b0 (once at roll start)
//     update f_80953e80, f_80953a70, f_80953de0, f_80953980, f_80953950, f_80953470, f_80953d60, f_80953930 (x2)
//     leave  f_809590b0
//   ExecutePlayerBasicAttack (name getter f_827b73b0, vtable around guest 0x849dcd08):
//     update f_827b6fd0 (x2 per frame), f_8094ece0;  leave f_807c0760
// In f_80953e80: x19 = this->[+8]->[+8]->[+0x1f0] (the player actor), x21 = x19->[+0x270]->[+0x358].

static GuestFn o_RollUpdate = nullptr;   // f_80953e80
static bool g_GoronEnabled = false;

// ---- animation drivers: the driven clips' curves read their keys from "GORONDRV" blocks in the loaded animation
// archives (mod_manager/bfres_anim.py Driver); writing a slot changes what every such clip shows this frame --------
static std::vector<uint64_t> g_DriverBlocks;   // guest addresses of slot 0
static void FindDriverBlocks() {
    static const unsigned char magic[16] = {'G','O','R','O','N','D','R','V',1,0,0,0};
    g_DriverBlocks.clear();
    const uint64_t base = 0x100000000000ULL;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    while (m && fgets(line, sizeof line, m)) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r') continue;
        lo = std::max<unsigned long>(lo, base); hi = std::min<unsigned long>(hi, base + (1ULL << 39));
        for (unsigned long w = lo; w < hi; w += (1UL << 30)) {          // 1 GB windows (the guest heap is one huge mapping)
            unsigned long we = std::min<unsigned long>(hi, w + (1UL << 30));
            vec.resize((we - w) >> 12);
            if (mincore((void*)w, we - w, vec.data()) != 0) continue;
            for (size_t pg = 0; pg < vec.size(); pg++) {
                if (!(vec[pg] & 1)) continue;
                const unsigned char* p = (const unsigned char*)(w + (pg << 12));
                for (int i = 0; i < 4096; i += 16)
                    if (!memcmp(p + i, magic, 12) && !memcmp(p + i + 12, magic + 12, 4))
                        g_DriverBlocks.push_back((uint64_t)(p + i + 16) - base);
            }
        }
    }
    if (m) fclose(m);
    OverlayLog("[driver] %zu animation driver blocks found\n", g_DriverBlocks.size());
}
static void SetDriver(int slot, float v) {
    for (uint64_t b : g_DriverBlocks) {
        uint64_t k = b + 16 * slot;
        GWrite<float>(k, v); GWrite<float>(k + 4, 1e-4f); GWrite<float>(k + 8, v + 1e-4f); GWrite<float>(k + 12, 0.0f);
    }
}

static void GoronOnRollUpdate(uint64_t actor);
static std::atomic<uint64_t> g_RollActorSeen{0};                 // player actor while a roll update runs
static std::chrono::steady_clock::time_point g_RollSeenAt;
static std::atomic<uint64_t> g_RollNode{0};              // the ExecutePlayerRolling node (set by the roll update hooks)

// ---- engine calls (resolved at install) -----------------------------------------------------------------------
static GuestFn g_CctSetLinearVelocity = nullptr;   // f_80559cd0 (cct, Vec3*)   CCT vtable +0x78
static GuestFn g_CctGetLinearVelocity = nullptr;   // f_838b3f40 (cct) -> Vec3* CCT vtable +0x120

static uint64_t PlayerCct(uint64_t actor) {
    uint64_t p = GRead<uint64_t>(actor + 0x270);
    for (uint64_t off : {0x50ULL, 0x20ULL, 0x10ULL, 0x8ULL}) {
        if (!p) return 0;
        p = GRead<uint64_t>(p + off);
    }
    return (p && GRead<uint64_t>(p) == 0x84c54f10ULL) ? p : 0;
}

// Call a guest function with x0 = a0 and x1 = a pointer to a Vec3 (carved out of the guest stack). Restores Ctx.
static void GuestCallVec3(GuestCtx* c, GuestFn fn, uint64_t a0, const float v[3]) {
    GuestCtx saved = *c;
    uint64_t arg = (c->x[31] - 64) & ~0xFULL;
    GWrite<float>(arg, v[0]); GWrite<float>(arg + 4, v[1]); GWrite<float>(arg + 8, v[2]); GWrite<float>(arg + 12, 0.0f);
    c->x[31] = arg - 64;
    c->x[0] = a0;
    c->x[1] = arg;
    fn(c);
    *c = saved;
}

static bool GuestCallGetVec3(GuestCtx* c, GuestFn fn, uint64_t a0, float out[3]) {
    GuestCtx saved = *c;
    c->x[31] = (c->x[31] - 128) & ~0xFULL;
    c->x[0] = a0;
    fn(c);
    uint64_t p = c->x[0];
    *c = saved;
    if (!p) return false;
    out[0] = GRead<float>(p); out[1] = GRead<float>(p + 4); out[2] = GRead<float>(p + 8);
    return true;
}

static bool ResolveEngineCalls() {
    auto f = ResolveGuestFns({0x80559cd0, 0x838b3f40});
    if (!f.count(0x80559cd0) || !f.count(0x838b3f40)) { OverlayLog("[goron] engine calls not found\n"); return false; }
    g_CctSetLinearVelocity = (GuestFn)f[0x80559cd0];
    g_CctGetLinearVelocity = (GuestFn)f[0x838b3f40];
    return true;
}

// ---- probe: HOOKSHOT_ROLL_PROBE=<file> dumps, on every roll update, the node and the actor structs ------------
static FILE* g_RollProbe = nullptr;

static void ProbeRollUpdate(GuestCtx* c) {
    uint64_t self = c->x[0];
    uint64_t a = GRead<uint64_t>(self + 8);
    uint64_t b = a ? GRead<uint64_t>(a + 8) : 0;
    uint64_t actor = b ? GRead<uint64_t>(b + 0x1f0) : 0;
    uint64_t comp = actor ? GRead<uint64_t>(actor + 0x270) : 0;
    uint64_t x21 = comp ? GRead<uint64_t>(comp + 0x358) : 0;
    o_RollUpdate(c);
    if (actor) { g_RollActorSeen = actor; g_RollSeenAt = std::chrono::steady_clock::now(); g_RollNode = self; }
    if (g_RollProbe && actor) {
        uint64_t hdr[6] = {0x45424F52504C4C52ULL /* RLLPROBE */, self, actor, comp, x21, (uint64_t)time(nullptr)};
        fwrite(hdr, 8, 6, g_RollProbe);
        fwrite(GPtr(self), 1, 0x200, g_RollProbe);
        fwrite(GPtr(actor), 1, 0x1000, g_RollProbe);
        fwrite(GPtr(x21), 1, 0x2000, g_RollProbe);
        uint32_t ret; memcpy(&ret, &c->vreg[0][0], 4);
        fwrite(&ret, 4, 1, g_RollProbe);
        fflush(g_RollProbe);
    }
}

// ---- position search: on the first roll update, find every copy of the actor's (x, y, z) in guest memory, then log
// those addresses each update (HOOKSHOT_POS_SEARCH=1) -------------------------------------------------------------
static std::vector<uint64_t> g_PosHits;
static int g_PosFrame = -1;

// Scan resident pages of guest memory in [center - span, center + span) for an exact (x, y, z) float triple.
static void SearchGuestFloats3(const float want[3], std::vector<uint64_t>& out, uint64_t center, uint64_t span) {
    const uint64_t base = 0x100000000000ULL;
    uint32_t w0, w1, w2;
    memcpy(&w0, &want[0], 4); memcpy(&w1, &want[1], 4); memcpy(&w2, &want[2], 4);
    FILE* m = fopen("/proc/self/maps", "r");
    if (!m) return;
    char line[512];
    uint64_t want_lo = (base + (center > span ? center - span : 0)) & ~0xFFFULL, want_hi = (base + center + span) & ~0xFFFULL;
    std::vector<unsigned char> vec;
    while (fgets(line, sizeof line, m) && out.size() < 256) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r') continue;
        lo = std::max<unsigned long>(lo, want_lo); hi = std::min<unsigned long>(hi, want_hi);
        if (lo >= hi) continue;
        size_t pages = (hi - lo) >> 12;
        vec.resize(pages);
        if (mincore((void*)lo, hi - lo, vec.data()) != 0) continue;
        for (size_t pg = 0; pg < pages && out.size() < 256; pg++) {
            if (!(vec[pg] & 1)) continue;
            const uint32_t* p = (const uint32_t*)(lo + (pg << 12));
            for (int i = 0; i < 1024 - 2; i++)                  // (triples crossing a page edge are missed)
                if (p[i] == w0 && p[i + 1] == w1 && p[i + 2] == w2) out.push_back((uint64_t)(p + i) - base);
        }
    }
    fclose(m);
}

static void PosSearchRollUpdate(GuestCtx* c) {
    uint64_t self = c->x[0];
    uint64_t a = GRead<uint64_t>(self + 8), b = a ? GRead<uint64_t>(a + 8) : 0;
    uint64_t actor = b ? GRead<uint64_t>(b + 0x1f0) : 0;
    o_RollUpdate(c);
    if (!actor) return;
    if (g_PosFrame < 0) {
        float w[3] = {GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320), GRead<float>(actor + 0x324)};
        SearchGuestFloats3(w, g_PosHits, actor, 1ULL << 30);
        OverlayLog("[pos] actor %llx start (%.3f %.3f %.3f): %zu copies\n", (unsigned long long)actor, w[0], w[1], w[2], g_PosHits.size());
        g_PosFrame = 0;
    }
    char line[8192]; int n = snprintf(line, sizeof line, "[pos f%d]", g_PosFrame++);
    for (uint64_t h : g_PosHits) {
        if (n > 8000) break;
        n += snprintf(line + n, sizeof line - n, " %llx:(%.2f,%.2f,%.2f)", (unsigned long long)h,
                      GRead<float>(h), GRead<float>(h + 4), GRead<float>(h + 8));
    }
    if (g_PosFrame < 40) OverlayLog("%s\n", line);
}

// ---- velocity experiment (HOOKSHOT_ROLL_VEL=<m/s>): force the roll's velocity along the facing every update -----
static float g_RollVel = 0.0f;
static uint64_t g_TestCct = 0;
static float g_TestFwd[2];
static std::chrono::steady_clock::time_point g_TestUntil;
static GuestFn o_CctSetLinearVelocity = nullptr;
static std::atomic<int> g_SetVelCalls{0};

// Every linear-velocity set on the player's controller while the test is live gets the roll velocity instead.
static void HookCctSetLinearVelocity(GuestCtx* c) {
    if (g_TestCct && c->x[0] == g_TestCct && std::chrono::steady_clock::now() < g_TestUntil) {
        uint64_t v = c->x[1];
        float was[3] = {GRead<float>(v), GRead<float>(v + 4), GRead<float>(v + 8)};
        GWrite<float>(v, g_TestFwd[0] * g_RollVel);
        GWrite<float>(v + 8, g_TestFwd[1] * g_RollVel);
        if (g_SetVelCalls++ < 60) OverlayLog("[vel] set call replaced (%.2f %.2f %.2f) -> (%.2f %.2f %.2f)\n", was[0], was[1], was[2],
                                             GRead<float>(v), GRead<float>(v + 4), GRead<float>(v + 8));
    }
    o_CctSetLinearVelocity(c);
}

static void VelRollUpdate(GuestCtx* c) {
    uint64_t self = c->x[0];
    uint64_t a = GRead<uint64_t>(self + 8), b = a ? GRead<uint64_t>(a + 8) : 0;
    uint64_t actor = b ? GRead<uint64_t>(b + 0x1f0) : 0;
    o_RollUpdate(c);
    if (!actor) return;
    static bool armed = false;
    if (!g_CctGetLinearVelocity) ResolveEngineCalls();
    if (!armed && getenv("HOS_WATCH") && getenv("HOOKSHOT_ARM_WATCH")) {          // data breakpoints on velocity x/z and position x/z (host prof.c)
        armed = true;
        if (FILE* w = fopen(getenv("HOS_WATCH"), "w")) {
            const char* offs = getenv("HOOKSHOT_WATCH_OFFS");   // actor offsets (hex, comma separated), default velocity/position
            if (!offs) offs = "388,390,31c,324";
            uint64_t wbase = actor;
            if (getenv("HOOKSHOT_WATCH_NODE")) wbase = self;     // offsets relative to the roll node instead
            if (getenv("HOOKSHOT_WATCH_P28")) {                  // relative to cct->[0x28] (forward dir/speed block)
                uint64_t cct = PlayerCct(actor);
                wbase = cct ? GRead<uint64_t>(cct + 0x28) : 0;
            }
            if (getenv("HOOKSHOT_WATCH_CCTVEL")) {               // relative to the controller's velocity storage
                uint64_t cct = PlayerCct(actor);
                GuestCtx saved = *c;
                c->x[31] = (c->x[31] - 128) & ~0xFULL; c->x[0] = cct;
                if (cct && g_CctGetLinearVelocity) g_CctGetLinearVelocity(c);
                wbase = c->x[0];
                *c = saved;
                OverlayLog("[vel] cct %llx velocity storage %llx\n", (unsigned long long)cct, (unsigned long long)wbase);
            }
            std::string o(offs);
            size_t p = 0;
            while (p < o.size()) {
                size_t e = o.find(',', p); if (e == std::string::npos) e = o.size();
                fprintf(w, "%llx ", (unsigned long long)(wbase + strtoull(o.substr(p, e - p).c_str(), nullptr, 16)));
                p = e + 1;
            }
            fprintf(w, "\n");
            fclose(w);
        }
    }
    if (const char* dump = getenv("HOOKSHOT_CCT_DUMP")) {        // per-frame dump of the controller objects
        static FILE* df = fopen(dump, "wb");
        uint64_t cct = PlayerCct(actor);
        if (df && cct) {
            uint64_t p20 = GRead<uint64_t>(cct + 0x20), p28 = GRead<uint64_t>(cct + 0x28);
            uint64_t hdr[4] = {cct, p20, p28, actor};
            fwrite(hdr, 8, 4, df);
            fwrite(GPtr(cct), 1, 0x800, df);
            fwrite(GPtr(p20), 1, 0x400, df);
            fwrite(GPtr(p28), 1, 0x800, df);
            fwrite(GPtr(actor + 0x300), 1, 0x100, df);
            fflush(df);
        }
    }
    g_RollActorSeen = actor; g_RollSeenAt = std::chrono::steady_clock::now(); g_RollNode = self;
    GoronOnRollUpdate(actor);
    if (getenv("HOOKSHOT_AS_LOG")) {                        // the animation-sequence layer the roll update drives
        uint64_t p = GRead<uint64_t>(actor + 0x270);
        for (uint64_t off : {0x40ULL, 0x18ULL, 0x18ULL, 0x40ULL}) p = p ? GRead<uint64_t>(p + off) : 0;
        uint64_t vt = p ? GRead<uint64_t>(p) : 0;
        static int n = 0;
        if (n++ < 3) OverlayLog("[as] layer %llx vtable %llx +0x80 -> %llx\n", (unsigned long long)p, (unsigned long long)vt,
                                (unsigned long long)(vt ? GRead<uint64_t>(vt + 0x80) : 0));
    }
    if (getenv("HOOKSHOT_ROLL_LOG"))
        OverlayLog("[roll] pos (%.3f %.3f %.3f)\n", GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320), GRead<float>(actor + 0x324));
    if (g_RollVel <= 0) return;
    uint64_t cct = PlayerCct(actor);
    g_TestCct = cct;
    g_TestFwd[0] = GRead<float>(actor + 0x330); g_TestFwd[1] = GRead<float>(actor + 0x348);
    g_TestUntil = std::chrono::steady_clock::now() + std::chrono::milliseconds(100);
    if (!cct || !g_CctSetLinearVelocity) { OverlayLog("[vel] no cct\n"); return; }
    float fx = GRead<float>(actor + 0x330), fz = GRead<float>(actor + 0x348);
    float cur[3] = {0, 0, 0};
    GuestCallGetVec3(c, g_CctGetLinearVelocity, cct, cur);
    float v[3] = {fx * g_RollVel, cur[1], fz * g_RollVel};        // keep the controller's vertical (gravity)
    GuestCallVec3(c, g_CctSetLinearVelocity, cct, v);
    OverlayLog("[vel] pos (%.3f %.3f %.3f) fwd (%.2f %.2f) cct v was (%.2f %.2f %.2f) set %.2f\n", GRead<float>(actor + 0x31c),
               GRead<float>(actor + 0x320), GRead<float>(actor + 0x324), fx, fz, cur[0], cur[1], cur[2], g_RollVel);
}

// ---- CCT diagnostics (HOOKSHOT_CCT_DIAG=1): log the player controller's per-frame method calls with arguments -----
static std::atomic<uint64_t> g_PlayerActor{0}, g_PlayerCct{0};
static GuestFn o_ActorApplyTransform = nullptr;      // f_80156ae0(actor, world3x4*, dt)
static GuestFn o_CctA8 = nullptr, o_CctE0 = nullptr, o_Cct68 = nullptr, o_Cct58 = nullptr;
static std::atomic<int> g_CctDiagLines{0};

static std::atomic<int64_t> g_CctvUntil{0};
static GuestFn g_CctChangePosition = nullptr;        // f_806d2530 (cct, Vec3*)  CCT vtable +0xe8
static float g_TeleportTo[3];
static std::atomic<int> g_TeleportPending{0};
// Ground pound: while airborne the height comes from MM's simulation (above the take-off point; MM stops horizontal
// motion for the whole jump, so he lands where he left). Applied with the CCT's changePosition before each step.
static std::atomic<bool> g_AirActive{false};
static std::atomic<float> g_AirY{0.0f};                  // metres above g_GroundY
static float g_GroundY = 0, g_AirX = 0, g_AirZ = 0;
static float g_AirLastTargetY = 0;                       // height last handed to changePosition (before the CCT's step)
static float g_AirTargetHist[4] = {0, 0, 0, 0};          // the last four (newest first): changePosition lands a frame later
static int g_AirTargetCount = 0;
static float g_AirTickPrevY = 0, g_AirTickPrevTarget = 0;   // actual height / delayed target at the previous MM tick
static bool g_AirTickPrevValid = false;
static float g_AirCorr = 0.0f;    // the CCT's step sags a forced height a little more each frame in the air (~0.035 m
                                  // per MM tick, from 0 at take-off): integral correction, reset per hop
static bool g_AirTargetValid = false;
       // HOOKSHOT_TELEPORT="x,y,z": once, when the player first moves

static void GoronFrame(uint64_t actor);
static bool IsLinkActor(uint64_t actor);
static bool GoronAirVelocity(float* mps);       // MM's velocity.y in m/s while the roll is airborne (defined below)
// probe (HOOKSHOT_ACTOR_LIST=1): name + position of every actor seen by f_80156ae0, once each
static void LogActorOnce(uint64_t actor) {
    static std::mutex mx; static std::vector<uint64_t> seen;
    std::lock_guard<std::mutex> lk(mx);
    for (uint64_t a : seen) if (a == actor) return;
    if (seen.size() > 400) return;
    seen.push_back(actor);
    char name[64] = "?";
    uint64_t comps = GRead<uint64_t>(actor + 0x270);
    uint64_t mv = comps ? GRead<uint64_t>(comps + 800) : 0;
    if (mv > 0x1d50 && mv < (1ULL << 39)) {
        uint64_t res = GRead<uint64_t>(mv - 0x1d50 + 16);
        if (res > 0x80000000ULL && res < (1ULL << 39)) {
            for (int i = 0; i < 63; i++) { char ch = GRead<char>(res + 88 + i); name[i] = ch; if (!ch) break; if (ch < 32 || ch > 126) { name[i] = 0; break; } }
            name[63] = 0;
        }
    }
    // name search: pointers (depth <= 2) from the actor's first 0x600 bytes to "Enemy_" / "Npc_" / "Obj_" strings
    auto isName = [](uint64_t q) {
        if (q < 0x80000000ULL || q >= (1ULL << 39) || (q & 0)) return false;
        char b[8]; for (int i = 0; i < 6; i++) b[i] = GRead<char>(q + i);
        return !memcmp(b, "Enemy_", 6) || !memcmp(b, "Npc_", 4) || !memcmp(b, "Obj_", 4) || !memcmp(b, "Player", 6);
    };
    for (int o = 0; o < 0x600 && name[0] == '?'; o += 8) {
        uint64_t p1 = GRead<uint64_t>(actor + o);
        if (p1 < 0x80000000ULL || p1 >= (1ULL << 39)) continue;
        if (isName(p1)) { snprintf(name, sizeof name, "@%x:", o); int k = strlen(name); for (int i = 0; i < 40 && k < 63; i++) { char ch = GRead<char>(p1 + i); if (ch < 32 || ch > 126) break; name[k++] = ch; } name[k] = 0; break; }
        for (int o2 = 0; o2 < 0x80; o2 += 8) {
            uint64_t p2 = GRead<uint64_t>(p1 + o2);
            if (isName(p2)) { snprintf(name, sizeof name, "@%x/%x:", o, o2); int k = strlen(name); for (int i = 0; i < 40 && k < 63; i++) { char ch = GRead<char>(p2 + i); if (ch < 32 || ch > 126) break; name[k++] = ch; } name[k] = 0; break; }
        }
    }
    OverlayLog("[actors] %llx comps %llx name '%s' pos %.2f %.2f %.2f vt %llx\n", (unsigned long long)actor,
               (unsigned long long)comps, name, GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320), GRead<float>(actor + 0x324),
               (unsigned long long)GRead<uint64_t>(actor));
}

// probe: HOOKSHOT_ACTOR_DUMP=<name substring>; when the file HOOKSHOT_ACTOR_DUMP_FILE appears (its contents = output
// path), the matching actor's component table (0x1000 bytes) and 0x800 bytes of every object it points to are written
static void ActorDumpProbe(uint64_t actor) {
    static const char* want = getenv("HOOKSHOT_ACTOR_DUMP");
    static const char* trig = getenv("HOOKSHOT_ACTOR_DUMP_FILE");
    if (!want || !trig || access(trig, F_OK) != 0) return;
    static std::mutex dmx; std::lock_guard<std::mutex> dlk(dmx);
    if (access(trig, F_OK) != 0) return;
    uint64_t pl = g_RollActorSeen.load();
    if (pl) {                                              // only the one next to Link
        float dx = GRead<float>(actor + 0x31c) - GRead<float>(pl + 0x31c), dz = GRead<float>(actor + 0x324) - GRead<float>(pl + 0x324);
        if (dx * dx + dz * dz > 36.0f) return;
    }
    uint64_t p1 = GRead<uint64_t>(actor + 8);
    if (p1 < 0x80000000ULL || p1 >= (1ULL << 39)) return;
    uint64_t np = GRead<uint64_t>(p1 + 0x20);
    if (np < 0x80000000ULL || np >= (1ULL << 39)) return;
    char name[64] = {0};
    for (int i = 0; i < 63; i++) { name[i] = GRead<char>(np + i); if (!name[i]) break; }
    if (!strstr(name, want)) return;
    char out[512] = {0};
    if (FILE* t = fopen(trig, "r")) { if (!fgets(out, sizeof out, t)) out[0] = 0; fclose(t); }
    unlink(trig);
    out[strcspn(out, "\n")] = 0;
    if (!out[0]) return;
    if (!strncmp(out, "life=", 5)) {                       // test: set this actor's life (comps+0x408 -> +0x60)
        uint64_t comps = GRead<uint64_t>(actor + 0x270), lc = GRead<uint64_t>(comps + 0x408);
        int v = atoi(out + 5);
        OverlayLog("[dump] %s actor %llx: life %d -> %d\n", name, (unsigned long long)actor, GRead<int32_t>(lc + 0x60), v);
        GWrite<int32_t>(lc + 0x60, v);
        return;
    }
    if (!strncmp(out, "watchc=", 7)) {                     // watchc=<comps slot>,<field>: arm HOS_WATCH there
        unsigned slot = 0, fld = 0;
        sscanf(out + 7, "%x,%x", &slot, &fld);
        uint64_t comps = GRead<uint64_t>(actor + 0x270), obj = GRead<uint64_t>(comps + slot);
        if (const char* w = getenv("HOS_WATCH")) if (FILE* wf = fopen(w, "w")) { fprintf(wf, "%llx\n", (unsigned long long)(obj + fld)); fclose(wf); }
        OverlayLog("[dump] %s actor %llx: watching comps+%x -> %llx +%x\n", name, (unsigned long long)actor, slot,
                   (unsigned long long)obj, fld);
        return;
    }
    if (!strcmp(out, "watch")) {                           // arm HOS_WATCH on this actor's life (comps+0x408 -> +0x60)
        uint64_t comps = GRead<uint64_t>(actor + 0x270), lc = GRead<uint64_t>(comps + 0x408);
        if (const char* w = getenv("HOS_WATCH")) if (FILE* wf = fopen(w, "w")) { fprintf(wf, "%llx\n", (unsigned long long)(lc + 0x60)); fclose(wf); }
        OverlayLog("[dump] %s actor %llx: watching life at %llx = %d\n", name, (unsigned long long)actor,
                   (unsigned long long)(lc + 0x60), GRead<int32_t>(lc + 0x60));
        return;
    }
    FILE* f = fopen(out, "wb");
    if (!f) return;
    uint64_t comps = GRead<uint64_t>(actor + 0x270);
    auto blk = [&](uint64_t addr, uint32_t len) { fwrite(&addr, 8, 1, f); fwrite(&len, 4, 1, f); fwrite(GPtr(addr), 1, len, f); };
    blk(actor, 0x800);
    blk(comps, 0x1000);
    std::vector<uint64_t> lvl1, done;
    for (int o = 0; o < 0x1000; o += 8) {
        uint64_t q = GRead<uint64_t>(comps + o);
        if (q >= 0x1000000000ULL && q < 0x1100000000ULL) { blk(q, 0x800); lvl1.push_back(q); done.push_back(q); }
    }
    static const bool deep = getenv("HOOKSHOT_ACTOR_DUMP_DEEP") != nullptr;
    if (deep)                                              // one more level, deduplicated, capped
        for (uint64_t q : lvl1)
            for (int o = 0; o < 0x800 && done.size() < 4000; o += 8) {
                uint64_t r = GRead<uint64_t>(q + o);
                if (r < 0x1000000000ULL || r >= 0x1100000000ULL) continue;
                bool dup = false;
                for (uint64_t d0 : done) if (r >= d0 && r < d0 + 0x400) { dup = true; break; }
                if (dup) continue;
                blk(r, 0x400); done.push_back(r);
            }
    fclose(f);
    OverlayLog("[dump] %s actor %llx -> %s\n", name, (unsigned long long)actor, out);
}

// probe: HOOKSHOT_CCT_DUMP_FILE=<trigger>; when it appears (contents = output path), the player's CCT, cct->[0x28],
// cct->[0x48] and the objects they point to (0x400 each, two levels) are written (same block format as the actor dump)
static void CctDumpProbe(uint64_t actor) {
    static const char* trig = getenv("HOOKSHOT_CCT_DUMP_FILE");
    if (!trig || access(trig, F_OK) != 0 || actor != g_RollActorSeen.load()) return;
    char out[512] = {0};
    if (FILE* t = fopen(trig, "r")) { if (!fgets(out, sizeof out, t)) out[0] = 0; fclose(t); }
    unlink(trig);
    out[strcspn(out, "\n")] = 0;
    uint64_t cct = PlayerCct(actor);
    if (!out[0] || !cct) return;
    FILE* f = fopen(out, "wb");
    if (!f) return;
    std::vector<uint64_t> done;
    auto blk = [&](uint64_t addr, uint32_t len) {
        for (uint64_t d0 : done) if (addr == d0) return;
        done.push_back(addr); fwrite(&addr, 8, 1, f); fwrite(&len, 4, 1, f); fwrite(GPtr(addr), 1, len, f);
    };
    std::vector<uint64_t> roots = {cct, GRead<uint64_t>(cct + 0x28), GRead<uint64_t>(cct + 0x48), GRead<uint64_t>(cct + 0x20)};
    for (uint64_t r : roots) if (r >= 0x1000000000ULL && r < 0x1100000000ULL) blk(r, 0x800);
    for (uint64_t r : roots) {
        if (r < 0x1000000000ULL || r >= 0x1100000000ULL) continue;
        for (int o = 0; o < 0x800; o += 8) {
            uint64_t q = GRead<uint64_t>(r + o);
            if (q >= 0x1000000000ULL && q < 0x1100000000ULL) blk(q, 0x400);
        }
    }
    fclose(f);
    OverlayLog("[cctdump] -> %s (%zu blocks)\n", out, done.size());
}

static void HookActorApplyTransform(GuestCtx* c) {
    uint64_t actor = c->x[0];
    ActorDumpProbe(actor);
    CctDumpProbe(actor);
    static const bool listActors = getenv("HOOKSHOT_ACTOR_LIST") != nullptr;
    if (listActors) LogActorOnce(actor);
    if (g_GoronEnabled && IsLinkActor(actor)) GoronFrame(actor);
    bool player = actor && actor == g_RollActorSeen.load();     // Link, as identified by the roll node
    static const bool tpTest = getenv("HOOKSHOT_TELEPORT_FILE") != nullptr;
    if (!player && tpTest && actor && !g_RollActorSeen.load()) {   // (tests without the Goron mod: Link by actor name)
        uint64_t info = GRead<uint64_t>(actor + 8), name = info ? GRead<uint64_t>(info + 0x20) : 0;
        player = name && !strncmp((const char*)GPtr(name), "Player", 6);
    }
    if (player) {
        uint64_t cct = PlayerCct(actor);
        if (cct) { g_PlayerActor = actor; g_PlayerCct = cct; }
    }
    static const char* trigger = getenv("HOOKSHOT_TELEPORT_FILE");   // teleport when this file appears (repeatable;
    if (player && trigger && g_CctChangePosition && g_PlayerCct.load() && access(trigger, F_OK) == 0) {   // "x,y,z" inside
        if (FILE* tf = fopen(trigger, "r")) {
            float t[3];
            if (fscanf(tf, "%f,%f,%f", &t[0], &t[1], &t[2]) == 3) memcpy(g_TeleportTo, t, sizeof t);
            fclose(tf);
        }
        g_TeleportPending = 1;
    }
    static const bool velTestHook = getenv("HOOKSHOT_VY_TEST") != nullptr;
    if (player && velTestHook && g_PlayerCct.load()) {
        float v;
        if (GoronAirVelocity(&v)) GWrite<float>(g_PlayerCct.load() + 0x2d4, v);
    }
    if (player && !velTestHook && g_AirActive.load() && g_CctChangePosition && g_PlayerCct.load()) {
        float to[3] = {GRead<float>(actor + 0x31c), g_GroundY + g_AirY.load(), GRead<float>(actor + 0x324)};
        if (g_AirTargetCount == 0) g_AirCorr = 0.0f;           // take-off
        g_AirLastTargetY = to[1]; g_AirTargetValid = true;      // (history holds the arc itself; the CCT gets +corr)
        for (int i = 3; i > 0; i--) g_AirTargetHist[i] = g_AirTargetHist[i - 1];
        g_AirTargetHist[0] = to[1];
        if (g_AirTargetCount < 4) g_AirTargetCount++;
        static const bool airDbg = getenv("HOOKSHOT_AIR_DEBUG") != nullptr;
        if (airDbg) OverlayLog("[air] frame y %.3f target %.3f airY %.3f ground %.3f\n",
                               GRead<float>(actor + 0x320), to[1], g_AirY.load(), g_GroundY);
        GWrite<float>(g_PlayerCct.load() + 0x2d4, 0.0f);   // the CCT's own airborne fall speed: MM's arc is in charge
                                                        // (left alone it accumulates gravity and drags each step down)
        to[1] += g_AirCorr;
        GuestCallVec3(c, g_CctChangePosition, g_PlayerCct.load(), to);
    }
    if (player && g_TeleportPending.load() && g_CctChangePosition && g_PlayerCct.load()) {
        if ((!trigger || access(trigger, F_OK) == 0) && g_TeleportPending.exchange(0)) {
            if (trigger) unlink(trigger);
            GuestCallVec3(c, g_CctChangePosition, g_PlayerCct.load(), g_TeleportTo);   // before the step, as Hookshot's fly cheat
            OverlayLog("[tp] player moved to (%.2f %.2f %.2f)\n", g_TeleportTo[0], g_TeleportTo[1], g_TeleportTo[2]);
        }
    }
    o_ActorApplyTransform(c);
}

static void LogCctCall(const char* what, GuestCtx* c) {
    if (c->x[0] != g_PlayerCct.load() || g_CctDiagLines++ > 400) return;
    uint64_t a1 = c->x[1];
    float f[4] = {0, 0, 0, 0};
    if (a1 > 0x80000000ULL && a1 < (1ULL << 39)) for (int i = 0; i < 4; i++) f[i] = GRead<float>(a1 + 4 * i);
    OverlayLog("[cct] %s x1=%llx [%.3f %.3f %.3f %.3f] x2=%llx w3=%x s0=%.3f s1=%.3f pos(%.2f %.2f %.2f)\n", what,
               (unsigned long long)a1, f[0], f[1], f[2], f[3], (unsigned long long)c->x[2], (unsigned)c->x[3], VregF(c, 0), VregF(c, 1),
               GRead<float>(g_PlayerActor + 0x31c), GRead<float>(g_PlayerActor + 0x320), GRead<float>(g_PlayerActor + 0x324));
}
static void HookCctA8(GuestCtx* c) { LogCctCall("+a8", c); o_CctA8(c); }
static void HookCctE0(GuestCtx* c) { LogCctCall("+e0", c); o_CctE0(c); }
static void HookCct68(GuestCtx* c) { LogCctCall("+68", c); o_Cct68(c); }
static void HookCct58(GuestCtx* c) { LogCctCall("+58", c); o_Cct58(c); }

// ---- forward speed experiment (HOOKSHOT_ROLL_SPEED=<m/s>): after the engine's movement step (f_809552f0) writes
// the controller's forward speed (cct->[0x28] + 0x1d8), override it while a roll is running -----------------------
static GuestFn o_MoveStep = nullptr;                 // f_809552f0
static float g_RollSpeed = 0.0f;

static void GoronAfterMoveStep();
static void HookMoveStep(GuestCtx* c) {
    o_MoveStep(c);
    GoronAfterMoveStep();
    uint64_t actor = g_RollActorSeen.load();
    if (!actor || g_RollSpeed <= 0 || std::chrono::steady_clock::now() - g_RollSeenAt > std::chrono::milliseconds(80)) return;
    uint64_t cct = PlayerCct(actor);
    uint64_t p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
    if (p28) GWrite<float>(p28 + 0x1d8, g_RollSpeed);
}

// ---- node tick (f_8054dd00, Execute nodes' vtable +0x80): log / override the roll node's returned status ---------
static GuestFn o_NodeTick = nullptr;
static int g_HoldRollFrames = 0;                         // HOOKSHOT_HOLD_ROLL=<n>: keep reporting "running" n more ticks

static void HookNodeTick(GuestCtx* c) {
    uint64_t self = c->x[0];
    o_NodeTick(c);
    if (self && self == g_RollNode.load()) {
        uint32_t st = (uint32_t)c->x[0];
        static int ticks = 0;
        if (st != 0 && g_HoldRollFrames > 0) {
            OverlayLog("[tick] roll node status %u -> 0 (held, %d left)\n", st, g_HoldRollFrames);
            g_HoldRollFrames--;
            c->x[0] = 0;
        } else if (st != 0 || (ticks++ % 20) == 0) {
            OverlayLog("[tick] roll node status %u\n", st);
        }
    }
}

// ---- input: the game's controller reads (Hookshot HLE nn::hid::GetNpadStates, NpadFullKeyState 0x28 bytes:
// i64 sampling, u64 buttons (bit 0 = A), i32 lx, ly, rx, ry, u32 attributes, u32 pad) --------------------------------
static GuestFn o_GetNpadFullKey = nullptr, o_GetNpadHandheld = nullptr, o_GetNpadJoyDual = nullptr;
static std::chrono::steady_clock::time_point g_SuppressAUntil;   // report A as released until then
static std::atomic<bool> g_RealAHeld{false};
static std::atomic<bool> g_RealYHeld{false};
static std::atomic<bool> g_RealBHeld{false};             // Y = this game's attack button = MM's B (ground pound)
static std::atomic<bool> g_RollOwnsY{false};             // while rolling, Y belongs to the roll (hidden from the game)
static std::atomic<int32_t> g_StickX{0}, g_StickY{0};       // left stick, -32767..32767
// Song of Healing (song_of_healing.inc): buttons hidden from / pressed for the game during its performance
static std::atomic<uint64_t> g_SohInputMask{0}, g_SohInputForce{0};
static std::atomic<bool> g_SohRealB{false};
static void (*g_SohFrame)() = nullptr;                 // its timeline, advanced on every controller read

// Samples are a history (newest first) keyed by their sampling number, and the game derives presses from consecutive
// samples, so a sample once reported with A released must stay released in every later read.
static uint64_t g_ReleasedSamples[128];
static int g_ReleasedHead = 0;
static bool SampleReleased(uint64_t n) {
    for (uint64_t s : g_ReleasedSamples) if (s == n && n) return true;
    return false;
}

static void FilterNpad(GuestFn orig, GuestCtx* c) {
    uint64_t out = c->x[0];
    int count = (int)c->x[1];
    orig(c);
    int got = (int)c->x[0];
    if (got <= 0 || got > count || got > 64) return;
    uint64_t b = GRead<uint64_t>(out + 8);                 // newest state first
    g_RealAHeld = (b & 1) != 0;
    g_RealYHeld = (b & 8) != 0;
    g_RealBHeld = (b & 2) != 0;
    g_SohRealB = (b & 2) != 0;
    if (g_SohFrame) g_SohFrame();
    if (uint64_t m = g_SohInputMask.load() | g_SohInputForce.load()) {
        const uint64_t keep = ~g_SohInputMask.load(), force = g_SohInputForce.load();
        for (int i = 0; i < got; i++) GWrite<uint64_t>(out + 0x28 * i + 8, (GRead<uint64_t>(out + 0x28 * i + 8) & keep) | force);
        (void)m;
    }
    if (g_RollOwnsY.load())
        for (int i = 0; i < got; i++) GWrite<uint64_t>(out + 0x28 * i + 8, GRead<uint64_t>(out + 0x28 * i + 8) & ~8ULL);
    g_StickX = GRead<int32_t>(out + 0x10); g_StickY = GRead<int32_t>(out + 0x14);
    static const bool always = getenv("HOOKSHOT_SUPPRESS_A_ALWAYS") != nullptr;
    bool window = always || std::chrono::steady_clock::now() < g_SuppressAUntil;
    for (int i = 0; i < got; i++) {
        uint64_t e = out + 0x28 * i;
        uint64_t n = GRead<uint64_t>(e);
        bool rel = SampleReleased(n);
        if (!rel && window && i == 0) {                    // newest sample inside the window: release it for good
            g_ReleasedSamples[g_ReleasedHead++ & 127] = n;
            rel = true;
        }
        if (rel) GWrite<uint64_t>(e + 8, GRead<uint64_t>(e + 8) & ~1ULL);
    }
}
static void HookNpadFullKey(GuestCtx* c) { FilterNpad(o_GetNpadFullKey, c); }
static void HookNpadHandheld(GuestCtx* c) { FilterNpad(o_GetNpadHandheld, c); }
static void HookNpadJoyDual(GuestCtx* c) { FilterNpad(o_GetNpadJoyDual, c); }

static bool InstallInputHooks() {
    if (o_GetNpadFullKey) return true;                      // (the Goron roll and the Song of Healing share it)
    // the GetNpadStates imports are jmp stubs into one static host function
    uintptr_t a = ResolveHostSymbol("npad_states");
    GuestFn t = a ? HookHost(a, HookNpadFullKey, "npad_states") : nullptr;
    if (!t) { OverlayLog("[input] could not hook npad_states\n"); return false; }
    o_GetNpadFullKey = t;
    OverlayLog("[input] controller hook installed (npad_states)\n");
    return true;
}

// ---- roll chaining (HOOKSHOT_ROLL_CHAIN=1): when a roll ends with A still held, release A for one controller read
static GuestFn o_RollLeave = nullptr;      // f_809590b0
static void HookRollLeave(GuestCtx* c) {
    o_RollLeave(c);
    if (g_RealAHeld.load()) {
        g_SuppressAUntil = std::chrono::steady_clock::now() + std::chrono::milliseconds(getenv("HOOKSHOT_CHAIN_MS") ? atoi(getenv("HOOKSHOT_CHAIN_MS")) : 34);
        OverlayLog("[chain] roll ended with A held: re-press\n");
    }
}

// ---- MM Goron roll controller (HOOKSHOT_GORON_ROLL=1) ------------------------------------------------------------
#include "goron_roll.inc"
#include "goron_audio.inc"
static std::mutex g_GoronMx;
static goron_roll::State g_Goron;
static std::chrono::steady_clock::time_point g_GoronLast;
static double g_GoronAcc = 0;

static goron_roll::Input GoronInput() {
    goron_roll::Input in{0, 0};
    float sx = g_StickX.load() / 32767.0f, sy = g_StickY.load() / 32767.0f;
    float m = sqrtf(sx * sx + sy * sy);
    if (m < 0.12f) return in;                                  // dead zone
    in.stickMag = std::min(60.0f, m * 60.0f);
    float rx = 1, rz = 0;                                      // camera right axis (Hookshot's oot_gameplay_view)
    uint64_t single = GRead<uint64_t>(0x84ca6bc0ULL), mgr = single ? GRead<uint64_t>(single) : 0;
    uint64_t cams = (mgr && GRead<uint32_t>(mgr + 32)) ? GRead<uint64_t>(mgr + 40) : 0;
    uint64_t cam = cams ? GRead<uint64_t>(cams) : 0, view = cam ? GRead<uint64_t>(cam + 120) : 0;
    if (view && GRead<uint64_t>(view) == 0x84c18a88ULL) {
        float x = GRead<float>(view + 8), z = GRead<float>(view + 16), l = hypotf(x, z);
        if (l > 0.01f) { rx = x / l; rz = z / l; }
    }
    float dx = rx * sx + rz * sy, dz = rz * sx - rx * sy;      // (Hookshot's desired[0], desired[2])
    in.stickYaw = goron_roll::Atan2S_XY(dz, dx);
    return in;
}

// ---- MM animation player (pose tables from goron_driver.bin) ----------------------------------------------------
struct DriverTables {
    bool ok = false;
    int nbones = 0;
    float centreY = 0, hidden[3] = {}, sklBind[3] = {}, waistBind[3] = {};
    std::vector<float> boneBind;                      // nbones * 3
    int iSkl = -1, iWaist = -1, iBall = -1, iSpike = -1, iFire = -1;           // skeleton bone indices (v2)
    std::vector<int> iBones, iBody;
    struct Root { int bone; float t[3]; };
    std::vector<Root> roots;                          // v3: chain roots moved away while curled
    struct Anim { std::string name; int frames = 0; std::vector<float> data; };   // frames * (6 + 3 nbones)
    std::vector<Anim> anims;
    const Anim* find(const char* n) const { for (auto& a : anims) if (a.name == n) return &a; return nullptr; }
};
static DriverTables g_Tables;

static void LoadDriverTables() {
    FILE* f = nullptr;
    char path[512];
    if (FILE* p = popen("ls mods/[!_-]*/goron_driver.bin 2>/dev/null | head -1", "r")) {
        if (fgets(path, sizeof path, p)) { path[strcspn(path, "\n")] = 0; f = fopen(path, "rb"); }
        pclose(p);
    }
    if (!f) { OverlayLog("[anim] goron_driver.bin not found\n"); return; }
    char magic[4]; uint32_t ver, nb, na;
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, "GDRV", 4) || fread(&ver, 4, 1, f) != 1 || fread(&nb, 4, 1, f) != 1 ||
        fread(&na, 4, 1, f) != 1) { fclose(f); return; }
    g_Tables.nbones = (int)nb;
    fread(&g_Tables.centreY, 4, 1, f); fread(g_Tables.hidden, 4, 3, f); fread(g_Tables.sklBind, 4, 3, f); fread(g_Tables.waistBind, 4, 3, f);
    g_Tables.boneBind.resize(nb * 3); fread(g_Tables.boneBind.data(), 4, nb * 3, f);
    if (ver >= 2) {
        uint32_t three[3]; fread(three, 4, 3, f);
        g_Tables.iSkl = (int)three[0]; g_Tables.iWaist = (int)three[1]; g_Tables.iBall = (int)three[2];
        std::vector<uint32_t> bi(nb); fread(bi.data(), 4, nb, f);
        g_Tables.iBones.assign(bi.begin(), bi.end());
        uint32_t nbody = 0; fread(&nbody, 4, 1, f);
        std::vector<uint32_t> bd(nbody); fread(bd.data(), 4, nbody, f);
        g_Tables.iBody.assign(bd.begin(), bd.end());
    }
    if (ver >= 3) {
        uint32_t nr = 0; fread(&nr, 4, 1, f);
        for (uint32_t i = 0; i < nr; i++) {
            uint32_t bi; float t[3]; fread(&bi, 4, 1, f); fread(t, 4, 3, f);
            g_Tables.roots.push_back({(int)bi, {t[0], t[1], t[2]}});
        }
    }
    if (ver >= 4) { uint32_t sp = 0; fread(&sp, 4, 1, f); g_Tables.iSpike = (int)sp; }
    if (ver >= 5) { uint32_t fb = 0; fread(&fb, 4, 1, f); g_Tables.iFire = (int)fb; }
    for (uint32_t i = 0; i < na; i++) {
        DriverTables::Anim a; char name[24]; uint32_t fr;
        fread(name, 1, 24, f); fread(&fr, 4, 1, f);
        a.name.assign(name, strnlen(name, 24)); a.frames = (int)fr;
        a.data.resize(fr * (6 + 3 * nb)); fread(a.data.data(), 4, a.data.size(), f);
        g_Tables.anims.push_back(std::move(a));
    }
    fclose(f);
    g_Tables.ok = true;
    OverlayLog("[anim] driver tables loaded from %s (%u bones, %u animations)\n", path, nb, na);
}

enum { kSlotSklRot = 0, kSlotSklScale = 3, kSlotSklTrans = 6, kSlotWaistRot = 9, kSlotBallTrans = 12, kSlotBodyScale = 15,
       kSlotBone0 = 18 };

static void SetDriver3(int slot, float x, float y, float z) { SetDriver(slot, x); SetDriver(slot + 1, y); SetDriver(slot + 2, z); }

// ---- pose override: written into Link's local pose (96-byte records: scale, 3 rotation rows, translation, pad; per
// skeleton bone) right before the engine's local->world pass f_81501400(skeleton instance, first bone, end bone, ..)
// computes the world matrices. [instance] = model resource (->[0x10] bone array, 0x58 stride), [instance+0x18] = local
// pose, [instance+0x20] = world matrices (64-byte). This is how MM's Player_Draw poses the curled ball, in code.
struct BonePose { int bone; int what; float s[3]; float R[9]; float t[3]; };   // what: 1 scale, 2 rotation, 4 translation
static std::vector<BonePose> g_Pose;          // current frame's override (empty = the game's own animation)
static std::mutex g_PoseMx;
static GuestFn o_LocalToWorld = nullptr;      // f_81501400

static void EulerToR(float x, float y, float z, float R[9]) {     // bfres order R = Rz Ry Rx
    float cx = cosf(x), sx = sinf(x), cy = cosf(y), sy = sinf(y), cz = cosf(z), sz = sinf(z);
    R[0] = cz * cy; R[1] = cz * sy * sx - sz * cx; R[2] = cz * sy * cx + sz * sx;
    R[3] = sz * cy; R[4] = sz * sy * sx + cz * cx; R[5] = sz * sy * cx - cz * sx;
    R[6] = -sy;     R[7] = cy * sx;                R[8] = cy * cx;
}

static inline bool GuestPtrOk(uint64_t p) { return p >= 0x80000000ULL && p < (1ULL << 39) && !(p & 1); }

static bool BoneNameIs(uint64_t boneArr, int i, const char* want) {
    if (!GuestPtrOk(boneArr)) return false;
    uint64_t np = GRead<uint64_t>(boneArr + 0x58ULL * i);
    if (!GuestPtrOk(np)) return false;
    uint16_t len = GRead<uint16_t>(np);
    if (len == 0 || len > 64) return false;
    size_t wl = strlen(want);
    return len == wl && !memcmp(GPtr(np + 2), want, wl);
}

static void HookLocalToWorld(GuestCtx* c) {
    uint64_t inst = c->x[0];
    const int argFirst = (int)c->x[1], argEnd = (int)c->x[2];     // (the call clobbers the registers)
    static uint64_t linkRes[4] = {};
    if (GuestPtrOk(inst) && g_Tables.iBall >= 0) {
        uint64_t res = GRead<uint64_t>(inst);
        bool link = false;
        for (uint64_t r : linkRes) if (r && r == res) link = true;
        if (!link && GuestPtrOk(res)) {
            // only skeletons with at least (ball index + 1) bones: the range end (w2) of this pass bounds the bone count
            uint64_t arr = GRead<uint64_t>(res + 0x10);
            if ((int)c->x[2] > g_Tables.iBall && BoneNameIs(arr, g_Tables.iSkl, "Skl_Root") &&
                BoneNameIs(arr, g_Tables.iBall, "Ph_Leg_Support_Waist_L")) {
                for (auto& r : linkRes) if (!r) { r = res; break; }
                extern uint64_t g_LinkResDbg; if (!g_LinkResDbg) g_LinkResDbg = res;
                link = true;
                OverlayLog("[pose] Link skeleton resource %llx\n", (unsigned long long)res);
            }
        }
        if (link) {
            uint64_t local = GRead<uint64_t>(inst + 0x18);
            if (!GuestPtrOk(local)) { o_LocalToWorld(c); return; }
            // One skeleton evaluation is several calls over bone ranges (first = 0 starts it). Snapshot the override at
            // the start and use it for the whole evaluation: a mode switch between two ranges would mix the ball pose
            // with the body pose in one frame (a torn, "exploded" mesh).
            struct Eval { std::vector<BonePose> pose; int lastFirst = 1 << 30; };
            static std::unordered_map<uint64_t, Eval> evalByInst;   // (shared: ranges may run on other threads)
            int first = (int)c->x[1], end = (int)c->x[2];
            std::vector<BonePose> eval;
            {
                std::lock_guard<std::mutex> lk(g_PoseMx);
                Eval& e = evalByInst[inst];
                if (first <= e.lastFirst) e.pose = g_Pose;          // ranges ascend within one evaluation
                e.lastFirst = first;
                eval = e.pose;
            }
            // record (f_81501400): +0x00 u32 flags (bit 23 segment scale compensate; 0x0F000000 = scale-uniform /
            // scale-one / rotation-zero / translation-zero shortcuts), +0x10 scale, +0x20/+0x30/+0x40 local matrix
            // columns (rotation * scale), +0x50 translation
            for (const BonePose& p : eval) {
                if (p.bone < first || p.bone >= end) continue;          // only this range's bones (not yet evaluated)
                uint64_t r = local + 0x60ULL * p.bone;
                float sc[3] = {GRead<float>(r + 0x10), GRead<float>(r + 0x14), GRead<float>(r + 0x18)};
                if (GRead<uint32_t>(r) & 0x02000000u) sc[0] = sc[1] = sc[2] = 1.0f;     // scale one: field unfilled
                float R[9];
                uint32_t flags0 = GRead<uint32_t>(r);
                if (p.what & 2) memcpy(R, p.R, sizeof R);
                else if (flags0 & 0x04000000u) { float I[9] = {1,0,0, 0,1,0, 0,0,1}; memcpy(R, I, sizeof R); }   // rotation zero
                else for (int col = 0; col < 3; col++) for (int row = 0; row < 3; row++) {   // current rotation (unscale)
                    float sv = sc[col] != 0 ? sc[col] : 1.0f;
                    R[3 * row + col] = GRead<float>(r + 0x20 + 0x10 * col + 4 * row) / sv;
                }
                if (p.what & 1) memcpy(sc, p.s, sizeof sc);
                if (p.what & 3) {
                    GWrite<float>(r + 0x10, sc[0]); GWrite<float>(r + 0x14, sc[1]); GWrite<float>(r + 0x18, sc[2]);
                    for (int col = 0; col < 3; col++) for (int row = 0; row < 3; row++)
                        GWrite<float>(r + 0x20 + 0x10 * col + 4 * row, R[3 * row + col] * sc[col]);
                }
                if (p.what & 4) { GWrite<float>(r + 0x50, p.t[0]); GWrite<float>(r + 0x54, p.t[1]); GWrite<float>(r + 0x58, p.t[2]); }
                // drop only the shortcut flags for what was written (0x1000000 scale uniform, 0x2000000 scale one,
                // 0x4000000 rotation zero, 0x8000000 translation zero): with a shortcut set the engine leaves those
                // fields unfilled, so clearing a shortcut for data we did not write would expose stale values
                uint32_t clear = ((p.what & 3) ? 0x07000000u : 0u) | ((p.what & 4) ? 0x08000000u : 0u);
                GWrite<uint32_t>(r, GRead<uint32_t>(r) & ~clear);
            }
        }
    }
    o_LocalToWorld(c);
    // (HOOKSHOT_POSE_DEBUG=1) world scale/translation of Skl_Root, Waist and the ball for a few frames after mode changes
    extern int g_PoseDebugFrames;
    extern uint64_t g_LinkResDbg;
    if (g_PoseDebugFrames > 0 && GuestPtrOk(inst) && GRead<uint64_t>(inst) == g_LinkResDbg) {
        uint64_t world = GRead<uint64_t>(inst + 0x20);
        int first = argFirst, end = std::min(argEnd, 309);
        if (GuestPtrOk(world) && first >= 0 && first < end) {
            char line[2048]; int n = snprintf(line, sizeof line, "[posedbg] range %d-%d:", first, end);
            for (int b = first; b < end && n < 1900; b++) {
                uint64_t m = world + 64ULL * b;
                float c0 = sqrtf(GRead<float>(m) * GRead<float>(m) + GRead<float>(m + 4) * GRead<float>(m + 4) + GRead<float>(m + 8) * GRead<float>(m + 8));
                float tx = GRead<float>(m + 48), ty = GRead<float>(m + 52), tz = GRead<float>(m + 56);
                if (!(c0 < 3.0f) || fabsf(tx) > 50 || fabsf(ty) > 50 || fabsf(tz) > 50)
                    n += snprintf(line + n, sizeof line - n, " b%d s%.2f t(%.1f %.1f %.1f)", b, c0, tx, ty, tz);
            }
            OverlayLog("%s\n", line);
            if (end > g_Tables.iBall) g_PoseDebugFrames--;
        }
    }
}

int g_PoseDebugFrames = 0;
uint64_t g_LinkResDbg = 0;
static void PoseClear() { std::lock_guard<std::mutex> lk(g_PoseMx); g_Pose.clear(); }

// Body pose from a table at fractional MM frame t (looping or clamped), visible body, hidden ball.
static void WriteBodyPose(const DriverTables::Anim* a, float t, bool loop) {
    if (!a || a->frames < 1 || g_Tables.iSkl < 0) return;
    int n = a->frames, stride = 6 + 3 * g_Tables.nbones;
    if (loop) { t = fmodf(t, (float)(n - 1)); if (t < 0) t += n - 1; }
    else t = std::min(std::max(t, 0.0f), (float)(n - 1));
    int i0 = (int)t, i1 = std::min(i0 + 1, n - 1);
    float k = t - i0;
    const float* f0 = &a->data[i0 * stride]; const float* f1 = &a->data[i1 * stride];
    auto L = [&](int j) { return f0[j] + (f1[j] - f0[j]) * k; };
    std::vector<BonePose> pose;
    BonePose p{};
    p.bone = g_Tables.iSkl; p.what = 1 | 2 | 4; p.s[0] = p.s[1] = p.s[2] = 1; EulerToR(0, 0, 0, p.R);
    p.t[0] = L(0); p.t[1] = L(1); p.t[2] = L(2); pose.push_back(p);
    p = BonePose{}; p.bone = g_Tables.iWaist; p.what = 1 | 2; p.s[0] = p.s[1] = p.s[2] = 1; EulerToR(L(3), L(4), L(5), p.R); pose.push_back(p);
    for (int b = 0; b < g_Tables.nbones; b++) {
        p = BonePose{}; p.bone = g_Tables.iBones[b]; p.what = 1 | 2; p.s[0] = p.s[1] = p.s[2] = 1;
        EulerToR(L(6 + 3 * b), L(7 + 3 * b), L(8 + 3 * b), p.R); pose.push_back(p);
    }
    for (auto& r : g_Tables.roots) {                   // chain roots at their bind place (they were moved away)
        p = BonePose{}; p.bone = r.bone; p.what = 4; memcpy(p.t, r.t, sizeof p.t); pose.push_back(p);
    }
    // ball hidden: parked away and shrunk (written in full: never derived from engine columns)
    p = BonePose{}; p.bone = g_Tables.iBall; p.what = 1 | 2 | 4; p.s[0] = p.s[1] = p.s[2] = 1e-4f; EulerToR(0, 0, 0, p.R);
    memcpy(p.t, g_Tables.hidden, sizeof p.t); pose.push_back(p);
    if (g_Tables.iSpike >= 0) { p.bone = g_Tables.iSpike; pose.push_back(p); }     // spikes hidden the same way
    if (g_Tables.iFire >= 0) { p.bone = g_Tables.iFire; pose.push_back(p); }       // and the charge fire
    std::lock_guard<std::mutex> lk(g_PoseMx);
    g_Pose.swap(pose);
}

// Idle: the game animates Link; only the charge fire's bone (Ph_WaistBelt_1, root of the skirt physics, which would
// otherwise place it on the body) is held parked.
static void WriteIdlePose() {
    std::vector<BonePose> pose;
    if (g_Tables.iFire >= 0) {
        BonePose p{}; p.bone = g_Tables.iFire; p.what = 1 | 2 | 4; p.s[0] = p.s[1] = p.s[2] = 1e-4f; EulerToR(0, 0, 0, p.R);
        memcpy(p.t, g_Tables.hidden, sizeof p.t); pose.push_back(p);
    }
    std::lock_guard<std::mutex> lk(g_PoseMx);
    g_Pose.swap(pose);
}

// Player_Draw's curled-ball transform: Translate(0, 1200*scale*(1+ABC)) RotY(B28) RotX(B86[0]) RotY(-B28) [RotY(yaw)]
// RotZ(rot.z) Scale((1-ABC/2), (1+ABC), max)*1.15 RotX(rot.x). The yaw is the game's own facing.
static void WriteBallPose(const goron_roll::State& g) {
    auto rx = [](float a, float m[3][3]) { float c = cosf(a), s = sinf(a); float r[3][3] = {{1,0,0},{0,c,-s},{0,s,c}}; memcpy(m, r, sizeof r); };
    auto ry = [](float a, float m[3][3]) { float c = cosf(a), s = sinf(a); float r[3][3] = {{c,0,s},{0,1,0},{-s,0,c}}; memcpy(m, r, sizeof r); };
    auto rz = [](float a, float m[3][3]) { float c = cosf(a), s = sinf(a); float r[3][3] = {{c,-s,0},{s,c,0},{0,0,1}}; memcpy(m, r, sizeof r); };
    auto mul = [](const float a[3][3], const float b[3][3], float o[3][3]) {
        float t[3][3]; for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) t[i][j] = a[i][0]*b[0][j] + a[i][1]*b[1][j] + a[i][2]*b[2][j];
        memcpy(o, t, sizeof t); };
    const float k = (float)M_PI / 32768.0f;
    float A[3][3], B[3][3], R[3][3];
    ry(g.wobbleYaw * k, A); rx(g.wobble * k, B); mul(A, B, R);
    ry(-g.wobbleYaw * k, A); mul(R, A, R);
    rz(g.leanZ * k, A); mul(R, A, R);
    rx(g.spinAngle * k, A); mul(R, A, R);
    float sy = -R[2][0]; sy = std::max(-1.0f, std::min(1.0f, sy));       // R = Rz Ry Rx -> euler (bfres order)
    float ey = asinf(sy), ex, ez;
    if (fabsf(sy) < 0.99999f) { ex = atan2f(R[2][1], R[2][2]); ez = atan2f(R[1][0], R[0][0]); }
    else { ex = atan2f(-R[1][2], R[1][1]); ez = 0; }
    float sY = (1.0f + g.abc) * 1.15f, sXZ = (1.0f - g.abc * 0.5f) * 1.15f, sZ = std::max(1.0f + g.abc, 1.0f - g.abc * 0.5f) * 1.15f;
    (void)ex; (void)ey; (void)ez;
    if (g_Tables.iSkl < 0) return;
    std::vector<BonePose> pose;
    BonePose p{};
    p.bone = g_Tables.iSkl; p.what = 1 | 2 | 4; p.s[0] = sXZ; p.s[1] = sY; p.s[2] = sZ;
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) p.R[3 * i + j] = R[i][j];
    p.t[0] = g_Tables.sklBind[0]; p.t[1] = g_Tables.centreY * sY; p.t[2] = g_Tables.sklBind[2];
    pose.push_back(p);
    p = BonePose{}; p.bone = g_Tables.iWaist; p.what = 1 | 2; p.s[0] = p.s[1] = p.s[2] = 1; EulerToR(0, 0, 0, p.R); pose.push_back(p);
    for (auto& r : g_Tables.roots) {                   // body chains moved far away (no scale: no SSC side effects)
        p = BonePose{}; p.bone = r.bone; p.what = 4; p.t[0] = 0; p.t[1] = -1000.0f; p.t[2] = 0; pose.push_back(p);
    }
    p = BonePose{}; p.bone = g_Tables.iBall; p.what = 1 | 2 | 4; p.s[0] = p.s[1] = p.s[2] = 1; EulerToR(0, 0, 0, p.R);
    p.t[0] = p.t[1] = p.t[2] = 0; pose.push_back(p);
    if (g_Tables.iSpike >= 0) {
        // object_link_goron_DL_00C540 drawn with the ball's matrix while unk_B86[1] != 0; below 3 it is scaled by
        // func_80124618(D_8085D540 = {0: 0%, 2: 100%}, unk_B86[1]) -> 0.5, 1.0
        p = BonePose{}; p.bone = g_Tables.iSpike; p.what = 1 | 2 | 4; EulerToR(0, 0, 0, p.R);
        if (g.spikes != 0) {
            float k = g.spikes < 3 ? g.spikes * 0.5f : 1.0f;
            p.s[0] = p.s[1] = p.s[2] = k; p.t[0] = p.t[1] = p.t[2] = 0;
        } else {
            p.s[0] = p.s[1] = p.s[2] = 1e-4f; memcpy(p.t, g_Tables.hidden, sizeof p.t);
        }
        pose.push_back(p);
    }
    if (g_Tables.iFire >= 0) {
        // Player_Draw (curled): while charging (av1 >= 5) and until the spikes are out (unk_B86[1] < 3), MM draws
        // DL_0127B0 / DL_0134D0 translucent over the ball with Matrix_Scale(1, k, k): k = (av1 - 4) * 0.02 while
        // charging, 0.65 once the spikes appear (its alpha, 200 * k then 255 - 85 * B86[1], is not controllable
        // here). The materials' texture scroll (grt_0*_model.cmab, one wrap per 30 frames) wraps around the
        // shells' axis, so it is drawn as a turn about X.
        static auto t0 = std::chrono::steady_clock::now();
        float secs = std::chrono::duration<float>(std::chrono::steady_clock::now() - t0).count();
        p = BonePose{}; p.bone = g_Tables.iFire; p.what = 1 | 2 | 4;
        static const bool force = getenv("HOOKSHOT_FIRE_FORCE") != nullptr;   // test: always shown, full size
        if (force || (g.phase >= 5 && g.spikes < 3)) {
            float k = force ? 1.0f : (g.spikes != 0 ? 0.65f : (g.phase - 4) * 0.02f);
            p.s[0] = 1.0f; p.s[1] = p.s[2] = std::max(k, 1e-3f);
            EulerToR(fmodf(secs, 1.0f) * 2.0f * (float)M_PI, 0, 0, p.R);
            p.t[0] = p.t[1] = p.t[2] = 0;
        } else {
            p.s[0] = p.s[1] = p.s[2] = 1e-4f; EulerToR(0, 0, 0, p.R); memcpy(p.t, g_Tables.hidden, sizeof p.t);
        }
        pose.push_back(p);
    }
    std::lock_guard<std::mutex> lk(g_PoseMx);
    g_Pose.swap(pose);
}

enum class AnimMode { Idle, Curl, Ball, Uncurl };
static std::atomic<int> g_CamMode{0};
static std::atomic<int> g_QuakeTick{0}, g_QuakeStart{-1000};   // Player_RequestQuake(speed, y, duration), MM frames
static std::atomic<int> g_QuakeSpeed{27767}, g_QuakeY{7}, g_QuakeDur{20};                    // 0 normal, 1 GORONDASH, 2 GORONJUMP (set by GoronFrame)
static uint8_t* g_A64Dirty = nullptr;

static goron_roll::State g_GoronPrev;     // state before the latest 20 Hz tick (rendering interpolates to g_Goron)

// MM runs at 20 fps; like Zelda64Recomp's frame interpolation, the drawn pose is interpolated between the last two
// game ticks (alpha = how far into the next tick this frame is), so the 60 fps render is smooth.
static goron_roll::State InterpState(const goron_roll::State& a, const goron_roll::State& b, float t) {
    goron_roll::State s = b;
    auto ang = [t](int16_t x, int16_t y) { return (int16_t)(x + (int16_t)(y - x) * t); };
    s.spinAngle = ang(a.spinAngle, b.spinAngle);
    s.leanZ = ang(a.leanZ, b.leanZ);
    s.wobble = ang(a.wobble, b.wobble);
    s.wobbleYaw = ang(a.wobbleYaw, b.wobbleYaw);
    s.abc = a.abc + (b.abc - a.abc) * t;
    s.curlFrame = a.curlFrame + (b.curlFrame - a.curlFrame) * t;
    return s;
}
static AnimMode g_AnimMode = AnimMode::Idle;
static float g_WaitFrame = 0, g_UncurlFrame = 0;
static int g_MoveCurlTick = 0;
static std::chrono::steady_clock::time_point g_FrameLast;
static double g_FrameAcc = 0;

// ---- magic meter (MM units) for the spiked roll: the game's meter, found by memory diff (RESEARCH_NOTES §42).
// HOOKSHOT_MAGIC_ADDR=<hex guest address>[:u8|u16|s32|f32][*scale]  overrides; HOOKSHOT_MAGIC_FAKE=<n> = a private
// meter for tests.
static uint64_t g_MagicAddr = 0;
static char g_MagicType = 'b';           // b u8, h u16, i s32, f f32
static float g_MagicScale = 1.0f;        // game units per MM unit
static int g_MagicFake = -1;
// The game's meter is GameData Int "PlayerStatus.MP" (murmur3 0x76b18200; MPMax 0x12d63a97, Life 0x31ab5580), kept
// in a runtime table of {u32 hash, s32 value} pairs. One magic level = 240 here, 48 in MM: scale 5.
constexpr uint32_t kHashMP = 0x76b18200, kHashMPMax = 0x12d63a97, kHashLife = 0x31ab5580;
static std::atomic<uint64_t> g_MagicFound{0};
static std::atomic<int> g_MagicScan{0};          // 0 not started, 1 running, 2 done
static void MagicScanThread() {
    const uint64_t base = 0x100000000000ULL;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    std::vector<std::pair<uint64_t, int>> mp, mpmax, life;   // (guest address of the value, value)
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
                for (int i = 0; i + 1 < 1024; i += 2) {                        // 8-byte aligned pairs
                    uint32_t h = p[i]; int v = (int)p[i + 1];
                    if (h != kHashMP && h != kHashMPMax && h != kHashLife) continue;
                    uint64_t g = (uint64_t)(p + i + 1) - base;
                    if (h == kHashMP && v >= 0 && v <= 2000) mp.push_back({g, v});
                    else if (h == kHashMPMax && v >= 0 && v <= 2000) mpmax.push_back({g, v});
                    else if (h == kHashLife && v >= 0 && v <= 80) life.push_back({g, v});
                }
            }
        }
    }
    if (m) fclose(m);
    auto near = [](uint64_t a, const std::vector<std::pair<uint64_t, int>>& v) {
        for (auto& e : v) if ((a > e.first ? a - e.first : e.first - a) < 0x10000) return e.second;
        return -1;
    };
    for (auto& e : mp) {
        int mx = near(e.first, mpmax);
        if (mx >= e.second && near(e.first, life) >= 0) {
            g_MagicFound = e.first;
            OverlayLog("[magic] PlayerStatus.MP at 0x%llx = %d (MPMax %d)\n", (unsigned long long)e.first, e.second, mx);
            break;
        }
    }
    if (!g_MagicFound) {
        OverlayLog("[magic] PlayerStatus.MP not found (%zu/%zu/%zu candidates); retrying\n", mp.size(), mpmax.size(), life.size());
        for (auto& e : mp) OverlayLog("[magic]   MP %llx = %d\n", (unsigned long long)e.first, e.second);
        for (auto& e : mpmax) OverlayLog("[magic]   MPMax %llx = %d\n", (unsigned long long)e.first, e.second);
        for (auto& e : life) OverlayLog("[magic]   Life %llx = %d\n", (unsigned long long)e.first, e.second);
        std::this_thread::sleep_for(std::chrono::seconds(3));     // (e.g. the save was not loaded yet) try again
        g_MagicScan = 0;
        return;
    }
    g_MagicScan = 2;
}
static int MagicGet() {
    if (g_MagicFake >= 0) return g_MagicFake;
    if (!g_MagicAddr) {
        uint64_t f = g_MagicFound.load();
        if (f && GRead<uint32_t>(f - 4) == kHashMP) { g_MagicAddr = f; g_MagicType = 'i'; g_MagicScale = 5.0f; }
        else {
            if (f) g_MagicFound = 0;
            int st = g_MagicScan.load();
            if (st == 0 || (st == 2 && f)) { g_MagicScan = 1; std::thread(MagicScanThread).detach(); }   // (re)locate
            return 0;
        }
    }
    if (g_MagicType == 'i' && g_MagicScale == 5.0f && GRead<uint32_t>(g_MagicAddr - 4) != kHashMP) {  // table moved
        g_MagicAddr = 0; g_MagicFound = 0; g_MagicScan = 0;
        return 0;
    }
    float v = 0;
    switch (g_MagicType) {
    case 'h': v = GRead<uint16_t>(g_MagicAddr); break;
    case 'i': v = (float)GRead<int32_t>(g_MagicAddr); break;
    case 'f': v = GRead<float>(g_MagicAddr); break;
    default: v = GRead<uint8_t>(g_MagicAddr); break;
    }
    return (int)lrintf(v / g_MagicScale);
}
static void MagicSet(int m) {
    if (g_MagicFake >= 0) { g_MagicFake = m; return; }
    if (!g_MagicAddr) return;
    float v = m * g_MagicScale;
    switch (g_MagicType) {
    case 'h': GWrite<uint16_t>(g_MagicAddr, (uint16_t)lrintf(v)); break;
    case 'i': GWrite<int32_t>(g_MagicAddr, (int32_t)lrintf(v)); break;
    case 'f': GWrite<float>(g_MagicAddr, v); break;
    default: GWrite<uint8_t>(g_MagicAddr, (uint8_t)lrintf(v)); break;
    }
}
static goron_roll::Magic g_Magic;
static void MagicInit() {
    g_Magic.get = MagicGet; g_Magic.set = MagicSet;
    if (const char* f = getenv("HOOKSHOT_MAGIC_FAKE")) g_MagicFake = atoi(f);
    if (const char* a = getenv("HOOKSHOT_MAGIC_ADDR")) {
        char* e = nullptr;
        g_MagicAddr = strtoull(a, &e, 16);
        if (e && *e == ':') { ++e; g_MagicType = !strncmp(e, "u16", 3) ? 'h' : !strncmp(e, "s32", 3) ? 'i' : !strncmp(e, "f32", 3) ? 'f' : 'b';
                              while (*e && *e != '*') ++e; }
        if (e && *e == '*') g_MagicScale = strtof(e + 1, nullptr);
    }
    OverlayLog("[magic] meter: %s 0x%llx type %c scale %.3f\n", g_MagicFake >= 0 ? "fake" : "game",
               (unsigned long long)g_MagicAddr, g_MagicType, g_MagicScale);
}
static bool g_PrevA = false;

// Is this f_80156ae0 actor Link? (Hookshot's hos_oot_lift_player / hos_oot_is_player checks)
static bool IsLinkActor(uint64_t actor) {
    static uint64_t known = 0;
    if (actor == known) return true;
    uint64_t comps = GRead<uint64_t>(actor + 0x270);
    uint64_t mv = comps ? GRead<uint64_t>(comps + 800) : 0;
    if (!mv || mv < 0x1d50 || GRead<uint64_t>(mv + 24) != actor) return false;
    uint64_t obj = mv - 0x1d50;
    if (GRead<uint64_t>(obj) != 0x84b6c460ULL) return false;
    uint64_t res = GRead<uint64_t>(obj + 16);
    if (!res || GRead<uint32_t>(res + 88) != 0x79616c50 || GRead<uint16_t>(res + 92) != 0x7265 || GRead<uint8_t>(res + 94) != 0) return false;
    known = actor;
    g_RollActorSeen = actor;
    OverlayLog("[anim] Link actor %llx\n", (unsigned long long)actor);
    return true;
}

// Roll node started (moving entry: func_80836B3C)
static void GoronOnRollUpdate(uint64_t actor) {
    if (!g_GoronEnabled) return;
    std::lock_guard<std::mutex> lk(g_GoronMx);
    if (g_Goron.active || g_AnimMode == AnimMode::Uncurl) return;
    uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
    float speed = p28 ? GRead<float>(p28 + 0x1d8) : 0;          // m/s
    int16_t yaw = goron_roll::Atan2S_XY(GRead<float>(actor + 0x348), GRead<float>(actor + 0x330));
    goron_roll::Enter(g_Goron, speed / goron_roll::kSpeedToMps, yaw);
    g_GoronPrev = g_Goron;
    g_AnimMode = AnimMode::Curl;
    g_MoveCurlTick = 0;
    goron_sfx::Play(goron_sfx::TO_BALL);                       // func_80836AD8
    OverlayLog("[goron] roll start (moving): speed %.2f m/s (%.2f u/f), yaw %04x\n", speed, g_Goron.linearVelocity, (unsigned)(uint16_t)yaw);
}

// probe (HOOKSHOT_BAKE_PROBE=1): runs of >= 16 floats == 1000.0 (the driven clips' hidden-ball curve) in guest memory
static void BakeProbe() {
    const uint64_t base = 0x100000000000ULL;
    const uint32_t k1000 = 0x447a0000;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    int found = 0;
    while (m && fgets(line, sizeof line, m) && found < 30) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r') continue;
        lo = std::max<unsigned long>(lo, base); hi = std::min<unsigned long>(hi, base + (1ULL << 39));
        for (unsigned long w = lo; w < hi && found < 30; w += (1UL << 30)) {
            unsigned long we = std::min<unsigned long>(hi, w + (1UL << 30));
            vec.resize((we - w) >> 12);
            if (mincore((void*)w, we - w, vec.data()) != 0) continue;
            for (size_t pg = 0; pg < vec.size() && found < 30; pg++) {
                if (!(vec[pg] & 1)) continue;
                const uint32_t* p = (const uint32_t*)(w + (pg << 12));
                const uint16_t* h = (const uint16_t*)p;
                for (int i = 1; i < 2048 - 16; i++) {                  // half-float 1000.0 runs (stride 2 or 4 or 8 bytes)
                    if (h[i] != 0x63D0 || h[i - 1] == 0x63D0) continue;
                    for (int st : {1, 2, 4}) {
                        int run = 0;
                        while (i + st * run < 2048 && h[i + st * run] == 0x63D0) run++;
                        if (run >= 16) {
                            OverlayLog("[bake] half 1000.0 run %d stride %d B at %llx\n", run, 2 * st,
                                       (unsigned long long)((uint64_t)(h + i) - base));
                            found++; break;
                        }
                    }
                }
                for (int i = 1; i < 1024 - 16; i++) {
                    if (p[i] != k1000 || p[i - 1] == k1000) continue;
                    int run = 0, run2 = 0;
                    while (i + run < 1024 && p[i + run] == k1000) run++;
                    while (i + 2 * run2 < 1024 && p[i + 2 * run2] == k1000) run2++;
                    if (run >= 16 || run2 >= 16) {
                        const float* f = (const float*)(p + i);
                        OverlayLog("[bake] run %d (stride8 %d) at %llx; before: %.3f %.3f %.3f %.3f\n", run, run2,
                                   (unsigned long long)((uint64_t)(p + i) - base), f[-4], f[-3], f[-2], f[-1]);
                        found++;
                        i += run;
                    }
                }
            }
        }
    }
    if (m) fclose(m);
    OverlayLog("[bake] probe done (%d)\n", found);
}

// probe (HOOKSHOT_SKIN_PROBE=1): runs of >= 250 near-rigid 3x4 matrices (skinning palettes), any layout
static void SkinProbe() {
    const uint64_t base = 0x100000000000ULL;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    int found = 0;
    auto rigid34 = [](const float* f) {          // row-major 3x4: rows f[0..2], f[4..6], f[8..10]; translation f[3,7,11]
        for (int r = 0; r < 3; r++) { float n = f[4*r]*f[4*r] + f[4*r+1]*f[4*r+1] + f[4*r+2]*f[4*r+2]; if (n < 0.5f || n > 4.0f) return false; }
        return fabsf(f[3]) < 3 && fabsf(f[7]) < 3 && fabsf(f[11]) < 3;
    };
    auto rigid43 = [](const float* f) {          // column-major 4x3: columns f[0..2], f[3..5], f[6..8], translation f[9..11]
        for (int c = 0; c < 3; c++) { float n = f[3*c]*f[3*c] + f[3*c+1]*f[3*c+1] + f[3*c+2]*f[3*c+2]; if (n < 0.5f || n > 4.0f) return false; }
        return fabsf(f[9]) < 3 && fabsf(f[10]) < 3 && fabsf(f[11]) < 3;
    };
    while (m && fgets(line, sizeof line, m) && found < 20) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r') continue;
        lo = std::max<unsigned long>(lo, base); hi = std::min<unsigned long>(hi, base + (1ULL << 39));
        for (unsigned long w = lo; w < hi && found < 20; w += (1UL << 30)) {
            unsigned long we = std::min<unsigned long>(hi, w + (1UL << 30));
            vec.resize((we - w) >> 12);
            if (mincore((void*)w, we - w, vec.data()) != 0) continue;
            for (size_t pg = 0; pg + 4 < vec.size() && found < 20; pg++) {
                if (!(vec[pg] & 1)) continue;
                for (int lay = 0; lay < 2; lay++) {
                    for (int off = 0; off < 4096; off += 16) {
                        uint64_t a = w + (pg << 12) + off;
                        auto ok = [&](uint64_t x) {
                            if (x + 48 > we || !(vec[(x - w) >> 12] & 1) || !(vec[(x + 47 - w) >> 12] & 1)) return false;
                            return lay == 0 ? rigid34((const float*)x) : rigid43((const float*)x);
                        };
                        if (!ok(a) || (a >= w + 48 && ok(a - 48))) continue;
                        int run = 0;
                        while (run < 600 && ok(a + 48ULL * run)) run++;
                        if (run >= 250) {
                            OverlayLog("[skin] %s run %d at %llx\n", lay ? "4x3col" : "3x4row", run, (unsigned long long)(a - base));
                            found++;
                        }
                    }
                }
            }
        }
    }
    if (m) fclose(m);
    OverlayLog("[skin] probe done (%d)\n", found);
}

static bool GoronAirVelocity(float* mps) {
    if (!g_Goron.active || !g_Goron.airborne) return false;
    *mps = g_Goron.velY * goron_roll::kSpeedToMps;
    return true;
}

// Wall contact = MM's BGCHECKFLAG_WALL + actor.wallYaw: the character controller's current contact normal (p28 +0xe4:
// (0, 1, 0) on open ground, the touched surface's normal while touching it; +0x150 keeps the LAST wall and goes stale).
// Walls are the steep contacts (|n.y| < 0.6), only while travelling into them; wallYaw = Math_Atan2S_XY(n.z, n.x).
// Sampled every frame and held until the next 20 Hz MM tick (a graze can last only a few frames).
static bool g_WallLatched = false;
static int16_t g_WallLatchYaw = 0;
static void WallSample(uint64_t actor) {
    if (!g_Goron.active || g_Goron.airborne || g_Goron.phase < 4) return;
    uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
    if (!p28) return;
    float nx = GRead<float>(p28 + 0xe4), ny = GRead<float>(p28 + 0xe8), nz = GRead<float>(p28 + 0xec);
    float nl = sqrtf(nx * nx + ny * ny + nz * nz);
    if (!(nl > 0.5f) || fabsf(ny / nl) >= 0.6f) return;
    float into = (goron_roll::SinS(g_Goron.currentYaw) * nx + goron_roll::CosS(g_Goron.currentYaw) * nz) *
                 (g_Goron.linearVelocity >= 0.0f ? 1.0f : -1.0f);
    if (into >= 0.0f) return;
    g_WallLatched = true;
    g_WallLatchYaw = goron_roll::Atan2S_XY(nz / nl, nx / nl);
    static const bool dbg = getenv("HOOKSHOT_WALL_DEBUG") != nullptr;
    if (dbg) OverlayLog("[goron] wall normal (%.2f %.2f %.2f) yaw %04x speed %.1f\n", nx, ny, nz,
                        (unsigned)(uint16_t)g_WallLatchYaw, g_Goron.rollSpeed);
}

// Once per frame for Link (from f_80156ae0): MM's state machine at 20 Hz and the animation drivers.
static void GoronFrame(uint64_t actor) {
    if (!g_GoronEnabled) return;
    std::lock_guard<std::mutex> lk(g_GoronMx);
    static bool init = false;
    if (!init) { init = true; LoadDriverTables(); MagicInit(); goron_sfx::Init(); g_FrameLast = std::chrono::steady_clock::now(); }
    if (!g_Tables.ok) return;
    static int watchFrames = 0;
    if (getenv("HOOKSHOT_WATCH_DRIVER") && ++watchFrames == 300) {      // data breakpoints on driver keys (HOS_WATCH)
        if (const char* w = getenv("HOS_WATCH")) {
            if (FILE* f = fopen(w, "w")) {
                for (size_t i = 0; i < g_DriverBlocks.size() && i < 3; i++)
                    fprintf(f, "%llx ", (unsigned long long)(g_DriverBlocks[i] + 16 * kSlotSklTrans + 16));
                fprintf(f, "\n"); fclose(f);
                OverlayLog("[driver] watch armed on Skl_Root trans-y keys of %zu blocks\n", g_DriverBlocks.size());
            }
        }
    }
    static int pokeFrames = 0;
    if (const char* pw = getenv("HOOKSHOT_WATCH_ADDRS")) {             // arm HOS_WATCH on given guest addresses
        if (++pokeFrames == 300) if (const char* w = getenv("HOS_WATCH")) if (FILE* f = fopen(w, "w")) { fprintf(f, "%s\n", pw); fclose(f); }
    }
    static int hidFrames = 0;
    if (getenv("HOOKSHOT_HIDDEN_PROBE") && ++hidFrames == 600) {        // where does the engine keep bone local poses?
        float h[3] = {g_Tables.hidden[0], g_Tables.hidden[1], g_Tables.hidden[2]};
        std::vector<uint64_t> hits;
        SearchGuestFloats3(h, hits, 0x1000000000ULL, 0x0FFFFFFFFFULL);
        OverlayLog("[pose] %zu copies of the ball's hidden translation\n", hits.size());
        for (size_t i = 0; i < hits.size() && i < 24; i++) OverlayLog("[pose]   %llx\n", (unsigned long long)hits[i]);
        if (const char* dp = getenv("HOOKSHOT_POSE_DUMP")) if (FILE* f = fopen(dp, "wb")) {   // region around each copy
            for (size_t i = 0; i < hits.size() && i < 9; i++) {
                uint64_t h = hits[i];
                fwrite(&h, 8, 1, f);
                fwrite(GPtr(h - 0x10000), 1, 0x14000, f);
            }
            fclose(f);
        }
        if (const char* w = getenv("HOS_WATCH")) if (FILE* f = fopen(w, "w")) {      // watch the first copies (+ x, y, z)
            for (size_t i = 0; i < hits.size() && i < 4; i++) fprintf(f, "%llx ", (unsigned long long)hits[i]);
            fprintf(f, "\n"); fclose(f);
        }
    }
    static int skinFrames = 0;
    if (getenv("HOOKSHOT_SKIN_PROBE") && ++skinFrames == 600) SkinProbe();
    static int bakeFrames = 0;
    if (getenv("HOOKSHOT_BAKE_PROBE") && ++bakeFrames == 600) BakeProbe();
    auto now = std::chrono::steady_clock::now();
    static const bool posLog = getenv("HOOKSHOT_POS_LOG") != nullptr;   // calibration: position + CCT speed, ~2/s
    static int posN = 0;
    if (posLog && (posN++ % 30) == 0) {
        uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
        OverlayLog("[pos] %.3f %.3f %.3f t %.3f cct %.3f\n", GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320),
                   GRead<float>(actor + 0x324), std::chrono::duration<double>(now.time_since_epoch()).count(),
                   p28 ? GRead<float>(p28 + 0x1d8) : -1.0f);
    }
    g_FrameAcc += std::chrono::duration<double>(now - g_FrameLast).count();
    g_FrameLast = now;
    if (g_FrameAcc > 0.25) g_FrameAcc = 0.05;
    bool aHeld = g_RealAHeld.load();
    bool aPressed = aHeld && !g_PrevA;
    g_PrevA = aHeld;
    // func_80839F98 (stationary): A pressed, no stick, not moving -> curl
    if (aPressed && !g_Goron.active && g_AnimMode == AnimMode::Idle) {
        uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
        float speed = p28 ? GRead<float>(p28 + 0x1d8) : 0;
        if (GoronInput().stickMag == 0 && fabsf(speed) < 0.05f) {
            goron_roll::EnterStanding(g_Goron, goron_roll::Atan2S_XY(GRead<float>(actor + 0x348), GRead<float>(actor + 0x330)));
            g_GoronPrev = g_Goron;
            g_AnimMode = AnimMode::Curl;
            goron_sfx::Play(goron_sfx::TO_BALL);               // func_80836AD8
            OverlayLog("[goron] curl (standing)\n");
        }
    }
    WallSample(actor);
    {   // probe (HOOKSHOT_JUMP_PROBE=1): the CCT motion block (p28 +0x100..+0x260) for 40 frames after B (the game's jump)
        static const bool jp = getenv("HOOKSHOT_JUMP_PROBE") != nullptr;
        static int left = 0; static bool prevB = false;
        if (jp) {
            bool b = g_RealBHeld.load();
            if (b && !prevB) {
                left = 40;
                g_CctvUntil = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count() + 400;
            }
            prevB = b;
            uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
            if (left > 0 && p28) {
                left--;
                static char line[32768];
                for (int part = 0; part < 3; part++) {
                    uint64_t base = part == 0 ? p28 : (part == 1 ? cct : GRead<uint64_t>(cct + 0x20));
                    int len = part == 0 ? 0x600 : 0x400;
                    if (base < 0x1000000000ULL || base >= 0x1100000000ULL) continue;
                    int k = snprintf(line, sizeof line, "[jump%d] y %.3f |", part, GRead<float>(actor + 0x320));
                    for (int o = 0; o < len; o += 4) k += snprintf(line + k, sizeof line - k, " %x:%.4g", o, GRead<float>(base + o));
                    OverlayLog("%s\n", line);
                }
            }
        }
    }
    goron_roll::Input in = GoronInput();
    in.aHeld = aHeld;
    static bool prevY = false, pendingAttack = false;
    bool yHeld = g_RealYHeld.load();
    if (yHeld && !prevY && g_Goron.active) pendingAttack = true;   // press edge, consumed by the next MM tick
    prevY = yHeld;
    g_RollOwnsY = g_Goron.active;
    int ticks = 0;
    while (g_FrameAcc >= 1.0 / goron_roll::kMmFps && ticks < 4) {
        g_FrameAcc -= 1.0 / goron_roll::kMmFps;
        ticks++;
        goron_sfx::GameTick();
        g_QuakeTick++;
        g_Magic.Update();                                      // Magic_Update (once per game frame)
        switch (g_AnimMode) {
        case AnimMode::Idle: g_WaitFrame += 1.0f; break;
        case AnimMode::Curl:
        case AnimMode::Ball:
            g_GoronPrev = g_Goron;                             // (frame interpolation: the state before this tick)
            if (g_Goron.active && g_Goron.phase != 0 && goron_roll::WantsUncurl(g_Goron, in)) {   // func_80857950
                g_Goron.active = false;
                g_AnimMode = AnimMode::Uncurl;
                g_UncurlFrame = 7.0f;
                goron_sfx::Play(goron_sfx::BALL_TO_GORON);     // func_80857950
                if (g_Goron.spikeFlag) { g_Goron.spikeFlag = false; g_Magic.Reset(); }   // (leaving Action_96: Magic_Reset)
                OverlayLog("[goron] uncurl\n");
                break;
            }
            {
                int16_t sp0 = g_Goron.spikes;
                bool wasAir = g_Goron.airborne;
                in.attackPressed = pendingAttack; pendingAttack = false;
                // MM's bgcheck while airborne: the CCT's step after the forced height is the collision. Pushed up while
                // falling = floor (land there: a ledge too); pushed down while rising = ceiling (velocity.y = 0).
                static const bool airDbgT = getenv("HOOKSHOT_AIR_DEBUG") != nullptr;
                if (airDbgT && g_Goron.airborne)
                    OverlayLog("[air] tick y %.3f hist %.3f %.3f %.3f velY %.2f corr %.3f\n", GRead<float>(actor + 0x320),
                               g_AirTargetHist[0], g_AirTargetHist[1], g_AirTargetHist[2], g_Goron.velY, g_AirCorr);
                if (g_Goron.airborne && g_AirTargetValid && g_AirTargetCount >= 3) {
                    // (the position read here reflects the target handed over two frames ago)
                    float y = GRead<float>(actor + 0x320), d = y - g_AirTargetHist[1];
                    const float unitM = goron_roll::kSpeedToMps / (goron_roll::kMmFps * goron_roll::kActorMovementScale);
                    float tRise = g_AirTickPrevValid ? g_AirTargetHist[1] - g_AirTickPrevTarget : 0.0f;
                    float aRise = g_AirTickPrevValid ? y - g_AirTickPrevY : 0.0f;
                    bool stalledFall = tRise < -0.1f && aRise > 0.25f * tRise;   // target dropping, ball isn't
                    if (g_Goron.velY <= 0.0f && (d > 0.15f || stalledFall)) {
                        g_Goron.posY = 0; g_Goron.velY = 0; g_Goron.airborne = false; g_Goron.landed = true;
                        g_GroundY = y; g_AirTargetValid = false;
                        OverlayLog("[goron] lands on the floor at y %.2f (%.2f above the take-off)\n", y, y - g_AirLastTargetY + g_AirY.load());
                    } else if (g_Goron.velY > 0.0f && tRise > 0.1f && aRise < 0.25f * tRise) {
                        // (the CCT's step settles each forced height a little low, rising or falling, so the gap to the
                        // target isn't a ceiling; a ceiling is the ball not rising while the target does)
                        g_Goron.velY = 0;                                    // ceiling
                        g_Goron.posY = (y - g_GroundY) / unitM;
                        OverlayLog("[goron] ceiling at y %.2f\n", y);
                    }
                    else if (g_Goron.airborne) {
                        float c = g_AirCorr - 0.6f * d;                  // free flight: trim the step's sag
                        g_AirCorr = c < 0.0f ? 0.0f : c > 2.0f ? 2.0f : c;
                    }
                    g_AirTickPrevY = y; g_AirTickPrevTarget = g_AirTargetHist[1]; g_AirTickPrevValid = true;
                }
                if (g_WallLatched) {                       // wall contact seen since the last MM tick (WallSample)
                    in.wallTouched = true; in.wallYaw = g_WallLatchYaw; g_WallLatched = false;
                }
                goron_roll::Tick(g_Goron, in, g_Magic);
                static const bool stuckDbg = getenv("HOOKSHOT_STUCK_DEBUG") != nullptr;
                if (stuckDbg)
                    OverlayLog("[stuck] pos %.2f %.2f %.2f air %d phase %d lin %.2f B08 %.2f yaw %04x home %04x wall %d stick %.0f@%04x\n",
                               GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320), GRead<float>(actor + 0x324),
                               g_Goron.airborne, g_Goron.phase, g_Goron.linearVelocity, g_Goron.rollSpeed,
                               (unsigned)(uint16_t)g_Goron.currentYaw, (unsigned)(uint16_t)g_Goron.homeYaw, in.wallTouched,
                               in.stickMag, (unsigned)(uint16_t)in.stickYaw);
                if (!g_Goron.airborne) { g_AirTargetValid = false; g_AirTargetCount = 0; g_AirTickPrevValid = false; }
                if (g_Goron.airborne && !wasAir) {             // take-off: remember the floor point
                    g_GroundY = GRead<float>(actor + 0x320); g_AirX = GRead<float>(actor + 0x31c); g_AirZ = GRead<float>(actor + 0x324);
                    OverlayLog("[goron] ground pound jump at y %.2f\n", g_GroundY);
                }
                if (!g_Goron.airborne && wasAir) OverlayLog("[goron] ground pound lands (phase %d)\n", g_Goron.phase);
                {                                          // Player_Action_96's sounds
                    using namespace goron_sfx;
                    const int b = g_Goron.sfx;
                    // GORON_ROLL / GORON_CHG_ROLL: MM re-triggers it every revolution (shape.rot.x crossing 0) before its
                    // held looping rumble ends, so while the ball spins fast it sounds as one continuous rumble: held here
                    // (synced to unk_B08). Below ~1 revolution / 0.6 s MM's triggers no longer overlap: one short hit each.
                    const int rollId = g_Goron.spikes != 0 ? CHG_ROLL : ROLL;
                    if (abs(g_Goron.spin) >= 0x1555) {
                        float f, v; Synced(g_Goron.rollSpeed, &f, &v);
                        PlayFlagged(rollId, f, v);
                    } else if (b & goron_roll::SFX_ROLL) {
                        float f, v; Synced(g_Goron.rollSfxParam, &f, &v);
                        PlayShort(rollId, 0.4f, f, v);
                    }
                    if (b & goron_roll::SFX_WOBBLE) PlaySynced(ROLL, g_Goron.wobbleSfxParam);
                    if (b & goron_roll::SFX_CHARGE) PlayFlagged(BALL_CHARGE);
                    if (b & goron_roll::SFX_CHARGE_DASH) Play(BALL_CHARGE_DASH);
                    if (b & goron_roll::SFX_CHARGE_FAILED) Play(BALL_CHARGE_FAILED);
                    if (b & goron_roll::SFX_SLIP) PlayFlagged(SLIP);
                    if (b & goron_roll::SFX_DUST) PlayFlagged(ROLL_DUST);
                    if (b & goron_roll::SFX_BALLJUMP) Play(BALLJUMP);
                    if (b & goron_roll::SFX_POUND) {                     // Player_RequestQuakeAndRumble
                        Play(GORON_PUNCH); g_QuakeSpeed = 27767; g_QuakeY = 7; g_QuakeDur = 20; g_QuakeStart = g_QuakeTick + 1;
                    }
                    if (b & goron_roll::SFX_BONK) Play(BODY_HIT);
                    if (b & goron_roll::SFX_REFLECT) Play(ROLLING_REFLECTION);
                    if (g_Goron.quake == 1) { g_QuakeSpeed = 33267; g_QuakeY = 3; g_QuakeDur = 12; g_QuakeStart = g_QuakeTick + 1; }
                    static int lastAtk = -1;                             // Player_SetCylinderForAttack: hook point
                    if (g_Goron.attack != lastAtk) {
                        lastAtk = g_Goron.attack;
                        OverlayLog("[attack] %s (radius %.0f MM units, %.2f m)\n",
                                   lastAtk == goron_roll::ATK_GORON_POUND ? "DMG_GORON_POUND" :
                                   lastAtk == goron_roll::ATK_GORON_SPIKES ? "DMG_GORON_SPIKES" : "DMG_NORMAL_ROLL",
                                   g_Goron.attackRadius, g_Goron.attackRadius * 1.68f / 70.0f);
                    }
                }
                if ((g_Goron.spikes != 0) != (sp0 != 0) || (g_Goron.sfx & goron_roll::SFX_CHARGE_DASH))
                    OverlayLog("[goron] spikes %d -> %d (phase %d, v %.2f, magic %d)\n", sp0, g_Goron.spikes, g_Goron.phase,
                               g_Goron.linearVelocity, g_Magic.value());
            }
            g_MoveCurlTick++;
            if (g_Goron.phase != 0) g_AnimMode = AnimMode::Ball;
            break;
        case AnimMode::Uncurl:
            g_UncurlFrame -= 2.0f / 3.0f;                      // pg_maru_change 7 -> 0 at -PLAYER_ANIM_ADJUSTED_SPEED
            if (g_UncurlFrame <= 0.0f) { g_AnimMode = AnimMode::Idle; g_WaitFrame = 0; }
            break;
        }
    }
    const DriverTables::Anim* wait = g_Tables.find("pg_wait");
    const DriverTables::Anim* curl = g_Tables.find("pg_maru_change");
    static AnimMode lastMode = AnimMode::Idle;
    if (g_AnimMode != lastMode) {
        if (getenv("HOOKSHOT_POSE_DEBUG")) { g_PoseDebugFrames = 4; OverlayLog("[posedbg] mode %d -> %d\n", (int)lastMode, (int)g_AnimMode); }
        lastMode = g_AnimMode;
    }
    g_CamMode = (g_AnimMode == AnimMode::Ball && g_Goron.spikeFlag) ? 1 : 0;   // CAM_MODE_GORONDASH (z_player.c)
    {
        bool air = g_Goron.active && (g_Goron.airborne || g_GoronPrev.airborne);
        float a = (float)std::min(1.0, std::max(0.0, g_FrameAcc * goron_roll::kMmFps));
        float y = g_GoronPrev.posY + (g_Goron.posY - g_GoronPrev.posY) * a;
        g_AirY = y * goron_roll::kSpeedToMps / (goron_roll::kMmFps * goron_roll::kActorMovementScale);   // units -> m
        g_AirActive = air;
    }
    if (const char* cf = getenv("HOOKSHOT_CAM_FORCE")) if (access(cf, F_OK) == 0) g_CamMode = 1;   // test: file present
    float alpha = (float)std::min(1.0, std::max(0.0, g_FrameAcc * goron_roll::kMmFps));
    switch (g_AnimMode) {
    case AnimMode::Idle: WriteIdlePose(); (void)wait; break;      // the game's own (baked pg_wait) idle
    case AnimMode::Curl:
        // standing: pg_maru_change at its MM frame; moving: the landing curl, 4 ticks (approximated with pg_maru_change)
        WriteBodyPose(curl, g_Goron.standingEntry ? std::min(7.0f, g_Goron.curlFrame + (2.0f / 3.0f) * alpha)
                                                  : (g_MoveCurlTick + alpha) * 7.0f / 4.0f, false);
        break;
    case AnimMode::Ball: WriteBallPose(InterpState(g_GoronPrev, g_Goron, alpha)); break;
    case AnimMode::Uncurl: WriteBodyPose(curl, std::max(0.0f, g_UncurlFrame - (2.0f / 3.0f) * alpha), false); break;
    }
    static int n = 0;
    if (ticks && g_AnimMode == AnimMode::Ball && (n++ % 20) == 0)
        OverlayLog("[goron] v %.2f u/f spin %d abc %.2f lean %d stick %.0f phase %d spikes %d magic %d\n", g_Goron.linearVelocity,
                   g_Goron.spin, g_Goron.abc, g_Goron.leanZ, in.stickMag, g_Goron.phase, g_Goron.spikes, g_Magic.value());
    if (ticks && g_AnimMode == AnimMode::Ball && (n % 20) == 1)
        OverlayLog("[goron] pos %.3f %.3f %.3f t %.3f cmd %.2f m/s\n", GRead<float>(actor + 0x31c), GRead<float>(actor + 0x320),
                   GRead<float>(actor + 0x324), std::chrono::duration<double>(now.time_since_epoch()).count(),
                   g_Goron.linearVelocity * goron_roll::kSpeedToMps);
}

// After the engine's movement step: MM's speed and direction into the character controller.
static void GoronAfterMoveStep() {
    if (!g_GoronEnabled) return;
    uint64_t actor = g_RollActorSeen.load();
    if (!actor) return;
    std::lock_guard<std::mutex> lk(g_GoronMx);
    float mps;
    if (g_Goron.active) mps = g_Goron.linearVelocity * goron_roll::kSpeedToMps;
    else if (g_AnimMode == AnimMode::Uncurl) mps = 0;          // Player_Action_4: stopped while uncurling
    else return;
    uint64_t cct = PlayerCct(actor), p28 = cct ? GRead<uint64_t>(cct + 0x28) : 0;
    if (!p28) return;
    GWrite<float>(p28 + 0x1d8, mps);
    static const bool velTest = getenv("HOOKSHOT_VY_TEST") != nullptr;   // probe: vertical velocity through +0x1fc
    if (velTest && g_Goron.active && g_Goron.airborne)
        GWrite<float>(cct + 0x2d4, g_Goron.velY * goron_roll::kSpeedToMps);
    if (g_Goron.active) {
        static const bool upTest = getenv("HOOKSHOT_UP_TEST") != nullptr;    // probe: does +0x1c4 move vertically?
        float up = upTest ? 1.0f : 0.0f, n = upTest ? sqrtf(2.0f) : 1.0f;
        GWrite<float>(p28 + 0x1c0, goron_roll::SinS(g_Goron.currentYaw) / n);
        GWrite<float>(p28 + 0x1c4, up / n);
        GWrite<float>(p28 + 0x1c8, goron_roll::CosS(g_Goron.currentYaw) / n);
    }
}

// ---- camera: post-hook on Hookshot's hos_oot_free_view(ctx, view), called once per frame from the game's camera
// update (f_804e48c0) with the gameplay view (vtable 0x84c18a88): eye doubles at +0x60, target at +0x78.
typedef void (*FreeViewFn)(GuestCtx*, uint64_t);
static FreeViewFn o_FreeView = nullptr;
static void GoronCamera(uint64_t view);
// FOV: the view embeds a sead::PerspectiveProjection at +0x90 (vtable, +0x98 dirty flags, +0x9c matrix, +0xdc device
// matrix, +0x128 near, +0x12c far, +0x130 fovy, +0x134 sin, +0x138 cos, +0x13c tan, +0x140 aspect). The camera update
// (f_804e48c0 +0x13958) sets the FOV after Hookshot's free-view call; f_8035a8c0 rebuilds the matrix. So the FOV is
// set (setFovy-style) in a pre-hook on f_8035a8c0.
static std::atomic<uint64_t> g_CamView{0};
static std::atomic<float> g_FovScale{1.0f};              // MM fovTarget / normal fov, blended
static GuestFn o_CameraUpdate = nullptr;
// Post-hook on the camera update f_804e48c0: it has just set the view's FOV (setFovy) for this frame; re-apply MM's
// FOV the same way (fovy, sin / cos / tan of fovy/2, dirty flags) so the lazily rebuilt projection uses it.
static void HookCameraUpdate(GuestCtx* c) {
    o_CameraUpdate(c);
    uint64_t v = g_CamView.load();
    float k = g_FovScale.load();
    if (!v || k == 1.0f) return;
    float base = GRead<float>(v + 0x130);
    if (!(base > 0.1f && base < 3.0f)) return;
    float f = std::min(3.0f, base * k);
    GWrite<float>(v + 0x130, f);
    GWrite<float>(v + 0x134, sinf(0.5f * f)); GWrite<float>(v + 0x138, cosf(0.5f * f)); GWrite<float>(v + 0x13c, tanf(0.5f * f));
    GWrite<uint8_t>(v + 0x98, 1); GWrite<uint8_t>(v + 0x99, 1);
    if (g_A64Dirty) g_A64Dirty[((v + 0x98) >> 12) & 0x7ffffff] = 1, g_A64Dirty[((v + 0x140) >> 12) & 0x7ffffff] = 1;
}
static void HookFreeView(GuestCtx* c, uint64_t view) {
    if (!view) return;                                   // (the original's `test rsi,rsi; je`)
    o_FreeView(c, view);
    GoronCamera(view);
}
static void InstallCameraHook() {
    uintptr_t f = ResolveHostSymbol("hos_oot_free_view");
    static const uint8_t want[] = {0x48, 0x85, 0xf6, 0x0f, 0x84};
    static const uint8_t pushes[] = {0x41, 0x57, 0x41, 0x56, 0x41, 0x55};
    if (!f || memcmp((void*)f, want, sizeof want) || memcmp((void*)(f + 9), pushes, sizeof pushes)) {
        OverlayLog("[camera] hos_oot_free_view not found or changed; Goron camera off\n");
        return;
    }
    if (!g_TrampPool) {
        g_TrampPool = (uint8_t*)mmap(nullptr, 1 << 16, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (g_TrampPool == MAP_FAILED) { g_TrampPool = nullptr; return; }
    }
    uint8_t* t = g_TrampPool + g_TrampUsed;               // trampoline: the three pushes, then back to +15
    memcpy(t, pushes, sizeof pushes);
    WriteAbsJmp(t + sizeof pushes, f + 15);
    g_TrampUsed += 32;
    uintptr_t page = f & ~(uintptr_t)0xFFF;
    if (mprotect((void*)page, 0x2000, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) return;
    o_FreeView = (FreeViewFn)t;
    g_A64Dirty = (uint8_t*)ResolveHostSymbol("a64_dirty");
    WriteAbsJmp((uint8_t*)f, (uintptr_t)HookFreeView);    // 14 bytes: test/je + the pushes (all re-done)
    OverlayLog("[camera] hos_oot_free_view hooked\n");
}

// MM's Goron camera (z_camera_data.inc.c, setting NORMAL0, CAM_FUNC_NORMAL1), applied as MM's mode change on top of
// the game's own camera:  CAM_FUNCDATA_NORM1(yOffset, distMin, distMax, pitchTarget, .., fovTarget, ..)
//   normal     (-20, 280, 320, 10, 22, 15, 40, 60, 84)
//   GORONDASH  ( 50, 160, 255, -2, 12, 10, 40, 92, 88)   spiked roll on the ground (z_player.c, PLAYER_STATE3_80000)
//   GORONJUMP  (-10, 160, 400,  0, 18, 12, 40, 80, 60)   spiked roll in the air
// Deltas from normal: look-at height (yOffset, MM units: Goron Link is 70 units / 1.68 m tall -> 0.024 m), distance
// (Camera_ClampDist2 into [distMin, distMax] from normal's ~300), pitch, and FOV (ratio to normal's 60). The blend
// follows Normal1's offset / fov update rate (Camera_ScaledStepToCeilF towards 0.05 per frame, x1.5 at 20 fps).
struct CamMode { float yOffset, distMin, distMax, pitch, fov; };
static const CamMode kCamNormal{-20, 280, 320, 10, 60}, kCamDash{50, 160, 255, -2, 92}, kCamJump{-10, 160, 400, 0, 80};

static void GoronCamera(uint64_t view) {
    static double blend = 0, last = 0;
    static int mode = 0;
    static auto t0 = std::chrono::steady_clock::now();
    double now = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    double dt = last ? std::min(0.1, now - last) : 0; last = now;
    static int dbgN = 0;
    if (getenv("HOOKSHOT_CAM_DEBUG") && (++dbgN % 60) == 0)
        OverlayLog("[camera] fov %.4f tan %.4f m00 %.4f m11 %.4f dev11 %.4f dirty %d%d scale %.3f x0..3 %llx\n",
                   GRead<float>(view + 0x130), GRead<float>(view + 0x13c), GRead<float>(view + 0x9c), GRead<float>(view + 0xb0),
                   GRead<float>(view + 0xf0), GRead<uint8_t>(view + 0x98), GRead<uint8_t>(view + 0x99),
                   g_FovScale.load(), (unsigned long long)view);
    static int watchN = 0;
    if (getenv("HOOKSHOT_FOV_WATCH") && ++watchN == 600)          // probe: who writes the view's FOV / projection
        if (const char* w = getenv("HOS_WATCH")) if (FILE* f = fopen(w, "w")) {
            fprintf(f, "%llx %llx\n", (unsigned long long)(view + 0x130), (unsigned long long)(view + 0xb0)); fclose(f);
            OverlayLog("[camera] watching view %llx +0x130 / +0xb0\n", (unsigned long long)view);
        }
    int want = g_CamMode.load();
    if (want) mode = want;
    double k = 1.0 - pow(1.0 - 0.05 * 1.5, dt * 20.0);   // MM per-frame rate, frame-rate independent
    blend += ((want ? 1.0 : 0.0) - blend) * k;
    // Player_RequestQuake(play, 27767, 7, 20) on the pound landing (QUAKE_TYPE_3: y perturbation 7, decaying)
    int qt = g_QuakeTick.load() - g_QuakeStart.load();
    double quake = 0;
    const int qd = g_QuakeDur.load();
    if (qt >= 0 && qt < qd) quake = sin(g_QuakeSpeed.load() * (M_PI / 32768.0) * qt) * g_QuakeY.load() * (qd - qt) / (double)qd;
    if (blend < 1e-4 && quake == 0) { blend = 0; g_FovScale = 1.0f; return; }
    if (blend < 1e-4) blend = 0;
    const CamMode& m = mode == 2 ? kCamJump : kCamDash;
    const double unit = 1.68 / 70.0;
    double E[3], T[3];
    for (int i = 0; i < 3; i++) { E[i] = GRead<double>(view + 0x60 + 8 * i); T[i] = GRead<double>(view + 0x78 + 8 * i); }
    double d[3] = {E[0] - T[0], E[1] - T[1], E[2] - T[2]};
    double D = sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (!(D > 1e-3) || !(D < 1e4)) return;
    double normalDist = 0.5 * (kCamNormal.distMin + kCamNormal.distMax);
    double pitch = asin(std::max(-1.0, std::min(1.0, d[1] / D))), yaw = atan2(d[0], d[2]);
    double distRatio = std::min(std::max(normalDist, (double)m.distMin), (double)m.distMax) / normalDist;
    // MM's geometry scaled to this camera's distance (the game's camera sits ~3.8 m away, MM's ~300 units = 7.2 m):
    // the look-at raise keeps MM's angle, atan(dy / dist), so the ball sits where MM frames it.
    (void)unit;
    double ty = T[1] + (m.yOffset - kCamNormal.yOffset) / normalDist * D * blend;
    double D2 = D * (1.0 + (distRatio - 1.0) * blend);
    double p2 = pitch + (m.pitch - kCamNormal.pitch) * (M_PI / 180.0) * blend;
    double qy = quake / normalDist * D;                    // (MM units scaled to this camera, as the offsets)
    double T2[3] = {T[0], ty + qy, T[2]};
    double E2[3] = {T2[0] + D2 * cos(p2) * sin(yaw), T2[1] + D2 * sin(p2), T2[2] + D2 * cos(p2) * cos(yaw)};
    // view matrix rows (right, up, back) with translation -row.eye, as the game / Hookshot's free view lay it out
    double bk[3] = {E2[0] - T2[0], E2[1] - T2[1], E2[2] - T2[2]};
    double bl = sqrt(bk[0] * bk[0] + bk[1] * bk[1] + bk[2] * bk[2]);
    for (double& x : bk) x /= bl;
    double rt[3] = {bk[2] * 1.0 - 0.0, 0.0, -bk[0]};      // cross((0,1,0), back) = (back.z, 0, -back.x)
    rt[0] = 1.0 * bk[2]; rt[2] = -1.0 * bk[0];
    double rl = sqrt(rt[0] * rt[0] + rt[2] * rt[2]);
    if (rl < 1e-6) return;
    rt[0] /= rl; rt[2] /= rl;
    double up[3] = {bk[1] * rt[2] - bk[2] * rt[1], bk[2] * rt[0] - bk[0] * rt[2], bk[0] * rt[1] - bk[1] * rt[0]};
    const double* rows[3] = {rt, up, bk};
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++) GWrite<float>(view + 0x8 + 0x10 * r + 4 * c, (float)rows[r][c]);
        GWrite<float>(view + 0x8 + 0x10 * r + 12, (float)-(rows[r][0] * E2[0] + rows[r][1] * E2[1] + rows[r][2] * E2[2]));
    }
    for (int i = 0; i < 3; i++) {
        GWrite<double>(view + 0x60 + 8 * i, E2[i]); GWrite<double>(view + 0x78 + 8 * i, T2[i]);
        GWrite<float>(view + 0x38 + 4 * i, (float)E2[i]); GWrite<float>(view + 0x44 + 4 * i, (float)T2[i]);
        GWrite<float>(view + 0x50 + 4 * i, (float)up[i]);
    }
    // FOV: MM's fovTarget ratio, re-applied after each camera update (HookCameraUpdate)
    g_CamView = view;
    g_FovScale = 1.0f + (m.fov / kCamNormal.fov - 1.0f) * (float)blend;
    if (g_A64Dirty)                                       // the runtime's guest-write tracking (as Hookshot does)
        for (uint64_t a = view & ~0xFFFULL; a < view + 0x150; a += 0x1000) g_A64Dirty[(a >> 12) & 0x7ffffff] = 1;
}

// probe (HOOKSHOT_PUSH_PROBE=1): f_804fe5d0 (a list push seen writing the hit list of a struck enemy): log args + LR
static GuestFn o_ListPush = nullptr;
static void HookListPush(GuestCtx* c) {
    static int n = 0;
    uint64_t lr = c->x[30], a0 = c->x[0], a1 = c->x[1], a2 = c->x[2], a3 = c->x[3];
    o_ListPush(c);
    if (n < 60) {
        n++;
        OverlayLog("[push] x0 %llx x1 %llx x2 %llx x3 %llx lr %llx\n", (unsigned long long)a0, (unsigned long long)a1,
                   (unsigned long long)a2, (unsigned long long)a3, (unsigned long long)lr);
    }
}

// probe (HOOKSHOT_SETUP_PROBE=1): callees of f_808f29c0 (ExecuteSensorHitSetupAttack core) called from it
static std::atomic<int> g_SetupProbeN{0};
static GuestFn o_SP_8283db80 = nullptr;
static void H_SP_8283db80(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_8283db80 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_8283db80(c);
}
static GuestFn o_SP_829624a0 = nullptr;
static void H_SP_829624a0(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_829624a0 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_829624a0(c);
}
static GuestFn o_SP_82e04460 = nullptr;
static void H_SP_82e04460(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82e04460 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82e04460(c);
}
static GuestFn o_SP_8283d950 = nullptr;
static void H_SP_8283d950(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_8283d950 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_8283d950(c);
}
static GuestFn o_SP_82e03e60 = nullptr;
static void H_SP_82e03e60(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82e03e60 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82e03e60(c);
}
static GuestFn o_SP_82ec5f00 = nullptr;
static void H_SP_82ec5f00(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82ec5f00 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82ec5f00(c);
}
static GuestFn o_SP_80236670 = nullptr;
static void H_SP_80236670(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80236670 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80236670(c);
}
static GuestFn o_SP_8017af50 = nullptr;
static void H_SP_8017af50(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_8017af50 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_8017af50(c);
}
static GuestFn o_SP_80179b90 = nullptr;
static void H_SP_80179b90(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80179b90 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80179b90(c);
}
static GuestFn o_SP_82b1b990 = nullptr;
static void H_SP_82b1b990(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82b1b990 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82b1b990(c);
}
static GuestFn o_SP_80b41a80 = nullptr;
static void H_SP_80b41a80(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80b41a80 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80b41a80(c);
}
static GuestFn o_SP_82ec6e90 = nullptr;
static void H_SP_82ec6e90(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82ec6e90 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82ec6e90(c);
}
static GuestFn o_SP_80ab4870 = nullptr;
static void H_SP_80ab4870(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80ab4870 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80ab4870(c);
}
static GuestFn o_SP_82ec6e30 = nullptr;
static void H_SP_82ec6e30(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82ec6e30 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82ec6e30(c);
}
static GuestFn o_SP_80a49690 = nullptr;
static void H_SP_80a49690(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80a49690 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80a49690(c);
}
static GuestFn o_SP_80b41830 = nullptr;
static void H_SP_80b41830(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_80b41830 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_80b41830(c);
}
static GuestFn o_SP_82739fb0 = nullptr;
static void H_SP_82739fb0(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82739fb0 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82739fb0(c);
}
static GuestFn o_SP_82e6f340 = nullptr;
static void H_SP_82e6f340(GuestCtx* c) {
    uint64_t lr = c->x[30];
    if (lr >= 0x808f29c0ULL && lr < 0x808f6000ULL && g_SetupProbeN.load() < 400) {
        g_SetupProbeN++;
        OverlayLog("[setup] f_82e6f340 lr %llx x0 %llx x1 %llx x2 %llx x3 %llx x4 %llx\n", (unsigned long long)lr,
                   (unsigned long long)c->x[0], (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   (unsigned long long)c->x[3], (unsigned long long)c->x[4]);
    }
    o_SP_82e6f340(c);
}

// probe (HOOKSHOT_CCTV_PROBE=1): CCT vtable methods called on Link's CCT within 0.4 s of a B press (the game's jump)
static std::atomic<int> g_CctvN{0};
static void CctvLog(const char* slots, GuestCtx* c) {
    int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    if (now > g_CctvUntil.load() || c->x[0] != g_PlayerCct.load() || g_CctvN.load() > 300) return;
    g_CctvN++;
    char extra[256] = "";
    uint64_t a1 = c->x[1];
    if (a1 >= 0x1000000000ULL && a1 < 0x1200000000ULL)
        snprintf(extra, sizeof extra, " *x1=(%.3f %.3f %.3f %.3f | %08x %08x %08x %08x)", GRead<float>(a1), GRead<float>(a1 + 4),
                 GRead<float>(a1 + 8), GRead<float>(a1 + 12), GRead<uint32_t>(a1), GRead<uint32_t>(a1 + 4), GRead<uint32_t>(a1 + 8),
                 GRead<uint32_t>(a1 + 12));
    OverlayLog("[cctv] slot %s x1 %llx x2 %llx s0 %.3f%s lr %llx\n", slots, (unsigned long long)c->x[1],
               (unsigned long long)c->x[2], VregF(c, 0), extra, (unsigned long long)c->x[30]);
}
static GuestFn o_CV_838b4a20 = nullptr;
static void H_CV_838b4a20(GuestCtx* c) { CctvLog("0", c); o_CV_838b4a20(c); }
static GuestFn o_CV_838b4a30 = nullptr;
static void H_CV_838b4a30(GuestCtx* c) { CctvLog("1", c); o_CV_838b4a30(c); }
static GuestFn o_CV_838b4aa0 = nullptr;
static void H_CV_838b4aa0(GuestCtx* c) { CctvLog("2", c); o_CV_838b4aa0(c); }
static GuestFn o_CV_80699230 = nullptr;
static void H_CV_80699230(GuestCtx* c) { CctvLog("3", c); o_CV_80699230(c); }
static GuestFn o_CV_838b38d0 = nullptr;
static void H_CV_838b38d0(GuestCtx* c) { CctvLog("4", c); o_CV_838b38d0(c); }
static GuestFn o_CV_838b3a00 = nullptr;
static void H_CV_838b3a00(GuestCtx* c) { CctvLog("5", c); o_CV_838b3a00(c); }
static GuestFn o_CV_806d2520 = nullptr;
static void H_CV_806d2520(GuestCtx* c) { CctvLog("6", c); o_CV_806d2520(c); }
static GuestFn o_CV_838b38c0 = nullptr;
static void H_CV_838b38c0(GuestCtx* c) { CctvLog("7", c); o_CV_838b38c0(c); }
static GuestFn o_CV_80699240 = nullptr;
static void H_CV_80699240(GuestCtx* c) { CctvLog("8", c); o_CV_80699240(c); }
static GuestFn o_CV_838b4360 = nullptr;
static void H_CV_838b4360(GuestCtx* c) { CctvLog("9", c); o_CV_838b4360(c); }
static GuestFn o_CV_80559e20 = nullptr;
static void H_CV_80559e20(GuestCtx* c) { CctvLog("10", c); o_CV_80559e20(c); }
static GuestFn o_CV_8074a130 = nullptr;
static void H_CV_8074a130(GuestCtx* c) { CctvLog("11", c); o_CV_8074a130(c); }
static GuestFn o_CV_838b4370 = nullptr;
static void H_CV_838b4370(GuestCtx* c) { CctvLog("12", c); o_CV_838b4370(c); }
static GuestFn o_CV_8015d480 = nullptr;
static void H_CV_8015d480(GuestCtx* c) { CctvLog("13", c); o_CV_8015d480(c); }
static GuestFn o_CV_80559df0 = nullptr;
static void H_CV_80559df0(GuestCtx* c) { CctvLog("14", c); o_CV_80559df0(c); }
static GuestFn o_CV_80559cd0 = nullptr;
static void H_CV_80559cd0(GuestCtx* c) { CctvLog("15", c); o_CV_80559cd0(c); }
static GuestFn o_CV_838b4390 = nullptr;
static void H_CV_838b4390(GuestCtx* c) { CctvLog("16", c); o_CV_838b4390(c); }
static GuestFn o_CV_838b43c0 = nullptr;
static void H_CV_838b43c0(GuestCtx* c) { CctvLog("17", c); o_CV_838b43c0(c); }
static GuestFn o_CV_838b43d0 = nullptr;
static void H_CV_838b43d0(GuestCtx* c) { CctvLog("18", c); o_CV_838b43d0(c); }
static GuestFn o_CV_838b4400 = nullptr;
static void H_CV_838b4400(GuestCtx* c) { CctvLog("19", c); o_CV_838b4400(c); }
static GuestFn o_CV_838b4410 = nullptr;
static void H_CV_838b4410(GuestCtx* c) { CctvLog("20", c); o_CV_838b4410(c); }
static GuestFn o_CV_807490b0 = nullptr;
static void H_CV_807490b0(GuestCtx* c) { CctvLog("21", c); o_CV_807490b0(c); }
static GuestFn o_CV_838b3b60 = nullptr;
static void H_CV_838b3b60(GuestCtx* c) { CctvLog("22", c); o_CV_838b3b60(c); }
static GuestFn o_CV_809f8440 = nullptr;
static void H_CV_809f8440(GuestCtx* c) { CctvLog("23", c); o_CV_809f8440(c); }
static GuestFn o_CV_809f7ff0 = nullptr;
static void H_CV_809f7ff0(GuestCtx* c) { CctvLog("24", c); o_CV_809f7ff0(c); }
static GuestFn o_CV_806d2330 = nullptr;
static void H_CV_806d2330(GuestCtx* c) { CctvLog("25", c); o_CV_806d2330(c); }
static GuestFn o_CV_806d1b00 = nullptr;
static void H_CV_806d1b00(GuestCtx* c) { CctvLog("26", c); o_CV_806d1b00(c); }
static GuestFn o_CV_838b3bc0 = nullptr;
static void H_CV_838b3bc0(GuestCtx* c) { CctvLog("27", c); o_CV_838b3bc0(c); }
static GuestFn o_CV_80536ab0 = nullptr;
static void H_CV_80536ab0(GuestCtx* c) { CctvLog("28", c); o_CV_80536ab0(c); }
static GuestFn o_CV_806d2530 = nullptr;
static void H_CV_806d2530(GuestCtx* c) { CctvLog("29", c); o_CV_806d2530(c); }
static GuestFn o_CV_838b3bd0 = nullptr;
static void H_CV_838b3bd0(GuestCtx* c) { CctvLog("30", c); o_CV_838b3bd0(c); }
static GuestFn o_CV_838b3cb0 = nullptr;
static void H_CV_838b3cb0(GuestCtx* c) { CctvLog("31", c); o_CV_838b3cb0(c); }
static GuestFn o_CV_838b3cf0 = nullptr;
static void H_CV_838b3cf0(GuestCtx* c) { CctvLog("32", c); o_CV_838b3cf0(c); }
static GuestFn o_CV_838b3d70 = nullptr;
static void H_CV_838b3d70(GuestCtx* c) { CctvLog("33", c); o_CV_838b3d70(c); }
static GuestFn o_CV_806d8910 = nullptr;
static void H_CV_806d8910(GuestCtx* c) { CctvLog("34", c); o_CV_806d8910(c); }
static GuestFn o_CV_838b3ef0 = nullptr;
static void H_CV_838b3ef0(GuestCtx* c) { CctvLog("35", c); o_CV_838b3ef0(c); }
static GuestFn o_CV_838b3f40 = nullptr;
static void H_CV_838b3f40(GuestCtx* c) { CctvLog("36", c); o_CV_838b3f40(c); }
static GuestFn o_CV_838b3f50 = nullptr;
static void H_CV_838b3f50(GuestCtx* c) { CctvLog("37", c); o_CV_838b3f50(c); }
static GuestFn o_CV_8013ffe0 = nullptr;
static void H_CV_8013ffe0(GuestCtx* c) { CctvLog("38", c); o_CV_8013ffe0(c); }
static GuestFn o_CV_80149940 = nullptr;
static void H_CV_80149940(GuestCtx* c) { CctvLog("39", c); o_CV_80149940(c); }
static GuestFn o_CV_838b3f70 = nullptr;
static void H_CV_838b3f70(GuestCtx* c) { CctvLog("40", c); o_CV_838b3f70(c); }
static GuestFn o_CV_809691e0 = nullptr;
static void H_CV_809691e0(GuestCtx* c) { CctvLog("41", c); o_CV_809691e0(c); }
static GuestFn o_CV_80142d30 = nullptr;
static void H_CV_80142d30(GuestCtx* c) { CctvLog("42", c); o_CV_80142d30(c); }
static GuestFn o_CV_806a8580 = nullptr;
static void H_CV_806a8580(GuestCtx* c) { CctvLog("43", c); o_CV_806a8580(c); }
static GuestFn o_CV_838b4060 = nullptr;
static void H_CV_838b4060(GuestCtx* c) { CctvLog("44", c); o_CV_838b4060(c); }
static GuestFn o_CV_838b4070 = nullptr;
static void H_CV_838b4070(GuestCtx* c) { CctvLog("45", c); o_CV_838b4070(c); }
static GuestFn o_CV_838b40d0 = nullptr;
static void H_CV_838b40d0(GuestCtx* c) { CctvLog("46", c); o_CV_838b40d0(c); }
static GuestFn o_CV_838b40e0 = nullptr;
static void H_CV_838b40e0(GuestCtx* c) { CctvLog("47", c); o_CV_838b40e0(c); }
static GuestFn o_CV_838b40f0 = nullptr;
static void H_CV_838b40f0(GuestCtx* c) { CctvLog("48", c); o_CV_838b40f0(c); }
static GuestFn o_CV_838b4100 = nullptr;
static void H_CV_838b4100(GuestCtx* c) { CctvLog("49", c); o_CV_838b4100(c); }
static GuestFn o_CV_838b4110 = nullptr;
static void H_CV_838b4110(GuestCtx* c) { CctvLog("50", c); o_CV_838b4110(c); }
static GuestFn o_CV_838b4120 = nullptr;
static void H_CV_838b4120(GuestCtx* c) { CctvLog("51", c); o_CV_838b4120(c); }
static GuestFn o_CV_808c3490 = nullptr;
static void H_CV_808c3490(GuestCtx* c) { CctvLog("52", c); o_CV_808c3490(c); }
static GuestFn o_CV_8018c260 = nullptr;
static void H_CV_8018c260(GuestCtx* c) { CctvLog("53", c); o_CV_8018c260(c); }
static GuestFn o_CV_838b4130 = nullptr;
static void H_CV_838b4130(GuestCtx* c) { CctvLog("54", c); o_CV_838b4130(c); }
static GuestFn o_CV_838b4140 = nullptr;
static void H_CV_838b4140(GuestCtx* c) { CctvLog("55", c); o_CV_838b4140(c); }
static GuestFn o_CV_838b4150 = nullptr;
static void H_CV_838b4150(GuestCtx* c) { CctvLog("56", c); o_CV_838b4150(c); }
static GuestFn o_CV_838b4160 = nullptr;
static void H_CV_838b4160(GuestCtx* c) { CctvLog("57", c); o_CV_838b4160(c); }
static GuestFn o_CV_80536f60 = nullptr;
static void H_CV_80536f60(GuestCtx* c) { CctvLog("58", c); o_CV_80536f60(c); }
static GuestFn o_CV_838b41b0 = nullptr;
static void H_CV_838b41b0(GuestCtx* c) { CctvLog("59", c); o_CV_838b41b0(c); }
static GuestFn o_CV_838b41c0 = nullptr;
static void H_CV_838b41c0(GuestCtx* c) { CctvLog("60", c); o_CV_838b41c0(c); }
static GuestFn o_CV_838b41d0 = nullptr;
static void H_CV_838b41d0(GuestCtx* c) { CctvLog("61", c); o_CV_838b41d0(c); }
static GuestFn o_CV_838b41e0 = nullptr;
static void H_CV_838b41e0(GuestCtx* c) { CctvLog("62", c); o_CV_838b41e0(c); }
static GuestFn o_CV_838b42a0 = nullptr;
static void H_CV_838b42a0(GuestCtx* c) { CctvLog("63", c); o_CV_838b42a0(c); }
static GuestFn o_CV_838b42b0 = nullptr;
static void H_CV_838b42b0(GuestCtx* c) { CctvLog("64", c); o_CV_838b42b0(c); }
static GuestFn o_CV_838b4350 = nullptr;
static void H_CV_838b4350(GuestCtx* c) { CctvLog("65", c); o_CV_838b4350(c); }
static GuestFn o_CV_8015dc50 = nullptr;
static void H_CV_8015dc50(GuestCtx* c) { CctvLog("66", c); o_CV_8015dc50(c); }
static GuestFn o_CV_838b4980 = nullptr;
static void H_CV_838b4980(GuestCtx* c) { CctvLog("67", c); o_CV_838b4980(c); }
static GuestFn o_CV_838b49c0 = nullptr;
static void H_CV_838b49c0(GuestCtx* c) { CctvLog("68", c); o_CV_838b49c0(c); }
static GuestFn o_CV_838b4a00 = nullptr;
static void H_CV_838b4a00(GuestCtx* c) { CctvLog("69", c); o_CV_838b4a00(c); }
static GuestFn o_CV_838b4a10 = nullptr;
static void H_CV_838b4a10(GuestCtx* c) { CctvLog("70", c); o_CV_838b4a10(c); }
static GuestFn o_CV_8015e650 = nullptr;
static void H_CV_8015e650(GuestCtx* c) { CctvLog("71", c); o_CV_8015e650(c); }
static GuestFn o_CV_806d1aa0 = nullptr;
static void H_CV_806d1aa0(GuestCtx* c) { CctvLog("72", c); o_CV_806d1aa0(c); }
static GuestFn o_CV_838b4ab0 = nullptr;
static void H_CV_838b4ab0(GuestCtx* c) { CctvLog("73", c); o_CV_838b4ab0(c); }
static GuestFn o_CV_806d23a0 = nullptr;
static void H_CV_806d23a0(GuestCtx* c) { CctvLog("74", c); o_CV_806d23a0(c); }

// The Goron Link mod's folder, only while the mod is enabled (the mod manager disables a mod by prefixing its folder
// with '_' or '-'). Empty when it is off: then none of the Goron code is installed.
static const char* GoronModDir() {
    static char dir[512];
    static bool done = false;
    if (!done) {
        done = true;
        if (FILE* p = popen("ls mods/[!_-]*/goron_driver.bin 2>/dev/null | head -1", "r")) {
            if (fgets(dir, sizeof dir, p)) {
                dir[strcspn(dir, "\n")] = 0;
                if (char* s = strrchr(dir, '/')) *s = 0;
            }
            pclose(p);
        }
    }
    return dir;
}

static void InstallModHooks() {
    const char* gr = getenv("HOOKSHOT_GORON_ROLL");                 // MM Goron roll: with the mod, unless =0
    const bool goronMod = GoronModDir()[0] != 0;
    OverlayLog("[goron] Goron Link mod %s\n", goronMod ? GoronModDir() : "not enabled: roll code off");
    if (goronMod && (!gr || strcmp(gr, "0") != 0)) {
        {
            auto f = ResolveGuestFns({0x806d2530});                // CCT changePosition (ground pound height)
            if (f.count(0x806d2530)) g_CctChangePosition = (GuestFn)f[0x806d2530];
        }
        g_GoronEnabled = true;
        InstallInputHooks();
        InstallGuestHooks({{0x809552f0, HookMoveStep, &o_MoveStep, "movement step"}});
        if (!o_ActorApplyTransform)
            InstallGuestHooks({{0x80156ae0, HookActorApplyTransform, &o_ActorApplyTransform, "actor apply transform"}});
        InstallGuestHooks({{0x81501400, HookLocalToWorld, &o_LocalToWorld, "skeleton local->world"}});
        InstallCameraHook();
        if (getenv("HOOKSHOT_SETUP_PROBE")) InstallGuestHooks({{0x8283db80, H_SP_8283db80, &o_SP_8283db80, "f_8283db80"}, {0x829624a0, H_SP_829624a0, &o_SP_829624a0, "f_829624a0"}, {0x82e04460, H_SP_82e04460, &o_SP_82e04460, "f_82e04460"}, {0x8283d950, H_SP_8283d950, &o_SP_8283d950, "f_8283d950"}, {0x82e03e60, H_SP_82e03e60, &o_SP_82e03e60, "f_82e03e60"}, {0x82ec5f00, H_SP_82ec5f00, &o_SP_82ec5f00, "f_82ec5f00"}, {0x80236670, H_SP_80236670, &o_SP_80236670, "f_80236670"}, {0x8017af50, H_SP_8017af50, &o_SP_8017af50, "f_8017af50"}, {0x80179b90, H_SP_80179b90, &o_SP_80179b90, "f_80179b90"}, {0x82b1b990, H_SP_82b1b990, &o_SP_82b1b990, "f_82b1b990"}, {0x80b41a80, H_SP_80b41a80, &o_SP_80b41a80, "f_80b41a80"}, {0x82ec6e90, H_SP_82ec6e90, &o_SP_82ec6e90, "f_82ec6e90"}, {0x80ab4870, H_SP_80ab4870, &o_SP_80ab4870, "f_80ab4870"}, {0x82ec6e30, H_SP_82ec6e30, &o_SP_82ec6e30, "f_82ec6e30"}, {0x80a49690, H_SP_80a49690, &o_SP_80a49690, "f_80a49690"}, {0x80b41830, H_SP_80b41830, &o_SP_80b41830, "f_80b41830"}, {0x82739fb0, H_SP_82739fb0, &o_SP_82739fb0, "f_82739fb0"}, {0x82e6f340, H_SP_82e6f340, &o_SP_82e6f340, "f_82e6f340"}});
        if (getenv("HOOKSHOT_CCTV_PROBE")) InstallGuestHooks({{0x838b4a20, H_CV_838b4a20, &o_CV_838b4a20, "cct slot 0"}, {0x838b4a30, H_CV_838b4a30, &o_CV_838b4a30, "cct slot 1"}, {0x838b4aa0, H_CV_838b4aa0, &o_CV_838b4aa0, "cct slot 2"}, {0x80699230, H_CV_80699230, &o_CV_80699230, "cct slot 3"}, {0x838b38d0, H_CV_838b38d0, &o_CV_838b38d0, "cct slot 4"}, {0x838b3a00, H_CV_838b3a00, &o_CV_838b3a00, "cct slot 5"}, {0x806d2520, H_CV_806d2520, &o_CV_806d2520, "cct slot 6"}, {0x838b38c0, H_CV_838b38c0, &o_CV_838b38c0, "cct slot 7"}, {0x80699240, H_CV_80699240, &o_CV_80699240, "cct slot 8"}, {0x838b4360, H_CV_838b4360, &o_CV_838b4360, "cct slot 9"}, {0x80559e20, H_CV_80559e20, &o_CV_80559e20, "cct slot 10"}, {0x8074a130, H_CV_8074a130, &o_CV_8074a130, "cct slot 11"}, {0x838b4370, H_CV_838b4370, &o_CV_838b4370, "cct slot 12"}, {0x8015d480, H_CV_8015d480, &o_CV_8015d480, "cct slot 13"}, {0x80559df0, H_CV_80559df0, &o_CV_80559df0, "cct slot 14"}, {0x80559cd0, H_CV_80559cd0, &o_CV_80559cd0, "cct slot 15"}, {0x838b4390, H_CV_838b4390, &o_CV_838b4390, "cct slot 16"}, {0x838b43c0, H_CV_838b43c0, &o_CV_838b43c0, "cct slot 17"}, {0x838b43d0, H_CV_838b43d0, &o_CV_838b43d0, "cct slot 18"}, {0x838b4400, H_CV_838b4400, &o_CV_838b4400, "cct slot 19"}, {0x838b4410, H_CV_838b4410, &o_CV_838b4410, "cct slot 20"}, {0x807490b0, H_CV_807490b0, &o_CV_807490b0, "cct slot 21"}, {0x838b3b60, H_CV_838b3b60, &o_CV_838b3b60, "cct slot 22"}, {0x809f8440, H_CV_809f8440, &o_CV_809f8440, "cct slot 23"}, {0x809f7ff0, H_CV_809f7ff0, &o_CV_809f7ff0, "cct slot 24"}, {0x806d2330, H_CV_806d2330, &o_CV_806d2330, "cct slot 25"}, {0x806d1b00, H_CV_806d1b00, &o_CV_806d1b00, "cct slot 26"}, {0x838b3bc0, H_CV_838b3bc0, &o_CV_838b3bc0, "cct slot 27"}, {0x80536ab0, H_CV_80536ab0, &o_CV_80536ab0, "cct slot 28"}, {0x806d2530, H_CV_806d2530, &o_CV_806d2530, "cct slot 29"}, {0x838b3bd0, H_CV_838b3bd0, &o_CV_838b3bd0, "cct slot 30"}, {0x838b3cb0, H_CV_838b3cb0, &o_CV_838b3cb0, "cct slot 31"}, {0x838b3cf0, H_CV_838b3cf0, &o_CV_838b3cf0, "cct slot 32"}, {0x838b3d70, H_CV_838b3d70, &o_CV_838b3d70, "cct slot 33"}, {0x806d8910, H_CV_806d8910, &o_CV_806d8910, "cct slot 34"}, {0x838b3ef0, H_CV_838b3ef0, &o_CV_838b3ef0, "cct slot 35"}, {0x838b3f40, H_CV_838b3f40, &o_CV_838b3f40, "cct slot 36"}, {0x838b3f50, H_CV_838b3f50, &o_CV_838b3f50, "cct slot 37"}, {0x8013ffe0, H_CV_8013ffe0, &o_CV_8013ffe0, "cct slot 38"}, {0x80149940, H_CV_80149940, &o_CV_80149940, "cct slot 39"}, {0x838b3f70, H_CV_838b3f70, &o_CV_838b3f70, "cct slot 40"}, {0x809691e0, H_CV_809691e0, &o_CV_809691e0, "cct slot 41"}, {0x80142d30, H_CV_80142d30, &o_CV_80142d30, "cct slot 42"}, {0x806a8580, H_CV_806a8580, &o_CV_806a8580, "cct slot 43"}, {0x838b4060, H_CV_838b4060, &o_CV_838b4060, "cct slot 44"}, {0x838b4070, H_CV_838b4070, &o_CV_838b4070, "cct slot 45"}, {0x838b40d0, H_CV_838b40d0, &o_CV_838b40d0, "cct slot 46"}, {0x838b40e0, H_CV_838b40e0, &o_CV_838b40e0, "cct slot 47"}, {0x838b40f0, H_CV_838b40f0, &o_CV_838b40f0, "cct slot 48"}, {0x838b4100, H_CV_838b4100, &o_CV_838b4100, "cct slot 49"}, {0x838b4110, H_CV_838b4110, &o_CV_838b4110, "cct slot 50"}, {0x838b4120, H_CV_838b4120, &o_CV_838b4120, "cct slot 51"}, {0x808c3490, H_CV_808c3490, &o_CV_808c3490, "cct slot 52"}, {0x8018c260, H_CV_8018c260, &o_CV_8018c260, "cct slot 53"}, {0x838b4130, H_CV_838b4130, &o_CV_838b4130, "cct slot 54"}, {0x838b4140, H_CV_838b4140, &o_CV_838b4140, "cct slot 55"}, {0x838b4150, H_CV_838b4150, &o_CV_838b4150, "cct slot 56"}, {0x838b4160, H_CV_838b4160, &o_CV_838b4160, "cct slot 57"}, {0x80536f60, H_CV_80536f60, &o_CV_80536f60, "cct slot 58"}, {0x838b41b0, H_CV_838b41b0, &o_CV_838b41b0, "cct slot 59"}, {0x838b41c0, H_CV_838b41c0, &o_CV_838b41c0, "cct slot 60"}, {0x838b41d0, H_CV_838b41d0, &o_CV_838b41d0, "cct slot 61"}, {0x838b41e0, H_CV_838b41e0, &o_CV_838b41e0, "cct slot 62"}, {0x838b42a0, H_CV_838b42a0, &o_CV_838b42a0, "cct slot 63"}, {0x838b42b0, H_CV_838b42b0, &o_CV_838b42b0, "cct slot 64"}, {0x838b4350, H_CV_838b4350, &o_CV_838b4350, "cct slot 65"}, {0x8015dc50, H_CV_8015dc50, &o_CV_8015dc50, "cct slot 66"}, {0x838b4980, H_CV_838b4980, &o_CV_838b4980, "cct slot 67"}, {0x838b49c0, H_CV_838b49c0, &o_CV_838b49c0, "cct slot 68"}, {0x838b4a00, H_CV_838b4a00, &o_CV_838b4a00, "cct slot 69"}, {0x838b4a10, H_CV_838b4a10, &o_CV_838b4a10, "cct slot 70"}, {0x8015e650, H_CV_8015e650, &o_CV_8015e650, "cct slot 71"}, {0x806d1aa0, H_CV_806d1aa0, &o_CV_806d1aa0, "cct slot 72"}, {0x838b4ab0, H_CV_838b4ab0, &o_CV_838b4ab0, "cct slot 73"}, {0x806d23a0, H_CV_806d23a0, &o_CV_806d23a0, "cct slot 74"}});
        if (getenv("HOOKSHOT_PUSH_PROBE")) InstallGuestHooks({{0x804fe5d0, HookListPush, &o_ListPush, "list push"}});
        InstallGuestHooks({{0x804e48c0, HookCameraUpdate, &o_CameraUpdate, "camera update"}});
        // The game's hold-A sprint (now the rolling ball) carries a held roll; re-pressing A at the roll's end is
        // only needed without it.
        if (getenv("HOOKSHOT_ROLL_CHAIN"))
            InstallGuestHooks({{0x809590b0, HookRollLeave, &o_RollLeave, "roll leave"}});
        if (!getenv("HOOKSHOT_ROLL_VEL") && !getenv("HOOKSHOT_ROLL_PROBE"))
            InstallGuestHooks({{0x80953e80, VelRollUpdate, &o_RollUpdate, "roll update"}});
    }
    if (getenv("HOOKSHOT_TELEPORT_FILE") && !o_ActorApplyTransform) {          // (tests)
        auto f = ResolveGuestFns({0x806d2530});
        if (f.count(0x806d2530)) g_CctChangePosition = (GuestFn)f[0x806d2530];
        InstallGuestHooks({{0x80156ae0, HookActorApplyTransform, &o_ActorApplyTransform, "actor apply transform"}});
    }
    if (const char* tp = getenv("HOOKSHOT_TELEPORT")) {
        if (sscanf(tp, "%f,%f,%f", &g_TeleportTo[0], &g_TeleportTo[1], &g_TeleportTo[2]) == 3) {
            auto f = ResolveGuestFns({0x806d2530});
            if (f.count(0x806d2530)) g_CctChangePosition = (GuestFn)f[0x806d2530];
            g_TeleportPending = 1;
            InstallGuestHooks({{0x80156ae0, HookActorApplyTransform, &o_ActorApplyTransform, "actor apply transform"}});
        }
    }

    if (const char* h = getenv("HOOKSHOT_HOLD_ROLL")) g_HoldRollFrames = atoi(h);
    if (getenv("HOOKSHOT_TICK_LOG"))
        InstallGuestHooks({{0x8054dd00, HookNodeTick, &o_NodeTick, "execute node tick"}});
    if (const char* v = getenv("HOOKSHOT_ROLL_SPEED")) {
        g_RollSpeed = (float)atof(v);
        InstallGuestHooks({{0x809552f0, HookMoveStep, &o_MoveStep, "movement step"}});
        if (!o_ActorApplyTransform)
            InstallGuestHooks({{0x80156ae0, HookActorApplyTransform, &o_ActorApplyTransform, "actor apply transform"}});
        InstallGuestHooks({{0x81501400, HookLocalToWorld, &o_LocalToWorld, "skeleton local->world"}});
    }
    if (getenv("HOOKSHOT_CCT_DIAG")) {
        InstallGuestHooks({{0x80156ae0, HookActorApplyTransform, &o_ActorApplyTransform, "actor apply transform"},
                           {0x807490b0, HookCctA8, &o_CctA8, "cct +a8"}, {0x80536ab0, HookCctE0, &o_CctE0, "cct +e0"},
                           {0x8015d480, HookCct68, &o_Cct68, "cct +68"}, {0x8074a130, HookCct58, &o_Cct58, "cct +58"}});
        return;
    }
    if (const char* v = getenv("HOOKSHOT_ROLL_VEL")) {
        g_RollVel = (float)atof(v);
        ResolveEngineCalls();
        InstallGuestHooks({{0x80953e80, VelRollUpdate, &o_RollUpdate, "roll update velocity test"},
                           {0x80559cd0, HookCctSetLinearVelocity, &o_CctSetLinearVelocity, "cct setLinearVelocity"}});
        return;
    }
    if (getenv("HOOKSHOT_POS_SEARCH")) {
        InstallGuestHooks({{0x80953e80, PosSearchRollUpdate, &o_RollUpdate, "roll update pos search"}});
        return;
    }
    if (const char* p = getenv("HOOKSHOT_ROLL_PROBE")) {
        g_RollProbe = fopen(p, "wb");
        InstallGuestHooks({{0x80953e80, ProbeRollUpdate, &o_RollUpdate, "roll update probe"}});
    }
}
