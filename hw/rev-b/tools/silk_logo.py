#!/usr/bin/env python3
"""Black-and-white image -> silkscreen polygons on the board (KiCad Python + potrace).
The image is thresholded (black = ink), traced with potrace into smooth polygons with holes, scaled to HEIGHT mm,
fractured (KiCad polygons can't carry holes in the file) and added as filled PCB_SHAPEs on LAYER, grouped as NAME.
On B.SilkS the image is mirrored so it reads correctly when the board is viewed from the back.
--bold grows every stroke smoothly (blur + shifted threshold) so thin lines reach the fab minimum (JLC silkscreen
~0.15 mm); specks smaller than MIN_FEAT squared are dropped by potrace.
Removes an earlier group NAME first, so re-running replaces the logo.
Usage: silk_logo.py PCB IMAGE CX CY HEIGHT_MM [--layer B.SilkS] [--name remai_logo] [--min-feat 0.18] [--bold 0.03]"""
import json, math, os, subprocess, sys, tempfile
import pcbnew


def opt(n, d):
    return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d


def main():
    pcb, img, cx, cy, h = sys.argv[1:6]
    cx, cy, h = float(cx), float(cy), float(h)
    layer_name, name = opt('--layer', 'B.SilkS'), opt('--name', 'remai_logo')
    min_feat = float(opt('--min-feat', '0.18'))
    bold = float(opt('--bold', '0'))   # grow every ink stroke by this much (mm per side) before the clean-up
    # 1) threshold + minimum-feature clean-up in pixel space (system python has Pillow)
    tmp = tempfile.mkdtemp()
    bmp = os.path.join(tmp, 'in.pbm')
    prep = f'''
from PIL import Image, ImageFilter, ImageOps
im = Image.open({img!r}).convert('L')
bb = ImageOps.invert(im).point(lambda v: 255 if v > 96 else 0).getbbox()
im = im.crop(bb)
px = 2400
w = round(im.width * px / im.height)
im = im.resize((w, px), Image.LANCZOS)
# grow the ink smoothly by BOLD mm per side: blur, then move the threshold toward white
r = max(1.0, {bold} / {h} * px)
if {bold} > 0:
    im = im.filter(ImageFilter.GaussianBlur(r)).point(lambda v: 0 if v < 200 else 255)
else:
    im = im.point(lambda v: 0 if v < 128 else 255)
im.convert('1').save({bmp!r})
print(w, px)
'''
    w_px, h_px = map(int, subprocess.run(['/usr/bin/python3', '-c', prep], check=True, capture_output=True, text=True).stdout.split())
    gj = os.path.join(tmp, 'out.json')
    turd = int((min_feat / h * 2400) ** 2)          # specks smaller than a min-feature square are dropped
    subprocess.run(['potrace', '-b', 'geojson', '-a', '1.1', '-O', '0.5', '-t', str(turd), bmp, '-o', gj], check=True)
    feats = json.load(open(gj))['features']
    b = pcbnew.LoadBoard(pcb)
    layer = b.GetLayerID(layer_name)
    mirror = layer_name.startswith('B.')
    for g in list(b.Groups()):
        if g.GetName() == name:
            for it in list(g.GetItems()):
                b.Remove(it)
            b.Remove(g)
    pts = [c for f in feats for poly in (f['geometry']['coordinates'] if f['geometry']['type'] == 'MultiPolygon'
                                         else [f['geometry']['coordinates']]) for ring in poly for c in ring]
    x0, x1 = min(c[0] for c in pts), max(c[0] for c in pts)
    y0, y1 = min(c[1] for c in pts), max(c[1] for c in pts)
    s = h / (y1 - y0)                  # scale from the traced extent (potrace's units are not pixels)

    def P(x, y):   # potrace geojson: y up
        u = (x - (x0 + x1) / 2) * s
        v = ((y0 + y1) / 2 - y) * s
        return pcbnew.VECTOR2I(pcbnew.FromMM(cx + (-u if mirror else u)), pcbnew.FromMM(cy + v))
    ps = pcbnew.SHAPE_POLY_SET()
    for f in feats:
        geom = f['geometry']
        polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
        for poly in polys:
            oi = ps.NewOutline()
            for x, y in poly[0][:-1]:
                ps.Append(P(x, y), oi)
            for hole in poly[1:]:
                hi = ps.NewHole(oi)
                for x, y in hole[:-1]:
                    ps.Append(P(x, y), oi, hi)
    ps.Simplify()
    ps.Fracture()
    grp = pcbnew.PCB_GROUP(b); grp.SetName(name); b.Add(grp)
    n = 0
    for i in range(ps.OutlineCount()):
        sh = pcbnew.PCB_SHAPE(b); sh.SetShape(pcbnew.SHAPE_T_POLY)
        one = pcbnew.SHAPE_POLY_SET(); one.AddOutline(ps.Outline(i))
        sh.SetPolyShape(one); sh.SetFilled(True); sh.SetWidth(0); sh.SetLayer(layer)
        b.Add(sh); grp.AddItem(sh); n += 1
    b.Save(pcb)
    x0, x1, y0, y1 = cx - (x1 - x0) * s / 2, cx + (x1 - x0) * s / 2, cy - h / 2, cy + h / 2
    print(f'{name}: {n} polygons on {layer_name}, {x1-x0:.2f} x {y1-y0:.2f} mm at x {x0:.2f}..{x1:.2f} y {y0:.2f}..{y1:.2f}')


if __name__ == '__main__':
    main()
