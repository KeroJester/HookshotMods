#!/usr/bin/env python3
"""Map the in-memory PlayerGameParam struct from a probe-build dump.
Fields are laid out in alphabetical order; tagged (mod) values pin positions exactly, untagged
(base) fields are matched by value in alphabetical order between pinned neighbours.
usage: map_struct.py <dump.bin> <struct_start_rel_hex> <struct_end_rel_hex>  -> prints offset table"""
import os, re, struct, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
hdr = open(os.path.join(ROOT, "overlay", "player_param_keys.h")).read()
keys = [(m[0], float(m[1]), float(m[3])) for m in
        re.findall(r'\{"([^"]+)", ([-0-9.e+inf]+)f, (true|false), ([-0-9.e+inf]+)f\}', hdr)]
f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
data = open(sys.argv[1], "rb").read(); W = len(data) // 2
lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
slots = [(rel, struct.unpack_from("<f", data, W + rel)[0]) for rel in range(lo, hi, 4)]
names = sorted(k[0] for k in keys); info = {k[0]: k for k in keys}
pinned = {}
for rel, v in slots:
    for n, van, mod in keys:
        if mod != van and v == f32(mod): pinned[rel] = n
# walk names alphabetically, assigning slots between pins by vanilla value
out = {}; si = 0
for n in names:
    van, mod = info[n][1], info[n][2]
    pin = next((r for r, pn in pinned.items() if pn == n), None)
    if pin is not None:
        out[n] = (pin, "tagged"); si = [r for r, _ in slots].index(pin) + 1; continue
    for j in range(si, min(si + 6, len(slots))):
        if slots[j][1] in (f32(van), f32(mod)):
            out[n] = (slots[j][0], "by-value"); si = j + 1; break
base = min(r for r, _ in out.values())
for n in names:
    if n in out:
        r, how = out[n]; print(f"0x{r - base:03x}  {n:45s} {how}")
    else:
        print(f"  ?    {n:45s} not found")
print(f"# struct base = anchor{base:+#x}; {len(out)}/{len(names)} mapped")
