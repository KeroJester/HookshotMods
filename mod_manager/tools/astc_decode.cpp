// astc_decode <in.blocks> <width> <height> <block w> <block h> <out.rgba>
// Linear (already deswizzled) ASTC blocks -> RGBA8, with the ASTC decoder Hookshot bundles
// (reference/hookshot-src/gpu/third_party/suyu/video_core/textures/astc.cpp, GPL-2.0-or-later).
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <span>
#include <vector>
#include "video_core/textures/astc.h"
int main(int argc, char** argv) {
    if (argc != 7) { fprintf(stderr, "usage: astc_decode in w h bw bh out\n"); return 2; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) return 1;
    std::vector<uint8_t> in;
    uint8_t buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) in.insert(in.end(), buf, buf + n);
    fclose(f);
    uint32_t w = atoi(argv[2]), h = atoi(argv[3]), bw = atoi(argv[4]), bh = atoi(argv[5]);
    std::vector<uint8_t> out((size_t)w * h * 4);
    Tegra::Texture::ASTC::DecompressSerial(in, w, h, 1, bw, bh, out);
    FILE* o = fopen(argv[6], "wb");
    if (!o) return 1;
    fwrite(out.data(), 1, out.size(), o);
    fclose(o);
    return 0;
}
