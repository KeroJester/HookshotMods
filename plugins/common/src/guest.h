// Calling the game's (lifted) functions from a hook, on the calling thread, as Hookshot's host/oot_menu.c guest_call
// does: the guest context is saved, x0.. are set, x30 = 0, the lifted function runs, the context is restored.
// Functions are named by their guest address and found once through the f_<addr> symbols.
#pragma once
#include "platform.h"
#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>

namespace guest {

class Functions {
public:
    // resolves every address; false (and the first missing one logged) when any is missing
    bool Resolve(const std::vector<uint64_t>& addrs, const char* who) {
        std::vector<std::string> names;
        char n[32];
        for (uint64_t a : addrs) { snprintf(n, sizeof n, "f_%llx", (unsigned long long)a); names.push_back(n); }
        auto sym = plat::ResolveSymbols(names);
        for (size_t i = 0; i < names.size(); i++) {
            if (!sym.count(names[i])) { plat::Log("[%s] %s not found\n", who, names[i].c_str()); return false; }
            fn_[addrs[i]] = sym[names[i]];
        }
        return true;
    }
    uintptr_t Host(uint64_t addr) const { auto it = fn_.find(addr); return it == fn_.end() ? 0 : it->second; }
    uint64_t Call(plat::GuestCtx* c, uint64_t addr, uint64_t a0 = 0, uint64_t a1 = 0, uint64_t a2 = 0, uint64_t a3 = 0,
                  uint64_t a4 = 0, uint64_t a5 = 0) const {
        const plat::GuestCtx saved = *c;
        c->x[0] = a0, c->x[1] = a1, c->x[2] = a2, c->x[3] = a3, c->x[4] = a4, c->x[5] = a5, c->x[30] = 0;
        ((plat::GuestFn)fn_.at(addr))(c);
        const uint64_t r = c->x[0];
        *c = saved;
        return r;
    }
private:
    std::unordered_map<uint64_t, uintptr_t> fn_;
};

inline uint64_t Q(uint64_t a) { return plat::GuestPtrOk(a) ? plat::GRead<uint64_t>(a) : 0; }
inline uint32_t D(uint64_t a) { return plat::GuestPtrOk(a) ? plat::GRead<uint32_t>(a) : 0; }

// Scratch memory below the guest stack for a call's arguments (the calls then run below it)
struct Scratch {
    plat::GuestCtx* c;
    uint64_t at;
    explicit Scratch(plat::GuestCtx* cc) : c(cc), at(cc->x[31] - 0x100) { c->x[31] -= 0x400; }
    ~Scratch() { c->x[31] += 0x400; }
};

}  // namespace guest
