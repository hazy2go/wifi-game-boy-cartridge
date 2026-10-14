#!/usr/bin/env python3
"""Add one hand-chosen polyline on a layer, only if every segment clears all other-net copper by CLEAR
(exact KiCad shapes, incl. footprint copper graphics). Usage: add_path.py PCB NET LAYER WIDTH x0 y0 x1 y1 ..."""
import sys, pcbnew
CLEAR = 0.16
def mm(v): return pcbnew.FromMM(v)
pcb, net, layer, w = sys.argv[1:5]; w = float(w)
pts = list(zip(map(float, sys.argv[5::2]), map(float, sys.argv[6::2])))
b = pcbnew.LoadBoard(pcb)
L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}[layer]
obs = []
for t in b.GetTracks():
    if t.IsOnLayer(L) and t.GetNetname() != net: obs.append((t.GetEffectiveShape(L), f'{t.GetClass()} {t.GetNetname()}'))
for f in b.GetFootprints():
    for p in f.Pads():
        if p.IsOnLayer(L) and p.GetNetname() != net: obs.append((p.GetEffectiveShape(L), f'{f.GetReference()}.{p.GetNumber()} {p.GetNetname()}'))
    for g in f.GraphicalItems():
        if g.IsOnLayer(L): obs.append((g.GetEffectiveShape(L), f'{f.GetReference()} graphic'))
bad = []
for p, q in zip(pts, pts[1:]):
    s = pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])), pcbnew.VECTOR2I(mm(q[0]), mm(q[1])), mm(w))
    bad += [(p, q, d) for sh, d in obs if sh.Collide(s, mm(CLEAR))]
if bad:
    for p, q, d in bad: print('blocked', p, q, 'by', d)
    sys.exit(1)
ni = b.FindNet(net)
for p, q in zip(pts, pts[1:]):
    t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))); t.SetEnd(pcbnew.VECTOR2I(mm(q[0]), mm(q[1])))
    t.SetWidth(mm(w)); t.SetLayer(L); t.SetNet(ni); b.Add(t)
b.Save(pcb); print('added', len(pts) - 1, 'segments on', layer, 'for', net)
