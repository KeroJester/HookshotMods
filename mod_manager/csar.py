"""Minimal 3DS sound archive (BCSAR) reader: sound names -> wave sounds -> CWAR/CWAV -> PCM.

Enough of the format to pull Majora's Mask 3D sound effects (JokerSound.bcsar) out as 16-bit PCM:
  CSAR  STRG (names + lookup tree), INFO (sound / wave archive / file tables), FILE (embedded files)
  CWSD  wave sound data: wave id table (archive item id, wave index) + per-sound note -> wave id
  CWAR  wave archive: table of CWAV files
  CWAV  one wave: PCM8 / PCM16 / DSP-ADPCM channels
All 3DS (little-endian) "reference" fields are {u16 type, u16 pad, s32 offset}, offsets relative to the start of
the table or structure that holds them.
"""
import struct

import numpy as np

NULL = 0xFFFFFFFF


class _R:
    def __init__(self, d: bytes):
        self.d = d

    def u8(self, o): return self.d[o]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def s16(self, o): return struct.unpack_from("<h", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]

    def ref(self, o):
        """(type, offset or None)"""
        t, off = struct.unpack_from("<HxxI", self.d, o)
        return t, (None if off == NULL else off)

    def sections(self, base=0):
        """Header section table: {type id: (offset, size)} (offsets absolute)."""
        hsize = self.u16(base + 6)
        n = self.u16(base + 0x10)
        out = {}
        for i in range(n):
            t, off = self.ref(base + 0x14 + i * 12)
            out[t] = (base + off, self.u32(base + 0x14 + i * 12 + 8))
        assert hsize >= 0x14
        return out


