#!/usr/bin/env python3
"""Connect unconnected plane-net items to their inner plane with a via (KiCad Python).

For every DRC 'unconnected_items' entry on a net that has a plane on In1 (GND) or In4 (VCC, +5V, VBUS, +3V3),
take each endpoint item (pad, or end of a track piece) and look for a via position within MAX_R of it such that
  - the via lies inside that net's plane outline on the plane layer (filled zone),
  - the via, and an F.Cu (or the item's layer) stub from the item to it, keep CLEAR from every other-net
    copper on all layers and from board edges,
then add stub + via. One via per item is enough (the plane does the rest). Prints every addition.
Usage: stitch_planes.py PCB drc.json
"""
import json, math, sys
import pcbnew

MAX_R, STEP, CLEAR = 1.6, 0.1, 0.16
VIA_D, VIA_H, STUB_W = 0.5, 0.2, 0.25
PLANE_LAYER = {'GND': pcbnew.In1_Cu, 'VCC': pcbnew.In4_Cu, '+5V': pcbnew.In4_Cu, 'VBUS': pcbnew.In4_Cu, '+3V3': pcbnew.In4_Cu}


def mm(v):
    return pcbnew.FromMM(v)


def main(pcb, drc_json):
    b = pcbnew.LoadBoard(pcb)
    filler = pcbnew.ZONE_FILLER(b); filler.Fill(b.Zones())
    d = json.load(open(drc_json))
    edge = pcbnew.SHAPE_POLY_SET(); b.GetBoardPolygonOutlines(edge, True)
    edge.Deflate(mm(0.4), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.01))
    obstacles = []      # (shape, layerset, net)
    for t in b.GetTracks():
        for l in t.GetLayerSet().CuStack():
            obstacles.append((t.GetEffectiveShape(l), l, t.GetNetname()))
    for f in b.GetFootprints():
        for p in f.Pads():
            for l in p.GetLayerSet().CuStack():
                obstacles.append((p.GetEffectiveShape(l), l, p.GetNetname()))
    # zones are not obstacles: they are refilled around the new via/stub
    plane_poly = {}
    for z in b.Zones():
        n = z.GetNetname()
        if n in PLANE_LAYER and z.IsOnLayer(PLANE_LAYER[n]):
            plane_poly.setdefault(n, []).append(z.GetFilledPolysList(PLANE_LAYER[n]))

    def free(shape, layer, net):
        for o in obstacles:
            if o[1] != layer or o[2] == net:
                continue
            if o[0].Collide(shape, mm(CLEAR)):
                return False
        return True

    done, added = set(), 0
    for u in d['unconnected_items']:
        for it in u['items']:
            desc = it['description']
            net = desc.split('[', 1)[1].split(']', 1)[0] if '[' in desc else ''
            if net not in PLANE_LAYER:
                continue
            x, y = it['pos']['x'], it['pos']['y']
            key = (net, round(x, 2), round(y, 2))
            if key in done:
                continue
            layer = pcbnew.F_Cu if 'B.Cu' not in desc else pcbnew.B_Cu
            best = None
            r = 0.0
            while r <= MAX_R and best is None:
                n = max(1, int(2 * math.pi * r / STEP))
                for k in range(n):
                    a = 2 * math.pi * k / n
                    vx, vy = x + r * math.cos(a), y + r * math.sin(a)
                    P = pcbnew.VECTOR2I(mm(vx), mm(vy))
                    if not edge.Contains(P):
                        continue
                    if not any(pp.Contains(P) for pp in plane_poly.get(net, [])):
                        continue
                    via = pcbnew.SHAPE_CIRCLE(P, mm(VIA_D / 2))
                    if not all(free(via, l, net) for l in b.GetEnabledLayers().CuStack()):
                        continue
                    if r > 0:
                        stub = pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(mm(x), mm(y)), P, mm(STUB_W))
                        if not free(stub, layer, net):
                            continue
                    best = (vx, vy)
                    break
                r += STEP
            if best is None:
                print(f'NO SPOT  {net} at ({x:.2f}, {y:.2f}): {desc[:60]}')
                continue
            ni = b.FindNet(net)
            v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(best[0]), mm(best[1])))
            v.SetWidth(mm(VIA_D)); v.SetDrill(mm(VIA_H)); v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(ni); b.Add(v)
            obstacles.append((v.GetEffectiveShape(pcbnew.F_Cu), pcbnew.F_Cu, net))
            if (best[0], best[1]) != (x, y):
                t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(x), mm(y))); t.SetEnd(v.GetPosition())
                t.SetWidth(mm(STUB_W)); t.SetLayer(layer); t.SetNet(ni); b.Add(t)
            done.add(key); added += 1
            print(f'via      {net} at ({best[0]:.2f}, {best[1]:.2f}) for {desc[:60]}')
    b.Save(pcb)
    print(f'added {added} vias')


if __name__ == '__main__':
    main(*sys.argv[1:3])
