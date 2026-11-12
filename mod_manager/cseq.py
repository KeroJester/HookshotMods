"""Render Majora's Mask 3D sound effects (CSEQ sequence sounds + CBNK banks + CWAR waves) to PCM.

MM3D keeps the N64 design: every sound effect is a short sequence (notes on bank instruments), started at a
label offset inside a shared CSEQ file. This is a small offline sequencer + sampler, enough for one-shot and
looping player SFX:
  - CSEQ track commands (CTR / NW4C sequence set): notes, waits, program / bank, open track, jump / call /
    loop, volume / pan / transpose / pitch bend / bend range, ADSR, portamento, sweep, tempo, timebase,
    variables (set / add / compare + _if / _r / _v prefixes), fin.
  - CBNK: direct / ranged / indexed key and velocity regions -> wave id, original key, volume, pitch, ADSR.
  - Voices: linear-interpolated resampling, looping waves, a simple ADSR (NW4C-like times).
Output: mono float32 at OUT_RATE. Panning / effects sends are ignored (the game positions the sound).
"""
import math
import random

import numpy as np

from .csar import CSAR, _R, decode_cwav

OUT_RATE = 32000


def _varlen(d, p):
    v = 0
    while True:
        b = d[p]
        p += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, p


# ---- banks ------------------------------------------------------------------------------------------------
class Bank:
    def __init__(self, sar: CSAR, bank_index):
        self.sar = sar
        e = sar._table(0x2101)[bank_index]
        self.d = d = sar.file_data(sar.r.u32(e))
        r = self.r = _R(d)
        info = r.sections()[0x5800][0]
        base = info + 8
        _t, wt = r.ref(base)
        _t, it = r.ref(base + 8)
        wt += base
        it += base
        self.wave_ids = [(r.u32(wt + 4 + 8 * i), r.u32(wt + 8 + 8 * i)) for i in range(r.u32(wt))]
        self.instruments = []
        for i in range(r.u32(it)):
            t, o = r.ref(it + 4 + 8 * i)
            self.instruments.append(None if o is None or t != 0x5900 else it + o)
        self._waves = {}

    def _region(self, ref_at, key):
        """Follow a key/velocity region reference (direct / ranged / indexed) to its info structure."""
        r = self.r
        t, o = r.ref(ref_at)
        if o is None:
            return None
        p = ref_at + o if t in (0x6000, 0x6001, 0x6002) else None
        if t == 0x6000:                                   # direct: one ref
            _t, o2 = r.ref(p)
            return p + o2
        if t == 0x6001:                                   # ranged: u32 count, u8 max keys (aligned), refs
            n = r.u32(p)
            keys = list(self.d[p + 4:p + 4 + n])
            refs = p + 4 + ((n + 3) & ~3)
            for i, k in enumerate(keys):
                if key <= k:
                    _t, o2 = r.ref(refs + 8 * i)
                    return p + o2 if o2 is not None else None      # (offsets from the table start)
            return None
        if t == 0x6002:                                   # indexed: u8 min, u8 max, pad, refs
            lo, hi = self.d[p], self.d[p + 1]
            if not lo <= key <= hi:
                return None
            at = p + 4 + 8 * (key - lo)
            _t, o2 = r.ref(at)
            return p + o2 if o2 is not None else None
        return None

    def note(self, prg, key, velocity):
        """-> dict(wave=(rate, pcm, loop, ls, le), key0, volume, pitch, adsr) or None"""
        if prg >= len(self.instruments) or self.instruments[prg] is None:
            return None
        ins = self.instruments[prg]
        kr = self._region(ins, key)                       # key region info (0x5901): holds a velocity region ref
        if kr is None:
            return None
        vr = self._region(kr, velocity)                   # velocity region info (0x5902)
        if vr is None:
            return None
        r = self.r
        wave_idx, flags = r.u32(vr), r.u32(vr + 4)
        p = vr + 8
        key0, vol, pitch, adsr = 60, 127, 1.0, (127, 127, 127, 0, 127)
        if flags & 1:
            key0 = r.u32(p); p += 4
        if flags & 2:
            vol = r.u32(p); p += 4
        if flags & 4:
            p += 4                                        # pan
        if flags & 8:
            pitch = np.frombuffer(self.d, np.float32, 1, p)[0]; p += 4
        if flags & 0x10:
            p += 4                                        # ignore note-off / key group / interpolation
        if flags & 0x20:
            p += 4
        if flags & 0x40:
            p += 4
        if flags & 0x80:
            p += 4
        if flags & 0x100:
            p += 4
        if flags & 0x200:                                 # ADSHR curve: offset (from vr) to a ref to 5 bytes
            off = r.u32(p)
            q = vr + off
            _t, o = r.ref(q)
            if o is not None:
                a = q + o
                adsr = (self.d[a], self.d[a + 1], self.d[a + 2], self.d[a + 3], self.d[a + 4])
        return dict(wave=self.wave(wave_idx), key0=key0, volume=vol, pitch=float(pitch), adsr=adsr)

    def wave(self, idx):
        if idx not in self._waves:
            arch_item, w = self.wave_ids[idx]
            sar = self.sar
            cwar = sar.file_data(sar.wave_archive_file(arch_item & 0xFFFFFF))
            ra = _R(cwar)
            s = ra.sections()
            ainfo, afile = s[0x6800][0], s[0x6801][0]
            _t, woff = ra.ref(ainfo + 8 + 4 + w * 12)
            size = ra.u32(ainfo + 8 + 4 + w * 12 + 8)
            rate, pcm, loop, ls, le = decode_cwav(cwar[afile + 8 + woff:afile + 8 + woff + size])
            self._waves[idx] = (rate, pcm[:, 0].astype(np.float32) / 32768.0, loop, ls, le)
        return self._waves[idx]


