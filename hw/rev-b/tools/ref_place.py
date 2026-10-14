#!/usr/bin/env python3
"""Move reference designators to the nearest legal silkscreen spot (KiCad Python).
For each footprint whose visible silk reference touches a pad, a via, other silkscreen or the board edge, try
positions on rings around the footprint (0.1 mm steps, up to MAX_D) at 0 and 90 degrees. A spot is legal if the
text clears every pad and via on that side by CLEAR, every other silk item by 0 and the outline by EDGE.
No spot: the reference goes to the Fab layer (listed). Usage: ref_place.py PCB [--only REF,REF] [--dry]"""
import math, sys
import pcbnew
CLEAR, EDGE, MAX_D = 0.1, 0.3, 3.0


def mm(v): return pcbnew.FromMM(v)


def main():
    pcb = sys.argv[1]
    only = set(sys.argv[sys.argv.index('--only') + 1].split(',')) if '--only' in sys.argv else None
    b = pcbnew.LoadBoard(pcb)
    S = {pcbnew.F_SilkS: pcbnew.F_Cu, pcbnew.B_SilkS: pcbnew.B_Cu}
    FAB = {pcbnew.F_SilkS: pcbnew.F_Fab, pcbnew.B_SilkS: pcbnew.B_Fab}
    cu = {l: [] for l in S.values()}
    for f in b.GetFootprints():
        for p in f.Pads():
            for l in cu:
                if p.IsOnLayer(l):
                    cu[l].append(p.GetEffectiveShape(l))
        for g in f.GraphicalItems():           # mask openings drawn as graphics (SW1, J5)
            if g.GetLayer() in (pcbnew.F_Mask, pcbnew.B_Mask):
                cu[pcbnew.F_Cu if g.GetLayer() == pcbnew.F_Mask else pcbnew.B_Cu].append(g.GetEffectiveShape(g.GetLayer()))
    for t in b.GetTracks():
        if t.GetClass() == 'PCB_VIA':
            for l in cu:
                cu[l].append(t.GetEffectiveShape(l))
    silk = {l: [] for l in S}   # (owner id, shape)
    for d in b.GetDrawings():
        if d.GetLayer() in S:
            silk[d.GetLayer()].append((None, d.GetEffectiveShape(d.GetLayer())))
    for f in b.GetFootprints():
        for g in list(f.GraphicalItems()) + list(f.GetFields()):
            if g.GetLayer() in S and (not hasattr(g, 'IsVisible') or g.IsVisible()):
                silk[g.GetLayer()].append((g.m_Uuid.AsString(), g.GetEffectiveShape(g.GetLayer())))
    inner = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(inner, True)
    inner.Deflate(mm(EDGE), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))

    def legal(r):
        L = r.GetLayer(); sh = r.GetEffectiveShape(L); me = r.m_Uuid.AsString()
        bb = r.GetBoundingBox()
        if not all(inner.Contains(c) for c in (bb.GetOrigin(), bb.GetEnd(), pcbnew.VECTOR2I(bb.GetLeft(), bb.GetBottom()),
                                                pcbnew.VECTOR2I(bb.GetRight(), bb.GetTop()))):
            return False
        if any(s.Collide(sh, mm(CLEAR)) for s in cu[S[L]]):
            return False
        return not any(o != me and s.Collide(sh, 0) for o, s in silk[L])

    moved, fab = [], []
    for f in b.GetFootprints():
        r = f.Reference()
        if r.GetLayer() not in S or not r.IsVisible() or (only and f.GetReference() not in only):
            continue
        if legal(r):
            continue
        L = r.GetLayer(); p0, a0 = r.GetPosition(), r.GetTextAngleDegrees()
        c = f.GetBoundingBox(False).Centre()
        best = None
        for k in range(1, int(MAX_D / 0.1) + 1):
            d = k * 0.1; n = max(8, int(2 * math.pi * d / 0.1))
            for i in range(n):
                t = 2 * math.pi * i / n
                P = pcbnew.VECTOR2I(int(p0.x + mm(d) * math.cos(t)), int(p0.y + mm(d) * math.sin(t)))
                for ang in (a0, (a0 + 90) % 180):
                    r.SetPosition(P); r.SetTextAngleDegrees(ang)
                    if legal(r):
                        best = (P, ang); break
                if best:
                    break
            if best:
                break
        if best:
            r.SetPosition(best[0]); r.SetTextAngleDegrees(best[1]); moved.append(f.GetReference())
            silk[L].append((r.m_Uuid.AsString(), r.GetEffectiveShape(L)))
        else:
            r.SetPosition(p0); r.SetTextAngleDegrees(a0); r.SetLayer(FAB[L]); fab.append(f.GetReference())
    print('moved:', ' '.join(moved)); print('to Fab:', ' '.join(fab) or '-')
    if '--dry' not in sys.argv:
        b.Save(pcb)


if __name__ == '__main__':
    main()
