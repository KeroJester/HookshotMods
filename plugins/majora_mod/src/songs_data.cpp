// The songs' data (see songs.h). Notes and replays: Majora's Mask (3D). Names: Majora's Mask 3D's own for English,
// French and Spanish (its message files, "You played the ...!"); German, Italian and Japanese are not verified against
// Majora's Mask (RESEARCH_NOTES.md §79k). Captions are the mod's own text. Languages Majora's Mask was never released
// in show the English name.
#include "songs.h"

namespace songs {

const char* PitchName(int key) {
    static const char* const k[] = {"LowB", "LowC", "LowCsharp", "D", "Dsharp", "E", "F", "Fsharp", "G", "Gsharp", "A",
                                    "Asharp", "B", "C", "Csharp", "HighD", "HighDsharp", "HighE", "HighF"};
    return key >= 59 && key <= 77 ? k[key - 59] : nullptr;
}

// grammar tag parameters (§79k): [gender][elision/declension][article][1]
static const uint8_t kEnThe[4] = {0x00, 0x01, 0xff, 0x01};    // "the Song of Time"
static const uint8_t kFrM[4] = {0x01, 0x00, 0x00, 0x01};      // le Chant
static const uint8_t kFrMV[4] = {0x01, 0x04, 0x00, 0x01};     // l'Hymne (masculine, elided: as "l'Esprit")
static const uint8_t kFrF[4] = {0x02, 0x01, 0x01, 0x01};      // la Sonate
static const uint8_t kFrFV[4] = {0x02, 0x04, 0x01, 0x01};     // l'Ode (feminine, elided: as "l'Épée")
static const uint8_t kEsF[4] = {0x02, 0x01, 0x01, 0x01};      // la Canción
static const uint8_t kDeF[4] = {0x02, 0x01, 0x01, 0x01};      // die Hymne
static const uint8_t kDeN[4] = {0x03, 0x02, 0x02, 0x01};      // das Lied (as "das Menuett")
static const uint8_t kItF[4] = {0x02, 0x02, 0x02, 0x01};      // la Canzone
static const uint8_t kItFV[4] = {0x02, 0x03, 0x03, 0x01};     // l'Elegia (as "l'Ode a un uomo di paglia")

constexpr int D = 62, F = 65, A = 69, B = 71, D5 = 74;

static std::vector<Song> Make() {
    std::vector<Song> s;
    s.push_back({14, "HealingSong", "Song of Healing", {B, A, F, B, A, F},
        // NA_BGM_OCA_CURE_SEQ
        {{0.0057f, 71, 118, 0.5341f}, {0.5511f, 69, 118, 0.5114f}, {1.0966f, 65, 99, 0.3977f}, {1.642f, 71, 124, 0.5455f},
         {2.1875f, 69, 118, 0.483f}, {2.7329f, 65, 116, 0.4034f}, {3.2784f, 71, 114, 0.5227f}, {3.8238f, 69, 122, 0.4261f},
         {4.3693f, 64, 116, 0.2614f}, {4.642f, 62, 118, 0.3295f}, {4.9147f, 64, 122, 1.5625f}},
        0, nullptr, 0,   // (Zelda's Lullaby's sound: it opens on B as this does, and is long enough)
        {{"en", "Song of Healing", kEnThe, "This gentle melody heals troubled spirits and soothes sorrowful hearts."},
         {"fr", "Chant de l'apaisement", kFrM, "Cette douce mélodie apaise les âmes tourmentées et guérit les cœurs meurtris."},
         {"es", "Canción de curación", kEsF, "Esta suave melodía cura a las almas atormentadas y consuela los corazones tristes."},
         {"de", "Lied der Befreiung", kDeN, "Diese sanfte Melodie heilt gequälte Seelen und tröstet traurige Herzen."},
         {"it", "Canzone della guarigione", kItF, "Questa dolce melodia guarisce le anime tormentate e consola i cuori afflitti."},
         {"ja", "いやしの{歌|うた}", nullptr, "さまよう魂をいやし、悲しみをやわらげる、やさしい旋律。"}}});
    s.push_back({15, "SoaringSong", "Song of Soaring", {F, B, D5, F, B, D5},
        // NA_BGM_OCA_BIGWING_SEQ (its doubled track left out)
        {{0.0f, 65, 84, 0.325f}, {0.3f, 71, 80, 0.2938f}, {0.6f, 74, 94, 0.55f}, {1.2f, 65, 73, 0.35f},
         {1.5f, 71, 76, 0.2938f}, {1.8f, 74, 96, 1.7625f}},
        -1, "F", 4,
        {{"en", "Song of Soaring", kEnThe, "A melody that carries you on great wings to places you have been before."},
         {"fr", "Chant de l'envol", kFrM, "Une mélodie qui vous porte sur de grandes ailes vers les lieux déjà visités."},
         {"es", "Canción de vuelo", kEsF, "Una melodía que te lleva con grandes alas a los lugares que ya has visitado."},
         {"de", "Lied des Himmels", kDeN, "Eine Melodie, die dich auf großen Schwingen an bereits besuchte Orte trägt."},
         {"it", "Canzone del volo", kItF, "Una melodia che ti porta su grandi ali nei luoghi già visitati."},
         {"ja", "{大翼|おおつばさ}の{歌|うた}", nullptr, "大きな翼で、訪れたことのある場所へ運んでくれるメロディ。"}}});
    s.push_back({16, "AwakeningSonata", "Sonata of Awakening", {D5, B, D5, B, D, A, D},
        // NA_BGM_OCA_AWAKING_SEQ
        {{0.0f, 74, 102, 0.2394f}, {0.3191f, 71, 100, 0.2327f}, {0.6383f, 74, 100, 0.2726f}, {0.9574f, 71, 100, 0.6383f},
         {1.5957f, 62, 102, 0.6449f}, {2.234f, 69, 99, 1.2434f}, {3.5106f, 62, 92, 1.2101f}},
        -1, "HighD", 2,
        {{"en", "Sonata of Awakening", kEnThe, "A melody that awakens those sunk in a deep slumber."},
         {"fr", "Sonate de l'éveil", kFrF, "Une mélodie qui réveille ceux qui sont plongés dans un profond sommeil."},
         {"es", "Sonata del despertar", kEsF, "Una melodía que despierta a quienes duermen un sueño profundo."},
         {"de", "Sonate des Erwachens", kDeF, "Eine Melodie, die jene weckt, die in tiefem Schlaf versunken sind."},
         {"it", "Sonata del risveglio", kItF, "Una melodia che risveglia chi è immerso in un sonno profondo."},
         {"ja", "{目覚|めざ}めのソナタ", nullptr, "深い眠りについた者を目覚めさせるメロディ。"}}});
    s.push_back({17, "GoronLullaby", "Goron Lullaby", {D, A, B, D, A, B, A, D},
        // NA_BGM_OCA_LULLABY_SEQ
        {{0.0f, 62, 88, 0.7631f}, {0.6977f, 69, 90, 0.6042f}, {1.3643f, 71, 97, 0.4167f}, {2.0095f, 62, 102, 0.6681f},
         {2.6992f, 69, 96, 0.6528f}, {3.3658f, 71, 90, 0.5444f}, {4.011f, 69, 95, 0.7112f}, {4.7006f, 62, 86, 1.8403f}},
        -1, "D", 1,
        {{"en", "Goron Lullaby", kEnThe, "A Goron lullaby that lulls even the mightiest to sleep."},
         {"fr", "Berceuse des Gorons", kFrF, "Une berceuse goron qui endort même les plus robustes."},
         {"es", "Nana goron", kEsF, "Una nana goron que adormece incluso a los más fuertes."},
         {"de", "Goronen-Wiegenlied", kDeN, "Ein Goronen-Wiegenlied, das selbst die Stärksten in den Schlaf wiegt."},
         {"it", "Ninna nanna dei Goron", kItF, "Una ninna nanna goron che fa addormentare anche i più forti."},
         {"ja", "ゴロンの{子守歌|こもりうた}", nullptr, "どんなに強い者でも眠りに誘う、ゴロン族の子守歌。"}}});
    s.push_back({18, "BossaNova", "New Wave Bossa Nova", {B, D5, B, A, F, B, A},
        // NA_BGM_OCA_TIDE_SEQ
        {{0.0f, 71, 102, 1.0625f}, {1.0714f, 74, 98, 0.2232f}, {1.2857f, 71, 94, 0.1607f}, {1.5f, 69, 92, 1.2143f},
         {2.7857f, 65, 98, 0.2232f}, {3.0f, 71, 98, 0.2143f}, {3.2143f, 69, 94, 3.0357f}},
        -1, "B", 5,
        {{"en", "New Wave Bossa Nova", kEnThe, "A gentle melody of the waves, sung by a Zora to her eggs."},
         {"fr", "Bossa-nova des flots", kFrF, "Une douce mélodie des flots, chantée par une Zora à ses œufs."},
         {"es", "Bossanova de las olas", kEsF, "Una suave melodía de las olas que una zora canta a sus huevos."},
         {"de", "Bossa Nova der Wellen", kDeF, "Eine sanfte Melodie der Wellen, die eine Zora ihren Eiern vorsingt."},
         {"it", "Bossa nova delle onde", kItF, "Una dolce melodia delle onde, cantata da una Zora alle sue uova."},
         {"ja", "{潮騒|しおさい}のボサノバ", nullptr, "ゾーラが卵に歌って聞かせる、波のようにやさしいメロディ。"}}});
    s.push_back({19, "ElegyOfEmptiness", "Elegy of Emptiness", {A, B, A, F, A, D5, B},
        // NA_BGM_OCA_SHELL_SEQ
        {{0.0f, 69, 112, 1.3393f}, {1.4286f, 71, 110, 0.7143f}, {2.1428f, 69, 112, 0.625f}, {2.8571f, 65, 108, 0.3571f},
         {3.2143f, 69, 108, 0.3571f}, {3.5714f, 74, 120, 0.625f}, {4.2857f, 71, 115, 1.4286f}},
        -1, "A", 3,
        {{"en", "Elegy of Emptiness", kEnThe, "A mournful melody that leaves behind an empty shell of yourself."},
         {"fr", "Hymne du vide", kFrMV, "Une mélodie mélancolique qui laisse derrière vous une enveloppe vide."},
         {"es", "Elegía al vacío", kEsF, "Una melodía melancólica que deja atrás un cascarón vacío de ti mismo."},
         {"de", "Elegie der Leere", kDeF, "Eine wehmütige Melodie, die eine leere Hülle von dir zurücklässt."},
         {"it", "Elegia del vuoto", kItFV, "Una melodia malinconica che lascia dietro di sé un guscio vuoto."},
         {"ja", "{抜|ぬ}け{殻|がら}のエレジー", nullptr, "自分の抜け殻をその場に残す、もの悲しいメロディ。"}}});
    s.push_back({20, "OathToOrder", "Oath to Order", {A, F, D, F, A, D5},
        // NA_BGM_OCA_PLEDGE_SEQ (its doubled track left out)
        {{0.0f, 69, 124, 1.6216f}, {1.6216f, 65, 108, 0.8108f}, {2.4324f, 62, 98, 0.8108f}, {3.2432f, 65, 98, 0.8108f},
         {4.054f, 69, 114, 0.8108f}, {4.8648f, 74, 120, 1.5203f}},
        -1, "A", 3,
        {{"en", "Oath to Order", kEnThe, "A solemn call that summons the four giants."},
         {"fr", "Ode de l'appel", kFrFV, "Un appel solennel qui invoque les quatre géants."},
         {"es", "Oda al orden", kEsF, "Una llamada solemne que invoca a los cuatro gigantes."},
         {"de", "Hymne des Appells", kDeF, "Ein feierlicher Ruf, der die vier Giganten herbeiruft."},
         {"it", "Ode all'ordine", kItFV, "Un richiamo solenne che invoca i quattro giganti."},
         {"ja", "{誓|ちか}いの{号令|ごうれい}", nullptr, "四人の巨人を呼び寄せる、おごそかな呼びかけ。"}}});
    s.push_back({21, "InvertedSongOfTime", "Inverted Song of Time", {F, D, A, F, D, A},
        // NA_BGM_OCA_TIME_SPEED_SEQ; the last note held 0.66 s instead of 1.9 s (the host, one held F, lasts 7.2 s)
        {{0.0057f, 65, 110, 0.5455f}, {0.5511f, 62, 127, 1.0909f}, {1.642f, 69, 112, 0.4773f}, {2.1875f, 65, 112, 0.5455f},
         {2.7329f, 62, 122, 1.0909f}, {3.8238f, 69, 95, 0.4545f}, {4.3688f, 64, 112, 0.75f}, {5.1188f, 63, 112, 0.25f},
         {5.3688f, 62, 112, 0.25f}, {5.6188f, 61, 112, 0.25f}, {5.8688f, 60, 95, 0.5f}, {6.3686f, 59, 106, 0.66f}},
        -1, "F", 4,
        {{"en", "Inverted Song of Time", kEnThe, "The Song of Time played backward. It slows the flow of time."},
         {"fr", "Chant du temps inversé", kFrM, "Le Chant du temps joué à l'envers. Il ralentit le cours du temps."},
         {"es", "Canción del tiempo invertida", kEsF, "La canción del tiempo tocada al revés. Ralentiza el paso del tiempo."},
         {"de", "Umgekehrte Hymne der Zeit", kDeF, "Die Hymne der Zeit, rückwärts gespielt. Sie verlangsamt den Lauf der Zeit."},
         {"it", "Canzone del tempo invertita", kItF, "La Canzone del tempo suonata al contrario. Rallenta lo scorrere del tempo."},
         {"ja", "{逆|さか}さ{時|とき}の{歌|うた}", nullptr, "時の歌を逆さに奏でたもの。時の流れをゆるやかにする。"}}});
    s.push_back({22, "DoubleTimeSong", "Song of Double Time", {A, A, D, D, F, F},
        // NA_BGM_OCA_TIME_JUMP_SEQ
        {{0.0058f, 69, 118, 0.1042f}, {0.2836f, 69, 116, 0.1736f}, {0.5602f, 62, 112, 0.0972f}, {0.7824f, 62, 106, 0.1343f},
         {1.0039f, 65, 112, 0.0989f}, {1.1938f, 65, 116, 0.1345f}, {1.3833f, 69, 124, 0.0898f}, {1.5557f, 69, 114, 0.1078f},
         {1.7279f, 62, 120, 0.0811f}, {1.8901f, 62, 105, 0.1014f}, {2.0522f, 65, 97, 0.0661f}, {2.2109f, 65, 114, 0.0794f},
         {2.3696f, 70, 125, 0.0618f}, {2.5258f, 70, 102, 0.0846f}, {2.6822f, 64, 95, 0.0777f}, {2.8444f, 64, 116, 0.0878f},
         {3.0067f, 67, 122, 0.0718f}, {3.1792f, 67, 118, 0.1078f}, {3.3521f, 73, 117, 0.0899f}, {3.5482f, 73, 117, 0.0776f},
         {3.7451f, 70, 112, 0.124f}, {3.9832f, 70, 108, 0.124f}, {4.223f, 76, 121, 0.1263f}, {4.5421f, 76, 123, 0.1596f},
         {4.8654f, 77, 120, 3.9332f}},
        4, nullptr, 4,   // (the Song of Time's sound: it opens on A and lasts 10.2 s)
        {{"en", "Song of Double Time", kEnThe, "A melody that skips time ahead to the next hour."},
         {"fr", "Chant du temps accéléré", kFrM, "Une mélodie qui fait avancer le temps jusqu'à l'heure suivante."},
         {"es", "Canción del doble tiempo", kEsF, "Una melodía que hace avanzar el tiempo hasta la hora siguiente."},
         {"de", "Hymne der doppelten Zeit", kDeF, "Eine Melodie, die die Zeit bis zur nächsten Stunde vorspult."},
         {"it", "Canzone del doppio tempo", kItF, "Una melodia che fa avanzare il tempo fino all'ora successiva."},
         {"ja", "{時|とき}の{重|かさ}ね{歌|うた}", nullptr, "時を次の時刻まで早送りするメロディ。"}}});
    return s;
}

const std::vector<Song>& All() {
    static const std::vector<Song> s = Make();
    return s;
}

const Song* ByType(uint32_t type) {
    for (const Song& s : All()) if (s.type == type) return &s;
    return nullptr;
}

}  // namespace songs
