# build_ct_bgm.py: Clock Town's music (MM3D's own stream) into the test mod (RESEARCH_NOTES.md §80q).
#   python3 build_ct_bgm.py [mod folder name]       (after build_dev.py, which empties the mod folder)
# How the game picks a map's music: GameBancParam.SoundSceneryName is the Label of a Sound/Scenery param (Bootup
# pack); its BgmName is written to a global BGM property, and the bgm.byml BGM whose StartCondition matches it plays
# its node graph; an Asset node's Key is a BGM_Static.bars entry / Stream/<Key>.bwav.
# An Asset node's Key must be an asset of the BGM's SLink2 user (a key the user lacks opens nothing).
# Here: a new BGM "BGM_ClockTown" (a copy of BGM_ZoraDomain: one looping asset node) on SLink2 user BGM_LonLonRanch,
# whose asset "BGM_LonLonRanch" no graph plays; that BARS entry and stream carry MM3D's 029NA_BGM_MARKET (its
# DSP-ADPCM copied as it is: 32 kHz, its own coefficients and loop); a scenery "ClockTown" in the Bootup pack and in
# the AltoConfig's scenery list; the map's SoundSceneryName set to its label.
import copy, os, struct, sys, zlib
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser('~/Projects/hookshot-mods'))
from rf import rf, ROMFS
from mod_manager.byml import parse_byml, write_byml
from mod_manager.zstd_helper import compress_zs

GAME = os.path.expanduser(os.environ.get('HOOKSHOT_GAME_DIR', '~/Games/Hookshot'))
MOD = os.path.join(GAME, 'mods', sys.argv[1] if len(sys.argv) > 1 else '_070-ClockTownDev')
OUT = os.path.join(MOD, 'romfs')
MM = os.path.expanduser('~/Games/Hookshot/mods/_MajoraAssets/extracted/mm3d_romfs/sound/streaming/stream/')
BANC = os.environ.get('DEV_BANC', 'ClockTownDev')
BGM = os.environ.get('CT_BGM', 'BGM_ClockTown')
KEY = os.environ.get('CT_BGM_KEY', 'BGM_LonLonRanch')    # (an asset of USER that no graph plays)
SRC = os.environ.get('CT_BGM_SRC', '029NA_BGM_MARKET')
USER = os.environ.get('CT_BGM_USER', 'BGM_LonLonRanch')    # the SLink2 user whose asset KEY is (keys resolve there)
TEMPLATE = 'BGM_ZoraDomain'
VOLUME = float(os.environ.get('CT_BGM_VOLUME', '0.13'))   # (MM3D's stream RMS ~5500; the game's town streams ~700)
SCENERY_FROM = 'KakarikoVillage_Child'
LABEL = os.environ.get('CT_SCENERY_LABEL', '時計じかけの町')     # (Clock Town's Japanese name)


def put(rel, data, zs=False):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(compress_zs(data, rel, ROMFS) if zs else data)
    print('  +', rel, len(data))


