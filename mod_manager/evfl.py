"""BFEVFL (event flowchart) reader / writer, version 0.4, little-endian, flowcharts only (no timelines).

Layout (all pointers are absolute file offsets, 8 bytes; each is listed in the relocation table):
  header 0x48: "BFEVFL\\0\\0", version, BOM, alignment shift, file-name offset (to the text), relocated flag, first block
               offset, RELT offset, file size, flowchart count, timeline count, flowchart pointer array, flowchart DIC,
               timeline pointer array, timeline DIC
  EVFL block:  "EVFL", string pool offset (relative), counts (actors, actions, queries, events, entry points), name,
               actors, events, entry point DIC, entry points
  actor 0x38:  name, secondary name, argument name, actions (array of string pointers), queries, params container,
               action count, query count, entry point index (0xffff none), cut number
  event 0x30:  name, type (0 action, 1 switch, 2 fork, 3 join, 4 sub flow), then by type:
               action   next, actor, actor action, params container
               switch   case count, actor, actor query, params container, cases {u32 value, u16 event, u16 pad}
               fork     fork count, join event, forks (u16 array)
               join     next
               sub flow next, params container, flowchart name, entry point name
               5 sub flow switch: case count, params container, cases {u32 exit label hash, u16 event}, flowchart
                 name, entry point name (the sub flow ends on a type 6 event, whose label picks the case)
               6 exit: u32 label hash at +0xc
  entry point 0x20: sub flow event indices (u16 array), variable DIC, variable defs, counts, main event
  container:   type, count, DIC, then the item pointers; item: type u8, pad u8, count u16, u32, DIC u64, value(s)
               types: 0 argument, 1 container, 2 int, 3 bool, 4 float, 5 string, 6 wstring, 7 int array, 8 bool array,
               9 float array, 10 string array, 11 wstring array, 12 actor identifier (two strings)
  DIC:         "DIC ", count, root + entries {i32 bit reference, u16 next[2], name pointer}: a radix tree over the
               key bits (nn::util::ResDic)
  STR pool:    "STR ", ..., count, then {u16 length, text, NUL} each aligned to 2 (pointers point at the length)
  RELT:        "RELT", own offset, section count; section {u64 0, u32 0, u32 data end, u32 first entry, u32 entries};
               entry {u32 offset, u32 flags}: bit i set = the 8 bytes at offset + 8 i hold a pointer

In memory a flowchart is plain Python data (see read_flowchart): easy to build by hand or to edit.
"""
import struct

NONE = 0xFFFF


class Reader:
    def __init__(self, d):
        self.d = d

    def u8(self, o): return self.d[o]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]
    def i32(self, o): return struct.unpack_from("<i", self.d, o)[0]
    def u64(self, o): return struct.unpack_from("<Q", self.d, o)[0]
    def f32(self, o): return struct.unpack_from("<f", self.d, o)[0]

    def string(self, o):
        """string at a pointer (to the u16 length)"""
        if not o:
            return None
        n = self.u16(o)
        return self.d[o + 2:o + 2 + n].decode("utf-8")

    def dic(self, o):
        """[names] in entry order (without the root)"""
        if not o:
            return []
        assert self.d[o:o + 4] == b"DIC ", hex(o)
        n = self.u32(o + 4)
        return [self.string(self.u64(o + 8 + 16 * (i + 1) + 8)) for i in range(n)]

    def container(self, o):
        """-> dict name -> value (ordered), or None"""
        if not o:
            return None
        typ, _pad, n = struct.unpack_from("<BBH", self.d, o)
        assert typ == 1, (hex(o), typ)
        names = self.dic(self.u64(o + 8))
        assert len(names) == n, (hex(o), names, n)
        out = {}
        for i in range(n):
            out[names[i]] = self.item(self.u64(o + 16 + 8 * i))
        return out

    def item(self, o):
        typ, _pad, n = struct.unpack_from("<BBH", self.d, o)
        v = o + 16
        if typ == 0:
            return ("argument", self.string(self.u64(v)))
        if typ == 1:
            return self.container(o)
        if typ == 2:
            return ("int", self.i32(v))
        if typ == 3:
            return ("bool", self.u32(v) != 0)
        if typ == 4:
            return ("float", self.f32(v))
        if typ == 5:
            return ("string", self.string(self.u64(v)))
        if typ == 7:
            return ("int_array", [self.i32(v + 4 * i) for i in range(n)])
        if typ == 8:
            return ("bool_array", [self.u32(v + 4 * i) != 0 for i in range(n)])
        if typ == 9:
            return ("float_array", [self.f32(v + 4 * i) for i in range(n)])
        if typ == 10:
            return ("string_array", [self.string(self.u64(v + 8 * i)) for i in range(n)])
        if typ == 12:
            return ("actor", (self.string(self.u64(v)), self.string(self.u64(v + 8))))
        raise ValueError(f"container item type {typ} at {o:#x}")


