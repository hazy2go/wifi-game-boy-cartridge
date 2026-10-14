#!/usr/bin/env python3
"""Greedy legal placement of the staged (off-board) parts on the top side (KiCad Python, numpy).

Raster model at RES mm:
  allowed   inside the outline (0.3 mm inset), outside the shell-rib bands and the edge-connector zone
  crt       top courtyards of everything already on the board
  cu[net]   F.Cu copper of everything already on the board, dilated by CLEAR, per net
A candidate (x, y, rot) is legal if its courtyard lies on allowed cells with no crt, and every pad rectangle
avoids copper of other nets (same-net copper is allowed under a pad). All positions of a rotation are checked at
once with integral images. Among legal candidates the one with the smallest wirelength to already-placed pads
of the same nets wins (GND and the big rails count 0.2: they are planes/pours). Parts go in the given order;
each placed part immediately becomes an obstacle for the next.

Nothing here is trusted on its own: the result is written to docs/design/placement.json for review and
checked by KiCad DRC (verify/kicad_check.py).
Usage: autoplace.py PCB placement.json REF [REF ...] [--box X0 Y0 X1 Y1]   (appends to / updates placement.json)
"""
import json, math, sys
import numpy as np
import pcbnew

RES, CLEAR = 0.1, 0.2
RIBS = [(60.5, 63.0), (111.6, 113.6)]
CONNECTOR_Y = 113.6
WEAK_NETS = {'GND', '+3V3', 'VCC', '+5V', 'VBUS'}
# nets whose length matters electrically get a higher weight: buck-boost switch nodes and feedback,
# hub crystal, regulator high-impedance nodes (LOGBOOK 09:19)
NET_WEIGHT = {'/Radio/RF_L1': 20, '/Radio/RF_L2': 20, '/Radio/RF_FB': 5, '/Radio/HUB_XI': 20, '/Radio/HUB_XO': 20,
              '/Radio/REG_REF': 5, '/Radio/REG_CCN': 5, '/Radio/REG_K': 5, '/Radio/REG_G': 5, '/Radio/REG_S': 5}


def mm(v):
    return pcbnew.FromMM(v)


class Grid:
    def __init__(self, b):
        bb = b.GetBoardEdgesBoundingBox()
        self.bb = bb
        self.x0, self.y0 = bb.GetX() / 1e6, bb.GetY() / 1e6
        self.nx, self.ny = int(bb.GetWidth() / 1e6 / RES) + 2, int(bb.GetHeight() / 1e6 / RES) + 2
        outline = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(outline, True)
        outline.Deflate(mm(0.3), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))
        self.allowed = np.zeros((self.ny, self.nx), bool)
        for j in range(self.ny):
            y = self.y0 + j * RES
            if y > CONNECTOR_Y or any(a <= y <= c for a, c in RIBS):
                continue
            for i in range(self.nx):
                self.allowed[j, i] = outline.Contains(pcbnew.VECTOR2I(mm(self.x0 + i * RES), mm(y)))
        self.crt = np.zeros((self.ny, self.nx), bool)
        self.cu = {}

    def idx(self, xa, ya, xb, yb):
        return (max(int(math.floor((ya - self.y0) / RES)), 0), max(int(math.ceil((yb - self.y0) / RES)), 0),
                max(int(math.floor((xa - self.x0) / RES)), 0), max(int(math.ceil((xb - self.x0) / RES)), 0))

    def mark(self, arr, xa, ya, xb, yb):
        j0, j1, i0, i1 = self.idx(xa, ya, xb, yb)
        arr[j0:j1, i0:i1] = True

    def cu_of(self, net):
        if net not in self.cu:
            self.cu[net] = np.zeros((self.ny, self.nx), bool)
        return self.cu[net]

    def add_footprint(self, fp):
        if fp.GetLayer() == pcbnew.F_Cu:
            cy = fp.GetCourtyard(pcbnew.F_CrtYd)
            r = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
            self.mark(self.crt, r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6)
        for p in fp.Pads():
            if p.IsOnLayer(pcbnew.F_Cu):
                r = p.GetBoundingBox()
                self.mark(self.cu_of(p.GetNetname()), r.GetX() / 1e6 - CLEAR, r.GetY() / 1e6 - CLEAR,
                          r.GetRight() / 1e6 + CLEAR, r.GetBottom() / 1e6 + CLEAR)

    def add_track(self, t):
        if not t.IsOnLayer(pcbnew.F_Cu):
            return
        arr = self.cu_of(t.GetNetname())
        if t.GetClass() == 'PCB_VIA':
            c, r = t.GetPosition(), t.GetWidth(pcbnew.F_Cu) / 2e6 + CLEAR
            self.mark(arr, c.x / 1e6 - r, c.y / 1e6 - r, c.x / 1e6 + r, c.y / 1e6 + r)
            return
        s, e, hw = t.GetStart(), t.GetEnd(), t.GetWidth() / 2e6 + CLEAR
        n = max(2, int(t.GetLength() / 1e6 / (RES / 2)))
        for k in range(n + 1):
            px, py = (s.x + (e.x - s.x) * k / n) / 1e6, (s.y + (e.y - s.y) * k / n) / 1e6
            self.mark(arr, px - hw, py - hw, px + hw, py + hw)


