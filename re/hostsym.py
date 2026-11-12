import bisect, sys
"""hostsym.py <syms.txt> <hex offset>... : host offset in bin/hookshot -> containing f_<guest> function (+delta)"""
addrs, names = [], []
for line in open(sys.argv[1]):
    a, _t, n = line.split()
    addrs.append(int(a, 16)); names.append(n)
def lookup(off):
    i = bisect.bisect_right(addrs, off) - 1
    return f"{names[i]}+{off - addrs[i]:#x}" if i >= 0 else "?"
if __name__ == "__main__":
    for t in sys.argv[2:]:
        print(t, lookup(int(t, 16)))
