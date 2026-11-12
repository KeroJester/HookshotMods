#!/usr/bin/env python3
"""Build the "Song of Healing" mod: Majora's Mask's Song of Healing as a playable ocarina song.

The game's song list is a fixed native enum (13 types) and a song's whole presentation is looked up by type, so the
game presents the Song of Healing through Zelda's Lullaby's slot (the "host") while the overlay
(overlay/song_of_healing.inc) swaps what that slot shows and plays. This script writes:

  song_of_healing/song.cfg       notes (the game's note numbers), stream names
  song_of_healing/patches.bin    what to swap in the game's memory while the song is presented: the host replay's
                                 prefetch BWAV (header + first samples, inside Ocarina.bars) and the host's title text
  romfs/Sound/Resource/Stream/Ocarina_HealingSong.bwav   the replay stream, opened in place of the host's

The replay is MM3D's NA_BGM_OCA_CURE_SEQ (the Song of Healing replay: B A F, B A F, B A E D E at tempo 110, single
ocarina track) note for note: its timing, lengths and velocities, played with this game's own ocarina note recordings
(Ocarina_<pitch>.bwav), so it sounds like the notes Link just played, like this game's replays of its own songs.
Without the MM3D files it falls back to MM's playback table (code_8019AF00.c sOcarinaSongNotes[OCARINA_SONG_HEALING]).

Usage:
    python3 -m mod_manager.build_song_of_healing
"""

import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from mod_manager import zstd_helper  # noqa: E402
from mod_manager.bwav import decode_bwav, find_prefetch, replace_stream  # noqa: E402
from mod_manager.core import HookshotModManager  # noqa: E402

MOD_NAME = "SongOfHealing"
MOD_VERSION = "0.2.0"
MOD_DESCRIPTION = "Majora's Mask's Song of Healing as a playable ocarina song (work in progress)"
STREAM = "Sound/Resource/Stream"
RATE = 48000

# The game's note numbers (OcarinaMgr note history: 1 + index into its pitch table LowB..HighF).
# Controller: A = A, X = HighD, Y = B, L = D, R = F (MM: C-right, C-up, C-left, A, C-down).
NOTE_NUMBER = {"D": 4, "F": 7, "A": 11, "B": 13, "HighD": 16}
# MM: OCARINA_SONG_HEALING = C-left C-right C-down C-left C-right C-down = B A F B A F
SONG_NOTES = ["B", "A", "F", "B", "A", "F"]

# The host slot: Zelda's Lullaby (MelodyType 0). Its title has the same length as ours, which lets the title be
# swapped in place in the loaded message file.
HOST_STREAM = "Ocarina_ZeldaLullaby"
OUR_STREAM = "Ocarina_HealingSong"            # (not longer than the host's name: the overlay rewrites the path in place)
HOST_TITLE_LABEL = "Title_00"                 # LayoutMsg/Ocarina_00.msbt
TITLE = "Song of Healing"
TITLE_ARTICLE = 1                             # the title tag's article byte: 0 none ("Zelda's Lullaby"), 1 "the"
MESSAGE_ARCHIVES = ["Mals/USen.Product.110.sarc.zs", "Mals/EUen.Product.110.sarc.zs"]

# MIDI key (MM's sequence key, the ocarina sounds an octave up) -> this game's note recording
KEY_TO_SAMPLE = {62: "D", 64: "E", 65: "F", 67: "G", 69: "A", 71: "B", 72: "C", 74: "HighD", 76: "HighE"}

# MM (N64) sOcarinaSongNotes[OCARINA_SONG_HEALING]: pitch, length (1/60 s), volume
MM_PLAYBACK = [(71, 32, 88), (69, 33, 88), (65, 33, 69), (71, 32, 94), (69, 33, 88), (65, 121, 86)]


def find_or_create_mod(mgr):
    for m in mgr.list_mods():
        if m.folder_name.split("-", 1)[-1] == MOD_NAME:
            return m.base_path
    folder = mgr.create_mod(MOD_NAME, description=MOD_DESCRIPTION, category="Gameplay", version=MOD_VERSION)
    return os.path.join(mgr.mods_dir, folder)


