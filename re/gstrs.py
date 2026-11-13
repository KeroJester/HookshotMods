#!/usr/bin/env python3
"""gstrs.py <guest addr hex>: the calls and the strings (adrp+add) in the guest function around an address, in order."""
import os, re, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
out = subprocess.run([sys.executable, os.path.join(here, "gdis.py"), sys.argv[1]], capture_output=True, text=True).stdout
d = open(os.path.join(os.environ.get("HOOKSHOT_GAME_DIR", "/home/justin/Games/Hookshot"), "image.bin"), "rb").read()
def cstr(a):
    o = a - 0x80000000
    if not 0 <= o < len(d): return None
    e = d.find(b"\0", o); s = d[o:e]
    try: s = s.decode()
    except UnicodeDecodeError: return None
    return s if s.isprintable() and 2 < len(s) < 80 else None
regs = {}
for l in out.splitlines():
    l = re.sub(r" <_binary_image_bin_start[^>]*>", "", l)
    if l.startswith(";"): print(l); continue
    m = re.search(r"adrp\s+(x\d+), (0x[0-9a-f]+)", l)
    if m: regs[m.group(1)] = int(m.group(2), 16); continue
    m = re.search(r"add\s+(x\d+), (x\d+), #(0x[0-9a-f]+)", l)
    if m and m.group(2) in regs:
        s = cstr(regs[m.group(2)] + int(m.group(3), 16))
        if s: print(f"{l.split(':')[0]}  str {s!r}")
        continue
    m = re.search(r"\b(bl|blr)\s+(\S+)", l)
    if m: print(f"{l.split(':')[0]}  {m.group(1)} {m.group(2)}")
