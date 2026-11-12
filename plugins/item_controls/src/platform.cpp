#include "platform.h"
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <mutex>
#include <algorithm>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dlfcn.h>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <sys/mman.h>
#include <unistd.h>
#endif

namespace plat {

static FILE* g_Log = nullptr;
static std::mutex g_LogMu;

char PathSep() {
#ifdef _WIN32
    return '\\';
#else
    return '/';
#endif
}

std::string LibraryDir() {
    char path[4096] = {};
#ifdef _WIN32
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)(void*)&LibraryDir, &self);
    char raw[4096] = {};
    GetModuleFileNameA(self, raw, sizeof raw);
    if (!GetFullPathNameA(raw, sizeof path, path, nullptr)) snprintf(path, sizeof path, "%s", raw);
#else
    Dl_info info;
    if (dladdr((void*)&LibraryDir, &info) && info.dli_fname) {   // (the loader may open it as .../linux/./lib...so)
        if (!realpath(info.dli_fname, path)) snprintf(path, sizeof path, "%s", info.dli_fname);
    }
#endif
    std::string s = path;
    size_t k = s.find_last_of("/\\");
    return k == std::string::npos ? std::string(".") : s.substr(0, k);
}

void LogOpen(const std::string& path) {
    std::lock_guard<std::mutex> lk(g_LogMu);
    if (g_Log) fclose(g_Log);
    g_Log = fopen(path.c_str(), "w");
}

void Log(const char* fmt, ...) {
    std::lock_guard<std::mutex> lk(g_LogMu);
    if (!g_Log) return;
    va_list a;
    va_start(a, fmt);
    vfprintf(g_Log, fmt, a);
    va_end(a);
    fflush(g_Log);
}

int64_t NowNs() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

#ifndef _WIN32
// ---- Linux ---------------------------------------------------------------------------------------------------
static int FirstObject(struct dl_phdr_info* info, size_t, void* out) {
    *(uintptr_t*)out = info->dlpi_addr;          // the first entry is the main program
    return 1;
}

std::unordered_map<std::string, uintptr_t> ResolveSymbols(const std::vector<std::string>& names) {
    std::unordered_map<std::string, uintptr_t> out;
    uintptr_t base = 0;
    dl_iterate_phdr(FirstObject, &base);
    std::unordered_map<std::string, int> want;
    for (auto& n : names) want[n] = 1;
    int fd = open("/proc/self/exe", O_RDONLY);
    if (fd < 0) return out;
    Elf64_Ehdr eh;
    if (pread(fd, &eh, sizeof eh, 0) != (ssize_t)sizeof eh) { close(fd); return out; }
    std::vector<Elf64_Shdr> sh(eh.e_shnum);
    if (pread(fd, sh.data(), sizeof(Elf64_Shdr) * eh.e_shnum, eh.e_shoff) <= 0) { close(fd); return out; }
    for (auto& s : sh) {
        if (s.sh_type != SHT_SYMTAB) continue;
        const Elf64_Shdr& strs = sh[s.sh_link];
        std::vector<char> str(strs.sh_size);
        if (pread(fd, str.data(), strs.sh_size, strs.sh_offset) <= 0) continue;
        size_t n = s.sh_size / sizeof(Elf64_Sym);
        const size_t chunk = 65536;
        std::vector<Elf64_Sym> syms(chunk);
        for (size_t i = 0; i < n; i += chunk) {
            size_t k = std::min(chunk, n - i);
            if (pread(fd, syms.data(), k * sizeof(Elf64_Sym), s.sh_offset + i * sizeof(Elf64_Sym)) <= 0) break;
            for (size_t j = 0; j < k; j++) {
                const Elf64_Sym& y = syms[j];
                if (ELF64_ST_TYPE(y.st_info) != STT_FUNC || y.st_name >= str.size() || !y.st_value) continue;
                auto it = want.find(&str[y.st_name]);
                if (it != want.end()) out[it->first] = base + y.st_value;
            }
        }
    }
    close(fd);
    return out;
}

