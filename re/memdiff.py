#!/usr/bin/env python3
"""Diff overlay tuner snapshots ("snap <path>": records of u64 guest address + 4096 bytes).

  memdiff.py s1.bin s2.bin s3.bin [--types u8,u16,s32,f32] [--equal-steps]

Prints addresses whose value strictly decreases from each snapshot to the next (a meter being spent),
optionally only those with equal steps (s1-s2 == s2-s3). Pages identical in all snapshots are skipped fast.
"""
import mmap
import sys

import numpy as np

REC = 8 + 4096


def index(path):
    f = open(path, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    n = len(mm) // REC
    addrs = np.frombuffer(mm, dtype=np.uint64, count=n * (REC // 8)).reshape(n, REC // 8)[:, 0].copy()
    return mm, {int(a): i for i, a in enumerate(addrs)}


def page(mm, i):
    return np.frombuffer(mm, dtype=np.uint8, count=4096, offset=i * REC + 8)


def main():
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    types = "u8,u16,s32,f32"
    equal = "--equal-steps" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--types="):
            types = a.split("=", 1)[1]
    snaps = [index(p) for p in paths]
    common = set(snaps[0][1])
    for _, ix in snaps[1:]:
        common &= set(ix)
    print(f"{len(common)} common pages", file=sys.stderr)
    dt = {"u8": np.uint8, "u16": np.uint16, "s32": np.int32, "f32": np.float32}
    hits = 0
    for a in sorted(common):
        pages = [page(mm, ix[a]) for mm, ix in snaps]
        if all(np.array_equal(pages[0], p) for p in pages[1:]):
            continue
        for t in types.split(","):
            vs = [p.view(dt[t]).astype(np.float64) for p in pages]
            ok = np.ones(vs[0].shape, bool)
            for x, y in zip(vs, vs[1:]):
                ok &= y < x
            if t == "f32":
                ok &= np.isfinite(vs[0]) & (np.abs(vs[0]) < 1e5) & (vs[-1] >= 0)
            if equal and len(vs) >= 3:
                ok &= np.isclose(vs[0] - vs[1], vs[1] - vs[2])
            for k in np.nonzero(ok)[0]:
                sz = np.dtype(dt[t]).itemsize
                print(f"{t} 0x{a + k * sz:x}: " + " -> ".join(f"{v[k]:g}" for v in vs))
                hits += 1
                if hits > 2000:
                    return


if __name__ == "__main__":
    main()
