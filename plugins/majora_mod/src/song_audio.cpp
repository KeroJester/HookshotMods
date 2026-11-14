// The Song of Healing's replay sound (RESEARCH_NOTES.md §79h). The game has no recording of it, so one is built at
// startup from the game's own single-note recordings (Sound/Resource/Stream/Ocarina_<pitch>.bwav), following
// Majora's Mask 3D's replay sequence (NA_BGM_OCA_CURE_SEQ: start, key, velocity and length of each note).
//
// It is played through a host song's sound: the melody sound for type 14 asks for Zelda's Lullaby (song_healing.cpp),
// and the next time the game opens that song's stream file, it reads ours instead. The host's BARS prefetch (header
// and first samples, held in memory) stays the host's, so the built stream keeps the host's format exactly: same
// channels, coefficients, sample count and data offsets; the prefetch's samples are the host's own (both songs start
// on B), and ours takes over after them with a short crossfade. The rest of the host's length is silence.
#include "song_audio.h"
#include "bwav.h"
#include "gamefiles.h"
#include "platform.h"
#include "vfs.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <thread>

namespace songaudio {
using namespace plat;
using gamefiles::Bytes;

constexpr const char* kHost = "ZeldaLullaby";
constexpr const char* kStreamDir = "Sound/Resource/Stream/";
constexpr const char* kBars = "Sound/Resource/Ocarina.bars.zs";
constexpr int kRate = 48000;
constexpr float kTail = 1.2f;   // after the last note (the game's replays end about 1.2 s after their last loud note)

struct Note { float start; int key, velocity; float length; };
// MM3D NA_BGM_OCA_CURE_SEQ (seconds, MIDI key, velocity, seconds): B A F, B A F, B A E D E
static const Note kNotes[] = {
    {0.0057f, 71, 118, 0.5341f}, {0.5511f, 69, 118, 0.5114f}, {1.0966f, 65, 99, 0.3977f},
    {1.6420f, 71, 124, 0.5455f}, {2.1875f, 69, 118, 0.4830f}, {2.7329f, 65, 116, 0.4034f},
    {3.2784f, 71, 114, 0.5227f}, {3.8238f, 69, 122, 0.4261f}, {4.3693f, 64, 116, 0.2614f},
    {4.6420f, 62, 118, 0.3295f}, {4.9147f, 64, 122, 1.5625f},
};
static const char* Pitch(int key) { return key == 62 ? "D" : key == 64 ? "E" : key == 65 ? "F" : key == 69 ? "A" : key == 71 ? "B" : nullptr; }

static std::atomic<float> g_Seconds{0};         // our replay's length (0 until built)
static std::shared_ptr<const Bytes> g_Stream;     // the built stream file (null until built, or if it failed)
static std::mutex g_Mu;
static std::atomic<int64_t> g_PendingUntil{0};   // steady-clock ns: a type-14 melody sound was asked for

static int64_t Now() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

static bool Load(const std::string& rel, Bytes& out) {
    const std::string p = gamefiles::RomfsPath(rel);
    if (p.empty() || !gamefiles::ReadFile(p, out)) { Log("[audio] %s: not readable\n", rel.c_str()); return false; }
    return true;
}

static bool Mono(const char* pitch, std::vector<float>& out) {   // one note's recording, channels averaged, -1..1
    Bytes f;
    std::vector<bwav::Channel> ch;
    if (!Load(std::string(kStreamDir) + "Ocarina_" + pitch + ".bwav", f) || !bwav::Channels(f, ch) || ch.empty() || ch[0].rate != kRate) return false;
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
// above a single note), peak at most 0.98.
static bool Render(std::vector<float>& out) {
    float total = 0;
    for (auto& n : kNotes) total = std::max(total, n.start + n.length);
    out.assign((size_t)((total + kTail) * kRate) + 1, 0.f);
    std::vector<float> src[128];
    const size_t release = (size_t)(0.12 * kRate);
    for (auto& n : kNotes) {
        std::vector<float>& s = src[n.key];
        if (s.empty() && !Mono(Pitch(n.key), s)) return false;
        const size_t hold = (size_t)(n.length * kRate), len = std::min(s.size(), hold + release), at = (size_t)(n.start * kRate);
        for (size_t i = 0; i < len && at + i < out.size(); i++) {
            float x = s[i];
            if (i >= hold) x *= std::exp(-(float)(i - hold) / (release / 4.f));
            if (i < 64) x *= i / 63.f;
            out[at + i] += x * (n.velocity / 127.f);
        }
    }
    std::vector<float> b;
    if (!Mono("B", b)) return false;
    double ref = 0, loud = 0;
    size_t cnt = 0;
    for (size_t i = 0; i < std::min<size_t>(b.size(), kRate); i++) ref += (double)b[i] * b[i];
    ref = std::sqrt(ref / std::min<size_t>(b.size(), kRate));
    for (float x : out) if (x != 0) loud += (double)x * x, cnt++;
    loud = cnt ? std::sqrt(loud / cnt) : 1;
    float gain = (float)(1.18 * ref / std::max(loud, 1e-6)), peak = 0;
    for (float& x : out) { x *= gain; peak = std::max(peak, std::fabs(x)); }
    if (peak > 0.98f) for (float& x : out) x *= 0.98f / peak;
    Log("[audio] replay rendered: %zu notes, %.2f s, peak %.2f\n", sizeof kNotes / sizeof *kNotes, out.size() / (double)kRate, std::min(peak, 0.98f));
    return true;
}

// Prefetch sample counts per channel: from the host's prefetch BWAV inside the BARS (same magic .. CRC as the stream).
static bool Prefetch(const Bytes& host, std::vector<uint32_t>& out) {
    Bytes z, bars;
    if (!Load(kBars, z) || !gamefiles::Unzstd(z, bars)) return false;
    for (size_t o = 0; o + 12 <= bars.size(); o += 4) {
        if (memcmp(&bars[o], host.data(), 12)) continue;
        Bytes head(bars.begin() + o, bars.begin() + std::min(bars.size(), o + 0x10 + 8 * bwav::kRecord));
        std::vector<bwav::Channel> ch;
        if (!bwav::Channels(head, ch)) return false;
        for (auto& c : ch) out.push_back(c.prefetch);
        return true;
    }
    Log("[audio] %s: prefetch not found in the BARS\n", kHost);
    return false;
}

static void Build() {
    Bytes host;
    std::vector<bwav::Channel> ch;
    std::vector<uint32_t> pre;
    std::vector<float> ours;
    const std::string rel = std::string(kStreamDir) + "Ocarina_" + kHost + ".bwav";
    if (!Load(rel, host) || !bwav::Channels(host, ch) || !Prefetch(host, pre) || pre.size() != ch.size() || !Render(ours)) {
        Log("[audio] the Song of Healing keeps the stand-in sound\n");
        return;
    }
    auto out = std::make_shared<Bytes>(host);
    const size_t fade = kRate / 10;
    for (size_t i = 0; i < ch.size(); i++) {
        const bwav::Channel& c = ch[i];
        const size_t p = pre[i], n = c.samples;
        if (c.codec != 1 || c.rate != kRate || p % 14 || p >= n || c.offStream != c.offHere) {
            Log("[audio] %s channel %zu: unexpected format (codec %u, prefetch %zu)\n", kHost, i, c.codec, p);
            return;
        }
        std::vector<int16_t> h = bwav::Decode(host, c);
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
        if (at + enc.size() > out->size()) { Log("[audio] %s channel %zu: data past the file\n", kHost, i); return; }
        memcpy(out->data() + at, enc.data(), enc.size());
    }
    if (const char* dump = getenv("MAJORA_DUMP_REPLAY")) {   // (development: the built stream, for listening to it)
        if (FILE* f = fopen(dump, "wb")) { fwrite(out->data(), 1, out->size(), f); fclose(f); }
    }
    std::lock_guard<std::mutex> lk(g_Mu);
    g_Stream = out;
    g_Seconds = ours.size() / (float)kRate;
    Log("[audio] Song of Healing replay ready (in %s's stream format, %zu bytes)\n", kHost, out->size());
}

void Asked(uint64_t kind) {
    g_PendingUntil = Now() + 5000000000LL;
    Log("[audio] melody sound kind %llu for the Song of Healing\n", (unsigned long long)kind);
}

float Seconds() { return g_Seconds; }

void Start() {
    const std::string rel = std::string(kStreamDir) + "Ocarina_" + kHost + ".bwav";
    vfs::RegisterLive(rel, [] () -> std::shared_ptr<const Bytes> {
        const int64_t until = g_PendingUntil.exchange(0);
        std::shared_ptr<const Bytes> s;
        { std::lock_guard<std::mutex> lk(g_Mu); s = g_Stream; }
        const bool ours = until > Now() && s;
        Log("[audio] the game opened %s's stream: %s\n", kHost, ours ? "the Song of Healing" : until ? "own (replay not built)" : "own");
        return ours ? s : nullptr;
    });
    std::thread(Build).detach();
}

}  // namespace songaudio