def mono(romfs, name):
    rate, ch = decode_bwav(open(os.path.join(romfs, STREAM, name + ".bwav"), "rb").read())
    assert rate == RATE, (name, rate)
    return np.mean([c.astype(np.float32) / 32768.0 for c in ch], axis=0)


def mm3d_events(log):
    """MM3D's replay note events [(start s, key, velocity, length s)], or None without the MM3D files."""
    try:
        from mod_manager import cseq, mm3d
        from mod_manager.csar import CSAR
    except Exception as e:  # pragma: no cover
        log(f"  (no MM3D tools: {e})")
        return None
    path = os.path.join(mm3d.ROMFS, "sound", "sequence", "JokerSound.bcsar")
    if not os.path.exists(path):
        log(f"  (no {path})")
        return None
    sar = CSAR(open(path, "rb").read())
    s = sar._table(0x2100)[sar.items["NA_BGM_OCA_CURE_SEQ"] & 0xFFFFFF]
    r = sar.r
    file_id = r.u32(s)
    _t, off = r.ref(s + 0xC)
    det = s + off
    _t, bt = r.ref(det)
    bt += det
    start = r.u32(det + 0x10)
    banks = [cseq.Bank(sar, b & 0xFFFFFF) if (b >> 24) == 3 else None
             for b in [r.u32(bt + 4 + 4 * i) for i in range(r.u32(bt))]]
    seq = cseq.Sequencer(sar, file_id, banks)
    seq.render(start, 12.0)
    return [(t, key, vel, length) for (t, _trk, _bank, _prg, key, vel, length, _vol) in seq.events]


def render_playback(romfs, events, log):
    """Each note: this game's recording of that pitch from its attack, held for the note's length, then released
    the way the game's own notes end (a short fade). -> float32 (n, 2)"""
    samples = {}
    total = max(t + l for t, _k, _v, l in events) + 0.6
    out = np.zeros(int(total * RATE) + 1, np.float32)
    release = int(0.12 * RATE)
    for t, key, vel, length in events:
        name = KEY_TO_SAMPLE[key]
        if name not in samples:
            samples[name] = mono(romfs, "Ocarina_" + name)
        src = samples[name]
        n = min(len(src), int(length * RATE) + release)
        x = src[:n].copy()
        hold = int(length * RATE)
        if n > hold:                                     # release
            x[hold:] *= np.exp(-np.arange(n - hold) / (release / 4.0))
        x[:64] *= np.linspace(0.0, 1.0, 64)               # declick
        a = int(t * RATE)
        out[a:a + n] += x * (vel / 127.0)
    # level: the game's own replays sit about 1.33x a held note (Ocarina_SariaSong vs
    # Ocarina_B, RMS of the sustained notes); 1.18 on this whole-clip RMS gives that (measured in the game's output)
    ref = np.sqrt(np.mean(mono(romfs, "Ocarina_B")[:RATE] ** 2))
    loud = np.sqrt(np.mean(out[out != 0] ** 2)) if np.any(out) else 1.0
    out *= 1.18 * ref / max(loud, 1e-6)
    peak = float(np.abs(out).max())
    if peak > 0.98:
        out *= 0.98 / peak
    log(f"  replay: {len(events)} notes, {len(out) / RATE:.2f} s (peak {min(peak, 0.98):.2f})")
    return np.stack([out, out], axis=1)


