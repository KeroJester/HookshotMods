"""nn::util::ResDic (the "_DIC" radix tree in BFRES / BNTX / BFEVFL): build(names) -> nodes [ref, left, right, key];
node 0 is the root (ref -1). write(buf, at, names, name_ptrs) stores the nodes {i32 ref, u16 left, u16 right, u64
name} after the 8-byte header ("_DIC", u32 count) at the given offset. Checked against the game's dictionaries (icon work)."""
import struct
def bit(key, i):
    if i < 0: return 0
    j = i >> 3
    if j >= len(key): return 0
    return (key[len(key) - 1 - j] >> (i & 7)) & 1
def first_diff(a, b):
    n = max(len(a), len(b)) * 8
    for i in range(n):
        if bit(a, i) != bit(b, i): return i
    return -1
def build(names):
    # nodes: [ref, left, right, key]
    nodes = [[-1, 0, 0, b'']]
    for key in names:
        # search
        p = 0; c = nodes[0][1]
        while nodes[c][0] > nodes[p][0]:
            p = c; c = nodes[c][2] if bit(key, nodes[c][0]) else nodes[c][1]
        crit = first_diff(key, nodes[c][3])
        # insertion point
        p = 0; c = nodes[0][1]; side = 1
        while nodes[c][0] > nodes[p][0] and nodes[c][0] < crit:
            p = c
            if bit(key, nodes[c][0]): c = nodes[c][2]; side = 2
            else: c = nodes[c][1]; side = 1
        n = len(nodes)
        if bit(key, crit): nodes.append([crit, c, n, key])
        else: nodes.append([crit, n, c, key])
        if p == 0: nodes[0][1] = n
        else: nodes[p][side] = n
    return nodes


def write(buf, at, names, name_ptrs, root_name_ptr):
    """names: list of bytes; name_ptrs: their string pointers; root_name_ptr: the empty string's pointer."""
    nodes = build(names)
    struct.pack_into("<4sI", buf, at, b"_DIC", len(names))
    for i, (ref, l, r, key) in enumerate(nodes):
        p = root_name_ptr if i == 0 else name_ptrs[i - 1]
        struct.pack_into("<iHHQ", buf, at + 8 + 16 * i, ref, l, r, p)


def size(n):
    return 8 + 16 * (n + 1)
