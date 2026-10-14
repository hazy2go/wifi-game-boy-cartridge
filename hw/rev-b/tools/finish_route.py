#!/usr/bin/env python3
"""Route the remaining unconnected pairs on the inner signal layers In2/In3 (KiCad Python, numpy A*).

For every DRC 'unconnected_items' pair (A, B):
  1. at each end find a via spot within MAX_R that is free on every copper layer (CLEAR to other-net copper,
     inside the outline) and reachable from the item by a short stub on the item's own layer;
  2. A* between the two vias on a RES grid over In2 and In3 (layer change = extra via, cost VIA_COST), where a
     cell is blocked if it lies within (CLEAR + width/2) of other-net copper on that layer; In2 prefers
     horizontal, In3 vertical moves (they are 0.116 mm apart);
  3. add stubs, vias and the path (collinear runs merged). Each new item becomes an obstacle for the next.
Unroutable pairs are reported, never forced. Verify with KiCad DRC afterwards.
Usage: finish_route.py PCB drc.json [--nets NET,NET]
"""
import heapq, json, math, sys
import numpy as np
import pcbnew

RES, CLEAR, MAX_R = 0.05, 0.16, 3.0
VIA_D, VIA_H = 0.5, 0.2
POWER = {'VCC', 'VBUS', '+5V', '+3V3', 'VRES', 'VLIM', '/Radio/VMUX', '/Radio/+3V3_RF', 'GND'}
LAYERS = (pcbnew.In2_Cu, pcbnew.In3_Cu)
ALL = (pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.B_Cu)   # signal layers (In1/In4 planes get antipads)
VIA_COST = 40


def mm(v):
    return pcbnew.FromMM(v)


class Raster:
    def __init__(self, b):
        bb = b.GetBoardEdgesBoundingBox()
        self.x0, self.y0 = bb.GetX() / 1e6, bb.GetY() / 1e6
        self.nx, self.ny = int(bb.GetWidth() / 1e6 / RES) + 2, int(bb.GetHeight() / 1e6 / RES) + 2
        self.b = b
        self.items = []    # (net, layer, shape) for exact collision tests
        self.raw = []      # (net, layer, kind, data) for rasterizing
        for t in b.GetTracks():
            for l in t.GetLayerSet().CuStack():
                self.add_raw(t, l)
        for f in b.GetFootprints():
            for p in f.Pads():
                for l in p.GetLayerSet().CuStack():
                    self.add_raw(p, l)
        for f in b.GetFootprints():
            for g in f.GraphicalItems():
                for l in (pcbnew.F_Cu, pcbnew.B_Cu):
                    if g.IsOnLayer(l):
                        self.items.append(('', l, g.GetEffectiveShape(l)))
                        bb = g.GetBoundingBox()
                        self.raw.append(('', l, 'box', (bb.GetX() / 1e6, bb.GetY() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6)))
        self.outline = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(self.outline, True)
        self.outline.Deflate(mm(0.4), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))
        self.outside = np.ones((self.ny, self.nx), bool)
        ol = pcbnew.SHAPE_POLY_SET(self.outline)
        for j in range(self.ny):
            y = self.y0 + j * RES
            row = [ol.Contains(pcbnew.VECTOR2I(mm(self.x0 + i * RES), mm(y))) for i in range(self.nx)]
            self.outside[j] = ~np.array(row)

    def add_raw(self, it, l):
        n = it.GetNetname()
        self.items.append((n, l, it.GetEffectiveShape(l)))
        c = it.GetClass()
        if c == 'PCB_TRACK':
            d = ('seg', (it.GetStart().x / 1e6, it.GetStart().y / 1e6, it.GetEnd().x / 1e6, it.GetEnd().y / 1e6, it.GetWidth() / 2e6))
        elif c == 'PCB_VIA':
            d = ('circ', (it.GetPosition().x / 1e6, it.GetPosition().y / 1e6, it.GetWidth(l) / 2e6))
        else:
            bb = it.GetBoundingBox()
            d = ('box', (bb.GetX() / 1e6, bb.GetY() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6))
        self.raw.append((n, l) + d)
        return d

    def prepare(self, nets, r):
        """Per inner layer: count of copper items within r of each cell (all nets) and per wanted net."""
        self.r = r
        self.total = {L: np.zeros((self.ny, self.nx), np.int16) for L in ALL}
        self.per = {(n, L): np.zeros((self.ny, self.nx), np.int16) for n in nets for L in ALL}
        for n, l, kind, data in self.raw:
            if l in ALL:
                self._raster(n, l, kind, data, 1)

    def _raster(self, n, l, kind, data, sign):
        r = self.r
        if kind == 'seg':
            ax, ay, cx, cy, w = data; xa, xb, ya, yb = min(ax, cx) - w, max(ax, cx) + w, min(ay, cy) - w, max(ay, cy) + w
        elif kind == 'circ':
            cx, cy, w = data; xa, xb, ya, yb = cx - w, cx + w, cy - w, cy + w
        else:
            xa, ya, xb, yb = data
        i0, i1 = max(int((xa - r - self.x0) / RES), 0), min(int((xb + r - self.x0) / RES) + 2, self.nx)
        j0, j1 = max(int((ya - r - self.y0) / RES), 0), min(int((yb + r - self.y0) / RES) + 2, self.ny)
        if i0 >= i1 or j0 >= j1:
            return
        XX, YY = np.meshgrid(self.x0 + np.arange(i0, i1) * RES, self.y0 + np.arange(j0, j1) * RES)
        if kind == 'seg':
            dx, dy = cx - ax, cy - ay
            L2 = dx * dx + dy * dy or 1e-12
            u = np.clip(((XX - ax) * dx + (YY - ay) * dy) / L2, 0, 1)
            m = np.hypot(XX - (ax + u * dx), YY - (ay + u * dy)) <= w + r
        elif kind == 'circ':
            m = np.hypot(XX - cx, YY - cy) <= w + r
        else:
            m = (XX >= xa - r) & (XX <= xb + r) & (YY >= ya - r) & (YY <= yb + r)
        self.total[l][j0:j1, i0:i1] += sign * m
        if (n, l) in self.per:
            self.per[(n, l)][j0:j1, i0:i1] += sign * m

    def blocked(self, net, layer, halfw):
        g = (self.total[layer] - self.per[(net, layer)]) > 0
        extra = halfw - (self.r - CLEAR)          # rasters were built for half-width 0.075 mm
        if extra > 1e-9:
            k = int(math.ceil(extra / RES))
            gg = g.copy()
            for dj in range(-k, k + 1):
                for di in range(-k, k + 1):
                    if dj * dj + di * di <= k * k:
                        gg |= np.roll(np.roll(g, dj, 0), di, 1)
            g = gg
        return g | self.outside      # outside the outline is blocked too

    def add_item(self, it, l):
        kind, data = self.add_raw(it, l)
        if l in ALL:
            self._raster(it.GetNetname(), l, kind, data, 1)

    def free_shape(self, shape, layer, net):
        return not any(l == layer and n != net and s.Collide(shape, mm(CLEAR)) for n, l, s in self.items)


