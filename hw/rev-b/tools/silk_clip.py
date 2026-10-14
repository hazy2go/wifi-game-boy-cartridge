#!/usr/bin/env python3
"""Clip the silkscreen of selected footprints so it touches no pad copper and no other silkscreen (KiCad Python).
Straight silk segments are sampled every STEP mm and split into the runs that stay MARGIN clear of every pad
(any footprint, on the same side) and of all silk already kept; runs shorter than MIN_RUN are dropped.
Other silk shapes (arcs, circles, polygons: outlines, pin-1 marks) are kept when clear, otherwise moved to the
Fab layer. Footprints are processed largest first, so IC outlines win over passives. Unselected footprints'
silk and board silk count as obstacles and are never touched.
Usage: silk_clip.py PCB [--min-ref 100]   (selects references with number >= min-ref)"""
import math, re, sys
import pcbnew
STEP, MARGIN, MIN_RUN = 0.05, 0.05, 0.25


def mm(v): return pcbnew.FromMM(v)


def main():
    pcb = sys.argv[1]
    minref = int(sys.argv[sys.argv.index('--min-ref') + 1]) if '--min-ref' in sys.argv else 100
    b = pcbnew.LoadBoard(pcb)
    side = {pcbnew.F_SilkS: pcbnew.F_Cu, pcbnew.B_SilkS: pcbnew.B_Cu}
    fab = {pcbnew.F_SilkS: pcbnew.F_Fab, pcbnew.B_SilkS: pcbnew.B_Fab}

    def sel(f):
        m = re.match(r'[A-Z]+(\d+)$', f.GetReference()); return bool(m and int(m.group(1)) >= minref)
    pads = {l: [] for l in side.values()}
    for f in b.GetFootprints():
        for p in f.Pads():
            for l in side.values():
                if p.IsOnLayer(l):
                    pads[l].append(p.GetEffectiveShape(l))
    silk = {l: [] for l in side}          # obstacles: unselected silk + board silk + kept selected silk
    for d in b.GetDrawings():
        if d.GetLayer() in side:
            silk[d.GetLayer()].append(d.GetEffectiveShape(d.GetLayer()))
    chosen = []
    for f in b.GetFootprints():
        items = [g for g in f.GraphicalItems() if g.GetLayer() in side]
        if not sel(f):
            for g in items:
                silk[g.GetLayer()].append(g.GetEffectiveShape(g.GetLayer()))
            continue
        chosen.append(f)
    chosen.sort(key=lambda f: -f.GetBoundingBox(False).GetArea())
    stats = dict(kept=0, split=0, dropped=0, to_fab=0)
    for f in chosen:
        for g in [g for g in f.GraphicalItems() if g.GetLayer() in side]:
            L = g.GetLayer(); cu = side[L]; w = g.GetWidth()
            def clear(shape):
                return not any(s.Collide(shape, mm(MARGIN)) for s in pads[cu]) and not any(s.Collide(shape, 0) for s in silk[L])
            if g.GetClass() == 'PCB_SHAPE' and g.GetShape() == pcbnew.SHAPE_T_SEGMENT:
                A, B = g.GetStart(), g.GetEnd()
                n = max(1, int(math.hypot(B.x - A.x, B.y - A.y) / 1e6 / STEP))
                pts = [pcbnew.VECTOR2I(int(A.x + (B.x - A.x) * k / n), int(A.y + (B.y - A.y) * k / n)) for k in range(n + 1)]
                ok = [clear(pcbnew.SHAPE_CIRCLE(p, w // 2)) for p in pts]
                if all(ok):
                    silk[L].append(g.GetEffectiveShape(L)); stats['kept'] += 1; continue
                runs, i = [], 0
                while i <= n:
                    if ok[i]:
                        j = i
                        while j + 1 <= n and ok[j + 1]:
                            j += 1
                        if (pts[j] - pts[i]).EuclideanNorm() / 1e6 >= MIN_RUN:
                            runs.append((pts[i], pts[j]))
                        i = j + 1
                    else:
                        i += 1
                if not runs:
                    f.Remove(g); stats['dropped'] += 1; continue
                g.SetStart(runs[0][0]); g.SetEnd(runs[0][1]); silk[L].append(g.GetEffectiveShape(L))
                for s, e in runs[1:]:
                    h = pcbnew.PCB_SHAPE(f, pcbnew.SHAPE_T_SEGMENT); h.SetLayer(L); h.SetWidth(w)
                    h.SetStart(s); h.SetEnd(e); f.Add(h); silk[L].append(h.GetEffectiveShape(L))
                stats['split'] += 1
            else:
                if clear(g.GetEffectiveShape(L)):
                    silk[L].append(g.GetEffectiveShape(L)); stats['kept'] += 1
                else:
                    g.SetLayer(fab[L]); stats['to_fab'] += 1
    b.Save(pcb)
    print(f'{len(chosen)} footprints: {stats}')


if __name__ == '__main__':
    main()