# ---- envelope (NW4C-style) ---------------------------------------------------------------------------------
def _attack_time(a):            # 127 = instant
    return 0.0 if a >= 127 else (127 - a) ** 1.6 * 0.0009
def _decay_time(d):
    return 0.0 if d >= 127 else (127 - d) ** 1.6 * 0.004
def _release_time(r):
    return 0.0 if r >= 127 else (127 - r) ** 1.6 * 0.004


class Voice:
    """One sampler voice. Pitch per sample = glide(key) + bend + sweep + LFO, relative to the region's key."""
    def __init__(self, note, key, velocity, track_vol, length_s, bend_semis, sweep, lfo=None, glide=None):
        rate, pcm, loop, ls, le = note["wave"]
        self.pcm, self.loop, self.ls, self.le = pcm, loop, ls, min(le, len(pcm))
        self.step0 = rate / OUT_RATE * note["pitch"]
        self.key0 = note["key0"]
        self.key = float(key)
        self.glide = glide                               # (from key, to key, start t, duration s) or None
        self.bend = bend_semis
        self.sweep = sweep                               # (semitones start offset, duration s)
        self.lfo = lfo                                   # (depth semitones, freq Hz, delay s) or None
        self.gain = (note["volume"] / 127.0) * (velocity / 127.0) ** 2 * track_vol
        self.adsr = note["adsr"]
        self.length = length_s
        self.pos = 0.0
        self.t = 0.0
        self.done = False

    def retarget(self, key, glide_s):
        """Tie mode: the next note continues this voice, gliding to its key."""
        cur = self._key_at(self.t)
        self.glide = (cur, float(key), self.t, max(glide_s, 1e-3)) if glide_s > 0 else None
        self.key = float(key)

    def release_now(self):
        self.length = self.t

    def _key_at(self, t):
        if self.glide:
            k0, k1, t0, d = self.glide
            if t < t0 + d:
                return k0 + (k1 - k0) * max(0.0, (t - t0) / d)
        return self.key

    def render(self, n):
        out = np.zeros(n, np.float32)
        dt = 1.0 / OUT_RATE
        a, d, s, _h, r = self.adsr
        at, dc, rl = _attack_time(a), _decay_time(d), _release_time(r)
        sus = (s / 127.0) ** 2
        for i in range(n):
            if self.done:
                break
            t = self.t
            semis = self._key_at(t) - self.key0 + self.bend
            if self.sweep and t < self.sweep[1]:
                semis += self.sweep[0] * (1.0 - t / self.sweep[1])
            if self.lfo and t >= self.lfo[2]:
                semis += self.lfo[0] * math.sin(2.0 * math.pi * self.lfo[1] * (t - self.lfo[2]))
            step = self.step0 * 2.0 ** (semis / 12.0)
            ip = int(self.pos)
            if ip + 1 >= self.le:
                if self.loop and self.le > self.ls + 1:
                    self.pos -= (self.le - self.ls)
                    ip = int(self.pos)
                else:
                    self.done = True
                    break
            f = self.pos - ip
            smp = self.pcm[ip] * (1 - f) + self.pcm[ip + 1] * f
            if t < at:
                env = t / at
            elif t < at + dc:
                env = 1.0 - (1.0 - sus) * (t - at) / dc
            else:
                env = sus
            if self.length is not None and t >= self.length:
                rt = t - self.length
                if rl <= 0 or rt >= rl:
                    self.done = True
                    break
                env *= 1.0 - rt / rl
            out[i] = smp * env * self.gain
            self.pos += step
            self.t += dt
        return out


