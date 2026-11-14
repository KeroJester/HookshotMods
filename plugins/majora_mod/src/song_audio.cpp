// The songs' replay sounds (RESEARCH_NOTES.md §79h, §79k). The game has no recordings of them, so they are built at
// startup from the game's own single-note recordings (Sound/Resource/Stream/Ocarina_<pitch>.bwav), following Majora's
// Mask 3D's replay sequences (start, key, velocity and length of each note; songs.cpp).
//
// Each plays through a host sound: a native song's (the melody sound for the song asks for that type, mm_songs.cpp).
// While the replay plays, reads of the host's stream file return ours (decided per read). The host's BARS prefetch (header and first 14336 samples, held
// in memory) stays the host's, so the built stream keeps the host's format exactly: same channels, coefficients, sample
// count and data offsets. The prefetch's 0.3 s are the host's own, so a host opens on the song's first note; ours takes
// over after it with a short crossfade. The rest of the host's length is silence (the presentation is let go when ours ends).
#include "song_audio.h"
#include "bwav.h"
#include "gamefiles.h"
#include "platform.h"
#include "songs.h"
#include "vfs.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <thread>

namespace songaudio {
using namespace plat;
using gamefiles::Bytes;

constexpr const char* kStreamDir = "Sound/Resource/Stream/";
constexpr const char* kBars = "Sound/Resource/Ocarina.bars.zs";
constexpr int kRate = 48000;
constexpr float kTail = 1.2f;   // after the last note (the game's replays end about 1.2 s after their last loud note)
static const char* const kNative[] = {"ZeldaLullaby", "EponaSong", "SariaSong", "SunSong", "TimeSong", "StormSong",
                                      "ForestMinuet", "FireBolero", "WaterSerenade", "SpiritRequiem", "ShadowNocturne",
                                      "LightPrelude"};

struct Built { std::shared_ptr<const Bytes> stream; float seconds = 0; };
static std::map<uint32_t, Built> g_Built;          // song type -> its stream (filled by the worker)
static std::mutex g_Mu;
static std::atomic<uint32_t> g_PendingType{0};
static std::atomic<int64_t> g_PendingUntil{0};     // steady-clock ns: until then, that song's host reads its replay
static std::atomic<bool> g_Logged{false};

static int64_t Now() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

std::string HostStream(const songs::Song& s) {
    if (s.hostType >= 0 && s.hostType < 12) return std::string("Ocarina_") + kNative[s.hostType];
    return std::string("Ocarina_") + (s.hostPitch ? s.hostPitch : "B");
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
// above a single note), peak at most 0.98. maxSeconds: the host's length (the replay and its tail must fit).
static bool Render(const songs::Song& song, float maxSeconds, std::map<int, std::vector<float>>& cache, std::vector<float>& out) {
    float last = 0;
    for (auto& n : song.replay) last = std::max(last, n.start + n.length + 0.12f);
    const float total = std::min(maxSeconds - 0.05f, last + kTail);
    if (total < last) Log("[audio] %s: %.2f s is longer than its host (%.2f s): cut\n", song.english, last, maxSeconds);
    out.assign((size_t)(total * kRate), 0.f);
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
    if (peak > 0.98f) for (float& x : out) x *= 0.98f / peak;
    return true;
}

// Prefetch sample counts per channel: from the host's prefetch BWAV inside the BARS (same magic .. CRC as the stream).
static bool Prefetch(const Bytes& bars, const Bytes& host, std::vector<uint32_t>& out) {
    for (size_t o = 0; o + 12 <= bars.size(); o += 4) {
        if (memcmp(&bars[o], host.data(), 12)) continue;
        Bytes head(bars.begin() + o, bars.begin() + std::min(bars.size(), o + 0x10 + 8 * bwav::kRecord));
        std::vector<bwav::Channel> ch;
        if (!bwav::Channels(head, ch)) return false;
        for (auto& c : ch) out.push_back(c.prefetch);
        return true;
    }
    return false;
}

static bool BuildOne(const songs::Song& song, const Bytes& bars, std::map<int, std::vector<float>>& cache) {
    const std::string host = HostStream(song);
    Bytes file;
    std::vector<bwav::Channel> ch;
    std::vector<uint32_t> pre;
    std::vector<float> ours;
    if (!Load(std::string(kStreamDir) + host + ".bwav", file) || !bwav::Channels(file, ch) || ch.empty()) return false;
    if (!Prefetch(bars, file, pre) || pre.size() != ch.size()) { Log("[audio] %s: prefetch not found in the BARS\n", host.c_str()); return false; }
    if (!Render(song, ch[0].samples / (float)kRate, cache, ours)) return false;
    auto out = std::make_shared<Bytes>(file);
    const size_t fade = kRate / 8;
    for (size_t i = 0; i < ch.size(); i++) {
        const bwav::Channel& c = ch[i];
        const size_t p = pre[i], n = c.samples;
        if (c.codec != 1 || c.rate != kRate || p % 14 || p < 2 || p >= n || c.offStream != c.offHere) {
            Log("[audio] %s channel %zu: unexpected format (codec %u, prefetch %zu)\n", host.c_str(), i, c.codec, p);
            return false;
        }
        std::vector<int16_t> h = bwav::Decode(file, c);
        std::vector<int16_t> pcm(n - p);
        for (size_t k = p; k < n; k++) {
            float y = k < ours.size() ? ours[k] * 32767.f : 0.f;
            if (k < p + fade) {
                const float w = (float)(k - p) / fade, a = std::cos(w * 1.5707963f), b = std::sin(w * 1.5707963f);
                y = h[k] * a + y * b;
            }
            pcm[k - p] = (int16_t)std::lrint(std::max(-32768.f, std::min(32767.f, y)));
        }
        int h1 = h[p - 1], h2 = h[p - 2];
        Bytes enc = bwav::EncodeDsp(pcm.data(), pcm.size(), c.coefs, h1, h2);
        const size_t at = c.offStream + p / 14 * 8;
        if (at + enc.size() > out->size()) { Log("[audio] %s channel %zu: data past the file\n", host.c_str(), i); return false; }
        memcpy(out->data() + at, enc.data(), enc.size());
    }
    if (const char* dir = getenv("MAJORA_DUMP_REPLAY")) {   // (development: the built streams, for listening to them)
        const std::string path = std::string(dir) + "/" + song.id + ".bwav";
        if (FILE* f = fopen(path.c_str(), "wb")) { fwrite(out->data(), 1, out->size(), f); fclose(f); }
    }
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Built[song.type] = {out, ours.size() / (float)kRate};
    Log("[audio] %s: replay ready, %.2f s (in %s's stream format)\n", song.english, ours.size() / (float)kRate, host.c_str());
    return true;
}

static void Build() {
    Bytes z, bars;
    if (!Load(kBars, z) || !gamefiles::Unzstd(z, bars)) { Log("[audio] the BARS is not readable: the songs keep their host sounds\n"); return; }
    std::map<int, std::vector<float>> cache;
    for (const songs::Song& s : songs::All())
        if (!BuildOne(s, bars, cache)) Log("[audio] %s keeps its host's sound\n", s.english);
}

void Asked(uint32_t type, uint64_t kind) {
    g_PendingType = type;
    g_PendingUntil = Now() + (int64_t)((Seconds(type) + 1.5) * 1e9);   // (the replay, and the stream's read-ahead)
    g_Logged = false;
    const songs::Song* s = songs::ByType(type);
    Log("[audio] melody sound kind %llu for the %s\n", (unsigned long long)kind, s ? s->english : "?");
}

float Seconds(uint32_t type) {
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Built.find(type);
    return it == g_Built.end() ? 0.f : it->second.seconds;
}

void Start() {
    std::map<std::string, bool> hosts;
    for (const songs::Song& s : songs::All()) hosts[HostStream(s)] = true;
    for (auto& [host, unused] : hosts) {
        const std::string name = host;
        vfs::RegisterLive(std::string(kStreamDir) + host + ".bwav", [name]() -> std::shared_ptr<const Bytes> {
            const int64_t until = g_PendingUntil.load();
            if (!until || until < Now()) return nullptr;              // (the host's own sound: a native song, a note)
            const songs::Song* s = songs::ByType(g_PendingType);
            if (!s || HostStream(*s) != name) return nullptr;
            std::shared_ptr<const Bytes> d;
            { std::lock_guard<std::mutex> lk(g_Mu); auto it = g_Built.find(s->type); if (it != g_Built.end()) d = it->second.stream; }
            if (d && !g_Logged.exchange(true)) Log("[audio] the game reads %s's stream: the %s\n", name.c_str(), s->english);
            return d;
        });
    }
    std::thread(Build).detach();
}

}  // namespace songaudio
