// -------------------------------------------------------------
// Param Tuner (F2 > "Param Tuner")
// Finds Child Link's PlayerGameParam floats in guest memory and lets you edit them live.
//
// How it works: the game loads Player_Child's PlayerGameParam into memory. We know every float
// in it (player_param_keys.h, generated from romfs and the GoronLink mod), so we scan the guest
// heap for 4-byte-aligned spots where many of those values sit close together. Values whose
// preceding byte is 0xD2 (the BYML float type tag) are raw BYML file data rather than the parsed
// struct; both kinds are shown so we can learn which one the game actually reads.
// -------------------------------------------------------------
#include <atomic>
#include <thread>
#include <unordered_map>
#include "player_param_keys.h"

namespace tuner {

static const uint64_t kHeapGuest = 0x1000000000ULL;   // "heap: 6894 MB at 0x1000000000"
static const uint64_t kHeapSize = 6894ULL << 20;
static const int kWindow = 0x2000;                    // bytes either side of an anchor

struct Candidate {
    uint64_t anchor;      // guest address of the anchor float
    int vanilla_hits;     // distinct vanilla child values found nearby
    int mod_hits;         // distinct GoronLink-mod values found nearby
    bool raw_byml;        // anchor looks like BYML file data
};

struct Row {
    int rel;                      // offset from anchor
    float original;               // value when the block was selected
    std::vector<int> keys;        // indices into kParamKeys that match this value
};

static std::atomic<int> g_State{0};   // 0 idle, 1 scanning, 2 done
static std::atomic<int> g_ProgressPct{0};
static std::mutex g_Mutex;
static std::vector<Candidate> g_Cands;
static int g_Selected = -1;
static std::vector<Row> g_Rows;
static bool g_ShowAll = false;
static char g_Filter[64] = "";
static std::string g_Msg = "Load a CHILD save, then press Scan.";

static bool PageResident(uintptr_t host, size_t len, std::vector<unsigned char>& vec) {
    size_t pages = len / 4096;
    vec.resize(pages);
    return mincore((void*)host, len, vec.data()) == 0;
}

// float bit pattern -> key indices, for vanilla [0] and mod [1] values
static std::unordered_map<uint32_t, std::vector<int>> g_ValueIndex[2];

static void BuildIndex() {
    for (int m = 0; m < 2; ++m) {
        g_ValueIndex[m].clear();
        for (int k = 0; k < kParamKeyCount; ++k) {
            float v = m ? kParamKeys[k].mod_value : kParamKeys[k].child_value;
            if (v == 0.0f || v == 1.0f) continue;   // too common to mean anything
            uint32_t bits;
            memcpy(&bits, &v, 4);
            g_ValueIndex[m][bits].push_back(k);
        }
    }
}

static int CountDistinct(const uint8_t* base, size_t lo, size_t hi, int which) {
    // number of distinct keys whose value appears in [lo, hi)
    char seen[kParamKeyCount];
    memset(seen, 0, sizeof(seen));
    int n = 0;
    const auto& idx = g_ValueIndex[which];
    for (size_t o = lo; o + 4 <= hi; o += 4) {
        uint32_t bits;
        memcpy(&bits, base + o, 4);
        auto it = idx.find(bits);
        if (it == idx.end()) continue;
        for (int k : it->second) if (!seen[k]) { seen[k] = 1; ++n; }
    }
    return n;
}

static void ScanThread() {
    BuildIndex();
    std::vector<Candidate> found;
    float anchors[2] = {5.625f, 9.0f};   // RollingMaxSpeed: vanilla child, GoronLink mod (exact values below)
    for (int k = 0; k < kParamKeyCount; ++k)
        if (!strcmp(kParamKeys[k].name, "RollingMaxSpeed")) {
            anchors[0] = kParamKeys[k].child_value;
            anchors[1] = kParamKeys[k].mod_value;
        }
    const size_t chunk = 64ULL << 20;
    std::vector<unsigned char> res;
    // every readable mapping inside the guest window (the heap and the resource region at 0x7f..)
    std::vector<std::pair<uint64_t, uint64_t>> regions;   // guest [a, b)
    if (FILE* m = fopen("/proc/self/maps", "r")) {
        char line[512];
        while (fgets(line, sizeof line, m)) {
            unsigned long a, b; char perms[8];
            if (sscanf(line, "%lx-%lx %7s", &a, &b, perms) != 3 || perms[0] != 'r') continue;
            if (a < GUEST_RAM_BASE || a >= GUEST_RAM_BASE + (1ULL << 40)) continue;
            regions.push_back({a - GUEST_RAM_BASE, b - GUEST_RAM_BASE});
        }
        fclose(m);
    }
    if (regions.empty()) regions.push_back({kHeapGuest, kHeapGuest + kHeapSize});
    uint64_t total = 0, done = 0;
    for (auto& r : regions) total += r.second - r.first;
    for (auto& r : regions)
    for (uint64_t g = r.first; g < r.second; g += chunk, done += chunk) {
        g_ProgressPct = (int)(100.0 * done / (double)total);
        uintptr_t host = GUEST_RAM_BASE + g;
        size_t clen = std::min<uint64_t>(chunk, r.second - g);
        if (!PageResident(host, clen, res)) continue;
        for (size_t p = 0; p < res.size(); ++p) {
            if (!(res[p] & 1)) continue;
            const uint8_t* page = (const uint8_t*)(host + p * 4096);
            for (int o = 0; o < 4096; o += 4) {
                float f;
                memcpy(&f, page + o, 4);
                if (f != anchors[0] && f != anchors[1]) continue;
                uint64_t ga = g + p * 4096 + o;
                size_t a_off = p * 4096 + o;
                size_t lo = a_off > (size_t)kWindow ? a_off - kWindow : 0;
                size_t hi = std::min(clen, a_off + kWindow);
                size_t lp = lo / 4096, hp = (hi + 4095) / 4096;
                bool ok = true;
                for (size_t q = lp; q < hp && q < res.size(); ++q) if (!(res[q] & 1)) { ok = false; break; }
                if (!ok) continue;
                const uint8_t* cb = (const uint8_t*)host;
                int v = CountDistinct(cb, lo, hi, 0);
                int m = CountDistinct(cb, lo, hi, 1);
                if (std::max(v, m) < 12) continue;
                bool byml = (o >= 1) ? (page[o - 1] == 0xD2) : false;
                found.push_back({ga, v, m, byml});
            }
        }
    }
    std::sort(found.begin(), found.end(), [](const Candidate& a, const Candidate& b) {
        if (a.mod_hits != b.mod_hits) return a.mod_hits > b.mod_hits;
        return a.vanilla_hits > b.vanilla_hits;
    });
    if (found.size() > 40) found.resize(40);
    {
        std::lock_guard<std::mutex> lk(g_Mutex);
        g_Cands = found;
        g_Selected = -1;
        g_Rows.clear();
    }
    OverlayLog("[tuner] scan done: %zu candidates\n", found.size());
    for (auto& c : found)
        OverlayLog("[tuner]   anchor=0x%llx vanilla=%d mod=%d %s\n", (unsigned long long)c.anchor,
                   c.vanilla_hits, c.mod_hits, c.raw_byml ? "raw-byml" : "struct?");
    g_Msg = found.empty() ? "No candidates. Are you in a CHILD save with the player loaded?"
                          : "Scan done. Pick a candidate (highest hits first).";
    g_ProgressPct = 100;
    g_State = 2;
}

static void Select(int idx) {
    std::lock_guard<std::mutex> lk(g_Mutex);
    if (idx < 0 || idx >= (int)g_Cands.size()) return;
    g_Selected = idx;
    g_Rows.clear();
    const Candidate& c = g_Cands[idx];
    for (int rel = -kWindow; rel < kWindow; rel += 4) {
        float f;
        memcpy(&f, (const void*)(GUEST_RAM_BASE + c.anchor + rel), 4);
        Row r{rel, f, {}};
        for (int k = 0; k < kParamKeyCount; ++k)
            if (f == kParamKeys[k].child_value || f == kParamKeys[k].mod_value) r.keys.push_back(k);
        g_Rows.push_back(r);
    }
}

static float* Ptr(const Candidate& c, int rel) { return (float*)(GUEST_RAM_BASE + c.anchor + rel); }

static void SaveFile(bool dump_raw) {
    std::lock_guard<std::mutex> lk(g_Mutex);
    if (g_Selected < 0) return;
    const Candidate& c = g_Cands[g_Selected];
    char path[256];
    if (dump_raw) {
        snprintf(path, sizeof(path), "overlay/param_dump_%llx.bin", (unsigned long long)c.anchor);
        FILE* f = fopen(path, "wb");
        if (!f) { g_Msg = "Could not write dump."; return; }
        fwrite((const void*)(GUEST_RAM_BASE + c.anchor - kWindow), 1, 2 * kWindow, f);
        fclose(f);
    } else {
        snprintf(path, sizeof(path), "overlay/param_tweaks.txt");
        FILE* f = fopen(path, "a");
        if (!f) { g_Msg = "Could not write tweaks."; return; }
        fprintf(f, "# anchor 0x%llx (%s)\n", (unsigned long long)c.anchor, c.raw_byml ? "raw-byml" : "struct?");
        for (auto& r : g_Rows) {
            float now = *Ptr(c, r.rel);
            if (now == r.original) continue;
            fprintf(f, "%c%04x  %g -> %g  ", r.rel < 0 ? '-' : '+', r.rel < 0 ? -r.rel : r.rel, r.original, now);
            for (int k : r.keys) fprintf(f, "%s ", kParamKeys[k].name);
            fprintf(f, "\n");
        }
        fclose(f);
    }
    g_Msg = std::string("Saved ") + path;
}

static void DrawTab() {
    ImGui::TextWrapped("Finds Link's movement settings in memory so you can edit them live. "
                       "Use a CHILD save. Edits last until the area reloads.");
    int st = g_State.load();
    if (st == 1) {
        ImGui::Text("Scanning... %d%%", g_ProgressPct.load());
    } else if (ImGui::Button("Scan memory")) {
        g_State = 1;
        g_Msg = "Scanning...";
        std::thread(ScanThread).detach();
    }
    ImGui::SameLine();
    ImGui::TextDisabled("(takes a few seconds)");
    ImGui::TextColored(ImVec4(0.9f, 0.85f, 0.4f, 1), "%s", g_Msg.c_str());

    std::lock_guard<std::mutex> lk(g_Mutex);
    if (!g_Cands.empty() && ImGui::BeginListBox("##cands", ImVec2(-1, 110))) {
        for (int i = 0; i < (int)g_Cands.size(); ++i) {
            const Candidate& c = g_Cands[i];
            char label[160];
            snprintf(label, sizeof(label), "0x%llx  vanilla %d / mod %d  %s",
                     (unsigned long long)c.anchor, c.vanilla_hits, c.mod_hits,
                     c.raw_byml ? "[raw file data]" : "[loaded struct?]");
            if (ImGui::Selectable(label, g_Selected == i)) {
                g_Mutex.unlock(); Select(i); g_Mutex.lock();
            }
        }
        ImGui::EndListBox();
    }
    if (g_Selected < 0 || g_Selected >= (int)g_Cands.size()) return;
    const Candidate& c = g_Cands[g_Selected];
    ImGui::Text("Block 0x%llx: %s values loaded", (unsigned long long)c.anchor,
                c.mod_hits > c.vanilla_hits ? "GoronLink MOD" : "VANILLA");
    ImGui::Checkbox("Show unnamed values", &g_ShowAll);
    ImGui::SameLine();
    ImGui::SetNextItemWidth(160);
    ImGui::InputTextWithHint("##filter", "filter names", g_Filter, sizeof(g_Filter));
    ImGui::SameLine();
    bool want_save = ImGui::Button("Save changed");
    ImGui::SameLine();
    bool want_dump = ImGui::Button("Dump block");
    ImGui::SameLine();
    if (ImGui::Button("Reset all")) for (auto& r : g_Rows) *Ptr(c, r.rel) = r.original;

    if (ImGui::BeginTable("##rows", 3, ImGuiTableFlags_ScrollY | ImGuiTableFlags_RowBg | ImGuiTableFlags_Resizable,
                          ImVec2(-1, 300))) {
        ImGui::TableSetupColumn("Offset", ImGuiTableColumnFlags_WidthFixed, 60);
        ImGui::TableSetupColumn("Value", ImGuiTableColumnFlags_WidthFixed, 150);
        ImGui::TableSetupColumn("Possible setting names");
        ImGui::TableHeadersRow();
        for (auto& r : g_Rows) {
            if (!g_ShowAll && r.keys.empty()) continue;
            if (g_Filter[0]) {
                bool any = false;
                for (int k : r.keys) if (strcasestr(kParamKeys[k].name, g_Filter)) { any = true; break; }
                if (!any) continue;
            }
            float* p = Ptr(c, r.rel);
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::Text("%c%04x", r.rel < 0 ? '-' : '+', r.rel < 0 ? -r.rel : r.rel);
            ImGui::TableNextColumn();
            ImGui::PushID(r.rel);
            ImGui::SetNextItemWidth(-1);
            float v = *p;
            if (ImGui::InputFloat("##v", &v, 0.1f, 1.0f, "%.4f")) *p = v;
            if (*p != r.original) {
                ImGui::SameLine();
                if (ImGui::SmallButton("x")) *p = r.original;
            }
            ImGui::PopID();
            ImGui::TableNextColumn();
            std::string names;
            for (size_t i = 0; i < r.keys.size() && i < 6; ++i) {
                if (i) names += ", ";
                names += kParamKeys[r.keys[i]].name;
            }
            if (r.keys.size() > 6) names += ", ...";
            ImGui::TextUnformatted(names.empty() ? "-" : names.c_str());
        }
        ImGui::EndTable();
    }
    if (want_save || want_dump) {
        g_Mutex.unlock();
        SaveFile(want_dump);
        g_Mutex.lock();
    }
}



// find <SettingName|0xBITS> [more...]: search EVERY guest mapping (from /proc/self/maps) for the exact
// mod value of each named setting (or raw float bits), logging up to 64 hits each with the byte before it.
static bool g_FindCtx = false;
static void FindEverywhere(std::vector<uint32_t> want, std::vector<std::string> labels) {
    std::vector<std::pair<uintptr_t, uintptr_t>> regions;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    while (m && fgets(line, sizeof line, m)) {
        unsigned long a, b;
        char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &a, &b, perms) != 3) continue;
        if (perms[0] != 'r') continue;
        if (a < GUEST_RAM_BASE || a >= GUEST_RAM_BASE + (1ULL << 40)) continue;
        regions.push_back({a, b});
    }
    if (m) fclose(m);
    uint64_t total = 0;
    for (auto& r : regions) total += r.second - r.first;
    OverlayLog("[tuner] find: %zu guest regions, %llu MB\n", regions.size(), (unsigned long long)(total >> 20));
    std::vector<int> count(want.size(), 0);
    std::vector<unsigned char> res;
    for (auto& r : regions) {
        for (uintptr_t c = r.first; c < r.second; c += (64ULL << 20)) {
            size_t len = std::min<uintptr_t>(64ULL << 20, r.second - c);
            if (!PageResident(c, len, res)) continue;
            for (size_t p = 0; p < res.size(); ++p) {
                if (!(res[p] & 1)) continue;
                const uint32_t* w = (const uint32_t*)(c + p * 4096);
                for (int i = 0; i < 1024; ++i)
                    for (size_t k = 0; k < want.size(); ++k)
                        if (w[i] == want[k] && count[k] < 64) {
                            ++count[k];
                            uint64_t g = (uint64_t)(c + p * 4096 + i * 4) - GUEST_RAM_BASE;
                            uint8_t before = i ? ((const uint8_t*)&w[i])[-1] : 0;
                            OverlayLog("[tuner] find %s @0x%llx (prev byte %02x)\n", labels[k].c_str(),
                                       (unsigned long long)g, before);
                            if (g_FindCtx && i >= 8 && i < 1024 - 8) {          // context: 16 words around the hit
                                char ln[400]; int o = 0;
                                for (int j = -8; j < 8; ++j) o += snprintf(ln + o, sizeof ln - o, " %08x", w[i + j]);
                                OverlayLog("[tuner]   ctx%s\n", ln);
                            }
                        }
            }
        }
    }
    for (size_t k = 0; k < want.size(); ++k) OverlayLog("[tuner] find %s: %d hits\n", labels[k].c_str(), count[k]);
}

