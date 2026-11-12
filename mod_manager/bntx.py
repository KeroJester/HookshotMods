#!/usr/bin/env python3
"""BNTX (Switch texture container) reader plus in-place texture writer.

Only what Goron Link needs:
- parse the first BRTI (texture info): format, size, mip count, block height, mip data offsets
- Switch 2 block-linear (de)swizzle of compressed blocks (GOB = 64 bytes x 8 rows; row-bit order per Hookshot)
- BC4 decode (to verify the swizzle on a real texture)
- ASTC "void-extent" blocks (a whole block of one colour) so a palette can be painted into an ASTC
  texture without an ASTC encoder

BRTI layout (BNTX 4.1): +0x10 u8 flags, u8 dim, u16 tile mode, u16 swizzle, u16 mips, u32 samples,
u32 format, u32 access, i32 width, i32 height, i32 depth, i32 array, i32 layout (low 3 bits =
block height log2), ... +0x50 u32 image size, +0x54 u32 alignment, +0x70 u64 mip offset table.
"""

import struct
from typing import List, Tuple

import numpy as np

# format high byte -> (block w, block h, bytes per block)
FORMATS = {
    0x1A: (4, 4, 8),    # BC1
    0x1B: (4, 4, 16),   # BC2
    0x1C: (4, 4, 16),   # BC3
    0x1D: (4, 4, 8),    # BC4
    0x1E: (4, 4, 16),   # BC5
    0x20: (4, 4, 16),   # BC7
    0x2D: (4, 4, 16), 0x2E: (5, 4, 16), 0x2F: (5, 5, 16), 0x30: (6, 5, 16), 0x31: (6, 6, 16),
    0x32: (8, 5, 16), 0x33: (8, 6, 16), 0x34: (8, 8, 16), 0x35: (10, 5, 16), 0x36: (10, 6, 16),
    0x37: (10, 8, 16), 0x38: (10, 10, 16), 0x39: (12, 10, 16), 0x3A: (12, 12, 16),   # ASTC
}
ASTC = range(0x2D, 0x3B)


def _div(a, b):
    return (a + b - 1) // b


