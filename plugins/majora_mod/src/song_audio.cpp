// The songs' replay sounds as sounds of their own (RESEARCH_NOTES.md §79h, §79k, §79m). The game has no recordings of
// them, so they are built at startup from the game's single-note recordings (Sound/Resource/Stream/Ocarina_<pitch>.bwav),
// following Majora's Mask 3D's replay sequences (start, key, velocity and length of each note; songs_data.cpp).
//
// A native song's sound is an entry of Sound/Resource/Ocarina.bars (name "Ocarina_<Song>"): its AMTA (with one "UI"
// marker per note: the presentation's staff shows note i at marker i, and waits for every note) and its prefetch BWAV
// (header and the first 14336 samples), then the rest from Stream/Ocarina_<Song>.bwav. Each added song gets the same:
//   - an entry "Ocarina_Majora<type>" (padded to its template's name length) added to the BARS;
//   - its AMTA: a native one with as many notes (6 Epona's Song, 7 Nocturne of Shadow, 8 Bolero of Fire), its names,
//     CRC, length and UI markers set to the song's (marker times and the MINF's copy of them);
//   - its prefetch BWAV and stream, 2 channels (the same mono replay), DSP-ADPCM with Epona's Song's coefficients;
//   - the stream file is new: vfs::RegisterNew opens the template's stream (same path length) and serves ours.
// The melody sound reaches it through SLink2 (song_link.cpp). The presentation lasts as long as
// the song's own sound, and its staff follows the song's own notes.
#include "song_audio.h"
#include "bwav.h"
#include "gamefiles.h"
#include "platform.h"
#include "rstb.h"
#include "song_link.h"
#include "songs.h"
#include "vfs.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>

