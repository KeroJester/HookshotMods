// See msbt.h. Sections: 16-byte header (magic, u32 size, 8 bytes 0), data, padding to 16 with 0xAB.
// LBL1: u32 bucket count, {u32 count, u32 offset} per bucket, then per label u8 length + name + u32 text index;
// bucket = hash % count, hash = hash * 0x492 + byte. TXT2: u32 count, u32 offsets (from the section data), texts.
#include "msbt.h"
#include <cstring>

namespace msbt {

namespace {
uint32_t Rd32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
void Put32(gamefiles::Bytes& b, uint32_t v) { for (int i = 0; i < 4; i++) b.push_back(uint8_t(v >> (8 * i))); }
uint32_t LabelHash(const std::string& s, uint32_t buckets) {
    uint32_t h = 0;
    for (unsigned char ch : s) h = h * 0x492 + ch;
    return h % buckets;
}
}  // namespace

bool File::Parse(const gamefiles::Bytes& d) {
    if (d.size() < 0x20 || memcmp(d.data(), "MsgStdBn", 8) || d[8] != 0xFF || d[9] != 0xFE) return false;
    header.assign(d.begin(), d.begin() + 0x20);
    const uint16_t nsec = d[0xe] | d[0xf] << 8;
    size_t off = 0x20;
    bool haveLbl = false, haveTxt = false;
    for (int s = 0; s < nsec; s++) {
        if (off + 16 > d.size()) return false;
        const uint32_t size = Rd32(&d[off + 4]);
        const size_t at = off + 16;
        if (at + size > d.size()) return false;
        if (!memcmp(&d[off], "LBL1", 4)) {
            buckets = Rd32(&d[at]);
            for (uint32_t b = 0; b < buckets; b++) {
                uint32_t cnt = Rd32(&d[at + 4 + 8 * b]), p = at + Rd32(&d[at + 8 + 8 * b]);
                for (uint32_t k = 0; k < cnt && p < at + size; k++) {
                    const uint8_t len = d[p];
                    labels.emplace_back(std::string((const char*)&d[p + 1], len), Rd32(&d[p + 1 + len]));
                    p += 1 + len + 4;
                }
            }
            haveLbl = true;
        } else if (!memcmp(&d[off], "TXT2", 4)) {
            const uint32_t n = Rd32(&d[at]);
            for (uint32_t i = 0; i < n; i++) {
                const uint32_t a = Rd32(&d[at + 4 + 4 * i]), b = i + 1 < n ? Rd32(&d[at + 8 + 4 * i]) : size;
                if (a > b || b > size) return false;
                texts.emplace_back(d.begin() + at + a, d.begin() + at + b);
            }
            haveTxt = true;
        } else {
            return false;                                  // (attributes, styles...: not handled)
        }
        off = (at + size + 15) & ~(size_t)15;
    }
    return haveLbl && haveTxt && buckets;
}

const gamefiles::Bytes* File::Text(const std::string& label) const {
    for (auto& [l, i] : labels) if (l == label && i < texts.size()) return &texts[i];
    return nullptr;
}

void File::Set(const std::string& label, const gamefiles::Bytes& text) {
    for (auto& [l, i] : labels) if (l == label && i < texts.size()) { texts[i] = text; return; }
    labels.emplace_back(label, (uint32_t)texts.size());
    texts.push_back(text);
}

gamefiles::Bytes File::Write() const {
    gamefiles::Bytes out(header);
    auto section = [&](const char* magic, const gamefiles::Bytes& data) {
        out.insert(out.end(), magic, magic + 4);
        Put32(out, (uint32_t)data.size());
        out.insert(out.end(), 8, 0);
        out.insert(out.end(), data.begin(), data.end());
        while (out.size() % 16) out.push_back(0xAB);
    };
    // LBL1
    std::vector<std::vector<const std::pair<std::string, uint32_t>*>> bucket(buckets);
    for (auto& l : labels) bucket[LabelHash(l.first, buckets)].push_back(&l);
    gamefiles::Bytes lbl;
    Put32(lbl, buckets);
    uint32_t pos = 4 + 8 * buckets;
    for (auto& b : bucket) {
        Put32(lbl, (uint32_t)b.size());
        Put32(lbl, pos);
        for (auto* l : b) pos += 1 + (uint32_t)l->first.size() + 4;
    }
    for (auto& b : bucket)
        for (auto* l : b) {
            lbl.push_back((uint8_t)l->first.size());
            lbl.insert(lbl.end(), l->first.begin(), l->first.end());
            Put32(lbl, l->second);
        }
    section("LBL1", lbl);
    // TXT2
    gamefiles::Bytes txt;
    Put32(txt, (uint32_t)texts.size());
    uint32_t at = 4 + 4 * (uint32_t)texts.size();
    for (auto& t : texts) { Put32(txt, at); at += (uint32_t)t.size(); }
    for (auto& t : texts) txt.insert(txt.end(), t.begin(), t.end());
    section("TXT2", txt);
    out[0xe] = 2, out[0xf] = 0;
    const uint32_t size = (uint32_t)out.size();
    memcpy(&out[0x12], &size, 4);
    return out;
}

gamefiles::Bytes LeadingTag(const gamefiles::Bytes& t) {
    if (t.size() < 7 || t[0] != 0x0E) return {};
    const size_t n = 7 + (t[5] | t[6] << 8);
    return n <= t.size() ? gamefiles::Bytes(t.begin(), t.begin() + n) : gamefiles::Bytes();
}

}  // namespace msbt
