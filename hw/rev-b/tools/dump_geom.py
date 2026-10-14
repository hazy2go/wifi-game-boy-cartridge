#!/usr/bin/env python3
"""Dump top-side geometry (F.Cu pads/tracks/vias, courtyards, outline) to JSON for plotting (KiCad Python).
Usage: dump_geom.py PCB out.json"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); bb = b.GetBoardEdgesBoundingBox()
g = dict(pads=[], tracks=[], vias=[], crt=[], edge=[])
for f in b.GetFootprints():
    if not bb.Contains(f.GetPosition()):
        continue
    r = f.GetCourtyard(pcbnew.F_CrtYd).BBox()
    g['crt'].append([f.GetReference(), r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6, int(f.GetReference()[1:].isdigit() and int(''.join(c for c in f.GetReference() if c.isdigit())) >= 101)])
    for p in f.Pads():
        if p.IsOnLayer(pcbnew.F_Cu):
            r = p.GetBoundingBox()
            g['pads'].append([p.GetNetname(), r.GetX() / 1e6, r.GetY() / 1e6, r.GetRight() / 1e6, r.GetBottom() / 1e6])
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA':
        g['vias'].append([t.GetNetname(), t.GetPosition().x / 1e6, t.GetPosition().y / 1e6, t.GetWidth(pcbnew.F_Cu) / 1e6])
    else:
        g['tracks'].append([t.GetNetname(), b.GetLayerName(t.GetLayer()), t.GetStart().x / 1e6, t.GetStart().y / 1e6, t.GetEnd().x / 1e6, t.GetEnd().y / 1e6, t.GetWidth() / 1e6])
for d in b.GetDrawings():
    if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_SEGMENT:
        g['edge'].append([d.GetStart().x / 1e6, d.GetStart().y / 1e6, d.GetEnd().x / 1e6, d.GetEnd().y / 1e6])
json.dump(g, open(sys.argv[2], 'w'))
