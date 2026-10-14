#!/usr/bin/env python3
"""Via-in-pad for a pad that has no room for a fan-out via (KiCad Python).
Tries positions along the pad's long axis (pad centre first), via D/H given; a candidate is legal if the via
lies fully inside the pad copper, keeps CLEAR (and its drill HOLECLR) from all other-net copper on every copper layer (zones excluded:
they are refilled around it) and HOLE2HOLE from every other drilled hole. Needs filled+capped vias (JLC POFV,
standard on 6-layer). --dry only reports. --rip: if only inner/B.Cu track segments (never pads or vias) block
the best spot, delete those segments (their nets are printed: re-route them with finish_route.py).
Usage: via_in_pad.py PCB REF PAD [D H] [--dry] [--rip]
"""
import sys
import pcbnew
CLEAR, HOLE2HOLE, HOLECLR = 0.15, 0.25, 0.25


def mm(v): return pcbnew.FromMM(v)


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    pcb, ref, pad = a[:3]
    D, H = (float(a[3]), float(a[4])) if len(a) > 4 else (0.3, 0.15)
    b = pcbnew.LoadBoard(pcb)
    p = b.FindFootprintByReference(ref).FindPadByNumber(pad)
    net = p.GetNetname()
    cu = list(b.GetEnabledLayers().CuStack())
    obst, holes = [], []
    for t in b.GetTracks():
        if t.GetClass() == 'PCB_VIA':
            holes.append((t.GetPosition(), t.GetDrill()))
        if t.GetNetname() == net:
            continue
        for l in t.GetLayerSet().CuStack():
            obst.append((l, t.GetEffectiveShape(l), t))
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.GetDrillSizeX() > 0:
                holes.append((q.GetPosition(), max(q.GetDrillSizeX(), q.GetDrillSizeY())))
            if q.GetNetname() == net and q.GetNetname() != '':
                continue
            for l in q.GetLayerSet().CuStack():
                obst.append((l, q.GetEffectiveShape(l), q))
    c = p.GetPosition(); sz = p.GetSize()
    ang = p.GetOrientation().AsRadians()
    import math
    long_x = sz.x >= sz.y
    half = (max(sz.x, sz.y) - pcbnew.FromMM(D)) / 2
    padshape = p.GetEffectiveShape(pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu)
    best, rip, seen = None, [], set()
    for k in [k for pas in (0, 1) for k in range(41)] if '--rip' in sys.argv else range(41):
        pas = 1 if (k, 'x') in seen else 0; seen.add((k, 'x'))
        off = (k + 1) // 2 * (1 if k % 2 else -1) * half / 20
        dx, dy = (off, 0) if long_x else (0, off)
        P = pcbnew.VECTOR2I(int(c.x + dx * math.cos(ang) + dy * math.sin(ang)), int(c.y - dx * math.sin(ang) + dy * math.cos(ang)))
        v = pcbnew.SHAPE_CIRCLE(P, mm(D / 2))
        # inside pad: every point of the via circle within the pad (pad shape collides with a shrunken probe)
        inside = all(padshape.Collide(pcbnew.VECTOR2I(int(P.x + mm(D / 2) * math.cos(t)), int(P.y + mm(D / 2) * math.sin(t))), 0)
                     for t in [i * math.pi / 8 for i in range(16)])
        if not inside:
            continue
        hole = pcbnew.SHAPE_CIRCLE(P, mm(H / 2))
        hits = [(l, it) for l, s, it in obst if l in cu and (s.Collide(v, mm(CLEAR)) or s.Collide(hole, mm(HOLECLR)))]
        bad = [l for l, it in hits]
        hb = [h for h in holes if math.hypot(h[0].x - P.x, h[0].y - P.y) / 1e6 - (h[1] / 1e6 + H) / 2 < HOLE2HOLE]
        if not bad and not hb:
            best = P; break
        if pas and not hb and all(it.GetClass() == 'PCB_TRACK' and l != pcbnew.F_Cu for l, it in hits):
            best, rip = P, list({id(it): it for l, it in hits}.values()); break
        if k == 0 and not pas:
            print(f'centre blocked: layers {sorted(set(b.GetLayerName(l) for l in bad))}, holes {len(hb)}')
    if best is None:
        print(f'FAIL {ref}.{pad} [{net}] pad {sz.x/1e6:.2f}x{sz.y/1e6:.2f}: no legal via-in-pad {D}/{H}'); return 1
    print(f'OK   {ref}.{pad} [{net}] via {D}/{H} at ({best.x/1e6:.3f}, {best.y/1e6:.3f})')
    for it in rip:
        print(f'rip  {it.GetNetname()} on {b.GetLayerName(it.GetLayer())}')
    if '--dry' in sys.argv:
        return 0
    for it in rip:
        b.Remove(it)
    v = pcbnew.PCB_VIA(b); v.SetPosition(best); v.SetWidth(mm(D)); v.SetDrill(mm(H))
    v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(p.GetNet()); b.Add(v)
    b.Save(pcb)
    return 0


if __name__ == '__main__':
    sys.exit(main())
