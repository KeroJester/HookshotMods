// Item Controls research tools (debug switches only; included by item_controls.inc inside namespace itemctl).
//   HOOKSHOT_ITEMCTL_DEBUG=1        button/value logs, the scanners below
//   HOOKSHOT_ITEMCTL_FIND=<file>    value search: "=hex[|hex..]" exact, "!" changed, "~" unchanged, "*0|0|range" snapshot
//   HOOKSHOT_ITEMCTL_AUTOWATCH=1    arm HOS_WATCH on a single remaining hit
//   HOOKSHOT_ITEMCTL_DUMP=a:len,..  dump regions after 60 s;  HOOKSHOT_ITEMCTL_OBJ=<file> dump objects by vtable
//   HOOKSHOT_ITEMCTL_REGION=<hex>   with OBJ: dump +-range around the first object to re/runs/region_*.bin
//   HOOKSHOT_ITEMCTL_LOADTRACE=1    log preview-model load requests (f_80b17e20);  HOOKSHOT_ITEMCTL_LOADALL=1 all names
//   HOOKSHOT_ITEMCTL_PROBE=1        count base-screen calls that get the pause screen
static std::atomic<uint64_t> g_AddrChild{0}, g_AddrAdult{0};   // research monitor: live value addresses

// {u32 hash, u32 value} pairs, as for the other PlayerStatus values; several copies exist (save mirrors), so log all
static void ScanOnce() {
    const uint64_t base = 0x100000000000ULL;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    std::vector<unsigned char> vec;
    int n = 0;
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
                    uint32_t h = p[i];
                    if (h != kHashAssignChild && h != kHashAssignAdult) continue;
                    uint64_t g = (uint64_t)(p + i + 1) - base;
                    OverlayLog("[itemctl] %s at 0x%llx = %08x\n", h == kHashAssignChild ? "AssignableItem.Child" : "AssignableItem.Adult",
                               (unsigned long long)g, p[i + 1]);
                    uint64_t e = ResolveEntry(g - 4, p[i + 1]);
                    if (e) OverlayLog("[itemctl]   -> entry 0x%llx, current %08x\n", (unsigned long long)e, GRead<uint32_t>(e + 0x1C));
                    if (e && h == kHashAssignChild) g_AddrChild = e + 0x1C;
                    if (e && h == kHashAssignAdult) g_AddrAdult = e + 0x1C;
                    n++;
                }
            }
        }
    }
    if (m) fclose(m);
    OverlayLog("[itemctl] scan: %d candidates\n", n);
}

