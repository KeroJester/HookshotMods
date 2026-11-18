#!/usr/bin/env python3
"""RESTBL (System/Resource/ResourceSizeTable.*.rsizetable.zs) reader/writer.

The table maps a RomFS path (without the .zs suffix) to the buffer size the engine
reserves when loading that file. If a modded file decompresses to more than its entry,
the game can fail to load it (crash / black screen). New files need entries too.

Entries are keyed by CRC32 of the path; paths whose CRC collides are stored by name.

Size formulas measured on this game's vanilla table (value - round_up(size, 32)):
  .pack (SARC)          +312   (exact for every sampled pack)
  .ainb                 +376
  .bntx/.extm/.byml...  +264
  .bphsh                +368
  .bgyml                ~+5000..26000 (depends on contents)  -> estimate generously
  .bfres                ~+5000..660000 (depends on contents) -> estimate generously
"""

import struct
import zlib
from typing import Dict, Optional

import zstandard as zstd

FIXED_OVERHEAD = {
    "pack": 312, "ainb": 376, "baev": 272, "bfevfl": 272, "bars": 584, "bntx": 264,
    "extm": 264, "byml": 264, "hght": 264, "mate": 264, "flnt": 264, "bphsh": 368,
    "bphnm": 304, "bphcl": 264, "bpvsts": 264, "bkprmb": 264, "wbr": 264,
}


def _round32(n: int) -> int:
    return (n + 31) & ~31


def estimate_size(path: str, decompressed_size: int, vanilla_entry: Optional[int] = None,
                  vanilla_size: Optional[int] = None) -> int:
    """Return an RSTB value for a file of the given decompressed size.

    If the vanilla file exists, keep its measured overhead (scaled for growth).
    Otherwise use the fixed per-type overhead, or a generous estimate for parser-heavy types.
    """
    p = path[:-3] if path.endswith(".zs") else path
    ext = p.rsplit(".", 1)[-1].lower()
    if ext in FIXED_OVERHEAD:
        return _round32(decompressed_size) + FIXED_OVERHEAD[ext]
    if vanilla_entry is not None and vanilla_size:
        overhead = vanilla_entry - _round32(vanilla_size)
        scale = max(1.0, decompressed_size / vanilla_size)
        return _round32(decompressed_size) + int(overhead * scale) + 0x1000
    if ext == "bgyml":
        return _round32(decompressed_size) * 4 + 0x8000
    if ext == "bfres":
        return _round32(decompressed_size) * 2 + 0x100000
    return _round32(decompressed_size) * 2 + 0x1000


class ResourceSizeTable:
    def __init__(self, data: bytes):
        if data[:4] == b"\x28\xb5\x2f\xfd":
            data = zstd.ZstdDecompressor().decompress(data, max_output_size=1 << 28)
        magic, ver, slen, ncrc, nname = struct.unpack_from("<6sIIII", data, 0)
        if magic != b"RESTBL":
            raise ValueError("Not a RESTBL file")
        self.version = ver
        self.string_size = slen
        self.crc: Dict[int, int] = {}
        self.names: Dict[str, int] = {}
        off = 22
        for _ in range(ncrc):
            c, s = struct.unpack_from("<II", data, off)
            self.crc[c] = s
            off += 8
        for _ in range(nname):
            n = data[off:off + slen].split(b"\0")[0].decode("utf-8")
            self.names[n] = struct.unpack_from("<I", data, off + slen)[0]
            off += slen + 4

    @classmethod
    def load(cls, path: str) -> "ResourceSizeTable":
        with open(path, "rb") as f:
            return cls(f.read())

    @staticmethod
    def _key(path: str) -> str:
        return path[:-3] if path.endswith(".zs") else path

    def get(self, path: str) -> Optional[int]:
        k = self._key(path)
        if k in self.names:
            return self.names[k]
        return self.crc.get(zlib.crc32(k.encode("utf-8")))

    def set(self, path: str, size: int) -> None:
        k = self._key(path)
        if k in self.names:
            self.names[k] = size
        else:
            self.crc[zlib.crc32(k.encode("utf-8"))] = size

    def to_bytes(self) -> bytes:
        out = bytearray(struct.pack("<6sIIII", b"RESTBL", self.version, self.string_size,
                                    len(self.crc), len(self.names)))
        for c in sorted(self.crc):
            out += struct.pack("<II", c, self.crc[c])
        for n in sorted(self.names, key=lambda s: s.encode("utf-8")):
            b = n.encode("utf-8")
            out += b + b"\0" * (self.string_size - len(b)) + struct.pack("<I", self.names[n])
        return bytes(out)

    def to_zs(self, level: int = 16) -> bytes:
        return zstd.ZstdCompressor(level=level).compress(self.to_bytes())
