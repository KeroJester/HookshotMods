#!/usr/bin/env python3
"""Map a Param Tuner dump (overlay/param_dump_<anchor>.bin, anchor +-0x2000) to PlayerGameParam names.
usage: analyze_dump.py <dump.bin> [--all]
Prints offset (from anchor), value, and the setting names whose vanilla or mod value matches."""
import os, re, struct, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
hdr = open(os.path.join(ROOT, "overlay", "player_param_keys.h")).read()
keys = [(m[0], float(m[1]), float(m[3])) for m in
        re.findall(r'\{"([^"]+)", ([-0-9.e+inf]+)f, (true|false), ([-0-9.e+inf]+)f\}', hdr)]
f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
by = {}
for n, v, m in keys:
    by.setdefault(f32(v), []).append((n, "vanilla"))
    if m != v: by.setdefault(f32(m), []).append((n, "mod"))
data = open(sys.argv[1], "rb").read()
W = len(data) // 2
show_all = "--all" in sys.argv
named = 0
for o in range(0, len(data) - 3, 4):
    (val,) = struct.unpack_from("<f", data, o)
    hits = by.get(val, []) if val not in (0.0, 1.0) else []
    if hits or show_all:
        rel = o - W
        tag = " byml?" if o and data[o - 1] == 0xD2 else ""
        print(f"{'-' if rel < 0 else '+'}{abs(rel):04x}  {val:12.6g}{tag}  " + ", ".join(f"{n}({k})" for n, k in hits))
        named += bool(hits)
print(f"# {named} slots match known values")
