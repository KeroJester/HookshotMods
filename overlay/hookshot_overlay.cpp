#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#ifndef VK_NO_PROTOTYPES
#define VK_NO_PROTOTYPES
#endif
#include <vulkan/vulkan.h>
#include <SDL3/SDL.h>
#include <dlfcn.h>
#include <link.h>
#include <errno.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <chrono>
#include <vector>
#include <mutex>
#include <string>
#include <cmath>
#include <algorithm>

#include "imgui.h"
#include "imgui_impl_sdl3.h"
#include "imgui_impl_vulkan.h"

static FILE* g_LogFile = nullptr;
static std::mutex g_LogMutex;

// The folder holding this library (the source checkout's overlay/): its log and tuner files go there.
static std::string OverlayPath(const char* name) {
    static std::string dir = [] {
        Dl_info info{};
        std::string d = ".";
        if (dladdr((void*)&OverlayPath, &info) && info.dli_fname) {
            d = info.dli_fname;
            size_t slash = d.rfind('/');
            d = slash == std::string::npos ? "." : d.substr(0, slash);
        }
        return d;
    }();
    return dir + "/" + name;
}

static void OverlayLog(const char* fmt, ...) {
    std::lock_guard<std::mutex> lock(g_LogMutex);
    if (!g_LogFile) {
        g_LogFile = fopen(OverlayPath("overlay.log").c_str(), "a");
    }
    if (!g_LogFile) return;
    va_list args;
    va_start(args, fmt);
    vfprintf(g_LogFile, fmt, args);
    va_end(args);
    fflush(g_LogFile);
}

#include "guest_hooks.inc"
static void InstallModHooks();
static void SohInstall();

__attribute__((constructor))
static void OnOverlayLoaded() {
    if (program_invocation_name && !strstr(program_invocation_name, "hookshot")) {
        return;
    }
    g_LogFile = fopen(OverlayPath("overlay.log").c_str(), "a");
    OverlayLog("[overlay] Loaded into process pid=%d prog=%s\n", (int)getpid(), program_invocation_name ? program_invocation_name : "?");
    if (const char* t = getenv("HOOKSHOT_GUEST_TRACE")) StartGuestTrace(t);
    InstallModHooks();
    SohInstall();
}

// -------------------------------------------------------------
// Hookshot Host Dispatch & Guest Execution Bridge
// -------------------------------------------------------------
static const uintptr_t GUEST_RAM_BASE = 0x100000000000ULL;

struct HosHostCall {
    void (*fn)(uint64_t* ctx, void* ud); // +0x00
    void* ud;                             // +0x08
    char name[64];                        // +0x10
    uint64_t count;                       // +0x50
};

typedef uint64_t (*hos_call_guest_fn)(uint64_t* ctx, uint64_t pc, uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3);
typedef uint64_t* (*hos_ctx_fn)();
typedef uint64_t (*hos_sys_alloc_fn)(uint64_t size, uint64_t align);

static uintptr_t g_HookshotBase = 0;
static hos_call_guest_fn hos_call_guest = nullptr;
static hos_ctx_fn hos_ctx = nullptr;
static hos_sys_alloc_fn hos_sys_alloc = nullptr;
static int32_t* g_HosNCalls = nullptr;
static HosHostCall* g_HosCalls = nullptr;
static uint8_t* g_HosPosixKeys = nullptr;

static void (*real_nvnQueuePresentTexture)(uint64_t* ctx, void* ud) = nullptr;

static uint64_t g_ScratchGAddr = 0;

static uint64_t GetScratchGAddr() {
    if (g_ScratchGAddr != 0) return g_ScratchGAddr;
    if (hos_sys_alloc) {
        g_ScratchGAddr = hos_sys_alloc(0x10000, 0x1000); // 64 KB aligned to 4 KB
        OverlayLog("[overlay] Allocated guest scratch via hos_sys_alloc: 0x%lx\n", (unsigned long)g_ScratchGAddr);
    }
    if (g_ScratchGAddr == 0) {
        g_ScratchGAddr = 0x06000000ULL;
        OverlayLog("[overlay] Using fallback guest scratch: 0x%lx\n", (unsigned long)g_ScratchGAddr);
    }
    mprotect((void*)(GUEST_RAM_BASE + g_ScratchGAddr), 0x10000, PROT_READ | PROT_WRITE);
    return g_ScratchGAddr;
}

// Safely execute recompiled guest function without corrupting live CPU registers
static uint64_t SafeCallGuest(uint64_t* ctx, uint64_t pc, uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3) {
    if (!ctx || !hos_call_guest) return 0;
    alignas(16) uint8_t saved_ctx[0x360];
    memcpy(saved_ctx, ctx, 0x360);
    uint64_t ret = hos_call_guest(ctx, pc, x0, x1, x2, x3);
    memcpy(ctx, saved_ctx, 0x360);
    return ret;
}

static uint64_t SafeCallGuest5(uint64_t* ctx, uint64_t pc, uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3, uint64_t x4) {
    if (!ctx || !hos_call_guest) return 0;
    alignas(16) uint8_t saved_ctx[0x360];
    memcpy(saved_ctx, ctx, 0x360);
    ctx[4] = x4;
    uint64_t ret = hos_call_guest(ctx, pc, x0, x1, x2, x3);
    memcpy(ctx, saved_ctx, 0x360);
    return ret;
}

// Action queue for thread-safe UI -> Guest execution
enum class ActionType {
    GrantMasterUnlock,
    GrantSwordsShields,
    GrantTunicsBoots,
    GrantMasks,
    GrantTools,
    GrantBottles,
    GrantSongs,
    SetHearts,
    FullHeal,
    SetMagic,
    SetRupees,
    SwitchAgeChild,
    SwitchAgeAdult,
    Warp,
    TransformGoron,
    RevertHylian
};

struct QueuedAction {
    ActionType type;
    int int_val = 0;
    std::string str_val;
};

static std::mutex g_ActionMutex;
static std::vector<QueuedAction> g_ActionQueue;

static void EnqueueAction(const QueuedAction& act) {
    std::lock_guard<std::mutex> lock(g_ActionMutex);
    g_ActionQueue.push_back(act);
}

struct FakePushNode {
    uint8_t pad0[0x18];
    uint32_t flags;            // +0x18 = 0
    uint32_t pad1;
    uint64_t ptr_to_str_ptr;   // +0x20 -> scratch + 0x38
    uint64_t pad2;
    uint64_t ptr_to_is_direct; // +0x30 -> scratch + 0x40
    uint64_t str_ptr;          // +0x38 -> scratch + 0x48
    uint32_t is_direct;        // +0x40 = 1
    uint32_t pad3;
    char actor_name[128];      // +0x48 = "..."
};

static void PushItemToPouch(uint64_t* ctx, const char* actor_name) {
    if (!ctx || !hos_call_guest || !actor_name) return;
    uint64_t scratch = GetScratchGAddr();
    if (!scratch) return;

    FakePushNode* node = (FakePushNode*)(GUEST_RAM_BASE + scratch);
    memset(node, 0, sizeof(FakePushNode));
    node->flags = 0;
    node->ptr_to_str_ptr = scratch + offsetof(FakePushNode, str_ptr);
    node->ptr_to_is_direct = scratch + offsetof(FakePushNode, is_direct);
    node->str_ptr = scratch + offsetof(FakePushNode, actor_name);
    node->is_direct = 1;
    strncpy(node->actor_name, actor_name, sizeof(node->actor_name) - 1);

    SafeCallGuest(ctx, 0x8112d370ULL, scratch, 0, 0, 0);
}

static uint64_t GetPlayerMgr() {
    uint64_t* pmgr_var = (uint64_t*)(GUEST_RAM_BASE + 0x84e98e28ULL);
    if (pmgr_var && *pmgr_var) return *pmgr_var;
    uint64_t* holder = (uint64_t*)(GUEST_RAM_BASE + 0x84c9e0d0ULL);
    if (holder && *holder) {
        uint64_t* pmgr_ptr = (uint64_t*)(GUEST_RAM_BASE + *holder);
        if (pmgr_ptr && *pmgr_ptr) return *pmgr_ptr;
    }
    return 0;
}

static uint64_t GetEquipComp() {
    uint64_t* pmgr_var = (uint64_t*)(GUEST_RAM_BASE + 0x84e98e28ULL);
    if (pmgr_var && *pmgr_var) {
        uint64_t* ec_ptr = (uint64_t*)(GUEST_RAM_BASE + *pmgr_var + 0x470);
        if (ec_ptr && *ec_ptr) return *ec_ptr;
    }
    uint64_t* holder = (uint64_t*)(GUEST_RAM_BASE + 0x84c9e0d0ULL);
    if (holder && *holder) {
        uint64_t* pmgr_ptr = (uint64_t*)(GUEST_RAM_BASE + *holder);
        if (pmgr_ptr && *pmgr_ptr) {
            uint64_t* ec_ptr = (uint64_t*)(GUEST_RAM_BASE + *pmgr_ptr + 0x470);
            if (ec_ptr && *ec_ptr) return *ec_ptr;
        }
    }
    uint64_t pmgr = GetPlayerMgr();
    if (pmgr) {
        uint64_t* ec_ptr = (uint64_t*)(GUEST_RAM_BASE + pmgr + 0x470);
        if (ec_ptr && *ec_ptr) return *ec_ptr;
    }
    return 0;
}

static void SyncEquipment(uint64_t* ctx) {
    (void)ctx;
    uint64_t equip_comp = GetEquipComp();
    if (!equip_comp) return;
    uint32_t* dirty = (uint32_t*)(GUEST_RAM_BASE + equip_comp + 0x12d0);
    *dirty |= 0x1ffff; // Mark all equipment slots dirty so engine naturally refreshes without un-equipping
    SafeCallGuest(ctx, 0x803971a0ULL, equip_comp, 0, 0, 0);
}

// -------------------------------------------------------------
// Live Memory Probing & Goron Speed Buff Handler
// -------------------------------------------------------------
static int g_ProbePipe[2] = {-1, -1};
static bool IsAddressReadable(const void* ptr, size_t sz) {
    if (!ptr) return false;
    if (g_ProbePipe[0] == -1) {
        if (pipe(g_ProbePipe) != 0) return false;
    }
    ssize_t written = write(g_ProbePipe[1], ptr, sz);
    if (written > 0) {
        char dummy[128];
        while (written > 0) {
            ssize_t r = read(g_ProbePipe[0], dummy, (written > (ssize_t)sizeof(dummy)) ? sizeof(dummy) : written);
            if (r <= 0) break;
            written -= r;
        }
        return true;
    }
    return false;
}

[[maybe_unused]] static bool IsChildForm(uint64_t* ctx) {
    (void)ctx;
    uint64_t equip_comp = GetEquipComp();
    if (!equip_comp) return true;
    uint64_t* player_actor_ptr = (uint64_t*)(GUEST_RAM_BASE + equip_comp + 0x18);
    if (!player_actor_ptr || !IsAddressReadable(player_actor_ptr, 8) || !*player_actor_ptr) return true;
    uint64_t player_actor = *player_actor_ptr;

    // Check actor name strings in player_actor (+0x10 to +0x40)
    for (int off = 0x10; off <= 0x40; off += 8) {
        uint64_t* ptr = (uint64_t*)(GUEST_RAM_BASE + player_actor + off);
        if (ptr && IsAddressReadable(ptr, 8) && *ptr) {
            const char* str = (const char*)(GUEST_RAM_BASE + *ptr);
            if (IsAddressReadable(str, 16)) {
                if (strstr(str, "Adult")) return false;
                if (strstr(str, "Child")) return true;
            }
        }
    }
    return true; // Default to Child Link (the standard OoT/MM child form)
}

static bool CheckAndPatchParamBlock(void* host_ptr, bool goron, bool is_child) {
    if (!host_ptr) return false;
    if (!IsAddressReadable(host_ptr, 0xd0)) return false;
    uint8_t* b = (uint8_t*)host_ptr;
    uint32_t val0 = *(uint32_t*)(b + 0x00);
    uint32_t valC8 = *(uint32_t*)(b + 0xc8);
    // Fingerprint: 50.0f (0x42480000) at +0x00 and 3.0f (0x40400000) at +0xc8
    if (val0 == 0x42480000 && valC8 == 0x40400000) {
        float* f_scale = (float*)(b + 0x34);
        float* f_lock = (float*)(b + 0x9c);
        float* f_roll = (float*)(b + 0xa8);
        if (goron) {
            *f_roll = 24.0f;
            *f_lock = 20.0f;
            *f_scale = 1.30f;
        } else {
            *f_roll = is_child ? 5.625f : 6.75f;
            *f_lock = is_child ? 5.625f : 6.75f;
            *f_scale = 1.0f;
        }
        return true;
    }
    return false;
}

static void* g_CachedParamBlock = nullptr;

static void SetGoronSpeedBuff(uint64_t* ctx, bool enable) {
    bool is_child = ctx ? IsChildForm(ctx) : true;

    // 1. Patch static image table at 0x83f21114
    uintptr_t static_host = GUEST_RAM_BASE + 0x83f21114ULL;
    uintptr_t page = static_host & ~0xfffULL;
    mprotect((void*)page, 0x2000, PROT_READ | PROT_WRITE);
    CheckAndPatchParamBlock((void*)static_host, enable, is_child);

    // 2. Check cached block first for zero-overhead per-frame updates
    if (g_CachedParamBlock && CheckAndPatchParamBlock(g_CachedParamBlock, enable, is_child)) {
        return;
    }
    g_CachedParamBlock = nullptr;

    // 3. Scan player_actor components in guest heap
    uint64_t equip_comp = GetEquipComp();
    if (!equip_comp) return;

    uint64_t* player_actor_ptr = (uint64_t*)(GUEST_RAM_BASE + equip_comp + 0x18);
    if (!player_actor_ptr || !IsAddressReadable(player_actor_ptr, 8) || !*player_actor_ptr) return;
    uint64_t player_actor = *player_actor_ptr;

    int patched_count = 0;
    // Scan level 1 pointers
    for (uint32_t off1 = 0; off1 < 0x800; off1 += 8) {
        uint64_t* p1_addr = (uint64_t*)(GUEST_RAM_BASE + player_actor + off1);
        if (!IsAddressReadable(p1_addr, 8)) continue;
        uint64_t p1 = *p1_addr;
        if (p1 < 0x10000000ULL || p1 >= 0x20000000000ULL) continue;
        void* host1 = (void*)(GUEST_RAM_BASE + p1);

        if (CheckAndPatchParamBlock(host1, enable, is_child)) {
            OverlayLog("[overlay] SetGoronSpeedBuff: Patched level 1 param at guest 0x%lx (actor +0x%x)\n",
                       (unsigned long)p1, off1);
            g_CachedParamBlock = host1;
            patched_count++;
            continue;
        }

        // Scan level 2 pointers
        for (uint32_t off2 = 0; off2 < 0x200; off2 += 8) {
            uint64_t* p2_addr = (uint64_t*)((uint8_t*)host1 + off2);
            if (!IsAddressReadable(p2_addr, 8)) continue;
            uint64_t p2 = *p2_addr;
            if (p2 < 0x10000000ULL || p2 >= 0x20000000000ULL) continue;
            void* host2 = (void*)(GUEST_RAM_BASE + p2);

            if (CheckAndPatchParamBlock(host2, enable, is_child)) {
                OverlayLog("[overlay] SetGoronSpeedBuff: Patched level 2 param at guest 0x%lx (via 0x%lx +0x%x)\n",
                           (unsigned long)p2, (unsigned long)p1, off2);
                g_CachedParamBlock = host2;
                patched_count++;
            }
        }
    }
    OverlayLog("[overlay] SetGoronSpeedBuff complete: enable=%d, total heap blocks patched=%d\n", enable, patched_count);
}

static bool EquipItemDirect(uint64_t* ctx, uint32_t slot, const char* actor_name) {
    if (!ctx || !hos_call_guest) return false;
    uint64_t equip_comp = GetEquipComp();
    if (!equip_comp) {
        OverlayLog("[overlay] EquipItemDirect failed: equip_comp is 0 (pmgr=0x%lx)\n", (unsigned long)GetPlayerMgr());
        return false;
    }

    if (!actor_name || actor_name[0] == '\0') {
        OverlayLog("[overlay] Unequipping slot %u\n", slot);
        SafeCallGuest(ctx, 0x810cceb0ULL, equip_comp, slot, 0, 0);
        uint32_t* dirty = (uint32_t*)(GUEST_RAM_BASE + equip_comp + 0x12d0);
        *dirty |= (1 << slot);
        SafeCallGuest(ctx, 0x803971a0ULL, equip_comp, 0, 0, 0);
        return true;
    }

    uint64_t scratch = GetScratchGAddr();
    if (!scratch) return false;

    // +0x600: actor name string
    // +0x680: pointer to actor name string
    uint64_t str_gaddr = scratch + 0x600;
    char* str_host = (char*)(GUEST_RAM_BASE + str_gaddr);
    strncpy(str_host, actor_name, 63);
    str_host[63] = '\0';

    uint64_t ptr_gaddr = scratch + 0x680;
    uint64_t* ptr_host = (uint64_t*)(GUEST_RAM_BASE + ptr_gaddr);
    *ptr_host = str_gaddr;

    OverlayLog("[overlay] EquipItemDirect: slot=%u name=%s (equip_comp=0x%lx str=0x%lx ptr=0x%lx)\n",
               slot, actor_name, (unsigned long)equip_comp, (unsigned long)str_gaddr, (unsigned long)ptr_gaddr);

    // Unequip current item in slot first so any existing mask (e.g. Mask of Truth) is cleanly detached
    SafeCallGuest(ctx, 0x810cceb0ULL, equip_comp, slot, 0, 0);

    uint64_t res = SafeCallGuest5(ctx, 0x81033f20ULL, equip_comp, slot, ptr_gaddr, 0, 1);
    OverlayLog("[overlay] EquipItemDirect: 0x81033f20 returned %lu\n", (unsigned long)res);

    uint32_t* dirty = (uint32_t*)(GUEST_RAM_BASE + equip_comp + 0x12d0);
    *dirty |= (1 << slot);

    // Refresh model immediately so the 3D model transforms on the current frame
    SafeCallGuest(ctx, 0x803971a0ULL, equip_comp, 0, 0, 0);

    return (res != 0);
}

