"""One-texture BNTX files (Tex/<name>.bntx.zs), made from a small game texture used as a template (RESEARCH_NOTES.md
§80g). The template's header, string table, dictionary and BRTI block are kept; the name (same length), format, size,
mip chain and data are replaced, and the relocation table is rebuilt.

BNTX layout (as the game's Tex files): header "BNTX" (+0x18 _RLT offset, +0x1c file size, +0x28 texture array
pointer, +0x30 BRTD pointer, +0x38 _DIC pointer), _STR, _DIC, BRTI (+0x10 flags, dimension, tile mode, swizzle,
u16 mips, u32 samples, u32 format, u32 access, i32 width / height / depth / array, i32 layout (block height log2),
+0x50 image size, +0x54 alignment, +0x58 channel sources, +0x5c dimension, +0x60 name, +0x68 parent, +0x70 mip
offset table), BRTD ("BRTD", u32 0, u64 size incl. this header, data), _RLT (sections {u64 0, u32 position, u32
size, u32 first entry, u32 entries}, entries {u32 position, u16 structs, u8 pointers per struct, u8 u64s skipped
between structs}).
"""
import struct
from typing import List

import numpy as np

from . import resdic

FMT_RGBA8_SRGB = 0x0B06


def _div(a, b): return (a + b - 1) // b


def _rlt_pointers(d: bytes) -> List[int]:
    r = struct.unpack_from("<I", d, 0x18)[0]
    assert d[r:r + 4] == b"_RLT"
    nsec = struct.unpack_from("<I", d, r + 8)[0]
    e0 = r + 0x10 + 0x18 * nsec
    total = sum(struct.unpack_from("<I", d, r + 0x10 + 0x18 * i + 0x14)[0] for i in range(nsec))
    out = []
    for i in range(total):
        pos, n, k, skip = struct.unpack_from("<IHBB", d, e0 + 8 * i)
        for s in range(n):
            for j in range(k):
                out.append(pos + 8 * (s * (k + skip) + j))
    return out


def _block_height_log2(hb: int) -> int:
    bh = 0
    while bh < 4 and (8 << bh) < hb:
        bh += 1
    return bh


def _mip_layout(w, h, mips, bpb=4, bw=1, bhh=1):
    """Sizes (bytes) of each mip as block-linear, and the base block height log2."""
    hb0 = _div(h, bhh)
    bh_log2 = _block_height_log2(hb0)
    sizes = []
    for lv in range(mips):
        mw, mh = max(1, w >> lv), max(1, h >> lv)
        wb, hb = _div(mw, bw), _div(mh, bhh)
        bh = 1 << bh_log2
        while bh > 1 and _div(hb, 8) <= bh // 2:
            bh //= 2
        gobs_x = _div(wb * bpb, 64)
        sizes.append(gobs_x * 512 * _div(hb, 8 * bh) * bh)
    return sizes, bh_log2


