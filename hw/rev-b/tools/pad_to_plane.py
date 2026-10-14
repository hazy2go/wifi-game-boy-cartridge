#!/usr/bin/env python3
"""Connect an outer-layer pad to a same-net plane with one via (KiCad Python).
Searches via spots in a box, each reached from the pad by a straight or L-shaped trace on the pad's layer.
A candidate is legal if the trace clears all other-net copper on that layer by CLEAR, the via clears all
other-net copper on every copper layer by CLEAR and every other hole by HOLE2HOLE, and the via lies inside the
plane's filled copper on PLANE_LAYER (zones are refilled first). Shortest legal trace wins.
Usage: pad_to_plane.py PCB REF PAD PLANE_LAYER WIDTH X0 Y0 X1 Y1 [--dry]
"""
import math, sys
import pcbnew
CLEAR, HOLE2HOLE, VIA_D, VIA_H, STEP = 0.16, 0.25, 0.6, 0.3, 0.1


def mm(v): return pcbnew.FromMM(v)


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    pcb, ref, padnum, plane_layer, w = a[:5]; w = float(w)
    x0, y0, x1, y1 = map(float, a[5:9])
    b = pcbnew.LoadBoard(pcb)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
    PL = L[plane_layer]
    pad = b.FindFootprintByReference(ref).FindPadByNumber(padnum)
    net = pad.GetNetname()
    play = pcbnew.F_Cu if pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
    obst, holes = [], []
    for t in b.GetTracks():
        if t.GetClass() == 'PCB_VIA':
            holes.append((t.GetPosition(), t.GetDrill()))
        if t.GetNetname() != net:
            for l in t.GetLayerSet().CuStack():
                obst.append((l, t.GetEffectiveShape(l)))
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.GetDrillSizeX() > 0:
                holes.append((q.GetPosition(), max(q.GetDrillSizeX(), q.GetDrillSizeY())))
            if q.GetNetname() == net and net:
                continue
            for l in q.GetLayerSet().CuStack():
                obst.append((l, q.GetEffectiveShape(l)))
        for g in f.GraphicalItems():
            if g.GetClass() == 'PCB_SHAPE' and g.GetLayer() in L.values() and g.GetNetname() != net:
                obst.append((g.GetLayer(), g.GetEffectiveShape(g.GetLayer())))
    planes = [z for z in b.Zones() if z.GetNetname() == net and z.IsOnLayer(PL)]
    P = pad.GetPosition()
    sig = [l for l in L.values() if l != PL]

    def via_ok(V):
        c = pcbnew.SHAPE_CIRCLE(V, mm(VIA_D / 2))
        if any(math.hypot(h[0].x - V.x, h[0].y - V.y) / 1e6 - (h[1] / 1e6 + VIA_H) / 2 < HOLE2HOLE for h in holes):
            return False
        if any(s.Collide(c, mm(CLEAR)) for l, s in obst if l in sig):
            return False
        # inside the plane fill, with the full via pad
        for z in planes:
            fp = z.GetFilledPolysList(PL)
            if all(fp.Contains(pcbnew.VECTOR2I(int(V.x + mm(VIA_D / 2) * math.cos(t)), int(V.y + mm(VIA_D / 2) * math.sin(t))))
                   for t in [i * math.pi / 6 for i in range(12)]) and fp.Contains(V):
                return True
        return False

    def seg_ok(A, B):
        s = pcbnew.SHAPE_SEGMENT(A, B, mm(w))
        return not any(sh.Collide(s, mm(CLEAR)) for l, sh in obst if l == play)

    best = None
    nx, ny = int((x1 - x0) / STEP) + 1, int((y1 - y0) / STEP) + 1
    for i in range(nx):
        for j in range(ny):
            V = pcbnew.VECTOR2I(mm(x0 + i * STEP), mm(y0 + j * STEP))
            for path in ([P, V], [P, pcbnew.VECTOR2I(P.x, V.y), V], [P, pcbnew.VECTOR2I(V.x, P.y), V]):
                ln = sum(math.hypot(path[k + 1].x - path[k].x, path[k + 1].y - path[k].y) for k in range(len(path) - 1)) / 1e6
                if best and ln >= best[0]:
                    continue
                if all(seg_ok(path[k], path[k + 1]) for k in range(len(path) - 1)) and via_ok(V):
                    best = (ln, path)
    if not best:
        print(f'FAIL {ref}.{padnum} [{net}]: no legal via into {plane_layer} in box'); return 1
    ln, path = best
    print(f'OK   {ref}.{padnum} [{net}] {ln:.2f} mm on {b.GetLayerName(play)} w {w}, via {VIA_D}/{VIA_H} at '
          f'({path[-1].x/1e6:.2f}, {path[-1].y/1e6:.2f})')
    if '--dry' in sys.argv:
        return 0
    for k in range(len(path) - 1):
        if path[k] == path[k + 1]:
            continue
        t = pcbnew.PCB_TRACK(b); t.SetStart(path[k]); t.SetEnd(path[k + 1]); t.SetWidth(mm(w))
        t.SetLayer(play); t.SetNet(pad.GetNet()); b.Add(t)
    v = pcbnew.PCB_VIA(b); v.SetPosition(path[-1]); v.SetWidth(mm(VIA_D)); v.SetDrill(mm(VIA_H))
    v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(pad.GetNet()); b.Add(v)
    b.Save(pcb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
