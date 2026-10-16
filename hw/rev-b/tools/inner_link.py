#!/usr/bin/env python3
"""Join two points of one net that already have copper on an inner signal layer (a via, or a track end on that
layer) with a path on that layer only, using finish_route.py's raster and A* (keepout rule areas included).
Usage: inner_link.py PCB NET LAYER X1,Y1 X2,Y2"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pcbnew
import finish_route as fr

pcb, net, layer = sys.argv[1:4]
(xa, ya), (xb, yb) = [tuple(map(float, p.split(','))) for p in sys.argv[4:6]]
b = pcbnew.LoadBoard(pcb)
L = {'In2.Cu': 0, 'In3.Cu': 1}[layer]
R = fr.Raster(b)
R.prepare({net}, fr.CLEAR + 0.075)
w = 0.15
grids = [R.blocked(net, l, w / 2) for l in fr.LAYERS]
grids[1 - L][:] = True                    # stay on the one layer
cell = lambda x, y: (int(round((y - R.y0) / fr.RES)), int(round((x - R.x0) / fr.RES)))
for x, y in ((xa, ya), (xb, yb)):
    j, i = cell(x, y); grids[L][max(j - 3, 0):j + 4, max(i - 3, 0):i + 4] = False
path = fr.astar(grids, cell(xa, ya), cell(xb, yb), np.zeros_like(grids[0]))
if path is None or any(p[0] != L for p in path):
    sys.exit(f'FAIL {net}: no path on {layer}')
pts = [(R.x0 + i * fr.RES, R.y0 + j * fr.RES) for _, j, i in path]
pts[0], pts[-1] = (xa, ya), (xb, yb)
keep = [pts[0]]
for k in range(1, len(pts) - 1):           # merge collinear steps
    a, m, c = keep[-1], pts[k], pts[k + 1]
    if abs((m[0] - a[0]) * (c[1] - m[1]) - (m[1] - a[1]) * (c[0] - m[0])) > 1e-9:
        keep.append(m)
keep.append(pts[-1])
ni, lay = b.FindNet(net), b.GetLayerID(layer)
for p, q in zip(keep, keep[1:]):
    t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1])))
    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(q[0]), pcbnew.FromMM(q[1]))); t.SetWidth(pcbnew.FromMM(w)); t.SetLayer(lay); t.SetNet(ni); b.Add(t)
b.Save(pcb)
print(f'OK   {net} on {layer}: {len(keep) - 1} segments, {sum(math.hypot(q[0]-p[0], q[1]-p[1]) for p, q in zip(keep, keep[1:])):.1f} mm')