# ---- sequencer --------------------------------------------------------------------------------------------
class Track:
    def __init__(self, pos):
        self.pos = pos
        self.wait = 0
        self.stack = []
        self.loops = []
        self.prg = 0
        self.bank = 0
        self.vol = 127
        self.vol2 = 127
        self.transpose = 0
        self.bend = 0
        self.bend_range = 2
        self.adsr = [None] * 5
        self.note_wait = True
        self.porta = None               # porta source key (0xC9); portamento active while set
        self.porta_time = 0             # 0xCF
        self.porta_transposed = False   # porta holds a played (already transposed) key
        self.mod_depth = 0              # 0xCA (0..127)
        self.mod_speed = 16             # 0xCB (x 0.390625 Hz)
        self.mod_type = 0               # 0xCC (0 pitch)
        self.mod_range = 1              # 0xCD (semitones at full depth)
        self.mod_delay = 0              # 0xE0 (ticks)
        self.tied = None                # tie mode: the voice later notes continue
        self.sweep = 0
        self.tie = False
        self.done = False
        self.cmp = True


class Sequencer:
    def __init__(self, sar: CSAR, seq_file, banks, variables=None, seed=0, sustain=False):
        self.sar = sar
        d = sar.file_data(seq_file)
        r = _R(d)
        self.d = d
        self.data = r.sections()[0x5000][0] + 8
        self.banks = banks
        self.vars = dict(variables or {})
        self.rng = random.Random(seed)
        self.tempo = 120
        self.timebase = 48
        self.sustain = sustain          # hold every note (looping samples keep sounding): for continuously-held sounds
        self.events = []                # (seconds, track pos id, bank, program, key, velocity, length s, track volume)
        self.now = 0.0

    def render(self, start, max_seconds=4.0, tail=1.0):
        tracks = {0: Track(self.data + start)}
        voices = []
        out = []
        tick = 0
        tick_samples = lambda: OUT_RATE * 60.0 / (self.tempo * self.timebase)
        acc = 0.0
        end_at = None
        while True:
            for no in sorted(tracks):
                t = tracks[no]
                guard = 0
                while not t.done and t.wait <= 0 and guard < 10000:
                    guard += 1
                    self._step(t, tracks, voices)
                t.wait -= 1
            acc += tick_samples()
            n = int(acc)
            acc -= n
            buf = np.zeros(n, np.float32)
            for v in voices:
                if not v.done:
                    buf += v.render(n)
            voices = [v for v in voices if not v.done]
            out.append(buf)
            tick += 1
            secs = sum(len(b) for b in out) / OUT_RATE
            self.now = secs
            if all(t.done for t in tracks.values()) and not voices:
                break
            if all(t.done for t in tracks.values()) and end_at is None:
                end_at = secs + tail
            if secs >= max_seconds or (end_at is not None and secs >= end_at):
                break
        return np.concatenate(out) if out else np.zeros(0, np.float32)

    def _arg_u8(self, t):
        v = self.d[t.pos]; t.pos += 1; return v

    def _arg_s16(self, t):
        v = int.from_bytes(self.d[t.pos:t.pos + 2], "big", signed=True); t.pos += 2; return v

    def _arg_u16(self, t):
        v = int.from_bytes(self.d[t.pos:t.pos + 2], "big"); t.pos += 2; return v

    def _arg_u24(self, t):
        v = int.from_bytes(self.d[t.pos:t.pos + 3], "big"); t.pos += 3; return v

    def _step(self, t, tracks, voices, prefix=None):
        d = self.d
        op = d[t.pos]
        t.pos += 1

        def arg(kind):
            if prefix and prefix[0] == "r" and kind != "u24":
                lo, hi = prefix[1], prefix[2]
                return self.rng.randint(min(lo, hi), max(lo, hi))
            if prefix and prefix[0] == "v" and kind != "u24":
                return self.vars.get(prefix[1], 0)
            return {"u8": self._arg_u8, "s8": lambda tt: (self._arg_u8(tt) ^ 0x80) - 0x80, "s16": self._arg_s16,
                    "u16": self._arg_u16, "u24": self._arg_u24, "var": lambda tt: self._varlen(tt)}[kind](t)

        if op < 0x80:                                       # note
            vel = self._arg_u8(t)
            length = arg("var")
            self._note(t, op, vel, length, voices)
            if t.note_wait:
                t.wait = length
            return
        if op == 0x80:
            t.wait = arg("var"); return
        if op == 0x81:
            p = arg("var")
            t.prg = p & 0xFFFF
            if p >> 16:
                t.bank = (p >> 16) & 0xFF
            return
        if op == 0x88:
            no = self._arg_u8(t); off = self._arg_u24(t)
            tracks[no] = Track(self.data + off)
            tracks[no].bank, tracks[no].prg = t.bank, t.prg
            return
        if op == 0x89:
            t.pos = self.data + self._arg_u24(t); return
        if op == 0x8A:
            off = self._arg_u24(t); t.stack.append(t.pos); t.pos = self.data + off; return
        if op == 0xA0:                                      # _r: random last argument
            lo = self._arg_s16(t); hi = self._arg_s16(t)
            # the prefixed command's own argument bytes are absent: build it with a random value
            return self._step(t, tracks, voices, ("r", lo, hi))
        if op == 0xA1:                                      # _v
            var = self._arg_u8(t)
            return self._step(t, tracks, voices, ("v", var))
        if op == 0xA2:                                      # _if
            if t.cmp:
                return self._step(t, tracks, voices, prefix)
            self._skip(t)
            return
        if op in (0xA3, 0xA4, 0xA5):                        # _t / _tr / _tv: time-interpolated: apply directly
            if op == 0xA3:
                self._arg_s16(t)
            elif op == 0xA4:
                self._arg_s16(t); self._arg_s16(t)
            else:
                self._arg_u8(t)
            return self._step(t, tracks, voices, prefix)
        if 0xB0 <= op <= 0xDF:
            if op in (0xC3, 0xC4):
                v = arg("s8")
            else:
                v = arg("u8")
            if op == 0xB0: self.timebase = v or 48
            elif op == 0xB6: t.bank = v
            elif op == 0xC1: t.vol = v
            elif op == 0xD5: t.vol2 = v
            elif op == 0xC3: t.transpose = v
            elif op == 0xC4: t.bend = v
            elif op == 0xC5: t.bend_range = v
            elif op == 0xC7: t.note_wait = bool(v)
            elif op == 0xC8:
                t.tie = bool(v)
                if not t.tie and t.tied is not None:
                    t.tied.release_now(); t.tied = None
            elif op == 0xC9: t.porta = v; t.porta_transposed = False
            elif op == 0xCA: t.mod_depth = v
            elif op == 0xCB: t.mod_speed = v
            elif op == 0xCC: t.mod_type = v
            elif op == 0xCD: t.mod_range = v
            elif op == 0xCE: t.porta = None if not v else (t.porta if t.porta is not None else 60)
            elif op == 0xCF: t.porta_time = v
            elif op == 0xD0: t.adsr[0] = v
            elif op == 0xD1: t.adsr[1] = v
            elif op == 0xD2: t.adsr[2] = v
            elif op == 0xD3: t.adsr[3 + 1] = v
            elif op == 0xD4: t.loops.append([t.pos, v])
            return
        if op == 0xE0 or op == 0xE1 or op == 0xE2 or op == 0xE3 or op == 0xE4:
            v = arg("s16")
            if op == 0xE1: self.tempo = v or 120
            elif op == 0xE3: t.sweep = v
            elif op == 0xE0: t.mod_delay = v
            return
        if op == 0xF0:                                      # extended
            sub = self._arg_u8(t)
            if 0x80 <= sub <= 0x8B or 0x90 <= sub <= 0x95:
                var = self._arg_u8(t); val = arg("s16")
                cur = self.vars.get(var, 0)
                if sub == 0x80: self.vars[var] = val
                elif sub == 0x81: self.vars[var] = cur + val
                elif sub == 0x82: self.vars[var] = cur - val
                elif sub == 0x83: self.vars[var] = cur * val
                elif sub == 0x84: self.vars[var] = int(cur / val) if val else cur
                elif sub == 0x85: self.vars[var] = cur << val if val >= 0 else cur >> -val
                elif sub == 0x86: self.vars[var] = self.rng.randint(0, val) if val >= 0 else -self.rng.randint(0, -val)
                elif sub == 0x87: self.vars[var] = cur & val
                elif sub == 0x88: self.vars[var] = cur | val
                elif sub == 0x89: self.vars[var] = cur ^ val
                elif sub == 0x8A: self.vars[var] = ~val
                elif sub == 0x8B: self.vars[var] = cur % val if val else cur
                elif sub == 0x90: t.cmp = cur == val
                elif sub == 0x91: t.cmp = cur >= val
                elif sub == 0x92: t.cmp = cur > val
                elif sub == 0x93: t.cmp = cur <= val
                elif sub == 0x94: t.cmp = cur < val
                elif sub == 0x95: t.cmp = cur != val
            elif sub == 0xE0:
                self._arg_u16(t)
            else:
                self._arg_u8(t); self._arg_s16(t)
            return
        if op == 0xFC:                                      # loop end
            if t.loops:
                lp = t.loops[-1]
                if lp[1] == 0:
                    t.pos = lp[0]                           # infinite
                else:
                    lp[1] -= 1
                    if lp[1] > 0:
                        t.pos = lp[0]
                    else:
                        t.loops.pop()
            return
        if op == 0xFD:
            if t.stack:
                t.pos = t.stack.pop()
            else:
                t.done = True
            return
        if op == 0xFE:
            self._arg_u16(t); return
        if op == 0xFF:
            t.done = True; return
        t.done = True                                       # unknown: stop the track

    def _varlen(self, t):
        v, t.pos = _varlen(self.d, t.pos)
        return v

    def _skip(self, t):
        """Skip one command (for a false _if)."""
        saved = (t.wait, t.prg, t.bank, t.vol, t.transpose, t.bend, list(t.loops), list(t.stack), t.pos)
        dummy_tracks, dummy_voices = {}, []
        self._step(t, dummy_tracks, dummy_voices)
        pos = t.pos
        (t.wait, t.prg, t.bank, t.vol, t.transpose, t.bend, t.loops, t.stack, _p) = saved
        t.pos = pos

    def _note(self, t, key, vel, length, voices):
        if t.bank >= len(self.banks) or self.banks[t.bank] is None:
            return
        k = key + t.transpose
        tick_s = 60.0 / (self.tempo * self.timebase)
        secs = length * tick_s
        # portamento: glide over porta_time ticks (0: over the whole note)
        glide_s = (t.porta_time * tick_s if t.porta_time else secs) if t.porta is not None else 0.0
        if t.tie and t.tied is not None and not t.tied.done:   # tie: continue the held voice (legato)
            t.tied.retarget(k, glide_s)
            if t.porta is not None:
                t.porta = k
                t.porta_transposed = True
            return
        self.events.append((self.now, id(t), t.bank, t.prg, k, vel, secs, t.vol * t.vol2 / 16129.0))
        note = self.banks[t.bank].note(t.prg, k, vel)
        if note is None:
            return
        adsr = list(note["adsr"])
        for i, v in enumerate(t.adsr):
            if v is not None and i < 5:
                adsr[i] = v
        note = dict(note, adsr=tuple(adsr))
        bend = t.bend / 128.0 * t.bend_range
        sweep = (t.sweep / 64.0, secs) if t.sweep else None
        lfo = None
        if t.mod_depth and t.mod_type == 0:                   # pitch LFO (vibrato)
            lfo = (t.mod_depth / 128.0 * max(t.mod_range, 1), t.mod_speed * 0.390625, t.mod_delay * tick_s)
        pk = None if t.porta is None else t.porta + (0 if t.porta_transposed else t.transpose)   # porta key, transposed
        glide = (float(pk), float(k), 0.0, max(glide_s, 1e-3)) if pk is not None and pk != k else None
        held = t.tie or self.sustain
        v = Voice(note, k, vel, (t.vol / 127.0) * (t.vol2 / 127.0), None if held else secs, bend, sweep, lfo, glide)
        voices.append(v)
        if t.tie:
            t.tied = v
        if t.porta is not None:
            t.porta = k                                       # the next glide starts from this note
            t.porta_transposed = True


