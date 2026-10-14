#!/usr/bin/env python3
"""Render ASCII 32..127 into 8x8 Game Boy 2bpp tiles (font.2bpp) with macOS Monaco 9 px, aliased.
Only used for the test ROM's text; legibility is checked by eye in the screenshot, correctness by tile map."""
from PIL import Image, ImageDraw, ImageFont
f = ImageFont.truetype('/System/Library/Fonts/Monaco.ttf', 9)   # 9 px, aliased: best of 5 candidates by eye
out = bytearray()
for c in range(32, 128):
    im = Image.new('L', (8, 8), 0)
    d = ImageDraw.Draw(im); d.fontmode = '1'          # no anti-aliasing
    d.text((1, -2), chr(c) if c < 127 else ' ', font=f, fill=255)
    for y in range(8):
        row = 0
        for x in range(8):
            if im.getpixel((x, y)) > 110:
                row |= 0x80 >> x
        out += bytes([row, row])          # colour 3 (black) where set
open('font.2bpp', 'wb').write(out)
print(len(out), 'bytes')