// Research: HOOKSHOT_ITEMCTL_FIND=<file>; each time the file's text (a hex u32) changes, the first search lists every
// aligned u32 equal to it in writable guest memory, later ones keep only the previous hits that now hold the new value.
static std::vector<uint64_t> g_Objs;
static std::vector<uint64_t> g_Hits;
static std::vector<uint32_t> g_HitVals;
// mode '=': value == want (first time: whole memory); '!': changed since the last step; '~': unchanged
static std::vector<uint32_t> g_Want;               // '=' accepts any of these
static bool Wanted(uint32_t v) { for (uint32_t w : g_Want) if (v == w) return true; return false; }
static void FindValue(char mode, uint32_t want) {
    const uint64_t base = 0x100000000000ULL;
    std::vector<uint64_t> hits;
    if (!g_Hits.empty()) {
        for (size_t i = 0; i < g_Hits.size(); i++) {
            uint32_t v = GRead<uint32_t>(g_Hits[i]);
            if ((mode == '=' && Wanted(v)) || (mode == '!' && v != g_HitVals[i]) || (mode == '~' && v == g_HitVals[i]))
                hits.push_back(g_Hits[i]);
        }
    } else if (mode == '*') {                         // snapshot every u32 within want-range (g_Want = {lo32, hi32, range})
        uint64_t center = ((uint64_t)g_Want[0] << 32) | g_Want[1], range = g_Want.size() > 2 ? g_Want[2] : 0x2000000;
        if (!center && !g_Objs.empty()) center = g_Objs[0];          // (around the object found by HOOKSHOT_ITEMCTL_OBJ)
        OverlayLog("[itemctl] snapshot around 0x%llx +-0x%llx\n", (unsigned long long)center, (unsigned long long)range);
        for (uint64_t a = (center - range) & ~0xFFFULL; a < center + range; a += 4) {
            uintptr_t hp = (uintptr_t)GPtr(a);
            unsigned char v;
            if ((a & 0xFFF) == 0 && mincore((void*)hp, 4096, &v) != 0) { a += 0xFFC; continue; }
            hits.push_back(a);
        }
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
                    for (int i = 0; i < 1024; i++) if (Wanted(p[i])) hits.push_back((uint64_t)(p + i) - base);
                }
            }
        }
        if (m) fclose(m);
    }
    g_Hits = hits;
    g_HitVals.clear();
    for (uint64_t g : hits) g_HitVals.push_back(GRead<uint32_t>(g));
    OverlayLog("[itemctl] find %c%08x: %zu hits\n", mode, want, hits.size());
    for (size_t i = 0; i < hits.size() && i < 60; i++)
        OverlayLog("[itemctl]   hit 0x%llx = %08x\n", (unsigned long long)hits[i], g_HitVals[i]);
    const char* w = getenv("HOS_WATCH");
    if (hits.size() == 1 && w && getenv("HOOKSHOT_ITEMCTL_AUTOWATCH")) {   // arm Hookshot's data breakpoints on it
        if (FILE* f = fopen(w, "w")) { fprintf(f, "%llx\n", (unsigned long long)hits[0]); fclose(f); }
        OverlayLog("[itemctl] watch armed on 0x%llx\n", (unsigned long long)hits[0]);
    }
}

static void DumpRegions(const char* spec) {        // "hexaddr:hexlen,..." -> overlay/itemctl_<addr>.bin
    std::string s = spec;
    size_t pos = 0;
    while (pos < s.size()) {
        size_t e = s.find(',', pos); if (e == std::string::npos) e = s.size();
        unsigned long long a = 0, n = 0;
        if (sscanf(s.substr(pos, e - pos).c_str(), "%llx:%llx", &a, &n) == 2) {
            char path[256]; snprintf(path, sizeof path, "overlay/itemctl_%llx.bin", a);
            if (FILE* f = fopen(path, "wb")) { fwrite(GPtr(a), 1, n, f); fclose(f); }
            OverlayLog("[itemctl] dumped 0x%llx +0x%llx\n", a, n);
        }
        pos = e + 1;
    }
}

