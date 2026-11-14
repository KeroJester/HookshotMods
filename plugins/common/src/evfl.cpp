// See evfl.h.
#include "evfl.h"
#include <cstring>
#include <vector>

namespace evfl {

namespace {
struct File {
    gamefiles::Bytes& d;
    template <class T> T Get(size_t o) const { T v{}; if (o + sizeof(T) <= d.size()) memcpy(&v, &d[o], sizeof(T)); return v; }
    template <class T> void Put(size_t o, T v) { if (o + sizeof(T) <= d.size()) memcpy(&d[o], &v, sizeof(T)); }
    std::string Str(uint64_t o) const {
        const uint16_t n = Get<uint16_t>(o);
        return o + 2 + n <= d.size() ? std::string((const char*)&d[o + 2], n) : std::string();
    }
};
}  // namespace

int AddCase(gamefiles::Bytes& file, const std::string& actor, const std::string& query, uint32_t value, uint32_t copyFrom,
            const std::set<std::string>& skip, std::string* changedNames) {
    File f{file};
    if (file.size() < 0x40 || memcmp(file.data(), "BFEVFL\0\0", 8) || f.Get<uint16_t>(0x14) != 0) return -1;
    const uint64_t fc = f.Get<uint64_t>(f.Get<uint64_t>(0x28));
    if (fc + 0x38 > file.size() || memcmp(&file[fc], "EVFL", 4)) return -1;
    const uint16_t nActors = f.Get<uint16_t>(fc + 0x10), nEvents = f.Get<uint16_t>(fc + 0x16);
    const uint64_t actors = f.Get<uint64_t>(fc + 0x28), events = f.Get<uint64_t>(fc + 0x30);
    // the actor and its query index
    int actorIdx = -1, queryIdx = -1;
    for (int a = 0; a < nActors && actorIdx < 0; a++) {
        const uint64_t ar = actors + 0x38ull * a;
        if (f.Str(f.Get<uint64_t>(ar)) != actor) continue;
        const uint64_t qs = f.Get<uint64_t>(ar + 0x20);
        for (int q = 0; q < f.Get<uint16_t>(ar + 0x32); q++)
            if (f.Str(f.Get<uint64_t>(qs + 8ull * q)) == query) { actorIdx = a, queryIdx = q; break; }
    }
    if (actorIdx < 0) return 0;
    struct Change { uint64_t ev; std::vector<std::pair<uint32_t, uint16_t>> cases; };
    std::vector<Change> todo;
    for (int i = 0; i < nEvents; i++) {
        const uint64_t ev = events + 0x30ull * i;
        if (f.Get<uint8_t>(ev + 8) != 1 || f.Get<uint16_t>(ev + 0xc) != actorIdx || f.Get<uint16_t>(ev + 0xe) != queryIdx) continue;
        const std::string name = f.Str(f.Get<uint64_t>(ev));
        if (skip.count(name)) continue;
        const uint16_t n = f.Get<uint16_t>(ev + 0xa);
        const uint64_t tab = f.Get<uint64_t>(ev + 0x18);
        Change ch{ev, {}};
        int target = -1;
        bool has = false;
        for (int k = 0; k < n; k++) {
            const uint32_t v = f.Get<uint32_t>(tab + 8ull * k);
            const uint16_t e = f.Get<uint16_t>(tab + 8ull * k + 4);
            ch.cases.emplace_back(v, e);
            if (v == copyFrom) target = e;
            if (v == value) has = true;
        }
        if (has || target < 0) continue;
        ch.cases.emplace_back(value, (uint16_t)target);
        todo.push_back(std::move(ch));
        if (changedNames) *changedNames += (changedNames->empty() ? "" : " ") + name;
    }
    for (auto& ch : todo) {
        while (file.size() % 8) file.push_back(0);
        const uint64_t at = file.size();
        for (auto& [v, e] : ch.cases) {
            uint8_t rec[8] = {};
            memcpy(rec, &v, 4), memcpy(rec + 4, &e, 2);
            file.insert(file.end(), rec, rec + 8);
        }
        f.Put<uint16_t>(ch.ev + 0xa, (uint16_t)ch.cases.size());
        f.Put<uint64_t>(ch.ev + 0x18, at);
    }
    if (!todo.empty()) f.Put<uint32_t>(0x1c, (uint32_t)file.size());
    return (int)todo.size();
}

}  // namespace evfl
