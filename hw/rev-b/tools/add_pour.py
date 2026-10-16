#!/usr/bin/env python3
"""Add a rectangular copper pour (KiCad Python): lowest priority, so every existing zone wins where they overlap;
pads connect solid, islands are removed, then all zones are refilled.
Usage: add_pour.py PCB NET LAYER X0 Y0 X1 Y1 [CLEARANCE]"""
import sys
import pcbnew

pcb, net, layer = sys.argv[1:4]
x0, y0, x1, y1 = map(float, sys.argv[4:8])
clr = float(sys.argv[8]) if len(sys.argv) > 8 else 0.2
b = pcbnew.LoadBoard(pcb)
z = pcbnew.ZONE(b)
z.SetLayer(b.GetLayerID(layer))
z.SetNet(b.FindNet(net))
z.SetAssignedPriority(0)
z.SetLocalClearance(pcbnew.FromMM(clr))
z.SetMinThickness(pcbnew.FromMM(0.2))
z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
o = z.Outline()
o.NewOutline()
for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
    o.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
b.Add(z)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print(f'{net} pour on {layer} ({x0},{y0})-({x1},{y1}), filled area {z.GetFilledArea() / 1e12:.1f} mm2')
