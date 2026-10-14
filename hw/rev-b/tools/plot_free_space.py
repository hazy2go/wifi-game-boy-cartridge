#!/usr/bin/env python3
"""Plot the grid saved by free_space.py: plot_free_space.py in.npz out.png"""
import sys, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
d = np.load(sys.argv[1])
fig, ax = plt.subplots(figsize=(7, 8))
ax.imshow(d['free'], extent=tuple(d['extent']), cmap='Greens', vmin=0, vmax=1.5)
for x, y, w, h in d['rects']:
    ax.add_patch(plt.Rectangle((x, y), w, h, fill=False, ec='r')); ax.text(x + 0.3, y + 1, f'{w:.1f}x{h:.1f}', color='r', fontsize=7)
ax.set_title('free top-side area (green), largest free rectangles (red)')
fig.savefig(sys.argv[2], dpi=110)
