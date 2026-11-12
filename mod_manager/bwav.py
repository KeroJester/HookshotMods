"""BWAV (Switch stream wave) reader: PCM16 / DSP-ADPCM channels.

Header: "BWAV", BOM, version, CRC, prefetch flag, channel count; then one 0x4c channel record each:
codec u16 (0 PCM16, 1 DSP-ADPCM), pan u16, rate u32, samples u32, prefetch samples u32, 16 i16 coefficients,
prefetch data offset u32, data offset u32, loop flag u32, loop end u32, loop start u32, predictor/scale and
history words.
"""
import struct

import numpy as np

try:
    from .csar import decode_dsp_adpcm
except ImportError:  # pragma: no cover
    from csar import decode_dsp_adpcm


def decode_bwav(d: bytes):
    """-> (rate, [np.int16 array per channel])"""
    if d[:4] != b"BWAV":
        raise ValueError("not a BWAV")
    nch = struct.unpack_from("<H", d, 0x0e)[0]
    rate, chans = 0, []
    for i in range(nch):
        o = 0x10 + i * 0x4c
        codec, _pan, rate, nsamp = struct.unpack_from("<HHII", d, o)
        coefs = struct.unpack_from("<16h", d, o + 0x10)
        data_off = struct.unpack_from("<I", d, o + 0x34)[0]
        if codec == 0:
            chans.append(np.frombuffer(d, np.int16, nsamp, data_off).copy())
        elif codec == 1:
            chans.append(decode_dsp_adpcm(d[data_off:], coefs, nsamp))
        else:
            raise ValueError(f"BWAV codec {codec}")
    return rate, chans


def write_wav(path, rate, chans):
    """Interleaved PCM16 .wav"""
    n = min(len(c) for c in chans)
    pcm = np.stack([c[:n] for c in chans], axis=1).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, len(chans), rate, rate * 2 * len(chans), 2 * len(chans), 16))
        f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)


def encode_bwav_pcm16(rate, chans, loop=False):
    """Stream BWAV, PCM16 (codec 0). chans: list of np.int16 arrays."""
    import zlib
    n = min(len(c) for c in chans)
    nch = len(chans)
    head = 0x10 + 0x4c * nch
    off = (head + 0x3f) & ~0x3f
    datas, offs = [], []
    for c in chans:
        offs.append(off)
        d = c[:n].astype("<i2").tobytes()
        d += b"\0" * (-len(d) & 0x3f)
        datas.append(d)
        off += len(d)
    body = b"".join(datas)
    out = bytearray(b"BWAV" + struct.pack("<HHIHH", 0xFEFF, 1, zlib.crc32(body) & 0xFFFFFFFF, 0, nch))
    for i in range(nch):
        pan = 2 if nch == 1 else i                      # 0 left, 1 right, 2 centre
        out += struct.pack("<HHII I", 0, pan, rate, n, n) + b"\0" * 32
        out += struct.pack("<IIIII", offs[i], offs[i], 1, 0xFFFFFFFF, 0) + b"\0" * 12
    out += b"\0" * (offs[0] - len(out))
    return bytes(out) + body


def encode_dsp_adpcm(pcm, coefs):
    """Nintendo DSP-ADPCM encoder for a given coefficient table (16 values: 8 predictor pairs).
    pcm: np.int16. -> bytes (8 per 14 samples). Each frame tries every predictor and scale and keeps the one with
    the least squared error (decoded exactly as decode_dsp_adpcm does, so the history never drifts)."""
    pcm = np.asarray(pcm, np.int64)
    n = len(pcm)
    frames = (n + 13) // 14
    x = np.zeros(frames * 14, np.int64)
    x[:n] = pcm
    c1 = np.repeat(np.array(coefs[0::2], np.int64), 13)      # candidates: 8 predictors x 13 scales
    c2 = np.repeat(np.array(coefs[1::2], np.int64), 13)
    sc = np.tile(1 << np.arange(13, dtype=np.int64), 8)
    hdr = (np.repeat(np.arange(8), 13) << 4) | np.tile(np.arange(13), 8)
    out = bytearray(frames * 8)
    h1 = h2 = 0
    for f in range(frames):
        a = np.full(104, h1, np.int64)
        b = np.full(104, h2, np.int64)
        err = np.zeros(104, np.int64)
        nibs = np.empty((14, 104), np.int64)
        for k in range(14):
            s = x[f * 14 + k]
            pred = c1 * a + c2 * b
            q = np.clip(np.rint(((s << 11) - pred) / (sc << 11)).astype(np.int64), -8, 7)
            d = np.clip((((q * sc) << 11) + 1024 + pred) >> 11, -32768, 32767)
            err += (d - s) ** 2
            nibs[k] = q
            b = a
            a = d
        best = int(np.argmin(err))
        h1, h2 = int(a[best]), int(b[best])
        out[f * 8] = int(hdr[best])
        q = nibs[:, best] & 0xF
        for j in range(7):
            out[f * 8 + 1 + j] = int((q[2 * j] << 4) | q[2 * j + 1])
    return bytes(out)