def window_free(blocked, h, w):
    """free[j, i] = True if blocked[j:j+h, i:i+w] has no True cell."""
    ii = np.pad(blocked.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    H, W = blocked.shape
    out = np.zeros((H, W), bool)
    if h > H or w > W:
        return out
    s = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    out[:H - h + 1, :W - w + 1] = s == 0
    return out


def shift(a, dj, di):
    """out[j, i] = a[j + dj, i + di] (False outside)."""
    out = np.zeros_like(a)
    H, W = a.shape
    js, je = max(0, -dj), min(H, H - dj)
    is_, ie = max(0, -di), min(W, W - di)
    if js < je and is_ < ie:
        out[js:je, is_:ie] = a[js + dj:je + dj, is_ + di:ie + di]
    return out


def place_one(b, g, fp, net_points):
    best = None
    for rot in (0, 90, 180, 270):
        fp.SetPosition(pcbnew.VECTOR2I(0, 0)); fp.SetOrientationDegrees(rot)
        cy = fp.GetCourtyard(pcbnew.F_CrtYd)
        r = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
        cx0, cy0 = r.GetX() / 1e6, r.GetY() / 1e6
        ch, cw = int(math.ceil(r.GetHeight() / 1e6 / RES)) + 1, int(math.ceil(r.GetWidth() / 1e6 / RES)) + 1
        # legal origins (top-left courtyard cell) for the courtyard
        ok = window_free(g.crt | ~g.allowed, ch, cw)
        pads = []
        for p in fp.Pads():
            if not p.IsOnLayer(pcbnew.F_Cu):
                continue
            pr = p.GetBoundingBox()
            pads.append((p.GetNetname(), pr.GetX() / 1e6 - cx0, pr.GetY() / 1e6 - cy0, pr.GetWidth() / 1e6, pr.GetHeight() / 1e6,
                         p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
        for net, dx, dy, pw, ph, _, _ in pads:
            other = np.zeros_like(g.crt)
            for n, arr in g.cu.items():
                if n != net or not net:
                    other |= arr
            pf = window_free(other, int(math.ceil(ph / RES)) + 1, int(math.ceil(pw / RES)) + 1)
            ok &= shift(pf, int(round(dy / RES)), int(round(dx / RES)))
        js, is_ = np.nonzero(ok)
        if len(js) == 0:
            continue
        # cost: wirelength from each pad to the nearest already-placed point of its net
        X = g.x0 + is_ * RES - cx0     # footprint origin x for each candidate
        Y = g.y0 + js * RES - cy0
        cost = np.zeros(len(js))
        for net, _, _, _, _, px, py in pads:
            pts = net_points.get(net)
            if not net or not pts:
                continue
            P = np.array(pts)
            d = np.min(np.hypot(X[:, None] + px - P[None, :, 0], Y[:, None] + py - P[None, :, 1]), axis=1)
            cost += d * (0.2 if net in WEAK_NETS else NET_WEIGHT.get(net, 1.0))
        k = int(np.argmin(cost))
        if best is None or cost[k] < best[0]:
            best = (float(cost[k]), float(X[k]), float(Y[k]), rot)
    return best


def member_ok(g, fp, rot):
    """Legal map for one footprint at rotation rot, indexed by its courtyard top-left cell, plus geometry."""
    fp.SetPosition(pcbnew.VECTOR2I(0, 0)); fp.SetOrientationDegrees(rot)
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    r = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
    cx0, cy0 = r.GetX() / 1e6, r.GetY() / 1e6
    ch, cw = int(math.ceil(r.GetHeight() / 1e6 / RES)) + 1, int(math.ceil(r.GetWidth() / 1e6 / RES)) + 1
    ok = window_free(g.crt | ~g.allowed, ch, cw)
    pads = []
    for p in fp.Pads():
        if not p.IsOnLayer(pcbnew.F_Cu):
            continue
        pr = p.GetBoundingBox()
        net = p.GetNetname()
        other = np.zeros_like(g.crt)
        for n, arr in g.cu.items():
            if n != net or not net:
                other |= arr
        pf = window_free(other, int(math.ceil(pr.GetHeight() / 1e6 / RES)) + 1, int(math.ceil(pr.GetWidth() / 1e6 / RES)) + 1)
        ok &= shift(pf, int(round((pr.GetY() / 1e6 - cy0) / RES)), int(round((pr.GetX() / 1e6 - cx0) / RES)))
        pads.append((net, p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
    return ok, cx0, cy0, pads


def place_group(g, members, net_points):
    """members: [(fp, dx, dy, rot)] relative to the group anchor. Rigid placement, 4 group rotations."""
    best = None
    for R in (0, 90, 180, 270):
        c, sn = round(math.cos(math.radians(R))), round(math.sin(math.radians(R)))
        ok_all = np.ones_like(g.crt)
        geo = []
        for fp, dx, dy, rot in members:
            ox, oy = dx * c + dy * sn, -dx * sn + dy * c          # KiCad rotation (y down, CCW positive)
            ok, cx0, cy0, pads = member_ok(g, fp, (rot + R) % 360)
            ok_all &= shift(ok, int(round((oy + cy0) / RES)), int(round((ox + cx0) / RES)))
            geo.append((fp, ox, oy, (rot + R) % 360, pads))
        js, is_ = np.nonzero(ok_all)
        if len(js) == 0:
            continue
        AX, AY = g.x0 + is_ * RES, g.y0 + js * RES
        cost = np.zeros(len(js))
        for fp, ox, oy, rr, pads in geo:
            for net, px, py in pads:
                pts = net_points.get(net)
                if not net or not pts:
                    continue
                P = np.array(pts)
                d = np.min(np.hypot(AX[:, None] + ox + px - P[None, :, 0], AY[:, None] + oy + py - P[None, :, 1]), axis=1)
                cost += d * (0.2 if net in WEAK_NETS else NET_WEIGHT.get(net, 1.0))
        k = int(np.argmin(cost))
        if best is None or cost[k] < best[0]:
            best = (float(cost[k]), float(AX[k]), float(AY[k]), R, geo)
    return best


def main(pcb, plan_path, refs, box=None, keepouts=()):
    b = pcbnew.LoadBoard(pcb)
    g = Grid(b)
    if box:     # restrict candidate courtyards to this rectangle (x0, y0, x1, y1)
        inside = np.zeros_like(g.allowed)
        j0, j1, i0, i1 = g.idx(*box)
        inside[j0:j1, i0:i1] = True
        g.allowed &= inside
    for k in keepouts:  # mechanical keep-outs (e.g. the Game Boy soft-button contact area)
        j0, j1, i0, i1 = g.idx(*k)
        g.allowed[j0:j1, i0:i1] = False
    staged = set(refs)
    on = [f for f in b.GetFootprints() if g.bb.Contains(f.GetPosition()) and f.GetReference() not in staged]
    for f in on:
        g.add_footprint(f)
    for t in b.GetTracks():
        g.add_track(t)
    net_points = {}
    for f in on:
        for p in f.Pads():
            net_points.setdefault(p.GetNetname(), []).append((p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
    try:
        plan = json.load(open(plan_path))
    except FileNotFoundError:
        plan = []
    by_ref = {e['ref']: e for e in plan}
    groups = json.load(open(plan_path.replace('placement.json', 'groups.json'))) if '@' in ''.join(refs) else {}
    for ref in refs:
        if ref.startswith('@'):
            grp = groups[ref[1:]]
            members = [(b.FindFootprintByReference(m['ref']), m['dx'], m['dy'], m.get('rot', 0)) for m in grp['members']]
            res = place_group(g, members, net_points)
            if res is None:
                print(f'{ref}: NO LEGAL POSITION')
                continue
            cost, ax, ay, R, geo = res
            for fp, ox, oy, rr, pads in geo:
                fp.SetPosition(pcbnew.VECTOR2I(mm(ax + ox), mm(ay + oy))); fp.SetOrientationDegrees(rr)
                g.add_footprint(fp)
                for p in fp.Pads():
                    net_points.setdefault(p.GetNetname(), []).append((p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
                r = fp.GetReference()
                by_ref[r] = dict(ref=r, x=round(ax + ox, 3), y=round(ay + oy, 3), rot=rr,
                                 why=f'group {ref[1:]} ({grp["why"]}); group placed by autoplace.py at ({ax:.2f}, {ay:.2f}) rot {R}, wirelength {cost:.1f} mm')
            print(f'{ref}: anchor ({ax:.2f}, {ay:.2f}) rot {R}  wirelength {cost:.1f} mm')
            continue
        fp = b.FindFootprintByReference(ref)
        if fp is None:
            sys.exit(f'{ref!r}: not on the board')
        res = place_one(b, g, fp, net_points)
        if res is None:
            print(f'{ref}: NO LEGAL POSITION')
            continue
        cost, x, y, rot = res
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y))); fp.SetOrientationDegrees(rot)
        g.add_footprint(fp)
        for p in fp.Pads():
            net_points.setdefault(p.GetNetname(), []).append((p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
        by_ref[ref] = dict(ref=ref, x=round(x, 2), y=round(y, 2), rot=rot,
                           why=f'autoplace.py: legal (courtyard + copper clearance), min wirelength {cost:.1f} mm')
        print(f'{ref}: ({x:.2f}, {y:.2f}) rot {rot}  wirelength {cost:.1f} mm')
    order = [e['ref'] for e in plan] + [r for r in by_ref if r not in {e['ref'] for e in plan}]
    json.dump([by_ref[r] for r in order if r in by_ref], open(plan_path, 'w'), indent=1)
    b.Save(pcb)


if __name__ == '__main__':
    a = sys.argv[1:]
    box = [float(v) for v in a[a.index('--box') + 1:a.index('--box') + 5]] if '--box' in a else None
    if box:
        del a[a.index('--box'):a.index('--box') + 5]
    # SW1 (Croco "GameBoy_soft_button") contact pads span 149.8-158.5 x 76.5-84.5: keep parts out of it
    main(a[0], a[1], a[2:], box, keepouts=[(149.8, 76.5, 158.5, 84.5)])
