#!/usr/bin/env python3
"""plot_geom.py geom.json out.png x0 y0 x1 y1 [--bcu]: zoomed top-side view with a 1 mm grid."""
import json, sys, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
g = json.load(open(sys.argv[1])); x0, y0, x1, y1 = map(float, sys.argv[3:7])
fig, ax = plt.subplots(figsize=(max(6, (x1 - x0) * 0.7), max(6, (y1 - y0) * 0.7)))
for net, l, xa, ya, xb, yb, w in g['tracks']:
    if l == 'F.Cu':
        ax.plot([xa, xb], [ya, yb], color='tab:red', lw=w * 30, solid_capstyle='round', alpha=0.7)
    elif l == 'B.Cu' and '--bcu' in sys.argv:
        ax.plot([xa, xb], [ya, yb], color='tab:blue', lw=w * 30, alpha=0.4)
for net, xa, ya, xb, yb in g['pads']:
    ax.add_patch(plt.Rectangle((xa, ya), xb - xa, yb - ya, color='goldenrod', alpha=0.8))
for net, x, y, d in g['vias']:
    ax.add_patch(plt.Circle((x, y), d / 2, color='gray'))
for ref, xa, ya, xb, yb, new in g['crt']:
    ax.add_patch(plt.Rectangle((xa, ya), xb - xa, yb - ya, fill=False, ec='green' if new else 'purple', lw=0.8))
    if x0 - 2 < xa < x1 and y0 - 2 < ya < y1:
        ax.text((xa + xb) / 2, (ya + yb) / 2, ref, fontsize=7, ha='center', va='center')
for xa, ya, xb, yb in g['edge']:
    ax.plot([xa, xb], [ya, yb], 'k-')
ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_aspect('equal')
import numpy as np
ax.set_xticks(np.arange(int(x0), int(x1) + 1, 1)); ax.set_yticks(np.arange(int(y0), int(y1) + 1, 1))
ax.tick_params(labelsize=6); ax.grid(lw=0.3)
fig.savefig(sys.argv[2], dpi=90, bbox_inches='tight')