def via_spot(R, net, x, y, layer, stub_w):
    for t in R.b.GetTracks():
        if t.GetClass() == 'PCB_VIA' and t.GetNetname() == net and math.hypot(t.GetPosition().x / 1e6 - x, t.GetPosition().y / 1e6 - y) < 0.05:
            return x, y, True
    r = 0.0
    while r <= MAX_R:
        n = max(1, int(2 * math.pi * r / RES))
        for k in range(n):
            a = 2 * math.pi * k / n
            vx, vy = x + r * math.cos(a), y + r * math.sin(a)
            P = pcbnew.VECTOR2I(mm(vx), mm(vy))
            if not R.outline.Contains(P):
                continue
            c = pcbnew.SHAPE_CIRCLE(P, mm(VIA_D / 2))
            if not all(R.free_shape(c, l, net) for l in R.b.GetEnabledLayers().CuStack()):
                continue
            if r > 0 and not R.free_shape(pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(mm(x), mm(y)), P, mm(stub_w)), layer, net):
                continue
            return vx, vy, False
        r += RES * 2
    return None


def fanout(R, net, x, y, layer, width, max_len=6.0):
    """BFS on the item's own outer layer from (x, y) to the nearest cell where a via fits on every signal
    layer. Returns [(x, y), ..., (vx, vy)] or None."""
    blk = R.blocked(net, layer, width / 2)
    vr = VIA_D / 2
    vok = ~np.any([R.blocked(net, l, vr) for l in ALL], axis=0)
    j0, i0 = int(round((y - R.y0) / RES)), int(round((x - R.x0) / RES))
    lim = int(max_len / RES)
    from collections import deque
    q = deque([(j0, i0)]); prev = {(j0, i0): None}
    while q:
        j, i = q.popleft()
        if vok[j, i] and (j, i) != (j0, i0):
            path = [(j, i)]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return [(R.x0 + ii * RES, R.y0 + jj * RES) for jj, ii in path[::-1]]
        for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nj, ni = j + dj, i + di
            if (nj, ni) in prev or abs(nj - j0) > lim or abs(ni - i0) > lim:
                continue
            if not (0 <= nj < R.ny and 0 <= ni < R.nx):
                continue
            # the first few cells may sit on the item's own pad copper (same net => not blocked anyway)
            if blk[nj, ni]:
                continue
            prev[(nj, ni)] = (j, i); q.append((nj, ni))
    return None