static void FullHeal(uint64_t* ctx) {
    if (!ctx || !hos_call_guest) return;
    uint64_t pmgr = GetPlayerMgr();
    if (pmgr) {
        SafeCallGuest(ctx, 0x833cc0d0ULL, pmgr, 0, 0, 0);
    }
}

static uint64_t GetGameDataMgr() {
    uint64_t* bss_var = (uint64_t*)(GUEST_RAM_BASE + 0x84ea42a8ULL);
    if (bss_var && *bss_var) return *bss_var;
    uint64_t* holder_ptr = (uint64_t*)(GUEST_RAM_BASE + 0x84c9e180ULL);
    if (holder_ptr && *holder_ptr) {
        uint64_t* gmd_ptr = (uint64_t*)(GUEST_RAM_BASE + *holder_ptr);
        if (gmd_ptr && *gmd_ptr) return *gmd_ptr;
        return *holder_ptr;
    }
    return 0x84ea42a8ULL;
}

static void SetGameDataInt(uint64_t* ctx, uint32_t hash, uint32_t val) {
    if (!ctx || !hos_call_guest) return;
    uint64_t gmd = GetGameDataMgr();
    if (!gmd) return;

    uint64_t scratch = GetScratchGAddr();
    if (!scratch) return;

    // Use dummy key block in scratch space (flags = 1 at offset +0xc indicates hash-only lookup)
    uint64_t key_gaddr = scratch + 0x200;
    uint32_t* key_host = (uint32_t*)(GUEST_RAM_BASE + key_gaddr);
    memset(key_host, 0, 32);
    *(uint64_t*)(key_host) = gmd + 0x128; // Pointer to IntTable
    key_host[3] = 1; // offset 0xc has flag bit 0 set

    SafeCallGuest(ctx, 0x809d39e0ULL, gmd, val, key_gaddr, hash);
}

static void SetGameDataString(uint64_t* ctx, uint32_t hash, const char* str) {
    if (!ctx || !hos_call_guest || !str) return;
    uint64_t gmd = GetGameDataMgr();
    if (!gmd) return;

    uint64_t scratch = GetScratchGAddr();
    if (!scratch) return;

    uint64_t str_gaddr = scratch + 0x400;
    char* str_host = (char*)(GUEST_RAM_BASE + str_gaddr);
    strncpy(str_host, str, 63);
    str_host[63] = '\0';
    int32_t len = (int32_t)strlen(str_host);

    // SafeString struct: +0x00: char* c_str, +0x08: int64_t len
    uint64_t ss_gaddr = scratch + 0x380;
    uint64_t* ss_host = (uint64_t*)(GUEST_RAM_BASE + ss_gaddr);
    ss_host[0] = str_gaddr;
    ss_host[1] = (uint64_t)len;

    SafeCallGuest(ctx, 0x83779370ULL, gmd, ss_gaddr, hash, 0xffffffffULL);
}

static void TriggerWarp(uint64_t* ctx, const char* banc_name) {
    if (!ctx || !banc_name) return;
    uint64_t gmd = GetGameDataMgr();
    if (!gmd) {
        OverlayLog("[overlay] TriggerWarp skipped: GameDataMgr not available\n");
        return;
    }
    // 0xa77aecee is hash for Sequence_ReservedNextBanc
    SetGameDataString(ctx, 0xa77aecee, banc_name);
    // 0x3fc35576 is hash for IsRequestedResetGimmick
    SetGameDataInt(ctx, 0x3fc35576, 1);
    OverlayLog("[overlay] Triggered warp to %s\n", banc_name);
}

static void DirectSetStats(int hearts, int magic, int rupees) {
    (void)magic;
    (void)rupees;
    uint64_t pmgr = GetPlayerMgr();
    if (pmgr) {
        uint64_t* status_ptr = (uint64_t*)(GUEST_RAM_BASE + pmgr + 0x110);
        if (status_ptr && *status_ptr) {
            uint32_t* status = (uint32_t*)(GUEST_RAM_BASE + *status_ptr);
            if (hearts > 0) {
                status[0x40 / 4] = hearts * 4; // Max HP (quarter hearts)
                status[0x48 / 4] = hearts * 4; // Current HP
            }
        }
    }
}

// Forward declarations
#include "param_tuner.inc"

static void ProcessGuestFrame(uint64_t* ctx);
static void Hooked_nvnQueuePresentTexture(uint64_t* ctx, void* ud);

static int FindHookshotBaseCallback(struct dl_phdr_info *info, size_t, void*) {
    if (info->dlpi_name == nullptr || info->dlpi_name[0] == '\0' || strstr(info->dlpi_name, "hookshot")) {
        if (info->dlpi_addr != 0) {
            g_HookshotBase = info->dlpi_addr;
            return 1;
        }
    }
    return 0;
}

static void InitHookshotBridge() {
    if (g_HookshotBase) return;
    dl_iterate_phdr(FindHookshotBaseCallback, nullptr);
    if (!g_HookshotBase) return;

    // Resolved by name from Hookshot's symbol table (the fixed offsets of v2.0.3 broke with every update).
    hos_call_guest = (hos_call_guest_fn)ResolveHostSymbol("hos_call_guest");
    hos_ctx = (hos_ctx_fn)ResolveHostSymbol("hos_ctx");
    hos_sys_alloc = (hos_sys_alloc_fn)ResolveHostSymbol("hos_sys_alloc");
    if (!g_HosPosixKeys) g_HosPosixKeys = (uint8_t*)ResolveHostSymbol("hos_posix_keys");
    // The host-call table has no exported name: read it from hos_hostcall_target's code, which loads
    // `movslq ncalls(%rip),%rdi` (48 63 3d disp32) and `lea calls(%rip),%rdi` (48 8d 3d disp32).
    if (uintptr_t f = ResolveHostSymbol("hos_hostcall_target")) {
        const uint8_t* c = (const uint8_t*)f;
        for (int i = 0; i < 96 && !(g_HosNCalls && g_HosCalls); i++) {
            if (c[i] != 0x48 || c[i + 2] != 0x3d) continue;
            int32_t d; memcpy(&d, c + i + 3, 4);
            uintptr_t t = f + i + 7 + (intptr_t)d;
            if (c[i + 1] == 0x63 && !g_HosNCalls) g_HosNCalls = (int32_t*)t;
            else if (c[i + 1] == 0x8d && !g_HosCalls) g_HosCalls = (HosHostCall*)t;
        }
    }
    if (!hos_call_guest || !hos_ctx || !hos_sys_alloc || !g_HosPosixKeys || !g_HosNCalls || !g_HosCalls)
        OverlayLog("[overlay] bridge: missing symbols (call_guest %p ctx %p sys_alloc %p keys %p ncalls %p calls %p)\n",
                   (void*)hos_call_guest, (void*)hos_ctx, (void*)hos_sys_alloc, (void*)g_HosPosixKeys,
                   (void*)g_HosNCalls, (void*)g_HosCalls);

    OverlayLog("[overlay] Initialized Hookshot bridge: base=0x%lx calls=%p sys_alloc=%p\n",
               (unsigned long)g_HookshotBase, (void*)g_HosCalls, (void*)hos_sys_alloc);
}

static void HookHostCalls() {
    if (!g_HosCalls || !g_HosNCalls) return;
    int32_t n = *g_HosNCalls;
    if (n <= 0 || n > 10000) return;

    for (int32_t i = 0; i < n; i++) {
        if (strcmp(g_HosCalls[i].name, "nvnQueuePresentTexture") == 0) {
            if (g_HosCalls[i].fn != Hooked_nvnQueuePresentTexture) {
                real_nvnQueuePresentTexture = g_HosCalls[i].fn;
                g_HosCalls[i].fn = Hooked_nvnQueuePresentTexture;
                OverlayLog("[overlay] Hooked nvnQueuePresentTexture in calls[%d]\n", i);
            }
        }
    }
}

// -------------------------------------------------------------
// Playground Cheats & Action Handlers
// -------------------------------------------------------------
static bool g_IsGoronTransformed = false;
static bool g_InfHealth = false;
static bool g_InfMagic = false;
static bool g_InfAmmo = false;
static bool g_AutoTransformOnChildLoad = false;
static int g_SelectedHearts = 20;
static int g_SelectedMagic = 64;
static int g_SelectedRupees = 999;
static std::string g_StatusMessage = "Ready. Press [F2] anytime in-game to open/close this menu.";

