#!/usr/bin/env python3
"""bl_xref.py <image.bin> <hex target>...: BL/B call sites of guest functions (image at 0x80000000)."""
import sys, numpy as np
BASE = 0x80000000
d = np.fromfile(sys.argv[1], dtype='<u4')
op = d & 0xFC000000
idx = np.nonzero((op == 0x94000000) | (op == 0x14000000))[0]
imm = (d[idx] & 0x03FFFFFF).astype(np.int64)
imm = np.where(imm & (1 << 25), imm - (1 << 26), imm)
tgt = BASE + idx.astype(np.int64) * 4 + imm * 4
for t in sys.argv[2:]:
    t = int(t, 16)
    for i in idx[tgt == t]:
        print(f"{t:#x} <- {'bl' if d[i] & 0x80000000 else 'b '} at {BASE + int(i) * 4:#x}")
