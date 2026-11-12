// -------------------------------------------------------------
// Guest function hooks (included by hookshot_overlay.cpp)
// -------------------------------------------------------------
// Hookshot lifts every guest (AArch64) function to a host function `void f_<guestaddr>(Ctx *c)`; guest registers live
// in Ctx (a64-kit rt/a64.h GuestContext) and guest memory at host 0x100000000000 + address. bin/hookshot keeps all
// f_ symbols in its static symbol table (not exported), so they are looked up in /proc/self/exe's .symtab.
//
// hook_guest(addr, fn) patches the host function's entry with `jmp [rip+0]; dq fn` (14 bytes, no register touched) and
// returns a trampoline that runs the original: the copied whole prologue instructions, then a jump back. Only
// instructions the small decoder below understands are copied (pushes, register moves, immediates, ModRM forms that
// are not RIP-relative); anything else and the hook is refused (logged), never guessed.
//
// HOOKSHOT_GUEST_TRACE="80953e80,806d9a30,..." hooks each listed function with a counter and logs, every 250 ms,
// how many times each ran (only lines with calls), with the first call's x0. Used to find which methods run when.

#include <elf.h>
#include <link.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <atomic>
#include <string>
#include <vector>
#include <unordered_map>
#include <thread>
#include <chrono>

struct GuestCtx {                 // a64-kit rt/a64.h GuestContext
    uint64_t x[32];               // x[31] = SP
    uint64_t pc;
    uint8_t n, z, c, v;
    int halted;
    uint64_t vreg[32][2];         // Q0-Q31
    uint64_t tpidr_el0, tpidrro_el0, fpcr, fpsr;
    uint64_t excl_addr, excl_lo, excl_hi;
    uint32_t excl_size;
    int excl_valid;
    void* thread;
};
typedef void (*GuestFn)(GuestCtx*);

static inline uint8_t* GPtr(uint64_t a) { return (uint8_t*)(0x100000000000ULL + a); }
template <class T> static inline T GRead(uint64_t a) { T v; memcpy(&v, GPtr(a), sizeof(T)); return v; }
template <class T> static inline void GWrite(uint64_t a, T v) { memcpy(GPtr(a), &v, sizeof(T)); }
static inline float VregF(GuestCtx* c, int i) { float f; memcpy(&f, &c->vreg[i][0], 4); return f; }
static inline void SetVregF(GuestCtx* c, int i, float f) { c->vreg[i][0] = 0; c->vreg[i][1] = 0; memcpy(&c->vreg[i][0], &f, 4); }

// ---- symbols ------------------------------------------------------------------------------------------------
static uintptr_t g_ExeBase = 0;

static int FindExeBase(struct dl_phdr_info* info, size_t, void*) {
    if (g_ExeBase == 0) g_ExeBase = info->dlpi_addr;      // first entry is the main program
    return 1;
}

// Look up f_<addr> for every requested guest address. Returns {guest addr: host address}.
static std::unordered_map<uint32_t, uintptr_t> ResolveGuestFns(const std::vector<uint32_t>& want) {
    std::unordered_map<uint32_t, uintptr_t> out;
    if (!g_ExeBase) dl_iterate_phdr(FindExeBase, nullptr);
    int fd = open("/proc/self/exe", O_RDONLY);
    if (fd < 0) return out;
    Elf64_Ehdr eh;
    if (pread(fd, &eh, sizeof eh, 0) != (ssize_t)sizeof eh) { close(fd); return out; }
    std::vector<Elf64_Shdr> sh(eh.e_shnum);
    pread(fd, sh.data(), sizeof(Elf64_Shdr) * eh.e_shnum, eh.e_shoff);
    std::unordered_map<std::string, uint32_t> names;
    char buf[16];
    for (uint32_t a : want) { snprintf(buf, sizeof buf, "f_%08x", a); names[buf] = a; }
    for (auto& s : sh) {
        if (s.sh_type != SHT_SYMTAB) continue;
        const Elf64_Shdr& strs = sh[s.sh_link];
        std::vector<char> str(strs.sh_size);
        pread(fd, str.data(), strs.sh_size, strs.sh_offset);
        size_t n = s.sh_size / sizeof(Elf64_Sym);
        const size_t chunk = 65536;
        std::vector<Elf64_Sym> syms(chunk);
        for (size_t i = 0; i < n; i += chunk) {
            size_t k = std::min(chunk, n - i);
            pread(fd, syms.data(), k * sizeof(Elf64_Sym), s.sh_offset + i * sizeof(Elf64_Sym));
            for (size_t j = 0; j < k; j++) {
                const Elf64_Sym& y = syms[j];
                if (ELF64_ST_TYPE(y.st_info) != STT_FUNC || y.st_name >= str.size()) continue;
                const char* nm = &str[y.st_name];
                if (nm[0] != 'f' || nm[1] != '_') continue;
                auto it = names.find(nm);
                if (it != names.end()) out[it->second] = g_ExeBase + y.st_value;
            }
        }
    }
    close(fd);
    return out;
}

