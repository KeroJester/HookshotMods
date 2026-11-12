#!/usr/bin/env python3
"""
Zstandard Dictionary & SARC Extraction/Creation Utilities for Hookshot / OoT Remastered.
Loads dictionaries directly from romfs/Pack/ZsDic.pack.zs.
"""

import os
import struct
import zstandard as zstd
from typing import Dict, List, Optional, Tuple

CHUNK_SIZE = 131072  # 128 KB per dictionary
DATA_OFFSET = 0xA0

DICT_NAMES = [
    "zs.zsdic",
    "bcett.byml.zsdic",
    "pack.zsdic",
    "bkprb.zsdic",
]

_CACHED_DICTS: Optional[Dict[str, bytes]] = None
_CACHED_ZSTD_DICTS: Optional[Dict[str, zstd.ZstdCompressionDict]] = None


def load_dictionaries(romfs_path: str) -> Dict[str, bytes]:
    global _CACHED_DICTS, _CACHED_ZSTD_DICTS
    if _CACHED_DICTS is not None:
        return _CACHED_DICTS

    zsdic_path = os.path.join(romfs_path, "Pack", "ZsDic.pack.zs")
    if not os.path.exists(zsdic_path):
        raise FileNotFoundError(f"ZsDic archive not found at: {zsdic_path}")

    with open(zsdic_path, "rb") as f:
        sarc = zstd.ZstdDecompressor().decompress(f.read())

    dicts = {}
    zstd_dicts = {}
    for i, name in enumerate(DICT_NAMES):
        start = DATA_OFFSET + i * CHUNK_SIZE
        data = sarc[start : start + CHUNK_SIZE]
        dicts[name] = data
        zstd_dicts[name] = zstd.ZstdCompressionDict(data)

    _CACHED_DICTS = dicts
    _CACHED_ZSTD_DICTS = zstd_dicts
    return dicts


def get_dict_name_for_path(filename: str, romfs_path: Optional[str] = None) -> Optional[str]:
    if romfs_path:
        rel_path = filename
        if rel_path.startswith("romfs/"):
            rel_path = rel_path[6:]
        orig_file = os.path.join(romfs_path, rel_path)
        if os.path.exists(orig_file):
            try:
                with open(orig_file, "rb") as f:
                    b = f.read(5)
                fhd = b[4]
                if (fhd & 3) == 0:
                    return None  # Original file in vanilla romfs did NOT use a dictionary!
            except Exception:
                pass

    lower = filename.lower()
    if "rsdb" in lower or "rsizetable" in lower or "system/resource" in lower or ".rstbl." in lower:
        return None
    if lower.endswith(".bcett.byml.zs") or lower.endswith(".bcett.byml"):
        return "bcett.byml.zsdic"
    if lower.endswith(".pack.zs") or lower.endswith(".pack"):
        return "pack.zsdic"
    if lower.endswith(".bkprb.zs") or lower.endswith(".bkprb"):
        return "bkprb.zsdic"
    return "zs.zsdic"


def decompress_zs(data: bytes, filename: str, romfs_path: str) -> bytes:
    """Decompress a .zs file using the appropriate dictionary."""
    dict_name = get_dict_name_for_path(filename, romfs_path)
    if dict_name is None:
        try:
            return zstd.ZstdDecompressor().decompress(data)
        except Exception:
            pass

    load_dictionaries(romfs_path)
    dict_obj = _CACHED_ZSTD_DICTS.get(dict_name) if dict_name else None

    # First try the target dictionary
    if dict_obj:
        try:
            dctx = zstd.ZstdDecompressor(dict_data=dict_obj)
            return dctx.decompress(data)
        except Exception:
            pass

    # Try standard uncompressed (no dictionary)
    try:
        return zstd.ZstdDecompressor().decompress(data)
    except Exception:
        pass

    # Fallback: try all dictionaries
    for name, candidate in _CACHED_ZSTD_DICTS.items():
        if name == dict_name:
            continue
        try:
            dctx = zstd.ZstdDecompressor(dict_data=candidate)
            return dctx.decompress(data)
        except Exception:
            continue

    raise ValueError(f"Failed to decompress {filename} with any dictionary")


def compress_zs(data: bytes, filename: str, romfs_path: str, level: int = 16) -> bytes:
    """Compress data into .zs format using the appropriate dictionary."""
    dict_name = get_dict_name_for_path(filename, romfs_path)
    if dict_name is None:
        return zstd.ZstdCompressor(level=level).compress(data)

    load_dictionaries(romfs_path)
    dict_obj = _CACHED_ZSTD_DICTS.get(dict_name)

    if dict_obj:
        cctx = zstd.ZstdCompressor(level=level, dict_data=dict_obj)
    else:
        cctx = zstd.ZstdCompressor(level=level)

    return cctx.compress(data)


def sarc_hash(name: str) -> int:
    h = 0
    for char in name:
        h = (h * 0x65 + ord(char)) & 0xFFFFFFFF
    return h


