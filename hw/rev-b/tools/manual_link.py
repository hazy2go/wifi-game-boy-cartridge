#!/usr/bin/env python3
"""Small targeted connection (KiCad Python): from an outer-layer point A (pad) drop a via V1 nearby, run a
straight inner-layer segment to a via V2 placed on an existing same-net inner track (points sampled along it).
Every candidate is checked with exact KiCad shape collision against all other-net copper (CLEAR), on all
signal layers for vias. Picks the shortest legal combination. Prints what it added; DRC verifies afterwards.
Usage: manual_link.py PCB NET AX AY LAYER_OF_A TRACK_LAYER TX0 TY0 TX1 TY1 [INNER_LAYER]
"""
import math, sys
import pcbnew
CLEAR, VIA_D, VIA_H = 0.16, 0.5, 0.2


def mm(v): return pcbnew.FromMM(v)


def main(pcb, net, ax, ay, la, tl, tx0, ty0, tx1, ty1, il='In3.Cu'):
    b = pcbnew.LoadBoard(pcb)
    L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
    ax, ay, tx0, ty0, tx1, ty1 = map(float, (ax, ay, tx0, ty0, tx1, ty1))
    items = []
    for t in b.GetTracks():
        for l in t.GetLayerSet().CuStack():
            items.append((t.GetNetname(), l, t.GetEffectiveShape(l)))
    for f in b.GetFootprints():
        for p in f.Pads():
            for l in p.GetLayerSet().CuStack():
                items.append((p.GetNetname(), l, p.GetEffectiveShape(l)))
        for g in f.GraphicalItems():
            for l in (pcbnew.F_Cu, pcbnew.B_Cu):
                if g.IsOnLayer(l):
                    items.append(('', l, g.GetEffectiveShape(l)))
    sig = [L['F.Cu'], L['In2.Cu'], L['In3.Cu'], L['B.Cu']]
    free = lambda s, l: not any(ll == l and n != net and sh.Collide(s, mm(CLEAR)) for n, ll, sh in items)
    via_ok = lambda x, y: all(free(pcbnew.SHAPE_CIRCLE(pcbnew.VECTOR2I(mm(x), mm(y)), mm(VIA_D / 2)), l) for l in sig)
    seg = lambda p, q, w: pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])), pcbnew.VECTOR2I(mm(q[0]), mm(q[1])), mm(w))
    v1s = []
    for r in [k * 0.1 for k in range(1, 46)]:
        for k in range(int(2 * math.pi * r / 0.1) + 1):
            a = 2 * math.pi * k / (int(2 * math.pi * r / 0.1) + 1)
            x, y = ax + r * math.cos(a), ay + r * math.sin(a)
            if via_ok(x, y) and free(seg((ax, ay), (x, y), 0.15), L[la]):
                v1s.append((r, x, y))
        if len(v1s) > 30:
            break
    best = None
    n = int(math.hypot(tx1 - tx0, ty1 - ty0) / 0.1)
    v2s = [(tx0 + (tx1 - tx0) * k / n, ty0 + (ty1 - ty0) * k / n) for k in range(n + 1)]
    v2s = [p for p in v2s if via_ok(*p)]
    for r, x, y in v1s:
        for qx, qy in v2s:
            d = r + math.hypot(qx - x, qy - y)
            if best and d >= best[0]:
                continue
            if free(seg((x, y), (qx, qy), 0.15), L[il]):
                best = (d, x, y, qx, qy)
    if not best:
        sys.exit(f'no legal link ({len(v1s)} V1 candidates, {len(v2s)} V2 candidates)')
    d, x, y, qx, qy = best
    ni = b.FindNet(net)
    for (px, py) in ((x, y), (qx, qy)):
        v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(px), mm(py))); v.SetWidth(mm(VIA_D)); v.SetDrill(mm(VIA_H))
        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(ni); b.Add(v)
    for p, q, l in (((ax, ay), (x, y), L[la]), ((x, y), (qx, qy), L[il])):
        t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))); t.SetEnd(pcbnew.VECTOR2I(mm(q[0]), mm(q[1])))
        t.SetWidth(mm(0.15)); t.SetLayer(l); t.SetNet(ni); b.Add(t)
    b.Save(pcb)
    print(f'linked {net}: V1 ({x:.2f},{y:.2f}) -> V2 ({qx:.2f},{qy:.2f}) on {il}, {d:.2f} mm')


if __name__ == '__main__':
    main(*sys.argv[1:])