def astar(grids, a, b, via_ok):
    """grids: [In2, In3] blocked arrays; a, b: (j, i) cells. Returns [(layer_idx, j, i)]."""
    H, W = grids[0].shape
    start = [(0, a[0], a[1]), (1, a[0], a[1])]
    goal = {(0, b[0], b[1]), (1, b[0], b[1])}
    hcost = lambda j, i: abs(j - b[0]) + abs(i - b[1])
    dist, prev, pq = {}, {}, []
    for s in start:
        dist[s] = 0; heapq.heappush(pq, (hcost(s[1], s[2]), 0, s))
    moves = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    while pq:
        f, d, u = heapq.heappop(pq)
        if u in goal:
            path = [u]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        if d > dist.get(u, 1e18):
            continue
        L, j, i = u
        for dj, di in moves:
            nj, ni = j + dj, i + di
            if not (0 <= nj < H and 0 <= ni < W) or grids[L][nj, ni]:
                continue
            step = 1.414 if dj and di else 1.0
            pref = (dj != 0 and di == 0) if L == 0 else (di != 0 and dj == 0)   # In2 vertical-ish penalty
            cost = d + step * (1.3 if pref else 1.0)
            v = (L, nj, ni)
            if cost < dist.get(v, 1e18):
                dist[v] = cost; prev[v] = u; heapq.heappush(pq, (cost + hcost(nj, ni), cost, v))
        o = (1 - L, j, i)
        if via_ok[j, i] and not grids[1 - L][j, i] and d + VIA_COST < dist.get(o, 1e18):
            dist[o] = d + VIA_COST; prev[o] = u; heapq.heappush(pq, (d + VIA_COST + hcost(j, i), d + VIA_COST, o))
    return None


