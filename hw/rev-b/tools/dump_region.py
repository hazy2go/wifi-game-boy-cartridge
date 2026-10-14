#!/usr/bin/env python3
"""Dump copper of every layer inside a box to JSON (KiCad Python), zones refilled: tracks, vias, pads (as
polygons), filled zone polygons. Pair with plot_layers.py. Usage: dump_region.py PCB out.json X0 Y0 X1 Y1"""
import json, sys
import pcbnew
pcb, out = sys.argv[1:3]; x0, y0, x1, y1 = map(float, sys.argv[3:7])
b = pcbnew.LoadBoard(pcb)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
box = pcbnew.BOX2I(pcbnew.VECTOR2I(pcbnew.FromMM(x0 - 1), pcbnew.FromMM(y0 - 1)),
                   pcbnew.VECTOR2I(pcbnew.FromMM(x1 - x0 + 2), pcbnew.FromMM(y1 - y0 + 2)))
names = {l: b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()}
g = {'tracks': [], 'vias': [], 'polys': []}


def poly(sps):
    res = []
    for i in range(sps.OutlineCount()):
        o = sps.Outline(i)
        res.append([(o.CPoint(k).x / 1e6, o.CPoint(k).y / 1e6) for k in range(o.PointCount())])
    return res


for t in b.GetTracks():
    if not t.GetBoundingBox().Intersects(box):
        continue
    if t.GetClass() == 'PCB_VIA':
        g['vias'].append([t.GetNetname(), t.GetPosition().x / 1e6, t.GetPosition().y / 1e6, t.GetWidth() / 1e6, t.GetDrill() / 1e6])
    else:
        g['tracks'].append([t.GetNetname(), names[t.GetLayer()], t.GetStart().x / 1e6, t.GetStart().y / 1e6,
                            t.GetEnd().x / 1e6, t.GetEnd().y / 1e6, t.GetWidth() / 1e6])
for f in b.GetFootprints():
    for p in f.Pads():
        if not p.GetBoundingBox().Intersects(box):
            continue
        for l in p.GetLayerSet().CuStack():
            if l in names:
                sps = pcbnew.SHAPE_POLY_SET(); p.TransformShapeToPolygon(sps, l, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_INSIDE)
                for o in poly(sps):
                    g['polys'].append([p.GetNetname(), names[l], 'pad', f'{f.GetReference()}.{p.GetNumber()}', o])
for z in b.Zones():
    if not z.GetBoundingBox().Intersects(box):
        continue
    for l in z.GetLayerSet().CuStack():
        if l in names and z.HasFilledPolysForLayer(l):
            for o in poly(z.GetFilledPolysList(l)):
                g['polys'].append([z.GetNetname(), names[l], 'zone', '', o])
json.dump(g, open(out, 'w'))
print(len(g['tracks']), 'tracks', len(g['vias']), 'vias', len(g['polys']), 'polys')
