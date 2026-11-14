#!/usr/bin/env python3
"""MajoraMod's songbook icons (original art): one glyph per added song, drawn at 4x and reduced to the game's icon size
(107 x 107, RGBA, transparent around the glyph; the button draws the dark disc behind it).
Writes art/icons/<type>.png and src/icons_data.cpp (zstd-compressed linear RGBA, built into the mod).
Run: python3 art/make_icons.py   (needs Pillow, numpy, zstandard)"""
import math, os, sys
import numpy as np
from PIL import Image, ImageDraw
import zstandard

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE, K = 107, 4
W = SIZE * K
C = W / 2

def canvas():
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)

def P(x, y):   # unit coordinates (-1..1, y up) -> pixels
    return (C + x * W * 0.40, C - y * W * 0.40)

def poly(d, pts, col): d.polygon([P(x, y) for x, y in pts], fill=col)
def line(d, pts, col, w): d.line([P(x, y) for x, y in pts], fill=col, width=int(w * K), joint="curve")
def circle(d, x, y, r, col, w=None):
    box = [P(x - r, y + r), P(x + r, y - r)]
    if w: d.ellipse(box, outline=col, width=int(w * K))
    else: d.ellipse(box, fill=col)
def arc(d, x, y, r, a0, a1, col, w):
    box = [P(x - r, y + r), P(x + r, y - r)]
    d.arc(box, -a1, -a0, fill=col, width=int(w * K))

def heart(d, col):            # Song of Healing: a heart, a soft spiral mending its middle
    pts = []
    for i in range(200):
        t = i / 200 * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x / 17 * 0.95, y / 17 * 0.95 + 0.08))
    poly(d, pts, col)
    pts = [(0.03 * t * math.cos(t), 0.03 * t * math.sin(t) + 0.12) for t in np.linspace(0, 4.2 * math.pi, 120)]
    line(d, pts, (0, 0, 0, 0), 4.5)

def feather(d, col):          # Song of Soaring: a feather
    ax = np.array([0.62, 0.78]); ax /= np.linalg.norm(ax)          # the quill's direction
    nx = np.array([-ax[1], ax[0]])
    base = np.array([-0.50, -0.62])
    pts = []
    for side in (1, -1):
        rng = np.linspace(0, 1, 60) if side == 1 else np.linspace(1, 0, 60)
        for t in rng:
            w = 0.34 * math.sin(math.pi * min(1, t * 1.15)) ** 0.7
            p = base + ax * (t * 1.55) + nx * w * side
            pts.append(tuple(p))
    poly(d, pts, col)
    for k in range(5):                                              # notches in the vane
        t = 0.30 + 0.13 * k
        p = base + ax * (t * 1.55)
        q = p + nx * 0.40 + ax * 0.10
        line(d, [tuple(p + nx * 0.18), tuple(q)], (0, 0, 0, 0), 2.6)
    line(d, [tuple(base - ax * 0.30), tuple(base + ax * 1.45)], (0, 0, 0, 0), 2.4)
    line(d, [tuple(base - ax * 0.32), tuple(base + ax * 0.15)], col, 4.5)

def eye(d, col):              # Sonata of Awakening: an eye opening, lashes like a sunrise
    top = [(x, 0.42 * math.cos(x * math.pi / 2)) for x in np.linspace(-0.85, 0.85, 50)]
    bot = [(x, -0.30 * math.cos(x * math.pi / 2)) for x in np.linspace(0.85, -0.85, 50)]
    poly(d, top + bot, col)
    circle(d, 0, 0.02, 0.27, (0, 0, 0, 0))
    circle(d, 0, 0.02, 0.15, col)
    for a in (-60, -30, 0, 30, 60):
        r = math.radians(90 + a)
        line(d, [(0.55 * math.cos(r), 0.40 + 0.18 * math.sin(r) - 0.1), (0.88 * math.cos(r), 0.40 + 0.42 * math.sin(r) - 0.08)], col, 5)

def moon(d, col):             # Goron Lullaby: a crescent moon with sleepy z's
    circle(d, -0.12, -0.08, 0.66, col)
    circle(d, 0.16, 0.10, 0.56, (0, 0, 0, 0))
    for (x, y, s) in ((0.36, 0.30, 0.20), (0.62, 0.62, 0.14)):
        line(d, [(x - s, y + s), (x + s, y + s), (x - s, y - s), (x + s, y - s)], col, 4.2)

def wave(d, col):             # New Wave Bossa Nova: a wave curling over an egg
    d.ellipse([P(-0.26, -0.20), P(0.26, -0.86)], fill=col)                     # the egg
    crest = [(x, 0.05 + 0.10 * math.sin(x * 2.4)) for x in np.linspace(-0.92, 0.35, 40)]
    curl = [(0.35 + 0.30 * math.cos(a), 0.40 + 0.30 * math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi * 1.15, 50)]
    inner = [(0.35 + 0.12 * math.cos(a), 0.40 + 0.12 * math.sin(a)) for a in np.linspace(math.pi * 1.15, -math.pi / 2, 30)]
    under = [(x, -0.10 + 0.08 * math.sin(x * 2.4)) for x in np.linspace(0.35, -0.92, 40)]
    poly(d, crest + curl + inner + under, col)

