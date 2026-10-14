#!/usr/bin/env python3
"""ASCII 32..127 as 8x8 Game Boy 2bpp tiles (font.2bpp) from font8x8_basic.h (Daniel Hepper, public domain, after
the public-domain IBM VGA font; the header is kept next to this file). Tile n = ASCII n when loaded at tile 32.
Colour 3 (black) where a pixel is set. No system font or PIL needed, so it builds the same on any OS.
(Until 2026-10-15 this rendered macOS Monaco, whose license does not allow redistributing it.)"""
import os, re

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'font8x8_basic.h')).read()
rows = [[int(v, 16) for v in re.findall(r'0x([0-9A-Fa-f]{2})', line)]
        for line in re.findall(r'\{([^}]*)\}', src.split('font8x8_basic[128][8] =', 1)[1])]
assert len(rows) >= 128 and all(len(r) == 8 for r in rows[:128])
out = bytearray()
for c in range(32, 128):
    for b in rows[c] if c < 127 else [0] * 8:
        row = int(f'{b:08b}'[::-1], 2)     # font8x8: bit 0 = leftmost pixel; Game Boy: bit 7 = leftmost
        out += bytes([row, row])
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'font.2bpp'), 'wb').write(out)
print(len(out), 'bytes')
