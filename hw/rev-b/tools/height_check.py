#!/usr/bin/env python3
"""Top-side part heights vs the shell (LOGBOOK 04:54: 3.3-3.85 mm above the top side in the label half of a DMG
shell, full-width ribs near ~11 mm and ~62 mm from the connector end).

Reads every top-side footprint's 3D model (STEP points in mm, VRML points in KiCad's 0.1 inch units), applies the
footprint's model scale / rotation / offset, and reports the highest point above the board. Parts without a model
use the datasheet height in NO_MODEL. Exit 1 if any part is taller than BUDGET; parts taller than RIB_TALL are
listed with their distance from the connector end, for a look at the rib bands.
Run with KiCad's Python: .../KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
    hw/rev-b/tools/height_check.py hw/rev-b/GameboyCartridgeV2.1.kicad_pcb
"""
import math, os, re, sys
import pcbnew

BUDGET = 3.3        # mm, the lower end of the measured 3.3-3.85 mm
RIB_TALL = 2.5      # mm, list taller parts against the rib bands
RIBS = (11.0, 62.0)  # mm from the connector end (approximate)
NO_MODEL = {        # height in mm, source
    'U103': (1.0, 'TPS63802 DLA (VSON-HR 2x3): package max 1.0 mm, SLVSEU9D mechanical'),
    'U104': (2.55, 'ESP32-C3-MINI-1U: 2.55 mm max (module datasheet, LOGBOOK 04:54)'),
    'Q101': (0.80, 'SiA975DJ PowerPAK SC-70: A max 0.80 mm (Vishay datasheet, package drawing)'),
    'J4': (0.05, 'Tag-Connect TC2030-NL: pads only'),
    'JP1': (0.05, 'solder jumper: pads only'),
    'J5': (0.05, 'edge connector fingers'),
    'U107': (1.0, 'CH334P QFN-16 3x3: the referenced KiCad model is not in KiCad 10; generic QFN max 1.0 mm (assumed)'),
}
# datasheet beats the KiCad model where both exist
OVERRIDE = {
    'J1': (3.46, 'GCT USB4105 drawing (4 Oct 2019, rev B4): 3.31 mm above PCB +-0.15 (KiCad model: 3.73 mm)'),
}
# reviewed exceptions: may exceed BUDGET
EXCEPT = {
    'J1': 'Croco V2.1 original part and position, at the top edge where the USB-C plug (6.5 mm max) needs a shell '
          'cut-out anyway; the cut-out also clears the receptacle',
}
ENV = {}


def resolve(path):
    for k, v in ENV.items():
        path = path.replace('${%s}' % k, v)
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), path)
    return path