class Texture:
    def __init__(self, data: bytes):
        self.d = bytearray(data)
        assert self.d[:4] == b"BNTX"
        self.brti = self.d.find(b"BRTI")
        o = self.brti
        (self.flags, self.dim, self.tile, self.swz, self.mips, self.samples, self.format, self.access,
         self.width, self.height, self.depth, self.array, layout) = struct.unpack_from("<BBHHHIIIiiiii", self.d, o + 0x10)
        self.bh_log2 = layout & 7
        self.image_size = struct.unpack_from("<I", self.d, o + 0x50)[0]
        ptrs = struct.unpack_from("<Q", self.d, o + 0x70)[0]
        self.mip_offsets = [struct.unpack_from("<Q", self.d, ptrs + 8 * i)[0] for i in range(self.mips)]
        self.bw, self.bhh, self.bpb = FORMATS[self.format >> 8]
        assert self.tile == 0 and self.depth == 1 and self.array == 1, "only plain 2D block-linear textures"

    def mip_blocks(self, level) -> Tuple[int, int]:
        w = max(1, self.width >> level); h = max(1, self.height >> level)
        return _div(w, self.bw), _div(h, self.bhh)

    def mip_block_height(self, level) -> int:
        """GOBs per block for a mip level: the base block height, shrunk while the mip is small."""
        bh = 1 << self.bh_log2
        _, hb = self.mip_blocks(level)
        while bh > 1 and _div(hb, 8) <= bh // 2:
            bh //= 2
        return bh

    def _addresses(self, level) -> np.ndarray:
        """Byte offset (within the mip) of every block, as an [hb, wb] array (block-linear swizzle)."""
        wb, hb = self.mip_blocks(level)
        bpb = self.bpb
        bh = self.mip_block_height(level)
        x = np.arange(wb) * bpb                       # byte x of each block
        y = np.arange(hb)
        X, Y = np.meshgrid(x, y)
        gobs_per_row = _div(wb * bpb, 64)
        # Switch 2 GOB layout (Hookshot gpu/vk/vk_texture.cpp texture_read_texels; differs from Switch 1 in the row bits)
        gob_in = (((X & 63) >> 5) << 8) | (((Y & 7) >> 2) << 7) | (((X & 31) >> 4) << 6) | \
                 (((Y >> 1) & 1) << 5) | ((Y & 1) << 4) | (X & 15)
        block = (Y // (8 * bh)) * gobs_per_row * bh + (X // 64) * bh + (Y % (8 * bh)) // 8
        return block * 512 + gob_in

    def read_blocks(self, level) -> np.ndarray:
        """[hb, wb, bpb] uint8 linear block array for a mip."""
        wb, hb = self.mip_blocks(level)
        addr = self._addresses(level) + self.mip_offsets[level]
        out = np.empty((hb, wb, self.bpb), np.uint8)
        buf = np.frombuffer(bytes(self.d), np.uint8)
        for k in range(self.bpb):
            out[:, :, k] = buf[addr + k]
        return out

    def write_blocks(self, level, blocks: np.ndarray):
        wb, hb = self.mip_blocks(level)
        assert blocks.shape == (hb, wb, self.bpb)
        addr = self._addresses(level) + self.mip_offsets[level]
        buf = np.frombuffer(self.d, np.uint8)            # writable view on the bytearray
        for k in range(self.bpb):
            buf[addr + k] = blocks[:, :, k]

    def to_bytes(self) -> bytes:
        return bytes(self.d)


def decode_bc4(blocks: np.ndarray) -> np.ndarray:
    """[hb, wb, 8] BC4 blocks -> [hb*4, wb*4] uint8 image."""
    hb, wb, _ = blocks.shape
    img = np.zeros((hb * 4, wb * 4), np.uint8)
    for by in range(hb):
        for bx in range(wb):
            b = blocks[by, bx]
            r0, r1 = int(b[0]), int(b[1])
            pal = [r0, r1]
            if r0 > r1:
                pal += [((6 - i) * r0 + (1 + i) * r1) // 7 for i in range(6)]
            else:
                pal += [((4 - i) * r0 + (1 + i) * r1) // 5 for i in range(4)] + [0, 255]
            bits = int.from_bytes(bytes(b[2:8]), "little")
            for i in range(16):
                img[by * 4 + i // 4, bx * 4 + i % 4] = pal[(bits >> (3 * i)) & 7]
    return img


def astc_void_extent(r, g, b, a=255) -> bytes:
    """One ASTC LDR void-extent block: the whole block is a single RGBA8 colour."""
    lo = 0xFFFFFFFFFFFFFDFC          # block mode 0x1FC, LDR, reserved bits 11, no extent coordinates
    c = [int(v) & 0xFF for v in (r, g, b, a)]
    hi = sum(((v << 8) | v) << (16 * i) for i, v in enumerate(c))
    return struct.pack("<QQ", lo, hi)


def paint_astc_cells(tex: Texture, cell_colors: np.ndarray):
    """Fill an ASTC texture with a grid of solid colour cells.
    cell_colors: [ny, nx, 4] uint8; the base mip is split evenly into ny x nx cells (each must be a whole
    number of blocks). Every mip gets void-extent blocks coloured by the cell(s) they cover."""
    assert (tex.format >> 8) in ASTC
    ny, nx, _ = cell_colors.shape
    wb0, hb0 = tex.mip_blocks(0)
    assert wb0 % nx == 0 and hb0 % ny == 0, "cells must be whole blocks on the base mip"
    cw, ch = wb0 // nx, hb0 // ny
    col = cell_colors.astype(np.float64)
    for level in range(tex.mips):
        wb, hb = tex.mip_blocks(level)
        blocks = np.empty((hb, wb, 16), np.uint8)
        scale = 1 << level
        for by in range(hb):
            y0, y1 = (by * scale) // ch, min(ny - 1, ((by + 1) * scale - 1) // ch)
            for bx in range(wb):
                x0, x1 = (bx * scale) // cw, min(nx - 1, ((bx + 1) * scale - 1) // cw)
                c = col[min(y0, ny - 1):y1 + 1, min(x0, nx - 1):x1 + 1].reshape(-1, 4).mean(0)
                blocks[by, bx] = np.frombuffer(astc_void_extent(*np.round(c)), np.uint8)
        tex.write_blocks(level, blocks)


# ---- BC1 -------------------------------------------------------------------------------------------
def _to565(c):
    c = np.clip(np.round(c), 0, 255).astype(np.int64)
    return (c[..., 0] >> 3) << 11 | (c[..., 1] >> 2) << 5 | (c[..., 2] >> 3)


def _from565(v):
    r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float64)


def encode_bc1(img: np.ndarray) -> np.ndarray:
    """[H, W, 3|4] uint8 (H, W multiples of 4) -> [H/4, W/4, 8] BC1 blocks (opaque 4-colour mode)."""
    h, w = img.shape[:2]
    px = img[..., :3].astype(np.float64).reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h // 4, w // 4, 16, 3)
    mean = px.mean(2, keepdims=True)
    cen = px - mean
    cov = np.einsum("abki,abkj->abij", cen, cen)
    axis = np.ones(cov.shape[:2] + (3,))
    for _ in range(8):                                    # power iteration for the principal axis
        axis = np.einsum("abij,abj->abi", cov, axis)
        axis /= np.linalg.norm(axis, axis=-1, keepdims=True) + 1e-9
    proj = np.einsum("abki,abi->abk", cen, axis)
    e0 = mean[:, :, 0] + axis * proj.max(2)[..., None]
    e1 = mean[:, :, 0] + axis * proj.min(2)[..., None]
    c0, c1 = _to565(e0), _to565(e1)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 2)       # [hb, wb, 4, 3]
    dist = ((px[:, :, :, None, :] - pal[:, :, None, :, :]) ** 2).sum(-1)    # [hb, wb, 16, 4]
    idx = dist.argmin(-1)
    idx = np.where((c0 == c1)[..., None], 0, idx)
    if img.shape[2] == 4:                                 # punch-through: blocks with transparent texels use
        al = img[..., 3].reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(h // 4, w // 4, 16)
        tr = al < 128                                     # 3-colour mode (c0 <= c1), index 3 = transparent
        blk = tr.any(-1)
        if blk.any():
            c0b, c1b = np.minimum(c0, c1), np.maximum(c0, c1)
            q0, q1 = _from565(c0b), _from565(c1b)
            pal3 = np.stack([q0, q1, (q0 + q1) / 2], 2)
            d3 = ((px[:, :, :, None, :] - pal3[:, :, None, :, :]) ** 2).sum(-1)
            idx3 = np.where(tr, 3, d3.argmin(-1))
            c0 = np.where(blk, c0b, c0); c1 = np.where(blk, c1b, c1)
            idx = np.where(blk[..., None], idx3, idx)
    bits = (idx.astype(np.int64) << (2 * np.arange(16))).sum(-1)
    out = np.zeros(c0.shape + (8,), np.uint8)
    out[..., 0] = c0 & 0xFF; out[..., 1] = c0 >> 8
    out[..., 2] = c1 & 0xFF; out[..., 3] = c1 >> 8
    for k in range(4):
        out[..., 4 + k] = (bits >> (8 * k)) & 0xFF
    return out


def retype(tex: Texture, fmt: int, width: int, height: int, mips: int, mip_offsets, image_size: int,
           bh_log2: int = None):
    """Rewrite a texture's BRTI in place to another format/size whose data fits in the existing region.
    mip_offsets must follow the GPU's own block-linear mip layout (copy them from a real texture)."""
    o = tex.brti
    data_end = tex.mip_offsets[0] + tex.image_size
    assert mip_offsets[0] == tex.mip_offsets[0] and mip_offsets[0] + image_size <= data_end, "new data must fit"
    assert mips <= tex.mips, "mip table has no room for more levels"
    struct.pack_into("<H", tex.d, o + 0x16, mips)
    struct.pack_into("<I", tex.d, o + 0x1C, fmt)
    struct.pack_into("<ii", tex.d, o + 0x24, width, height)
    if bh_log2 is not None:
        layout = struct.unpack_from("<i", tex.d, o + 0x34)[0]
        struct.pack_into("<i", tex.d, o + 0x34, (layout & ~7) | bh_log2)
    struct.pack_into("<I", tex.d, o + 0x50, image_size)
    ptrs = struct.unpack_from("<Q", tex.d, o + 0x70)[0]
    for i, off in enumerate(mip_offsets):
        struct.pack_into("<Q", tex.d, ptrs + 8 * i, off)
    tex.__init__(bytes(tex.d))      # re-parse with the new fields


def mip_chain(img: np.ndarray, levels: int):
    out = [img]
    for _ in range(levels - 1):
        a = out[-1].astype(np.float64)
        h, w = a.shape[:2]
        if h >= 2 and w >= 2:
            a = a[:h // 2 * 2, :w // 2 * 2].reshape(h // 2, 2, w // 2, 2, -1).mean((1, 3))
        out.append(np.round(a).astype(np.uint8))
    return out


def decode_bc1(blocks: np.ndarray) -> np.ndarray:
    """[hb, wb, 8] BC1 blocks -> [hb*4, wb*4, 3] uint8 (alpha ignored)."""
    hb, wb, _ = blocks.shape
    b = blocks.astype(np.int64)
    c0 = b[..., 0] | b[..., 1] << 8
    c1 = b[..., 2] | b[..., 3] << 8
    p0, p1 = _from565(c0), _from565(c1)
    four = (c0 > c1)[..., None]
    p2 = np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(four, (p0 + 2 * p1) / 3, 0)
    pal = np.stack([p0, p1, p2, p3], 2)                       # [hb, wb, 4, 3]
    bits = b[..., 4] | b[..., 5] << 8 | b[..., 6] << 16 | b[..., 7] << 24
    idx = (bits[..., None] >> (2 * np.arange(16))) & 3        # [hb, wb, 16]
    px = np.take_along_axis(pal, idx[..., None].repeat(3, -1), 2)  # [hb, wb, 16, 3]
    return px.reshape(hb, wb, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(hb * 4, wb * 4, 3).astype(np.uint8)
