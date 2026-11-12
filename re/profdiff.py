"""profdiff.py <syms.txt> <A samples> <B samples>: guest functions on stacks (inclusive) in A but rare in B."""
import sys, bisect, collections
addrs, names = [], []
for line in open(sys.argv[1]):
    a, _t, n = line.split(); addrs.append(int(a, 16)); names.append(n)
_z = sorted(zip(addrs, names)); addrs = [a for a, _ in _z]; names = [n for _, n in _z]
def fn(off):
    i = bisect.bisect_right(addrs, off) - 1
    return names[i] if i >= 0 else '?'
def load(p):
    c = collections.Counter(); n = 0; threads = collections.Counter()
    for line in open(p):
        if '|' not in line: continue
        head, st = line.rstrip().split('|', 1)
        th = head.split(' ', 1)[1]
        n += 1
        seen = set()
        for fr in st.split(';'):
            if fr.startswith('hookshot+'):
                f = fn(int(fr[9:], 16))
                if f.startswith('f_') and f not in seen: seen.add(f); c[(th, f)] += 1
    return c, n
A, na = load(sys.argv[2]); B, nb = load(sys.argv[3])
rows = []
for k, v in A.items():
    b = B.get(k, 0)
    if v >= 8 and b * 4 < v: rows.append((v, b, k))
for v, b, k in sorted(rows, reverse=True)[:60]: print(f"{v:6d} {b:5d}  {k[0]:28s} {k[1]}")
