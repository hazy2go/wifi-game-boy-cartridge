#!/usr/bin/env python3
"""Join a via (or any point with a same-net via) to an existing same-net track (KiCad Python).
Candidate landing vias are sampled along the net's tracks on TRACK_LAYER; each is joined to the source via by a
straight, L-shaped or 45-degree-then-straight path on one inner/back layer. Every via and segment is checked with
exact KiCad shapes against all other-net copper (CLEAR, HOLE2HOLE). Shortest legal path wins.
Usage: via_to_track.py PCB NET SX SY TRACK_LAYER [--via D/H] [--layers In2.Cu,In3.Cu,B.Cu] [--box X0,Y0,X1,Y1] [--dry]
"""
import math, sys
import pcbnew
CLEAR, CLS, HOLE2HOLE = 0.155, 0.1505, 0.25


def mm(v): return pcbnew.FromMM(v)
def P(x, y): return pcbnew.VECTOR2I(mm(x), mm(y))


def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    pcb, net, sx, sy, tl = sys.argv[1:6]; sx, sy = float(sx), float(sy)
    D, H = map(float, opt('--via', '0.35/0.15').split('/'))
    b = pcbnew.LoadBoard(pcb)
    L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
    layers = [L[n] for n in opt('--layers', 'In2.Cu,In3.Cu,B.Cu').split(',')]
    bx = list(map(float, opt('--box', f'{sx-8},{sy-8},{sx+8},{sy+8}').split(',')))
    box = pcbnew.BOX2I(P(bx[0] - 1, bx[1] - 1), P(bx[2] - bx[0] + 2, bx[3] - bx[1] + 2))
    sig = [l for n, l in L.items() if n not in ('In1.Cu', 'In4.Cu')]
    obst, holes, tracks = [], [], []
    for t in b.GetTracks():
        if not t.GetBoundingBox().Intersects(box):
            continue
        if t.GetNetname() == net:
            if t.GetClass() == 'PCB_TRACK' and t.GetLayer() == L[tl]:
                tracks.append(t)
            continue
        if t.GetClass() == 'PCB_VIA':
            holes.append((t.GetPosition(), t.GetDrill()))
        for l in t.GetLayerSet().CuStack():
            obst.append((l, t.GetEffectiveShape(l)))
    for f in b.GetFootprints():
        for q in f.Pads():
            if not q.GetBoundingBox().Intersects(box):
                continue
            if q.GetDrillSizeX() > 0:
                holes.append((q.GetPosition(), q.GetDrillSizeX()))
            if q.GetNetname() == net:
                continue
            for l in q.GetLayerSet().CuStack():
                obst.append((l, q.GetEffectiveShape(l)))
        for g in f.GraphicalItems():
            if g.GetClass() == 'PCB_SHAPE' and g.GetLayer() in L.values() and g.GetNetname() != net and g.GetBoundingBox().Intersects(box):
                obst.append((g.GetLayer(), g.GetEffectiveShape(g.GetLayer())))

    def via_ok(V):
        c = pcbnew.SHAPE_CIRCLE(V, mm(D / 2)); h = pcbnew.SHAPE_CIRCLE(V, mm(H / 2))
        if any(math.hypot(a.x - V.x, a.y - V.y) / 1e6 - (d / 1e6 + H) / 2 < HOLE2HOLE for a, d in holes):
            return False
        return not any(s.Collide(c, mm(CLEAR)) or s.Collide(h, mm(0.25)) for l, s in obst if l in sig)

    def seg_ok(A, B, l):
        if A == B:
            return True
        s = pcbnew.SHAPE_SEGMENT(A, B, mm(0.15))
        return not any(sh.Collide(s, mm(CLS)) for ll, sh in obst if ll == l)

    S = P(sx, sy)
    cands = []
    for t in tracks:
        a, e = t.GetStart(), t.GetEnd(); n = max(1, int(t.GetLength() / 1e6 / 0.05))
        for k in range(n + 1):
            V = pcbnew.VECTOR2I(int(a.x + (e.x - a.x) * k / n), int(a.y + (e.y - a.y) * k / n))
            if bx[0] <= V.x / 1e6 <= bx[2] and bx[1] <= V.y / 1e6 <= bx[3]:
                cands.append(V)
    cands.sort(key=lambda V: math.hypot(V.x - S.x, V.y - S.y))
    best = None
    for V in cands:
        d0 = math.hypot(V.x - S.x, V.y - S.y) / 1e6
        if best and d0 >= best[0]:
            break
        if not via_ok(V):
            continue
        dx, dy = V.x - S.x, V.y - S.y
        m = min(abs(dx), abs(dy))
        diag = pcbnew.VECTOR2I(S.x + int(math.copysign(m, dx)), S.y + int(math.copysign(m, dy)))
        diag2 = pcbnew.VECTOR2I(V.x - int(math.copysign(m, dx)), V.y - int(math.copysign(m, dy)))
        for path in ([S, V], [S, P(V.x / 1e6, S.y / 1e6), V], [S, P(S.x / 1e6, V.y / 1e6), V], [S, diag, V], [S, diag2, V]):
            ln = sum(math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y) for i in range(len(path) - 1)) / 1e6
            if best and ln >= best[0]:
                continue
            for l in layers:
                if all(seg_ok(path[i], path[i + 1], l) for i in range(len(path) - 1)):
                    best = (ln, path, l); break
    if not best:
        print(f'FAIL {net}: no legal via-to-track link'); return 1
    ln, path, l = best
    print(f'OK   {net}: {ln:.2f} mm on {b.GetLayerName(l)}, landing via at ({path[-1].x/1e6:.3f}, {path[-1].y/1e6:.3f})')
    if '--dry' in sys.argv:
        return 0
    nt = b.FindNet(net)
    for i in range(len(path) - 1):
        if path[i] == path[i + 1]:
            continue
        t = pcbnew.PCB_TRACK(b); t.SetStart(path[i]); t.SetEnd(path[i + 1]); t.SetWidth(mm(0.15)); t.SetLayer(l); t.SetNet(nt); b.Add(t)
    v = pcbnew.PCB_VIA(b); v.SetPosition(path[-1]); v.SetWidth(mm(D)); v.SetDrill(mm(H))
    v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(nt); b.Add(v)
    b.Save(pcb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
