#!/usr/bin/env python3
"""Stitch an outer-layer pour (or pads) of NET to its inner plane with a grid of through vias (KiCad Python).

A grid point gets a via if the whole via circle lies inside NET copper on F.Cu (a filled zone of NET, or with
--pads REF inside a NET pad of that footprint: via-in-pad) and inside NET's filled plane on PLANE, and it keeps
CLEAR from every other-net track, via, pad and filled zone on every copper layer (so no other plane gets a new
antipad), HOLE2HOLE from every drilled hole and EDGE from the outline. Zones are refilled before and after.
With --pads: one via per NET pad of REF inside the box, nearest its centre, instead of the grid.
Usage: gnd_stitch.py PCB NET PLANE X0 Y0 X1 Y1 PITCH [D H] [--pads REF] [--dry]"""
import math, sys
import pcbnew

CLEAR, HOLE2HOLE, EDGE = 0.2, 0.25, 0.5


def mm(v):
    return pcbnew.FromMM(v)


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    pcb, net, plane = a[:3]
    x0, y0, x1, y1, pitch = map(float, a[3:8])
    D, H = (float(a[8]), float(a[9])) if len(a) > 9 else (0.45, 0.2)
    padref = sys.argv[sys.argv.index('--pads') + 1] if '--pads' in sys.argv else None
    b = pcbnew.LoadBoard(pcb)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
    PL, cu = L[plane], list(L.values())
    holes, obst = [], []
    for t in b.GetTracks():
        if t.GetClass() == 'PCB_VIA':
            holes.append((t.GetPosition(), t.GetDrill()))
        if t.GetNetname() != net:
            for l in t.GetLayerSet().CuStack():
                obst.append((l, t.GetEffectiveShape(l)))
    own = []
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.GetDrillSizeX() > 0:
                holes.append((q.GetPosition(), max(q.GetDrillSizeX(), q.GetDrillSizeY())))
            if q.GetNetname() == net:
                if padref and f.GetReference() == padref and q.IsOnLayer(pcbnew.F_Cu):
                    own.append(q.GetEffectiveShape(pcbnew.F_Cu))
                continue
            for l in q.GetLayerSet().CuStack():
                obst.append((l, q.GetEffectiveShape(l)))
    top, inner = [], []
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for l in z.GetLayerSet().CuStack():
            fp = z.GetFilledPolysList(l)
            if z.GetNetname() == net:
                (top if l == pcbnew.F_Cu else inner if l == PL else []).append(fp)
            else:
                obst.append((l, fp))
    edge = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(edge, True)
    edge.Deflate(mm(EDGE), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))

    def ring(P, r):
        return [pcbnew.VECTOR2I(int(P.x + r * math.cos(t)), int(P.y + r * math.sin(t))) for t in [i * math.pi / 8 for i in range(16)]] + [P]

    added = []

    def legal(P, src):
        pts = ring(P, mm(D / 2))
        if not all(edge.Contains(p) for p in pts):
            return False
        if not any(all(s.Collide(p, 0) for p in pts) for s in src):
            return False
        if not any(all(s.Contains(p) for p in pts) for s in inner):
            return False
        v = pcbnew.SHAPE_CIRCLE(P, mm(D / 2))
        if any(l in cu and s.Collide(v, mm(CLEAR)) for l, s in obst):
            return False
        return not any(math.hypot(h[0].x - P.x, h[0].y - P.y) / 1e6 - (h[1] / 1e6 + H) / 2 < HOLE2HOLE for h in holes)

    if padref:              # one via per pad, as near its centre as is legal (pads inside the box only)
        for q in [q for q in b.FindFootprintByReference(padref).Pads() if q.GetNetname() == net]:
            c = q.GetPosition()
            if not (mm(x0) <= c.x <= mm(x1) and mm(y0) <= c.y <= mm(y1)):
                continue
            s = q.GetEffectiveShape(pcbnew.F_Cu)
            cand = sorted((pcbnew.VECTOR2I(c.x + mm(dx * 0.05), c.y + mm(dy * 0.05)) for dx in range(-12, 13) for dy in range(-12, 13)),
                          key=lambda P: (P.x - c.x) ** 2 + (P.y - c.y) ** 2)
            P = next((P for P in cand if legal(P, [s])), None)
            if P is None:
                print(f'  no legal via in {padref}.{q.GetNumber()} at ({c.x / 1e6:.3f}, {c.y / 1e6:.3f})')
                continue
            holes.append((P, mm(H)))
            added.append(P)
    nx, ny = (int((x1 - x0) / pitch) + 1, int((y1 - y0) / pitch) + 1) if not padref else (0, 0)
    for j in range(ny):
        for i in range(nx):
            P = pcbnew.VECTOR2I(mm(x0 + i * pitch), mm(y0 + j * pitch))
            if not legal(P, top):
                continue
            holes.append((P, mm(H)))
            added.append(P)
    print(f'{len(added)} vias {D}/{H} on {net} -> {plane} in ({x0},{y0})-({x1},{y1}) ' + (f'in pads of {padref}' if padref else f'pitch {pitch}'))
    if '--dry' in sys.argv or not added:
        return
    n = b.FindNet(net)
    for P in added:
        v = pcbnew.PCB_VIA(b); v.SetPosition(P); v.SetWidth(mm(D)); v.SetDrill(mm(H))
        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(n); b.Add(v)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.Save(pcb)


if __name__ == '__main__':
    main()