// ---- x86-64 prologue length decoder (subset) -----------------------------------------------------------------
static int X86InsnLen(const uint8_t* p) {
    int i = 0;
    while (p[i] == 0x66 || p[i] == 0xF2 || p[i] == 0xF3) i++;          // operand-size / SSE prefixes
    uint8_t rex = 0;
    if ((p[i] & 0xF0) == 0x40) rex = p[i++];
    uint8_t op = p[i++];
    auto modrm = [&](int imm) -> int {
        uint8_t m = p[i++];
        int mod = m >> 6, rm = m & 7;
        if (mod != 3 && rm == 4) {                                      // SIB
            uint8_t sib = p[i++];
            if (mod == 0 && (sib & 7) == 5) i += 4;
        } else if (mod == 0 && rm == 5) {
            return -1;                                                  // RIP-relative: not relocatable here
        }
        if (mod == 1) i += 1;
        else if (mod == 2) i += 4;
        return i + imm;
    };
    if (op >= 0x50 && op <= 0x5F) return i;                             // push / pop r64
    if (op >= 0xB8 && op <= 0xBF) return i + ((rex & 8) ? 8 : 4);       // mov r, imm32/imm64
    switch (op) {
    case 0x01: case 0x03: case 0x09: case 0x0B: case 0x21: case 0x23: case 0x29: case 0x2B:
    case 0x31: case 0x33: case 0x39: case 0x3B: case 0x85: case 0x89: case 0x8B: case 0x8D:
        return modrm(0);
    case 0x83: case 0xC6: case 0x80: return modrm(1);
    case 0x81: case 0xC7: return modrm(4);
    case 0x0F: {
        uint8_t op2 = p[i++];
        if (op2 == 0x1F || op2 == 0x10 || op2 == 0x11 || op2 == 0x28 || op2 == 0x29 || op2 == 0x57 ||
            op2 == 0xB6 || op2 == 0xB7 || op2 == 0xBE || op2 == 0xBF || op2 == 0xD6 || op2 == 0x7E || op2 == 0x6E)
            return modrm(0);
        return -1;
    }
    default: return -1;
    }
}

// Host (non-lifted) functions, e.g. Hookshot's HLE imports "imp__ZN2nn3hid13GetNpadStates...": same Ctx convention.
static uintptr_t ResolveHostSymbol(const char* name) {
    if (!g_ExeBase) dl_iterate_phdr(FindExeBase, nullptr);
    int fd = open("/proc/self/exe", O_RDONLY);
    if (fd < 0) return 0;
    Elf64_Ehdr eh;
    pread(fd, &eh, sizeof eh, 0);
    std::vector<Elf64_Shdr> sh(eh.e_shnum);
    pread(fd, sh.data(), sizeof(Elf64_Shdr) * eh.e_shnum, eh.e_shoff);
    uintptr_t found = 0;
    size_t want = strlen(name);
    for (auto& s : sh) {
        if (s.sh_type != SHT_SYMTAB || found) continue;
        const Elf64_Shdr& strs = sh[s.sh_link];
        std::vector<char> str(strs.sh_size);
        pread(fd, str.data(), strs.sh_size, strs.sh_offset);
        size_t n = s.sh_size / sizeof(Elf64_Sym);
        std::vector<Elf64_Sym> syms(65536);
        for (size_t i = 0; i < n && !found; i += 65536) {
            size_t k = std::min<size_t>(65536, n - i);
            pread(fd, syms.data(), k * sizeof(Elf64_Sym), s.sh_offset + i * sizeof(Elf64_Sym));
            for (size_t j = 0; j < k; j++)
                if (syms[j].st_name < str.size() && !strncmp(&str[syms[j].st_name], name, want + 1)) {
                    found = g_ExeBase + syms[j].st_value; break;
                }
        }
    }
    close(fd);
    return found;
}