static void ExecuteQueuedAction(uint64_t* ctx, const QueuedAction& act) {
    if (!ctx) return;

    uint64_t gmd = GetGameDataMgr();
    uint64_t pmgr = GetPlayerMgr();
    OverlayLog("[overlay] ExecuteQueuedAction: type=%d (gmd=0x%lx pmgr=0x%lx)\n",
               (int)act.type, (unsigned long)gmd, (unsigned long)pmgr);

    switch (act.type) {
        case ActionType::GrantMasterUnlock: {
            OverlayLog("[overlay] Executing GrantMasterUnlock\n");
            // Swords & Shields
            PushItemToPouch(ctx, "Weapon_Sword_Kokiri");
            PushItemToPouch(ctx, "Weapon_Sword_Master");
            PushItemToPouch(ctx, "Weapon_Sword_Biggoron");
            PushItemToPouch(ctx, "Weapon_Sword_Giant");
            PushItemToPouch(ctx, "Weapon_Shield_Deku");
            PushItemToPouch(ctx, "Weapon_Shield_Hylian");
            PushItemToPouch(ctx, "Weapon_Shield_Mirror");

            // Tunics & Boots & Gauntlets
            PushItemToPouch(ctx, "Armor_Adult_Goron");
            PushItemToPouch(ctx, "Armor_Adult_Zora");
            PushItemToPouch(ctx, "Armor_Adult_HeavyBoot");
            PushItemToPouch(ctx, "Armor_Adult_HoverBoot");
            PushItemToPouch(ctx, "Armor_Child_Gauntlet_Goron");
            PushItemToPouch(ctx, "Armor_Adult_Gauntlet_Silver");
            PushItemToPouch(ctx, "Armor_Adult_Gauntlet_Gold");

            // Masks (including Goron Transformation Mask!)
            PushItemToPouch(ctx, "Item_Mask_Goron");
            PushItemToPouch(ctx, "Armor_Child_Mask_Goron");
            PushItemToPouch(ctx, "Item_Mask_Keaton");
            PushItemToPouch(ctx, "Armor_Child_Mask_Keaton");
            PushItemToPouch(ctx, "Item_Mask_Skull");
            PushItemToPouch(ctx, "Armor_Child_Mask_Skull");
            PushItemToPouch(ctx, "Item_Mask_Spooky");
            PushItemToPouch(ctx, "Armor_Child_Mask_Spooky");
            PushItemToPouch(ctx, "Item_Mask_Bunny");
            PushItemToPouch(ctx, "Armor_Child_Mask_Bunny");
            PushItemToPouch(ctx, "Item_Mask_Gerudo");
            PushItemToPouch(ctx, "Armor_Child_Mask_Gerudo");
            PushItemToPouch(ctx, "Item_Mask_Zora");
            PushItemToPouch(ctx, "Armor_Child_Mask_Zora");
            PushItemToPouch(ctx, "Item_Mask_Truth");
            PushItemToPouch(ctx, "Armor_Child_Mask_Truth");

            // Explorer Tools & Magic Spells
            PushItemToPouch(ctx, "Hookshot");
            PushItemToPouch(ctx, "Longshot");
            PushItemToPouch(ctx, "Weapon_Range_Bow");
            PushItemToPouch(ctx, "Weapon_Hammer");
            PushItemToPouch(ctx, "Item_Bomb");
            PushItemToPouch(ctx, "Item_Bombchu");
            PushItemToPouch(ctx, "Item_Boomerang");
            PushItemToPouch(ctx, "Item_DekuNut");
            PushItemToPouch(ctx, "Item_DekuStick");
            PushItemToPouch(ctx, "Item_Ocarina_Time");
            PushItemToPouch(ctx, "Item_Scale_Gold");
            PushItemToPouch(ctx, "Item_Wallet_Large");
            PushItemToPouch(ctx, "Item_Quiver_Large");
            PushItemToPouch(ctx, "Item_BombBag_Large");
            PushItemToPouch(ctx, "Item_BulletBag_Large");
            PushItemToPouch(ctx, "Item_StickBag_Large");
            PushItemToPouch(ctx, "Item_NutBag_Large");
            PushItemToPouch(ctx, "Item_Token_Gerudo");
            PushItemToPouch(ctx, "Item_Token_Gold");
            PushItemToPouch(ctx, "Item_AgonyStone");
            PushItemToPouch(ctx, "Item_Arrow_Fire");
            PushItemToPouch(ctx, "Item_Arrow_Ice");
            PushItemToPouch(ctx, "Item_Arrow_Light");
            PushItemToPouch(ctx, "DinFire");
            PushItemToPouch(ctx, "NayruLove");

            // 4 Bottles
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");

            // All 12 Songs
            PushItemToPouch(ctx, "Item_Melody_ZeldaLullaby");
            PushItemToPouch(ctx, "Item_Melody_EponaSong");
            PushItemToPouch(ctx, "Item_Melody_SariaSong");
            PushItemToPouch(ctx, "Item_Melody_SunSong");
            PushItemToPouch(ctx, "Item_Melody_TimeSong");
            PushItemToPouch(ctx, "Item_Melody_StormSong");
            PushItemToPouch(ctx, "Item_Melody_ForestMinuet");
            PushItemToPouch(ctx, "Item_Melody_FireBolero");
            PushItemToPouch(ctx, "Item_Melody_WaterSerenade");
            PushItemToPouch(ctx, "Item_Melody_ShadowNocturne");
            PushItemToPouch(ctx, "Item_Melody_SpiritRequiem");
            PushItemToPouch(ctx, "Item_Melody_LightPrelude");

            // Medallions & Spiritual Stones
            PushItemToPouch(ctx, "Item_Medal_Forest");
            PushItemToPouch(ctx, "Item_Medal_Fire");
            PushItemToPouch(ctx, "Item_Medal_Water");
            PushItemToPouch(ctx, "Item_Medal_Shadow");
            PushItemToPouch(ctx, "Item_Medal_Spirit");
            PushItemToPouch(ctx, "Item_Medal_Light");
            PushItemToPouch(ctx, "Item_Stone_Forest");
            PushItemToPouch(ctx, "Item_Stone_Fire");
            PushItemToPouch(ctx, "Item_Stone_Water");

            EquipItemDirect(ctx, 4, nullptr);
            SyncEquipment(ctx);

            // Stats
            SetGameDataInt(ctx, 0xaf7068e9, 80); // MaxLife = 20 hearts
            SetGameDataInt(ctx, 0x0355c603, 80); // Life
            SetGameDataInt(ctx, 0x39e8a20d, 64); // MPMax
            SetGameDataInt(ctx, 0xcbca2f4d, 64); // MP
            SetGameDataInt(ctx, 0x0c4fb411, 1);  // MPEconomy
            SetGameDataInt(ctx, 0xc995bbb8, 999); // CurrentRupee

            FullHeal(ctx);
            DirectSetStats(20, 64, 999);
            break;
        }

        case ActionType::GrantSwordsShields: {
            OverlayLog("[overlay] Executing GrantSwordsShields\n");
            PushItemToPouch(ctx, "Weapon_Sword_Kokiri");
            PushItemToPouch(ctx, "Weapon_Sword_Master");
            PushItemToPouch(ctx, "Weapon_Sword_Biggoron");
            PushItemToPouch(ctx, "Weapon_Sword_Giant");
            PushItemToPouch(ctx, "Weapon_Shield_Deku");
            PushItemToPouch(ctx, "Weapon_Shield_Hylian");
            PushItemToPouch(ctx, "Weapon_Shield_Mirror");
            SyncEquipment(ctx);
            break;
        }

        case ActionType::GrantTunicsBoots: {
            OverlayLog("[overlay] Executing GrantTunicsBoots\n");
            PushItemToPouch(ctx, "Armor_Adult_Goron");
            PushItemToPouch(ctx, "Armor_Adult_Zora");
            PushItemToPouch(ctx, "Armor_Adult_HeavyBoot");
            PushItemToPouch(ctx, "Armor_Adult_HoverBoot");
            PushItemToPouch(ctx, "Armor_Child_Gauntlet_Goron");
            PushItemToPouch(ctx, "Armor_Adult_Gauntlet_Silver");
            PushItemToPouch(ctx, "Armor_Adult_Gauntlet_Gold");
            SyncEquipment(ctx);
            break;
        }

        case ActionType::GrantMasks: {
            OverlayLog("[overlay] Executing GrantMasks\n");
            PushItemToPouch(ctx, "Item_Mask_Goron");
            PushItemToPouch(ctx, "Armor_Child_Mask_Goron");
            PushItemToPouch(ctx, "Item_Mask_Keaton");
            PushItemToPouch(ctx, "Armor_Child_Mask_Keaton");
            PushItemToPouch(ctx, "Item_Mask_Skull");
            PushItemToPouch(ctx, "Armor_Child_Mask_Skull");
            PushItemToPouch(ctx, "Item_Mask_Spooky");
            PushItemToPouch(ctx, "Armor_Child_Mask_Spooky");
            PushItemToPouch(ctx, "Item_Mask_Bunny");
            PushItemToPouch(ctx, "Armor_Child_Mask_Bunny");
            PushItemToPouch(ctx, "Item_Mask_Gerudo");
            PushItemToPouch(ctx, "Armor_Child_Mask_Gerudo");
            PushItemToPouch(ctx, "Item_Mask_Zora");
            PushItemToPouch(ctx, "Armor_Child_Mask_Zora");
            PushItemToPouch(ctx, "Item_Mask_Truth");
            PushItemToPouch(ctx, "Armor_Child_Mask_Truth");
            EquipItemDirect(ctx, 4, nullptr);
            SyncEquipment(ctx);
            break;
        }

        case ActionType::GrantTools: {
            OverlayLog("[overlay] Executing GrantTools\n");
            PushItemToPouch(ctx, "Hookshot");
            PushItemToPouch(ctx, "Longshot");
            PushItemToPouch(ctx, "Weapon_Range_Bow");
            PushItemToPouch(ctx, "Weapon_Hammer");
            PushItemToPouch(ctx, "Item_Bomb");
            PushItemToPouch(ctx, "Item_Bombchu");
            PushItemToPouch(ctx, "Item_Boomerang");
            PushItemToPouch(ctx, "Item_DekuNut");
            PushItemToPouch(ctx, "Item_DekuStick");
            PushItemToPouch(ctx, "Item_Ocarina_Time");
            PushItemToPouch(ctx, "Item_Scale_Gold");
            PushItemToPouch(ctx, "Item_Wallet_Large");
            PushItemToPouch(ctx, "Item_Quiver_Large");
            PushItemToPouch(ctx, "Item_BombBag_Large");
            PushItemToPouch(ctx, "Item_BulletBag_Large");
            PushItemToPouch(ctx, "Item_StickBag_Large");
            PushItemToPouch(ctx, "Item_NutBag_Large");
            PushItemToPouch(ctx, "Item_Token_Gerudo");
            PushItemToPouch(ctx, "Item_Token_Gold");
            PushItemToPouch(ctx, "Item_AgonyStone");
            PushItemToPouch(ctx, "Item_Arrow_Fire");
            PushItemToPouch(ctx, "Item_Arrow_Ice");
            PushItemToPouch(ctx, "Item_Arrow_Light");
            PushItemToPouch(ctx, "DinFire");
            PushItemToPouch(ctx, "NayruLove");
            SyncEquipment(ctx);
            break;
        }

        case ActionType::GrantBottles: {
            OverlayLog("[overlay] Executing GrantBottles\n");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            PushItemToPouch(ctx, "Item_GetDemo_Bottle");
            SyncEquipment(ctx);
            break;
        }

        case ActionType::GrantSongs: {
            OverlayLog("[overlay] Executing GrantSongs\n");
            PushItemToPouch(ctx, "Item_Melody_ZeldaLullaby");
            PushItemToPouch(ctx, "Item_Melody_EponaSong");
            PushItemToPouch(ctx, "Item_Melody_SariaSong");
            PushItemToPouch(ctx, "Item_Melody_SunSong");
            PushItemToPouch(ctx, "Item_Melody_TimeSong");
            PushItemToPouch(ctx, "Item_Melody_StormSong");
            PushItemToPouch(ctx, "Item_Melody_ForestMinuet");
            PushItemToPouch(ctx, "Item_Melody_FireBolero");
            PushItemToPouch(ctx, "Item_Melody_WaterSerenade");
            PushItemToPouch(ctx, "Item_Melody_ShadowNocturne");
            PushItemToPouch(ctx, "Item_Melody_SpiritRequiem");
            PushItemToPouch(ctx, "Item_Melody_LightPrelude");

            PushItemToPouch(ctx, "Item_Medal_Forest");
            PushItemToPouch(ctx, "Item_Medal_Fire");
            PushItemToPouch(ctx, "Item_Medal_Water");
            PushItemToPouch(ctx, "Item_Medal_Shadow");
            PushItemToPouch(ctx, "Item_Medal_Spirit");
            PushItemToPouch(ctx, "Item_Medal_Light");
            PushItemToPouch(ctx, "Item_Stone_Forest");
            PushItemToPouch(ctx, "Item_Stone_Fire");
            PushItemToPouch(ctx, "Item_Stone_Water");
            SyncEquipment(ctx);
            break;
        }

        case ActionType::SetHearts: {
            int hp = act.int_val * 4;
            OverlayLog("[overlay] Executing SetHearts: hearts=%d hp=%d\n", act.int_val, hp);
            SetGameDataInt(ctx, 0xaf7068e9, hp); // MaxLife
            SetGameDataInt(ctx, 0x0355c603, hp); // Life
            DirectSetStats(act.int_val, 0, 0);
            break;
        }

        case ActionType::FullHeal: {
            OverlayLog("[overlay] Executing FullHeal\n");
            FullHeal(ctx);
            DirectSetStats(g_SelectedHearts, 0, 0);
            break;
        }

        case ActionType::SetMagic: {
            OverlayLog("[overlay] Executing SetMagic: magic=%d\n", act.int_val);
            SetGameDataInt(ctx, 0x39e8a20d, act.int_val); // MPMax
            SetGameDataInt(ctx, 0xcbca2f4d, act.int_val); // MP
            SetGameDataInt(ctx, 0x0c4fb411, 1);           // MPEconomy
            DirectSetStats(0, act.int_val, 0);
            break;
        }

        case ActionType::SetRupees: {
            OverlayLog("[overlay] Executing SetRupees: rupees=%d\n", act.int_val);
            PushItemToPouch(ctx, "Item_Wallet_Large");
            SetGameDataInt(ctx, 0xc995bbb8, act.int_val); // CurrentRupee
            DirectSetStats(0, 0, act.int_val);
            break;
        }

        case ActionType::SwitchAgeChild: {
            OverlayLog("[overlay] Executing SwitchAgeChild\n");
            TriggerWarp(ctx, "KokiriForest_Child");
            break;
        }

        case ActionType::SwitchAgeAdult: {
            OverlayLog("[overlay] Executing SwitchAgeAdult\n");
            TriggerWarp(ctx, "KokiriForest_Adult");
            break;
        }

        case ActionType::Warp: {
            OverlayLog("[overlay] Executing Warp to %s\n", act.str_val.c_str());
            TriggerWarp(ctx, act.str_val.c_str());
            break;
        }

        case ActionType::TransformGoron: {
            OverlayLog("[overlay] Executing TransformGoron\n");
            g_IsGoronTransformed = true;

            bool is_child = IsChildForm(ctx);
            OverlayLog("[overlay] TransformGoron: active form is_child=%d\n", is_child);

            // Give Goron transformation items to pouch
            PushItemToPouch(ctx, "Item_Mask_Goron");
            PushItemToPouch(ctx, "Armor_Child_Mask_Goron");
            PushItemToPouch(ctx, "Armor_Adult_Goron");

            // Unequip weapon and shield so Goron Link fights with fists and rolls freely
            EquipItemDirect(ctx, 0, nullptr); // Sword
            EquipItemDirect(ctx, 1, nullptr); // Shield

            // Equip Goron Mask in slot 4 (renders authentic MM Goron Link 3D model & hides Link body)
            EquipItemDirect(ctx, 4, nullptr);
            bool ok = EquipItemDirect(ctx, 4, "Armor_Child_Mask_Goron");

            if (!is_child) {
                // If Adult Link, also equip Goron armor in slot 3 to hide adult tunic materials
                EquipItemDirect(ctx, 3, "Armor_Adult_Goron");
            }
            OverlayLog("[overlay] TransformGoron: equip result=%d\n", ok);

            SyncEquipment(ctx);
            SetGoronSpeedBuff(ctx, true);

            g_StatusMessage = "✦ TRANSFORMED INTO GORON LINK ✦ (Goron character model, rolling, punches & ground pound active!)";
            break;
        }

        case ActionType::RevertHylian: {
            OverlayLog("[overlay] Executing RevertHylian\n");
            g_IsGoronTransformed = false;
            g_AutoTransformOnChildLoad = false;

            bool is_child = IsChildForm(ctx);
            if (is_child) {
                EquipItemDirect(ctx, 4, nullptr); // Remove Goron mask
                PushItemToPouch(ctx, "Weapon_Sword_Kokiri");
                PushItemToPouch(ctx, "Weapon_Shield_Deku");
                EquipItemDirect(ctx, 0, "Weapon_Sword_Kokiri");
                EquipItemDirect(ctx, 1, "Weapon_Shield_Deku");
            } else {
                PushItemToPouch(ctx, "Armor_Adult_Normal");
                PushItemToPouch(ctx, "Weapon_Sword_Master");
                PushItemToPouch(ctx, "Weapon_Shield_Hylian");
                EquipItemDirect(ctx, 3, "Armor_Adult_Normal"); // Restore green Kokiri tunic
                EquipItemDirect(ctx, 4, nullptr);
                EquipItemDirect(ctx, 0, "Weapon_Sword_Master"); // Restore Master Sword
                EquipItemDirect(ctx, 1, "Weapon_Shield_Hylian"); // Restore Hylian Shield
            }

            SyncEquipment(ctx);
            SetGoronSpeedBuff(ctx, false);
            g_StatusMessage = "Reverted to normal Hylian Link form.";
            break;
        }
    }
}

// -------------------------------------------------------------
// Majora's Mask Goron Link State Machine & Physics Engine
// -------------------------------------------------------------
enum class GoronAction {
    Standing,
    Curling,
    RollingNormal,
    RollingSpikes,
    Punch1,
    Punch2,
    Punch3,
    GroundPoundHover,
    GroundPoundFall,
    GroundPoundLand
};

struct PlayerPosRef {
    float* x = nullptr;
    float* y = nullptr;
    float* z = nullptr;
};

static GoronAction g_GoronAction = GoronAction::Standing;
static float g_GoronSpeed = 0.0f;
static float g_GoronRollTimer = 0.0f;
static float g_GoronActionTimer = 0.0f;
static float g_GoronFacingYaw = 0.0f;
static int g_GoronPunchCombo = 0;
static float g_GoronLastGroundY = 0.0f;
static float g_GoronMagicDrainTimer = 0.0f;

// Input states captured from SDL3
static bool g_KeyRollHeld = false;
static bool g_KeyPunchPressed = false;
static float g_KeyStickX = 0.0f;
static float g_KeyStickY = 0.0f;
static bool g_GamepadRollHeld = false;
static bool g_GamepadPunchPressed = false;
static float g_GamepadStickX = 0.0f;
static float g_GamepadStickY = 0.0f;
static bool g_MousePunchPressed = false;

static PlayerPosRef GetPlayerPositionPtrs() {
    PlayerPosRef res;
    uint64_t equip_comp = GetEquipComp();
    if (!equip_comp) return res;

    uint64_t* player_actor_ptr = (uint64_t*)(GUEST_RAM_BASE + equip_comp + 0x18);
    if (!player_actor_ptr || !IsAddressReadable(player_actor_ptr, 8) || !*player_actor_ptr) return res;
    uint64_t player_actor = *player_actor_ptr;

    uint64_t* c1_ptr = (uint64_t*)(GUEST_RAM_BASE + player_actor + 0x8);
    if (!c1_ptr || !IsAddressReadable(c1_ptr, 8) || !*c1_ptr) return res;
    uint64_t c1 = *c1_ptr;

    uint64_t* c2_ptr = (uint64_t*)(GUEST_RAM_BASE + c1 + 0x8);
    if (!c2_ptr || !IsAddressReadable(c2_ptr, 8) || !*c2_ptr) return res;
    uint64_t c2 = *c2_ptr;

    uint64_t* c3_ptr = (uint64_t*)(GUEST_RAM_BASE + c2 + 0x1f0);
    if (!c3_ptr || !IsAddressReadable(c3_ptr, 8) || !*c3_ptr) return res;
    uint64_t c3 = *c3_ptr;

    float* pos_ptr = (float*)(GUEST_RAM_BASE + c3 + 0x31C);
    if (!pos_ptr || !IsAddressReadable(pos_ptr, 12)) return res;

    res.x = &pos_ptr[0];
    res.y = &pos_ptr[1];
    res.z = &pos_ptr[2];
    return res;
}

static void UpdateGoronLinkState(uint64_t* ctx, float dt) {
    if (!ctx || !g_IsGoronTransformed) {
        g_GoronAction = GoronAction::Standing;
        g_GoronSpeed = 0.0f;
        return;
    }

    bool roll_input = g_KeyRollHeld || g_GamepadRollHeld;
    bool punch_input = g_KeyPunchPressed || g_GamepadPunchPressed || g_MousePunchPressed;
    g_KeyPunchPressed = false;
    g_GamepadPunchPressed = false;
    g_MousePunchPressed = false;

    float stick_x = (std::fabs(g_GamepadStickX) > 0.15f) ? g_GamepadStickX : g_KeyStickX;
    float stick_y = (std::fabs(g_GamepadStickY) > 0.15f) ? -g_GamepadStickY : g_KeyStickY;
    float stick_mag = std::sqrt(stick_x * stick_x + stick_y * stick_y);
    if (stick_mag > 1.0f) {
        stick_x /= stick_mag;
        stick_y /= stick_mag;
        stick_mag = 1.0f;
    }

    PlayerPosRef pos = GetPlayerPositionPtrs();
    if (pos.y) {
        if (g_GoronLastGroundY == 0.0f || (g_GoronAction == GoronAction::Standing && *pos.y < g_GoronLastGroundY + 2.0f)) {
            g_GoronLastGroundY = *pos.y;
        }
    }

    switch (g_GoronAction) {
        case GoronAction::Standing: {
            g_GoronSpeed = 0.0f;
            g_GoronRollTimer = 0.0f;

            if (roll_input) {
                g_GoronAction = GoronAction::Curling;
                g_GoronRollTimer = 0.0f;
                OverlayLog("[goron] Curling initiated\n");
            } else if (punch_input) {
                g_GoronAction = GoronAction::Punch1;
                g_GoronActionTimer = 0.0f;
                g_GoronPunchCombo = 1;
                OverlayLog("[goron] Punch 1 (Right Hook) triggered\n");
            }
            break;
        }

        case GoronAction::Curling:
        case GoronAction::RollingNormal:
        case GoronAction::RollingSpikes: {
            if (!roll_input) {
                g_GoronSpeed = std::max(0.0f, g_GoronSpeed - 24.0f * dt);
                if (g_GoronSpeed <= 0.5f) {
                    g_GoronAction = GoronAction::Standing;
                    OverlayLog("[goron] Roll finished, standing\n");
                    break;
                }
            } else {
                if (stick_mag > 0.15f) {
                    float target_yaw = std::atan2(stick_x, stick_y);
                    float diff = target_yaw - g_GoronFacingYaw;
                    while (diff > 3.14159265f) diff -= 6.2831853f;
                    while (diff < -3.14159265f) diff += 6.2831853f;
                    g_GoronFacingYaw += diff * std::min(1.0f, 9.0f * dt);

                    if (g_GoronAction == GoronAction::Curling) {
                        g_GoronAction = GoronAction::RollingNormal;
                    }

                    if (g_GoronAction == GoronAction::RollingNormal) {
                        g_GoronSpeed = std::min(14.0f, g_GoronSpeed + 15.0f * dt);
                        if (g_GoronSpeed >= 11.0f) {
                            g_GoronRollTimer += dt;
                            if (g_GoronRollTimer >= 1.0f && g_SelectedMagic > 0) {
                                g_GoronAction = GoronAction::RollingSpikes;
                                OverlayLog("[goron] SPIKES ACTIVATED! 24.0 m/s roll engaged\n");
                            }
                        } else {
                            g_GoronRollTimer = std::max(0.0f, g_GoronRollTimer - dt * 0.5f);
                        }
                    } else if (g_GoronAction == GoronAction::RollingSpikes) {
                        g_GoronSpeed = std::min(24.0f, g_GoronSpeed + 22.0f * dt);

                        // Magic drain rate (5 MP/sec, exact to MM)
                        g_GoronMagicDrainTimer += dt;
                        if (g_GoronMagicDrainTimer >= 0.20f) {
                            g_GoronMagicDrainTimer = 0.0f;
                            if (g_SelectedMagic > 0) {
                                g_SelectedMagic--;
                                SetGameDataInt(ctx, 0xcbca2f4d, g_SelectedMagic);
                                DirectSetStats(0, g_SelectedMagic, 0);
                            } else {
                                g_GoronAction = GoronAction::RollingNormal;
                                g_GoronSpeed = std::min(g_GoronSpeed, 14.0f);
                                OverlayLog("[goron] Magic depleted -> spikes retracted\n");
                            }
                        }
                    }
                } else {
                    if (g_GoronSpeed > 0.0f) {
                        g_GoronSpeed = std::max(0.0f, g_GoronSpeed - 10.0f * dt);
                    }
                }
            }

            // In-air Ground Pound trigger
            if (punch_input) {
                g_GoronAction = GoronAction::GroundPoundHover;
                g_GoronActionTimer = 0.0f;
                g_GoronSpeed = 0.0f;
                OverlayLog("[goron] Ground Pound Hover triggered\n");
                break;
            }

            // Apply forward velocity in Link's facing direction
            if (pos.x && pos.z && g_GoronSpeed > 0.1f) {
                *pos.x += std::sin(g_GoronFacingYaw) * g_GoronSpeed * dt;
                *pos.z += std::cos(g_GoronFacingYaw) * g_GoronSpeed * dt;
            }
            break;
        }

        case GoronAction::Punch1:
        case GoronAction::Punch2:
        case GoronAction::Punch3: {
            g_GoronActionTimer += dt;

            // Step forward momentum
            if (pos.x && pos.z && g_GoronActionTimer < 0.20f) {
                float step_spd = (g_GoronAction == GoronAction::Punch3) ? 7.0f : 3.5f;
                *pos.x += std::sin(g_GoronFacingYaw) * step_spd * dt;
                *pos.z += std::cos(g_GoronFacingYaw) * step_spd * dt;
            }

            // Chain next punch
            if (punch_input && g_GoronActionTimer > 0.12f && g_GoronActionTimer < 0.45f) {
                if (g_GoronAction == GoronAction::Punch1) {
                    g_GoronAction = GoronAction::Punch2;
                    g_GoronActionTimer = 0.0f;
                    g_GoronPunchCombo = 2;
                    OverlayLog("[goron] Punch 2 (Left Hook) triggered\n");
                } else if (g_GoronAction == GoronAction::Punch2) {
                    g_GoronAction = GoronAction::Punch3;
                    g_GoronActionTimer = 0.0f;
                    g_GoronPunchCombo = 3;
                    OverlayLog("[goron] Punch 3 (Double-Fist Ground Slam) triggered\n");
                }
            } else if (g_GoronActionTimer >= 0.55f) {
                g_GoronAction = GoronAction::Standing;
                g_GoronPunchCombo = 0;
            }
            break;
        }

        case GoronAction::GroundPoundHover: {
            g_GoronActionTimer += dt;
            if (g_GoronActionTimer >= 0.25f) {
                g_GoronAction = GoronAction::GroundPoundFall;
                g_GoronActionTimer = 0.0f;
                OverlayLog("[goron] Ground Pound falling\n");
            }
            break;
        }

        case GoronAction::GroundPoundFall: {
            g_GoronActionTimer += dt;
            if (pos.y) {
                *pos.y -= 35.0f * dt;
                if (*pos.y <= g_GoronLastGroundY || g_GoronActionTimer >= 1.5f) {
                    *pos.y = g_GoronLastGroundY;
                    g_GoronAction = GoronAction::GroundPoundLand;
                    g_GoronActionTimer = 0.0f;
                    OverlayLog("[goron] Ground Pound Landed! Shockwave created!\n");
                }
            } else {
                if (g_GoronActionTimer >= 0.6f) {
                    g_GoronAction = GoronAction::GroundPoundLand;
                    g_GoronActionTimer = 0.0f;
                }
            }
            break;
        }

        case GoronAction::GroundPoundLand: {
            g_GoronActionTimer += dt;
            if (g_GoronActionTimer >= 0.40f) {
                g_GoronAction = GoronAction::Standing;
            }
            break;
        }
    }
}

