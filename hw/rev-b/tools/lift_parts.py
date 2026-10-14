#!/usr/bin/env python3
"""Lift Croco parts off the board for re-placement (KiCad Python).

For each given reference: move the footprint to the staging area right of the board, and rip up the copper
that only served it: starting at each of its pads, follow connected track segments of the same net and delete
them, stopping at (and keeping) vias, at pads of parts that stay, and at branch points (3 or more segments),
so shared routing of other parts is never removed. Prints what was removed per net.
Usage: lift_parts.py PCB REF [REF ...]
"""
import collections, sys
import pcbnew

STAGE_X, STAGE_Y = 175.0, 130.0


def key(p):
    return (round(p.x / 1000), round(p.y / 1000))      # 1 um buckets


def main(pcb, refs):
    b = pcbnew.LoadBoard(pcb)
    lift = set(refs)
    segs = [t for t in b.GetTracks() if t.GetClass() in ('PCB_TRACK', 'PCB_ARC')]
    vias = {key(v.GetPosition()) for v in b.GetTracks() if v.GetClass() == 'PCB_VIA'}
    ends = collections.defaultdict(list)
    for i, t in enumerate(segs):
        ends[(t.GetNetname(), t.GetLayer(), key(t.GetStart()))].append(i)
        ends[(t.GetNetname(), t.GetLayer(), key(t.GetEnd()))].append(i)
    stay_pads = set()
    lift_pads = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if fp.GetReference() in lift:
                lift_pads.append(p)
            else:
                stay_pads.add((p.GetNetname(), key(p.GetPosition())))
    dead, removed = set(), collections.Counter()
    for p in lift_pads:
        net = p.GetNetname()
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            if not p.IsOnLayer(layer):
                continue
            # segments that end inside the pad
            start = [i for i, t in enumerate(segs) if t.GetNetname() == net and t.GetLayer() == layer and
                     (p.HitTest(t.GetStart()) or p.HitTest(t.GetEnd()))]
            stack = list(start)
            while stack:
                i = stack.pop()
                if i in dead:
                    continue
                dead.add(i)
                removed[net] += 1
                t = segs[i]
                for pt in (t.GetStart(), t.GetEnd()):
                    k = key(pt)
                    if k in vias or (net, k) in stay_pads or p.HitTest(pt):
                        continue
                    nxt = [j for j in ends[(net, layer, k)] if j not in dead]
                    if len(ends[(net, layer, k)]) >= 3:
                        continue             # branch point: other parts hang here, stop
                    stack.extend(nxt)
    for i in sorted(dead):
        b.Delete(segs[i])
    for n, fp in enumerate(sorted((f for f in b.GetFootprints() if f.GetReference() in lift), key=lambda f: f.GetReference())):
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(STAGE_X + (n % 6) * 8), pcbnew.FromMM(STAGE_Y + (n // 6) * 8)))
    b.Save(pcb)
    print(f'lifted {sorted(lift)}; removed track segments per net: {dict(removed)}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
