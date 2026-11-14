// BYML (little-endian "YB", v7 as the game's .bgyml files): read into a tree, change it, write it back.
// Ported from mod_manager/byml.py, whose layout rules it follows (sorted key and string tables, containers and 64-bit
// or binary payloads written after their parent).
#pragma once
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace byml {

enum Type : uint8_t {
    kStr = 0xA0, kBin = 0xA1, kBinAlign = 0xA2, kArray = 0xC0, kDict = 0xC1, kHash32 = 0x20, kHash64 = 0x21,
    kBool = 0xD0, kI32 = 0xD1, kF32 = 0xD2, kU32 = 0xD3, kI64 = 0xD4, kU64 = 0xD5, kF64 = 0xD6, kNull = 0xFF,
};

struct Node {
    Type type = kNull;
    uint64_t bits = 0;                                     // scalars (f32/f64 as their bits), binary alignment
    std::string str;                                       // kStr; kBin / kBinAlign bytes
    std::vector<Node> items;                               // kArray
    std::vector<std::pair<std::string, Node>> dict;        // kDict (kept in key order on write)
    std::vector<std::pair<uint64_t, Node>> hashed;         // kHash32 / kHash64

    static Node U32(uint32_t v) { Node n; n.type = kU32; n.bits = v; return n; }
    static Node I32(int32_t v) { Node n; n.type = kI32; n.bits = (uint32_t)v; return n; }
    static Node Array() { Node n; n.type = kArray; return n; }
    static Node Dict() { Node n; n.type = kDict; return n; }
    Node* Get(const std::string& key);                     // a dict's value (null when missing)
    void Set(const std::string& key, Node v);              // a dict's value (added or replaced)
};

bool Parse(const std::vector<uint8_t>& data, Node& root);  // false: not a little-endian BYML
std::vector<uint8_t> Write(const Node& root, uint16_t version = 7);

}  // namespace byml
