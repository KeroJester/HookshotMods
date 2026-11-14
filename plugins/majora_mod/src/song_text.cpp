// The Song of Healing's texts (RESEARCH_NOTES.md §79g): LayoutMsg/Ocarina_00.msbt in each language archive
// (Mals/<lang>.Product.110.sarc.zs) gets Title_14 and Caption_14. The game reads them by type: the songbook panel
// (f_83410d90 / f_83410e80) and the presentation's "You played ...!" (f_8342d250, inserted by Dm_SY_Ocarina's tag).
// A title starts with the language's grammar tag (group 201: article, gender, declension); it is copied from a
// native title with the same grammar in that language.
#include "song_text.h"
#include "gamefiles.h"
#include "msbt.h"
#include "platform.h"
#include "rstb.h"
#include "vfs.h"
#include <string>

namespace songtext {
using namespace plat;

struct Lang {
    const char* code;      // archive name
    const char* title;     // Majora's Mask's name for the song in this language (English where MM had none)
    int grammarFrom;       // the native title whose grammar tag fits (Title_%02d)
    const char* caption;
};
// en: "the Song of Healing" (as Song of Time); fr: masculine "le Chant" (as Chant d'Epona); es: feminine "la
// Cancion" (as Cancion de Epona); de: neuter "das Lied" in "X der Y" form (as Menuett des Waldes); it: feminine
// "la Canzone" (as Canzone del tempo; the Italian name is not verified); ja: no tag (as the native titles without
// furigana). Languages Majora's Mask was never released in use English for now (open question for the user).
static const char* kCaptionEn = "This gentle melody heals troubled spirits and soothes sorrowful hearts.";
static const Lang kLangs[] = {
    {"USen", "Song of Healing", 4, kCaptionEn},
    {"EUen", "Song of Healing", 4, kCaptionEn},
    {"USfr", "Chant de l'apaisement", 1, "Cette douce mélodie apaise les âmes tourmentées et guérit les cœurs meurtris."},
    {"EUfr", "Chant de l'apaisement", 1, "Cette douce mélodie apaise les âmes tourmentées et guérit les cœurs meurtris."},
    {"USes", "Canción de curación", 1, "Esta suave melodía cura a las almas atormentadas y consuela los corazones tristes."},
    {"EUes", "Canción de curación", 1, "Esta suave melodía cura a las almas atormentadas y consuela los corazones tristes."},
    {"EUde", "Lied der Befreiung", 6, "Diese sanfte Melodie heilt gequälte Seelen und tröstet traurige Herzen."},
    {"EUit", "Canzone della guarigione", 4, "Questa dolce melodia guarisce le anime tormentate e consola i cuori afflitti."},
    {"JPja", "いやしの歌", -1, "さまよう魂をいやし、悲しみをやわらげる、やさしい旋律。"},
    {"EUnl", "Song of Healing", 4, kCaptionEn},
    {"EUpl", "Song of Healing", 4, kCaptionEn},
    {"EUru", "Song of Healing", -1, kCaptionEn},
    {"USpt", "Song of Healing", 4, kCaptionEn},
    {"KRko", "Song of Healing", -1, kCaptionEn},
    {"CNzh", "Song of Healing", -1, kCaptionEn},
    {"TWzh", "Song of Healing", -1, kCaptionEn},
};
constexpr const char* kMsbt = "LayoutMsg/Ocarina_00.msbt";
constexpr uint32_t kArchiveExtra = 4096;               // (size table: the archive grows by the two texts)

static gamefiles::Bytes Text(const gamefiles::Bytes& tag, const std::string& s) {
    gamefiles::Bytes t(tag);
    t.insert(t.end(), s.begin(), s.end());
    t.push_back(0);
    return t;
}

static bool Patch(const Lang& lang, gamefiles::Bytes& file) {
    gamefiles::Bytes sarc;
    if (!gamefiles::Unzstd(file, sarc)) { Log("[text] %s: cannot decompress\n", lang.code); return false; }
    gamefiles::Bytes msg;
    for (auto& e : gamefiles::SarcList(sarc))
        if (e.name == kMsbt) msg.assign(sarc.begin() + e.offset, sarc.begin() + e.offset + e.size);
    msbt::File m;
    if (msg.empty() || !m.Parse(msg)) { Log("[text] %s: %s not readable\n", lang.code, kMsbt); return false; }
    gamefiles::Bytes tag;
    if (lang.grammarFrom >= 0) {
        char label[16];
        snprintf(label, sizeof label, "Title_%02d", lang.grammarFrom);
        if (const gamefiles::Bytes* t = m.Text(label)) tag = msbt::LeadingTag(*t);
        if (tag.size() < 7 || tag[1] != 201 || tag[2] != 0) tag.clear();   // (only the grammar tag, group 201)
    }
    m.Set("Title_14", Text(tag, lang.title));
    m.Set("Caption_14", Text({}, lang.caption));
    if (!gamefiles::SarcReplace(sarc, kMsbt, m.Write())) { Log("[text] %s: repack failed\n", lang.code); return false; }
    file = gamefiles::RawZstd(sarc.data(), sarc.size());
    Log("[text] %s: Title_14 \"%s\" and Caption_14 added\n", lang.code, lang.title);
    return true;
}

void Start() {
    for (const Lang& lang : kLangs) {
        const std::string archive = std::string("Mals/") + lang.code + ".Product.110.sarc";
        vfs::Register(archive + ".zs", [&lang](gamefiles::Bytes& file) { return Patch(lang, file); }, rstb::Applied);
        rstb::Grow(archive, kArchiveExtra);
    }
}

}  // namespace songtext
