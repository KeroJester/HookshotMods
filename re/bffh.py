#!/usr/bin/env python3
"""Explore BFFH UI layouts (Nintendo.Ui.ResModels.LayoutResData) inside the game's Layout2 archive.
Header (Hookshot nx2/prompt_assets.py): +0x118 u64 pane-name table, +0x120 u64 pane-record table, +0x130 u32 count.
Pane record: rotation, +0x10 scale (2f), +0x1c translation, +0x28 parent index, +0x2c size (2f)."""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mod_manager import zstd_helper

def layouts(sarc_files):
    for name, data in sorted(sarc_files.items()):
        if data[:4] == b"BFFH":
            yield name, data

def panes(lay):
    names, recs = struct.unpack_from("<QQ", lay, 0x118)
    n = struct.unpack_from("<I", lay, 0x130)[0]
    out = []
    for k in range(n):
        p = struct.unpack_from("<Q", lay, names + 8 * k)[0]
        r = struct.unpack_from("<Q", lay, recs + 8 * k)[0]
        out.append((lay[p:lay.index(b"\0", p)].decode(), r))
    return out

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "/home/justin/Games/Hookshot/prompts/Common.blarc"
    files = zstd_helper.unpack_sarc_to_dict(open(src, "rb").read())
    want = sys.argv[2] if len(sys.argv) > 2 else None
    for name, lay in layouts(files):
        if want and want not in name and want.encode() not in lay:
            continue
        ps = panes(lay)
        print(f"{name}: {len(lay)} bytes, {len(ps)} panes, refs PaItemBtn_00 x{lay.count(b'PaItemBtn_00')}")