// snap <path>: write every resident, writable guest page as (u64 guest address, 4096 bytes) records, for
// offline memory diffs (e.g. finding the magic meter: snapshot, spend magic, snapshot again).
static void SnapGuest(const char* path) {
    FILE* out = fopen(path, "wb");
    if (!out) { OverlayLog("[tuner] snap: cannot write %s\n", path); return; }
    std::vector<std::pair<uintptr_t, uintptr_t>> regions;
    FILE* m = fopen("/proc/self/maps", "r");
    char line[512];
    while (m && fgets(line, sizeof line, m)) {
        unsigned long a, b; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &a, &b, perms) != 3) continue;
        if (perms[0] != 'r' || perms[1] != 'w') continue;
        if (a < GUEST_RAM_BASE || a >= GUEST_RAM_BASE + (1ULL << 40)) continue;
        regions.push_back({a, b});
    }
    if (m) fclose(m);
    std::vector<unsigned char> res;
    uint64_t pages = 0;
    for (auto& r : regions)
        for (uintptr_t c = r.first; c < r.second; c += (64ULL << 20)) {
            size_t len = std::min<uintptr_t>(64ULL << 20, r.second - c);
            if (!PageResident(c, len, res)) continue;
            for (size_t p = 0; p < res.size(); ++p) {
                if (!(res[p] & 1)) continue;
                uint64_t g = (uint64_t)(c + p * 4096) - GUEST_RAM_BASE;
                fwrite(&g, 8, 1, out); fwrite((const void*)(c + p * 4096), 4096, 1, out); ++pages;
            }
        }
    fclose(out);
    OverlayLog("[tuner] snap %s: %llu pages\n", path, (unsigned long long)pages);
}

