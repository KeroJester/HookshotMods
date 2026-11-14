// MSBT message files ("MsgStdBn", as LayoutMsg/*.msbt in the Mals language archives): read the labels and texts,
// add or change texts, write the file back. Only files made of the label (LBL1) and text (TXT2) sections are
// handled (the game's LayoutMsg files); others are refused. Texts are kept as raw bytes in the file's encoding
// (this game: UTF-8, header byte 0x0c = 0), including tags (0x0E, u16 group, u16 tag, u16 size, params).
#pragma once
#include "gamefiles.h"
#include <string>
#include <utility>
#include <vector>

namespace msbt {

struct File {
    std::vector<uint8_t> header;                       // the 0x20-byte header (copied)
    uint32_t buckets = 0;                              // LBL1 hash buckets
    std::vector<std::pair<std::string, uint32_t>> labels;   // label -> text index
    std::vector<gamefiles::Bytes> texts;               // TXT2 entries (with their terminating 0)
    bool Parse(const gamefiles::Bytes& data);
    gamefiles::Bytes Write() const;
    const gamefiles::Bytes* Text(const std::string& label) const;
    void Set(const std::string& label, const gamefiles::Bytes& text);   // added, or replaced
};

// The leading tag of a text (0x0E ...), empty when it does not start with one
gamefiles::Bytes LeadingTag(const gamefiles::Bytes& text);

}  // namespace msbt
