// Majora's Mask's songs as new song types (RESEARCH_NOTES.md §79, §79k). The game's song types are 0 Zelda's Lullaby
// .. 12 Scarecrow's Song, 13 = none; this adds 14.. (songs.h, in the order of songs::All()):
//   - score book: GameParameter/MelodyScoreBook gets entries 12 and 13 (placeholders that can never be played: nine
//     notes do not fit the eight-note rings) and one per song, so list position == type wherever the game indexes the
//     book by type (replay, note count, staff, songbook);
//   - matcher f_832b7400: each entry's parsed type is set to its song (the type names are not in the game's enum), and
//     when nothing native matched, the entries are tested against both note rings here (the game would check Zelda's
//     Lullaby's learned flag for positions past 12; the songs are known from the start for now);
//   - event flows: the event system only takes query answers inside the MelodyType enum, so the flows see every added
//     song as 13 (a value they never get natively): the presentation flows get case 13 and the type query answers 13;
//   - size table: the score book, the flows and the message archives grow, so their entries are raised (rstb.*);
//   - melody sounds f_8337b730: each song plays through a host sound whose stream then reads its own replay
//     (song_audio.cpp); the presentation is let go when that replay ends;
//   - the song effect: the player's effect is chosen by the song type; each song shows a native song's (§79i).
#include "mm_songs.h"
#include "song_audio.h"
#include "song_text.h"
#include "songs.h"
#include "byml.h"
#include "evfl.h"
#include "rstb.h"
#include "gamefiles.h"
#include "guest.h"
#include "hook.h"
#include "platform.h"
#include "vfs.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>

