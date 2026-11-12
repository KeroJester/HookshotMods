#!/usr/bin/env python3
"""Replace skeletal animations (FSKA) inside a BFRES v10 animation archive.

The archive's existing bytes are never moved. New bone-animation records, curves and key data are
appended right before the relocation table (_RLT); the target FSKA's pointers are redirected to them;
the relocation table is rebuilt with entries for the new pointers. Curves are written in the classic
uncompressed form (float frames, float keys, linear interpolation), which the runtime still reads.

FSKA (stride 0x50): +0x08 name, +0x20 bind-index array (u16, runtime filled, 0xFFFF in file),
+0x28 bone-anim array, +0x40 u32 frame count, +0x44 u32 curve count, +0x48 u32 baked size,
+0x4C u16 bone-anim count.
Bone anim (stride 0x38): +0x00 name, +0x08 curves, +0x10 base data, +0x18/+0x20 user data,
+0x28 u32 flags (0x38 = base S/R/T; curve bits rotX 0x200 rotY 0x400 rotZ 0x800, transX/Y/Z
0x2000/0x4000/0x8000), u8 beginRot, u8 beginTrans, u8 curveCount, u8 beginBaseTrans, +0x30 u32 beginCurve.
Curve (stride 0x30): frames ptr, keys ptr, u16 flags, u16 key count, u32 target (byte offset in the
bone result: translate 16/20/24, rotate 32/36/40), f32 start, end, scale, offset, delta.
"""

import struct
from typing import Dict, Optional

import numpy as np

CURVE_LINEAR_FLOAT = 0x0010      # frame type Single, key type Single, curve type Linear
TARGET_SCALE = (4, 8, 12)
TARGET_TRANS = (16, 20, 24)
TARGET_ROT = (32, 36, 40)
KEEP_BONES = ("Root", "Ph_Pred_Ankle_L", "Ph_Pred_Ankle_R")   # root motion and foot prediction: keep vanilla curves
FLAG_SCALE_CURVES = 0x01C0
FLAG_SCALE_ONE = 0x03000000      # ScaleUniform | ScaleVolumeOne: the runtime then ignores scale entirely
FLAG_ROT_CURVES = 0x0E00
FLAG_TRANS_CURVES = 0xE000
FLAG_ALL_CURVES = 0xFFC0


class Driver:
    """A curve channel fed at runtime: 3 curves (x, y, z) whose 2 keys point into the archive's shared driver block
    (DRIVER_MAGIC + slots of 16 bytes = 2 keys x (value, delta)). The mod's overlay finds the block in guest memory and
    writes the values every frame, so every clip using the driver shows them whatever frame the engine samples."""
    def __init__(self, slot, init=(0.0, 0.0, 0.0)):
        self.slot, self.init = slot, init


DRIVER_MAGIC = b"GORONDRV\x01\x00\x00\x00"
DRIVER_SLOTS = 64


