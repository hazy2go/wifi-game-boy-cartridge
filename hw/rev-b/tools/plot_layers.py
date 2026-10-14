#!/usr/bin/env python3
"""Plot a dump_region.py JSON as one panel per copper layer, one net highlighted.
Usage: plot_layers.py dump.json out.png X0 Y0 X1 Y1 NET"""
import json, sys, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
g = json.load(open(sys.argv[1])); out = sys.argv[2]; x0, y0, x1, y1 = map(float, sys.argv[3:7]); hl = sys.argv[7]
layers = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu']
fig, axs = plt.subplots(2, 3, figsize=(3 * max(5, (x1 - x0) * 0.55), 2 * max(5, (y1 - y0) * 0.55)))
for ax, L in zip(axs.flat, layers):
    for net, l, kind, ref, pts in g['polys']:
        if l != L:
            continue
        c = 'red' if net == hl else ('#9ad' if kind == 'zone' else '#666')
        ax.add_patch(Polygon(pts, closed=True, fc=c, ec='none', alpha=0.35 if kind == 'zone' else 0.8))
        if kind == 'pad' and x0 <= pts[0][0] <= x1 and y0 <= pts[0][1] <= y1:
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            ax.text(cx, cy, ref, fontsize=5, ha='center', va='center')
    for net, l, xa, ya, xb, yb, w in g['tracks']:
        if l == L:
            ax.plot([xa, xb], [ya, yb], lw=w * 72 / 25.4 * fig.dpi / 100 * 4, color='red' if net == hl else '#c80',
                    solid_capstyle='round', alpha=0.85)
    for net, x, y, d, h in g['vias']:
        ax.add_patch(Circle((x, y), d / 2, fc='red' if net == hl else 'green', alpha=0.8))
        ax.add_patch(Circle((x, y), h / 2, fc='white'))
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_aspect('equal'); ax.set_title(L)
    ax.set_xticks(range(int(x0), int(x1) + 1)); ax.set_yticks(range(int(y0), int(y1) + 1))
    ax.tick_params(labelsize=5); ax.grid(lw=0.2)
plt.tight_layout(); plt.savefig(out, dpi=110)
