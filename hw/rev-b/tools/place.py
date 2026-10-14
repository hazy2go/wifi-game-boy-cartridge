#!/usr/bin/env python3
"""Apply placements from docs/design/placement.json to the board (KiCad Python), and rip up copper that a
placement entry declares obsolete. Every entry carries its reason; the JSON is the reviewable record of the
layout decisions.
  {"ref": "U104", "x": 121.0, "y": 84.0, "rot": 90, "why": "...",
   "rip": {"nets": ["GND"], "box": [x0, y0, x1, y1]}}      # optional: delete F.Cu tracks/vias of these nets
                                                            # whose endpoints lie in the box
Usage: place.py PCB placement.json
"""
import json, sys, pcbnew


def main(pcb, plan_path):
    b = pcbnew.LoadBoard(pcb)
    plan = json.load(open(plan_path))
    items = [(t, t.GetNetname(), t.GetStart(), t.GetEnd(), t.IsOnLayer(pcbnew.F_Cu)) for t in b.GetTracks()]
    dead = set()
    for e in plan:
        r = e.get('rip')
        if not r:
            continue
        x0, y0, x1, y1 = [pcbnew.FromMM(v) for v in r['box']]
        inside = lambda p: x0 <= p.x <= x1 and y0 <= p.y <= y1
        for k, (t, n, s, en, fcu) in enumerate(items):
            if n in r['nets'] and fcu and (inside(s) or inside(en)):
                dead.add(k)
    for k in sorted(dead):
        b.Delete(items[k][0])
    for e in plan:
        fp = b.FindFootprintByReference(e['ref'])
        if fp is None:
            sys.exit(f"{e['ref']} not on board")
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(e['x']), pcbnew.FromMM(e['y'])))
        fp.SetOrientationDegrees(e.get('rot', 0))
    b.Save(pcb)
    print(f'placed {len(plan)} parts, ripped {len(dead)} tracks/vias')


if __name__ == '__main__':
    main(*sys.argv[1:3])