namespace song {
using namespace plat;
using guest::Q;
using guest::D;

constexpr uint32_t kTypeNone = 13;
constexpr uint32_t kHashScarecrowSong = 514990477;
constexpr const char* kScoreBook = "GameParameter/MelodyScoreBook/Default.game__ocarina__MelodyScoreBook.bgyml";
// the ocarina presentation's event flows: their switches on the played song list only the native types (§79e).
// Other flows that ask (Gossip Stones, the Triforce mark, the Song of Time block, cows, frogs, sages...) react to
// specific songs and are left alone.
struct Flow { const char* file; uint32_t vanillaRoom; std::set<std::string> skip; };
static const Flow kFlows[] = {
    // (Event45: the song-learning path. Event13: the first switch after free play, where "no song" ends the event: §79l)
    {"Event/EventFlow/Dm_SY_Ocarina.bfevfl", 115184, {"Event45", "Event13"}},
    {"Event/EventFlow/Dm_SY_Ocarina_move_Ed.bfevfl", 61872, {}},     // (Event12, Event109: all songs but warps)
};
constexpr uint32_t kFlowExtraRoom = 4096;               // (size table: the vanilla entry plus room for the added cases)
// The event system turns a query answer into the MelodyType enum, and anything outside it (the query's -1 for "no song",
// or 14..) becomes 13 = None (§79l). So "no song" reaches the switches as 13 too, and a case 13 in the first switch
// after free play (Event13) made it run a presentation that waited forever for a song (the softlock after playing an
// unlearned song's notes). So: Event13 keeps no case 13 and asks while the added song's replay has not started yet
// (from its match to its melody sound): the query answers Zelda's Lullaby (case 0's branch, as every case but Epona's
// and the Sun's Song); after that it answers 13, and the presentation switches past Event13 have case 13 -> case 0's
// branch. The song sensor and the world's song events see 13 (no song), as before.
constexpr uint32_t kFlowValue = 13;
constexpr uint32_t kFirstSwitchAs = 0;
static uint32_t g_Matched;                               // an added song matched, its replay not started yet (game thread)

enum : uint64_t {
    fMatcher = 0x832b7400,      // (OM, MelodyType* out) -> bool, free play
    fBookGet = 0x832b3f20,      // (book + 0x20) -> score book
    fBookList = 0x832b3f70,     // (book) -> list of scores
    fListCount = 0x80021a20,    // (list) -> count
    fScoreMatch = 0x832b8510,   // (score, ring, ring length) -> bool
    fMelodySound = 0x8337b730,  // (sound, type, kind) -> melody sound by name ("Melody_" + type name), handle via x8
    fNew = 0x8005eca0,          // operator new(align, size) on the default heap
    fQueryType = 0x82a7c980,    // flow query EventQueryMelodyTypeForNormal (x0 node): writes the played type (13 -> -1)
    fQueryOut = 0x80854e00,     // (node + 0x20, value): a query node's output
    fOmUpdate = 0x832b4df0,     // OcarinaMgr per-frame update (x0 OM, x1 pad state)
    fPlayerMain = 0x80609d00,   // player: actor variable +0x1ac0 = song type (picks the song effect)
};
// Score objects (0x70 bytes): notes container at +0x38 (data +0x50, count +0x58/+0x5c, pitch codes like the note
// rings: D 4, F 7, A 11, B 13, HighD 16) and type (+0x60). The game makes them for every entry once the size table
// entry is raised; the mod makes copies for empty slots as a fallback.
constexpr uint64_t kBookSingleton = 0x84e8c7a8;          // * score book holder
static guest::Functions g_Fn;
static GuestFn o_Matcher, o_MelodySound, o_QueryType, o_OmUpdate, o_PlayerMain;
static std::atomic<uint64_t> g_Om{0};
static std::atomic<bool> g_BookPatched{false};
static uint32_t g_Last = 0;                              // the last added type

static uint32_t Murmur3(const char* s) {                 // (seed 0: the game's names, e.g. "HealingSong" 470802952)
    const size_t n = strlen(s);
    uint32_t h = 0;
    const uint32_t c1 = 0xcc9e2d51, c2 = 0x1b873593;
    auto mix = [&](uint32_t k) { k *= c1; k = k << 15 | k >> 17; k *= c2; return k; };
    size_t i = 0;
    for (; i + 4 <= n; i += 4) {
        uint32_t k;
        memcpy(&k, s + i, 4);
        h ^= mix(k);
        h = h << 13 | h >> 19;
        h = h * 5 + 0xe6546b64;
    }
    uint32_t k = 0;
    for (size_t j = 0; i + j < n; j++) k |= (uint32_t)(uint8_t)s[i + j] << (8 * j);
    if (n % 4) h ^= mix(k);
    h ^= (uint32_t)n;
    h ^= h >> 16, h *= 0x85ebca6b, h ^= h >> 13, h *= 0xc2b2ae35, h ^= h >> 16;
    return h;
}

static uint32_t NoteHash(int key) {   // as the score book stores notes (murmur3 of the note's name)
    return key == 62 ? 1673550086u : key == 65 ? 2552959238u : key == 69 ? 1423767502u : key == 71 ? 3433458314u : 825411668u;
}
static uint32_t PitchCode(int key) { return key == 62 ? 4 : key == 65 ? 7 : key == 69 ? 11 : key == 71 ? 13 : 16; }

static void QueryTypeHook(GuestCtx* c) {
    const uint64_t node = c->x[0];
    o_QueryType(c);
    const uint32_t t = D(g_Om + 0x220);
    if (songs::ByType(t)) g_Fn.Call(c, fQueryOut, node + 0x20, g_Matched == t ? kFirstSwitchAs : kFlowValue);
    if (getenv("MAJORA_TRACE_SONG") && songs::ByType(t))   // (development)
        Log("[trace] flow query: type %u -> %u\n", t, g_Matched == t ? kFirstSwitchAs : kFlowValue);
}

// ---- score book ------------------------------------------------------------------------------------------------------
static byml::Node Entry(const std::vector<uint32_t>& notes, uint32_t typeHash, int navi) {
    byml::Node e = byml::Node::Dict(), score = byml::Node::Array();
    for (uint32_t n : notes) score.items.push_back(byml::Node::U32(n));
    e.Set("MelodyScore", score);
    e.Set("MelodyType", byml::Node::U32(typeHash));
    if (navi) e.Set("NoteNumForNavi", byml::Node::I32(navi));
    return e;
}

static bool PatchScoreBook(gamefiles::Bytes& file) {
    byml::Node root;
    if (!byml::Parse(file, root)) { Log("[song] score book: not a BYML\n"); return false; }
    byml::Node* scores = root.Get("MelodyScores");
    if (!scores || scores->type != byml::kArray || scores->items.size() != 12) {
        Log("[song] score book: unexpected layout (%zu scores)\n", scores ? scores->items.size() : 0);
        return false;
    }
    const std::vector<uint32_t> never(9, NoteHash(62));
    scores->items.push_back(Entry(never, kHashScarecrowSong, 0));   // 12 Scarecrow's Song (its notes: GameData)
    scores->items.push_back(Entry(never, kHashScarecrowSong, 0));   // 13 none
    for (const songs::Song& s : songs::All()) {
        if (s.type != scores->items.size()) { Log("[song] score book: %s is type %u, position %zu\n", s.english, s.type, scores->items.size()); return false; }
        std::vector<uint32_t> notes;
        for (int k : s.notes) notes.push_back(NoteHash(k));
        scores->items.push_back(Entry(notes, Murmur3(s.id), 3));
    }
    file = byml::Write(root);
    g_BookPatched = true;
    Log("[song] score book: %zu songs added as positions %u..%u (%zu bytes)\n", songs::All().size(), songs::kFirst, g_Last, file.size());
    return true;
}

// The score list (0 if not loaded) and its count. A list can have a parent ([list+0x18]) whose entries come first.
static uint64_t ScoreList(GuestCtx* c, uint32_t& count) {
    count = 0;
    const uint64_t holder = Q(kBookSingleton);
    if (!holder) return 0;
    const uint64_t book = g_Fn.Call(c, fBookGet, holder + 0x20);
    const uint64_t list = book ? g_Fn.Call(c, fBookList, book) : 0;
    if (!GuestPtrOk(list)) return 0;
    count = (uint32_t)g_Fn.Call(c, fListCount, list);
    return list;
}

// Where the score pointer for a list position is, as the matcher indexes the list: position < the parent's count:
// look there; else position - that count in the list's own array at [list+0x10]. 0 when not found.
static uint64_t SlotAddr(GuestCtx* c, uint64_t list, uint32_t pos) {
    for (int depth = 0; depth < 8 && GuestPtrOk(list); depth++) {
        const uint64_t parent = Q(list + 0x18);
        if (!parent) return Q(list + 0x10) ? Q(list + 0x10) + 8ull * pos : 0;
        const uint32_t n = (uint32_t)g_Fn.Call(c, fListCount, parent);
        if (pos < n) { list = parent; continue; }
        pos -= n;
        return Q(list + 0x10) ? Q(list + 0x10) + 8ull * pos : 0;
    }
    return 0;
}

static uint64_t MakeScore(GuestCtx* c, uint64_t model, const std::vector<int>& keys, uint32_t type) {
    const uint64_t obj = g_Fn.Call(c, fNew, 8, 0x70);
    const uint64_t notes = g_Fn.Call(c, fNew, 8, 4 * 16);
    if (!GuestPtrOk(obj) || !GuestPtrOk(notes)) return 0;
    memcpy(GPtr(obj), GPtr(model), 0x70);
    uint32_t n = 0;
    for (int k : keys) GWrite<uint32_t>(notes + 4 * n++, PitchCode(k));
    GWrite<uint64_t>(obj + 0x50, notes);
    GWrite<uint32_t>(obj + 0x58, n);
    GWrite<uint32_t>(obj + 0x5c, n);
    GWrite<uint32_t>(obj + 0x60, type);
    return obj;
}

// The songs' score slots once the book is loaded (index = type): empty ones filled (fallback), each song's type
// written. Empty if not ready.
static std::vector<uint64_t> g_Slot;                     // (game thread only)
static bool Scores(GuestCtx* c) {
    static uint64_t s_List;
    uint32_t count = 0;
    const uint64_t list = ScoreList(c, count);
    static int s_NoList;
    if (!list && s_NoList++ < 3) Log("[song] no score list yet (holder %#llx)\n", (unsigned long long)Q(kBookSingleton));
    if (!list || count <= g_Last) {
        static int s_Logged;
        if (list && s_Logged++ < 3) Log("[song] the score list has %u entries (needs %u)\n", count, g_Last + 1);
        return false;
    }
    if (list == s_List && !g_Slot.empty()) return true;
    const uint64_t model = Q(SlotAddr(c, list, 12));
    if (!GuestPtrOk(model)) {
        static int s_NoModel;
        if (s_NoModel++ < 3) Log("[song] score list %#llx (%u entries, parent %#llx): no score at 12\n", (unsigned long long)list, count, (unsigned long long)Q(list + 0x18));
        return false;
    }
    g_Slot.assign(g_Last + 1, 0);
    for (uint32_t pos = 13; pos <= g_Last; pos++) g_Slot[pos] = SlotAddr(c, list, pos);
    if (g_Slot[13] && !Q(g_Slot[13])) GWrite<uint64_t>(g_Slot[13], MakeScore(c, model, std::vector<int>(9, 62), kTypeNone));
    for (const songs::Song& s : songs::All()) {
        const uint64_t slot = g_Slot[s.type];
        if (!slot) { Log("[song] no score slot for %s\n", s.english); continue; }
        uint64_t score = Q(slot);
        if (!score) {
            score = MakeScore(c, model, s.notes, s.type);
            GWrite<uint64_t>(slot, score);
            Log("[song] score object made for %s\n", s.english);
        }
        if (GuestPtrOk(score) && D(score + 0x60) != s.type) GWrite<uint32_t>(score + 0x60, s.type);
    }
    s_List = list;
    Log("[song] %zu song scores ready (list %#llx, %u entries, parent %#llx)\n", songs::All().size(),
        (unsigned long long)list, count, (unsigned long long)Q(list + 0x18));
    return true;
}

// ---- the ocarina event flows -------------------------------------------------------------------------------------------
// Every switch on GameSystemActor.EventQueryMelodyTypeForNormal gets case 13 -> Zelda's Lullaby's event (its branches
// are the generic ones: waits, closing the dialog, the condition sensor that reads the played type itself), except the
// listed ones. Switches with no case for Zelda's Lullaby (warp songs only) are not touched.
static bool PatchFlow(const Flow& flow, gamefiles::Bytes& file) {
    gamefiles::Bytes data;
    if (!gamefiles::Unzstd(file, data)) { Log("[song] %s: cannot decompress\n", flow.file); return false; }
    const size_t before = data.size();
    std::string names;
    const int n = evfl::AddCase(data, "GameSystemActor", "EventQueryMelodyTypeForNormal", kFlowValue, 0, flow.skip, &names);
    if (n <= 0) { Log("[song] %s: no switch changed (%d)\n", flow.file, n); return false; }
    if (data.size() > flow.vanillaRoom + kFlowExtraRoom) { Log("[song] %s: %zu bytes is more than the room set aside\n", flow.file, data.size()); return false; }
    Log("[song] %s: case %u added to %d switches (%s), %zu -> %zu bytes\n", flow.file, kFlowValue, n, names.c_str(), before, data.size());
    file = gamefiles::RawZstd(data.data(), data.size());
    return true;
}

// ---- matcher ---------------------------------------------------------------------------------------------------------
static void MatcherHook(GuestCtx* c) {
    const uint64_t om = c->x[0], out = c->x[1];
    g_Om = om;
    const bool ready = g_BookPatched && Scores(c);
    o_Matcher(c);
    if ((c->x[0] & 1) && GuestPtrOk(out) && songs::ByType(D(out))) g_Matched = D(out);
    if ((c->x[0] & 1) || !ready || !GuestPtrOk(out)) return;
    for (const songs::Song& s : songs::All()) {
        const uint64_t score = s.type < g_Slot.size() && g_Slot[s.type] ? Q(g_Slot[s.type]) : 0;
        if (!GuestPtrOk(score)) continue;
        for (auto [ring, len] : {std::pair<uint64_t, uint64_t>{0x158, 0x25c}, {0x138, 0x258}}) {
            if (g_Fn.Call(c, fScoreMatch, score, om + ring, D(om + len)) & 1) {
                GWrite<uint32_t>(out, s.type);
                GWrite<uint32_t>(om + 0x244, 0);
                g_Matched = s.type;
                c->x[0] = 1;
                Log("[song] %s played\n", s.english);
                return;
            }
        }
    }
}

// ---- melody sounds ---------------------------------------------------------------------------------------------------
// The sound plays through the song's host, a native song's sound (the type is swapped), whose stream then reads the
// song's replay (song_audio.cpp). (A held note's sound would be a cleaner host, but the game keeps the notes' streams
// in memory once played, so their files are not read again: §79k.) The result is a 16-byte handle (x8 = where it goes): pool index i8 at +0, slot i16
// at +2 (-1 none), id u32 at +4; the sound is alive while the pool's object at that slot still has that id. The
// presentation waits for the replay (OcarinaMgr+0x20, kind 0) to be gone; the host's stream is longer than the replay,
// so the handle is let go when the replay ends.
struct Replay { uint64_t lo = 0, hi = 0; int64_t endNs = 0; };
static Replay g_Replay;                                  // (game thread only)
static int64_t NowNs() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static void MelodySoundHook(GuestCtx* c) {
    const uint64_t out = c->x[8], kind = c->x[2];
    const songs::Song* s = songs::ByType((uint32_t)c->x[1]);
    if (s) {
        if (kind == 0) g_Matched = 0;                    // (its replay starts: the presentation switches answer 13)
        songaudio::Asked(s->type, kind);
        c->x[1] = (uint64_t)(s->hostType >= 0 ? s->hostType : 0);
    }
    o_MelodySound(c);
    const float secs = s ? songaudio::Seconds(s->type) : 0.f;
    if (s && kind == 0 && secs > 0 && GuestPtrOk(out)) {
        g_Replay = {Q(out), Q(out + 8), NowNs() + (int64_t)(secs * 1e9)};
        Log("[song] replay handle %016llx %016llx, ends in %.2f s\n", (unsigned long long)g_Replay.lo, (unsigned long long)g_Replay.hi, secs);
    }
}

// ---- the song effect -------------------------------------------------------------------------------------------------
// The player's song effect (§79i): entering the ocarina node (f_828b4340) emits the ELink key "Ocarina_Correct", and
// ELink picks Ply_Ocarina_Correct_<Song>_00 by an actor variable (+0x1ac0) that f_80609d00 sets from the song type
// each frame. The added types have no asset, so f_80609d00 sees the song's effectFrom while one plays
// (MAJORA_EFFECT_FROM=<type> shows that one's effect for every song, for testing).
static int g_EffectOverride = -1;
static void PlayerMainHook(GuestCtx* c) {
    const uint64_t om = g_Om;
    const uint32_t t = om ? D(om + 0x220) : 0;
    const songs::Song* s = songs::ByType(t);
    const uint32_t from = s ? (g_EffectOverride >= 0 ? (uint32_t)g_EffectOverride : s->effectFrom) : 0;
    if (s) GWrite<uint32_t>(om + 0x220, from);
    o_PlayerMain(c);
    if (s && D(om + 0x220) == from) GWrite<uint32_t>(om + 0x220, t);
}

static void OmUpdateHook(GuestCtx* c) {
    const uint64_t om = c->x[0];
    if (g_Replay.endNs && NowNs() >= g_Replay.endNs) {
        if (Q(om + 0x20) == g_Replay.lo && Q(om + 0x28) == g_Replay.hi) {
            GWrite<int16_t>(om + 0x22, -1);
            Log("[song] replay over: handle released\n");
        }
        g_Replay = {};
    }
    o_OmUpdate(c);
}

// ---- start -----------------------------------------------------------------------------------------------------------
bool Start() {
    if (!g_Fn.Resolve({fMatcher, fBookGet, fBookList, fListCount, fScoreMatch, fMelodySound, fNew, fQueryType, fQueryOut,
                       fOmUpdate, fPlayerMain}, "song")) {
        Log("[song] off\n");
        return false;
    }
    for (const songs::Song& s : songs::All()) g_Last = std::max(g_Last, s.type);
    songtext::Start();
    songaudio::Start();
    vfs::Register(kScoreBook, PatchScoreBook, rstb::Applied);   // (grown: waits for its size)
    // (the parsed book lives in the memory the size table sets aside for the file: 7768 bytes held the 12 vanilla
    // scores; with three more, the note arrays and the objects past the 13th failed to allocate; 8192 more held 15)
    rstb::Require("GameParameter/MelodyScoreBook/Default.game__ocarina__MelodyScoreBook.bgyml", 7768 + 8192 * 3);
    for (const Flow& f : kFlows) {
        vfs::Register(std::string(f.file) + ".zs", [&f](gamefiles::Bytes& file) { return PatchFlow(f, file); }, rstb::Applied);
        rstb::Require(f.file, f.vanillaRoom + kFlowExtraRoom);
    }
    o_MelodySound = hook::Install(g_Fn.Host(fMelodySound), MelodySoundHook, "melody sound f_8337b730");
    if (const char* v = getenv("MAJORA_EFFECT_FROM")) g_EffectOverride = atoi(v) % 13;   // (testing)
    o_PlayerMain = hook::Install(g_Fn.Host(fPlayerMain), PlayerMainHook, "player f_80609d00 (song effect)");
    o_OmUpdate = hook::Install(g_Fn.Host(fOmUpdate), OmUpdateHook, "ocarina update f_832b4df0");
    o_QueryType = hook::Install(g_Fn.Host(fQueryType), QueryTypeHook, "flow query f_82a7c980");
    o_Matcher = o_QueryType ? hook::Install(g_Fn.Host(fMatcher), MatcherHook, "song matcher f_832b7400") : nullptr;
    Log("[song] %zu songs %s\n", songs::All().size(), o_Matcher ? "on" : "off (hooks failed)");
    return o_Matcher != nullptr;
}

}  // namespace song
