// The Song of Healing as the game's 14th song type (RESEARCH_NOTES.md §79). The game's song types are 0 Zelda's
// Lullaby .. 12 Scarecrow's Song, 13 = none; this adds 14 = HealingSong:
//   - score book: GameParameter/MelodyScoreBook gets entries 12 and 13 (placeholders that can never be played: nine
//     notes do not fit the eight-note rings) and 14 = B A F B A F, so list position 14 is the song wherever the game
//     indexes the book by type (replay, note count, staff, songbook);
//   - matcher f_832b7400: the entry's parsed type is set to 14, and when nothing native matched, the entry is tested
//     against both note rings here (the game would check Zelda's Lullaby's learned flag for position 14; the song is
//     known from the start for now);
//   - event flows: the event system only takes query answers inside the MelodyType enum, so the flows see the song as
//     13 (a value they never get natively): the presentation flows get case 13 and the type query answers 13 for it;
//   - size table: the score book and the flows grow, so their entries are raised (rstb.*);
//   - melody sounds f_8337b730: no "Melody_HealingSong" exists yet, so type 14 plays through a stand-in.
#include "song_healing.h"
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
#include <thread>
#include <cstdlib>
#include <cstring>
#include <set>
#include <string>

namespace song {
using namespace plat;
using guest::Q;
using guest::D;

constexpr uint32_t kHealingSong = 14;
constexpr uint32_t kTypeNone = 13;
// note names as the score book stores them (murmur3)
constexpr uint32_t kNoteD = 1673550086, kNoteF = 2552959238, kNoteA = 1423767502, kNoteB = 3433458314;
constexpr uint32_t kHashScarecrowSong = 514990477, kHashHealingSong = 470802952;
constexpr const char* kScoreBook = "GameParameter/MelodyScoreBook/Default.game__ocarina__MelodyScoreBook.bgyml";
// the ocarina presentation's event flows: their switches on the played song list only the native types (§79e).
// Other flows that ask (Gossip Stones, the Triforce mark, the Song of Time block, cows, frogs, sages...) react to
// specific songs and are left alone.
struct Flow { const char* file; uint32_t vanillaRoom; std::set<std::string> skip; };
static const Flow kFlows[] = {
    {"Event/EventFlow/Dm_SY_Ocarina.bfevfl", 115184, {"Event45"}},   // (Event45: the song-learning path)
    {"Event/EventFlow/Dm_SY_Ocarina_move_Ed.bfevfl", 61872, {}},     // (Event12, Event109: all songs but warps)
};
constexpr uint32_t kFlowExtraRoom = 4096;               // (size table: the vanilla entry plus room for the added cases)
// The event system only takes query answers inside the MelodyType enum (0..13): a 14 ends the flow even with a case
// for it (§79f). 13 ("none") never reaches a flow natively (the query reports none as -1), so the flows see the Song
// of Healing as 13: the query answers 13 for it, and the presentation flows get case 13 -> Zelda's Lullaby's event.
// Flows that react to particular songs (Gossip Stones, the Triforce mark, the Song of Time block...) have no case 13.
constexpr uint32_t kFlowValue = 13;

enum : uint64_t {
    fMatcher = 0x832b7400,      // (OM, MelodyType* out) -> bool, free play
    fBookGet = 0x832b3f20,      // (book + 0x20) -> score book
    fBookList = 0x832b3f70,     // (book) -> list of scores
    fListCount = 0x80021a20,    // (list) -> count
    fScoreMatch = 0x832b8510,   // (score, ring, ring length) -> bool
    fMelodySound = 0x8337b730,  // (sound, type, kind) -> melody sound by name ("Melody_" + type name)
    fNew = 0x8005eca0,          // operator new(align, size) on the default heap
    fQueryType = 0x82a7c980,    // flow query EventQueryMelodyTypeForNormal (x0 node): writes the played type (13 -> -1)
    fQueryOut = 0x80854e00,     // (node + 0x20, value): a query node's output
};
// The game makes at most 13 score objects (one per native song type): list slots 13 and 14 stay empty. The mod makes
// them: a copy of slot 12's object (0x70 bytes) with its own notes (container at +0x38: data +0x50, count +0x58/+0x5c,
// as pitch codes like the note rings: D 4, F 7, A 11, B 13, HighD 16) and type (+0x60).
constexpr uint32_t kPitchF = 7, kPitchA = 11, kPitchB = 13, kPitchD = 4;
constexpr uint64_t kBookSingleton = 0x84e8c7a8;          // * score book holder
static guest::Functions g_Fn;
static GuestFn o_Matcher, o_MelodySound, o_QueryType;
static std::atomic<uint64_t> g_Om{0};
static void QueryTypeHook(GuestCtx* c) {
    const uint64_t node = c->x[0];
    o_QueryType(c);
    if (D(g_Om + 0x220) == kHealingSong) g_Fn.Call(c, fQueryOut, node + 0x20, kFlowValue);
}
static std::atomic<bool> g_BookPatched{false};

// ---- score book ------------------------------------------------------------------------------------------------------
static byml::Node Entry(std::initializer_list<uint32_t> notes, uint32_t typeHash, int navi) {
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
    const auto never = {kNoteD, kNoteD, kNoteD, kNoteD, kNoteD, kNoteD, kNoteD, kNoteD, kNoteD};
    scores->items.push_back(Entry(never, kHashScarecrowSong, 0));   // 12 Scarecrow's Song (its notes: GameData)
    scores->items.push_back(Entry(never, kHashScarecrowSong, 0));   // 13 none
    scores->items.push_back(Entry({kNoteB, kNoteA, kNoteF, kNoteB, kNoteA, kNoteF}, kHashHealingSong, 3));
    file = byml::Write(root);
    g_BookPatched = true;
    Log("[song] score book: Song of Healing added as position %u (%zu bytes)\n", kHealingSong, file.size());
    return true;
}

// The score at a list position, as the matcher indexes the list: a list can have a parent ([list+0x18]) whose entries
// come first (position < the parent's count: look there; else position - that count in the list's own array at
// [list+0x10]). 0 when out of range.
static uint64_t ScoreAt(GuestCtx* c, uint32_t pos) {
    const uint64_t holder = Q(kBookSingleton);
    if (!holder) return 0;
    const uint64_t book = g_Fn.Call(c, fBookGet, holder + 0x20);
    if (!book) return 0;
    uint64_t list = g_Fn.Call(c, fBookList, book);
    if (!GuestPtrOk(list) || (uint32_t)g_Fn.Call(c, fListCount, list) <= pos) return 0;
    for (int depth = 0; depth < 8 && GuestPtrOk(list); depth++) {
        const uint64_t parent = Q(list + 0x18);
        if (!parent) return Q(Q(list + 0x10) + 8ull * pos);
        const uint32_t n = (uint32_t)g_Fn.Call(c, fListCount, parent);
        if (pos < n) { list = parent; continue; }
        return Q(Q(list + 0x10) + 8ull * (pos - n));
    }
    return 0;
}

// The list's own array of score pointers (no parent chain: checked), and its count
static uint64_t ScoreSlots(GuestCtx* c, uint32_t& count) {
    count = 0;
    const uint64_t holder = Q(kBookSingleton);
    if (!holder) return 0;
    const uint64_t book = g_Fn.Call(c, fBookGet, holder + 0x20);
    const uint64_t list = book ? g_Fn.Call(c, fBookList, book) : 0;
    if (!GuestPtrOk(list) || Q(list + 0x18)) return 0;
    count = (uint32_t)g_Fn.Call(c, fListCount, list);
    return Q(list + 0x10);
}

static uint64_t MakeScore(GuestCtx* c, uint64_t model, std::initializer_list<uint32_t> pitches, uint32_t type) {
    const uint64_t obj = g_Fn.Call(c, fNew, 8, 0x70);
    const uint64_t notes = g_Fn.Call(c, fNew, 8, 4 * 16);
    if (!GuestPtrOk(obj) || !GuestPtrOk(notes)) return 0;
    memcpy(GPtr(obj), GPtr(model), 0x70);
    uint32_t n = 0;
    for (uint32_t p : pitches) GWrite<uint32_t>(notes + 4 * n++, p);
    GWrite<uint64_t>(obj + 0x50, notes);
    GWrite<uint32_t>(obj + 0x58, n);
    GWrite<uint32_t>(obj + 0x5c, n);
    GWrite<uint32_t>(obj + 0x60, type);
    return obj;
}

// Fills list slots 13 and 14 once the book is loaded (the game leaves them empty)
static bool EnsureScores(GuestCtx* c) {
    static uint64_t s_Slots;
    uint32_t count = 0;
    const uint64_t slots = ScoreSlots(c, count);
    if (!GuestPtrOk(slots) || count < 15) return false;
    if (slots == s_Slots && Q(slots + 8 * 14)) return true;
    const uint64_t model = Q(slots + 8 * 12);
    if (!GuestPtrOk(model)) return false;
    // (normally the game has made them: the size table entry is raised; this is the fallback)
    if (!Q(slots + 8 * 13)) {
        GWrite<uint64_t>(slots + 8 * 13, MakeScore(c, model, {kPitchD, kPitchD, kPitchD, kPitchD, kPitchD, kPitchD, kPitchD, kPitchD, kPitchD}, kTypeNone));
        Log("[song] score object made for list slot 13\n");
    }
    if (!Q(slots + 8 * 14)) {
        GWrite<uint64_t>(slots + 8 * 14, MakeScore(c, model, {kPitchB, kPitchA, kPitchF, kPitchB, kPitchA, kPitchF}, kHealingSong));
        Log("[song] score object made for list slot 14\n");
    }
    s_Slots = slots;
    return Q(slots + 8 * 14) != 0;
}

// ---- the ocarina event flows -------------------------------------------------------------------------------------------
// Every switch on GameSystemActor.EventQueryMelodyTypeForNormal gets case 14 -> Zelda's Lullaby's event (its branches
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
    const uint64_t score = g_BookPatched && EnsureScores(c) ? ScoreAt(c, kHealingSong) : 0;
    if (GuestPtrOk(score) && D(score + 0x60) != kHealingSong) {   // (its type name is not in the game's enum)
        Log("[song] Song of Healing score %#llx: type %u -> %u\n", (unsigned long long)score, D(score + 0x60), kHealingSong);
        GWrite<uint32_t>(score + 0x60, kHealingSong);
    }
    o_Matcher(c);
    if ((c->x[0] & 1) || !GuestPtrOk(score) || !GuestPtrOk(out)) return;
    for (auto [ring, len] : {std::pair<uint64_t, uint64_t>{0x158, 0x25c}, {0x138, 0x258}}) {
        if (g_Fn.Call(c, fScoreMatch, score, om + ring, D(om + len)) & 1) {
            GWrite<uint32_t>(out, kHealingSong);
            GWrite<uint32_t>(om + 0x244, 0);
            c->x[0] = 1;
            Log("[song] Song of Healing played\n");
            return;
        }
    }
}

