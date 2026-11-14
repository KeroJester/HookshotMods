// See bwav.h.
#include "bwav.h"
#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>

namespace bwav {

template <class T> static T Get(const gamefiles::Bytes& d, size_t o) { T v{}; if (o + sizeof v <= d.size()) memcpy(&v, &d[o], sizeof v); return v; }

bool Channels(const gamefiles::Bytes& d, std::vector<Channel>& out) {
    out.clear();
    if (d.size() < 0x10 || memcmp(d.data(), "BWAV", 4)) return false;
    const uint16_t n = Get<uint16_t>(d, 0x0e);
    if (d.size() < 0x10 + n * kRecord) return false;
    for (uint16_t i = 0; i < n; i++) {
        const size_t o = 0x10 + i * kRecord;
        Channel c;
        c.codec = Get<uint16_t>(d, o), c.pan = Get<uint16_t>(d, o + 2);
        c.rate = Get<uint32_t>(d, o + 4), c.samples = Get<uint32_t>(d, o + 8), c.prefetch = Get<uint32_t>(d, o + 0xc);
        memcpy(c.coefs, &d[o + 0x10], 32);
        c.offStream = Get<uint32_t>(d, o + 0x30), c.offHere = Get<uint32_t>(d, o + 0x34);
        out.push_back(c);
    }
    return true;
}

static inline int Clamp16(int64_t v) { return (int)std::min<int64_t>(32767, std::max<int64_t>(-32768, v)); }

void DecodeDsp(const uint8_t* data, size_t bytes, const int16_t coefs[16], size_t n, int16_t* out, int& h1, int& h2) {
    size_t k = 0;
    for (size_t pos = 0; k < n && pos + 8 <= bytes; pos += 8) {
        const uint8_t hdr = data[pos];
        const int scale = 1 << (hdr & 0xF), p = (hdr >> 4) & 7, c1 = coefs[2 * p], c2 = coefs[2 * p + 1];
        for (int j = 0; j < 14 && k < n; j++, k++) {
            int nib = j % 2 == 0 ? data[pos + 1 + j / 2] >> 4 : data[pos + 1 + j / 2] & 0xF;
            if (nib >= 8) nib -= 16;
            const int s = Clamp16((((int64_t)nib * scale) << 11) + 1024 + (int64_t)c1 * h1 + (int64_t)c2 * h2 >> 11);
            out[k] = (int16_t)s;
            h2 = h1, h1 = s;
        }
    }
    for (; k < n; k++) out[k] = 0;
}

std::vector<int16_t> Decode(const gamefiles::Bytes& d, const Channel& c) {
    std::vector<int16_t> out;
    if (c.offHere >= d.size()) return out;
    if (c.codec == 0) {
        const size_t n = std::min<size_t>(c.samples, (d.size() - c.offHere) / 2);
        out.resize(n);
        memcpy(out.data(), &d[c.offHere], n * 2);
    } else if (c.codec == 1) {
        out.resize(c.samples);
        int h1 = 0, h2 = 0;
        DecodeDsp(&d[c.offHere], d.size() - c.offHere, c.coefs, c.samples, out.data(), h1, h2);
    }
    return out;
}

gamefiles::Bytes EncodeDsp(const int16_t* pcm, size_t n, const int16_t coefs[16], int& h1, int& h2) {
    const size_t frames = (n + 13) / 14;
    gamefiles::Bytes out(frames * 8);
    for (size_t f = 0; f < frames; f++) {
        int x[14];
        for (int j = 0; j < 14; j++) { size_t i = f * 14 + j; x[j] = i < n ? pcm[i] : 0; }
        int64_t bestErr = LLONG_MAX;
        int best = 0, bestH1 = h1, bestH2 = h2, bestNib[14] = {};
        for (int p = 0; p < 8; p++) {
            const int64_t c1 = coefs[2 * p], c2 = coefs[2 * p + 1];
            for (int sh = 0; sh < 13; sh++) {
                const int64_t scale = 1 << sh;
                int64_t a = h1, b = h2, err = 0;
                int nib[14];
                for (int j = 0; j < 14 && err < bestErr; j++) {
                    const int64_t pred = c1 * a + c2 * b;
                    // nearest nibble: round(((s << 11) - pred) / (scale << 11)), as the Python builder does
                    const double q = ((double)((int64_t)x[j] << 11) - (double)pred) / (double)(scale << 11);
                    const int qi = (int)std::min(7.0, std::max(-8.0, std::nearbyint(q)));
                    const int d = Clamp16((((int64_t)qi * scale) << 11) + 1024 + pred >> 11);
                    err += (int64_t)(d - x[j]) * (d - x[j]);
                    nib[j] = qi;
                    b = a, a = d;
                }
                if (err < bestErr) {
                    bestErr = err, best = p << 4 | sh, bestH1 = (int)a, bestH2 = (int)b;
                    memcpy(bestNib, nib, sizeof nib);
                }
            }
        }
        h1 = bestH1, h2 = bestH2;
        out[f * 8] = (uint8_t)best;
        for (int j = 0; j < 7; j++) out[f * 8 + 1 + j] = (uint8_t)((bestNib[2 * j] & 0xF) << 4 | (bestNib[2 * j + 1] & 0xF));
    }
    return out;
}

}  // namespace bwav