// Research: HOOKSHOT_ITEMCTL_OBJ=<file>: each time the file's text changes ("<tag> <vt hex>[,<vt hex>]"), dump
// 0x1000 bytes of every 8-aligned object whose first word is one of the vtables to overlay/obj_<addr>_<tag>.bin.
static void DumpObjects(const char* spec) {
    char tag[64] = {}; char vts[256] = {};
    if (sscanf(spec, "%63s %255s", tag, vts) != 2) return;
    std::vector<uint64_t> want;
    for (char* q = vts; *q;) { char* e; uint64_t v = strtoull(q, &e, 16); if (e == q) break; want.push_back(v); q = (*e == ',') ? e + 1 : e; }
    if (g_Objs.empty()) {
        const uint64_t base = 0x100000000000ULL;
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
                    const uint64_t* p = (const uint64_t*)(w + (pg << 12));
                    for (int i = 0; i < 512; i++) for (uint64_t v : want) if (p[i] == v) g_Objs.push_back((uint64_t)(p + i) - base);
                }
            }
        }
        if (m) fclose(m);
    }
    for (uint64_t o : g_Objs) {
        char path[256]; snprintf(path, sizeof path, "overlay/obj_%llx_%s.bin", (unsigned long long)o, tag);
        if (FILE* f = fopen(path, "wb")) { fwrite(GPtr(o), 1, 0x1000, f); fclose(f); }
    }
    OverlayLog("[itemctl] objects %s: %zu dumped\n", tag, g_Objs.size());
    if (const char* rd = getenv("HOOKSHOT_ITEMCTL_REGION"); rd && !g_Objs.empty()) {   // +-range around the first object
        uint64_t range = strtoull(rd, nullptr, 16), lo = (g_Objs[0] - range) & ~0xFFFULL, hi = (g_Objs[0] + range) & ~0xFFFULL;
        char path[256]; snprintf(path, sizeof path, "re/runs/region_%llx_%s.bin", (unsigned long long)lo, tag);
        static std::vector<uint8_t> zero(4096, 0);
        if (FILE* f = fopen(path, "wb")) {
            for (uint64_t a = lo; a < hi; a += 4096) {
                unsigned char v;
                fwrite(mincore((void*)GPtr(a), 4096, &v) == 0 ? GPtr(a) : zero.data(), 1, 4096, f);
            }
            fclose(f);
        }
        OverlayLog("[itemctl] region 0x%llx..0x%llx dumped (%s)\n", (unsigned long long)lo, (unsigned long long)hi, tag);
    }
}

static void Monitor() {
    if (const char* of = getenv("HOOKSHOT_ITEMCTL_OBJ")) {
        std::string f = of;
        std::thread([f] {
            std::string last;
            for (;;) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                FILE* fp = fopen(f.c_str(), "r");
                if (!fp) continue;
                char b[512] = {}; fgets(b, sizeof b, fp); fclose(fp);
                if (!*b || last == b) continue;
                last = b;
                DumpObjects(b);
            }
        }).detach();
    }
    if (const char* d = getenv("HOOKSHOT_ITEMCTL_DUMP")) {
        std::string ds = d;
        std::thread([ds] { std::this_thread::sleep_for(std::chrono::seconds(60)); DumpRegions(ds.c_str()); }).detach();
    }
    if (const char* ff = getenv("HOOKSHOT_ITEMCTL_FIND")) {
        std::string f = ff;
        std::thread([f] {
            std::string last;
            for (;;) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                FILE* fp = fopen(f.c_str(), "r");
                if (!fp) continue;
                char b[512] = {}; fgets(b, sizeof b, fp); fclose(fp);
                std::string t = b;
                if (t.empty() || t == last) continue;
                last = t;
                { char md = (b[0] == '!' || b[0] == '~' || b[0] == '=' || b[0] == '*') ? b[0] : '=';
                  g_Want.clear();
                  for (char* q = b + (md == b[0]); *q;) { char* e; uint32_t v = (uint32_t)strtoul(q, &e, 16); if (e == q) break; g_Want.push_back(v); q = (*e == '|') ? e + 1 : e; }
                  FindValue(md, g_Want.empty() ? 0 : g_Want[0]); }
            }
        }).detach();
    }
    for (int tries = 0; !g_AddrChild && tries < 40; tries++) {
        std::this_thread::sleep_for(std::chrono::seconds(5));
        ScanOnce();
    }
    uint32_t lc = 0, la = 0;
    for (;;) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        uint64_t ac = g_AddrChild, aa = g_AddrAdult;
        uint32_t vc = ac ? GRead<uint32_t>(ac) : 0, va = aa ? GRead<uint32_t>(aa) : 0;
        if (vc != lc || va != la) {
            OverlayLog("[itemctl] AssignableItem child %08x adult %08x\n", vc, va);
            lc = vc; la = va;
        }
    }
}

