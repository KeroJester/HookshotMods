#!/usr/bin/env python3
"""Majora's Mask 3D (Grezzo) asset readers: LzS decompression and GAR archives.

LzS:  "LzS\\x01", u32 ?, u32 decompressed size, u32 compressed size, then Okumura-style LZSS
      (flag byte LSB-first, 1 = literal; ref = 2 bytes: offset 12 bits (b0 | (b1 & 0xF0) << 4),
      length (b1 & 0x0F) + 3; 4096-byte ring buffer starting at 0xFEE, zero-filled).
GAR v2 ("GAR\\x02", MM3D): u32 file size, u16 type count, u16 file count, u32 types offset,
      u32 file-info offset, u32 data-offsets offset, then a codename. Type entry (0x10):
      u32 count, u32 file-index list offset, u32 type-name offset, u32 ?. File info (0x0C): u32 size,
      u32 name offset, u32 full-name offset. Data offsets: u32 per file.
"""

import os
import struct
from typing import Dict

ROMFS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "mods", "_MajoraAssets", "extracted", "mm3d_romfs")


def lzs_decompress(data: bytes) -> bytes:
    if data[:4] != b"LzS\x01":
        return data
    size = struct.unpack_from("<I", data, 8)[0]
    src = memoryview(data)[16:]
    out = bytearray()
    ring = bytearray(4096)
    r = 0xFEE
    i = 0
    n = len(src)
    while len(out) < size and i < n:
        flags = src[i]; i += 1
        for bit in range(8):
            if len(out) >= size or i >= n:
                break
            if flags >> bit & 1:
                c = src[i]; i += 1
                out.append(c); ring[r] = c; r = (r + 1) & 0xFFF
            else:
                b0, b1 = src[i], src[i + 1]; i += 2
                off = b0 | ((b1 & 0xF0) << 4)
                ln = (b1 & 0x0F) + 3
                for k in range(ln):
                    c = ring[(off + k) & 0xFFF]
                    out.append(c); ring[r] = c; r = (r + 1) & 0xFFF
    return bytes(out)


def _cstr(d, o):
    e = d.index(b"\0", o)
    return d[o:e].decode("ascii", "replace")


def read_gar(data: bytes) -> Dict[str, bytes]:
    """{file name: bytes} for a GAR (v2) archive (decompresses LzS first)."""
    d = lzs_decompress(data)
    if d[:3] != b"GAR":
        raise ValueError("not a GAR archive")
    ver = d[3]
    _fsize, ntypes, nfiles, types_off, info_off, data_off = struct.unpack_from("<IHHIII", d, 4)
    if ver != 2:
        raise NotImplementedError(f"GAR version {ver}")
    names = {}
    for t in range(ntypes):
        cnt, idx_off, tname_off, _u = struct.unpack_from("<IIII", d, types_off + 0x10 * t)
        tname = _cstr(d, tname_off) if tname_off else "?"
        if idx_off == 0xFFFFFFFF or idx_off == 0:
            continue
        for k in range(cnt):
            fi = struct.unpack_from("<I", d, idx_off + 4 * k)[0]
            names[fi] = tname
    out = {}
    for fi in range(nfiles):
        size, name_off, full_off = struct.unpack_from("<III", d, info_off + 0x0C * fi)
        off = struct.unpack_from("<I", d, data_off + 4 * fi)[0]
        name = _cstr(d, full_off) if full_off else (_cstr(d, name_off) + "." + names.get(fi, "bin"))
        out[name] = d[off:off + size]
    return out


def actor_archive(name: str) -> Dict[str, bytes]:
    with open(os.path.join(ROMFS, "actors", name + ".gar.lzs"), "rb") as f:
        return read_gar(f.read())


if __name__ == "__main__":
    import sys
    for nm in sys.argv[1:]:
        files = actor_archive(nm)
        print(f"== {nm}: {len(files)} files")
        for k, v in list(files.items())[:60]:
            print(f"   {k:50s} {len(v):8d}  {v[:4]}")