void ForEachWritableGuestRange(const std::function<void(uintptr_t, uintptr_t)>& fn) {
    FILE* m = fopen("/proc/self/maps", "r");
    if (!m) return;
    char line[512];
    std::vector<unsigned char> vec;
    while (fgets(line, sizeof line, m)) {
        unsigned long lo, hi; char perms[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3 || perms[0] != 'r' || perms[1] != 'w') continue;
        lo = std::max<unsigned long>(lo, kGuestBase);
        hi = std::min<unsigned long>(hi, kGuestBase + kGuestSpan);
        for (unsigned long w = lo; w < hi; w += (1UL << 30)) {
            unsigned long we = std::min<unsigned long>(hi, w + (1UL << 30));
            vec.resize((we - w) >> 12);
            if (mincore((void*)w, we - w, vec.data()) != 0) continue;
            size_t run = 0;                                  // report runs of resident pages
            for (size_t pg = 0; pg <= vec.size(); pg++) {
                bool res = pg < vec.size() && (vec[pg] & 1);
                if (res) { if (!run) run = pg + 1; continue; }
                if (run) { fn(w + ((run - 1) << 12), w + (pg << 12)); run = 0; }
            }
        }
    }
    fclose(m);
}

uint8_t* AllocNear(uintptr_t near, size_t size) {
    const long pg = sysconf(_SC_PAGESIZE);
    size = (size + pg - 1) & ~(size_t)(pg - 1);
    for (int64_t d = 1 << 20; d < (1LL << 31) - (1LL << 24); d += 1 << 20) {
        for (int sgn = -1; sgn <= 1; sgn += 2) {
            uintptr_t want = (near + sgn * d) & ~(uintptr_t)(pg - 1);
            void* p = mmap((void*)want, size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (p == MAP_FAILED) continue;
            int64_t dist = (int64_t)((uintptr_t)p - near);
            if (dist > -(1LL << 31) + (1 << 20) && dist < (1LL << 31) - (1 << 20)) return (uint8_t*)p;
            munmap(p, size);
        }
    }
    return nullptr;
}

bool MakeWritable(void* p, size_t n, bool writable) {
    uintptr_t a = (uintptr_t)p & ~(uintptr_t)0xFFF, e = ((uintptr_t)p + n + 0xFFF) & ~(uintptr_t)0xFFF;
    return mprotect((void*)a, e - a, PROT_READ | PROT_EXEC | (writable ? PROT_WRITE : 0)) == 0;
}

void FlushCode(void* p, size_t n) { __builtin___clear_cache((char*)p, (char*)p + n); }

#else
// ---- Windows -------------------------------------------------------------------------------------------------
// The builder links with lld-link /map (MSVC layout: "Preferred load address is ...", then lines
// " 0001:00000000  f_80000000  0000000140001000 f  obj.o") or, with MinGW, lld's own map ("VMA LMA Size Align Out In
// Symbol"). Either way a line holds the symbol name and its preferred virtual address.
static bool IsHex(const std::string& t) {
    if (t.empty()) return false;
    for (char ch : t) if (!isxdigit((unsigned char)ch)) return false;
    return true;
}

static void ParseMap(const std::string& mapPath, uintptr_t moduleBase, uintptr_t defaultPreferred,
                     std::unordered_map<std::string, int>& want, std::unordered_map<std::string, uintptr_t>& out) {
    FILE* f = fopen(mapPath.c_str(), "r");
    if (!f) return;
    Log("[plat] reading %s\n", mapPath.c_str());
    uintptr_t preferred = 0;
    std::vector<std::pair<std::string, uintptr_t>> found;
    char line[2048];
    while (fgets(line, sizeof line, f)) {
        if (!preferred) {
            const char* p = strstr(line, "Preferred load address is ");
            if (p) { preferred = (uintptr_t)strtoull(p + 26, nullptr, 16); continue; }
        }
        // tokenize
        std::vector<std::string> tok;
        for (char* s = strtok(line, " \t\r\n"); s; s = strtok(nullptr, " \t\r\n")) tok.push_back(s);
        if (tok.size() < 2) continue;
        int nameAt = -1;
        for (size_t i = 0; i < tok.size(); i++) {
            if (tok[i] == "__ImageBase" && !preferred) nameAt = (int)i;
            else if (want.count(tok[i])) { nameAt = (int)i; break; }
        }
        if (nameAt < 0) continue;
        uintptr_t va = 0;
        for (size_t i = 0; i < tok.size(); i++) {
            if ((int)i == nameAt || !IsHex(tok[i]) || tok[i].size() < 8) continue;
            va = (uintptr_t)strtoull(tok[i].c_str(), nullptr, 16);
            if (va) break;
        }
        if (!va) continue;
        if (tok[nameAt] == "__ImageBase") { preferred = va; continue; }
        found.push_back({tok[nameAt], va});
    }
    fclose(f);
    if (!preferred) preferred = defaultPreferred;
    for (auto& [name, va] : found) out[name] = moduleBase + (va - preferred);
}

std::unordered_map<std::string, uintptr_t> ResolveSymbols(const std::vector<std::string>& names) {
    std::unordered_map<std::string, uintptr_t> out;
    std::unordered_map<std::string, int> want;
    for (auto& n : names) want[n] = 1;
    // the game's code is in hookshot.exe, or in hookshot_game.dll when the builder split it out
    struct Mod { HMODULE h; uintptr_t prefer; };
    Mod mods[2] = {{GetModuleHandleA(nullptr), 0x140000000ULL}, {GetModuleHandleA("hookshot_game.dll"), 0x180000000ULL}};
    for (auto& m : mods) {
        if (!m.h) continue;
        char path[MAX_PATH] = {};
        GetModuleFileNameA(m.h, path, MAX_PATH);
        std::string map = path;
        size_t dot = map.find_last_of('.');
        if (dot != std::string::npos) map = map.substr(0, dot);
        ParseMap(map + ".map", (uintptr_t)m.h, m.prefer, want, out);
    }
    return out;
}

void ForEachWritableGuestRange(const std::function<void(uintptr_t, uintptr_t)>& fn) {
    uintptr_t a = kGuestBase, end = kGuestBase + kGuestSpan;
    MEMORY_BASIC_INFORMATION mi;
    while (a < end && VirtualQuery((void*)a, &mi, sizeof mi) == sizeof mi) {
        uintptr_t lo = (uintptr_t)mi.BaseAddress, hi = lo + mi.RegionSize;
        const DWORD rw = PAGE_READWRITE | PAGE_EXECUTE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_WRITECOPY;
        if (mi.State == MEM_COMMIT && (mi.Protect & rw) && !(mi.Protect & (PAGE_GUARD | PAGE_NOACCESS)))
            fn(std::max(lo, a), std::min(hi, end));
        if (hi <= a) break;
        a = hi;
    }
}

uint8_t* AllocNear(uintptr_t near, size_t size) {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    const uintptr_t gran = si.dwAllocationGranularity;
    for (int64_t d = gran; d < (1LL << 31) - (1LL << 24); d += gran) {
        for (int sgn = -1; sgn <= 1; sgn += 2) {
            uintptr_t want = (near + sgn * d) & ~(gran - 1);
            void* p = VirtualAlloc((void*)want, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (p) return (uint8_t*)p;
        }
    }
    return nullptr;
}

bool MakeWritable(void* p, size_t n, bool writable) {
    DWORD old;
    return VirtualProtect(p, n, writable ? PAGE_EXECUTE_READWRITE : PAGE_EXECUTE_READ, &old) != 0;
}

void FlushCode(void* p, size_t n) { FlushInstructionCache(GetCurrentProcess(), p, n); }
#endif

}  // namespace plat
