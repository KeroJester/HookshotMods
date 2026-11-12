#!/usr/bin/env python3
"""Nintendo BYML (Binary YAML) v2-v7 reader and writer (little- and big-endian).

Lossless: every scalar keeps its exact BYML type across parse -> write.

Python mapping
--------------
  0xA0 string        -> str
  0xA1 binary        -> bytes
  0xA2 binary+align  -> AlignedBinary (bytes subclass with .alignment)
  0xC0 array         -> list
  0xC1 dict          -> dict  (keys from the key table)
  0x20 hash32 dict   -> HashDict32 (dict subclass, int keys)
  0x21 hash64 dict   -> HashDict64 (dict subclass, int keys)
  0xD0 bool          -> bool
  0xD1 int32         -> int (plain)
  0xD2 float32       -> float (plain)
  0xD3 uint32        -> U32
  0xD4 int64         -> I64
  0xD5 uint64        -> U64
  0xD6 double        -> F64
  0xFF null          -> None

Note: the older version of this module had the scalar type table off by one (it decoded
floats as raw ints and re-encoded them as int32), which silently corrupted float params on
write. Files written by that version should be rebuilt.
"""

import struct
from typing import Any, Dict, List, Tuple


class BymlError(Exception):
    pass


class U32(int):
    pass


class I64(int):
    pass


class U64(int):
    pass


class F64(float):
    pass


class AlignedBinary(bytes):
    def __new__(cls, data: bytes, alignment: int = 0):
        o = super().__new__(cls, data)
        o.alignment = alignment
        return o


class HashDict32(dict):
    pass


class HashDict64(dict):
    pass


T_STR, T_BIN, T_BIN_ALIGN = 0xA0, 0xA1, 0xA2
T_ARRAY, T_DICT, T_STRTAB = 0xC0, 0xC1, 0xC2
T_HASH32, T_HASH64 = 0x20, 0x21
T_BOOL, T_I32, T_F32, T_U32, T_I64, T_U64, T_F64, T_NULL = 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xFF
CONTAINERS = (T_ARRAY, T_DICT, T_HASH32, T_HASH64)


def float_bits_to_float(v: int) -> float:
    """Helper for data produced by the old buggy parser (raw float bits stored as int)."""
    return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]


def parse_byml(data: bytes) -> Any:
    if len(data) < 16:
        raise BymlError("Data too short for BYML header")
    magic = bytes(data[:2])
    if magic == b"YB":
        e = "<"
    elif magic == b"BY":
        e = ">"
    else:
        raise BymlError(f"Invalid BYML magic: {magic!r}")
    _, ver, key_off, str_off, root_off = struct.unpack_from(e + "2sHIII", data, 0)

    def u24(off):
        b = data[off:off + 3]
        return int.from_bytes(b, "little" if e == "<" else "big")

    def u32(off):
        return struct.unpack_from(e + "I", data, off)[0]

    def strtab(off):
        if off == 0:
            return []
        if data[off] != T_STRTAB:
            raise BymlError(f"Expected string table at 0x{off:x}")
        n = u24(off + 1)
        offs = [u32(off + 4 + i * 4) for i in range(n + 1)]
        out = []
        for i in range(n):
            s = off + offs[i]
            end = data.index(b"\0", s)
            out.append(bytes(data[s:end]).decode("utf-8"))
        return out

    keys = strtab(key_off)
    strs = strtab(str_off)

    def value(t, raw, pos):
        """raw = the 4-byte inline word; pos = file offset of that word (for 64-bit types)."""
        if t == T_STR:
            return strs[raw]
        if t == T_BOOL:
            return bool(raw)
        if t == T_I32:
            return struct.unpack(e + "i", struct.pack(e + "I", raw))[0]
        if t == T_F32:
            return struct.unpack(e + "f", struct.pack(e + "I", raw))[0]
        if t == T_U32:
            return U32(raw)
        if t == T_I64:
            return I64(struct.unpack_from(e + "q", data, raw)[0])
        if t == T_U64:
            return U64(struct.unpack_from(e + "Q", data, raw)[0])
        if t == T_F64:
            return F64(struct.unpack_from(e + "d", data, raw)[0])
        if t == T_NULL:
            return None
        if t == T_BIN:
            n = u32(raw)
            return bytes(data[raw + 4:raw + 4 + n])
        if t == T_BIN_ALIGN:
            n = u32(raw)
            al = u32(raw + 4)
            return AlignedBinary(bytes(data[raw + 8:raw + 8 + n]), al)
        if t in CONTAINERS:
            return node(raw)
        raise BymlError(f"Unknown BYML node type 0x{t:x}")

    def node(off):
        t = data[off]
        n = u24(off + 1)
        if t == T_ARRAY:
            types = data[off + 4:off + 4 + n]
            vs = (off + 4 + n + 3) & ~3
            return [value(types[i], u32(vs + i * 4), vs + i * 4) for i in range(n)]
        if t == T_DICT:
            d = {}
            for i in range(n):
                p = off + 4 + i * 8
                d[keys[u24(p)]] = value(data[p + 3], u32(p + 4), p + 4)
            return d
        if t in (T_HASH32, T_HASH64):
            hs = 4 if t == T_HASH32 else 8
            ent = hs + 4
            d = HashDict32() if t == T_HASH32 else HashDict64()
            types_off = off + 4 + n * ent
            for i in range(n):
                p = off + 4 + i * ent
                h = struct.unpack_from(e + ("I" if hs == 4 else "Q"), data, p)[0]
                d[h] = value(data[types_off + i], u32(p + hs), p + hs)
            return d
        raise BymlError(f"Unexpected container type 0x{t:x} at 0x{off:x}")

    if root_off == 0:
        return None
    return node(root_off)


