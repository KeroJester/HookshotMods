"""node_vtable.py <image.bin> <NodeName>...: AINB native node name -> getName function -> vtable(s) -> slots."""
import re, sys, numpy as np
BASE = 0x80000000
raw = open(sys.argv[1], "rb").read()
w = np.frombuffer(raw[:len(raw) // 4 * 4], dtype='<u4')
q = np.frombuffer(raw[:len(raw) // 8 * 8], dtype='<u8')
for name in sys.argv[2:]:
    for m in re.finditer(re.escape(name.encode()) + b"\0", raw):
        s = BASE + m.start()
        if raw[m.start() - 1] not in (0,):           # must be a whole string
            continue
        page, off = s & ~0xFFF, s & 0xFFF
        # adrp x0, page ; add x0, x0, off ; ret
        for i in np.nonzero((w & 0x9F00001F) == 0x90000000)[0]:
            ins = int(w[i]); pc = BASE + i * 4
            imm = ((ins >> 5) & 0x7FFFF) << 2 | ((ins >> 29) & 3)
            if imm & (1 << 20): imm -= 1 << 21
            if (pc & ~0xFFF) + (imm << 12) != page: continue
            if int(w[i + 1]) != (0x91000000 | (off << 10)) or int(w[i + 2]) != 0xD65F03C0: continue
            getter = pc
            slots = np.nonzero(q == getter)[0]
            for sl in slots:
                vt = BASE + sl * 8
                print(f"{name}: string {s:#x} getName {getter:#x} in table at {vt:#x}")
                print("   ", " ".join(f"{int(x):#x}" for x in q[sl - 6: sl + 14]))