def decode_dsp_adpcm(data: bytes, coefs, nsamples, hist1=0, hist2=0):
    """Nintendo DSP-ADPCM: 8-byte frames (1 header byte, 14 nibbles)."""
    out = np.empty(nsamples, np.int16)
    c = [(coefs[2 * i], coefs[2 * i + 1]) for i in range(8)]
    n = 0
    pos = 0
    while n < nsamples:
        hdr = data[pos]
        scale = 1 << (hdr & 0xF)
        c1, c2 = c[(hdr >> 4) & 7]
        for k in range(14):
            if n >= nsamples:
                break
            b = data[pos + 1 + k // 2]
            nib = (b >> 4) if (k % 2 == 0) else (b & 0xF)
            if nib >= 8:
                nib -= 16
            s = ((nib * scale) << 11) + 1024 + c1 * hist1 + c2 * hist2
            s >>= 11
            s = -32768 if s < -32768 else (32767 if s > 32767 else s)
            out[n] = s
            hist2, hist1 = hist1, s
            n += 1
        pos += 8
    return out


def decode_cwav(d: bytes):
    """CWAV -> (sample rate, int16 array [frames, channels], loop flag, loop start, loop end)."""
    r = _R(d)
    assert d[:4] == b"CWAV", d[:4]
    sec = r.sections()
    info, _ = sec[0x7000]
    data, _ = sec[0x7001]
    enc, loop = r.u8(info + 8), r.u8(info + 9)
    rate = r.u32(info + 0xC)
    loop_start, loop_end = r.u32(info + 0x10), r.u32(info + 0x14)
    tab = info + 0x1C
    nch = r.u32(tab)
    chans = []
    for c in range(nch):
        _t, off = r.ref(tab + 4 + c * 8)
        ci = tab + off
        _t, soff = r.ref(ci)
        samples = data + 8 + soff
        _t, aoff = r.ref(ci + 8)
        n = loop_end
        if enc == 2:
            a = ci + aoff
            coefs = [r.s16(a + 2 * i) for i in range(16)]
            chans.append(decode_dsp_adpcm(d[samples:], coefs, n))
        elif enc == 1:
            chans.append(np.frombuffer(d, np.int16, n, samples).copy())
        elif enc == 0:
            chans.append((np.frombuffer(d, np.int8, n, samples).astype(np.int16) << 8))
        else:
            raise NotImplementedError(f"CWAV encoding {enc}")
    return rate, np.stack(chans, 1), bool(loop), loop_start, loop_end


class CSAR:
    def __init__(self, data: bytes):
        self.d = data
        self.r = r = _R(data)
        assert data[:4] == b"CSAR"
        sec = r.sections()
        self.strg, _ = sec[0x2000]
        self.info, _ = sec[0x2001]
        self.file, _ = sec[0x2002]
        # strings
        _t, st = r.ref(self.strg + 8)
        stab = self.strg + 8 + st
        self.strings = []
        for i in range(r.u32(stab)):
            _t, off = r.ref(stab + 4 + i * 12)
            size = r.u32(stab + 4 + i * 12 + 8)
            self.strings.append(data[stab + off:stab + off + size].rstrip(b"\0").decode("ascii", "replace"))
        _t, tr = r.ref(self.strg + 16)
        tree = self.strg + 8 + tr
        self.items = {}                                   # name -> item id
        for i in range(r.u32(tree + 4)):
            node = tree + 8 + i * 20
            if r.u16(node) == 1:                          # leaf
                si, item = r.u32(node + 12), r.u32(node + 16)
                if si != NULL and si < len(self.strings):
                    self.items[self.strings[si]] = item
        # info tables
        base = self.info + 8
        self.tables = {}
        for k in range(8):
            t, off = r.ref(base + k * 8)
            if off is not None:
                self.tables[t] = base + off

    def _table(self, t):
        tab = self.tables[t]
        out = []
        for i in range(self.r.u32(tab)):
            _tt, off = self.r.ref(tab + 4 + i * 8)
            out.append(None if off is None else tab + off)
        return out

    def file_data(self, file_id):
        r = self.r
        fi = self._table(0x2106)[file_id]
        t, off = r.ref(fi)
        if t != 0x220C:                                   # external file
            return None
        loc = fi + off
        _t, foff = r.ref(loc)
        size = r.u32(loc + 8)
        if foff is None:
            return None
        start = self.file + 8 + foff
        return self.d[start:start + size]

    def sound(self, name):
        """{'type': 'wave'|'seq'|'strm', 'file': id, 'index': wsd index (wave), 'volume': u8}"""
        item = self.items[name]
        assert item >> 24 == 1, f"{name} is not a sound ({item:#x})"
        s = self._table(0x2100)[item & 0xFFFFFF]
        r = self.r
        file_id, volume = r.u32(s), r.u8(s + 8)
        t, off = r.ref(s + 0xC)
        kind = {0x2201: "seq", 0x2202: "strm", 0x2203: "wave"}.get(t, hex(t))
        out = dict(type=kind, file=file_id, volume=volume)
        if kind == "wave":
            out["index"] = r.u32(s + off)
        return out

    def wave_archive_file(self, war_index):
        wa = self._table(0x2103)[war_index]
        return self.r.u32(wa)

    def wave_sound_pcm(self, name):
        """Decode a wave sound: (rate, int16 [frames, ch], loop, loop_start, loop_end, info)."""
        info = self.sound(name)
        if info["type"] != "wave":
            raise NotImplementedError(f"{name}: {info['type']} sound")
        wsd = self.file_data(info["file"])
        r = _R(wsd)
        assert wsd[:4] == b"CWSD"
        sec = r.sections()
        blk, _ = sec[0x6800]
        base = blk + 8
        _t, wid = r.ref(base)                             # wave id table
        _t, wsdt = r.ref(base + 8)                        # wave sound data reference table
        wid += base
        wsdt += base
        _t, e = r.ref(wsdt + 4 + info["index"] * 8)
        ws = wsdt + e
        _t, ntab = r.ref(ws + 16)                         # note info table
        ntab += ws
        _t, n0 = r.ref(ntab + 4)
        note = ntab + n0
        wave_id_index = r.u32(note)
        arch_item, wave_index = r.u32(wid + 4 + wave_id_index * 8), r.u32(wid + 4 + wave_id_index * 8 + 4)
        cwar = self.file_data(self.wave_archive_file(arch_item & 0xFFFFFF))
        if cwar is None:
            raise RuntimeError(f"{name}: wave archive not embedded")
        ra = _R(cwar)
        asec = ra.sections()
        ainfo, _ = asec[0x6800]
        afile, _ = asec[0x6801]
        _t, woff = ra.ref(ainfo + 8 + 4 + wave_index * 12)
        wsize = ra.u32(ainfo + 8 + 4 + wave_index * 12 + 8)
        cwav = cwar[afile + 8 + woff:afile + 8 + woff + wsize]
        rate, pcm, loop, ls, le = decode_cwav(cwav)
        return rate, pcm, loop, ls, le, info
