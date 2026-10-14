#!/usr/bin/env python3
"""Back-silk credit (KiCad Python): removes the OSHW and Croco crab logo footprints and writes "co-created with" /
"RemAI" (mirrored, B.SilkS) centred on X. Re-runnable. The avatar itself is added by silk_logo.py.
Usage: silk_credit.py PCB X"""
import sys, pcbnew
pcb = sys.argv[1]
b = pcbnew.LoadBoard(pcb)
kill = [f for f in b.GetFootprints() if f.GetFPID().GetLibItemName().wx_str() in ('OSHW-Logo2_7.3x6mm_SilkScreen', 'LOGO')]
kill += [d for d in b.GetDrawings() if d.GetClass() == 'PCB_TEXT' and d.GetText() in ('RemAI', 'co-created with')]
for k in kill:
    b.Remove(k)
def T(txt, x, y, h, w, th):
    t = pcbnew.PCB_TEXT(b); t.SetText(txt); t.SetLayer(pcbnew.B_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(w), pcbnew.FromMM(h))); t.SetTextThickness(pcbnew.FromMM(th))
    t.SetMirrored(True); t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER); t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
    t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))); b.Add(t)
X = float(sys.argv[2])
T('co-created with', X, 104.3, 1.0, 0.85, 0.16)
T('RemAI', X, 107.9, 2.6, 2.3, 0.4)
b.Save(pcb)
