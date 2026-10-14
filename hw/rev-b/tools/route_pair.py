#!/usr/bin/env python3
"""Run finish_route.py for one net between a fixed end A and the first of several candidate ends B that routes.
Each attempt works on a copy; the first success replaces PCB. Ends are 'x,y' (F.Cu) or 'x,y,B' (B.Cu).
Usage: route_pair.py PCB NET AX,AY BX,BY [BX,BY ...] [--best]   (env FR_VIA passes through)"""
import json, os, shutil, subprocess, sys
KPY = '/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3'
FR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'finish_route.py')
pcb, net, a = sys.argv[1:4]


def item(s):
    p = s.split(','); lay = 'B.Cu' if len(p) > 2 else 'F.Cu'
    return {'description': f'Track [{net}] on {lay}', 'pos': {'x': float(p[0]), 'y': float(p[1])}}


best = None   # --best: try every candidate, keep the one with the fewest A* cells
ends = [x for x in sys.argv[4:] if not x.startswith('--')]
for n, bend in enumerate(ends):
    tmp = pcb + f'.try{n}.kicad_pcb'; shutil.copy(pcb, tmp)
    js = pcb + '.pair.json'
    json.dump({'unconnected_items': [{'items': [item(a), item(bend)]}]}, open(js, 'w'))
    out = subprocess.run([KPY, FR, tmp, js], capture_output=True, text=True).stdout
    line = next((l for l in out.splitlines() if l.startswith(('OK', 'FAIL'))), out.strip()[-200:])
    print(f'{bend}: {line}', flush=True)
    if line.startswith('OK'):
        cells = int(line.split(':')[1].split('cells')[0]) if 'cells' in line else 0
        if '--best' not in sys.argv:
            shutil.move(tmp, pcb); sys.exit(0)
        if best is None or cells < best[0]:
            best = (cells, tmp, bend)
if best:
    print(f'best: {best[2]} ({best[0]} cells)'); shutil.move(best[1], pcb); sys.exit(0)
sys.exit(1)
