#!/usr/bin/env python3
"""Remove the dangling vias/tracks KiCad DRC reports for the given nets, one DRC round per call.
Exact matching only: a via by net + position, a track by net + layer + reported length + an end at the
reported position (never other copper meeting at that point). Re-run until it reports nothing; DRC after.
Usage: prune_dangling.py PCB NET[,NET...]"""
import json, os, re, subprocess, sys, tempfile
import pcbnew
KC = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
pcb, nets = sys.argv[1], set(sys.argv[2].split(','))
js = tempfile.mktemp(suffix='.json')
subprocess.run([KC, 'pcb', 'drc', '--format', 'json', '-o', js, pcb], capture_output=True)
d = json.load(open(js)); os.remove(js)
b = pcbnew.LoadBoard(pcb)
L = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
tracks = list(b.GetTracks()); kill = []
for v in d['violations']:
    if v['type'] not in ('via_dangling', 'track_dangling'):
        continue
    it = v['items'][0]; desc = it['description']; net = desc.split('[', 1)[1].split(']', 1)[0]
    if net not in nets:
        continue
    P = pcbnew.VECTOR2I(pcbnew.FromMM(it['pos']['x']), pcbnew.FromMM(it['pos']['y']))
    if v['type'] == 'via_dangling':
        m = [t for t in tracks if t.GetClass() == 'PCB_VIA' and t.GetNetname() == net and (t.GetPosition() - P).EuclideanNorm() < 5000]
    else:
        lay = L[re.search(r' on (\S+),', desc).group(1)]; ln = float(re.search(r'length ([\d.]+) mm', desc).group(1))
        m = [t for t in tracks if t.GetClass() == 'PCB_TRACK' and t.GetNetname() == net and t.GetLayer() == lay
             and abs(t.GetLength() / 1e6 - ln) < 0.002
             and min((t.GetStart() - P).EuclideanNorm(), (t.GetEnd() - P).EuclideanNorm(), 10**9) < 5000 or
             (t.GetClass() == 'PCB_TRACK' and t.GetNetname() == net and t.GetLayer() == lay and abs(t.GetLength() / 1e6 - ln) < 0.002 and t.HitTest(P, 5000))]
    if len(m) != 1:
        print(f'skip ({len(m)} matches): {desc} @ {it["pos"]}'); continue
    kill.append(m[0]); print(f'rm {desc} @ {it["pos"]["x"]:.2f},{it["pos"]["y"]:.2f}')
for t in kill:
    b.Remove(t)
b.Save(pcb)
print(f'removed {len(kill)}')
