"""adrp_xref.py <image.bin> <hex addr>...: ADRP(+ADD/LDR) references to an address (image at 0x80000000)."""
import sys, numpy as np
BASE = 0x80000000
d = np.fromfile(sys.argv[1], dtype='<u4')
idx = np.nonzero((d & 0x9F000000) == 0x90000000)[0]
w = d[idx].astype(np.int64)
imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
imm = np.where(imm & (1 << 20), imm - (1 << 21), imm)
pcs = BASE + idx.astype(np.int64) * 4
pages = (pcs & ~0xFFF) + (imm << 12)
for t in [int(x, 16) for x in sys.argv[2:]]:
    for i in idx[pages == (t & ~0xFFF)]:
        rd = int(d[i]) & 31
        for k in range(1, 6):
            n = int(d[i + k])
            if (n & 0xFFC00000) == 0x91000000 and ((n >> 5) & 31) == rd and ((n >> 10) & 0xFFF) == (t & 0xFFF):
                print(f"{t:#x} <- adrp/add at {BASE + i * 4:#x}"); break
