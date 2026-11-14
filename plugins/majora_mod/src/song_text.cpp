// The songs' texts (RESEARCH_NOTES.md §79g, §79k): LayoutMsg/Ocarina_00.msbt in each language archive
// (Mals/<lang>.Product.110.sarc.zs) gets Title_<type> and Caption_<type> for every added song. The game reads them by
// type: the songbook panel (f_83410d90 / f_83410e80) and the presentation's "You played ...!" (f_8342d250, inserted by
// Dm_SY_Ocarina's tag). A title starts with the language's grammar tag (group 201, tag 0: article, gender, elision),
// which the sentences around it use; Italian titles also carry tag 201.13 as the game's do; Japanese titles have
// reading aids (furigana, tag group 0) over their kanji.
#include "song_text.h"
#include "gamefiles.h"
#include "msbt.h"
#include "platform.h"
#include "rstb.h"
#include "songs.h"
#include "vfs.h"
#include <cstdio>
#include <cstring>
#include <string>

namespace songtext {
using namespace plat;
using gamefiles::Bytes;

// archive -> the songs' language ("" = English names, no grammar tag: Majora's Mask was never released in these)
struct Lang { const char* code; const char* lang; };
static const Lang kLangs[] = {
    {"USen", "en"}, {"EUen", "en"}, {"USfr", "fr"}, {"EUfr", "fr"}, {"USes", "es"}, {"EUes", "es"}, {"EUde", "de"},
    {"EUit", "it"}, {"JPja", "ja"}, {"EUnl", ""}, {"EUpl", ""}, {"EUru", ""}, {"USpt", ""}, {"KRko", ""}, {"CNzh", ""},
    {"TWzh", ""},
};
constexpr const char* kMsbt = "LayoutMsg/Ocarina_00.msbt";
constexpr uint32_t kArchiveExtra = 4096;               // (size table: the archive grows by the texts)

static void Tag(Bytes& out, uint16_t group, uint16_t tag, const uint8_t* data, uint16_t n) {
    const uint8_t h[7] = {0x0E, (uint8_t)group, (uint8_t)(group >> 8), (uint8_t)tag, (uint8_t)(tag >> 8), (uint8_t)n, (uint8_t)(n >> 8)};
    out.insert(out.end(), h, h + 7);
    if (n) out.insert(out.end(), data, data + n);
}

static size_t CodePoints(const std::string& s) {
    size_t n = 0;
    for (unsigned char ch : s) if ((ch & 0xC0) != 0x80) n++;
    return n;
}

// "{base|reading}" -> furigana tag (group 0, tag 0: u16 base characters, u16 reading bytes, reading) + base
static void Furigana(Bytes& out, const std::string& s) {
    size_t i = 0;
    while (i < s.size()) {
        const size_t open = s.find('{', i);
        if (open == std::string::npos) { out.insert(out.end(), s.begin() + i, s.end()); break; }
        out.insert(out.end(), s.begin() + i, s.begin() + open);
        const size_t bar = s.find('|', open), close = s.find('}', open);
        if (bar == std::string::npos || close == std::string::npos || bar > close) { out.insert(out.end(), s.begin() + open, s.end()); break; }
        const std::string base = s.substr(open + 1, bar - open - 1), reading = s.substr(bar + 1, close - bar - 1);
        Bytes data;
        const uint16_t nb = (uint16_t)CodePoints(base), nr = (uint16_t)reading.size();
        data.push_back((uint8_t)nb), data.push_back((uint8_t)(nb >> 8)), data.push_back((uint8_t)nr), data.push_back((uint8_t)(nr >> 8));
        data.insert(data.end(), reading.begin(), reading.end());
        Tag(out, 0, 0, data.data(), (uint16_t)data.size());
        out.insert(out.end(), base.begin(), base.end());
        i = close + 1;
    }
}

// lang "": English text, no grammar tag, no reading aids
static Bytes Title(const songs::Text& t, const char* lang) {
    Bytes out;
    if (*lang && t.grammar) Tag(out, 201, 0, t.grammar, 4);
    if (!strcmp(lang, "it")) Tag(out, 201, 13, nullptr, 0);
    Furigana(out, t.title);
    out.push_back(0);
    return out;
}

static Bytes Plain(const char* s) {
    Bytes out(s, s + strlen(s));
    out.push_back(0);
    return out;
}

static const songs::Text* TextFor(const songs::Song& song, const char* lang) {
    const songs::Text* en = nullptr;
    for (const songs::Text& t : song.texts) {
        if (!strcmp(t.lang, lang)) return &t;
        if (!strcmp(t.lang, "en")) en = &t;
    }
    return en;
}

static bool Patch(const Lang& lang, Bytes& file) {
    Bytes sarc;
    if (!gamefiles::Unzstd(file, sarc)) { Log("[text] %s: cannot decompress\n", lang.code); return false; }
    Bytes msg;
    for (auto& e : gamefiles::SarcList(sarc))
        if (e.name == kMsbt) msg.assign(sarc.begin() + e.offset, sarc.begin() + e.offset + e.size);
    msbt::File m;
    if (msg.empty() || !m.Parse(msg)) { Log("[text] %s: %s not readable\n", lang.code, kMsbt); return false; }
    for (const songs::Song& song : songs::All()) {
        const songs::Text* t = TextFor(song, *lang.lang ? lang.lang : "en");
        if (!t) continue;
        char label[24];
        snprintf(label, sizeof label, "Title_%02u", song.type);
        m.Set(label, Title(*t, lang.lang));
        snprintf(label, sizeof label, "Caption_%02u", song.type);
        m.Set(label, Plain(t->caption));
    }
    if (!gamefiles::SarcReplace(sarc, kMsbt, m.Write())) { Log("[text] %s: repack failed\n", lang.code); return false; }
    file = gamefiles::RawZstd(sarc.data(), sarc.size());
    Log("[text] %s: titles and captions of %zu songs added\n", lang.code, songs::All().size());
    return true;
}

void Start() {
    for (const Lang& lang : kLangs) {
        const std::string archive = std::string("Mals/") + lang.code + ".Product.110.sarc";
        vfs::Register(archive + ".zs", [&lang](Bytes& file) { return Patch(lang, file); }, rstb::Applied);
        rstb::Grow(archive, kArchiveExtra);
    }
}

}  // namespace songtext