static void ProcessGuestFrame(uint64_t* ctx) {
    if (!ctx) return;
    tuner::PollCommands();

    // Drain queued actions
    std::vector<QueuedAction> actions;
    {
        std::lock_guard<std::mutex> lock(g_ActionMutex);
        if (!g_ActionQueue.empty()) {
            actions.swap(g_ActionQueue);
        }
    }

    for (const auto& act : actions) {
        ExecuteQueuedAction(ctx, act);
    }

    // Maintain Goron transformation speed & mechanics if active
    static auto s_LastFrameTime = std::chrono::steady_clock::now();
    auto cur_time = std::chrono::steady_clock::now();
    float dt = std::chrono::duration<float>(cur_time - s_LastFrameTime).count();
    s_LastFrameTime = cur_time;
    if (dt > 0.1f) dt = 0.1f;

    if (g_IsGoronTransformed) {
        UpdateGoronLinkState(ctx, dt);
    }

    // Auto-transform on scene load if requested
    if (g_AutoTransformOnChildLoad) {
        uint64_t equip_comp = GetEquipComp();
        if (equip_comp) {
            OverlayLog("[overlay] Scene loaded! Auto-transforming into Goron Link...\n");
            g_AutoTransformOnChildLoad = false;
            ExecuteQueuedAction(ctx, {ActionType::TransformGoron});
        }
    }

    // Apply per-frame trainer cheats if in-game
    uint64_t gmd = GetGameDataMgr();
    if (gmd) {
        if (g_InfHealth) {
            SetGameDataInt(ctx, 0x0355c603, g_SelectedHearts * 4); // Life = Max
            DirectSetStats(g_SelectedHearts, 0, 0);
        }
        if (g_InfMagic) {
            SetGameDataInt(ctx, 0xcbca2f4d, g_SelectedMagic);      // MP = Max
            SetGameDataInt(ctx, 0x0c4fb411, 1);
        }
        if (g_InfAmmo) {
            // Refill common consumable ammo
            PushItemToPouch(ctx, "Item_Quiver_Large");
            PushItemToPouch(ctx, "Item_BombBag_Large");
            PushItemToPouch(ctx, "Item_BulletBag_Large");
        }
    }
}

static void Hooked_nvnQueuePresentTexture(uint64_t* ctx, void* ud) {
    if (ctx) {
        ProcessGuestFrame(ctx);
    }
    if (real_nvnQueuePresentTexture) {
        real_nvnQueuePresentTexture(ctx, ud);
    }
}

// -------------------------------------------------------------
// Vulkan & SDL Hooks
// -------------------------------------------------------------
static PFN_vkVoidFunction Hook_vkGetInstanceProcAddr(VkInstance instance, const char* pName);
static PFN_vkVoidFunction Hook_vkGetDeviceProcAddr(VkDevice device, const char* pName);
static VkResult Hook_vkCreateInstance(const VkInstanceCreateInfo* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkInstance* pInstance);
static void Hook_vkDestroyInstance(VkInstance instance, const VkAllocationCallbacks* pAllocator);
static VkResult Hook_vkCreateDevice(VkPhysicalDevice physicalDevice, const VkDeviceCreateInfo* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkDevice* pDevice);
static void Hook_vkDestroyDevice(VkDevice device, const VkAllocationCallbacks* pAllocator);
static VkResult Hook_vkCreateSwapchainKHR(VkDevice device, const VkSwapchainCreateInfoKHR* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkSwapchainKHR* pSwapchain);
static void Hook_vkDestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain, const VkAllocationCallbacks* pAllocator);
static VkResult Hook_vkQueuePresentKHR(VkQueue queue, const VkPresentInfoKHR* pPresentInfo);

static SDL_Window* Hook_SDL_CreateWindow(const char* title, int w, int h, SDL_WindowFlags flags);
static bool Hook_SDL_PollEvent(SDL_Event* event);
static bool Hook_SDL_WaitEventTimeout(SDL_Event* event, Sint32 timeoutMS);
static bool Hook_SDL_SetWindowRelativeMouseMode(SDL_Window* window, bool enabled);
static SDL_MouseButtonFlags Hook_SDL_GetMouseState(float* x, float* y);
static const bool* Hook_SDL_GetKeyboardState(int* numkeys);

// Real Vulkan dispatch functions
static void* g_VkLib = nullptr;
static PFN_vkGetInstanceProcAddr real_vkGetInstanceProcAddr = nullptr;
static PFN_vkGetDeviceProcAddr real_vkGetDeviceProcAddr = nullptr;
static PFN_vkQueuePresentKHR real_vkQueuePresentKHR = nullptr;
static PFN_vkCreateDevice real_vkCreateDevice = nullptr;
static PFN_vkDestroyDevice real_vkDestroyDevice = nullptr;
static PFN_vkCreateInstance real_vkCreateInstance = nullptr;
static PFN_vkDestroyInstance real_vkDestroyInstance = nullptr;
static PFN_vkCreateSwapchainKHR real_vkCreateSwapchainKHR = nullptr;
static PFN_vkDestroySwapchainKHR real_vkDestroySwapchainKHR = nullptr;

// SDL Real functions
static void* g_SdlLib = nullptr;
static bool (*real_SDL_PollEvent)(SDL_Event* event) = nullptr;
static bool (*real_SDL_WaitEventTimeout)(SDL_Event* event, Sint32 timeoutMS) = nullptr;
static SDL_Window* (*real_SDL_CreateWindow)(const char* title, int w, int h, SDL_WindowFlags flags) = nullptr;
static bool (*real_SDL_SetWindowRelativeMouseMode)(SDL_Window* window, bool enabled) = nullptr;
static SDL_MouseButtonFlags (*real_SDL_GetMouseState)(float* x, float* y) = nullptr;
static const bool* (*real_SDL_GetKeyboardState)(int* numkeys) = nullptr;

// dlsym dispatch
typedef void* (*dlsym_fn)(void*, const char*);
static dlsym_fn real_dlsym = nullptr;

static void EnsureRealDlsym() {
    if (!real_dlsym) {
        real_dlsym = (dlsym_fn)dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.34");
        if (!real_dlsym) {
            real_dlsym = (dlsym_fn)dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5");
        }
    }
}

// Global state
static std::mutex g_Mutex;
static SDL_Window* g_Window = nullptr;
static bool g_ShowOverlay = false;
static bool g_PrevOverlayState = false;
static bool g_GameWantsRelativeMouse = false;
static bool g_ImGuiInitialized = false;
static bool g_SDL3BackendInitialized = false;

static void ToggleOverlay(const char* source) {
    static auto s_LastToggle = std::chrono::steady_clock::now() - std::chrono::seconds(10);
    auto now = std::chrono::steady_clock::now();
    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - s_LastToggle).count();
    if (elapsed_ms < 150) return;
    s_LastToggle = now;
    g_ShowOverlay = !g_ShowOverlay;
    OverlayLog("[overlay] Toggled via %s -> g_ShowOverlay=%d\n", source ? source : "?", (int)g_ShowOverlay);
}

static VkInstance g_Instance = VK_NULL_HANDLE;
static VkPhysicalDevice g_PhysicalDevice = VK_NULL_HANDLE;
static VkDevice g_Device = VK_NULL_HANDLE;
static VkQueue g_Queue = VK_NULL_HANDLE;
static uint32_t g_QueueFamily = 0;
static VkCommandPool g_CommandPool = VK_NULL_HANDLE;
static VkFormat g_SwapchainFormat = VK_FORMAT_B8G8R8A8_UNORM;
static uint32_t g_Width = 1920;
static uint32_t g_Height = 1080;

struct SwapchainData {
    VkSwapchainKHR swapchain = VK_NULL_HANDLE;
    std::vector<VkImage> images;
    std::vector<VkImageView> views;
    std::vector<VkCommandBuffer> command_buffers;
    std::vector<VkFence> fences;
};
static SwapchainData g_Swapchain;

// Dynamic rendering function pointers
static PFN_vkCmdBeginRenderingKHR pfn_vkCmdBeginRendering = nullptr;
static PFN_vkCmdEndRenderingKHR pfn_vkCmdEndRendering = nullptr;

static void InitRealFunctions() {
    EnsureRealDlsym();
    InitHookshotBridge();
    HookHostCalls();

    if (!real_dlsym) return;

    if (!g_VkLib) {
        g_VkLib = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_NOLOAD);
        if (!g_VkLib) g_VkLib = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    }
    if (g_VkLib) {
        if (!real_vkGetInstanceProcAddr) {
            real_vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)real_dlsym(g_VkLib, "vkGetInstanceProcAddr");
        }
        if (!real_vkGetDeviceProcAddr) {
            real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_dlsym(g_VkLib, "vkGetDeviceProcAddr");
        }
        if (!real_vkCreateInstance && real_vkGetInstanceProcAddr) {
            real_vkCreateInstance = (PFN_vkCreateInstance)real_vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkCreateInstance");
        }
    }

    if (!g_SdlLib) {
        g_SdlLib = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_NOLOAD);
        if (!g_SdlLib) g_SdlLib = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_LOCAL);
    }
    if (g_SdlLib) {
        if (!real_SDL_PollEvent)
            real_SDL_PollEvent = (bool (*)(SDL_Event*))real_dlsym(g_SdlLib, "SDL_PollEvent");
        if (!real_SDL_WaitEventTimeout)
            real_SDL_WaitEventTimeout = (bool (*)(SDL_Event*, Sint32))real_dlsym(g_SdlLib, "SDL_WaitEventTimeout");
        if (!real_SDL_CreateWindow)
            real_SDL_CreateWindow = (SDL_Window* (*)(const char*, int, int, SDL_WindowFlags))real_dlsym(g_SdlLib, "SDL_CreateWindow");
        if (!real_SDL_SetWindowRelativeMouseMode)
            real_SDL_SetWindowRelativeMouseMode = (bool (*)(SDL_Window*, bool))real_dlsym(g_SdlLib, "SDL_SetWindowRelativeMouseMode");
        if (!real_SDL_GetMouseState)
            real_SDL_GetMouseState = (SDL_MouseButtonFlags (*)(float*, float*))real_dlsym(g_SdlLib, "SDL_GetMouseState");
        if (!real_SDL_GetKeyboardState)
            real_SDL_GetKeyboardState = (const bool* (*)(int*))real_dlsym(g_SdlLib, "SDL_GetKeyboardState");
    }

    if (!g_HosPosixKeys && real_dlsym) {
        g_HosPosixKeys = (uint8_t*)real_dlsym(RTLD_DEFAULT, "hos_posix_keys");
    }
}

static void UpdateMouseCursorMode() {
    if (!g_Window) {
        g_Window = SDL_GetKeyboardFocus();
        if (!g_Window) g_Window = SDL_GetMouseFocus();
    }
    if (g_Window && real_SDL_SetWindowRelativeMouseMode) {
        if (g_ShowOverlay) {
            real_SDL_SetWindowRelativeMouseMode(g_Window, false);
            SDL_ShowCursor();
        } else {
            real_SDL_SetWindowRelativeMouseMode(g_Window, g_GameWantsRelativeMouse);
        }
    }
}

static PFN_vkVoidFunction ImGuiVulkanLoader(const char* function_name, void* /*user_data*/) {
    if (!function_name) return nullptr;
    PFN_vkVoidFunction fn = nullptr;

    bool is_instance_fn =
        strcmp(function_name, "vkEnumeratePhysicalDevices") == 0 ||
        strcmp(function_name, "vkDestroySurfaceKHR") == 0 ||
        strncmp(function_name, "vkGetPhysicalDevice", 19) == 0;

    if (!is_instance_fn && g_Device && real_vkGetDeviceProcAddr) {
        fn = real_vkGetDeviceProcAddr(g_Device, function_name);
    }
    if (!fn && g_Instance && real_vkGetInstanceProcAddr) {
        fn = real_vkGetInstanceProcAddr(g_Instance, function_name);
    }
    if (!fn && strcmp(function_name, "vkCmdBeginRenderingKHR") == 0) {
        if (g_Device && real_vkGetDeviceProcAddr) fn = real_vkGetDeviceProcAddr(g_Device, "vkCmdBeginRendering");
        if (!fn && g_Instance && real_vkGetInstanceProcAddr) fn = real_vkGetInstanceProcAddr(g_Instance, "vkCmdBeginRendering");
    }
    if (!fn && strcmp(function_name, "vkCmdEndRenderingKHR") == 0) {
        if (g_Device && real_vkGetDeviceProcAddr) fn = real_vkGetDeviceProcAddr(g_Device, "vkCmdEndRendering");
        if (!fn && g_Instance && real_vkGetInstanceProcAddr) fn = real_vkGetInstanceProcAddr(g_Instance, "vkCmdEndRendering");
    }
    if (!fn && real_vkGetInstanceProcAddr) {
        fn = real_vkGetInstanceProcAddr(VK_NULL_HANDLE, function_name);
    }
    if (!fn && g_VkLib && real_dlsym) {
        fn = (PFN_vkVoidFunction)real_dlsym(g_VkLib, function_name);
    }
    return fn;
}

