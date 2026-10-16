#!/usr/bin/env python3
"""Keepout under the RP2350 core regulator's switching node (KiCad Python). RP2350 datasheet 6.3.8 / Figure 27:
on boards with 4+ layers, cut away the copper immediately underneath LX / VREG_LX. Adds one rule area on In1-In4
(no tracks, vias or pours) shaped as the VREG_LX copper (its F.Cu zone fill, pads and tracks) grown by MARGIN,
and prints the inner-layer tracks it now conflicts with (rip and re-route those). Zones are refilled.
Usage: lx_keepout.py PCB [MARGIN]"""
import sys
import pcbnew

pcb = sys.argv[1]
margin = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2
NET = '/CPU/VREG_LX'
b = pcbnew.LoadBoard(pcb)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
sh = pcbnew.SHAPE_POLY_SET()
for z in b.Zones():
    if z.GetNetname() == NET:
        sh.Append(z.GetFilledPolysList(pcbnew.F_Cu))
for f in b.GetFootprints():
    for p in f.Pads():
        if p.GetNetname() == NET:
            p.TransformShapeToPolygon(sh, pcbnew.F_Cu, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_OUTSIDE)
for t in b.GetTracks():
    if t.GetNetname() == NET:
        t.TransformShapeToPolygon(sh, pcbnew.F_Cu, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_OUTSIDE)
sh.Simplify()
sh.Inflate(pcbnew.FromMM(margin), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
sh.Simplify()
inner = [pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu]
z = pcbnew.ZONE(b)
z.SetIsRuleArea(True)
z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
ls = pcbnew.LSET()
for l in inner:
    ls.AddLayer(l)
z.SetLayerSet(ls)
z.SetZoneName('VREG_LX cut-out (RP2350 DS Fig. 27)')
z.Outline().Append(sh)
b.Add(z)
for t in b.GetTracks():
    for l in inner:
        if t.IsOnLayer(l) and sh.Collide(t.GetEffectiveShape(l)):
            print(f'conflict {t.GetClass()} {t.GetNetname()} on {b.GetLayerName(l)}')
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
bb = sh.BBox()
print(f'keepout on In1-In4: ({bb.GetLeft()/1e6:.2f}, {bb.GetTop()/1e6:.2f})-({bb.GetRight()/1e6:.2f}, {bb.GetBottom()/1e6:.2f}), margin {margin} mm')
