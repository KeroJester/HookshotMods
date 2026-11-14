// See vfs.h. Hookshot's file functions (host/nn_fs.c, all GuestFn-shaped HLE entries):
//   OpenFile(FileHandle* out, const char* path, int mode)            x0 out, x1 path ("content:/UI/..."), x2 mode
//   ReadFile(FileHandle, long off, void* buf, u64 size) -> Result    x0..x3   (must read it all: 0x2EE202 otherwise)
//   ReadFile(u64* read, FileHandle, long off, void* buf, u64 size[, const ReadOption&])  x0 read, x1..x4
//   GetFileSize(long* out, FileHandle)                               x0 out, x1 handle
//   CloseFile(FileHandle)                                            x0
// Data written into guest memory is reported to Hookshot's write watch (hos_mark_written), as its own reads do.
#include "vfs.h"
#include "hook.h"
#include "platform.h"
#include <algorithm>
#include <cstring>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>

namespace vfs {
using namespace plat;
using gamefiles::Bytes;

struct Entry {
    std::string rel, key;                 // RomFS path; lower case, for matching
    Patch patch;
    int state = 0;                        // 0 not made, 1 being made, 2 made
    std::shared_ptr<const Bytes> data;    // the changed copy (null: serve the original)
};
static std::vector<Entry> g_Entries;
static std::mutex g_Mu;
static std::condition_variable g_Made;
static std::unordered_map<uint64_t, std::shared_ptr<const Bytes>> g_Open;   // FileHandle -> served copy

typedef void (*MarkWrittenFn)(const void* p, size_t n);
static MarkWrittenFn g_MarkWritten;
static GuestFn o_Open, o_Read4, o_Read5, o_Read6, o_Size, o_Close;

static std::string Lower(std::string s) {
    for (auto& ch : s) ch = (char)((ch >= 'A' && ch <= 'Z') ? ch + 32 : (ch == '\\' ? '/' : ch));
    return s;
}

void Register(const std::string& rel, Patch patch) {
    Entry e;
    e.rel = rel;
    e.key = Lower(rel);
    e.patch = std::move(patch);
    g_Entries.push_back(std::move(e));
}

static void Make(std::unique_lock<std::mutex>& lk, Entry& e) {   // (g_Mu held; released while working)
    if (e.state) return;
    e.state = 1;                          // (claimed: others wait on g_Made)
    Patch patch = e.patch;
    std::string rel = e.rel;
    lk.unlock();
    std::shared_ptr<Bytes> out;
    Bytes file;
    std::string path = gamefiles::RomfsPath(rel);
    if (path.empty() || !gamefiles::ReadFile(path, file)) Log("[vfs] %s: not readable (%s)\n", rel.c_str(), path.c_str());
    else if (!patch(file)) Log("[vfs] %s: left unchanged\n", rel.c_str());
    else { out = std::make_shared<Bytes>(std::move(file)); Log("[vfs] %s: changed copy ready (%zu bytes, from %s)\n", rel.c_str(), out->size(), path.c_str()); }
    lk.lock();
    e.data = out;
    e.state = 2;
    g_Made.notify_all();
}

static Entry* Find(const char* guestPath) {
    const char* rest = strstr(guestPath, ":/");
    if (!rest) return nullptr;
    rest += 2;
    while (*rest == '/') rest++;
    std::string key = Lower(rest);
    for (auto& e : g_Entries) if (e.key == key) return &e;
    return nullptr;
}

static void OpenHook(GuestCtx* c) {
    const uint64_t outp = c->x[0], pathp = c->x[1], mode = c->x[2];
    o_Open(c);
    if (c->x[0] != 0 || (mode & 6) || !GuestPtrOk(pathp) || !GuestPtrOk(outp)) return;
    char path[512];
    size_t n = 0;
    for (; n + 1 < sizeof path; n++) { path[n] = (char)GRead<uint8_t>(pathp + n); if (!path[n]) break; }
    path[n] = 0;
    std::unique_lock<std::mutex> lk(g_Mu);
    Entry* e = Find(path);
    if (!e) return;
    Make(lk, *e);                         // (made here if the worker has not got to it yet)
    g_Made.wait(lk, [&] { return e->state == 2; });
    if (e->data) {
        g_Open[GRead<uint64_t>(outp)] = e->data;
        static int logged;
        if (logged++ < 20) Log("[vfs] the game opened %s: reads the changed copy\n", e->rel.c_str());
    }
}

static std::shared_ptr<const Bytes> Served(uint64_t handle) {
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Open.find(handle);
    return it == g_Open.end() ? nullptr : it->second;
}

static uint64_t Copy(const Bytes& d, int64_t off, uint64_t buf, uint64_t size) {
    if (off < 0 || (uint64_t)off >= d.size() || !GuestPtrOk(buf)) return 0;
    uint64_t n = std::min<uint64_t>(size, d.size() - (uint64_t)off);
    memcpy(GPtr(buf), d.data() + off, n);
    if (g_MarkWritten) g_MarkWritten(GPtr(buf), n);
    return n;
}

static void Read4Hook(GuestCtx* c) {      // ReadFile(FileHandle, off, buf, size) -> Result
    auto d = Served(c->x[0]);
    if (!d) return o_Read4(c);
    uint64_t want = c->x[3], n = Copy(*d, (int64_t)c->x[1], c->x[2], want);
    c->x[0] = n == want ? 0 : 0x2EE202;
}
static void ReadN(GuestCtx* c, GuestFn orig) {   // ReadFile(u64* read, FileHandle, off, buf, size[, option])
    auto d = Served(c->x[1]);
    if (!d) return orig(c);
    uint64_t n = Copy(*d, (int64_t)c->x[2], c->x[3], c->x[4]);
    if (GuestPtrOk(c->x[0])) GWrite<uint64_t>(c->x[0], n);
    c->x[0] = 0;
}
static void Read5Hook(GuestCtx* c) { ReadN(c, o_Read5); }
static void Read6Hook(GuestCtx* c) { ReadN(c, o_Read6); }

static void SizeHook(GuestCtx* c) {
    auto d = Served(c->x[1]);
    if (!d) return o_Size(c);
    if (GuestPtrOk(c->x[0])) GWrite<uint64_t>(c->x[0], d->size());
    c->x[0] = 0;
}

static void CloseHook(GuestCtx* c) {
    uint64_t h = c->x[0];
    o_Close(c);
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Open.erase(h);
}

bool Start() {
    static const char* kOpen = "imp__ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci";
    static const char* kRead4 = "imp__ZN2nn2fs8ReadFileENS0_10FileHandleElPvm";
    static const char* kRead5 = "imp__ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm";
    static const char* kRead6 = "imp__ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE";
    static const char* kSize = "imp__ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE";
    static const char* kClose = "imp__ZN2nn2fs9CloseFileENS0_10FileHandleE";
    auto sym = ResolveSymbols({kOpen, kRead4, kRead5, kRead6, kSize, kClose, "hos_mark_written"});
    for (const char* n : {kOpen, kRead4, kRead5, kRead6, kSize, kClose})
        if (!sym.count(n)) { Log("[vfs] %s not found: game files stay unchanged\n", n); return false; }
    if (sym.count("hos_mark_written")) g_MarkWritten = (MarkWrittenFn)sym["hos_mark_written"];
    // the readers first, the opener last: a file is only served once every reader knows it
    o_Read4 = hook::Install(sym[kRead4], Read4Hook, kRead4);
    o_Read5 = hook::Install(sym[kRead5], Read5Hook, kRead5);
    o_Read6 = hook::Install(sym[kRead6], Read6Hook, kRead6);
    o_Size = hook::Install(sym[kSize], SizeHook, kSize);
    o_Close = hook::Install(sym[kClose], CloseHook, kClose);
    if (!o_Read4 || !o_Read5 || !o_Read6 || !o_Size || !o_Close) { Log("[vfs] reader hooks failed: game files stay unchanged\n"); return false; }
    o_Open = hook::Install(sym[kOpen], OpenHook, kOpen);
    if (!o_Open) { Log("[vfs] open hook failed: game files stay unchanged\n"); return false; }
    std::thread([] {                        // make the copies now, before the game asks for them
        std::unique_lock<std::mutex> lk(g_Mu);
        for (auto& e : g_Entries) Make(lk, e);
    }).detach();
    return true;
}

}  // namespace vfs
