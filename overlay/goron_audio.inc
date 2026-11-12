// -------------------------------------------------------------
// Goron roll sound effects: MM3D's SFX (rendered by the mod builder into mods/*/sfx/*.wav) on our own SDL3 stream.
// -------------------------------------------------------------
// MM's two ways of playing them (z_player.c / code_8019AF00.c):
//   - one-shot (Player_PlaySfx), optionally with Audio_SetSyncedSfxFreqAndVolume(param): param > 6 -> freq 1.1,
//     volume 1.0; else t = param / 6, volume = 0.775 + 0.225 t, freq = 0.9 + 0.2 t.
//   - "- SFX_FLAG" (Actor_PlaySfx_FlaggedCentered1): requested every frame, keeps sounding while requested.
// HOOKSHOT_GORON_SFX=0 turns it off; HOOKSHOT_GORON_SFX_VOL=<x> scales it (default 1).

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

namespace goron_sfx {

enum Id {
    TO_BALL, BALL_TO_GORON, ROLL, CHG_ROLL, BALL_CHARGE, BALL_CHARGE_DASH, BALL_CHARGE_FAILED, SLIP, ROLL_DUST,
    ROLLING_REFLECTION, BALLJUMP, GORON_PUNCH, BODY_HIT, COUNT
};
static const char* kNames[COUNT] = {
    "NA_SE_PL_GORON_TO_BALL", "NA_SE_PL_BALL_TO_GORON", "NA_SE_PL_GORON_ROLL", "NA_SE_PL_GORON_CHG_ROLL",
    "NA_SE_PL_GORON_BALL_CHARGE", "NA_SE_PL_GORON_BALL_CHARGE_DASH", "NA_SE_PL_GORON_BALL_CHARGE_FAILED",
    "NA_SE_PL_GORON_SLIP", "NA_SE_PL_ROLL_DUST", "NA_SE_IT_GORON_ROLLING_REFLECTION", "NA_SE_PL_GORON_BALLJUMP",
    "NA_SE_PL_GORON_PUNCH", "NA_SE_PL_BODY_HIT"};
static const bool kLoop[COUNT] = {false, false, false, false, true, false, false, true, true, false, false, false, false};

struct Clip { std::vector<float> s; int rate = 32000; long loopStart = -1; };   // loopStart from <name>.loop
struct Voice { int id; double pos; float freq, vol; bool flagged; int lastTick; float fade; float fadeIn; bool dying;
               double maxPos = 0; };   // maxPos > 0: stop (fade) after this many source samples

static Clip g_Clips[COUNT];
static std::vector<Voice> g_Voices;
static std::mutex g_Mx;
static SDL_AudioStream* g_Stream = nullptr;
static int g_OutRate = 48000;
static float g_Master = 1.0f;
static std::atomic<int> g_Tick{0};
static bool g_On = false;
static FILE* g_Dump = nullptr;           // HOOKSHOT_GORON_SFX_DUMP=<file>: raw float32 mono of the mix (tests)

static bool LoadWav(const std::string& path, Clip& c) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    std::vector<uint8_t> d;
    uint8_t buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) d.insert(d.end(), buf, buf + n);
    fclose(f);
    if (d.size() < 44 || memcmp(d.data(), "RIFF", 4) || memcmp(d.data() + 8, "WAVE", 4)) return false;
    size_t p = 12; int ch = 1, bits = 16;
    while (p + 8 <= d.size()) {
        uint32_t sz; memcpy(&sz, d.data() + p + 4, 4);
        if (!memcmp(d.data() + p, "fmt ", 4)) {
            uint16_t c16; memcpy(&c16, d.data() + p + 10, 2); ch = c16;
            uint32_t r; memcpy(&r, d.data() + p + 12, 4); c.rate = (int)r;
            memcpy(&c16, d.data() + p + 22, 2); bits = c16;
        } else if (!memcmp(d.data() + p, "data", 4) && bits == 16) {
            size_t frames = std::min<size_t>(sz, d.size() - p - 8) / (2 * ch);
            c.s.resize(frames);
            const int16_t* s = (const int16_t*)(d.data() + p + 8);
            for (size_t i = 0; i < frames; i++) c.s[i] = s[i * ch] / 32768.0f;
            return true;
        }
        p += 8 + sz + (sz & 1);
    }
    return false;
}