# ---- MM3D's stream: CSTM (INFO: stream info, channel info -> DSP-ADPCM info; DATA: blocks interleaved per channel)
def read_cstm(d):
    assert d[:4] == b'CSTM'
    nblk = struct.unpack_from('<H', d, 0x10)[0]
    blocks = {}
    for i in range(nblk):
        t, o, s = struct.unpack_from('<H2xII', d, 0x14 + 12 * i)
        blocks[t] = (o, s)
    info = blocks[0x4000][0]
    b = info + 8
    _t, si = struct.unpack_from('<H2xI', d, b)
    _t, ci = struct.unpack_from('<H2xI', d, b + 16)
    s = b + si
    codec, loop, nch = d[s], d[s + 1], d[s + 2]
    rate, loop_start, loop_end, nblocks, bsize, bsamp, lsize, lsamp, lpad = struct.unpack_from('<9I', d, s + 4)
    assert codec == 2, f'codec {codec}'
    t = b + ci
    assert struct.unpack_from('<I', d, t)[0] == nch
    chans = []
    for c in range(nch):
        _t, co = struct.unpack_from('<H2xI', d, t + 4 + 8 * c)
        cinfo = t + co
        _t, ao = struct.unpack_from('<H2xI', d, cinfo)
        a = cinfo + ao
        coefs = struct.unpack_from('<16h', d, a)
        ps, yn1, yn2, lps, lyn1, lyn2 = struct.unpack_from('<HhhHhh', d, a + 32)
        chans.append(dict(coefs=coefs, ps=ps, yn1=yn1, yn2=yn2, lps=lps, lyn1=lyn1, lyn2=lyn2, data=bytearray()))
    data = blocks[0x4002][0] + 0x20
    o = data
    for k in range(nblocks):
        last = k == nblocks - 1
        size, step = (lsize, lpad) if last else (bsize, bsize)
        for c in chans:
            c['data'] += d[o:o + size]
            o += step
    samples = loop_end
    for c in chans:
        c['data'] = bytes(c['data'][:(samples + 13) // 14 * 8])
    return dict(rate=rate, loop=bool(loop), loop_start=loop_start, samples=samples, chans=chans)


def make_bwav(st):
    """-> (stream file, prefetch BWAV for the BARS: header only, as the game's BGM entries)"""
    nch = len(st['chans'])
    head = 0x10 + 0x4c * nch
    off = (head + 0x3f) & ~0x3f
    offs, body = [], b''
    for c in st['chans']:
        offs.append(off + len(body))
        blob = c['data'] + b'\0' * (-len(c['data']) & 0x3f)
        body += blob
    crc = zlib.crc32(body) & 0xFFFFFFFF
    def header(prefetch):
        h = bytearray(b'BWAV' + struct.pack('<HHIHH', 0xFEFF, 1, crc, 1 if prefetch else 0, nch))
        for i, c in enumerate(st['chans']):
            n = st['samples']
            h += struct.pack('<HHIII', 1, i if nch > 1 else 2, st['rate'], n, 0 if prefetch else n)
            h += struct.pack('<16h', *c['coefs'])
            h += struct.pack('<IIIII', offs[i], off if prefetch else offs[i], 1 if st['loop'] else 0, n,
                             st['loop_start'] if st['loop'] else 0)
            h += struct.pack('<HhhH', c['lps'], c['lyn1'], c['lyn2'], 0)
        h += b'\0' * (off - len(h))
        return bytes(h)
    return header(False) + body, header(True)


# ---- the BARS ("BARS", size, BOM, 0x0102, count; CRCs ascending; (AMTA, BWAV) offsets; AMTAs; BWAVs 64-aligned)
def parse_bars(b):
    n = struct.unpack_from('<I', b, 0xc)[0]
    crcs = struct.unpack_from(f'<{n}I', b, 0x10)
    pr = struct.unpack_from(f'<{2 * n}I', b, 0x10 + 4 * n)
    ends = sorted(set(pr) | {len(b)})
    es = []
    for i in range(n):
        a, w = pr[2 * i], pr[2 * i + 1]
        es.append([crcs[i], b[a:a + struct.unpack_from('<I', b, a + 8)[0]], b[w:ends[ends.index(w) + 1]]])
    return es


def write_bars(es):
    es = sorted(es, key=lambda e: e[0])
    n = len(es)
    out = bytearray(0x10 + 12 * n)
    out[:4] = b'BARS'
    struct.pack_into('<HHI', out, 8, 0xFEFF, 0x0102, n)
    for i, (crc, amta, _w) in enumerate(es):
        struct.pack_into('<I', out, 0x10 + 4 * i, crc)
        out += b'\0' * (-len(out) & 3)
        struct.pack_into('<I', out, 0x10 + 4 * n + 8 * i, len(out))
        out += amta
    for i, (_c, _a, bw) in enumerate(es):
        out += b'\0' * (-len(out) & 0x3f)
        struct.pack_into('<I', out, 0x10 + 4 * n + 8 * i + 4, len(out))
        out += bw
    struct.pack_into('<I', out, 4, len(out))
    return bytes(out)


def make_amta(templ, name):
    """The game's small BGM AMTA (0x50 bytes of header, then the name): name, size and the name's CRC."""
    assert len(templ) == (0x50 + len(TEMPLATE) + 1 + 3) & ~3 and templ[0x50:0x50 + len(TEMPLATE)] == TEMPLATE.encode()
    a = bytearray(templ[:0x50]) + name.encode() + b'\0'
    a += b'\0' * (-len(a) & 3)
    struct.pack_into('<I', a, 8, len(a))
    struct.pack_into('<I', a, 0x28, zlib.crc32(name.encode()))
    return bytes(a)


def cond(name, op):   # the BGM condition "global property 10 == name" (op 6) / "!= name" (op 7), as the game's
    body = b'\x08\x00\x01\n' + bytes([op]) + b'\x01\x02\x00\t\x01\n\x00\n\x00\x04\x00' + name.encode() + b'\0'
    return b'C1' + struct.pack('<H', 4 + len(body)) + body


print('Clock Town BGM ->', OUT)
st = read_cstm(open(MM + SRC + '.dspadpcm.bcstm', 'rb').read())
print(f'  {SRC}: {len(st["chans"])} ch, {st["rate"]} Hz, {st["samples"]} samples, loop {st["loop"]} from {st["loop_start"]}')
stream, prefetch = make_bwav(st)
put(f'Sound/Resource/Stream/{KEY}.bwav', stream)

bars = parse_bars(rf('Sound/Resource/BGM_Static.bars.zs'))
tcrc = zlib.crc32(TEMPLATE.encode())
templ = [e for e in bars if e[0] == tcrc][0]
bars = [e for e in bars if e[0] != zlib.crc32(KEY.encode())] + [[zlib.crc32(KEY.encode()), make_amta(templ[1], KEY), prefetch]]
put('Sound/Resource/BGM_Static.bars.zs', write_bars(bars), zs=True)

bgm = parse_byml(rf('Sound/Bgm/bgm.Product.111.byml.zs'))
e = copy.deepcopy(bgm['BGM'][TEMPLATE])
e['StartConditionBin'] = cond(BGM, 6)
e['StopConditionBin'] = cond(BGM, 7)
e['SLinkUserName'] = USER
for n in e['Node']:
    for t in n.get('Transition', []):
        if 'ConditionBin' in t: t['ConditionBin'] = cond(BGM, 6)
    if n.get('Type') == 'Asset':
        n['Key'] = KEY
        n['NodeVolume'] = VOLUME
bgm['BGM'][BGM] = e
bgm['BGM'] = dict(sorted(bgm['BGM'].items()))
def add_player(h):
    for x in h:
        if x.get('Name') == 'Field' and any(p.get('Name') == TEMPLATE for p in x.get('Underlayer', [])):
            if not any(p.get('Name') == BGM for p in x['Underlayer']):
                x['Underlayer'].append({'Name': BGM, 'Type': 'Player'})
            return True
        if add_player(x.get('Underlayer', [])): return True
    return False
assert add_player(bgm['Hierarchy'])
put('Sound/Bgm/bgm.Product.111.byml.zs', write_byml(bgm), zs=True)

# ---- the scenery (a loose file next to the Bootup pack's: Kakariko's with the BGM and label)
boot = rf('Pack/Bootup.Nin_Ounce_NVN2.pack.zs')
hl, _bom, _size, dstart = struct.unpack_from('<HHII', boot, 4)
n = struct.unpack_from('<H', boot, 0x14 + 6)[0]
names = boot[0x14 + 12 + 16 * n + 8:dstart]
sc = None
for i in range(n):
    _h, attr, s0, e0 = struct.unpack_from('<IIII', boot, 0x14 + 12 + 16 * i)
    no = (attr & 0xffff) * 4
    nm = names[no:names.find(b'\0', no)].decode()
    if nm == f'Sound/Scenery/{SCENERY_FROM}.game__sound__SceneryParam.bgyml':
        sc = parse_byml(boot[dstart + s0:dstart + e0])
sc = {k: sc[k] for k in ('$parent', 'BaseAmbientSound') if k in sc}
sc['BgmName'] = BGM
sc['Label'] = LABEL
SC_REL = 'Sound/Scenery/ClockTown.game__sound__SceneryParam.bgyml'


def sarc_add(d, name, data):
    """One file added to a SARC; every other file keeps its offset from the data start, and the data start moves by
    a multiple of 16 (the pack's files are 8/16-aligned), so nothing else changes."""
    from mod_manager.zstd_helper import sarc_hash
    dstart = struct.unpack_from('<I', d, 0xc)[0]
    n = struct.unpack_from('<H', d, 0x1a)[0]
    nodes = [list(struct.unpack_from('<IIII', d, 0x20 + 16 * i)) for i in range(n)]
    sfnt = 0x20 + 16 * n
    strs = bytearray(d[sfnt + 8:dstart])
    strs = strs.rstrip(b'\0') + b'\0'                           # (the last name, without the padding before the data)
    strs += b'\0' * (-len(strs) & 3)
    name_off = len(strs)
    strs += name.encode() + b'\0'
    strs += b'\0' * (-len(strs) & 3)
    old_data = d[dstart:]
    start = len(old_data) + (-len(old_data) & 7)
    h = sarc_hash(name)
    assert all(x[0] != h for x in nodes)
    nodes.append([h, 0x01000000 | (name_off // 4), start, start + len(data)])
    nodes.sort(key=lambda x: x[0])
    head = 0x20 + 16 * len(nodes) + 8 + len(strs)
    new_start = dstart + ((head - dstart + 15) // 16) * 16 if head > dstart else dstart
    out = bytearray(d[:0x14])
    out += struct.pack('<4sHHI', b'SFAT', 0x0C, len(nodes), struct.unpack_from('<I', d, 0x1c)[0])
    for x in nodes: out += struct.pack('<IIII', *x)
    out += struct.pack('<4sHH', b'SFNT', 8, 0) + strs
    out += b'\0' * (new_start - len(out))
    out += old_data + b'\0' * (start - len(old_data)) + data
    struct.pack_into('<II', out, 8, len(out), new_start)
    return bytes(out)


boot2 = sarc_add(boot, SC_REL, write_byml(sc))
put('Pack/Bootup.Nin_Ounce_NVN2.pack.zs', boot2, zs=True)
print(f'  Bootup pack: {len(boot)} -> {len(boot2)} bytes (+{SC_REL})')

# the sound system's list of sceneries (Sound/Product.111.alto__AltoConfig.bgyml, loose): ours added
alto = parse_byml(open(os.path.join(ROMFS, 'Sound/Product.111.alto__AltoConfig.bgyml'), 'rb').read())
sc_work = 'Work/' + SC_REL.replace('.bgyml', '.gyml')
if sc_work not in alto['Scenery']: alto['Scenery'].append(sc_work)
put('Sound/Product.111.alto__AltoConfig.bgyml', write_byml(alto))

p = os.path.join(OUT, f'Banc/GameBancParam/{BANC}.game__banc__GameBancParam.bgyml')
gbp = parse_byml(open(p, 'rb').read())
gbp['SoundSceneryName'] = LABEL
open(p, 'wb').write(write_byml(gbp))
print('  SoundSceneryName ->', LABEL)