def _type_of(v) -> int:
    if v is None:
        return T_NULL
    if isinstance(v, bool):
        return T_BOOL
    if isinstance(v, U32):
        return T_U32
    if isinstance(v, I64):
        return T_I64
    if isinstance(v, U64):
        return T_U64
    if isinstance(v, int):
        return T_I32
    if isinstance(v, F64):
        return T_F64
    if isinstance(v, float):
        return T_F32
    if isinstance(v, str):
        return T_STR
    if isinstance(v, AlignedBinary):
        return T_BIN_ALIGN
    if isinstance(v, (bytes, bytearray)):
        return T_BIN
    if isinstance(v, HashDict32):
        return T_HASH32
    if isinstance(v, HashDict64):
        return T_HASH64
    if isinstance(v, dict):
        return T_DICT
    if isinstance(v, (list, tuple)):
        return T_ARRAY
    raise BymlError(f"Unsupported BYML value type: {type(v)}")


def write_byml(val: Any, version: int = 7, big_endian: bool = False) -> bytes:
    e = ">" if big_endian else "<"
    bo = "big" if big_endian else "little"
    keys, strs = set(), set()

    def scan(v):
        if isinstance(v, dict):
            for k, s in v.items():
                if not isinstance(v, (HashDict32, HashDict64)):
                    keys.add(k)
                scan(s)
        elif isinstance(v, (list, tuple)):
            for s in v:
                scan(s)
        elif isinstance(v, str):
            strs.add(v)

    scan(val)
    key_list = sorted(keys, key=lambda s: s.encode("utf-8"))
    str_list = sorted(strs, key=lambda s: s.encode("utf-8"))
    key_idx = {k: i for i, k in enumerate(key_list)}
    str_idx = {s: i for i, s in enumerate(str_list)}

    out = bytearray(16)

    def align(n):
        while len(out) % n:
            out.append(0)

    def write_strtab(sl):
        if not sl:
            return 0
        align(4)
        start = len(out)
        out.append(T_STRTAB)
        out.extend(len(sl).to_bytes(3, bo))
        table_pos = len(out)
        out.extend(b"\0" * 4 * (len(sl) + 1))
        for i, s in enumerate(sl):
            struct.pack_into(e + "I", out, table_pos + i * 4, len(out) - start)
            out.extend(s.encode("utf-8") + b"\0")
        struct.pack_into(e + "I", out, table_pos + len(sl) * 4, len(out) - start)
        align(4)
        return start

    key_off = write_strtab(key_list)
    str_off = write_strtab(str_list)

    # Deferred writing: containers and 64-bit/binary payloads are placed after their parent,
    # and the parent's inline word is patched once the child offset is known.
    def inline(v, t):
        """Return (word, deferred) where deferred is None or a callable producing an offset."""
        if t == T_STR:
            return str_idx[v], None
        if t == T_BOOL:
            return int(v), None
        if t == T_I32:
            return struct.unpack(e + "I", struct.pack(e + "i", v))[0], None
        if t == T_F32:
            return struct.unpack(e + "I", struct.pack(e + "f", v))[0], None
        if t == T_U32:
            return int(v) & 0xFFFFFFFF, None
        if t == T_NULL:
            return 0, None
        return 0, v

    def write_payload(v, t):
        if t in (T_I64, T_U64, T_F64):
            align(8)
            p = len(out)
            out.extend(struct.pack(e + {T_I64: "q", T_U64: "Q", T_F64: "d"}[t], v))
            return p
        if t == T_BIN:
            align(4)
            p = len(out)
            out.extend(struct.pack(e + "I", len(v)))
            out.extend(v)
            return p
        if t == T_BIN_ALIGN:
            align(4)
            p = len(out)
            out.extend(struct.pack(e + "II", len(v), getattr(v, "alignment", 0)))
            out.extend(v)
            return p
        return write_container(v, t)

    def write_container(v, t):
        align(4)
        p = len(out)
        pending = []  # (patch_pos, value, type)
        if t == T_ARRAY:
            items = list(v)
            types = [_type_of(x) for x in items]
            out.append(T_ARRAY)
            out.extend(len(items).to_bytes(3, bo))
            out.extend(bytes(types))
            align(4)
            for x, tt in zip(items, types):
                w, d = inline(x, tt)
                pending.append((len(out), d, tt))
                out.extend(struct.pack(e + "I", w))
        elif t == T_DICT:
            items = sorted(v.items(), key=lambda kv: key_idx[kv[0]])
            out.append(T_DICT)
            out.extend(len(items).to_bytes(3, bo))
            for k, x in items:
                tt = _type_of(x)
                out.extend(key_idx[k].to_bytes(3, bo))
                out.append(tt)
                w, d = inline(x, tt)
                pending.append((len(out), d, tt))
                out.extend(struct.pack(e + "I", w))
        else:
            hs = 4 if t == T_HASH32 else 8
            items = sorted(v.items())
            out.append(t)
            out.extend(len(items).to_bytes(3, bo))
            types = []
            for h, x in items:
                tt = _type_of(x)
                types.append(tt)
                out.extend(struct.pack(e + ("I" if hs == 4 else "Q"), h))
                w, d = inline(x, tt)
                pending.append((len(out), d, tt))
                out.extend(struct.pack(e + "I", w))
            out.extend(bytes(types))
            align(4)
        for pos, d, tt in pending:
            if d is not None:
                struct.pack_into(e + "I", out, pos, write_payload(d, tt))
        return p

    if val is None:
        root_off = 0
    else:
        rt = _type_of(val)
        if rt not in CONTAINERS:
            raise BymlError("BYML root must be a container")
        root_off = write_container(val, rt)

    struct.pack_into(e + "2sHIII", out, 0, b"BY" if big_endian else b"YB", version, key_off, str_off, root_off)
    return bytes(out)
