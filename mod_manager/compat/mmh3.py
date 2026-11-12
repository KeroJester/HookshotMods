"""Pure-Python stand-in for the `mmh3` package (only `hash`), used when mmh3 is not installed.
third_party/AINB needs it for murmur3 name hashes."""
import struct


def hash(key, seed: int = 0, signed: bool = True) -> int:   # noqa: A001 (same name as mmh3.hash)
    data = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    c1, c2, n = 0xcc9e2d51, 0x1b873593, len(data)
    h = seed & 0xffffffff
    for i in range(0, n // 4 * 4, 4):
        k = struct.unpack_from("<I", data, i)[0]
        k = (k * c1) & 0xffffffff
        k = ((k << 15) | (k >> 17)) & 0xffffffff
        k = (k * c2) & 0xffffffff
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xffffffff
        h = (h * 5 + 0xe6546b64) & 0xffffffff
    tail, k = data[n // 4 * 4:], 0
    for i, b in enumerate(tail):
        k |= b << (8 * i)
    if tail:
        k = (k * c1) & 0xffffffff
        k = ((k << 15) | (k >> 17)) & 0xffffffff
        k = (k * c2) & 0xffffffff
        h ^= k
    h ^= n
    h ^= h >> 16
    h = (h * 0x85ebca6b) & 0xffffffff
    h ^= h >> 13
    h = (h * 0xc2b2ae35) & 0xffffffff
    h ^= h >> 16
    return h - (1 << 32) if signed and h & 0x80000000 else h
