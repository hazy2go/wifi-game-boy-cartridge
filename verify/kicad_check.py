#!/usr/bin/env python3
"""KiCad ERC/DRC gate against a recorded baseline of the unmodified Croco V2.1 design.

Every violation is fingerprinted by (check type, sorted item descriptions), coordinates excluded so
moved parts keep their fingerprint. Any fingerprint not in the baseline is NEW and fails the gate;
baseline items that disappear are reported as resolved. Schematic-parity and unconnected items must
not grow either.

  verify/kicad_check.py --record   write verify/baseline/{erc,drc}.json from the current design
  verify/kicad_check.py            compare (exit 1 on any new violation)
Reviewed intentional violations live in verify/baseline/accepted.json, each with its reason.
"""
import collections, json, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HW = os.environ.get('HW_DIR', os.path.join(ROOT, 'hw', 'rev-b'))
SCH = os.path.join(HW, 'GameboyCartridgeV2.1.kicad_sch')
PCB = os.path.join(HW, 'GameboyCartridgeV2.1.kicad_pcb')
BASE = os.path.join(ROOT, 'verify', 'baseline')


def norm(f):
    # KiCad reports a different tiny segment of the same bus/track from run to run ("length 0.0254 mm" vs
    # "0.0508 mm"); the length is not part of the identity of a violation
    return re.sub(r', length [\d.]+ mm', '', f)


def fp(v):
    items = sorted(i.get('description', '') for i in v.get('items', []))
    return norm(v['type'] + ' | ' + ' || '.join(items))


def run():
    tmp = tempfile.mkdtemp()
    erc, drc = os.path.join(tmp, 'erc.json'), os.path.join(tmp, 'drc.json')
    subprocess.run(['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '-o', erc, SCH], check=True, capture_output=True)
    subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--refill-zones',
                    '--schematic-parity', '-o', drc, PCB], check=True, capture_output=True)
    e = json.load(open(erc))
    d = json.load(open(drc))
    return dict(
        erc=sorted(fp(v) for s in e['sheets'] for v in s['violations']),
        drc=sorted(fp(v) for v in d['violations']),
        unconnected=sorted(fp(v) for v in d.get('unconnected_items', [])),
        parity=sorted(fp(v) for v in d.get('schematic_parity', [])),
    )


def main():
    cur = run()
    if '--record' in sys.argv:
        os.makedirs(BASE, exist_ok=True)
        json.dump(cur, open(os.path.join(BASE, 'kicad.json'), 'w'), indent=1)
        print({k: len(v) for k, v in cur.items()}, 'recorded')
        return 0
    base = {k: [norm(f) for f in v] for k, v in json.load(open(os.path.join(BASE, 'kicad.json'))).items()}
    # reviewed, intentional new violations: {category: [{"fp": fingerprint, "why": reason}]}
    acc_path = os.path.join(BASE, 'accepted.json')
    accepted = json.load(open(acc_path)) if os.path.exists(acc_path) else {}
    for k, lst in accepted.items():
        for a in lst:
            if a['fp'] in cur[k]:
                print(f"ACCEPTED {k}: {a['fp'][:120]}  ({a['why']})")
                base[k] = base[k] + [a['fp']]
    bad = 0
    for k in cur:
        new = collections.Counter(cur[k]) - collections.Counter(base[k])
        gone = collections.Counter(base[k]) - collections.Counter(cur[k])
        status = 'PASS' if not new else 'FAIL'
        print(f"{status}  {k}: {len(cur[k])} now, {len(base[k])} baseline, {sum(new.values())} new, {sum(gone.values())} resolved")
        for f in list(new)[:25]:
            print('      NEW  ', f[:220])
        bad += sum(new.values())
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