static void SetupImGuiStyle() {
    ImGuiStyle& style = ImGui::GetStyle();
    ImVec4* colors = style.Colors;

    style.WindowRounding = 8.0f;
    style.FrameRounding = 5.0f;
    style.PopupRounding = 6.0f;
    style.GrabRounding = 4.0f;
    style.TabRounding = 5.0f;
    style.WindowBorderSize = 1.5f;
    style.FrameBorderSize = 1.0f;
    style.ItemSpacing = ImVec2(10, 8);
    style.WindowPadding = ImVec2(14, 14);

    // Zelda Theme: Forest green & Triforce gold accents
    colors[ImGuiCol_Text]                  = ImVec4(0.96f, 0.96f, 0.94f, 1.00f);
    colors[ImGuiCol_WindowBg]              = ImVec4(0.08f, 0.12f, 0.09f, 0.94f);
    colors[ImGuiCol_Border]                = ImVec4(0.24f, 0.65f, 0.35f, 0.80f);
    colors[ImGuiCol_FrameBg]               = ImVec4(0.14f, 0.20f, 0.15f, 0.80f);
    colors[ImGuiCol_FrameBgHovered]        = ImVec4(0.20f, 0.35f, 0.22f, 0.85f);
    colors[ImGuiCol_FrameBgActive]         = ImVec4(0.25f, 0.45f, 0.28f, 0.90f);
    colors[ImGuiCol_TitleBg]               = ImVec4(0.07f, 0.16f, 0.09f, 1.00f);
    colors[ImGuiCol_TitleBgActive]         = ImVec4(0.10f, 0.28f, 0.14f, 1.00f);
    colors[ImGuiCol_Button]                = ImVec4(0.18f, 0.45f, 0.24f, 0.85f);
    colors[ImGuiCol_ButtonHovered]         = ImVec4(0.25f, 0.60f, 0.32f, 1.00f);
    colors[ImGuiCol_ButtonActive]          = ImVec4(0.85f, 0.70f, 0.10f, 1.00f);
    colors[ImGuiCol_Header]                = ImVec4(0.15f, 0.35f, 0.20f, 0.80f);
    colors[ImGuiCol_HeaderHovered]         = ImVec4(0.22f, 0.50f, 0.28f, 0.90f);
    colors[ImGuiCol_HeaderActive]          = ImVec4(0.85f, 0.70f, 0.10f, 0.90f);
    colors[ImGuiCol_Tab]                   = ImVec4(0.12f, 0.25f, 0.15f, 0.86f);
    colors[ImGuiCol_TabHovered]            = ImVec4(0.25f, 0.55f, 0.30f, 1.00f);
    colors[ImGuiCol_TabSelected]           = ImVec4(0.18f, 0.42f, 0.24f, 1.00f);
    colors[ImGuiCol_CheckMark]             = ImVec4(0.95f, 0.80f, 0.20f, 1.00f);
    colors[ImGuiCol_SliderGrab]            = ImVec4(0.90f, 0.75f, 0.15f, 1.00f);
    colors[ImGuiCol_SliderGrabActive]      = ImVec4(1.00f, 0.88f, 0.30f, 1.00f);
}

static void InitImGuiVulkan() {
    if (g_ImGuiInitialized || !g_Device || !g_Instance || !g_Queue) return;

    if (!g_Window) {
        g_Window = SDL_GetKeyboardFocus();
        if (!g_Window) g_Window = SDL_GetMouseFocus();
    }

    if (!ImGui_ImplVulkan_LoadFunctions(VK_API_VERSION_1_3, ImGuiVulkanLoader, nullptr)) {
        OverlayLog("[overlay] Failed to load Vulkan functions for ImGui\n");
        return;
    }

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableGamepad;
    io.ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;
    io.DisplaySize = ImVec2((float)g_Width, (float)g_Height);
    io.IniFilename = nullptr;
    SetupImGuiStyle();

    if (g_Window && !g_SDL3BackendInitialized) {
        if (ImGui_ImplSDL3_InitForVulkan(g_Window)) {
            ImGui_ImplSDL3_SetGamepadMode(ImGui_ImplSDL3_GamepadMode_Manual, nullptr, 0);
            ImGui_ImplSDL3_SetMouseCaptureMode(ImGui_ImplSDL3_MouseCaptureMode_Disabled);
            g_SDL3BackendInitialized = true;
        }
    }

    uint32_t img_count = (uint32_t)g_Swapchain.images.size();
    if (img_count < 2) img_count = 2;

    ImGui_ImplVulkan_InitInfo init_info = {};
    init_info.ApiVersion = VK_API_VERSION_1_3;
    init_info.Instance = g_Instance;
    init_info.PhysicalDevice = g_PhysicalDevice;
    init_info.Device = g_Device;
    init_info.QueueFamily = g_QueueFamily;
    init_info.Queue = g_Queue;
    init_info.DescriptorPoolSize = 16;
    init_info.MinImageCount = 2;
    init_info.ImageCount = img_count;
    init_info.UseDynamicRendering = true;

    VkPipelineRenderingCreateInfoKHR rendering_info = {};
    rendering_info.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR;
    rendering_info.colorAttachmentCount = 1;
    rendering_info.pColorAttachmentFormats = &g_SwapchainFormat;
    init_info.PipelineInfoMain.PipelineRenderingCreateInfo = rendering_info;

    if (!ImGui_ImplVulkan_Init(&init_info)) {
        OverlayLog("[overlay] ImGui_ImplVulkan_Init failed\n");
        return;
    }

    g_ImGuiInitialized = true;
    OverlayLog("[overlay] ImGui Vulkan initialized: %ux%u fmt=%d images=%u sdl3_backend=%d\n",
               g_Width, g_Height, (int)g_SwapchainFormat, img_count, (int)g_SDL3BackendInitialized);
}

// -------------------------------------------------------------
// Majora's Mask Goron Link In-Game HUD
// -------------------------------------------------------------
static void RenderGoronHUD() {
    ImGuiIO& io = ImGui::GetIO();
    float hud_width = 330.0f;
    float hud_height = 135.0f;
    ImVec2 pos(io.DisplaySize.x - hud_width - 24.0f, 24.0f);

    ImGui::SetNextWindowPos(pos, ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(hud_width, hud_height), ImGuiCond_Always);

    ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration |
                             ImGuiWindowFlags_AlwaysAutoResize |
                             ImGuiWindowFlags_NoSavedSettings |
                             ImGuiWindowFlags_NoFocusOnAppearing |
                             ImGuiWindowFlags_NoNav;

    if (!g_ShowOverlay) {
        flags |= ImGuiWindowFlags_NoInputs;
    }

    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 8.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 2.0f);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0.08f, 0.05f, 0.03f, 0.82f));
    ImGui::PushStyleColor(ImGuiCol_Border, ImVec4(0.95f, 0.55f, 0.15f, 0.90f));

    if (ImGui::Begin("##GoronHUD", nullptr, flags)) {
        ImGui::TextColored(ImVec4(1.0f, 0.75f, 0.20f, 1.0f), "★ GORON LINK (Majora's Mask)");
        ImGui::SameLine(hud_width - 65.0f);
        ImGui::TextColored(ImVec4(0.7f, 0.7f, 0.7f, 0.8f), "[F2 Menu]");

        ImGui::Separator();

        const char* action_str = "Standing";
        ImVec4 action_col(0.75f, 0.85f, 0.75f, 1.0f);

        switch (g_GoronAction) {
            case GoronAction::Standing:
                action_str = "Standing / Ready";
                action_col = ImVec4(0.75f, 0.85f, 0.75f, 1.0f);
                break;
            case GoronAction::Curling:
                action_str = "Curling Into Ball...";
                action_col = ImVec4(1.0f, 0.8f, 0.3f, 1.0f);
                break;
            case GoronAction::RollingNormal:
                action_str = "Rolling (Normal: 14.0 m/s)";
                action_col = ImVec4(0.3f, 0.85f, 1.0f, 1.0f);
                break;
            case GoronAction::RollingSpikes:
                action_str = "⚡ SPIKE ROLL (24.0 m/s) [DRAIN] ⚡";
                action_col = ImVec4(1.0f, 0.25f, 0.15f, 1.0f);
                break;
            case GoronAction::Punch1:
                action_str = "Punch 1 (Right Hook!)";
                action_col = ImVec4(1.0f, 0.6f, 0.2f, 1.0f);
                break;
            case GoronAction::Punch2:
                action_str = "Punch 2 (Left Hook!)";
                action_col = ImVec4(1.0f, 0.5f, 0.2f, 1.0f);
                break;
            case GoronAction::Punch3:
                action_str = "Punch 3 (DOUBLE GROUND SLAM!)";
                action_col = ImVec4(1.0f, 0.2f, 0.2f, 1.0f);
                break;
            case GoronAction::GroundPoundHover:
                action_str = "Ground Pound (Hovering...)";
                action_col = ImVec4(0.9f, 0.4f, 1.0f, 1.0f);
                break;
            case GoronAction::GroundPoundFall:
                action_str = "Ground Pound (ROCKET SLAM!)";
                action_col = ImVec4(1.0f, 0.1f, 0.1f, 1.0f);
                break;
            case GoronAction::GroundPoundLand:
                action_str = "💥 GROUND IMPACT SHOCKWAVE! 💥";
                action_col = ImVec4(1.0f, 0.9f, 0.2f, 1.0f);
                break;
        }

        ImGui::TextColored(action_col, "Action: %s", action_str);

        float speed_fraction = g_GoronSpeed / 24.0f;
        char speed_buf[32];
        snprintf(speed_buf, sizeof(speed_buf), "%.1f / 24.0 m/s", g_GoronSpeed);
        if (g_GoronAction == GoronAction::RollingSpikes) {
            ImGui::PushStyleColor(ImGuiCol_PlotHistogram, ImVec4(1.0f, 0.2f, 0.1f, 1.0f));
        } else if (g_GoronSpeed > 10.0f) {
            ImGui::PushStyleColor(ImGuiCol_PlotHistogram, ImVec4(1.0f, 0.7f, 0.1f, 1.0f));
        } else {
            ImGui::PushStyleColor(ImGuiCol_PlotHistogram, ImVec4(0.2f, 0.7f, 1.0f, 1.0f));
        }
        ImGui::ProgressBar(speed_fraction, ImVec2(-1, 16), speed_buf);
        ImGui::PopStyleColor();

        ImGui::TextColored(ImVec4(0.35f, 0.95f, 0.45f, 1.0f), "Magic: %d MP %s",
            g_SelectedMagic,
            (g_GoronAction == GoronAction::RollingSpikes ? "(Consuming 5 MP/s)" : ""));

        ImGui::TextColored(ImVec4(0.65f, 0.65f, 0.65f, 0.85f), "Hold Space/RMB to Roll | LMB/F to Punch");
    }
    ImGui::End();

    ImGui::PopStyleColor(2);
    ImGui::PopStyleVar(2);
}

