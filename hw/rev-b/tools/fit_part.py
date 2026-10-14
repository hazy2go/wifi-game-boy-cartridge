#!/usr/bin/env python3
"""Search positions for one footprint on the top side (KiCad Python).

A candidate (x, y, rotation) is legal if (1) its courtyard stays inside the board outline (0.3 mm inset) and
out of the shell-rib/connector bands, (2) its courtyard overlaps no other top courtyard, (3) none of its pads
comes within CLEAR of any other F.Cu copper (pads, tracks, vias), except copper on nets listed with --ignore-net
(copper we intend to rip up). Prints legal positions ranked by distance to --near.
Usage: fit_part.py PCB REF --box X0 Y0 X1 Y1 [--step 0.25] [--near X Y] [--ignore-net GND ...] [--ignore-ref R ...]
"""
import sys, math, pcbnew
from free_space import RIBS, CONNECTOR_Y, is_new

CLEAR = 0.2


def mm(v):
    return pcbnew.FromMM(v)


def main():
    a = sys.argv[1:]
    pcb, ref = a[0], a[1]
    box = [float(v) for v in a[a.index('--box') + 1:a.index('--box') + 5]]
    step = float(a[a.index('--step') + 1]) if '--step' in a else 0.25
    near = [float(v) for v in a[a.index('--near') + 1:a.index('--near') + 3]] if '--near' in a else None
    ign_net = {a[i + 1] for i, x in enumerate(a) if x == '--ignore-net'}
    ign_ref = {a[i + 1] for i, x in enumerate(a) if x == '--ignore-ref'}
    rots = [float(a[a.index('--rot') + 1])] if '--rot' in a else [0, 90, 180, 270]
    b = pcbnew.LoadBoard(pcb)
    fp = b.FindFootprintByReference(ref)
    outline = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(outline, True)
    outline.Deflate(mm(0.3), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))
    # obstacles: other placed top-side parts (new staged parts outside the board are ignored automatically)
    bb = b.GetBoardEdgesBoundingBox()
    def on_board(p):
        return bb.Contains(p)
    others = [f for f in b.GetFootprints() if f.GetReference() != ref and f.GetReference() not in ign_ref
              and on_board(f.GetPosition())]
    crt = []
    for f in others:
        if f.GetLayer() != pcbnew.F_Cu:
            continue
        cy = f.GetCourtyard(pcbnew.F_CrtYd)
        r = cy.BBox() if cy.OutlineCount() else f.GetBoundingBox(False)
        crt.append((r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6, f.GetReference()))
    cu = []   # (shape, net, what)
    for f in others:
        for p in f.Pads():
            if p.IsOnLayer(pcbnew.F_Cu) and p.GetNetname() not in ign_net:
                cu.append((p.GetEffectiveShape(pcbnew.F_Cu), f'{f.GetReference()}.{p.GetNumber()}'))
    for t in b.GetTracks():
        if t.IsOnLayer(pcbnew.F_Cu) and t.GetNetname() not in ign_net:
            cu.append((t.GetEffectiveShape(pcbnew.F_Cu), f'{t.GetClass()} {t.GetNetname()}'))
    res = []
    x = box[0]
    while x <= box[2]:
        y = box[1]
        while y <= box[3]:
            for rot in rots:
                fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y))); fp.SetOrientationDegrees(rot)
                cy = fp.GetCourtyard(pcbnew.F_CrtYd)
                r = cy.BBox()
                x0, y0, x1, y1 = r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6
                ok = all(outline.Contains(pcbnew.VECTOR2I(mm(px), mm(py))) for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)))
                ok = ok and y1 < CONNECTOR_Y and not any(y0 < hb and y1 > ha for ha, hb in RIBS)
                hit = None
                if ok:
                    for c in crt:
                        if x0 < c[2] and x1 > c[0] and y0 < c[3] and y1 > c[1]:
                            ok, hit = False, c[4]; break
                if ok:
                    for p in fp.Pads():
                        ps = p.GetEffectiveShape(pcbnew.F_Cu)
                        for s, what in cu:
                            if ps.Collide(s, mm(CLEAR)):
                                ok, hit = False, what; break
                        if not ok:
                            break
                if ok:
                    d = math.hypot(x - near[0], y - near[1]) if near else 0
                    res.append((d, x, y, rot))
            y += step
        x += step
    res.sort()
    print(f'{len(res)} legal positions for {ref}')
    for d, x, y, rot in res[:15]:
        print(f'  ({x:.2f}, {y:.2f}) rot {rot:g}  dist {d:.1f}')


if __name__ == '__main__':
    main()