def main(pcb, drc_json, only=None):
    b = pcbnew.LoadBoard(pcb)
    R = Raster(b)
    d = json.load(open(drc_json))
    pairs = []
    for u in d['unconnected_items']:
        its = u['items']
        net = its[0]['description'].split('[', 1)[1].split(']', 1)[0]
        if only and net not in only:
            continue
        pairs.append((net, [(it['pos']['x'], it['pos']['y'], 'B.Cu' in it['description']) for it in its]))
    R.prepare({n for n, _ in pairs}, CLEAR + 0.075)   # base raster for 0.15 mm tracks; wider things dilate
    ok = fail = 0
    for net, ends in pairs:
        ni = b.FindNet(net)
        width = 0.35 if net in POWER else 0.15
        # 0) short gaps: a straight or L-shaped connection on the outer layer of the items, if it is clear
        (xa, ya, ba), (xb, yb, bb_) = ends
        if ba == bb_ and math.hypot(xb - xa, yb - ya) < 8:
            lay = pcbnew.B_Cu if ba else pcbnew.F_Cu
            done = False
            for mid in (None, (xb, ya), (xa, yb)):
                pts = [(xa, ya)] + ([mid] if mid else []) + [(xb, yb)]
                segs = [pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(mm(p[0]), mm(p[1])), pcbnew.VECTOR2I(mm(q[0]), mm(q[1])), mm(width)) for p, q in zip(pts, pts[1:])]
                if all(R.free_shape(sg, lay, net) and R.outline.Contains(sg.GetSeg().B) for sg in segs):
                    for p, q in zip(pts, pts[1:]):
                        t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))); t.SetEnd(pcbnew.VECTOR2I(mm(q[0]), mm(q[1])))
                        t.SetWidth(mm(width)); t.SetLayer(lay); t.SetNet(ni); b.Add(t); R.add_item(t, lay)
                    print(f'OK   {net}: direct on {b.GetLayerName(lay)} ({len(pts) - 1} segments)'); ok += 1; done = True
                    break
            if done:
                continue
        vias = []
        for x, y, bot in ends:
            layer = pcbnew.B_Cu if bot else pcbnew.F_Cu
            s = via_spot(R, net, x, y, layer, min(width, 0.25))
            if s is None:
                fo = fanout(R, net, x, y, layer, 0.15)
                if fo is None:
                    break
                vias.append((fo[-1], (x, y), layer, False, fo))
                continue
            vias.append(((s[0], s[1]), (x, y), layer, s[2], None))
        if len(vias) != 2:
            print(f'FAIL {net}: no via spot near {ends}'); fail += 1; continue
        grids = [R.blocked(net, l, width / 2) for l in LAYERS]
        cell = lambda p: (int(round((p[1] - R.y0) / RES)), int(round((p[0] - R.x0) / RES)))
        for g in grids:                     # the via cells themselves are ours
            for (vx, vy), _, _, _, _ in vias:
                j, i = cell((vx, vy)); g[max(j - 3, 0):j + 4, max(i - 3, 0):i + 4] = False
        # a layer change needs a via that is legal on every signal layer (via radius instead of track half-width)
        vr = VIA_D / 2 - width / 2
        via_ok = ~np.any([R.blocked(net, l, width / 2) for l in ALL], axis=0)
        if vr > 0:
            k = int(math.ceil(vr / RES))
            vo = via_ok.copy()
            for dj in range(-k, k + 1):
                for di in range(-k, k + 1):
                    if dj * dj + di * di <= k * k:
                        vo &= np.roll(np.roll(via_ok, dj, 0), di, 1)
            via_ok = vo
        path = astar(grids, cell(vias[0][0]), cell(vias[1][0]), via_ok)
        if path is None:
            print(f'FAIL {net}: no inner-layer path'); fail += 1; continue
        new = []
        def add_via(x, y):
            v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y))); v.SetWidth(mm(VIA_D)); v.SetDrill(mm(VIA_H))
            v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(ni); b.Add(v); new.append(v)
        def add_seg(p, q, layer, w=None):
            if abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6:
                return
            t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(p[0]), mm(p[1]))); t.SetEnd(pcbnew.VECTOR2I(mm(q[0]), mm(q[1])))
            t.SetWidth(mm(w or width)); t.SetLayer(layer); t.SetNet(ni); b.Add(t); new.append(t)
        for (vx, vy), (x, y), layer, existing, fo in vias:
            if fo:                           # fan-out path: merge collinear steps
                add_via(vx, vy)
                pts_ = [fo[0]]
                for k in range(1, len(fo) - 1):
                    a_, b_, c_ = pts_[-1], fo[k], fo[k + 1]
                    if abs((b_[0] - a_[0]) * (c_[1] - b_[1]) - (b_[1] - a_[1]) * (c_[0] - b_[0])) > 1e-9:
                        pts_.append(b_)
                pts_.append(fo[-1]); pts_[0] = (x, y)
                for p_, q_ in zip(pts_, pts_[1:]):
                    add_seg(p_, q_, layer, 0.15)
                # fan-out tracks use the signal width; power nets get a 0.25 mm neck here at most
            elif not existing:
                add_via(vx, vy); add_seg((x, y), (vx, vy), layer)
        pts = [(L, R.x0 + i * RES, R.y0 + j * RES) for L, j, i in path]
        pts[0] = (pts[0][0], vias[0][0][0], vias[0][0][1]); pts[-1] = (pts[-1][0], vias[1][0][0], vias[1][0][1])
        # merge collinear steps per layer, via at every layer change
        seg_start = pts[0]
        for k in range(1, len(pts)):
            p, q = pts[k - 1], pts[k]
            if q[0] != p[0]:
                add_seg(seg_start[1:], p[1:], LAYERS[p[0]])
                if all(math.hypot(p[1] - vx, p[2] - vy) > 0.6 for (vx, vy), _, _, _, _ in vias):
                    add_via(*p[1:])
                seg_start = q; continue
            nxt = pts[k + 1] if k + 1 < len(pts) else None
            if nxt is not None and nxt[0] == q[0]:
                d1 = (round((q[1] - p[1]) / RES), round((q[2] - p[2]) / RES)); d2 = (round((nxt[1] - q[1]) / RES), round((nxt[2] - q[2]) / RES))
                if d1 == d2:
                    continue
            add_seg(seg_start[1:], q[1:], LAYERS[q[0]]); seg_start = q
        for it in new:
            for l in it.GetLayerSet().CuStack():
                R.add_item(it, l)
        print(f'OK   {net}: {len(path)} cells, {sum(1 for n in new if n.GetClass() == "PCB_VIA")} vias'); ok += 1
    b.Save(pcb)
    print(f'routed {ok}, failed {fail}')


if __name__ == '__main__':
    a = sys.argv[1:]
    only = set(a[a.index('--nets') + 1].split(',')) if '--nets' in a else None
    main(a[0], a[1], only)