class AnimArchive:
    def __init__(self, data: bytes):
        self.d = bytearray(data)
        assert self.d[:8] == b"FRES    "
        self.rlt = self.u32(0x18)
        assert self.d[self.rlt:self.rlt + 4] == b"_RLT"
        nsec = self.u32(self.rlt + 8)
        self.sections = [list(struct.unpack_from("<QIIII", self.d, self.rlt + 16 + 24 * i)) for i in range(nsec)]
        n = sum(s[4] for s in self.sections)
        base = self.rlt + 16 + 24 * nsec
        self.entries = [struct.unpack_from("<IHBB", self.d, base + 8 * k) for k in range(n)]
        self.data_end = self.sections[0][2] + self.sections[0][1]
        self.new = bytearray()
        self.new_entries = []

    def u64(self, o): return struct.unpack_from("<Q", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def s(self, o): return self.d[o + 2:o + 2 + self.u16(o)].decode(errors="replace")

    def fskas(self):
        fmdl = self.u64(0x28)       # not used: anim archives list FSKAs via the header dicts
        out, i = {}, 0
        while True:
            i = self.d.find(b"FSKA", i)
            if i < 0 or i >= self.rlt:
                break
            name_ptr = self.u64(i + 8)
            if 0 < name_ptr < self.rlt:
                out[self.s(name_ptr)] = i
            i += 4
        return out

    # ---- appending ---------------------------------------------------------------------------------
    def _alloc(self, data: bytes, align=8) -> int:
        while (self.rlt + len(self.new)) % align:
            self.new.append(0)
        off = self.rlt + len(self.new)
        self.new += data
        return off

    def _reloc(self, pos, struct_count, ptrs, pads):
        self.new_entries.append((pos, struct_count, ptrs, pads))

    def driver_block(self):
        """Offset (from the file start) of this archive's shared driver slots, created once after DRIVER_MAGIC."""
        if getattr(self, "_driver_off", None) is None:
            self._driver_off = self._alloc(DRIVER_MAGIC + bytes(DRIVER_SLOTS * 16), 16) + len(DRIVER_MAGIC)
        return self._driver_off

    def _driver_init(self, slot, value):
        """Both keys of a driver slot = (value, delta 0)."""
        # (the two keys differ slightly, so a load-time "constant curve" shortcut can't freeze the channel)
        pos = self.driver_block() + 16 * slot - self.rlt
        struct.pack_into("<4f", self.new, pos, value, 1e-4, value + 1e-4, 0.0)

    def replace_animation(self, name: str, n_frames: int, bone_curves: Dict[str, Dict[str, np.ndarray]],
                          looping: Optional[bool] = None, keep_bones=KEEP_BONES):
        """bone_curves: {bone name: {"rot": [n_frames+1, 3] euler radians, "trans": [n_frames+1, 3], "scale": [n_frames+1, 3]
        (each optional)}}.
        Bones not listed keep their base pose (no curves), except `keep_bones` not in bone_curves: those keep their
        original curves (root motion lives on "Root") when the frame count is unchanged."""
        fs = self.fskas()
        if name not in fs:
            raise KeyError(f"animation {name!r} not in archive")
        f = fs[name]
        n_bones = self.u16(f + 0x4C)
        old_ba = self.u64(f + 0x28)
        new_ba = bytearray()
        curves = []                 # (bone index, target, values); kept original curves are (bone, None, None)
        flags_out = []
        kept_arr = {}               # bone index -> original curve array (left in place)
        same_length = self.u32(f + 0x40) == n_frames
        for b in range(n_bones):
            rec = bytearray(self.d[old_ba + 0x38 * b: old_ba + 0x38 * (b + 1)])
            bname = self.s(struct.unpack_from("<Q", rec, 0)[0])
            flags = struct.unpack_from("<I", rec, 0x28)[0] & ~FLAG_ALL_CURVES
            spec = bone_curves.get(bname)
            mine = []
            orig_n = rec[0x2E]
            if spec is None and bname in keep_bones and same_length and orig_n:
                kept_arr[b] = struct.unpack_from("<Q", rec, 0x08)[0]
                struct.pack_into("<I", rec, 0x30, len(curves))
                new_ba += rec
                flags_out.append((b, []))
                curves += [(b, None, None)] * orig_n
                continue
            if spec is not None:
                if spec.get("trans") is not None:
                    flags |= FLAG_TRANS_CURVES
                    mine += [(t, (("drv", spec["trans"].slot + k, spec["trans"].init[k]) if isinstance(spec["trans"], Driver) else spec["trans"][:, k])) for k, t in enumerate(TARGET_TRANS)]
                if spec.get("scale") is not None:
                    flags = (flags | FLAG_SCALE_CURVES) & ~FLAG_SCALE_ONE
                    mine += [(t, (("drv", spec["scale"].slot + k, spec["scale"].init[k]) if isinstance(spec["scale"], Driver) else spec["scale"][:, k])) for k, t in enumerate(TARGET_SCALE)]
                if spec.get("rot") is not None:
                    flags |= FLAG_ROT_CURVES
                    mine += [(t, (("drv", spec["rot"].slot + k, spec["rot"].init[k]) if isinstance(spec["rot"], Driver) else spec["rot"][:, k])) for k, t in enumerate(TARGET_ROT)]
                mine.sort(key=lambda x: x[0])
            struct.pack_into("<I", rec, 0x28, flags)
            rec[0x2E] = len(mine)
            struct.pack_into("<I", rec, 0x30, len(curves))
            struct.pack_into("<Q", rec, 0x08, 0)
            new_ba += rec
            flags_out.append((b, mine))
            curves += [(b, t, v) for t, v in mine]

        # key data per curve, then the curve arrays per bone
        frames = np.arange(n_frames + 1, dtype=np.float32)
        frames_off = self._alloc(frames.tobytes(), 8)
        curve_arr_off = {}
        for b, mine in flags_out:
            if not mine:
                continue
            recs = bytearray()
            for t, v in mine:
                if isinstance(v, tuple) and v[0] == "drv":
                    _tag, slot, init = v
                    keys_off = self.driver_block() + 16 * slot
                    self._driver_init(slot, init)
                    fr2 = self._alloc(np.array([0.0, float(n_frames)], np.float32).tobytes(), 8)
                    recs += struct.pack("<QQHHIfffff4x", fr2, keys_off, CURVE_LINEAR_FLOAT, 2, t,
                                        0.0, float(n_frames), 1.0, 0.0, 0.0)
                    continue
                v = np.asarray(v, dtype=np.float64)
                keys = np.zeros((len(v), 2), np.float32)
                keys[:, 0] = v
                keys[:-1, 1] = np.diff(v)
                keys_off = self._alloc(keys.tobytes(), 8)
                recs += struct.pack("<QQHHIfffff4x", frames_off, keys_off, CURVE_LINEAR_FLOAT, len(v), t,
                                    0.0, float(n_frames), 1.0, 0.0, float(v[-1] - v[0]))
            off = self._alloc(bytes(recs), 8)
            self._reloc(off, len(mine), 2, 4)
            curve_arr_off[b] = off
        curve_arr_off.update(kept_arr)
        for b, off in curve_arr_off.items():
            struct.pack_into("<Q", new_ba, 0x38 * b + 0x08, off)
        ba_off = self._alloc(bytes(new_ba), 8)
        # relocation entries for the bone-anim array (curves pointer only when non-null, like Nintendo's)
        for b in range(n_bones):
            pos = ba_off + 0x38 * b
            if b in curve_arr_off:
                self._reloc(pos, 1, 5, 2)
            else:
                self._reloc(pos, 1, 1, 6)
                self._reloc(pos + 0x10, 1, 3, 4)
        bind_off = self._alloc(b"\xff\xff" * n_bones, 8)

        struct.pack_into("<Q", self.d, f + 0x20, bind_off)
        struct.pack_into("<Q", self.d, f + 0x28, ba_off)
        struct.pack_into("<I", self.d, f + 0x40, n_frames)
        struct.pack_into("<I", self.d, f + 0x44, len(curves))
        baked = len(curves) * 4 * (((n_frames + 1) + 15) // 16 * 16) * 2
        struct.pack_into("<I", self.d, f + 0x48, baked)
        if looping is not None:
            fl = self.u16(f + 4)
            struct.pack_into("<H", self.d, f + 4, (fl | 0x4) if looping else (fl & ~0x4))
        return len(curves)

    # ---- output ------------------------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        if not self.new:
            return bytes(self.d)
        body = bytes(self.d[:self.rlt]) + bytes(self.new)
        while len(body) % 8:
            body += b"\0"
        new_end = self.rlt + len(self.new)
        new_rlt = len(body)
        entries = sorted(self.entries + self.new_entries, key=lambda e: e[0])
        secs = [list(s) for s in self.sections]
        secs[0][3] = 0
        secs[0][4] = len(entries)
        secs[0][2] = new_end - secs[0][1]
        for s in secs[1:]:
            s[1] = new_end
            s[3] = len(entries)
        out = bytearray(body)
        out += struct.pack("<4sIII", b"_RLT", new_rlt, len(secs), 0)
        for s in secs:
            out += struct.pack("<QIIII", *s)
        for e in entries:
            out += struct.pack("<IHBB", *e)
        struct.pack_into("<I", out, 0x18, new_rlt)
        struct.pack_into("<I", out, 0x1C, len(out))
        return bytes(out)