// SDL pulls audio: mix the active voices (linear interpolation, per-voice pitch / volume / fade)
static void SDLCALL Pull(void*, SDL_AudioStream* stream, int additional, int) {
    if (additional <= 0) return;
    int frames = additional / (int)sizeof(float);
    static std::vector<float> mix;
    mix.assign(frames, 0.0f);
    {
        std::lock_guard<std::mutex> lk(g_Mx);
        const int tick = g_Tick.load();
        for (auto& v : g_Voices) {
            const Clip& c = g_Clips[v.id];
            if (c.s.size() < 2) { v.fade = 0; continue; }
            double step = (double)c.rate / g_OutRate * v.freq;
            bool stopping = (v.flagged && tick - v.lastTick > 2) || v.dying;   // not requested any more / replaced
            const float fadeStep = 1.0f / ((v.dying ? 0.005f : 0.05f) * g_OutRate);
            for (int i = 0; i < frames; i++) {
                size_t ip = (size_t)v.pos;
                if (ip + 1 >= c.s.size()) {
                    long ls = c.loopStart >= 0 ? c.loopStart : (kLoop[v.id] ? 0 : -1);
                    bool loops = ls >= 0 && (v.flagged || kLoop[v.id]) && v.maxPos <= 0;
                    if (loops) { v.pos -= (double)(c.s.size() - 1 - ls); ip = (size_t)v.pos; }
                    else { v.fade = 0; break; }
                }
                if (v.maxPos > 0 && v.pos >= v.maxPos && !v.dying) v.dying = true;
                float fr = (float)(v.pos - ip);
                float smp = c.s[ip] + (c.s[ip + 1] - c.s[ip]) * fr;
                if (stopping) { v.fade -= fadeStep; if (v.fade <= 0) { v.fade = 0; break; } }
                if (v.fadeIn < 1.0f) v.fadeIn = std::min(1.0f, v.fadeIn + 1.0f / (0.002f * g_OutRate));
                mix[i] += smp * v.vol * v.fade * v.fadeIn * g_Master;
                v.pos += step;
            }
        }
        g_Voices.erase(std::remove_if(g_Voices.begin(), g_Voices.end(), [](const Voice& v) { return v.fade <= 0; }),
                       g_Voices.end());
    }
    for (auto& x : mix) x = std::max(-1.0f, std::min(1.0f, x));
    if (g_Dump) fwrite(mix.data(), sizeof(float), frames, g_Dump);
    SDL_PutAudioStreamData(stream, mix.data(), frames * (int)sizeof(float));
}

static void Init() {
    static bool done = false;
    if (done) return;
    done = true;
    const char* e = getenv("HOOKSHOT_GORON_SFX");
    if (e && !strcmp(e, "0")) return;
    if (!(e && !strcmp(e, "1")) && getenv("HOS_AUDIO") && !strcmp(getenv("HOS_AUDIO"), "0")) return;   // (=1 forces)
    if (const char* dp = getenv("HOOKSHOT_GORON_SFX_DUMP")) g_Dump = fopen(dp, "wb");
    if (const char* v = getenv("HOOKSHOT_GORON_SFX_VOL")) g_Master = (float)atof(v);
    std::string dir;
    if (FILE* p = popen("ls -d mods/[!_-]*/sfx 2>/dev/null | head -1", "r")) {
        char b[512];
        if (fgets(b, sizeof b, p)) { b[strcspn(b, "\n")] = 0; dir = b; }
        pclose(p);
    }
    int loaded = 0;
    for (int i = 0; i < COUNT && !dir.empty(); i++) {
        loaded += LoadWav(dir + "/" + kNames[i] + ".wav", g_Clips[i]);
        if (FILE* lf = fopen((dir + "/" + kNames[i] + ".loop").c_str(), "r")) {
            long ls = -1;
            if (fscanf(lf, "%ld", &ls) == 1 && ls >= 0 && ls + 2 < (long)g_Clips[i].s.size()) g_Clips[i].loopStart = ls;
            fclose(lf);
        }
    }
    if (!loaded) { OverlayLog("[sfx] no Goron sounds found (mods/*/sfx)\n"); return; }
    if (!SDL_WasInit(SDL_INIT_AUDIO) && !SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        OverlayLog("[sfx] SDL audio init failed: %s\n", SDL_GetError());
        return;
    }
    SDL_AudioSpec spec{SDL_AUDIO_F32, 1, g_OutRate};
    g_Stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, Pull, nullptr);
    if (!g_Stream) { OverlayLog("[sfx] audio stream failed: %s\n", SDL_GetError()); return; }
    SDL_ResumeAudioStreamDevice(g_Stream);
    g_On = true;
    OverlayLog("[sfx] %d Goron sounds from %s (volume %.2f)\n", loaded, dir.c_str(), g_Master);
}