def _swizzle(img: np.ndarray, bh_log2_base: int, level: int) -> bytes:
    """RGBA8 image [h, w, 4] -> block-linear bytes for one mip (Switch 2 GOB layout, as bntx.Texture)."""
    h, w = img.shape[:2]
    bpb = 4
    bh = 1 << bh_log2_base
    while bh > 1 and _div(h, 8) <= bh // 2:
        bh //= 2
    gobs_x = _div(w * bpb, 64)
    size = gobs_x * 512 * _div(h, 8 * bh) * bh
    out = np.zeros(size, np.uint8)
    X, Y = np.meshgrid(np.arange(w) * bpb, np.arange(h))
    gob_in = (((X & 63) >> 5) << 8) | (((Y & 7) >> 2) << 7) | (((X & 31) >> 4) << 6) | \
             (((Y >> 1) & 1) << 5) | ((Y & 1) << 4) | (X & 15)
    block = (Y // (8 * bh)) * gobs_x * bh + (X // 64) * bh + (Y % (8 * bh)) // 8
    addr = block * 512 + gob_in
    for k in range(4):
        out[addr + k] = img[:, :, k]
    return out.tobytes()


def mip_chain(img: np.ndarray, levels: int):
    out = [img]
    for _ in range(1, levels):
        a = out[-1].astype(np.float32)
        h, w = a.shape[:2]
        if h > 1: a = (a[0::2] + a[1::2][:a[0::2].shape[0]]) / 2 if h % 2 == 0 else a[0::2]
        if w > 1: a = (a[:, 0::2] + a[:, 1::2][:, :a[:, 0::2].shape[1]]) / 2 if w % 2 == 0 else a[:, 0::2]
        out.append(np.clip(a + 0.5, 0, 255).astype(np.uint8))
    return out


def build(template: bytes, name: str, img: np.ndarray) -> bytes:
    """template: a decompressed one-texture BNTX; img: RGBA8 [h, w, 4] (power-of-two sides). -> BNTX bytes"""
    d = bytearray(template)
    brti = d.find(b"BRTI")
    brtd = struct.unpack_from("<Q", d, 0x30)[0]
    assert d[brtd:brtd + 4] == b"BRTD"
    name_ptr = struct.unpack_from("<Q", d, brti + 0x60)[0]
    old_len = struct.unpack_from("<H", d, name_ptr)[0]
    assert len(name) == old_len, f"texture names must keep the template's length ({old_len})"
    d[name_ptr + 2:name_ptr + 2 + old_len] = name.encode()
    dic = struct.unpack_from("<Q", d, 0x38)[0]
    root_ptr = struct.unpack_from("<Q", d, dic + 8 + 8)[0]
    resdic.write(d, dic, [name.encode()], [name_ptr], root_ptr)
    h, w = img.shape[:2]
    mips = max(1, int(np.log2(max(w, h))) + 1)
    sizes, bh_log2 = _mip_layout(w, h, mips)
    chain = mip_chain(img, mips)
    data = bytearray()
    offs = []
    for lv, m in enumerate(chain):
        offs.append(len(data))
        data += _swizzle(m, bh_log2, lv)
        data += b"\0" * ((-len(data)) % 512)
    image_size = len(data)
    # BRTI
    struct.pack_into("<H", d, brti + 0x16, mips)
    struct.pack_into("<I", d, brti + 0x1c, FMT_RGBA8_SRGB)
    struct.pack_into("<ii", d, brti + 0x24, w, h)
    struct.pack_into("<i", d, brti + 0x34, bh_log2)
    struct.pack_into("<II", d, brti + 0x50, image_size, 0x200)
    struct.pack_into("<4B", d, brti + 0x58, 2, 3, 4, 5)
    table = struct.unpack_from("<Q", d, brti + 0x70)[0]
    assert table + 8 * mips <= brtd, "mip table does not fit"
    data_start = brtd + 0x10
    data_start_al = (data_start + 0xfff) & ~0xfff        # (the game's files start the image at 0x1000)
    for lv, o in enumerate(offs):
        struct.pack_into("<Q", d, table + 8 * lv, data_start_al + o)
    # rebuild from BRTD on
    head = bytes(d[:brtd])
    pad = data_start_al - data_start
    brtd_blk = b"BRTD" + b"\0" * 4 + struct.pack("<Q", 0x10 + pad + image_size) + b"\0" * pad + bytes(data)
    body = head + brtd_blk
    body += b"\0" * ((-len(body)) % 8)
    rlt_pos = len(body)
    ptrs = [p for p in _rlt_pointers(template) if p < brtd and not (table <= p < table + 8 * 64)]
    sec0 = sorted(set(ptrs))
    sec1 = sorted(set([0x30] + [table + 8 * lv for lv in range(mips)]))
    entries = [struct.pack("<IHBB", p, 1, 1, 0) for p in sec0 + sec1]
    rlt = bytearray(b"_RLT" + struct.pack("<II", rlt_pos, 2) + b"\0" * 4)
    rlt += struct.pack("<QIIII", 0, 0, brtd, 0, len(sec0))
    rlt += struct.pack("<QIIII", 0, brtd, len(body) - brtd, len(sec0), len(sec1))
    rlt += b"".join(entries)
    out = bytearray(body + rlt)
    struct.pack_into("<I", out, 0x18, rlt_pos)
    struct.pack_into("<I", out, 0x1c, len(out))
    return bytes(out)
