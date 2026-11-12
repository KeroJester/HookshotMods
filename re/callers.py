"""callers.py <image.bin> <hex target>...: BL / B call sites of guest functions (image at 0x80000000)."""
import sys, numpy as np
BASE = 0x80000000
d = np.fromfile(sys.argv[1], dtype='<u4')
pcs = BASE + np.arange(len(d), dtype=np.int64) * 4
for t in [int(x, 16) for x in sys.argv[2:]]:
    for opc, name in ((0x94000000, "bl"), (0x14000000, "b")):
        m = (d & 0xFC000000) == opc
        idx = np.nonzero(m)[0]
        imm = (d[idx].astype(np.int64) & 0x3FFFFFF)
        imm = np.where(imm & (1 << 25), imm - (1 << 26), imm)
        tgt = pcs[idx] + imm * 4
        for i in idx[tgt == t]:
            print(f"{t:#x} <- {name} at {BASE + i * 4:#x}")