// Audio_SetSyncedSfxFreqAndVolume
static void Synced(float param, float* freq, float* vol) {
    if (param > 6.0f) { *vol = 1.0f; *freq = 1.1f; return; }
    float t = param / 6.0f;
    *vol = t * (1.0f - 0.775f) + 0.775f;
    *freq = t * 0.2f + 0.9f;
}

// MM's sfx bank (AudioSfx_ProcessRequest): one entry per sound per source. Re-requesting a one-shot that is still
// playing interrupts it and starts it again (its id carries SFX_FLAG); a "- SFX_FLAG" request only refreshes it.
// The player's sounds share a few channels (gUsedChannelsPerBank): the oldest is dropped beyond kChannels.
constexpr int kChannels = 4;
static void Retire(int id) {
    for (auto& v : g_Voices) if (v.id == id && !v.dying) v.dying = true;    // short declick fade
}
static void LimitChannels() {
    int live = 0;
    for (auto it = g_Voices.rbegin(); it != g_Voices.rend(); ++it)
        if (!it->dying && ++live > kChannels) it->dying = true;
}
static void Play(int id, float freq = 1.0f, float vol = 1.0f) {
    if (!g_On) return;
    std::lock_guard<std::mutex> lk(g_Mx);
    if (getenv("HOOKSHOT_GORON_SFX_LOG")) OverlayLog("[sfx] %s freq %.2f vol %.2f\n", kNames[id] + 6, freq, vol);
    Retire(id);
    g_Voices.push_back(Voice{id, 0.0, freq, vol, false, g_Tick.load(), 1.0f, 0.0f, false});
    LimitChannels();
}

static void PlaySynced(int id, float param) { float f, v; Synced(param, &f, &v); Play(id, f, v); }

// "- SFX_FLAG": call every game frame while the sound should continue
static void PlayFlagged(int id, float freq = 1.0f, float vol = 1.0f) {
    if (!g_On) return;
    std::lock_guard<std::mutex> lk(g_Mx);
    const int tick = g_Tick.load();
    for (auto& v : g_Voices)
        if (v.id == id && v.flagged && !v.dying && tick - v.lastTick <= 2) { v.lastTick = tick; v.freq = freq; v.vol = vol; return; }
    if (getenv("HOOKSHOT_GORON_SFX_LOG")) OverlayLog("[sfx] %s (continuous) start\n", kNames[id] + 6);
    Retire(id);
    g_Voices.push_back(Voice{id, 0.0, freq, vol, true, tick, 1.0f, 0.0f, false});
    LimitChannels();
}

// One-shot cut short after `seconds` (a single MM-style retrigger of a held sound at slow speed)
static void PlayShort(int id, float seconds, float freq, float vol) {
    if (!g_On) return;
    std::lock_guard<std::mutex> lk(g_Mx);
    Retire(id);
    Voice v{id, 0.0, freq, vol, false, g_Tick.load(), 1.0f, 0.0f, false};
    v.maxPos = seconds * g_Clips[id].rate;
    g_Voices.push_back(v);
    LimitChannels();
}

static void GameTick() { g_Tick++; }     // once per MM (20 Hz) frame

}  // namespace goron_sfx
