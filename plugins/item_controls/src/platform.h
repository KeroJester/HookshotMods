// Item Controls plugin: what differs between Linux and Windows builds of Hookshot.
//   - guest memory: the AArch64 guest's address space at host A64_HOST_BASE (0x100000000000) on both
//   - lifted functions f_<guest addr> and host functions: ELF .symtab of /proc/self/exe (Linux), the linker map the
//     Hookshot builder writes beside the game (Windows: hookshot.map / hookshot_game.map, lld-link /map)
//   - writable memory regions (for the GameData scan), this library's own folder (= where the mod lives), logging
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <functional>
#include <unordered_map>

namespace plat {

constexpr uint64_t kGuestBase = 0x100000000000ULL;   // a64-kit rt/a64.h A64_HOST_BASE
constexpr uint64_t kGuestSpan = 1ULL << 39;          // the 512 GB guest window

struct GuestCtx {                 // a64-kit rt/a64.h GuestContext
    uint64_t x[32];               // x[31] = SP
    uint64_t pc;
    uint8_t n, z, c, v;
    int halted;
    uint64_t vreg[32][2];
    uint64_t tpidr_el0, tpidrro_el0, fpcr, fpsr;
    uint64_t excl_addr, excl_lo, excl_hi;
    uint32_t excl_size;
    int excl_valid;
    void* thread;
};
typedef void (*GuestFn)(GuestCtx*);

inline uint8_t* GPtr(uint64_t a) { return (uint8_t*)(kGuestBase + a); }
template <class T> inline T GRead(uint64_t a) { T v; memcpy(&v, GPtr(a), sizeof(T)); return v; }
template <class T> inline void GWrite(uint64_t a, T v) { memcpy(GPtr(a), &v, sizeof(T)); }
inline bool GuestPtrOk(uint64_t a) { return a && a < kGuestSpan; }

// Folder of this library, e.g. .../mods/050-ItemControls/layer/linux (with a trailing separator removed)
std::string LibraryDir();
char PathSep();

// Log file (opened by Init), printf style
void LogOpen(const std::string& path);
void Log(const char* fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

// Host addresses of named functions in the game's code (f_<guest addr> or host symbols). Missing names are absent.
std::unordered_map<std::string, uintptr_t> ResolveSymbols(const std::vector<std::string>& names);

// Calls fn(lo, hi) for every resident, readable+writable host range inside the guest window (host addresses)
void ForEachWritableGuestRange(const std::function<void(uintptr_t, uintptr_t)>& fn);

// Executable memory within +-2 GB of `near` (for hook trampolines), or nullptr
uint8_t* AllocNear(uintptr_t near, size_t size);
bool MakeWritable(void* p, size_t n, bool writable);   // code page protection
void FlushCode(void* p, size_t n);

int64_t NowNs();

}  // namespace plat