def read_flowchart(d: bytes):
    """-> {"name", "actors": [...], "events": [...], "entry_points": {name: {...}}}"""
    r = Reader(d)
    assert d[:6] == b"BFEVFL", d[:8]
    nflow, ntl = r.u16(0x20), r.u16(0x22)
    assert nflow == 1 and ntl == 0, (nflow, ntl)
    f = r.u64(r.u64(0x28))
    assert d[f:f + 4] == b"EVFL"
    na, _nact, _nq, ne, nep = struct.unpack_from("<5H", d, f + 0x10)
    name = r.string(r.u64(f + 0x20))
    pa, pe, pdic, pep = r.u64(f + 0x28), r.u64(f + 0x30), r.u64(f + 0x38), r.u64(f + 0x40)
    actors = []
    for i in range(na):
        o = pa + 0x38 * i
        nact, nq, ep, cut = struct.unpack_from("<HHHB", d, o + 0x30)
        pact, pq = r.u64(o + 0x18), r.u64(o + 0x20)
        actors.append(dict(
            name=r.string(r.u64(o)), secondary=r.string(r.u64(o + 8)), argument=r.string(r.u64(o + 0x10)),
            actions=[r.string(r.u64(pact + 8 * k)) for k in range(nact)],
            queries=[r.string(r.u64(pq + 8 * k)) for k in range(nq)],
            params=r.container(r.u64(o + 0x28)), entry_point=ep, cut=cut))
    events = []
    for i in range(ne):
        o = pe + 0x30 * i
        typ = r.u8(o + 8)
        e = dict(name=r.string(r.u64(o)))
        if typ == 0:
            nxt, actor, action = struct.unpack_from("<HHH", d, o + 0xa)
            e.update(type="action", next=nxt, actor=actor, action=action, params=r.container(r.u64(o + 0x10)))
        elif typ == 1:
            n, actor, query = struct.unpack_from("<HHH", d, o + 0xa)
            pc = r.u64(o + 0x18)
            e.update(type="switch", actor=actor, query=query, params=r.container(r.u64(o + 0x10)),
                     cases=[(r.u32(pc + 8 * k), r.u16(pc + 8 * k + 4)) for k in range(n)])
        elif typ == 2:
            n, join = struct.unpack_from("<HH", d, o + 0xa)
            pf = r.u64(o + 0x10)
            e.update(type="fork", join=join, forks=[r.u16(pf + 2 * k) for k in range(n)])
        elif typ == 3:
            e.update(type="join", next=r.u16(o + 0xa))
        elif typ == 4:
            e.update(type="sub_flow", next=r.u16(o + 0xa), params=r.container(r.u64(o + 0x10)),
                     flowchart=r.string(r.u64(o + 0x18)), entry_point=r.string(r.u64(o + 0x20)))
        elif typ == 5:
            n = r.u16(o + 0xa)
            pc = r.u64(o + 0x18)
            e.update(type="sub_flow_switch", params=r.container(r.u64(o + 0x10)),
                     cases=[(r.u32(pc + 8 * k), r.u16(pc + 8 * k + 4)) for k in range(n)],
                     flowchart=r.string(r.u64(o + 0x20)), entry_point=r.string(r.u64(o + 0x28)))
        elif typ == 6:
            e.update(type="exit", label=r.u32(o + 0xc))
        else:
            raise ValueError(f"event type {typ}")
        events.append(e)
    eps = {}
    for i, epname in enumerate(r.dic(pdic)):
        o = pep + 0x20 * i
        psub, pvdic, pvars = r.u64(o), r.u64(o + 8), r.u64(o + 0x10)
        nsub, nvars, main = struct.unpack_from("<HHH", d, o + 0x18)
        assert nvars == 0 and not pvars, "entry point variables are not supported"
        eps[epname] = dict(main=main, sub_flow_events=[r.u16(psub + 2 * k) for k in range(nsub)], _has_vdic=bool(pvdic))
    assert len(eps) == nep
    return dict(name=name, actors=actors, events=events, entry_points=eps)