def msbt_raw(b, label):
    """(raw text bytes of `label`, the 16 bytes that follow it) from an MSBT."""
    assert b[:8] == b"MsgStdBn"
    nsec = struct.unpack_from("<H", b, 0xe)[0]
    o, secs = 0x20, {}
    for _ in range(nsec):
        size = struct.unpack_from("<I", b, o + 4)[0]
        secs[b[o:o + 4]] = b[o + 0x10:o + 0x10 + size]
        o = (o + 0x10 + size + 15) & ~15
    lbl, index = secs[b"LBL1"], None
    for i in range(struct.unpack_from("<I", lbl, 0)[0]):
        cnt, p = struct.unpack_from("<II", lbl, 4 + i * 8)
        for _ in range(cnt):
            n = lbl[p]
            if lbl[p + 1:p + 1 + n].decode() == label:
                index = struct.unpack_from("<I", lbl, p + 1 + n)[0]
            p += 1 + n + 4
    txt = secs[b"TXT2"]
    cnt = struct.unpack_from("<I", txt, 0)[0]
    offs = list(struct.unpack_from(f"<{cnt}I", txt, 4)) + [len(txt)]
    return txt[offs[index]:offs[index + 1]], txt[offs[index + 1]:offs[index + 1] + 16]


def title_patches(romfs, log):
    """[(name, find, offset, replace)]: the host's title text (a 0x0E tag whose data starts with the article byte
    pair, then the UTF-8 name) -> ours, for each English message archive."""
    out, seen = [], set()
    for rel in MESSAGE_ARCHIVES:
        p = os.path.join(romfs, rel)
        if not os.path.exists(p):
            continue
        lang = os.path.basename(rel).split(".")[0]
        files = zstd_helper.unpack_sarc_to_dict(zstd_helper.decompress_zs(open(p, "rb").read(), p, romfs))
        raw, after = msbt_raw(files["LayoutMsg/Ocarina_00.msbt"], HOST_TITLE_LABEL)
        if raw in seen:
            continue
        seen.add(raw)
        assert raw[0] == 0x0E and raw[-1] == 0, raw
        size = struct.unpack_from("<H", raw, 5)[0]
        tag, name = bytearray(raw[:7 + size]), raw[7 + size:-1]
        new = TITLE.encode("utf-8")
        if len(new) != len(name):
            log(f"  title: {lang}: {name!r} and {TITLE!r} differ in length; not swapped")
            continue
        tag[8] = TITLE_ARTICLE
        out.append((f"title {lang}", raw + after, 0, bytes(tag) + new + b"\0"))
        log(f"  title: {name.decode()!r} -> {TITLE!r} ({lang})")
    return out


def marker_patch(bars, name, onsets, nsamples, log):
    """(name, find, 0, replace): the host replay's AMTA (sound metadata in the BARS) with its note markers moved to
    our notes. The markers (id 1.. in the marker table at +0x50: count, then {id, name offset, sample, length}, and again as a
    plain u32 list in MINF) time the staff notes and Link's fingering during the replay; the sample count (twice in
    MINF) is set too."""
    count = struct.unpack_from("<I", bars, 0xc)[0]
    table = 0x10 + 4 * count
    for i in range(count):
        a = struct.unpack_from("<I", bars, table + 8 * i)[0]
        size = struct.unpack_from("<I", bars, a + 8)[0]
        blob = bars[a:a + size]
        if blob[:4] == b"AMTA" and (name.encode() + b"\0") in blob and (b"Example" + name.encode()) not in blob:
            break
    else:
        raise ValueError(f"{name}: AMTA not found")
    new = bytearray(blob)
    n = struct.unpack_from("<I", blob, 0x50)[0]
    old = []
    for k in range(n):
        o = 0x54 + 16 * k
        mid, _name, pos, length = struct.unpack_from("<IIII", blob, o)
        if mid >= 1 and length == 0:
            old.append(pos)
            if mid - 1 < len(onsets):
                struct.pack_into("<I", new, o + 8, onsets[mid - 1])
    assert len(old) == len(onsets), (old, onsets)
    seq = struct.pack(f"<{len(old)}I", *old)
    at = blob.find(seq, 0x54 + 16 * n)
    assert at >= 0, "marker list not found in MINF"
    new[at:at + len(seq)] = struct.pack(f"<{len(onsets)}I", *onsets)
    host_n = struct.unpack_from("<I", blob, blob.find(b"MINF") + 0x18)[0]
    both = struct.pack("<II", host_n, host_n)
    at = blob.find(both)
    assert at >= 0
    new[at:at + 8] = struct.pack("<II", nsamples, nsamples)
    log(f"  note markers: {[round(x / RATE, 2) for x in old]} s -> {[round(x / RATE, 2) for x in onsets]} s")
    return ("replay note markers", bytes(blob), 0, bytes(new))