// ---- melody sounds ---------------------------------------------------------------------------------------------------
static void MelodySoundHook(GuestCtx* c) {
    if ((uint32_t)c->x[1] == kHealingSong) {
        Log("[song] melody sound kind %llu for the Song of Healing (stand-in: Zelda's Lullaby)\n", (unsigned long long)c->x[2]);
        c->x[1] = 0;
    }
    o_MelodySound(c);
}

// ---- start -----------------------------------------------------------------------------------------------------------
bool Start() {
    if (!g_Fn.Resolve({fMatcher, fBookGet, fBookList, fListCount, fScoreMatch, fMelodySound, fNew, fQueryType, fQueryOut}, "song")) {
        Log("[song] off\n");
        return false;
    }
    vfs::Register(kScoreBook, PatchScoreBook);
    // (the parsed book lives in the memory the size table sets aside for the file: 7768 bytes held the 12 vanilla
    // scores; with three more, the note arrays and the objects past the 13th failed to allocate)
    rstb::Require("GameParameter/MelodyScoreBook/Default.game__ocarina__MelodyScoreBook.bgyml", 7768 + 8192);
    for (const Flow& f : kFlows) {
        vfs::Register(std::string(f.file) + ".zs", [&f](gamefiles::Bytes& file) { return PatchFlow(f, file); });
        rstb::Require(f.file, f.vanillaRoom + kFlowExtraRoom);
    }
    o_MelodySound = hook::Install(g_Fn.Host(fMelodySound), MelodySoundHook, "melody sound f_8337b730");
    o_QueryType = hook::Install(g_Fn.Host(fQueryType), QueryTypeHook, "flow query f_82a7c980");
    o_Matcher = o_QueryType ? hook::Install(g_Fn.Host(fMatcher), MatcherHook, "song matcher f_832b7400") : nullptr;
    Log("[song] Song of Healing %s\n", o_Matcher ? "on" : "off (hooks failed)");
    return o_Matcher != nullptr;
}

}  // namespace song