// ---- hooks --------------------------------------------------------------------------------------------------
static uint8_t* g_TrampPool = nullptr;
static size_t g_TrampUsed = 0;

static void WriteAbsJmp(uint8_t* at, uintptr_t target) {
    at[0] = 0xFF; at[1] = 0x25; at[2] = at[3] = at[4] = at[5] = 0;      // jmp [rip+0]
    memcpy(at + 6, &target, 8);
}

// Patch host function `host` to jump to `fn`; returns the trampoline to the original (or nullptr, logged).
static GuestFn HookHost(uintptr_t host, GuestFn fn, const char* what) {
    uint8_t* p = (uint8_t*)host;
    // Already hooked by another feature (our `jmp [rip+0]; dq target`): chain. The new hook takes the entry and its
    // "original" is the previous hook, so independent mods can hook the same function in any order.
    if (p[0] == 0xFF && p[1] == 0x25 && !p[2] && !p[3] && !p[4] && !p[5]) {
        uint64_t prev; memcpy(&prev, p + 6, 8);
        if (!g_TrampPool) {
            g_TrampPool = (uint8_t*)mmap(nullptr, 1 << 16, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (g_TrampPool == MAP_FAILED) { g_TrampPool = nullptr; return nullptr; }
        }
        if (g_TrampUsed + 16 > (1 << 16)) return nullptr;
        uint8_t* t = g_TrampPool + g_TrampUsed;
        WriteAbsJmp(t, prev);
        g_TrampUsed += 16;
        uintptr_t page = host & ~(uintptr_t)0xFFF;
        if (mprotect((void*)page, 0x2000, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) return nullptr;
        uint64_t nfn = (uint64_t)fn;
        memcpy(p + 6, &nfn, 8);                                         // retarget the existing jump
        mprotect((void*)page, 0x2000, PROT_READ | PROT_EXEC);
        OverlayLog("[hooks] %s: chained onto an existing hook\n", what);
        return (GuestFn)t;
    }
    int len = 0;
    while (len < 14) {
        int n = X86InsnLen(p + len);
        if (n <= 0) {
            OverlayLog("[hooks] %s: cannot decode prologue at +%d (%02x %02x %02x %02x); not hooked\n", what, len,
                       p[len], p[len + 1], p[len + 2], p[len + 3]);
            return nullptr;
        }
        len += n;
    }
    if (!g_TrampPool) {
        g_TrampPool = (uint8_t*)mmap(nullptr, 1 << 16, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (g_TrampPool == MAP_FAILED) { g_TrampPool = nullptr; return nullptr; }
    }
    if (g_TrampUsed + len + 14 > (1 << 16)) return nullptr;
    uint8_t* t = g_TrampPool + g_TrampUsed;
    memcpy(t, p, len);
    WriteAbsJmp(t + len, host + len);
    g_TrampUsed += (len + 14 + 15) & ~15;
    uintptr_t page = host & ~(uintptr_t)0xFFF;
    if (mprotect((void*)page, 0x2000, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) {
        OverlayLog("[hooks] %s: mprotect failed\n", what);
        return nullptr;
    }
    WriteAbsJmp(p, (uintptr_t)fn);
    mprotect((void*)page, 0x2000, PROT_READ | PROT_EXEC);
    __builtin___clear_cache((char*)p, (char*)p + 14);
    return (GuestFn)t;
}

struct GuestHook { uint32_t addr; GuestFn fn; GuestFn* orig; const char* name; };

static bool InstallGuestHooks(const std::vector<GuestHook>& hooks) {
    std::vector<uint32_t> want;
    for (auto& h : hooks) want.push_back(h.addr);
    auto found = ResolveGuestFns(want);
    bool ok = true;
    for (auto& h : hooks) {
        if (*h.orig) continue;                       // already hooked (another feature installed it)
        auto it = found.find(h.addr);
        if (it == found.end()) { OverlayLog("[hooks] f_%08x (%s): symbol not found\n", h.addr, h.name); ok = false; continue; }
        GuestFn t = HookHost(it->second, h.fn, h.name);
        if (!t) { ok = false; continue; }
        *h.orig = t;
        OverlayLog("[hooks] f_%08x (%s) hooked at host %p\n", h.addr, h.name, (void*)it->second);
    }
    return ok;
}

// ---- trace mode -----------------------------------------------------------------------------------------------
enum { kMaxTrace = 96 };
static uint32_t g_TraceAddr[kMaxTrace];
static GuestFn g_TraceOrig[kMaxTrace];
static std::atomic<uint64_t> g_TraceCount[kMaxTrace];
static std::atomic<uint64_t> g_TraceX0[kMaxTrace];
static int g_TraceN = 0;

static bool g_TraceRet = false;          // HOOKSHOT_GUEST_TRACE_RET=1: log each function's return w0 when it changes
static uint32_t g_TraceLastRet[kMaxTrace];

template <int I> static void TraceStub(GuestCtx* c) {
    if (g_TraceCount[I].fetch_add(1, std::memory_order_relaxed) == 0) g_TraceX0[I] = c->x[0];
    uint64_t self = c->x[0];
    g_TraceOrig[I](c);
    if (g_TraceRet) {
        uint32_t r = (uint32_t)c->x[0];
        if (r != g_TraceLastRet[I] || g_TraceCount[I].load() == 1) {
            g_TraceLastRet[I] = r;
            OverlayLog("[ret] f_%08x self %llx -> w0 %08x (call %llu)\n", g_TraceAddr[I], (unsigned long long)self, r,
                       (unsigned long long)g_TraceCount[I].load());
        }
    }
}
template <int... I> static constexpr GuestFn TraceStubAt(int i, std::integer_sequence<int, I...>) {
    constexpr GuestFn t[] = {TraceStub<I>...};
    return t[i];
}

static void StartGuestTrace(const char* list) {
    g_TraceRet = getenv("HOOKSHOT_GUEST_TRACE_RET") != nullptr;
    std::vector<GuestHook> hooks;
    std::string s(list);
    size_t pos = 0;
    while (pos < s.size() && g_TraceN < kMaxTrace) {
        size_t e = s.find(',', pos);
        if (e == std::string::npos) e = s.size();
        std::string tok = s.substr(pos, e - pos);
        pos = e + 1;
        if (tok.empty()) continue;
        int i = g_TraceN++;
        g_TraceAddr[i] = (uint32_t)strtoul(tok.c_str(), nullptr, 16);
        hooks.push_back({g_TraceAddr[i], TraceStubAt(i, std::make_integer_sequence<int, kMaxTrace>{}), &g_TraceOrig[i], "trace"});
    }
    InstallGuestHooks(hooks);
    std::thread([] {
        uint64_t last[kMaxTrace] = {};
        auto t0 = std::chrono::steady_clock::now();
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            char line[4096]; int n = 0;
            for (int i = 0; i < g_TraceN; i++) {
                uint64_t c = g_TraceCount[i].load();
                if (c != last[i] && n < 3900)
                    n += snprintf(line + n, sizeof line - n, " %08x:%llu", g_TraceAddr[i], (unsigned long long)(c - last[i]));
                last[i] = c;
            }
            if (n) OverlayLog("[trace %.2f]%s\n", t, line);
        }
    }).detach();
}