def write_patches(path, patches):
    with open(path, "wb") as f:
        f.write(b"SOHP" + struct.pack("<I", len(patches)))
        for name, find, off, repl in patches:
            f.write(name.encode()[:31].ljust(32, b"\0"))
            f.write(struct.pack("<I", len(find)) + find + struct.pack("<II", off, len(repl)) + repl)


def build():
    mgr = HookshotModManager()
    romfs = mgr.romfs_dir
    lines = []
    log = lambda s: (print(s), lines.append(s))  # noqa: E731
    log(f"== Song of Healing build (from vanilla {romfs})")
    mod_dir = find_or_create_mod(mgr)
    out = os.path.join(mod_dir, "song_of_healing")
    os.makedirs(out, exist_ok=True)

    events = mm3d_events(log)
    if events:
        log("  replay notes: MM3D NA_BGM_OCA_CURE_SEQ")
    else:
        log("  replay notes: MM sOcarinaSongNotes[OCARINA_SONG_HEALING] (MM3D files not found)")
        events, t = [], 0.0
        for key, frames, vol in MM_PLAYBACK:
            events.append((t, key, vol, frames / 60.0))
            t += frames / 60.0
    pcm = render_playback(romfs, events, log)
    chans = [(np.clip(pcm[:, k], -1.0, 1.0) * 32767).astype(np.int16) for k in range(2)]

    # the replay stream and its prefetch, in the host replay's own format
    host_stream = open(os.path.join(romfs, STREAM, HOST_STREAM + ".bwav"), "rb").read()
    p = os.path.join(romfs, "Sound/Resource/Ocarina.bars.zs")
    bars = zstd_helper.decompress_zs(open(p, "rb").read(), p, romfs)
    po, psize = find_prefetch(bars, host_stream)
    stream, prefetch = replace_stream(host_stream, bars[po:po + psize], chans)
    sdir = os.path.join(mod_dir, "romfs", STREAM)
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, OUR_STREAM + ".bwav"), "wb") as f:
        f.write(stream)
    log(f"  replay stream: {STREAM}/{OUR_STREAM}.bwav ({len(stream)} bytes, in {HOST_STREAM}'s format)")

    onsets = [max(1, int(t * RATE)) for t, _k, _v, _l in events[:len(SONG_NOTES)]]
    patches = [("replay prefetch", host_stream[:12], 0, prefetch),
               marker_patch(bars, HOST_STREAM, onsets, len(chans[0]), log)] + title_patches(romfs, log)
    write_patches(os.path.join(out, "patches.bin"), patches)
    log(f"  patches.bin: {len(patches)} memory patches")
    for old in ("playback.wav", "success.wav", "title.otf"):          # (v0.1 files)
        if os.path.exists(os.path.join(out, old)):
            os.remove(os.path.join(out, old))

    with open(os.path.join(out, "song.cfg"), "w", encoding="utf-8") as f:
        f.write("# Song of Healing (read by the overlay)\n")
        f.write("notes " + " ".join(str(NOTE_NUMBER[n]) for n in SONG_NOTES) + "\n")
        f.write(f"host_stream {HOST_STREAM}.bwav\n")
        f.write(f"stream {OUR_STREAM}.bwav\n")
        f.write(f"replay_seconds {len(pcm) / RATE:.3f}\n")

    with open(os.path.join(mod_dir, "BUILD_LOG.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    info_path = os.path.join(mod_dir, "info.json")
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    info.update(version=MOD_VERSION, description=MOD_DESCRIPTION, category="Gameplay")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"Done: {mod_dir}")


if __name__ == "__main__":
    build()
