#include "hook.h"
#include <mutex>

namespace hook {
using plat::Log;

struct Insn { int len = -1; int ripDisp = -1; };   // ripDisp: offset of a RIP-relative disp32 inside the instruction

// x86-64 instruction length for the forms compilers put in prologues (subset)
static Insn Decode(const uint8_t* p) {
    Insn r;
    int i = 0;
    while (p[i] == 0x66 || p[i] == 0xF2 || p[i] == 0xF3) i++;           // operand-size / SSE prefixes
    uint8_t rex = 0;
    if ((p[i] & 0xF0) == 0x40) rex = p[i++];
    uint8_t op = p[i++];
    auto modrm = [&](int imm) -> Insn {
        uint8_t m = p[i++];
        int mod = m >> 6, rm = m & 7;
        if (mod != 3 && rm == 4) {                                      // SIB
            uint8_t sib = p[i++];
            if (mod == 0 && (sib & 7) == 5) i += 4;
        } else if (mod == 0 && rm == 5) {                               // RIP-relative disp32
            r.ripDisp = i;
            i += 4;
        }
        if (mod == 1) i += 1;
        else if (mod == 2) i += 4;
        r.len = i + imm;
        return r;
    };
    if (op >= 0x50 && op <= 0x5F) { r.len = i; return r; }             // push / pop r64
    if (op >= 0xB8 && op <= 0xBF) { r.len = i + ((rex & 8) ? 8 : 4); return r; }   // mov r, imm
    switch (op) {
    case 0x01: case 0x03: case 0x09: case 0x0B: case 0x21: case 0x23: case 0x29: case 0x2B:
    case 0x31: case 0x33: case 0x39: case 0x3B: case 0x63: case 0x84: case 0x85: case 0x88: case 0x89:
    case 0x8A: case 0x8B: case 0x8D:
        return modrm(0);
    case 0x80: case 0x83: case 0xC0: case 0xC1: case 0xC6: return modrm(1);
    case 0x81: case 0xC7: case 0x69: return modrm(4);
    case 0x90: case 0xC3: case 0xCC: r.len = i; return r;
    case 0x0F: {
        uint8_t op2 = p[i++];
        switch (op2) {
        case 0x1E: case 0x1F: case 0x10: case 0x11: case 0x28: case 0x29: case 0x57: case 0x6E: case 0x6F:
        case 0x7E: case 0x7F: case 0xB6: case 0xB7: case 0xBE: case 0xBF: case 0xD6: case 0xEF:
            return modrm(0);
        default: return r;
        }
    }
    default: return r;
    }
}

static void WriteAbsJmp(uint8_t* at, uintptr_t target) {
    at[0] = 0xFF; at[1] = 0x25; at[2] = at[3] = at[4] = at[5] = 0;       // jmp [rip+0]
    memcpy(at + 6, &target, 8);
}
static bool IsOurJmp(const uint8_t* p) { return p[0] == 0xFF && p[1] == 0x25 && !p[2] && !p[3] && !p[4] && !p[5]; }

uintptr_t FollowStubs(uintptr_t t) {
    for (int k = 0; k < 8; k++) {
        const uint8_t* p = (const uint8_t*)t;
        if (p[0] == 0xF3 && p[1] == 0x0F && p[2] == 0x1E && p[3] == 0xFA && (p[4] == 0xE9 || p[4] == 0xEB)) p += 4, t += 4;   // endbr64; jmp
        if (p[0] == 0xE9) { int32_t d; memcpy(&d, p + 1, 4); t = t + 5 + d; continue; }
        if (p[0] == 0xEB) { t = t + 2 + (int8_t)p[1]; continue; }
        if (p[0] == 0xFF && p[1] == 0x25 && !IsOurJmp(p)) {             // jmp [rip+x]: an import thunk
            int32_t d; memcpy(&d, p + 2, 4);
            uintptr_t slot = t + 6 + d, dest;
            memcpy(&dest, (void*)slot, 8);
            t = dest;
            continue;
        }
        break;
    }
    return t;
}

// trampolines: pools near the code they serve (RIP-relative operands must stay in reach)
struct Pool { uint8_t* base; size_t used, size; };
static std::vector<Pool> g_Pools;
static std::mutex g_Mu;

static uint8_t* Tramp(uintptr_t near, size_t n) {
    for (auto& p : g_Pools) {
        int64_t d = (int64_t)((uintptr_t)p.base - near);
        if (d > -(1LL << 30) && d < (1LL << 30) && p.used + n <= p.size) { uint8_t* r = p.base + p.used; p.used += (n + 15) & ~15; return r; }
    }
    const size_t sz = 1 << 16;
    uint8_t* b = plat::AllocNear(near, sz);
    if (!b) return nullptr;
    g_Pools.push_back({b, (n + 15) & ~(size_t)15, sz});
    return b;
}

plat::GuestFn Install(uintptr_t target, plat::GuestFn fn, const char* what) {
    std::lock_guard<std::mutex> lk(g_Mu);
    if (!target) { Log("[hook] %s: not found\n", what); return nullptr; }
    target = FollowStubs(target);
    uint8_t* p = (uint8_t*)target;
    if (IsOurJmp(p)) {                                                   // chain onto an existing hook
        uintptr_t prev; memcpy(&prev, p + 6, 8);
        uint8_t* t = Tramp(target, 16);
        if (!t) return nullptr;
        WriteAbsJmp(t, prev);
        if (!plat::MakeWritable(p, 14, true)) return nullptr;
        uintptr_t nf = (uintptr_t)fn;
        memcpy(p + 6, &nf, 8);
        plat::MakeWritable(p, 14, false);
        plat::FlushCode(p, 14);
        Log("[hook] %s: chained onto an existing hook at %p\n", what, (void*)p);
        return (plat::GuestFn)t;
    }
    int len = 0;
    std::vector<Insn> ins;
    while (len < 14) {
        Insn in = Decode(p + len);
        if (in.len <= 0) {
            Log("[hook] %s: cannot decode prologue at +%d (%02x %02x %02x %02x %02x); not hooked\n", what, len,
                p[len], p[len + 1], p[len + 2], p[len + 3], p[len + 4]);
            return nullptr;
        }
        ins.push_back(in);
        len += in.len;
    }
    uint8_t* t = Tramp(target, len + 14);
    if (!t) { Log("[hook] %s: no trampoline memory\n", what); return nullptr; }
    memcpy(t, p, len);
    int off = 0;
    for (auto& in : ins) {                                               // re-aim RIP-relative operands
        if (in.ripDisp >= 0) {
            int32_t d; memcpy(&d, p + off + in.ripDisp, 4);
            int64_t abs = (int64_t)(target + off + in.len) + d;
            int64_t nd = abs - (int64_t)((uintptr_t)t + off + in.len);
            if (nd < INT32_MIN || nd > INT32_MAX) { Log("[hook] %s: RIP-relative operand out of reach\n", what); return nullptr; }
            int32_t n32 = (int32_t)nd;
            memcpy(t + off + in.ripDisp, &n32, 4);
        }
        off += in.len;
    }
    WriteAbsJmp(t + len, target + len);
    plat::FlushCode(t, len + 14);
    if (!plat::MakeWritable(p, 14, true)) { Log("[hook] %s: cannot unprotect\n", what); return nullptr; }
    WriteAbsJmp(p, (uintptr_t)fn);
    plat::MakeWritable(p, 14, false);
    plat::FlushCode(p, 14);
    Log("[hook] %s hooked at %p\n", what, (void*)p);
    return (plat::GuestFn)t;
}

}  // namespace hook