def extract_sarc(sarc_bytes: bytes, out_dir: str) -> int:
    """Unpack a SARC archive into a directory."""
    if sarc_bytes[:4] != b"SARC":
        raise ValueError("Not a SARC archive")

    hdr_len, bom = struct.unpack("<HH", sarc_bytes[4:8])
    data_offset = struct.unpack("<I", sarc_bytes[0x0C:0x10])[0]

    sfat_magic, sfat_hdr_len, node_cnt, hash_mult = struct.unpack("<4sHHI", sarc_bytes[0x14:0x20])
    if sfat_magic != b"SFAT":
        raise ValueError("Invalid SFAT header")

    nodes = []
    for i in range(node_cnt):
        name_hash, flags_name_off, data_start, data_end = struct.unpack("<IIII", sarc_bytes[0x20 + i*16 : 0x30 + i*16])
        name_off = (flags_name_off & 0x00FFFFFF) * 4
        nodes.append((name_off, data_start, data_end))

    fnt_offset = 0x20 + node_cnt * 16
    sfnt_magic, sfnt_hdr_len = struct.unpack("<4sH", sarc_bytes[fnt_offset:fnt_offset+6])
    str_table = fnt_offset + 8

    os.makedirs(out_dir, exist_ok=True)
    count = 0
    for name_off, start, end in nodes:
        pos = str_table + name_off
        null_pos = sarc_bytes.find(b"\x00", pos)
        rel_name = sarc_bytes[pos:null_pos].decode("utf-8", errors="replace")
        file_data = sarc_bytes[data_offset + start : data_offset + end]

        dest_path = os.path.join(out_dir, rel_name)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(file_data)
        count += 1

    return count


def unpack_sarc_to_dict(sarc_bytes: bytes) -> Dict[str, bytes]:
    """Unpack a SARC archive into a memory dictionary {rel_path: bytes}."""
    if sarc_bytes[:4] != b"SARC":
        raise ValueError("Not a SARC archive")

    data_offset = struct.unpack("<I", sarc_bytes[0x0C:0x10])[0]
    sfat_magic, sfat_hdr_len, node_cnt, hash_mult = struct.unpack("<4sHHI", sarc_bytes[0x14:0x20])
    nodes = []
    for i in range(node_cnt):
        name_hash, flags_name_off, data_start, data_end = struct.unpack("<IIII", sarc_bytes[0x20 + i*16 : 0x30 + i*16])
        name_off = (flags_name_off & 0x00FFFFFF) * 4
        nodes.append((name_off, data_start, data_end))

    fnt_offset = 0x20 + node_cnt * 16
    str_table = fnt_offset + 8

    res = {}
    for name_off, start, end in nodes:
        pos = str_table + name_off
        null_pos = sarc_bytes.find(b"\x00", pos)
        rel_name = sarc_bytes[pos:null_pos].decode("utf-8", errors="replace")
        file_data = sarc_bytes[data_offset + start : data_offset + end]
        res[rel_name] = file_data
    return res


SARC_DEFAULT_ALIGN = 8
# Larger alignments some file types need when stored inside a SARC (GPU resources).
SARC_EXT_ALIGN = {".bfres": 0x1000, ".bntx": 0x1000, ".bnsh": 0x1000}


def _sarc_align_for(name: str) -> int:
    lower = name.lower()
    for ext, a in SARC_EXT_ALIGN.items():
        if lower.endswith(ext):
            return a
    return SARC_DEFAULT_ALIGN


def create_sarc(files: Dict[str, bytes]) -> bytes:
    """Pack a dictionary of {rel_path: bytes} into a SARC archive.

    Layout matches the vanilla Pack/Actor archives byte-for-byte: the data offset and every
    file's data are 8-byte aligned, and nodes are sorted by name hash. (An earlier version
    aligned only to 4 bytes and padded the data offset to 0x80, so repacked packs differed
    from vanilla.)
    """
    sorted_files = sorted(files.items(), key=lambda item: sarc_hash(item[0]))
    node_count = len(sorted_files)

    str_table = bytearray()
    name_offsets = []
    for name, _ in sorted_files:
        name_offsets.append(len(str_table))
        str_table.extend(name.encode("utf-8") + b"\x00")
        while len(str_table) % 4 != 0:
            str_table.append(0)

    sfat_len = 0x0C + node_count * 16
    sfnt_len = 8 + len(str_table)
    header_len = 0x14
    max_align = max([_sarc_align_for(n) for n, _ in sorted_files] or [SARC_DEFAULT_ALIGN])
    data_offset = header_len + sfat_len + sfnt_len
    data_offset = (data_offset + max_align - 1) & ~(max_align - 1)

    sfat_nodes = bytearray()
    file_data = bytearray()
    for i, (name, content) in enumerate(sorted_files):
        h = sarc_hash(name)
        name_off_words = name_offsets[i] // 4
        flags_name = (0x01000000) | (name_off_words & 0x00FFFFFF)
        align = _sarc_align_for(name)
        while (data_offset + len(file_data)) % align != 0:
            file_data.append(0)
        start = len(file_data)
        file_data.extend(content)
        end = len(file_data)
        sfat_nodes.extend(struct.pack("<IIII", h, flags_name, start, end))

    total_size = data_offset + len(file_data)
    sarc_hdr = struct.pack("<4sHHIII", b"SARC", 0x14, 0xFEFF, total_size, data_offset, 0x0100)
    sfat_hdr = struct.pack("<4sHHI", b"SFAT", 0x0C, node_count, 0x65)
    sfnt_hdr = struct.pack("<4sHH", b"SFNT", 0x08, 0)

    out = bytearray(sarc_hdr)
    out.extend(sfat_hdr)
    out.extend(sfat_nodes)
    out.extend(sfnt_hdr)
    out.extend(str_table)
    while len(out) < data_offset:
        out.append(0)
    out.extend(file_data)
    return bytes(out)
