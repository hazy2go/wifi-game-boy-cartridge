#!/usr/bin/env python3
"""Effective DC resistance of the routed copper between two pads of one net (KiCad Python + numpy).

Conductance network: nodes = track ends, via centres, pad centres; edges = tracks (rho*L/(w*t), t by layer),
vias (barrel: rho*h/(pi*d*t_plating)), pad-to-touching-copper (0), and zone fills: every item that touches a
filled zone of the net is joined to its ZK nearest zone neighbours with R = Rsq * d / ZW (ZW = assumed
current-spreading width, deliberately pessimistic). Solved as a resistor network (Laplacian), so parallel
paths count. Copper: outer 35 um (1 oz finished), inner 17.5 um (0.5 oz), plating 18 um, board 0.8 mm (JLC
6-layer 0.8 mm defaults). Usage: path_res.py PCB NET REF.PAD REF.PAD [REF.PAD ...] [--zw 1.0]
(the first pad is the source, every other pad is reported against it)"""
import math, sys
import numpy as np
import pcbnew
RHO, T_OUT, T_IN, T_PLATE, H = 1.72e-8, 35e-6, 17.5e-6, 18e-6, 0.8e-3
ZK = 4


def main():
    a = [x for i, x in enumerate(sys.argv[1:], 1) if not x.startswith('--') and sys.argv[i - 1] != '--zw']
    pcb, net, pads = a[0], a[1], a[2:]
    zw = float(sys.argv[sys.argv.index('--zw') + 1]) if '--zw' in sys.argv else 1.0
    b = pcbnew.LoadBoard(pcb)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    outer = {pcbnew.F_Cu, pcbnew.B_Cu}
    nodes, edges = {}, []

    def node(x, y, l):
        k = (round(x / 1000), round(y / 1000), l)      # 1 um grid
        return nodes.setdefault(k, len(nodes))
    items = []   # (shape per layer, node, layer) for touch tests
    for t in b.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.GetClass() == 'PCB_VIA':
            p = t.GetPosition(); layers = [l for l in t.GetLayerSet().CuStack()]
            layers.sort(key=lambda l: list(b.GetEnabledLayers().CuStack()).index(l))
            ns = [node(p.x, p.y, l) for l in layers]
            seg = RHO * H / (len(layers) - 1) / (math.pi * t.GetDrill() / 1e9 * T_PLATE)
            for i in range(len(ns) - 1):
                edges.append((ns[i], ns[i + 1], seg))
            for l, n in zip(layers, ns):
                items.append((t.GetEffectiveShape(l), n, l))
        else:
            l = t.GetLayer(); s, e = t.GetStart(), t.GetEnd()
            n1, n2 = node(s.x, s.y, l), node(e.x, e.y, l)
            th = T_OUT if l in outer else T_IN
            r = RHO * (t.GetLength() / 1e9) / (t.GetWidth() / 1e9 * th)
            edges.append((n1, n2, max(r, 1e-7)))
            w2 = t.GetWidth() // 2   # only the ends touch other copper (a mid-track crossing of a pad is not a joint)
            items.append((pcbnew.SHAPE_SEGMENT(s, s, 2 * w2), n1, l)); items.append((pcbnew.SHAPE_SEGMENT(e, e, 2 * w2), n2, l))
    padnode = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetNetname() != net:
                continue
            q = p.GetPosition()
            for l in p.GetLayerSet().CuStack():
                if l not in b.GetEnabledLayers().CuStack():
                    continue
                n = node(q.x, q.y, l)
                padnode.setdefault(f'{f.GetReference()}.{p.GetNumber()}', []).append(n)
                items.append((p.GetEffectiveShape(l), n, l))
            ls = [n for n in padnode.get(f'{f.GetReference()}.{p.GetNumber()}', [])]
            for i in range(len(ls) - 1):     # through-hole pad: barrel
                edges.append((ls[i], ls[i + 1], 1e-4))
    # touching items on the same layer are joined (track end on pad, track ends meeting)
    by_layer = {}
    for s, n, l in items:
        by_layer.setdefault(l, []).append((s, n))
    for l, lst in by_layer.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[i][1] != lst[j][1] and lst[i][0].Collide(lst[j][0], 0):
                    edges.append((lst[i][1], lst[j][1], 1e-6))
    # zones
    for z in b.Zones():
        if z.GetNetname() != net:
            continue
        for l in z.GetLayerSet().CuStack():
            if not z.HasFilledPolysForLayer(l):
                continue
            fp = z.GetFilledPolysList(l)
            th = T_OUT if l in outer else T_IN; rsq = RHO / th
            touch = [(n, None) for s, n, ll in items if ll == l and fp.Collide(s, 0)]
            pts = {}
            for n, _ in touch:
                k = [kk for kk, v in nodes.items() if v == n][0]; pts[n] = (k[0] / 1e3, k[1] / 1e3)
            ids = list(pts)
            for i in ids:
                d = sorted(((math.dist(pts[i], pts[j]), j) for j in ids if j != i))[:ZK]
                for dist, j in d:
                    edges.append((i, j, max(rsq * dist / zw, 1e-6)))
    N = len(nodes)
    G = np.zeros((N, N))
    for i, j, r in edges:
        g = 1.0 / r; G[i, i] += g; G[j, j] += g; G[i, j] -= g; G[j, i] -= g
    src = padnode[pads[0]][0]
    for p in pads[1:]:
        dst = padnode[p][0]
        I = np.zeros(N); I[src] = 1; I[dst] = -1
        keep = [k for k in range(N) if k != dst]
        try:
            v = np.linalg.lstsq(G[np.ix_(keep, keep)], I[keep], rcond=None)[0]
            r = v[keep.index(src)]
            print(f'{net}: {pads[0]} -> {p}: {r * 1000:.1f} mOhm' + ('  (NOT CONNECTED?)' if r > 100 else ''))
        except np.linalg.LinAlgError:
            print(f'{net}: {pads[0]} -> {p}: singular')


if __name__ == '__main__':
    main()
