#!/usr/bin/env python3
"""Cross-check: the regulator that was simulated (sim/power/inreg.py DESIGN) is the regulator in the
schematic netlist (docs/design/netlist_radio.json). Every simulated R/C/D/MOSFET/TLV431 must appear in the
netlist between the same nets with the same value, and every regulator part in the netlist must have been
simulated. Exit 1 on any mismatch."""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'sim', 'power'))
import inreg as I

NODE = {'vmux': 'VMUX', 'top': 'VMUX', 'a': 'VLIM', 's': 'REG_S', 'g': 'REG_G', 'k': 'REG_K', 'ref': 'REG_REF', 'ccn': 'REG_CCN',
        'cl1': 'REG_CL1', 'cl2': 'REG_CL2', 'gpor': 'REG_POR_G', 'rss': 'REG_RSS', 'vres': 'VRES', '0': 'GND'}
SKIP = {'Rbias', 'Cg', 'Cmux', 'Ca'}   # 1 Gohm / 1 pF numerical placeholders; Cmux/Ca are C103/C104 (checked below)


def num(v):
    m = re.fullmatch(r'([0-9.eE+-]+)\s*([pnumkKM]?)', v.strip())
    return float(m.group(1)) * {'': 1, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, 'k': 1e3, 'K': 1e3, 'M': 1e6}[m.group(2)]


def part_value(p):
    v = p['value'].split()[0].replace('Ω', '')
    v = v.replace('uF', 'u').replace('nF', 'n').replace('pF', 'p')
    return num(v)


sim = I.core(I.Vendor, dict(I.DESIGN), ramp=False)
els = []
for line in sim.splitlines():
    t = line.split()
    if not t or t[0].startswith(('*', '.', 'B', 'V')):
        continue
    els.append(t)
d = json.load(open(os.path.join(ROOT, 'docs', 'design', 'netlist_radio.json')))
parts = {p['ref']: p for p in d['parts']}
errs, used = [], set()


def find(kind, nets, value=None, sym=None):
    for r, p in parts.items():
        if sym and not p['symbol'].endswith(sym):
            continue
        if not sym and p['symbol'] != f'Device:{kind}':
            continue
        pn = p['pins']
        if kind in ('R', 'C') and sorted(pn.values()) == sorted(nets):
            if value is None or abs(part_value(p) - value) / value < 0.02:
                return r
        if kind == 'D' and (pn['2'], pn['1']) == tuple(nets):
            return r
    return None


for t in els:
    name = t[0]
    if name in SKIP:
        continue
    kind = name[0]
    if kind in 'RC':
        nets = [NODE[t[1]], NODE[t[2]]]
        r = find(kind, nets, num(t[3]))
    elif kind == 'D':
        r = find('D', [NODE[t[1]], NODE[t[2]]])       # anode, cathode
    elif kind == 'M':                                  # Mpor d g s b
        r = next((x for x, p in parts.items() if p['symbol'].endswith('2N7002')
                  and (p['pins']['3'], p['pins']['1'], p['pins']['2']) == (NODE[t[1]], NODE[t[2]], NODE[t[3]])), None)
    elif kind == 'X' and t[-1] == 'SiA975DJ':          # X d g s
        dd, gg, ss = NODE[t[1]], NODE[t[2]], NODE[t[3]]
        r = next((x for x, p in parts.items() if p['symbol'] == 'radio:SiA975DJ'
                  and ss == p['pins']['1'] == p['pins']['4'] and gg == p['pins']['2'] == p['pins']['5']
                  and dd in (p['pins']['3'], p['pins']['6'])), None)
    elif kind == 'X' and t[-1] == 'TLV431':            # X a k ref
        r = next((x for x, p in parts.items() if p['symbol'] == 'radio:TLV431DBZ'
                  and (p['pins']['3'], p['pins']['2'], p['pins']['1']) == (NODE[t[1]], NODE[t[2]], NODE[t[3]])), None)
    else:
        errs.append(f'unhandled sim element {line}')
        continue
    print(f"{'OK  ' if r else 'MISS'} {' '.join(t[:5]):40} -> {r}")
    if r:
        used.add(r)
    else:
        errs.append(f'{name}: not found in netlist')
for ref, nets in (('C103', {'VMUX', 'GND'}), ('C104', {'VLIM', 'GND'})):
    ok = set(parts[ref]['pins'].values()) == nets and abs(part_value(parts[ref]) - 1e-6) < 1e-9
    print(f"{'OK  ' if ok else 'MISS'} Cmux/Ca 1u -> {ref}")
    errs += [] if ok else [f'{ref} != simulated 1 uF']
reg = {r for r, p in parts.items() if any(n.startswith('REG_') for n in p['pins'].values())}
for r in sorted(reg - used):
    errs.append(f'{r}: in the netlist on a regulator net but not simulated')
print('RESULT:', 'PASS' if not errs else 'FAIL\n  ' + '\n  '.join(errs))
sys.exit(1 if errs else 0)
