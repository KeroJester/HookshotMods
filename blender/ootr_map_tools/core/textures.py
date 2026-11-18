"""Game textures (Tex/<name>.bntx.zs) -> RGBA images, cached as PNG (no bpy). Block-linear un-swizzling from
vendor/bntx.py; ASTC / BC decoding by texture2ddecoder (bundled wheel, MIT). Normal maps (two channels) get their Z
reconstructed so Blender's Normal Map node can use them."""
import os
import struct
import zlib

import numpy as np

from ..vendor import bntx

try:
    import texture2ddecoder as t2d
except ImportError:            # (not installed: textures are skipped)
    t2d = None


def cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    d = os.path.join(base, 'ootr_map_tools', 'textures')
    os.makedirs(d, exist_ok=True)
    return d


ASTC = {0x2d: (4, 4), 0x2e: (5, 4), 0x2f: (5, 5), 0x30: (6, 5), 0x31: (6, 6), 0x32: (8, 5), 0x33: (8, 6), 0x34: (8, 8),
        0x35: (10, 5), 0x36: (10, 6), 0x37: (10, 8), 0x38: (10, 10), 0x39: (12, 10), 0x3a: (12, 12)}


class _Tex(bntx.Texture):
    """bntx.Texture that also opens array / cube textures (layer 0 is read: layers follow each other)."""
    def __init__(self, data):
        self.d = bytearray(data)
        o = self.brti = self.d.find(b'BRTI')
        (self.flags, self.dim, self.tile, self.swz, self.mips, self.samples, self.format, self.access,
         self.width, self.height, self.depth, self.array, layout) = struct.unpack_from('<BBHHHIIIiiiii', self.d, o + 0x10)
        self.bh_log2 = layout & 7
        self.image_size = struct.unpack_from('<I', self.d, o + 0x50)[0]
        ptrs = struct.unpack_from('<Q', self.d, o + 0x70)[0]
        self.mip_offsets = [struct.unpack_from('<Q', self.d, ptrs + 8 * i)[0] for i in range(self.mips)]
        self.bw, self.bhh, self.bpb = {0x1f: (4, 4, 16)}.get(self.format >> 8) or bntx.FORMATS[self.format >> 8]


def decode_rgba(data: bytes) -> np.ndarray:
    """A BNTX (one texture, mip 0, layer 0) -> RGBA uint8 [h, w, 4], top row first."""
    t = _Tex(data)
    fmt = t.format >> 8
    w, h = t.width, t.height
    blocks = t.read_blocks(0)                      # [hb, wb, bytes per block], linear block order
    raw = blocks.tobytes()
    if fmt == 0x0b:
        img = np.frombuffer(raw, np.uint8).reshape(blocks.shape[0], blocks.shape[1], 4)[:h, :w]
        return img.copy()
    if t2d is None:
        raise RuntimeError('texture2ddecoder is not installed')
    bw, bh = blocks.shape[1] * t.bw, blocks.shape[0] * t.bhh       # (decoded at the padded size, then cropped)
    if fmt in ASTC:
        out = t2d.decode_astc(raw, bw, bh, *ASTC[fmt])
    else:
        fn = {0x1a: t2d.decode_bc1, 0x1b: t2d.decode_bc2 if hasattr(t2d, 'decode_bc2') else None, 0x1c: t2d.decode_bc3,
              0x1d: t2d.decode_bc4, 0x1e: t2d.decode_bc5, 0x1f: t2d.decode_bc6, 0x20: t2d.decode_bc7}.get(fmt)
        if fn is None:
            raise ValueError(f'texture format 0x{t.format:04x}')
        out = fn(raw, bw, bh)
    img = np.frombuffer(out, np.uint8).reshape(bh, bw, 4)[:h, :w]
    return img[..., [2, 1, 0, 3]].copy()            # (BGRA -> RGBA)


def normal_map(img):
    """Two-channel normal map (X in R, Y in G) -> RGB with Z reconstructed, alpha 255."""
    xy = img[..., :2].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - (xy ** 2).sum(-1), 0, 1))
    out = np.empty(img.shape[:2] + (4,), np.uint8)
    out[..., 0:2] = img[..., :2]
    out[..., 2] = np.clip((z * 0.5 + 0.5) * 255, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def write_png(path, img):
    h, w, c = img.shape
    raw = b''.join(b'\0' + img[y].tobytes() for y in range(h))
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    with open(path + '.tmp', 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6 if c == 4 else 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))
    os.replace(path + '.tmp', path)


def texture_png(game, name, role=None):
    """Tex/<name>.bntx.zs -> cached PNG path (None if the texture is missing / unreadable). role 'normal' rebuilds Z."""
    rel = f'Tex/{name}.bntx.zs'
    if not game.exists(rel):
        return None
    out = os.path.join(cache_dir(), f'{name}{"_n" if role == "normal" else ""}.png')
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(game.path(rel)):
        return out
    img = decode_rgba(game.read(rel))
    if role == 'normal':
        img = normal_map(img)
    write_png(out, img)
    return out