// Research: f_80b17e20 = load-request constructor (x1 -> {const char* name, u32 len}); the pause menu calls it for
// the hovered item's preview model ("Item_<Name>"). Log the name and the guest frame-pointer chain.
static GuestFn o_LoadReq = nullptr;
static void LoadReqHook(GuestCtx* c) {
    uint64_t np = GRead<uint64_t>(c->x[1]);
    uint32_t len = GRead<uint32_t>(c->x[1] + 8);
    char name[96] = {};
    (void)len;
    if (np && np < (1ULL << 40)) for (int i = 0; i < 95; i++) { name[i] = (char)GRead<uint8_t>(np + i); if (!name[i]) break; }
    static const bool all = getenv("HOOKSHOT_ITEMCTL_LOADALL") != nullptr;
    if (all) OverlayLog("[itemctl] load %s\n", name);
    else if (!strncmp(name, "Item_", 5)) {
        char bt[512]; int n = snprintf(bt, sizeof bt, "lr %llx", (unsigned long long)c->x[30]);
        uint64_t fp = c->x[29];
        for (int i = 0; i < 10 && fp > 0x1000 && fp < (1ULL << 40); i++) {
            uint64_t ra = GRead<uint64_t>(fp + 8) & 0xFFFFFFFFFFULL;
            n += snprintf(bt + n, sizeof bt - n, " %llx", (unsigned long long)ra);
            fp = GRead<uint64_t>(fp);
        }
        OverlayLog("[itemctl] load %s | %s | x19..x28:", name, bt);
        for (int r = 19; r <= 28; r++) OverlayLog(" %llx", (unsigned long long)c->x[r]);
        OverlayLog("\n");
    }
    o_LoadReq(c);
}

// Research: which per-frame base-screen functions get the pause screen (vtable 0x84c0baa8) in x0
template <int I> struct ProbeFn { static GuestFn orig; static std::atomic<int> hits, other;
    static void Hook(GuestCtx* c) {
        uint64_t vt = c->x[0] && c->x[0] < (1ULL << 40) ? GRead<uint64_t>(c->x[0]) : 0;
        if (vt == 0x84c0baa8) hits++; else other++;
        orig(c);
    } };
template <int I> GuestFn ProbeFn<I>::orig = nullptr;
template <int I> std::atomic<int> ProbeFn<I>::hits{0};
template <int I> std::atomic<int> ProbeFn<I>::other{0};

static void ResearchStart() {
    if (!g_Dbg) return;
    std::thread(Monitor).detach();
    if (getenv("HOOKSHOT_ITEMCTL_LOADTRACE")) InstallGuestHooks({{0x80b17e20, LoadReqHook, &o_LoadReq, "itemctl load request"}});
    if (getenv("HOOKSHOT_ITEMCTL_PROBE")) {
        InstallGuestHooks({{0x8379bc90, ProbeFn<0>::Hook, &ProbeFn<0>::orig, "probe 8379bc90"},
                           {0x80789d70, ProbeFn<1>::Hook, &ProbeFn<1>::orig, "probe 80789d70"},
                           {0x802f1380, ProbeFn<2>::Hook, &ProbeFn<2>::orig, "probe 802f1380"},
                           {0x802f6990, ProbeFn<3>::Hook, &ProbeFn<3>::orig, "probe 802f6990"},
                           {0x83be6d00, ProbeFn<4>::Hook, &ProbeFn<4>::orig, "probe 83be6d00"}});
        std::thread([] { for (;;) { std::this_thread::sleep_for(std::chrono::milliseconds(500));
            OverlayLog("[probe] %d/%d %d/%d %d/%d %d/%d %d/%d\n", ProbeFn<0>::hits.exchange(0), ProbeFn<0>::other.exchange(0),
                ProbeFn<1>::hits.exchange(0), ProbeFn<1>::other.exchange(0), ProbeFn<2>::hits.exchange(0), ProbeFn<2>::other.exchange(0),
                ProbeFn<3>::hits.exchange(0), ProbeFn<3>::other.exchange(0), ProbeFn<4>::hits.exchange(0), ProbeFn<4>::other.exchange(0)); } }).detach();
    }
}