def render_sound(sar: CSAR, name, max_seconds=4.0, variables=None, seed=0, sustain=False):
    """-> (float32 mono at OUT_RATE, info). Sequence sounds only."""
    s = sar._table(0x2100)[sar.items[name] & 0xFFFFFF]
    r = sar.r
    file_id, volume = r.u32(s), r.u8(s + 8)
    t, off = r.ref(s + 0xC)
    assert t == 0x2203, f"{name}: not a sequence sound ({t:#x})"
    det = s + off
    _t, bt = r.ref(det)
    bt += det
    start = r.u32(det + 0x10)
    bank_items = [r.u32(bt + 4 + 4 * i) for i in range(r.u32(bt))]
    banks = [Bank(sar, b & 0xFFFFFF) if (b >> 24) == 3 else None for b in bank_items]
    seq = Sequencer(sar, file_id, banks, variables, seed, sustain)
    pcm = seq.render(start, max_seconds, tail=max_seconds if sustain else 1.0)
    return pcm * (volume / 127.0), dict(volume=volume, start=start, banks=bank_items)


# ---- the Goron roll's sound effects (Player_Action_96 and friends) ------------------------------------------
# name -> continuous? (MM requests "- SFX_FLAG" sounds every frame; they loop while requested)
GORON_SFX = {
    "NA_SE_PL_GORON_TO_BALL": False,              # func_80836AD8 (curl)
    "NA_SE_PL_BALL_TO_GORON": False,              # func_80857950 (uncurl)
    "NA_SE_PL_GORON_ROLL": "sustain",             # re-triggered every revolution in MM: its held looping rumble
    "NA_SE_PL_GORON_CHG_ROLL": "sustain",         # same, spiked
    "NA_SE_PL_GORON_BALL_CHARGE": True,           # charging (- SFX_FLAG)
    "NA_SE_PL_GORON_BALL_CHARGE_DASH": False,     # spikes out
    "NA_SE_PL_GORON_BALL_CHARGE_FAILED": False,   # spikes gone
    "NA_SE_PL_GORON_SLIP": True,                  # func_808576BC (- SFX_FLAG)
    "NA_SE_PL_ROLL_DUST": True,                   # func_808576BC (- SFX_FLAG)
    "NA_SE_IT_GORON_ROLLING_REFLECTION": False,   # wall bounce
    "NA_SE_PL_GORON_BALLJUMP": False,             # func_80857640 (the ground pound jump)
    "NA_SE_PL_GORON_PUNCH": False,                # func_80857AEC: Player_RequestQuakeAndRumble on the pound landing
    "NA_SE_PL_BODY_HIT": False,                   # func_80840A30: the rolling bonk against a wall
}