// -------------------------------------------------------------
// Playground In-Game UI Render
// -------------------------------------------------------------
static void RenderPlaygroundUI() {
    ImGui::SetNextWindowSize(ImVec2(660, 560), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowPos(ImVec2(60, 60), ImGuiCond_FirstUseEver);

    if (ImGui::Begin("Hookshot Mod Playground & Dev Menu [F2]", &g_ShowOverlay, ImGuiWindowFlags_NoCollapse)) {
        ImGui::TextColored(ImVec4(0.95f, 0.85f, 0.25f, 1.0f), "THE LEGEND OF ZELDA: OCARINA OF TIME REMASTERED");
        ImGui::Separator();

        if (ImGui::BeginTabBar("PlaygroundTabs")) {
            // TAB 1: ALL ITEMS & EQUIPMENT
            if (ImGui::BeginTabItem("★ Playground / All Items")) {
                ImGui::Spacing();
                ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.20f, 0.65f, 0.30f, 1.0f));
                if (ImGui::Button("✦ GRANT ALL ITEMS & EQUIPMENT (MASTER UNLOCK) ✦", ImVec2(-1, 38))) {
                    g_SelectedHearts = 20;
                    g_SelectedMagic = 64;
                    g_SelectedRupees = 999;
                    EnqueueAction({ActionType::GrantMasterUnlock});
                    g_StatusMessage = "Applied Master Unlock: All swords, shields, tunics, boots, masks, tools, bottles & songs!";
                }
                ImGui::PopStyleColor();

                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.8f, 0.8f, 0.8f, 1.0f), "Quick Category Unlocks:");

                if (ImGui::Button("All Swords & Shields (Kokiri, Master, Biggoron, Deku, Hylian, Mirror)", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantSwordsShields});
                    g_StatusMessage = "Equipped full weapon arsenal: Kokiri, Master, Biggoron Swords + Deku, Hylian, Mirror Shields!";
                }
                if (ImGui::Button("All Tunics & Boots (Kokiri, Goron, Zora Tunics + Iron & Hover Boots)", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantTunicsBoots});
                    g_StatusMessage = "Unlocked all tunics (Goron, Zora) and elemental boots (Iron, Hover)!";
                }
                if (ImGui::Button("All Masks (Goron Transformation Mask, Keaton, Bunny, Truth...)", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantMasks});
                    g_StatusMessage = "Unlocked all masks including Majora's Mask Goron Transformation Mask!";
                }
                if (ImGui::Button("All Explorer Tools (Hookshot, Longshot, Bow, Megaton Hammer, Bombs, Din's Fire...)", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantTools});
                    g_StatusMessage = "Granted full explorer tool set: Bow, Hookshot, Hammer, Bombs, Din's Fire, Nayru's Love!";
                }
                if (ImGui::Button("4 Full Magic Bottles (Red Potion, Green Potion, Blue Potion, Fairy)", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantBottles});
                    g_StatusMessage = "Filled all 4 bottles with potions & fairy!";
                }
                if (ImGui::Button("All 12 Ocarina Songs + 6 Medallions + 3 Spiritual Stones", ImVec2(-1, 26))) {
                    EnqueueAction({ActionType::GrantSongs});
                    g_StatusMessage = "Learned all 12 songs (Zelda's Lullaby, Saria's Song, Song of Time...) & quest stones!";
                }

                ImGui::EndTabItem();
            }

            // TAB 2: STATS & CHEATS
            if (ImGui::BeginTabItem("Player & Cheats")) {
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.85f, 0.85f, 0.35f, 1.0f), "Player Health & Stats:");
                if (ImGui::SliderInt("Hearts", &g_SelectedHearts, 3, 20)) {
                    EnqueueAction({ActionType::SetHearts, g_SelectedHearts});
                }
                if (ImGui::Button("Apply Full 20 Hearts (80 HP)", ImVec2(240, 26))) {
                    g_SelectedHearts = 20;
                    EnqueueAction({ActionType::SetHearts, 20});
                    g_StatusMessage = "Health set to 20 full hearts (80 HP).";
                }
                ImGui::SameLine();
                if (ImGui::Button("Full Heal Now", ImVec2(160, 26))) {
                    EnqueueAction({ActionType::FullHeal});
                    g_StatusMessage = "Link healed to full HP.";
                }

                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.35f, 0.85f, 0.85f, 1.0f), "Magic Meter:");
                if (ImGui::SliderInt("Magic", &g_SelectedMagic, 0, 64)) {
                    EnqueueAction({ActionType::SetMagic, g_SelectedMagic});
                }
                if (ImGui::Button("Give Double Magic Meter (64 MP)", ImVec2(240, 26))) {
                    g_SelectedMagic = 64;
                    EnqueueAction({ActionType::SetMagic, 64});
                    g_StatusMessage = "Magic meter set to maximum double bar (64 MP).";
                }

                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.85f, 0.70f, 0.20f, 1.0f), "Rupees:");
                if (ImGui::SliderInt("Rupees", &g_SelectedRupees, 0, 999)) {
                    EnqueueAction({ActionType::SetRupees, g_SelectedRupees});
                }
                if (ImGui::Button("Give 999 Rupees", ImVec2(240, 26))) {
                    g_SelectedRupees = 999;
                    EnqueueAction({ActionType::SetRupees, 999});
                    g_StatusMessage = "Rupee wallet maxed at 999!";
                }

                ImGui::Separator();
                ImGui::TextColored(ImVec4(0.95f, 0.45f, 0.45f, 1.0f), "Trainer / Toggles:");
                ImGui::Checkbox("Infinite Health (Invincible)", &g_InfHealth);
                ImGui::SameLine(260);
                ImGui::Checkbox("Infinite Magic", &g_InfMagic);
                ImGui::Checkbox("Infinite Ammo (Arrows, Bombs, Seeds)", &g_InfAmmo);

                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.85f, 0.85f, 0.35f, 1.0f), "Age Swapper:");
                if (ImGui::Button("Switch to Child Link", ImVec2(180, 28))) {
                    EnqueueAction({ActionType::SwitchAgeChild});
                    g_StatusMessage = "Switched player form to Child Link (loading KokiriForest_Child)...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Switch to Adult Link", ImVec2(180, 28))) {
                    EnqueueAction({ActionType::SwitchAgeAdult});
                    g_StatusMessage = "Switched player form to Adult Link (loading KokiriForest_Adult)...";
                }

                ImGui::EndTabItem();
            }

            // TAB 3: WARPS & MAP SELECTOR
            if (ImGui::BeginTabItem("Warps & Maps")) {
                ImGui::Spacing();
                ImGui::Text("Overworld Locations:");
                if (ImGui::Button("Kokiri Forest", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "KokiriForest_Child"});
                    g_StatusMessage = "Warping to Kokiri Forest...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Temple of Time", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "Market_Child"});
                    g_StatusMessage = "Warping to Temple of Time...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Hyrule Field", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "HyruleField_Child"});
                    g_StatusMessage = "Warping to Hyrule Field...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Castle Market", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "Market_Child"});
                    g_StatusMessage = "Warping to Castle Market...";
                }

                if (ImGui::Button("Kakariko Village", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "KakarikoVillage_Child"});
                    g_StatusMessage = "Warping to Kakariko...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Goron City", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "GoronCity_Child"});
                    g_StatusMessage = "Warping to Goron City...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Zora's Domain", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "ZoraDomain_Child"});
                    g_StatusMessage = "Warping to Zora's Domain...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Lake Hylia", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "WaterTemple"});
                    g_StatusMessage = "Warping to Lake Hylia / Water Temple...";
                }

                if (ImGui::Button("Gerudo Valley", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "GerudoValley_Child"});
                    g_StatusMessage = "Warping to Gerudo Valley...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Desert Colossus", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "DesertColossus_Child"});
                    g_StatusMessage = "Warping to Desert Colossus...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Lon Lon Ranch", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "HyruleField_Child"});
                    g_StatusMessage = "Warping to Lon Lon Ranch...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Enemy Arena", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "EnemyMuseum"});
                    g_StatusMessage = "Warping to Enemy Museum / Arena...";
                }

                ImGui::Spacing();
                ImGui::Text("Dungeons & Temples:");
                if (ImGui::Button("Deku Tree", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "InsideTheDekuTree"});
                    g_StatusMessage = "Warping to Deku Tree...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Dodongo's Cavern", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "DodongoCavern"});
                    g_StatusMessage = "Warping to Dodongo's Cavern...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Jabu-Jabu's Belly", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "InsideJabuJabuBelly"});
                    g_StatusMessage = "Warping to Jabu-Jabu...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Forest Temple", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "ForestTemple"});
                    g_StatusMessage = "Warping to Forest Temple...";
                }

                if (ImGui::Button("Fire Temple", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "FireTemple"});
                    g_StatusMessage = "Warping to Fire Temple...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Water Temple", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "WaterTemple"});
                    g_StatusMessage = "Warping to Water Temple...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Shadow Temple", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "ShadowTemple"});
                    g_StatusMessage = "Warping to Shadow Temple...";
                }
                ImGui::SameLine();
                if (ImGui::Button("Spirit Temple", ImVec2(130, 26))) {
                    EnqueueAction({ActionType::Warp, 0, "SpiritTemple"});
                    g_StatusMessage = "Warping to Spirit Temple...";
                }

                ImGui::EndTabItem();
            }

            // TAB 4: MODS & TRANSFORMATIONS
            if (ImGui::BeginTabItem("Mods & Transformations")) {
                ImGui::Spacing();
                ImGui::TextColored(ImVec4(1.0f, 0.75f, 0.20f, 1.0f), "Playable Character: GORON LINK (Majora's Mask)");
                ImGui::TextDisabled("Separate Playable Character with authentic Goron 3D Model, Spikes, Punches & Ground Pound");

                ImGui::Spacing();
                ImGui::TextColored(ImVec4(0.4f, 0.85f, 1.0f, 1.0f), "Quick Character Toggle: Press [G] anytime during gameplay to transform or revert!");
                if (g_IsGoronTransformed) {
                    ImGui::TextColored(ImVec4(0.2f, 1.0f, 0.4f, 1.0f), "● ACTIVE CHARACTER: GORON LINK (Fists, Rolling, Spikes, Ground Pound Online)");
                } else {
                    ImGui::TextColored(ImVec4(0.7f, 0.7f, 0.7f, 1.0f), "○ ACTIVE CHARACTER: HYLIAN LINK (Standard Sword & Shield Form)");
                }

                ImGui::Spacing();
                if (g_IsGoronTransformed) {
                    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.25f, 0.65f, 0.25f, 1.0f));
                    if (ImGui::Button("✦ REVERT TO HYLIAN LINK ✦", ImVec2(320, 36))) {
                        EnqueueAction({ActionType::RevertHylian});
                    }
                    ImGui::PopStyleColor();
                } else {
                    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.85f, 0.45f, 0.10f, 1.0f));
                    if (ImGui::Button("✦ TRANSFORM INTO GORON LINK ✦", ImVec2(320, 36))) {
                        EnqueueAction({ActionType::TransformGoron});
                    }
                    ImGui::PopStyleColor();
                }

                ImGui::SameLine();
                if (ImGui::Button("Toggle [G]", ImVec2(120, 36))) {
                    if (g_IsGoronTransformed) EnqueueAction({ActionType::RevertHylian});
                    else EnqueueAction({ActionType::TransformGoron});
                }

                ImGui::Spacing();
                ImGui::Separator();
                ImGui::TextColored(ImVec4(0.95f, 0.85f, 0.25f, 1.0f), "Instant Action Triggers (Test Goron Abilities):");

                if (ImGui::Button("💥 Ground Pound (Butt Stomp)", ImVec2(220, 28))) {
                    if (g_IsGoronTransformed) {
                        g_GoronAction = GoronAction::GroundPoundHover;
                        g_GoronActionTimer = 0.0f;
                        g_StatusMessage = "Triggered Goron Ground Pound!";
                    } else {
                        g_StatusMessage = "Must be in Goron Link form to perform Ground Pound.";
                    }
                }
                ImGui::SameLine();
                if (ImGui::Button("🥊 1-2-3 Punch Combo", ImVec2(190, 28))) {
                    if (g_IsGoronTransformed) {
                        g_GoronAction = GoronAction::Punch1;
                        g_GoronActionTimer = 0.0f;
                        g_GoronPunchCombo = 1;
                        g_StatusMessage = "Triggered Goron Punch Combo!";
                    } else {
                        g_StatusMessage = "Must be in Goron Link form to Punch.";
                    }
                }
                ImGui::SameLine();
                if (ImGui::Button("⚡ Engage Spike Roll (24 m/s)", ImVec2(210, 28))) {
                    if (g_IsGoronTransformed) {
                        g_GoronAction = GoronAction::RollingSpikes;
                        g_GoronSpeed = 24.0f;
                        g_StatusMessage = "Engaged 24.0 m/s Spike Roll!";
                    } else {
                        g_StatusMessage = "Must be in Goron Link form to Roll.";
                    }
                }

                if (ImGui::Button("🧪 Refill Magic Meter (64 MP)", ImVec2(220, 26))) {
                    g_SelectedMagic = 64;
                    EnqueueAction({ActionType::SetMagic, 64});
                    g_StatusMessage = "Magic refilled to 64 MP!";
                }

                ImGui::Spacing();
                ImGui::Separator();
                ImGui::TextColored(ImVec4(0.4f, 0.85f, 1.0f, 1.0f), "Majora's Mask Controls Guide:");
                ImGui::BulletText("Roll / Curl: Hold Space / RMB / Gamepad A (curls and accelerates to 14.0 m/s).");
                ImGui::BulletText("Spikes: Keep rolling at top speed for >1.0s. Spikes emerge and speed hits 24.0 m/s!");
                ImGui::BulletText("Magic Drain: Spikes consume 5 MP/sec. Retracts automatically if magic runs out.");
                ImGui::BulletText("1-2-3 Punch Combo: Press F / LMB / Gamepad B (Right Hook -> Left Hook -> Double Ground Slam).");
                ImGui::BulletText("Ground Pound: Press Punch while airborne or rolling (hovers 0.25s then rocket slams down).");

                ImGui::Spacing();
                ImGui::Separator();
                ImGui::Text("Performance: %.1f FPS (%.2f ms/frame)", ImGui::GetIO().Framerate, 1000.0f / (ImGui::GetIO().Framerate > 0 ? ImGui::GetIO().Framerate : 60.0f));

                ImGui::EndTabItem();
            }

            if (ImGui::BeginTabItem("Param Tuner")) {
                tuner::DrawTab();
                ImGui::EndTabItem();
            }

            ImGui::EndTabBar();
        }

        ImGui::Spacing();
        ImGui::Separator();
        ImGui::TextColored(ImVec4(0.4f, 0.9f, 0.5f, 1.0f), "Status: %s", g_StatusMessage.c_str());

        ImGui::Spacing();
        ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.18f, 0.45f, 0.24f, 1.00f));
        if (ImGui::Button("▶ RESUME PLAYING (CLOSE MENU)", ImVec2(-1, 32))) {
            g_ShowOverlay = false;
            UpdateMouseCursorMode();
        }
        ImGui::PopStyleColor();
        ImGui::TextDisabled("Toggle Hotkey: [F2] | Click Resume or Press [F2] to Resume Playing");
    }
    ImGui::End();
}

// -------------------------------------------------------------
// SDL Hooks (internal linkage, returned via dlsym)
// -------------------------------------------------------------
static SDL_Window* Hook_SDL_CreateWindow(const char* title, int w, int h, SDL_WindowFlags flags) {
    InitRealFunctions();
    SDL_Window* window = real_SDL_CreateWindow ? real_SDL_CreateWindow(title, w, h, flags) : nullptr;
    if (window) {
        g_Window = window;
        OverlayLog("[overlay] SDL_CreateWindow: window=%p (%dx%d title=%s)\n", (void*)window, w, h, title ? title : "");
    }
    return window;
}

static bool Hook_SDL_SetWindowRelativeMouseMode(SDL_Window* window, bool enabled) {
    InitRealFunctions();
    if (window && !g_Window) g_Window = window;
    g_GameWantsRelativeMouse = enabled;
    if (g_ShowOverlay) {
        if (real_SDL_SetWindowRelativeMouseMode) {
            return real_SDL_SetWindowRelativeMouseMode(window, false);
        }
        return true;
    }
    return real_SDL_SetWindowRelativeMouseMode ? real_SDL_SetWindowRelativeMouseMode(window, enabled) : false;
}

static SDL_MouseButtonFlags Hook_SDL_GetMouseState(float* x, float* y) {
    InitRealFunctions();
    SDL_MouseButtonFlags buttons = real_SDL_GetMouseState ? real_SDL_GetMouseState(x, y) : 0;
    if (g_ShowOverlay) {
        return 0;
    }
    return buttons;
}

static const bool* Hook_SDL_GetKeyboardState(int* numkeys) {
    InitRealFunctions();
    int count = 0;
    const bool* real_keys = real_SDL_GetKeyboardState ? real_SDL_GetKeyboardState(&count) : nullptr;
    if (numkeys) *numkeys = count;

    if (real_keys && count > SDL_SCANCODE_F2) {
        static bool s_PrevF2 = false;
        bool f2_down = real_keys[SDL_SCANCODE_F2];
        if (f2_down && !s_PrevF2) {
            ToggleOverlay("SDL_GetKeyboardState");
        }
        s_PrevF2 = f2_down;
    }

    if (g_PrevOverlayState != g_ShowOverlay) {
        UpdateMouseCursorMode();
        g_PrevOverlayState = g_ShowOverlay;
    }

    if (g_IsGoronTransformed && real_keys && !g_ShowOverlay) {
        float sx = 0.0f;
        float sy = 0.0f;
        if (count > SDL_SCANCODE_W && real_keys[SDL_SCANCODE_W]) sy += 1.0f;
        if (count > SDL_SCANCODE_S && real_keys[SDL_SCANCODE_S]) sy -= 1.0f;
        if (count > SDL_SCANCODE_A && real_keys[SDL_SCANCODE_A]) sx -= 1.0f;
        if (count > SDL_SCANCODE_D && real_keys[SDL_SCANCODE_D]) sx += 1.0f;
        if (count > SDL_SCANCODE_UP && real_keys[SDL_SCANCODE_UP]) sy += 1.0f;
        if (count > SDL_SCANCODE_DOWN && real_keys[SDL_SCANCODE_DOWN]) sy -= 1.0f;
        if (count > SDL_SCANCODE_LEFT && real_keys[SDL_SCANCODE_LEFT]) sx -= 1.0f;
        if (count > SDL_SCANCODE_RIGHT && real_keys[SDL_SCANCODE_RIGHT]) sx += 1.0f;
        g_KeyStickX = sx;
        g_KeyStickY = sy;

        bool roll = (count > SDL_SCANCODE_SPACE && real_keys[SDL_SCANCODE_SPACE]) ||
                    (count > SDL_SCANCODE_LSHIFT && real_keys[SDL_SCANCODE_LSHIFT]) ||
                    (count > SDL_SCANCODE_RSHIFT && real_keys[SDL_SCANCODE_RSHIFT]) ||
                    (count > SDL_SCANCODE_E && real_keys[SDL_SCANCODE_E]);
        g_KeyRollHeld = roll;

        static bool s_PrevPunchKey = false;
        bool punch_down = (count > SDL_SCANCODE_F && real_keys[SDL_SCANCODE_F]) ||
                          (count > SDL_SCANCODE_Q && real_keys[SDL_SCANCODE_Q]) ||
                          (count > SDL_SCANCODE_R && real_keys[SDL_SCANCODE_R]);
        if (punch_down && !s_PrevPunchKey) {
            g_KeyPunchPressed = true;
        }
        s_PrevPunchKey = punch_down;
    }

    if (g_ShowOverlay && real_keys) {
        static bool empty_keys[SDL_SCANCODE_COUNT] = { false };
        if (numkeys) *numkeys = SDL_SCANCODE_COUNT;
        return empty_keys;
    }
    return real_keys;
}

// Returns true if the event should be consumed by the overlay (hidden from the game)
static bool ProcessOverlaySDLEvent(SDL_Event* event) {
    if (!event) return false;

    if (event->type == SDL_EVENT_KEY_DOWN && !event->key.repeat &&
        (event->key.key == SDLK_F2 || event->key.scancode == SDL_SCANCODE_F2)) {
        ToggleOverlay("SDL_Event");
        UpdateMouseCursorMode();
        g_PrevOverlayState = g_ShowOverlay;
        return true;
    }

    if (g_ImGuiInitialized && g_SDL3BackendInitialized) {
        {
            std::lock_guard<std::mutex> lock(g_Mutex);
            ImGui_ImplSDL3_ProcessEvent(event);
        }
        if (g_ShowOverlay) {
            // While overlay is open, consume mouse & keyboard input so the game doesn't act on clicks/keys
            switch (event->type) {
                case SDL_EVENT_MOUSE_MOTION:
                case SDL_EVENT_MOUSE_BUTTON_DOWN:
                case SDL_EVENT_MOUSE_BUTTON_UP:
                case SDL_EVENT_MOUSE_WHEEL:
                case SDL_EVENT_KEY_DOWN:
                case SDL_EVENT_KEY_UP:
                case SDL_EVENT_TEXT_INPUT:
                case SDL_EVENT_TEXT_EDITING:
                    return true;
                default:
                    break;
            }
        }
    }

    if (!g_ShowOverlay && event->type == SDL_EVENT_KEY_DOWN && !event->key.repeat &&
        (event->key.scancode == SDL_SCANCODE_G || event->key.key == SDLK_G)) {
        if (g_IsGoronTransformed) {
            EnqueueAction({ActionType::RevertHylian});
        } else {
            EnqueueAction({ActionType::TransformGoron});
        }
    }

    if (!g_ShowOverlay && g_IsGoronTransformed) {
        static bool s_TrackedKeys[SDL_SCANCODE_COUNT] = { false };
        static bool s_MouseRightHeld = false;

        if (event->type == SDL_EVENT_KEY_DOWN) {
            if (event->key.scancode < SDL_SCANCODE_COUNT) {
                s_TrackedKeys[event->key.scancode] = true;
            }
            if (!event->key.repeat) {
                if (event->key.scancode == SDL_SCANCODE_F ||
                    event->key.scancode == SDL_SCANCODE_Q ||
                    event->key.scancode == SDL_SCANCODE_R) {
                    g_KeyPunchPressed = true;
                }
            }
        } else if (event->type == SDL_EVENT_KEY_UP) {
            if (event->key.scancode < SDL_SCANCODE_COUNT) {
                s_TrackedKeys[event->key.scancode] = false;
            }
        }

        float sx = 0.0f;
        float sy = 0.0f;
        if (s_TrackedKeys[SDL_SCANCODE_W] || s_TrackedKeys[SDL_SCANCODE_UP]) sy += 1.0f;
        if (s_TrackedKeys[SDL_SCANCODE_S] || s_TrackedKeys[SDL_SCANCODE_DOWN]) sy -= 1.0f;
        if (s_TrackedKeys[SDL_SCANCODE_A] || s_TrackedKeys[SDL_SCANCODE_LEFT]) sx -= 1.0f;
        if (s_TrackedKeys[SDL_SCANCODE_D] || s_TrackedKeys[SDL_SCANCODE_RIGHT]) sx += 1.0f;
        g_KeyStickX = sx;
        g_KeyStickY = sy;

        if (event->type == SDL_EVENT_MOUSE_BUTTON_DOWN) {
            if (event->button.button == SDL_BUTTON_LEFT) {
                g_MousePunchPressed = true;
            } else if (event->button.button == SDL_BUTTON_RIGHT) {
                s_MouseRightHeld = true;
            }
        } else if (event->type == SDL_EVENT_MOUSE_BUTTON_UP) {
            if (event->button.button == SDL_BUTTON_RIGHT) {
                s_MouseRightHeld = false;
            }
        }

        g_KeyRollHeld = s_TrackedKeys[SDL_SCANCODE_SPACE] ||
                        s_TrackedKeys[SDL_SCANCODE_LSHIFT] ||
                        s_TrackedKeys[SDL_SCANCODE_RSHIFT] ||
                        s_TrackedKeys[SDL_SCANCODE_E] ||
                        s_MouseRightHeld;

        if (event->type == SDL_EVENT_GAMEPAD_BUTTON_DOWN) {
            if (event->gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ||
                event->gbutton.button == SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER) {
                g_GamepadRollHeld = true;
            } else if (event->gbutton.button == SDL_GAMEPAD_BUTTON_EAST ||
                       event->gbutton.button == SDL_GAMEPAD_BUTTON_WEST) {
                g_GamepadPunchPressed = true;
            }
        } else if (event->type == SDL_EVENT_GAMEPAD_BUTTON_UP) {
            if (event->gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ||
                event->gbutton.button == SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER) {
                g_GamepadRollHeld = false;
            }
        } else if (event->type == SDL_EVENT_GAMEPAD_AXIS_MOTION) {
            if (event->gaxis.axis == SDL_GAMEPAD_AXIS_LEFTX) {
                g_GamepadStickX = (float)event->gaxis.value / 32767.0f;
            } else if (event->gaxis.axis == SDL_GAMEPAD_AXIS_LEFTY) {
                g_GamepadStickY = (float)event->gaxis.value / 32767.0f;
            }
        }
    }
    return false;
}