def points(path):
    """model points in mm (model coordinates)."""
    base, ext = os.path.splitext(path)
    wrl = base + '.wrl'
    if not os.path.exists(path):     # KiCad 10 ships STEP only: Croco's .wrl references resolve to the .step
        for e in ('.step', '.STEP', '.stp'):
            if os.path.exists(base + e):
                path, ext = base + e, e
                break
    if os.path.exists(wrl):          # KiCad ships VRML next to STEP; VRML unit = 0.1 inch
        txt = open(wrl, errors='ignore').read()
        out = []
        for blk in re.findall(r'point\s*\[([^\]]*)\]', txt):
            nums = [float(x) for x in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', blk)]
            out += [(nums[i] * 2.54, nums[i + 1] * 2.54, nums[i + 2] * 2.54) for i in range(0, len(nums) - 2, 3)]
        if out:
            return out
    if os.path.exists(path) and ext.lower() in ('.step', '.stp'):
        txt = open(path, errors='ignore').read()
        out = []
        for m in re.finditer(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)", txt):
            v = [float(x) for x in m.group(1).split(',')]
            if len(v) == 3:
                out.append(tuple(v))
        return out
    return None


def rot(p, rx, ry, rz):
    x, y, z = p
    for ang, ax in ((rx, 'x'), (ry, 'y'), (rz, 'z')):
        a = math.radians(ang); c, s = math.cos(a), math.sin(a)
        if ax == 'x':
            y, z = y * c - z * s, y * s + z * c
        elif ax == 'y':
            x, z = x * c + z * s, -x * s + z * c
        else:
            x, y = x * c - y * s, x * s + y * c
    return x, y, z


def main():
    kd = '/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels'
    for k in ('KICAD10_3DMODEL_DIR', 'KICAD9_3DMODEL_DIR', 'KICAD8_3DMODEL_DIR', 'KICAD7_3DMODEL_DIR',
              'KICAD6_3DMODEL_DIR', 'KISYS3DMOD'):
        ENV[k] = os.environ.get(k, kd)
    ENV['KIPRJMOD'] = os.path.dirname(os.path.abspath(sys.argv[1]))
    b = pcbnew.LoadBoard(sys.argv[1])
    j5 = b.FindFootprintByReference('J5')
    ec = b.GetBoardEdgesBoundingBox()
    y0, y1 = ec.GetY() / 1e6, (ec.GetY() + ec.GetHeight()) / 1e6
    jy = j5.GetPosition().y / 1e6
    conn_edge = y0 if abs(jy - y0) < abs(jy - y1) else y1
    rows, fails = [], []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        if fp.GetLayer() != pcbnew.F_Cu or fp.IsDNP() or not ref.strip():
            continue
        h, src = None, ''
        for m in fp.Models():
            if not m.m_Show:
                continue
            pts = points(resolve(m.m_Filename))
            if not pts:
                continue
            sc, ro, of = m.m_Scale, m.m_Rotation, m.m_Offset
            zs = [rot((x * sc.x, y * sc.y, z * sc.z), -ro.x, -ro.y, -ro.z)[2] + of.z for x, y, z in pts]
            top = max(zs)
            h = top if h is None else max(h, top)
            src = os.path.basename(m.m_Filename)
        if ref in OVERRIDE:
            h, src = OVERRIDE[ref][0], 'datasheet: ' + OVERRIDE[ref][1]
        if h is None:
            if ref in NO_MODEL:
                h, src = NO_MODEL[ref][0], 'datasheet: ' + NO_MODEL[ref][1]
            else:
                fails.append(f'{ref}: no 3D model and no datasheet height'); continue
        d = abs(fp.GetPosition().y / 1e6 - conn_edge)
        rows.append((h, ref, str(fp.GetFPID().GetLibItemName()), d, src))
    rows.sort(reverse=True)
    print(f'{len(rows)} top-side parts; budget {BUDGET} mm (shell, label half), ribs ~{RIBS} mm from the connector end')
    for h, ref, name, d, src in rows:
        if h > BUDGET and ref not in EXCEPT and f'{ref} {h:.2f} mm > {BUDGET} mm' not in fails:
            fails.append(f'{ref} {h:.2f} mm > {BUDGET} mm')
    for h, ref, name, d, src in rows[:15]:
        flag = ('EXCEPTION' if ref in EXCEPT else 'OVER BUDGET') if h > BUDGET else ('near a rib' if h > RIB_TALL and any(abs(d - r) < 4 for r in RIBS) else '')
        print(f'  {h:5.2f} mm  {ref:6} {name[:34]:34} {d:5.1f} mm from connector  {flag:12} {src[:50]}')
        if h > BUDGET and ref not in EXCEPT:
            fails.append(f'{ref} {h:.2f} mm > {BUDGET} mm')
    for ref, why in EXCEPT.items():
        print(f'exception {ref}: {why}')
    tall_rib = [r for r in rows if r[0] > RIB_TALL and any(abs(r[3] - x) < 4 for x in RIBS)]
    print(f'parts > {RIB_TALL} mm within 4 mm of a rib band: {[(r[1], round(r[0], 2), round(r[3], 1)) for r in tall_rib] or "none"}')
    for f in fails:
        print('FAIL', f)
    print('RESULT:', 'PASS' if not fails else 'FAIL')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
