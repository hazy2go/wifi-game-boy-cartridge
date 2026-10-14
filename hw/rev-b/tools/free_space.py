#!/usr/bin/env python3
"""Top-side free-space map for placement (run with KiCad's Python).

A 0.1 mm grid over the board outline; a cell is blocked if it lies within CLEAR of any existing F.Cu pad, track
or via of a part that is staying put, inside any top courtyard of a staying part, outside the outline (0.3 mm
inset), in the edge-connector zone or in a shell-rib band. Prints the largest free axis-aligned rectangles and
whether each big new part fits anywhere, and writes a PNG of the map.
Usage: free_space.py PCB [--movable REF,REF,...] [--png out.png]
"""
import sys, numpy as np, pcbnew

RES, CLEAR = 0.1, 0.25
RIBS = [(60.5, 63.0), (111.6, 113.6)]      # y bands (mm) of the shell ribs, LOGBOOK 05:02 occupancy map
CONNECTOR_Y = 113.6                       # below this y: edge connector / rib, no parts
NEW_PREFIX_MIN = 101                      # refs numbered >= 101 are the new radio parts (staged off-board)


def is_new(ref):
    num = ''.join(ch for ch in ref if ch.isdigit())
    return num and int(num) >= NEW_PREFIX_MIN


def main(pcb, movable=(), png=None):
    b = pcbnew.LoadBoard(pcb)
    bb = b.GetBoardEdgesBoundingBox()
    x0, y0, w, h = bb.GetX() / 1e6, bb.GetY() / 1e6, bb.GetWidth() / 1e6, bb.GetHeight() / 1e6
    nx, ny = int(w / RES) + 1, int(h / RES) + 1
    blocked = np.zeros((ny, nx), bool)
    # outline: cells whose centre is inside the board edge polygon, 0.3 mm inset
    outline = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(outline, True)
    outline.Deflate(pcbnew.FromMM(0.3), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    ys, xs = np.mgrid[0:ny, 0:nx]
    for j in range(ny):
        for i in range(0, nx):
            if not outline.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x0 + i * RES), pcbnew.FromMM(y0 + j * RES))):
                blocked[j, i] = True
    def mark_box(xa, ya, xb, yb, pad=0.0):
        i0, i1 = int((xa - pad - x0) / RES), int((xb + pad - x0) / RES) + 1
        j0, j1 = int((ya - pad - y0) / RES), int((yb + pad - y0) / RES) + 1
        blocked[max(j0, 0):max(j1, 0), max(i0, 0):max(i1, 0)] = True
    for ya, yb in RIBS:
        mark_box(x0, ya, x0 + w, yb)
    mark_box(x0, CONNECTOR_Y, x0 + w, y0 + h)
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        if is_new(ref) or ref in movable:
            continue
        if fp.GetLayer() == pcbnew.F_Cu:
            cy = fp.GetCourtyard(pcbnew.F_CrtYd)
            r = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
            mark_box(r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6)
        for pad in fp.Pads():
            if pad.IsOnLayer(pcbnew.F_Cu):
                r = pad.GetBoundingBox()
                mark_box(r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6, CLEAR)
    for t in b.GetTracks():
        if not t.IsOnLayer(pcbnew.F_Cu):
            continue
        if t.GetClass() == 'PCB_VIA':
            c, r = t.GetPosition(), t.GetWidth(pcbnew.F_Cu) / 2e6
            mark_box(c.x / 1e6 - r, c.y / 1e6 - r, c.x / 1e6 + r, c.y / 1e6 + r, CLEAR)
        else:
            s, e, hw = t.GetStart(), t.GetEnd(), t.GetWidth() / 2e6 + CLEAR
            n = max(2, int(t.GetLength() / 1e6 / (RES / 2)))
            for k in range(n + 1):
                px = (s.x + (e.x - s.x) * k / n) / 1e6
                py = (s.y + (e.y - s.y) * k / n) / 1e6
                mark_box(px - hw, py - hw, px + hw, py + hw)
    free = ~blocked
    print(f'board {w:.1f} x {h:.1f} mm, free top area (cells) {free.sum() * RES * RES:.0f} mm2')
    # largest empty rectangle via histogram method; report top distinct ones
    def max_rects(mask, k=8):
        out = []
        m = mask.copy()
        for _ in range(k):
            hgt = np.zeros(m.shape[1], int); best = (0, None)
            for j in range(m.shape[0]):
                hgt = np.where(m[j], hgt + 1, 0)
                st = []
                for i in range(len(hgt) + 1):
                    cur = hgt[i] if i < len(hgt) else 0
                    start = i
                    while st and st[-1][1] >= cur:
                        s0, hh = st.pop()
                        area = hh * (i - s0)
                        if area > best[0]:
                            best = (area, (s0, j - hh + 1, i - s0, hh))
                        start = s0
                    st.append((start, cur))
            if not best[1]:
                break
            i, j, ww, hh = best[1]
            out.append((x0 + i * RES, y0 + j * RES, ww * RES, hh * RES))
            m[j:j + hh, i:i + ww] = False
        return out
    rects = max_rects(free)
    for r in rects:
        print(f'  free rect at ({r[0]:.1f}, {r[1]:.1f}) size {r[2]:.1f} x {r[3]:.1f} mm = {r[2]*r[3]:.0f} mm2')
    if png:     # KiCad's Python has no matplotlib: save the grid, plot with tools/plot_free_space.py
        np.savez(png, free=free, extent=np.array([x0, x0 + w, y0 + h, y0]), rects=np.array(rects))
        print('wrote', png)

if __name__ == '__main__':
    a = sys.argv[1:]
    mov = a[a.index('--movable') + 1].split(',') if '--movable' in a else ()
    png = a[a.index('--png') + 1] if '--png' in a else None
    main(a[0], mov, png)