def export_goron_sfx(bcsar_path, out_dir, log=print):
    """Render GORON_SFX from MM3D's JokerSound.bcsar to 16-bit mono WAVs (OUT_RATE) in out_dir.
    Continuous sounds are rendered long (their sequences loop) and played looping by the overlay."""
    import os
    import wave
    sar = CSAR(open(bcsar_path, "rb").read())
    os.makedirs(out_dir, exist_ok=True)
    for name, mode in GORON_SFX.items():
        loop_start = None
        if mode == "sustain":
            # notes held: one-time layers (clicks, non-looping samples) play out, then only looping samples remain;
            # the loop starts after the last one-time layer has ended (found from the render's own envelope)
            pcm, info = render_sound(sar, name, max_seconds=6.0, sustain=True)
            loop_start = _sustain_loop_start(pcm)
        elif mode:
            pcm, info = render_sound(sar, name, max_seconds=6.0)
            loop_start = 0
        else:
            pcm, info = render_sound(sar, name, max_seconds=4.0)
        if loop_start is not None:                        # seam: crossfade the end into the loop start
            k = min((len(pcm) - loop_start) // 4, int(0.05 * OUT_RATE))
            if k > 0:
                ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
                pcm[loop_start:loop_start + k] = pcm[loop_start:loop_start + k] * ramp + pcm[-k:] * (1.0 - ramp)
                pcm = pcm[:-k]
        x = (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(os.path.join(out_dir, name + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(OUT_RATE); w.writeframes(x.tobytes())
        if loop_start is not None:                        # loop point for the overlay (sample index)
            with open(os.path.join(out_dir, name + ".loop"), "w") as f:
                f.write(str(loop_start))
        elif os.path.exists(os.path.join(out_dir, name + ".loop")):
            os.remove(os.path.join(out_dir, name + ".loop"))
        log(f"  sfx {name}: {len(x) / OUT_RATE:.2f} s" + (f" (loop from {loop_start / OUT_RATE:.2f} s)" if loop_start is not None else ""))


def _sustain_loop_start(pcm, settle=0.5):
    """First point after which the level stays steady (only looping layers left), plus a margin."""
    win = int(0.05 * OUT_RATE)
    n = len(pcm) // win
    rms = np.sqrt((pcm[:n * win].reshape(n, win) ** 2).mean(1))
    tail = np.median(rms[n // 2:])
    start = n // 2
    for i in range(n // 2, 0, -1):                       # walk back while the level matches the steady tail
        if abs(rms[i] - tail) > 0.25 * tail + 1e-4:
            break
        start = i
    return min(len(pcm) // 2, int((start * win) + settle * OUT_RATE * 0.2))