static bool Hook_SDL_PollEvent(SDL_Event* event) {
    InitRealFunctions();
    if (!real_SDL_PollEvent) return false;

    if (g_PrevOverlayState != g_ShowOverlay) {
        UpdateMouseCursorMode();
        g_PrevOverlayState = g_ShowOverlay;
    }

    while (real_SDL_PollEvent(event)) {
        if (!ProcessOverlaySDLEvent(event)) {
            return true;
        }
    }
    return false;
}

static bool Hook_SDL_WaitEventTimeout(SDL_Event* event, Sint32 timeoutMS) {
    InitRealFunctions();
    if (!real_SDL_WaitEventTimeout) return false;

    if (g_PrevOverlayState != g_ShowOverlay) {
        UpdateMouseCursorMode();
        g_PrevOverlayState = g_ShowOverlay;
    }

    bool has_event = real_SDL_WaitEventTimeout(event, timeoutMS);
    if (!has_event || !event) return false;

    if (ProcessOverlaySDLEvent(event)) {
        return Hook_SDL_PollEvent(event);
    }
    return true;
}

// -------------------------------------------------------------
// Vulkan Hooks (internal linkage, returned via dlsym / ProcAddr)
// -------------------------------------------------------------
static VkResult Hook_vkCreateInstance(const VkInstanceCreateInfo* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkInstance* pInstance) {
    InitRealFunctions();
    if (!real_vkCreateInstance && real_vkGetInstanceProcAddr) {
        real_vkCreateInstance = (PFN_vkCreateInstance)real_vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkCreateInstance");
    }
    VkResult res = real_vkCreateInstance ? real_vkCreateInstance(pCreateInfo, pAllocator, pInstance) : VK_ERROR_INITIALIZATION_FAILED;
    if (res == VK_SUCCESS && pInstance && *pInstance) {
        g_Instance = *pInstance;
        OverlayLog("[overlay] vkCreateInstance: instance=%p\n", (void*)g_Instance);
        if (real_vkGetInstanceProcAddr) {
            if (!real_vkDestroyInstance)
                real_vkDestroyInstance = (PFN_vkDestroyInstance)real_vkGetInstanceProcAddr(g_Instance, "vkDestroyInstance");
            if (!real_vkCreateDevice)
                real_vkCreateDevice = (PFN_vkCreateDevice)real_vkGetInstanceProcAddr(g_Instance, "vkCreateDevice");
            if (!real_vkGetDeviceProcAddr)
                real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_vkGetInstanceProcAddr(g_Instance, "vkGetDeviceProcAddr");
        }
    }
    return res;
}

static void Hook_vkDestroyInstance(VkInstance instance, const VkAllocationCallbacks* pAllocator) {
    InitRealFunctions();
    if (!real_vkDestroyInstance && real_vkGetInstanceProcAddr && instance) {
        real_vkDestroyInstance = (PFN_vkDestroyInstance)real_vkGetInstanceProcAddr(instance, "vkDestroyInstance");
    }
    if (instance == g_Instance) g_Instance = VK_NULL_HANDLE;
    if (real_vkDestroyInstance) real_vkDestroyInstance(instance, pAllocator);
}