namespace songaudio {
using namespace plat;
using gamefiles::Bytes;

constexpr const char* kStreamDir = "Sound/Resource/Stream/";
constexpr const char* kBars = "Sound/Resource/Ocarina.bars";
constexpr int kRate = 48000;
constexpr float kTail = 1.2f;            // after the last note (the game's replays end about 1.2 s after their last loud note)
constexpr uint32_t kPrefetch = 14336;    // samples per channel in a prefetch BWAV (1024 ADPCM frames)
constexpr uint32_t kBarsExtra = 9 * 20480;   // (size table: 9 entries of AMTA + prefetch, padding)
constexpr size_t kHeader = 0xc0;         // BWAV header (0x10 + 2 records), padded

struct Template { const char* name; size_t notes; };   // native sounds whose AMTA has exactly that many UI markers
static const Template kTemplates[] = {{"Ocarina_EponaSong", 6}, {"Ocarina_ShadowNocturne", 7}, {"Ocarina_FireBolero", 8}};
constexpr const char* kCoefsFrom = "Ocarina_EponaSong";

struct Sound {
    std::string name, templ;                    // the BARS entry; its template's
    std::shared_ptr<const Bytes> stream;        // Stream/<name>.bwav
    Bytes prefetch, amta;
    float seconds = 0;
};
static std::map<uint32_t, Sound> g_Sounds;     // type -> its sound (made with the BARS copy)
static std::mutex g_Mu;
static std::atomic<bool> g_Served{false};      // the game read the BARS copy: the songs' entries exist

static uint16_t U16(const Bytes& b, size_t o) { return (uint16_t)(b[o] | b[o + 1] << 8); }
static uint32_t U32(const Bytes& b, size_t o) { return b[o] | b[o + 1] << 8 | b[o + 2] << 16 | (uint32_t)b[o + 3] << 24; }
static void Put16(Bytes& b, size_t o, uint16_t v) { b[o] = (uint8_t)v, b[o + 1] = (uint8_t)(v >> 8); }
static void Put32(Bytes& b, size_t o, uint32_t v) { for (int i = 0; i < 4; i++) b[o + i] = (uint8_t)(v >> (8 * i)); }
static size_t Align(size_t v, size_t a) { return (v + a - 1) / a * a; }
static uint32_t Crc(const Bytes& b) { return rstb::Crc32(std::string(b.begin(), b.end())); }

static const Template* TemplateFor(const songs::Song& s) {
    for (const Template& t : kTemplates) if (t.notes == s.notes.size()) return &t;
    return nullptr;
}

static std::string EntryName(const songs::Song& s, const Template& t) {   // same length as the template's name
    std::string n = "Ocarina_Majora" + std::to_string(s.type);
    if (n.size() > strlen(t.name)) return std::string();
    n.append(strlen(t.name) - n.size(), '_');
    return n;
}

static bool Load(const std::string& rel, Bytes& out) {
    const std::string p = gamefiles::RomfsPath(rel);
    if (p.empty() || !gamefiles::ReadFile(p, out)) { Log("[audio] %s: not readable\n", rel.c_str()); return false; }
    return true;
}

static bool Mono(const char* pitch, std::vector<float>& out) {   // one note's recording, channels averaged, -1..1
    Bytes f;
    std::vector<bwav::Channel> ch;
    if (!pitch || !Load(std::string(kStreamDir) + "Ocarina_" + pitch + ".bwav", f) || !bwav::Channels(f, ch) || ch.empty() || ch[0].rate != kRate) return false;
    std::vector<std::vector<int16_t>> pcm;
    size_t n = SIZE_MAX;
    for (auto& c : ch) { pcm.push_back(bwav::Decode(f, c)); n = std::min(n, pcm.back().size()); }
    if (!n || n == SIZE_MAX) return false;
    out.assign(n, 0.f);
    for (auto& p : pcm) for (size_t i = 0; i < n; i++) out[i] += p[i] / 32768.f / pcm.size();
    return true;
}

// Each note: the game's recording of that pitch from its attack, held for the note's length, then released the way
// the game's notes end (0.12 s fade); level 1.18x the RMS of a held note (the game's replays sit about that much
// above a single note), peak at most 0.98; then kTail of silence, as the game's replays end.
static bool Render(const songs::Song& song, std::map<int, std::vector<float>>& cache, std::vector<int16_t>& pcm) {
    float last = 0;
    for (auto& n : song.replay) last = std::max(last, n.start + n.length + 0.12f);
    std::vector<float> out((size_t)((last + kTail) * kRate), 0.f);
    const size_t release = (size_t)(0.12 * kRate);
    for (auto& n : song.replay) {
        std::vector<float>& s = cache[n.key];
        if (s.empty() && !Mono(songs::PitchName(n.key), s)) { Log("[audio] %s: no recording for key %d\n", song.english, n.key); return false; }
        const size_t hold = (size_t)(n.length * kRate), len = std::min(s.size(), hold + release), at = (size_t)(n.start * kRate);
        for (size_t i = 0; i < len && at + i < out.size(); i++) {
            float x = s[i];
            if (i >= hold) x *= std::exp(-(float)(i - hold) / (release / 4.f));
            if (i < 64) x *= i / 63.f;
            out[at + i] += x * (n.velocity / 127.f);
        }
    }
    std::vector<float>& b = cache[71];
    if (b.empty() && !Mono("B", b)) return false;
    double ref = 0, loud = 0;
    size_t cnt = 0;
    for (size_t i = 0; i < std::min<size_t>(b.size(), kRate); i++) ref += (double)b[i] * b[i];
    ref = std::sqrt(ref / std::min<size_t>(b.size(), kRate));
    for (float x : out) if (x != 0) loud += (double)x * x, cnt++;
    loud = cnt ? std::sqrt(loud / cnt) : 1;
    float gain = (float)(1.18 * ref / std::max(loud, 1e-6)), peak = 0;
    for (float& x : out) { x *= gain; peak = std::max(peak, std::fabs(x)); }
    if (peak > 0.98f) gain = 0.98f / peak; else gain = 1.f;
    pcm.resize(out.size());
    for (size_t i = 0; i < out.size(); i++) pcm[i] = (int16_t)std::lrint(std::max(-1.f, std::min(1.f, out[i] * gain)) * 32767.f);
    return true;
}

// ---- the BARS ----------------------------------------------------------------------------------------------------------
// "BARS", u32 size, BOM, version 0x0102, u32 count; count u32 CRC32s of the names (ascending); count (AMTA offset,
// BWAV offset) pairs; the AMTAs (u32 size at +8), then the prefetch BWAVs (offsets 64-aligned).
struct Entry { uint32_t crc; Bytes amta, bwav; };

static bool ParseBars(const Bytes& b, std::vector<Entry>& out) {
    if (b.size() < 0x10 || memcmp(b.data(), "BARS", 4) || U16(b, 0xa) != 0x0102) return false;
    const uint32_t n = U32(b, 0xc);
    if (0x10 + 12ull * n > b.size()) return false;
    std::vector<uint32_t> starts;
    for (uint32_t i = 0; i < 2 * n; i++) starts.push_back(U32(b, 0x10 + 4 * n + 4 * i));
    std::vector<uint32_t> sorted = starts;
    sorted.push_back((uint32_t)b.size());
    std::sort(sorted.begin(), sorted.end());
    for (uint32_t i = 0; i < n; i++) {
        const uint32_t a = starts[2 * i], w = starts[2 * i + 1];
        if (a + 12 > b.size() || memcmp(&b[a], "AMTA", 4) || w + 0x10 > b.size() || memcmp(&b[w], "BWAV", 4)) return false;
        const uint32_t asize = U32(b, a + 8), wend = *std::upper_bound(sorted.begin(), sorted.end(), w);
        if (a + asize > b.size()) return false;
        out.push_back({U32(b, 0x10 + 4 * i), Bytes(b.begin() + a, b.begin() + a + asize), Bytes(b.begin() + w, b.begin() + wend)});
    }
    return true;
}

static Bytes WriteBars(std::vector<Entry> es) {
    std::sort(es.begin(), es.end(), [](const Entry& x, const Entry& y) { return x.crc < y.crc; });
    const size_t n = es.size();
    Bytes out(0x10 + 12 * n, 0);
    memcpy(out.data(), "BARS", 4);
    Put16(out, 8, 0xfeff), Put16(out, 0xa, 0x0102), Put32(out, 0xc, (uint32_t)n);
    for (size_t i = 0; i < n; i++) {
        Put32(out, 0x10 + 4 * i, es[i].crc);
        out.resize(Align(out.size(), 4), 0);
        Put32(out, 0x10 + 4 * n + 8 * i, (uint32_t)out.size());
        out.insert(out.end(), es[i].amta.begin(), es[i].amta.end());
    }
    for (size_t i = 0; i < n; i++) {
        out.resize(Align(out.size(), 0x40), 0);
        Put32(out, 0x10 + 4 * n + 8 * i + 4, (uint32_t)out.size());
        out.insert(out.end(), es[i].bwav.begin(), es[i].bwav.end());
    }
    Put32(out, 4, (uint32_t)out.size());
    return out;
}

static const Entry* Find(const std::vector<Entry>& es, const std::string& name) {
    const uint32_t crc = rstb::Crc32(name);
    for (const Entry& e : es) if (e.crc == crc) return &e;
    return nullptr;
}

// The template's AMTA with the song's name, CRC, length and note times. AMTA v5: +0x08 size, +0x18 MINF offset, +0x28
// CRC32 of the name; markers at +0x50 (u32 count; {u32 id, u32 name offset from the field, u32 start, u32 length}).
// The MINF holds the length (samples) twice near its start and the UI times again as {u16 count, 0xffff, u32 times}.
static bool MakeAmta(const Entry& templ, const std::string& tname, uint32_t tsamples, const std::string& name,
                     const std::vector<uint32_t>& times, uint32_t samples, Bytes& out) {
    out = templ.amta;
    const size_t size = out.size();
    if (size < 0x60 || U32(out, 0x50) > 64) return false;
    int names = 0;
    for (size_t o = 0; o + tname.size() < size; o++)
        if (!memcmp(&out[o], tname.c_str(), tname.size() + 1)) { memcpy(&out[o], name.c_str(), name.size()); names++; }
    Put32(out, 0x28, rstb::Crc32(name));
    std::vector<uint32_t> old;
    const uint32_t count = U32(out, 0x50);
    for (uint32_t i = 0; i < count; i++) {
        const size_t e = 0x54 + 16 * i, s = e + 4 + U32(out, e + 4);
        if (e + 16 > size || s + 3 > size) return false;
        if (memcmp(&out[s], "UI\0", 3)) continue;
        if (old.size() < times.size()) Put32(out, e + 8, times[old.size()]);
        old.push_back(U32(templ.amta, e + 8));
    }
    if (old.size() != times.size()) return false;
    const size_t minf = U32(out, 0x18);
    int lens = 0;
    for (size_t o = minf; o + 4 <= std::min(size, minf + 0x40); o += 4)
        if (U32(out, o) == tsamples) Put32(out, o, samples), lens++;
    bool list = false;
    for (size_t o = minf; o + 4 + 4 * old.size() <= size && !list; o += 4) {
        if (U16(out, o) != old.size() || U16(out, o + 2) != 0xffff) continue;
        bool same = true;
        for (size_t i = 0; i < old.size() && same; i++) same = U32(templ.amta, o + 4 + 4 * i) == old[i];
        if (!same) continue;
        for (size_t i = 0; i < times.size(); i++) Put32(out, o + 4 + 4 * i, times[i]);
        list = true;
    }
    if (names != 2 || lens != 2 || !list) { Log("[audio] %s: AMTA not as expected (names %d, lengths %d, list %d)\n", name.c_str(), names, lens, list); return false; }
    return true;
}

// A BWAV of 2 channels (the same data): header 0x10, records 0x4c from the template's (loop words), data 64-aligned.
// prefetch: only the first kPrefetch samples of each channel (the BARS's copy); else the whole stream.
static Bytes MakeBwav(const Bytes& rec, const Bytes& enc, uint32_t samples, uint32_t crc, bool prefetch) {
    const size_t stride = Align(enc.size(), 0x40), pbytes = kPrefetch / 14 * 8;
    Bytes out(kHeader, 0);
    memcpy(out.data(), "BWAV", 4);
    Put16(out, 4, 0xfeff), Put16(out, 6, 1), Put32(out, 8, crc), Put16(out, 0xc, prefetch ? 1 : 0), Put16(out, 0xe, 2);
    for (int c = 0; c < 2; c++) {
        const size_t r = 0x10 + c * bwav::kRecord;
        memcpy(&out[r], rec.data(), bwav::kRecord);
        Put16(out, r, 1), Put16(out, r + 2, (uint16_t)c), Put32(out, r + 4, kRate), Put32(out, r + 8, samples);
        Put32(out, r + 0xc, prefetch ? std::min(kPrefetch, samples) : samples);
        Put32(out, r + 0x30, (uint32_t)(kHeader + c * stride));
        Put32(out, r + 0x34, (uint32_t)(kHeader + c * (prefetch ? pbytes : stride)));
    }
    for (int c = 0; c < 2; c++) {
        Bytes d(enc);
        d.resize(prefetch ? pbytes : (c ? enc.size() : stride), 0);
        out.insert(out.end(), d.begin(), d.end());
    }
    return out;
}

static bool MakeSound(const songs::Song& song, const std::vector<Entry>& es, const int16_t coefs[16], const Bytes& rec,
                      std::map<int, std::vector<float>>& cache, Sound& out) {
    const Template* t = TemplateFor(song);
    const Entry* te = t ? Find(es, t->name) : nullptr;
    if (!te) { Log("[audio] %s: no native sound with %zu notes to model it on\n", song.english, song.notes.size()); return false; }
    out.templ = t->name;
    out.name = EntryName(song, *t);
    std::vector<bwav::Channel> tch;
    std::vector<int16_t> pcm;
    if (out.name.empty() || Find(es, out.name) || !bwav::Channels(te->bwav, tch) || tch.empty() || !Render(song, cache, pcm)) return false;
    int h1 = 0, h2 = 0;
    const Bytes enc = bwav::EncodeDsp(pcm.data(), pcm.size(), coefs, h1, h2);
    const uint32_t samples = (uint32_t)pcm.size(), crc = Crc(enc);
    std::vector<uint32_t> times;
    for (size_t i = 0; i < song.notes.size() && i < song.replay.size(); i++) times.push_back((uint32_t)std::lrint(song.replay[i].start * kRate));
    if (!MakeAmta(*te, t->name, tch[0].samples, out.name, times, samples, out.amta)) return false;
    out.prefetch = MakeBwav(rec, enc, samples, crc, true);
    out.stream = std::make_shared<Bytes>(MakeBwav(rec, enc, samples, crc, false));
    out.seconds = samples / (float)kRate;
    if (const char* dir = getenv("MAJORA_DUMP_REPLAY")) {   // (development: the built streams, for listening to them)
        const std::string path = std::string(dir) + "/" + song.id + ".bwav";
        if (FILE* f = fopen(path.c_str(), "wb")) { fwrite(out.stream->data(), 1, out.stream->size(), f); fclose(f); }
    }
    return true;
}

static bool PatchBars(Bytes& file) {
    Bytes bars;
    std::vector<Entry> es;
    if (!gamefiles::Unzstd(file, bars) || !ParseBars(bars, es)) { Log("[audio] the BARS is not readable: the songs keep their host sounds\n"); return false; }
    const Entry* from = Find(es, kCoefsFrom);
    std::vector<bwav::Channel> ch;
    if (!from || !bwav::Channels(from->bwav, ch) || ch.empty() || from->bwav.size() < 0x10 + bwav::kRecord) return false;
    const Bytes rec(from->bwav.begin() + 0x10, from->bwav.begin() + 0x10 + bwav::kRecord);
    std::map<int, std::vector<float>> cache;
    std::map<uint32_t, Sound> made;
    const size_t native = es.size();
    for (const songs::Song& s : songs::All()) {
        Sound snd;
        if (!MakeSound(s, es, ch[0].coefs, rec, cache, snd)) { Log("[audio] %s keeps its host's sound\n", s.english); continue; }
        es.push_back({rstb::Crc32(snd.name), snd.amta, snd.prefetch});
        Log("[audio] %s: own sound %s, %.2f s, %zu notes (AMTA from %s)\n", s.english, snd.name.c_str(), snd.seconds, s.notes.size(), snd.templ.c_str());
        made[s.type] = std::move(snd);
    }
    if (made.empty()) return false;
    const Bytes out = WriteBars(es);
    file = gamefiles::RawZstd(out.data(), out.size());
    Log("[audio] BARS: %zu entries added (%zu -> %zu bytes)\n", es.size() - native, bars.size(), out.size());
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Sounds = std::move(made);
    return true;
}

// ---- API ---------------------------------------------------------------------------------------------------------------
std::string SoundName(uint32_t type) {
    if (!g_Served) return std::string();
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Sounds.find(type);
    return it == g_Sounds.end() ? std::string() : it->second.name;
}

void Asked(uint32_t type, uint64_t kind) {
    const songs::Song* s = songs::ByType(type);
    const std::string name = SoundName(type);
    Log("[audio] melody sound kind %llu for the %s: %s\n", (unsigned long long)kind, s ? s->english : "?",
        name.empty() ? "its host's sound" : name.c_str());
}

void Start() {
    vfs::Register(std::string(kBars) + ".zs", [](Bytes& file) { return PatchBars(file); },
                  [] { const bool ok = rstb::Applied(); if (ok) g_Served = true; return ok; });
    rstb::Grow(kBars, kBarsExtra);
    std::vector<std::string> names;
    for (const songs::Song& s : songs::All()) {
        const Template* t = TemplateFor(s);
        if (!t) continue;
        const std::string name = EntryName(s, *t);
        names.push_back(name);
        const uint32_t type = s.type;
        vfs::RegisterNew(std::string(kStreamDir) + name + ".bwav", std::string(kStreamDir) + t->name + ".bwav",
                         [type]() -> std::shared_ptr<const Bytes> {
                             std::lock_guard<std::mutex> lk(g_Mu);
                             auto it = g_Sounds.find(type);
                             return it == g_Sounds.end() ? nullptr : it->second.stream;
                         });
    }
    songlink::Start(names);
}

}  // namespace songaudio
