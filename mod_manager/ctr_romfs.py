#!/usr/bin/env python3
"""Extract the RomFS of a decrypted ("NoCrypto") Nintendo 3DS cartridge image (.3ds / .cci).

NCSD header @0x100 -> partition 0 (NCCH) -> RomFS (IVFC) -> level 3 = the file system:
  L3 header: u32 headerLen, dirHashOff, dirHashLen, dirMetaOff, dirMetaLen, fileHashOff, fileHashLen,
             fileMetaOff, fileMetaLen, fileDataOff (all relative to the L3 start)
  dir entry:  u32 parent, sibling, childDir, childFile, hashNext, nameLen, name (UTF-16LE)
  file entry: u32 parent, sibling, u64 dataOff, u64 dataSize, u32 hashNext, nameLen, name (UTF-16LE)
usage: python3 -m mod_manager.ctr_romfs <rom.3ds> <out_dir>
"""

import os
import struct
import sys

NONE = 0xFFFFFFFF


def open_romfs(path):
    f = open(path, "rb")
    hdr = f.read(0x200)
    assert hdr[0x100:0x104] == b"NCSD", "not an NCSD (.3ds/.cci) image"
    part_off = struct.unpack_from("<I", hdr, 0x120)[0] * 0x200
    f.seek(part_off)
    ncch = f.read(0x200)
    assert ncch[0x100:0x104] == b"NCCH"
    if not ncch[0x18F] & 4:
        raise RuntimeError("this NCCH is encrypted; a decrypted image is required")
    romfs_off = struct.unpack_from("<I", ncch, 0x1B0)[0] * 0x200
    base = part_off + romfs_off
    f.seek(base)
    ivfc = f.read(0x60)
    assert ivfc[:4] == b"IVFC"
    master_hash = struct.unpack_from("<I", ivfc, 0x08)[0]
    l3_bs = struct.unpack_from("<I", ivfc, 0x4C)[0]          # level 3 block size (log2)
    l3 = base + ((0x60 + master_hash + (1 << l3_bs) - 1) & ~((1 << l3_bs) - 1))
    return f, l3


def extract(path, out_dir, want=None, log=print):
    """Extract every file (or only those whose path contains one of the strings in `want`)."""
    f, l3 = open_romfs(path)
    f.seek(l3)
    h = struct.unpack("<10I", f.read(0x28))
    dir_meta_off, dir_meta_len, file_meta_off, file_meta_len, data_off = h[3], h[4], h[7], h[8], h[9]
    f.seek(l3 + dir_meta_off)
    dmeta = f.read(dir_meta_len)
    f.seek(l3 + file_meta_off)
    fmeta = f.read(file_meta_len)
    count = total = 0

    def name_at(buf, off, fixed):
        n = struct.unpack_from("<I", buf, off + fixed - 4)[0]
        return buf[off + fixed:off + fixed + n].decode("utf-16-le")

    def walk_dir(doff, rel):
        nonlocal count, total
        _parent, _sib, child_dir, child_file, _hn, _nl = struct.unpack_from("<6I", dmeta, doff)
        fo = child_file
        while fo != NONE:
            _p, sib, d_off, d_size, _hn2, nl = struct.unpack_from("<IIQQII", fmeta, fo)
            name = fmeta[fo + 0x20:fo + 0x20 + nl].decode("utf-16-le")
            rp = f"{rel}/{name}" if rel else name
            if want is None or any(w in rp for w in want):
                op = os.path.join(out_dir, rp)
                os.makedirs(os.path.dirname(op), exist_ok=True)
                f.seek(l3 + data_off + d_off)
                with open(op, "wb") as o:
                    remaining = d_size
                    while remaining:
                        chunk = f.read(min(remaining, 1 << 22))
                        o.write(chunk)
                        remaining -= len(chunk)
                count += 1
                total += d_size
            fo = sib
        co = child_dir
        while co != NONE:
            _p, sib, _cd, _cf, _hn3, nl = struct.unpack_from("<6I", dmeta, co)
            name = dmeta[co + 0x18:co + 0x18 + nl].decode("utf-16-le")
            walk_dir(co, f"{rel}/{name}" if rel else name)
            co = sib

    walk_dir(0, "")
    log(f"extracted {count} files, {total / 1e6:.1f} MB -> {out_dir}")
    return count


if __name__ == "__main__":
    extract(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
