#!/usr/bin/env python3
"""Import the new routing from a Freerouting .ses into the board (KiCad Python).

KiCad 10's pcbnew.ImportSpecctraSES() returns False without a message on Freerouting 2.5 sessions, so this
reads the session directly. Wires and vias marked "(type protect)" are the board's existing fixed copper and
are skipped; everything else is added as new tracks/vias with the session's widths and via padstacks
("Via[0-5]_D:H_um" -> diameter D, drill H). Coordinates: session resolution (um, N) and Specctra's y-up axis.
Usage: import_ses.py PCB board.ses
"""
import re, sys
import pcbnew


def tokens(text):
    return re.findall(r'\(|\)|"[^"]*"|[^\s()]+', text)


def parse(toks, i=0):
    out = []
    while i < len(toks):
        t = toks[i]
        if t == '(':
            sub, i = parse(toks, i + 1)
            out.append(sub)
        elif t == ')':
            return out, i + 1
        else:
            out.append(t.strip('"'))
            i += 1
    return out, i


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def main(pcb, ses):
    tree, _ = parse(tokens(open(ses).read()))
    session = tree[0]
    routes = find(session, 'routes')[0]
    res = find(routes, 'resolution')[0]
    scale = {'um': 1.0, 'mil': 25.4, 'mm': 1000.0}[res[1]] / float(res[2])     # session unit -> um
    b = pcbnew.LoadBoard(pcb)
    layers = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
    nets = b.GetNetsByName()
    to_nm = lambda v: int(round(float(v) * scale * 1000))
    ntr = nvia = 0
    for net in find(find(routes, 'network_out')[0], 'net'):
        name = net[1]
        ni = b.FindNet(name)
        if ni is None:
            sys.exit(f'net {name!r} not on the board')
        for w in find(net, 'wire'):
            if any(t[1] == 'protect' for t in find(w, 'type')):
                continue
            path = find(w, 'path')[0]
            layer, width, pts = path[1], to_nm(path[2]), path[3:]
            xy = [(to_nm(pts[k]), -to_nm(pts[k + 1])) for k in range(0, len(pts), 2)]
            for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(x0, y0)); t.SetEnd(pcbnew.VECTOR2I(x1, y1))
                t.SetWidth(width); t.SetLayer(layers[layer]); t.SetNet(ni)
                b.Add(t); ntr += 1
        for v in find(net, 'via'):
            if any(t[1] == 'protect' for t in find(v, 'type')):
                continue
            m = re.match(r'Via\[0-5\]_(\d+):(\d+)_um', v[1])
            via = pcbnew.PCB_VIA(b)
            via.SetPosition(pcbnew.VECTOR2I(to_nm(v[2]), -to_nm(v[3])))
            via.SetWidth(int(m.group(1)) * 1000); via.SetDrill(int(m.group(2)) * 1000)
            via.SetViaType(pcbnew.VIATYPE_THROUGH); via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); via.SetNet(ni)
            b.Add(via); nvia += 1
    b.Save(pcb)
    print(f'imported {ntr} track segments and {nvia} vias')


if __name__ == '__main__':
    main(*sys.argv[1:3])