static VkResult Hook_vkCreateDevice(VkPhysicalDevice physicalDevice, const VkDeviceCreateInfo* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkDevice* pDevice) {
    InitRealFunctions();
    if (!real_vkCreateDevice && real_vkGetInstanceProcAddr && g_Instance) {
        real_vkCreateDevice = (PFN_vkCreateDevice)real_vkGetInstanceProcAddr(g_Instance, "vkCreateDevice");
    }
    VkResult res = real_vkCreateDevice ? real_vkCreateDevice(physicalDevice, pCreateInfo, pAllocator, pDevice) : VK_ERROR_INITIALIZATION_FAILED;
    if (res == VK_SUCCESS && pDevice && *pDevice) {
        g_PhysicalDevice = physicalDevice;
        g_Device = *pDevice;
        g_CommandPool = VK_NULL_HANDLE;
        if (pCreateInfo && pCreateInfo->queueCreateInfoCount > 0 && pCreateInfo->pQueueCreateInfos) {
            g_QueueFamily = pCreateInfo->pQueueCreateInfos[0].queueFamilyIndex;
        }
        OverlayLog("[overlay] vkCreateDevice: device=%p queueFamily=%u\n", (void*)g_Device, g_QueueFamily);
        if (!real_vkGetDeviceProcAddr && real_vkGetInstanceProcAddr && g_Instance) {
            real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_vkGetInstanceProcAddr(g_Instance, "vkGetDeviceProcAddr");
        }
        if (real_vkGetDeviceProcAddr) {
            real_vkDestroyDevice = (PFN_vkDestroyDevice)real_vkGetDeviceProcAddr(g_Device, "vkDestroyDevice");
            real_vkCreateSwapchainKHR = (PFN_vkCreateSwapchainKHR)real_vkGetDeviceProcAddr(g_Device, "vkCreateSwapchainKHR");
            real_vkDestroySwapchainKHR = (PFN_vkDestroySwapchainKHR)real_vkGetDeviceProcAddr(g_Device, "vkDestroySwapchainKHR");
            real_vkQueuePresentKHR = (PFN_vkQueuePresentKHR)real_vkGetDeviceProcAddr(g_Device, "vkQueuePresentKHR");

            pfn_vkCmdBeginRendering = (PFN_vkCmdBeginRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdBeginRendering");
            if (!pfn_vkCmdBeginRendering)
                pfn_vkCmdBeginRendering = (PFN_vkCmdBeginRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdBeginRenderingKHR");
            pfn_vkCmdEndRendering = (PFN_vkCmdEndRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdEndRendering");
            if (!pfn_vkCmdEndRendering)
                pfn_vkCmdEndRendering = (PFN_vkCmdEndRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdEndRenderingKHR");
        }
    }
    return res;
}

static void Hook_vkDestroyDevice(VkDevice device, const VkAllocationCallbacks* pAllocator) {
    InitRealFunctions();
    if (!real_vkDestroyDevice && real_vkGetDeviceProcAddr && device) {
        real_vkDestroyDevice = (PFN_vkDestroyDevice)real_vkGetDeviceProcAddr(device, "vkDestroyDevice");
    }
    if (device == g_Device) {
        if (g_ImGuiInitialized) {
            ImGui_ImplVulkan_Shutdown();
            if (g_SDL3BackendInitialized) {
                ImGui_ImplSDL3_Shutdown();
                g_SDL3BackendInitialized = false;
            }
            ImGui::DestroyContext();
            g_ImGuiInitialized = false;
        }
        if (g_CommandPool && real_vkGetDeviceProcAddr) {
            PFN_vkDestroyCommandPool pfn_destroy_pool = (PFN_vkDestroyCommandPool)real_vkGetDeviceProcAddr(device, "vkDestroyCommandPool");
            if (pfn_destroy_pool) pfn_destroy_pool(device, g_CommandPool, nullptr);
            g_CommandPool = VK_NULL_HANDLE;
        }
        g_Device = VK_NULL_HANDLE;
    }
    if (real_vkDestroyDevice) real_vkDestroyDevice(device, pAllocator);
}

static VkResult Hook_vkCreateSwapchainKHR(VkDevice device, const VkSwapchainCreateInfoKHR* pCreateInfo, const VkAllocationCallbacks* pAllocator, VkSwapchainKHR* pSwapchain) {
    InitRealFunctions();
    if (!real_vkGetDeviceProcAddr && real_vkGetInstanceProcAddr && g_Instance) {
        real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_vkGetInstanceProcAddr(g_Instance, "vkGetDeviceProcAddr");
    }
    if (!real_vkCreateSwapchainKHR && real_vkGetDeviceProcAddr && device) {
        real_vkCreateSwapchainKHR = (PFN_vkCreateSwapchainKHR)real_vkGetDeviceProcAddr(device, "vkCreateSwapchainKHR");
    }

    // Ensure VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT is enabled on the swapchain so we can render ImGui onto swapchain images
    VkSwapchainCreateInfoKHR modified_info;
    const VkSwapchainCreateInfoKHR* info_to_use = pCreateInfo;
    if (pCreateInfo) {
        modified_info = *pCreateInfo;
        modified_info.imageUsage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
        info_to_use = &modified_info;
    }

    VkResult res = real_vkCreateSwapchainKHR ? real_vkCreateSwapchainKHR(device, info_to_use, pAllocator, pSwapchain) : VK_ERROR_INITIALIZATION_FAILED;
    if (res != VK_SUCCESS && real_vkCreateSwapchainKHR && pCreateInfo) {
        res = real_vkCreateSwapchainKHR(device, pCreateInfo, pAllocator, pSwapchain);
    }

    if (res == VK_SUCCESS && pSwapchain && *pSwapchain) {
        std::lock_guard<std::mutex> lock(g_Mutex);
        g_Device = device;
        g_Swapchain.swapchain = *pSwapchain;
        if (pCreateInfo) {
            g_SwapchainFormat = pCreateInfo->imageFormat;
            g_Width = pCreateInfo->imageExtent.width;
            g_Height = pCreateInfo->imageExtent.height;
        }

        if (real_vkGetDeviceProcAddr) {
            PFN_vkDestroyImageView pfn_destroy_view = (PFN_vkDestroyImageView)real_vkGetDeviceProcAddr(device, "vkDestroyImageView");
            PFN_vkDestroyFence pfn_destroy_fence = (PFN_vkDestroyFence)real_vkGetDeviceProcAddr(device, "vkDestroyFence");
            PFN_vkFreeCommandBuffers pfn_free_cmd = (PFN_vkFreeCommandBuffers)real_vkGetDeviceProcAddr(device, "vkFreeCommandBuffers");

            if (!g_Swapchain.views.empty()) {
                for (auto& v : g_Swapchain.views) {
                    if (v && pfn_destroy_view) pfn_destroy_view(device, v, nullptr);
                }
                g_Swapchain.views.clear();
            }
            if (!g_Swapchain.fences.empty()) {
                for (auto& f : g_Swapchain.fences) {
                    if (f && pfn_destroy_fence) pfn_destroy_fence(device, f, nullptr);
                }
                g_Swapchain.fences.clear();
            }
            if (!g_Swapchain.command_buffers.empty() && g_CommandPool && pfn_free_cmd) {
                pfn_free_cmd(device, g_CommandPool, (uint32_t)g_Swapchain.command_buffers.size(), g_Swapchain.command_buffers.data());
            }
            g_Swapchain.images.clear();
            g_Swapchain.command_buffers.clear();

            PFN_vkGetSwapchainImagesKHR pfn_get_images = (PFN_vkGetSwapchainImagesKHR)real_vkGetDeviceProcAddr(device, "vkGetSwapchainImagesKHR");
            PFN_vkCreateImageView pfn_create_view = (PFN_vkCreateImageView)real_vkGetDeviceProcAddr(device, "vkCreateImageView");
            PFN_vkCreateCommandPool pfn_create_pool = (PFN_vkCreateCommandPool)real_vkGetDeviceProcAddr(device, "vkCreateCommandPool");
            PFN_vkAllocateCommandBuffers pfn_alloc_cmd = (PFN_vkAllocateCommandBuffers)real_vkGetDeviceProcAddr(device, "vkAllocateCommandBuffers");
            PFN_vkCreateFence pfn_create_fence = (PFN_vkCreateFence)real_vkGetDeviceProcAddr(device, "vkCreateFence");

            uint32_t count = 0;
            uint32_t views_ok = 0;
            if (pfn_get_images) {
                pfn_get_images(device, *pSwapchain, &count, nullptr);
                if (count > 0) {
                    g_Swapchain.images.resize(count);
                    pfn_get_images(device, *pSwapchain, &count, g_Swapchain.images.data());

                    g_Swapchain.views.resize(count, VK_NULL_HANDLE);
                    for (uint32_t i = 0; i < count; i++) {
                        VkImageViewCreateInfo view_info = {};
                        view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
                        view_info.image = g_Swapchain.images[i];
                        view_info.viewType = VK_IMAGE_VIEW_TYPE_2D;
                        view_info.format = g_SwapchainFormat;
                        view_info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                        view_info.subresourceRange.levelCount = 1;
                        view_info.subresourceRange.layerCount = 1;
                        if (pfn_create_view && pfn_create_view(device, &view_info, nullptr, &g_Swapchain.views[i]) == VK_SUCCESS) {
                            views_ok++;
                        }
                    }

                    if (!g_CommandPool && pfn_create_pool) {
                        VkCommandPoolCreateInfo cp_info = {};
                        cp_info.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
                        cp_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
                        cp_info.queueFamilyIndex = g_QueueFamily;
                        pfn_create_pool(device, &cp_info, nullptr, &g_CommandPool);
                    }

                    if (g_CommandPool && pfn_alloc_cmd) {
                        g_Swapchain.command_buffers.resize(count, VK_NULL_HANDLE);
                        VkCommandBufferAllocateInfo alloc_info = {};
                        alloc_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
                        alloc_info.commandPool = g_CommandPool;
                        alloc_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
                        alloc_info.commandBufferCount = count;
                        pfn_alloc_cmd(device, &alloc_info, g_Swapchain.command_buffers.data());
                    }

                    if (pfn_create_fence) {
                        g_Swapchain.fences.resize(count, VK_NULL_HANDLE);
                        for (uint32_t i = 0; i < count; i++) {
                            VkFenceCreateInfo f_info = {};
                            f_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
                            pfn_create_fence(device, &f_info, nullptr, &g_Swapchain.fences[i]);
                        }
                    }

                    if (g_ImGuiInitialized) {
                        ImGui_ImplVulkan_SetMinImageCount(count >= 2 ? count : 2);
                    }
                }
            }
            OverlayLog("[overlay] vkCreateSwapchainKHR: swapchain=%p (%ux%u fmt=%d usage=0x%x) images=%u views_ok=%u\n",
                       (void*)*pSwapchain, g_Width, g_Height, (int)g_SwapchainFormat,
                       info_to_use ? (unsigned)info_to_use->imageUsage : 0u, count, views_ok);
        }
    }
    return res;
}

static void Hook_vkDestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain, const VkAllocationCallbacks* pAllocator) {
    InitRealFunctions();
    if (!real_vkDestroySwapchainKHR && real_vkGetDeviceProcAddr && device) {
        real_vkDestroySwapchainKHR = (PFN_vkDestroySwapchainKHR)real_vkGetDeviceProcAddr(device, "vkDestroySwapchainKHR");
    }
    {
        std::lock_guard<std::mutex> lock(g_Mutex);
        if (swapchain != VK_NULL_HANDLE && swapchain == g_Swapchain.swapchain) {
            OverlayLog("[overlay] vkDestroySwapchainKHR: destroying active swapchain=%p\n", (void*)swapchain);
            if (real_vkGetDeviceProcAddr && device) {
                PFN_vkDestroyImageView pfn_destroy_view = (PFN_vkDestroyImageView)real_vkGetDeviceProcAddr(device, "vkDestroyImageView");
                PFN_vkDestroyFence pfn_destroy_fence = (PFN_vkDestroyFence)real_vkGetDeviceProcAddr(device, "vkDestroyFence");
                PFN_vkFreeCommandBuffers pfn_free_cmd = (PFN_vkFreeCommandBuffers)real_vkGetDeviceProcAddr(device, "vkFreeCommandBuffers");

                for (auto& v : g_Swapchain.views) {
                    if (v && pfn_destroy_view) pfn_destroy_view(device, v, nullptr);
                }
                for (auto& f : g_Swapchain.fences) {
                    if (f && pfn_destroy_fence) pfn_destroy_fence(device, f, nullptr);
                }
                if (!g_Swapchain.command_buffers.empty() && g_CommandPool && pfn_free_cmd) {
                    pfn_free_cmd(device, g_CommandPool, (uint32_t)g_Swapchain.command_buffers.size(), g_Swapchain.command_buffers.data());
                }
            }
            g_Swapchain.swapchain = VK_NULL_HANDLE;
            g_Swapchain.views.clear();
            g_Swapchain.images.clear();
            g_Swapchain.command_buffers.clear();
            g_Swapchain.fences.clear();
        } else {
            OverlayLog("[overlay] vkDestroySwapchainKHR: ignoring retired oldSwapchain=%p (active=%p)\n",
                       (void*)swapchain, (void*)g_Swapchain.swapchain);
        }
    }
    if (real_vkDestroySwapchainKHR) real_vkDestroySwapchainKHR(device, swapchain, pAllocator);
}

static VkResult Hook_vkQueuePresentKHR(VkQueue queue, const VkPresentInfoKHR* pPresentInfo) {
    InitRealFunctions();
    if (!real_vkQueuePresentKHR && real_vkGetDeviceProcAddr && g_Device) {
        real_vkQueuePresentKHR = (PFN_vkQueuePresentKHR)real_vkGetDeviceProcAddr(g_Device, "vkQueuePresentKHR");
    }

    g_Queue = queue;

    // Also poll Hookshot's internal Win32 VK key table (0x71 == VK_F2, same table mkwvk uses for F1)
    if (g_HosPosixKeys) {
        static bool s_PrevVkF2 = false;
        bool vk_f2_down = (g_HosPosixKeys[0x71] != 0);
        if (vk_f2_down && !s_PrevVkF2) {
            ToggleOverlay("hos_posix_keys[VK_F2]");
        }
        s_PrevVkF2 = vk_f2_down;
    }

    if (!g_ImGuiInitialized && g_Device && g_Instance && !g_Swapchain.images.empty()) {
        InitImGuiVulkan();
    }

    if (g_ImGuiInitialized && !g_SDL3BackendInitialized && g_Window) {
        std::lock_guard<std::mutex> lock(g_Mutex);
        if (!g_SDL3BackendInitialized && ImGui_ImplSDL3_InitForVulkan(g_Window)) {
            ImGui_ImplSDL3_SetGamepadMode(ImGui_ImplSDL3_GamepadMode_Manual, nullptr, 0);
            ImGui_ImplSDL3_SetMouseCaptureMode(ImGui_ImplSDL3_MouseCaptureMode_Disabled);
            g_SDL3BackendInitialized = true;
            OverlayLog("[overlay] Late-initialized ImGui_ImplSDL3 for window=%p\n", (void*)g_Window);
        }
    }

    if (g_ImGuiInitialized && (g_ShowOverlay || g_IsGoronTransformed) && pPresentInfo && pPresentInfo->swapchainCount > 0 && pPresentInfo->pImageIndices) {
        std::lock_guard<std::mutex> lock(g_Mutex);
        uint32_t img_idx = pPresentInfo->pImageIndices[0];

        if (img_idx < g_Swapchain.views.size() && g_Swapchain.views[img_idx] != VK_NULL_HANDLE &&
            img_idx < g_Swapchain.command_buffers.size() && img_idx < g_Swapchain.fences.size()) {
            ImGuiIO& io = ImGui::GetIO();
            io.MouseDrawCursor = g_ShowOverlay;

            ImGui_ImplVulkan_NewFrame();
            if (g_SDL3BackendInitialized) {
                ImGui_ImplSDL3_NewFrame();
            }
            if (io.DisplaySize.x <= 0.0f || io.DisplaySize.y <= 0.0f) {
                io.DisplaySize = ImVec2((float)g_Width, (float)g_Height);
            }
            ImGui::NewFrame();

            if (g_ShowOverlay) {
                RenderPlaygroundUI();
            }

            if (g_IsGoronTransformed) {
                RenderGoronHUD();
            }

            ImGui::Render();

            ImDrawData* draw_data = ImGui::GetDrawData();
            // Upload any pending font/atlas textures BEFORE beginning our render command buffer
            if (draw_data && draw_data->Textures != nullptr) {
                for (ImTextureData* tex : *draw_data->Textures) {
                    if (tex->Status != ImTextureStatus_OK) {
                        ImGui_ImplVulkan_UpdateTexture(tex);
                    }
                }
            }

            VkCommandBuffer cmd = g_Swapchain.command_buffers[img_idx];
            VkImageView view = g_Swapchain.views[img_idx];
            VkImage image = g_Swapchain.images[img_idx];
            VkFence fence = g_Swapchain.fences[img_idx];

            PFN_vkResetCommandBuffer pfn_reset_cmd = (PFN_vkResetCommandBuffer)real_vkGetDeviceProcAddr(g_Device, "vkResetCommandBuffer");
            PFN_vkBeginCommandBuffer pfn_begin_cmd = (PFN_vkBeginCommandBuffer)real_vkGetDeviceProcAddr(g_Device, "vkBeginCommandBuffer");
            PFN_vkEndCommandBuffer pfn_end_cmd = (PFN_vkEndCommandBuffer)real_vkGetDeviceProcAddr(g_Device, "vkEndCommandBuffer");
            PFN_vkCmdPipelineBarrier pfn_barrier = (PFN_vkCmdPipelineBarrier)real_vkGetDeviceProcAddr(g_Device, "vkCmdPipelineBarrier");
            PFN_vkQueueSubmit pfn_queue_submit = (PFN_vkQueueSubmit)real_vkGetDeviceProcAddr(g_Device, "vkQueueSubmit");
            PFN_vkWaitForFences pfn_wait_fence = (PFN_vkWaitForFences)real_vkGetDeviceProcAddr(g_Device, "vkWaitForFences");
            PFN_vkResetFences pfn_reset_fence = (PFN_vkResetFences)real_vkGetDeviceProcAddr(g_Device, "vkResetFences");

            if (!pfn_vkCmdBeginRendering && g_Device) {
                pfn_vkCmdBeginRendering = (PFN_vkCmdBeginRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdBeginRendering");
                if (!pfn_vkCmdBeginRendering)
                    pfn_vkCmdBeginRendering = (PFN_vkCmdBeginRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdBeginRenderingKHR");
                pfn_vkCmdEndRendering = (PFN_vkCmdEndRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdEndRendering");
                if (!pfn_vkCmdEndRendering)
                    pfn_vkCmdEndRendering = (PFN_vkCmdEndRenderingKHR)real_vkGetDeviceProcAddr(g_Device, "vkCmdEndRenderingKHR");
            }

            if (draw_data && pfn_reset_cmd && pfn_begin_cmd && pfn_end_cmd && pfn_barrier && pfn_vkCmdBeginRendering && pfn_vkCmdEndRendering) {
                pfn_reset_cmd(cmd, 0);

                VkCommandBufferBeginInfo begin_info = {};
                begin_info.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
                begin_info.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
                pfn_begin_cmd(cmd, &begin_info);

                // Transition swapchain image from PRESENT_SRC_KHR to COLOR_ATTACHMENT_OPTIMAL
                VkImageMemoryBarrier b1 = {};
                b1.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                b1.srcAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
                b1.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
                b1.oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
                b1.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                b1.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                b1.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                b1.image = image;
                b1.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                b1.subresourceRange.levelCount = 1;
                b1.subresourceRange.layerCount = 1;
                pfn_barrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, nullptr, 0, nullptr, 1, &b1);

                // Dynamic Rendering
                VkRenderingAttachmentInfo att = {};
                att.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
                att.imageView = view;
                att.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                att.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
                att.storeOp = VK_ATTACHMENT_STORE_OP_STORE;

                VkRenderingInfo ri = {};
                ri.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
                ri.renderArea.offset = { 0, 0 };
                ri.renderArea.extent = { g_Width, g_Height };
                ri.layerCount = 1;
                ri.colorAttachmentCount = 1;
                ri.pColorAttachments = &att;

                pfn_vkCmdBeginRendering(cmd, &ri);
                ImGui_ImplVulkan_RenderDrawData(draw_data, cmd);
                pfn_vkCmdEndRendering(cmd);

                // Transition back to PRESENT_SRC_KHR
                VkImageMemoryBarrier b2 = {};
                b2.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                b2.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
                b2.dstAccessMask = VK_ACCESS_MEMORY_READ_BIT;
                b2.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
                b2.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
                b2.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                b2.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                b2.image = image;
                b2.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
                b2.subresourceRange.levelCount = 1;
                b2.subresourceRange.layerCount = 1;
                pfn_barrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, nullptr, 0, nullptr, 1, &b2);

                pfn_end_cmd(cmd);

                VkSubmitInfo submit = {};
                submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
                submit.commandBufferCount = 1;
                submit.pCommandBuffers = &cmd;
                if (pfn_queue_submit) {
                    if (pfn_queue_submit(queue, 1, &submit, fence) == VK_SUCCESS) {
                        if (pfn_wait_fence && pfn_reset_fence) {
                            pfn_wait_fence(g_Device, 1, &fence, VK_TRUE, UINT64_MAX);
                            pfn_reset_fence(g_Device, 1, &fence);
                        }
                    }
                }
            }
        }
    }

    return real_vkQueuePresentKHR ? real_vkQueuePresentKHR(queue, pPresentInfo) : VK_ERROR_INITIALIZATION_FAILED;
}

static PFN_vkVoidFunction Hook_vkGetInstanceProcAddr(VkInstance instance, const char* pName) {
    InitRealFunctions();
    if (!pName) return nullptr;

    if (strcmp(pName, "vkGetInstanceProcAddr") == 0) return (PFN_vkVoidFunction)Hook_vkGetInstanceProcAddr;

    PFN_vkVoidFunction real_fn = real_vkGetInstanceProcAddr ? real_vkGetInstanceProcAddr(instance, pName) : nullptr;
    if (!real_fn) return nullptr;

    if (strcmp(pName, "vkGetDeviceProcAddr") == 0) {
        real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_fn;
        return (PFN_vkVoidFunction)Hook_vkGetDeviceProcAddr;
    }
    if (strcmp(pName, "vkCreateInstance") == 0) {
        real_vkCreateInstance = (PFN_vkCreateInstance)real_fn;
        return (PFN_vkVoidFunction)Hook_vkCreateInstance;
    }
    if (strcmp(pName, "vkDestroyInstance") == 0) {
        real_vkDestroyInstance = (PFN_vkDestroyInstance)real_fn;
        return (PFN_vkVoidFunction)Hook_vkDestroyInstance;
    }
    if (strcmp(pName, "vkCreateDevice") == 0) {
        real_vkCreateDevice = (PFN_vkCreateDevice)real_fn;
        return (PFN_vkVoidFunction)Hook_vkCreateDevice;
    }
    if (strcmp(pName, "vkDestroyDevice") == 0) {
        real_vkDestroyDevice = (PFN_vkDestroyDevice)real_fn;
        return (PFN_vkVoidFunction)Hook_vkDestroyDevice;
    }
    if (strcmp(pName, "vkCreateSwapchainKHR") == 0) {
        real_vkCreateSwapchainKHR = (PFN_vkCreateSwapchainKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkCreateSwapchainKHR;
    }
    if (strcmp(pName, "vkDestroySwapchainKHR") == 0) {
        real_vkDestroySwapchainKHR = (PFN_vkDestroySwapchainKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkDestroySwapchainKHR;
    }
    if (strcmp(pName, "vkQueuePresentKHR") == 0) {
        real_vkQueuePresentKHR = (PFN_vkQueuePresentKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkQueuePresentKHR;
    }

    return real_fn;
}

static PFN_vkVoidFunction Hook_vkGetDeviceProcAddr(VkDevice device, const char* pName) {
    InitRealFunctions();
    if (!pName) return nullptr;

    if (!real_vkGetDeviceProcAddr && real_vkGetInstanceProcAddr && g_Instance) {
        real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_vkGetInstanceProcAddr(g_Instance, "vkGetDeviceProcAddr");
    }

    if (strcmp(pName, "vkGetDeviceProcAddr") == 0) return (PFN_vkVoidFunction)Hook_vkGetDeviceProcAddr;

    PFN_vkVoidFunction real_fn = real_vkGetDeviceProcAddr ? real_vkGetDeviceProcAddr(device, pName) : nullptr;
    if (!real_fn) return nullptr;

    if (strcmp(pName, "vkDestroyDevice") == 0) {
        real_vkDestroyDevice = (PFN_vkDestroyDevice)real_fn;
        return (PFN_vkVoidFunction)Hook_vkDestroyDevice;
    }
    if (strcmp(pName, "vkCreateSwapchainKHR") == 0) {
        real_vkCreateSwapchainKHR = (PFN_vkCreateSwapchainKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkCreateSwapchainKHR;
    }
    if (strcmp(pName, "vkDestroySwapchainKHR") == 0) {
        real_vkDestroySwapchainKHR = (PFN_vkDestroySwapchainKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkDestroySwapchainKHR;
    }
    if (strcmp(pName, "vkQueuePresentKHR") == 0) {
        real_vkQueuePresentKHR = (PFN_vkQueuePresentKHR)real_fn;
        return (PFN_vkVoidFunction)Hook_vkQueuePresentKHR;
    }

    return real_fn;
}

// Only dlsym is exported in the shared library's dynamic symbol table
extern "C" __attribute__((visibility("default"))) void* dlsym(void* handle, const char* name) {
    EnsureRealDlsym();
    void* real_sym = real_dlsym ? real_dlsym(handle, name) : nullptr;
    if (!real_sym) return nullptr;

    // Check if this symbol comes from libvulkan.so (and not an internal Vulkan layer/ICD)
    if (strncmp(name, "vk", 2) == 0) {
        Dl_info info = {};
        if (dladdr(real_sym, &info) && info.dli_fname && strstr(info.dli_fname, "libvulkan.so")) {
            if (strcmp(name, "vkGetInstanceProcAddr") == 0) {
                real_vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)real_sym;
                return (void*)Hook_vkGetInstanceProcAddr;
            }
            if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
                real_vkGetDeviceProcAddr = (PFN_vkGetDeviceProcAddr)real_sym;
                return (void*)Hook_vkGetDeviceProcAddr;
            }
            if (strcmp(name, "vkCreateInstance") == 0) {
                real_vkCreateInstance = (PFN_vkCreateInstance)real_sym;
                return (void*)Hook_vkCreateInstance;
            }
            if (strcmp(name, "vkDestroyInstance") == 0) {
                real_vkDestroyInstance = (PFN_vkDestroyInstance)real_sym;
                return (void*)Hook_vkDestroyInstance;
            }
            if (strcmp(name, "vkCreateDevice") == 0) {
                real_vkCreateDevice = (PFN_vkCreateDevice)real_sym;
                return (void*)Hook_vkCreateDevice;
            }
            if (strcmp(name, "vkDestroyDevice") == 0) {
                real_vkDestroyDevice = (PFN_vkDestroyDevice)real_sym;
                return (void*)Hook_vkDestroyDevice;
            }
            if (strcmp(name, "vkCreateSwapchainKHR") == 0) {
                real_vkCreateSwapchainKHR = (PFN_vkCreateSwapchainKHR)real_sym;
                return (void*)Hook_vkCreateSwapchainKHR;
            }
            if (strcmp(name, "vkDestroySwapchainKHR") == 0) {
                real_vkDestroySwapchainKHR = (PFN_vkDestroySwapchainKHR)real_sym;
                return (void*)Hook_vkDestroySwapchainKHR;
            }
            if (strcmp(name, "vkQueuePresentKHR") == 0) {
                real_vkQueuePresentKHR = (PFN_vkQueuePresentKHR)real_sym;
                return (void*)Hook_vkQueuePresentKHR;
            }
        }
        return real_sym;
    }

    // Intercept SDL3 window/input functions resolved by Hookshot
    if (strncmp(name, "SDL_", 4) == 0) {
        if (strcmp(name, "SDL_CreateWindow") == 0) {
            real_SDL_CreateWindow = (SDL_Window* (*)(const char*, int, int, SDL_WindowFlags))real_sym;
            return (void*)Hook_SDL_CreateWindow;
        }
        if (strcmp(name, "SDL_PollEvent") == 0) {
            real_SDL_PollEvent = (bool (*)(SDL_Event*))real_sym;
            return (void*)Hook_SDL_PollEvent;
        }
        if (strcmp(name, "SDL_WaitEventTimeout") == 0) {
            real_SDL_WaitEventTimeout = (bool (*)(SDL_Event*, Sint32))real_sym;
            return (void*)Hook_SDL_WaitEventTimeout;
        }
        if (strcmp(name, "SDL_SetWindowRelativeMouseMode") == 0) {
            real_SDL_SetWindowRelativeMouseMode = (bool (*)(SDL_Window*, bool))real_sym;
            return (void*)Hook_SDL_SetWindowRelativeMouseMode;
        }
        if (strcmp(name, "SDL_GetMouseState") == 0) {
            real_SDL_GetMouseState = (SDL_MouseButtonFlags (*)(float*, float*))real_sym;
            return (void*)Hook_SDL_GetMouseState;
        }
        if (strcmp(name, "SDL_GetKeyboardState") == 0) {
            real_SDL_GetKeyboardState = (const bool* (*)(int*))real_sym;
            return (void*)Hook_SDL_GetKeyboardState;
        }
    }

    return real_sym;
}

// Goron Link gameplay hooks (MM 1:1 roll and punch combo) live in goron_hooks.inc
#include "goron_hooks.inc"
// Song of Healing (mod 040-SongOfHealing)
#include "song_of_healing.inc"
// Item Controls moved to its own Vulkan layer: plugins/item_controls (mod 050-ItemControls/layer)