def hollow(d, col):           # Elegy of Emptiness: a hollow figure, its shell left standing
    circle(d, 0, 0.55, 0.22, col, 4.5)
    line(d, [(-0.32, 0.22), (0.32, 0.22), (0.42, -0.25), (0.2, -0.28), (0.24, -0.9), (-0.24, -0.9), (-0.2, -0.28),
             (-0.42, -0.25), (-0.32, 0.22)], col, 4.5)
    line(d, [(-0.10, -0.28), (0.10, -0.28)], col, 3)

def giants(d, col):           # Oath to Order: four giants standing together
    for k, x in enumerate((-0.6, -0.2, 0.2, 0.6)):
        h = 0.62 if k in (1, 2) else 0.48
        circle(d, x, -0.25 + h + 0.15, 0.13, col)
        d.rounded_rectangle([P(x - 0.13, -0.25 + h), P(x + 0.13, -0.78)], radius=int(0.06 * W * 0.4), fill=col)

def hourglass(d, col):        # Inverted Song of Time: an hourglass, an arrow turning back around it
    poly(d, [(-0.34, 0.56), (0.34, 0.56), (0.05, 0.0), (0.34, -0.56), (-0.34, -0.56), (-0.05, 0.0)], col)
    poly(d, [(-0.19, 0.43), (0.19, 0.43), (0.0, 0.08)], (0, 0, 0, 0))
    line(d, [(-0.42, 0.63), (0.42, 0.63)], col, 5)
    line(d, [(-0.42, -0.63), (0.42, -0.63)], col, 5)
    arc(d, 0, 0, 0.86, 200, 340, col, 4.5)                                     # around the bottom, right to left
    a = math.radians(200)
    x, y = 0.86 * math.cos(a), 0.86 * math.sin(a)
    poly(d, [(x - 0.02, y + 0.20), (x - 0.20, y - 0.10), (x + 0.16, y - 0.06)], col)   # the head, turning upward

def chevrons(d, col):         # Song of Double Time: two arrows racing ahead on a clock's rim
    arc(d, 0, 0, 0.82, -200, 20, col, 4.5)
    for x0 in (-0.42, 0.04):
        poly(d, [(x0, 0.40), (x0 + 0.42, 0.0), (x0, -0.40), (x0 - 0.14, -0.40), (x0 + 0.26, 0.0), (x0 - 0.14, 0.40)], col)

ICONS = [  # (type, draw, colour)
    (14, heart, (245, 105, 160, 255)),
    (15, feather, (95, 185, 255, 255)),
    (16, eye, (115, 215, 85, 255)),
    (17, moon, (255, 160, 60, 255)),
    (18, wave, (45, 190, 225, 255)),
    (19, hollow, (205, 205, 228, 255)),
    (20, giants, (250, 200, 60, 255)),
    (21, hourglass, (150, 120, 255, 255)),
    (22, chevrons, (210, 115, 255, 255)),
]

def render(fn, col):
    im, d = canvas()
    fn(d, col)
    return im.resize((SIZE, SIZE), Image.LANCZOS)

def main():
    out = os.path.join(HERE, "icons")
    os.makedirs(out, exist_ok=True)
    blobs = []
    for t, fn, col in ICONS:
        im = render(fn, col)
        im.save(os.path.join(out, f"{t}.png"))
        blobs.append((t, np.asarray(im, np.uint8).tobytes()))
    raw = b"".join(b for _, b in blobs)
    z = zstandard.ZstdCompressor(level=19).compress(raw)
    src = os.path.join(HERE, "..", "src", "icons_data.cpp")
    with open(src, "w") as f:
        f.write("// Generated by art/make_icons.py from the mod's own icon art (art/icons/*.png): do not edit.\n")
        f.write('#include "icons.h"\n\nnamespace icons {\n')
        f.write(f"const int kSize = {SIZE};\nconst uint32_t kTypes[] = {{{', '.join(str(t) for t, _ in blobs)}}};\n")
        f.write(f"const size_t kCount = {len(blobs)};\nconst size_t kRawSize = {len(raw)};\n")
        f.write(f"const size_t kZstdSize = {len(z)};\nconst uint8_t kZstd[] = {{\n")
        for i in range(0, len(z), 24):
            f.write("    " + ", ".join(f"0x{b:02x}" for b in z[i:i + 24]) + ",\n")
        f.write("};\n}  // namespace icons\n")
    print(f"{len(blobs)} icons, {len(raw)} bytes raw, {len(z)} compressed")
    # preview on the button's dark disc
    sheet = Image.new("RGBA", (SIZE * len(blobs) + 10 * len(blobs), SIZE + 10), (60, 20, 30, 255))
    for i, (t, fn, col) in enumerate(ICONS):
        disc = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        ImageDraw.Draw(disc).ellipse([4, 4, SIZE - 4, SIZE - 4], fill=(28, 20, 22, 255))
        disc.alpha_composite(Image.open(os.path.join(out, f"{t}.png")))
        sheet.alpha_composite(disc, (5 + i * (SIZE + 10), 5))
    sheet.save(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "preview.png"))

if __name__ == "__main__":
    main()
