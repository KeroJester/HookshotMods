// See byml.h.
#include "byml.h"
#include <algorithm>
#include <cstring>
#include <functional>
#include <map>
#include <set>

namespace byml {

Node* Node::Get(const std::string& key) {
    for (auto& kv : dict) if (kv.first == key) return &kv.second;
    return nullptr;
}
void Node::Set(const std::string& key, Node v) {
    if (Node* n = Get(key)) { *n = std::move(v); return; }
    dict.emplace_back(key, std::move(v));
}

// ---- reading ---------------------------------------------------------------------------------------------------------
namespace {
struct Reader {
    const std::vector<uint8_t>& d;
    std::vector<std::string> keys, strs;
    bool ok = true;
    uint32_t U32(size_t o) const { if (o + 4 > d.size()) return 0; uint32_t v; memcpy(&v, &d[o], 4); return v; }
    uint32_t U24(size_t o) const { return o + 3 > d.size() ? 0 : d[o] | d[o + 1] << 8 | d[o + 2] << 16; }
    uint64_t U64(size_t o) const { if (o + 8 > d.size()) return 0; uint64_t v; memcpy(&v, &d[o], 8); return v; }
    std::vector<std::string> StrTab(size_t off) {
        std::vector<std::string> out;
        if (!off) return out;
        if (off >= d.size() || d[off] != 0xC2) { ok = false; return out; }
        uint32_t n = U24(off + 1);
        for (uint32_t i = 0; i < n; i++) {
            size_t s = off + U32(off + 4 + 4 * i);
            size_t e = s;
            while (e < d.size() && d[e]) e++;
            out.emplace_back((const char*)&d[std::min(s, d.size())], e - std::min(s, e));
        }
        return out;
    }
    Node Value(uint8_t t, uint32_t raw, int depth) {
        Node n;
        n.type = (Type)t;
        switch (t) {
        case kStr: n.str = raw < strs.size() ? strs[raw] : std::string(); break;
        case kBool: case kI32: case kF32: case kU32: n.bits = raw; break;
        case kI64: case kU64: case kF64: n.bits = U64(raw); break;
        case kNull: break;
        case kBin: { uint32_t len = U32(raw); if (raw + 4 + (size_t)len <= d.size()) n.str.assign((const char*)&d[raw + 4], len); break; }
        case kBinAlign: { uint32_t len = U32(raw); n.bits = U32(raw + 4); if (raw + 8 + (size_t)len <= d.size()) n.str.assign((const char*)&d[raw + 8], len); break; }
        case kArray: case kDict: case kHash32: case kHash64: n = Container(raw, depth + 1); break;
        default: ok = false;
        }
        return n;
    }
    Node Container(size_t off, int depth) {
        Node n;
        if (off >= d.size() || depth > 64) { ok = false; return n; }
        uint8_t t = d[off];
        uint32_t cnt = U24(off + 1);
        n.type = (Type)t;
        if (t == kArray) {
            size_t vs = (off + 4 + cnt + 3) & ~(size_t)3;
            for (uint32_t i = 0; i < cnt && ok; i++) n.items.push_back(Value(d[off + 4 + i], U32(vs + 4 * i), depth));
        } else if (t == kDict) {
            for (uint32_t i = 0; i < cnt && ok; i++) {
                size_t p = off + 4 + 8 * i;
                uint32_t k = U24(p);
                n.dict.emplace_back(k < keys.size() ? keys[k] : std::string(), Value(d[p + 3], U32(p + 4), depth));
            }
        } else if (t == kHash32 || t == kHash64) {
            size_t hs = t == kHash32 ? 4 : 8, ent = hs + 4, types = off + 4 + cnt * ent;
            for (uint32_t i = 0; i < cnt && ok; i++) {
                size_t p = off + 4 + i * ent;
                uint64_t h = hs == 4 ? U32(p) : U64(p);
                n.hashed.emplace_back(h, Value(d[types + i], U32(p + hs), depth));
            }
        } else {
            ok = false;
        }
        return n;
    }
};
}  // namespace

bool Parse(const std::vector<uint8_t>& data, Node& root) {
    if (data.size() < 16 || data[0] != 'Y' || data[1] != 'B') return false;
    Reader r{data};
    r.keys = r.StrTab(r.U32(4));
    r.strs = r.StrTab(r.U32(8));
    uint32_t rootOff = r.U32(12);
    root = rootOff ? r.Container(rootOff, 0) : Node();
    return r.ok;
}

// ---- writing ---------------------------------------------------------------------------------------------------------
namespace {
struct Writer {
    std::vector<uint8_t> out;
    std::map<std::string, uint32_t> keyIdx, strIdx;
    void Align(size_t n) { while (out.size() % n) out.push_back(0); }
    void Put32(uint32_t v) { for (int i = 0; i < 4; i++) out.push_back(uint8_t(v >> (8 * i))); }
    void Put24(uint32_t v) { for (int i = 0; i < 3; i++) out.push_back(uint8_t(v >> (8 * i))); }
    void Patch32(size_t at, uint32_t v) { for (int i = 0; i < 4; i++) out[at + i] = uint8_t(v >> (8 * i)); }
    void Scan(const Node& n, std::set<std::string>& keys, std::set<std::string>& strs) {
        if (n.type == kStr) strs.insert(n.str);
        for (auto& x : n.items) Scan(x, keys, strs);
        for (auto& kv : n.dict) { keys.insert(kv.first); Scan(kv.second, keys, strs); }
        for (auto& kv : n.hashed) Scan(kv.second, keys, strs);
    }
    uint32_t StrTab(const std::map<std::string, uint32_t>& tab) {
        if (tab.empty()) return 0;
        Align(4);
        uint32_t start = (uint32_t)out.size();
        out.push_back(0xC2);
        Put24((uint32_t)tab.size());
        size_t table = out.size();
        out.resize(out.size() + 4 * (tab.size() + 1));
        size_t i = 0;
        for (auto& kv : tab) {                              // (std::map: sorted by bytes, as the format wants)
            Patch32(table + 4 * i++, (uint32_t)out.size() - start);
            out.insert(out.end(), kv.first.begin(), kv.first.end());
            out.push_back(0);
        }
        Patch32(table + 4 * i, (uint32_t)out.size() - start);
        Align(4);
        return start;
    }
    static bool Inline(Type t) { return t == kStr || t == kBool || t == kI32 || t == kF32 || t == kU32 || t == kNull; }
    uint32_t Word(const Node& n) { return n.type == kStr ? strIdx[n.str] : n.type == kNull ? 0 : (uint32_t)n.bits; }
    uint32_t Payload(const Node& n) {
        if (n.type == kI64 || n.type == kU64 || n.type == kF64) {
            Align(8);
            uint32_t p = (uint32_t)out.size();
            for (int i = 0; i < 8; i++) out.push_back(uint8_t(n.bits >> (8 * i)));
            return p;
        }
        if (n.type == kBin || n.type == kBinAlign) {
            Align(4);
            uint32_t p = (uint32_t)out.size();
            Put32((uint32_t)n.str.size());
            if (n.type == kBinAlign) Put32((uint32_t)n.bits);
            out.insert(out.end(), n.str.begin(), n.str.end());
            return p;
        }
        return Container(n);
    }
    uint32_t Container(const Node& n) {
        Align(4);
        uint32_t p = (uint32_t)out.size();
        std::vector<std::pair<size_t, const Node*>> pending;
        auto slot = [&](const Node& x) {
            pending.emplace_back(out.size(), Inline(x.type) ? nullptr : &x);
            Put32(Inline(x.type) ? Word(x) : 0);
        };
        if (n.type == kArray) {
            out.push_back(kArray);
            Put24((uint32_t)n.items.size());
            for (auto& x : n.items) out.push_back(x.type);
            Align(4);
            for (auto& x : n.items) slot(x);
        } else if (n.type == kDict) {
            std::vector<const std::pair<std::string, Node>*> items;
            for (auto& kv : n.dict) items.push_back(&kv);
            std::sort(items.begin(), items.end(), [&](auto* a, auto* b) { return keyIdx[a->first] < keyIdx[b->first]; });
            out.push_back(kDict);
            Put24((uint32_t)items.size());
            for (auto* kv : items) {
                Put24(keyIdx[kv->first]);
                out.push_back(kv->second.type);
                slot(kv->second);
            }
        } else {                                           // kHash32 / kHash64
            size_t hs = n.type == kHash32 ? 4 : 8;
            auto items = n.hashed;
            std::sort(items.begin(), items.end(), [](auto& a, auto& b) { return a.first < b.first; });
            out.push_back(n.type);
            Put24((uint32_t)items.size());
            std::vector<const Node*> vals;
            for (auto& kv : items) {
                for (size_t i = 0; i < hs; i++) out.push_back(uint8_t(kv.first >> (8 * i)));
                vals.push_back(&kv.second);
                slot(kv.second);
            }
            // (slot() kept pointers into the local copy: refer to the originals for the deferred payloads)
            for (size_t i = 0; i < items.size(); i++) out.push_back(items[i].second.type);
            Align(4);
            for (size_t i = 0; i < pending.size(); i++) if (pending[i].second) pending[i].second = vals[i];
            for (auto& [at, x] : pending) if (x) Patch32(at, Payload(*x));
            return p;
        }
        for (auto& [at, x] : pending) if (x) Patch32(at, Payload(*x));
        return p;
    }
};
}  // namespace

std::vector<uint8_t> Write(const Node& root, uint16_t version) {
    Writer w;
    std::set<std::string> keys, strs;
    w.Scan(root, keys, strs);
    uint32_t i = 0;
    for (auto& k : keys) w.keyIdx[k] = i++;
    i = 0;
    for (auto& s : strs) w.strIdx[s] = i++;
    w.out.resize(16);
    uint32_t keyOff = w.StrTab(w.keyIdx), strOff = w.StrTab(w.strIdx);
    uint32_t rootOff = root.type == kNull ? 0 : w.Container(root);
    w.out[0] = 'Y', w.out[1] = 'B', w.out[2] = uint8_t(version), w.out[3] = uint8_t(version >> 8);
    w.Patch32(4, keyOff), w.Patch32(8, strOff), w.Patch32(12, rootOff);
    return w.out;
}

}  // namespace byml