// ---- Headless control: HOOKSHOT_TUNER_CMD=<file>. Lines appended to the file are run in order:
//   scan                      scan memory (as the button)
//   select <n>                pick candidate n (0 = best)
//   set <SettingName> <value> write <value> to every row in the selected block that may be <SettingName>
//   dump                      write the selected block to overlay/param_dump_<addr>.bin
// Results go to overlay.log with a [tuner] prefix.
static size_t g_CmdPos = 0;
static void RunCommand(const char* line) {
    char cmd[32] = "", name[96] = "";
    float val = 0;
    int n = sscanf(line, "%31s %95s %f", cmd, name, &val);
    if (n < 1) return;
    OverlayLog("[tuner] cmd: %s", line);
    if (!strcmp(cmd, "watch")) {                      // watch <hex> [hex..]: arm HOS_WATCH data breakpoints now
        if (const char* w = getenv("HOS_WATCH")) if (FILE* f = fopen(w, "w")) { fprintf(f, "%s\n", line + 6); fclose(f); }
        OverlayLog("[tuner] watch armed: %s", line + 6);
    } else if (!strcmp(cmd, "peek")) {                       // peek <hex guest addr>
        unsigned long long ga = 0;
        if (sscanf(line, "%*s %llx", &ga) == 1) {
            uint32_t v; memcpy(&v, (void*)(GUEST_RAM_BASE + ga), 4);
            OverlayLog("[tuner] peek 0x%llx = %u\n", ga, v);
        }
    } else if (!strcmp(cmd, "poke")) {                       // poke <hex guest addr> <u32 value>
        unsigned long long ga = 0; unsigned v = 0;
        if (sscanf(line, "%*s %llx %u", &ga, &v) == 2) {
            uint32_t old; memcpy(&old, (void*)(GUEST_RAM_BASE + ga), 4);
            memcpy((void*)(GUEST_RAM_BASE + ga), &v, 4);
            OverlayLog("[tuner] poke 0x%llx: %u -> %u\n", ga, old, v);
        }
    } else if (!strcmp(cmd, "snap") && n >= 2) {
        SnapGuest(name);
    } else if (!strcmp(cmd, "scan")) {
        if (g_State != 1) { g_State = 1; std::thread(ScanThread).detach(); }
    } else if (!strcmp(cmd, "at") && n >= 2) {
        uint64_t ga = strtoull(name, nullptr, 16);
        {
            std::lock_guard<std::mutex> lk(g_Mutex);
            g_Cands.insert(g_Cands.begin(), Candidate{ga, 0, 0, false});
        }
        Select(0);
        OverlayLog("[tuner] at 0x%llx selected\n", (unsigned long long)ga);
    } else if (!strcmp(cmd, "select")) {
        Select(n >= 2 ? atoi(name) : 0);
        OverlayLog("[tuner] selected %s\n", g_Selected >= 0 ? "ok" : "nothing (scan first)");
    } else if (!strcmp(cmd, "find") || !strcmp(cmd, "findctx")) {
        g_FindCtx = !strcmp(cmd, "findctx");
        std::vector<uint32_t> want;
        std::vector<std::string> labels;
        char buf[256];
        strncpy(buf, line + (g_FindCtx ? 7 : 4), sizeof buf - 1);
        buf[sizeof buf - 1] = 0;
        for (char* t = strtok(buf, " \t\r\n"); t; t = strtok(nullptr, " \t\r\n")) {
            if (!strncmp(t, "0x", 2)) { want.push_back((uint32_t)strtoul(t, nullptr, 16)); labels.push_back(t); continue; }
            for (int k = 0; k < kParamKeyCount; ++k)
                if (!strcmp(kParamKeys[k].name, t)) {
                    uint32_t b; memcpy(&b, &kParamKeys[k].mod_value, 4);
                    want.push_back(b); labels.push_back(std::string(t) + "(mod)");
                    memcpy(&b, &kParamKeys[k].child_value, 4);
                    want.push_back(b); labels.push_back(std::string(t) + "(vanilla)");
                }
        }
        std::thread(FindEverywhere, want, labels).detach();
    } else if (!strcmp(cmd, "dump")) {
        SaveFile(true);
        OverlayLog("[tuner] %s\n", g_Msg.c_str());
    } else if (!strcmp(cmd, "set") && n == 3) {
        std::lock_guard<std::mutex> lk(g_Mutex);
        if (g_Selected < 0) { OverlayLog("[tuner] set: no block selected\n"); return; }
        const Candidate& c = g_Cands[g_Selected];
        int hits = 0;
        // Exact tagged (mod) value first: a probe build makes it unique. Only if nothing holds it,
        // fall back to the vanilla value, and only when exactly one slot holds that.
        int key = -1;
        for (int k = 0; k < kParamKeyCount; ++k) if (!strcmp(kParamKeys[k].name, name)) key = k;
        if (key < 0) { OverlayLog("[tuner] set: unknown setting %s\n", name); return; }
        std::vector<const Row*> exact, loose;
        for (auto& r : g_Rows) {
            float now = *Ptr(c, r.rel);
            if (now == kParamKeys[key].mod_value && kParamKeys[key].mod_value != kParamKeys[key].child_value) exact.push_back(&r);
            else if (now == kParamKeys[key].child_value) loose.push_back(&r);
        }
        // the parsed struct is the slot outside raw BYML data (byte before is not the 0xD2 float tag)
        std::vector<const Row*> pick;
        for (auto* r : exact) {
            const uint8_t* b = (const uint8_t*)Ptr(c, r->rel);
            if (b[-1] != 0xD2) pick.push_back(r);
        }
        if (pick.empty() && loose.size() == 1) pick = loose;
        for (auto* r : pick) {
            float* p = Ptr(c, r->rel);
            OverlayLog("[tuner] set %s @%c0x%x: %g -> %g\n", name, r->rel < 0 ? '-' : '+',
                       r->rel < 0 ? -r->rel : r->rel, *p, val);
            *p = val;
            ++hits;
        }
        if (!hits && !loose.empty())
            OverlayLog("[tuner] set: %s is ambiguous (%zu slots hold its vanilla value); use a --probe build\n", name, loose.size());
        if (!hits) OverlayLog("[tuner] set: %s not found in block\n", name);
    }
}

static void PollCommandsOnce(const char* path) {
    FILE* f = fopen(path, "r");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long end = ftell(f);
    if ((size_t)end > g_CmdPos) {
        fseek(f, (long)g_CmdPos, SEEK_SET);
        char line[256];
        while (fgets(line, sizeof line, f)) {
            if (!strchr(line, '\n')) break;   // (incomplete line: wait for the rest)
            g_CmdPos += strlen(line);
            RunCommand(line);
        }
    }
    fclose(f);
}

// Commands only read/write guest memory, so they run on a thread of their own: this works headless
// and does not depend on the present hook being installed.
static void PollCommands() {}   // (kept for the frame hook's call; the thread does the work)
struct CommandThreadStarter {
    CommandThreadStarter() {
        const char* path = getenv("HOOKSHOT_TUNER_CMD");
        if (!path || !*path) return;
        // LD_PRELOAD also loads us into helper processes (timeout, sh): only the game polls.
        if (!program_invocation_short_name || strcmp(program_invocation_short_name, "hookshot") != 0) return;
        std::thread([path] {
            for (;;) {
                PollCommandsOnce(path);
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
        }).detach();
    }
};
static CommandThreadStarter g_CommandThreadStarter;

}  // namespace tuner