CH = 0x4c            # channel record size


def channel_info(d, i):
    """BWAV channel record i as a dict (see the module docstring)."""
    o = 0x10 + i * CH
    codec, pan, rate, nsamp, npre = struct.unpack_from("<HHIII", d, o)
    off_stream, off_here = struct.unpack_from("<II", d, o + 0x30)
    return dict(o=o, codec=codec, pan=pan, rate=rate, nsamp=nsamp, npre=npre,
                coefs=struct.unpack_from("<16h", d, o + 0x10), off_stream=off_stream, off_here=off_here)


def replace_stream(host_stream: bytes, host_prefetch: bytes, chans):
    """A replacement for a streamed sound, in the host sound's own format.

    The engine plays a stream from the BARS's prefetch BWAV (header + the first samples) and then reads the rest
    from the stream file at the offsets the prefetch header gives, decoding with the prefetch header's codec and
    coefficients; it does not read the stream file's own header. So this returns
      (stream file bytes, prefetch BWAV bytes of exactly len(host_prefetch))
    for `chans` (np.int16 arrays, one per host channel): both DSP-ADPCM with the host's first coefficient table.
    """
    nch = struct.unpack_from("<H", host_stream, 0x0e)[0]
    assert nch == len(chans) == struct.unpack_from("<H", host_prefetch, 0x0e)[0]
    hc = [channel_info(host_prefetch, i) for i in range(nch)]
    assert all(c["codec"] == 1 for c in hc)
    coefs = hc[0]["coefs"]
    n = min(len(c) for c in chans)
    enc = [encode_dsp_adpcm(np.asarray(c[:n]), coefs) for c in chans]
    enc = [e + b"\0" * (-len(e) & 0x3f) for e in enc]
    off = channel_info(host_stream, 0)["off_stream"]
    offs = []
    for e in enc:
        offs.append(off)
        off += len(e)
    stream = bytearray(host_stream[:offs[0]]) + b"".join(enc)
    pre = bytearray(host_prefetch)
    for i in range(nch):
        for blob in (stream, pre):
            o = 0x10 + i * CH
            struct.pack_into("<I", blob, o + 8, n)
            struct.pack_into("<16h", blob, o + 0x10, *coefs)
            struct.pack_into("<I", blob, o + 0x30, offs[i])
        struct.pack_into("<I", stream, 0x10 + i * CH + 0xc, n)
        struct.pack_into("<I", stream, 0x10 + i * CH + 0x34, offs[i])
        nbytes = (hc[i]["npre"] + 13) // 14 * 8                       # the prefetch part of this channel
        assert n >= hc[i]["npre"], "the replacement is shorter than the prefetch"
        pre[hc[i]["off_here"]:hc[i]["off_here"] + nbytes] = enc[i][:nbytes]
    return bytes(stream), bytes(pre)


def find_prefetch(bars: bytes, host_stream: bytes):
    """(offset, size) of the prefetch BWAV for a stream inside a BARS: same magic .. CRC as the stream file."""
    o = bars.find(host_stream[:12])
    if o < 0:
        raise ValueError("prefetch BWAV not found in the BARS")
    nch = struct.unpack_from("<H", bars, o + 0x0e)[0]
    end = 0
    for i in range(nch):
        c = channel_info(bars[o:], i)
        end = max(end, c["off_here"] + (c["npre"] + 13) // 14 * 8)
    return o, end
