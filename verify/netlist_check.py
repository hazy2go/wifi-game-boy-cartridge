#!/usr/bin/env python3
"""Static checker for docs/design/netlist_radio.json (the radio + power sheet netlist).

Fails (exit 1) on:
  PIN_UNASSIGNED   a symbol pin with no entry in the JSON (every pin must be a net or "NC")
  PIN_UNKNOWN      a JSON pin number that the symbol does not have
  SYMBOL           symbol not resolvable (KiCad library or docs/design/radio_symbols.json)
  SINGLE_NET       a net with only one connection (Croco's connections count for global nets)
  GLOBAL_UNKNOWN   a global net that exists neither in Croco nor in interface_nets
  NO_SOURCE        a power_in pin on a net with no power source
  POWER_NC         a power_in pin marked NC
  MULTI_DRIVER     two different parts with output/power_out pins on one net
  REF_DUP          duplicated reference, or a reference that clashes with a Croco reference
  BOM              missing/invalid LCSC number or footprint; footprint not found in KiCad libs
  STRAP            ESP32-C3 strapping pin GPIO2/GPIO8/GPIO9 without a defined boot level
                   (required: GPIO2=1, GPIO8=1, GPIO9=1 -> SPI boot, ESP32-C3-MINI-1 DS Table 4-3)
  CROCO_EDIT       a croco_edits entry that does not match the Croco netlist

Usage:
  verify/netlist_check.py [--json docs/design/netlist_radio.json] [--croco-xml FILE]
  verify/netlist_check.py --selftest     mutate the JSON in memory, assert every mutation is caught
Croco netlist: exported on the fly with kicad-cli from hw/rev-b/GameboyCartridgeV2.1.kicad_sch
unless --croco-xml is given.
"""
import argparse, copy, json, os, re, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KICAD = '/Applications/KiCad/KiCad.app/Contents/SharedSupport'
SYMDIR = os.environ.get('KICAD_SYMBOL_DIR', os.path.join(KICAD, 'symbols'))
FPDIR = os.environ.get('KICAD_FOOTPRINT_DIR', os.path.join(KICAD, 'footprints'))
CROCO_SCH = os.path.join(ROOT, 'hw', 'rev-b', 'GameboyCartridgeV2.1.kicad_sch')
CROCO_FP_LIB = os.path.join(ROOT, 'hw', 'rev-b', 'CrocoV2.pretty')

KNOWN_SOURCES = {'GND', 'VCC', 'VBUS', '+3V3'}      # supplied by Croco / the cartridge slot / USB
DRIVER_TYPES = {'output', 'power_out'}
STRAPS = {'IO2': '1', 'IO8': '1', 'IO9': '1'}       # ESP32-C3 SPI boot (Table 4-3), GPIO8=1 for download
ESP_SYMBOL = 'radio:ESP32-C3-MINI-1U'

# ------------------------------------------------------------------ s-expressions / KiCad libs
_TOK = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')


def sexp(text):
    stack, cur, pos = [], [], 0
    while True:
        m = _TOK.match(text, pos)
        if not m:
            break
        pos = m.end()
        op, cl, st, at = m.groups()
        if op:
            stack.append(cur)
            cur = []
        elif cl:
            done, cur = cur, stack.pop()
            cur.append(done)
        elif st is not None:
            cur.append(st[1:-1])
        else:
            cur.append(at)
    return cur[0]


def kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


_libs = {}


def kicad_symbol_pins(lib_id):
    """{pin_number: (name, electrical_type)} for a KiCad library symbol (follows 'extends')."""
    lib, name = lib_id.split(':', 1)
    if lib not in _libs:
        path = os.path.join(SYMDIR, lib + '.kicad_sym')
        if not os.path.exists(path):
            raise KeyError(f'library {lib} not found ({path})')
        _libs[lib] = {s[1]: s for s in kids(sexp(open(path).read()), 'symbol')}
    syms = _libs[lib]
    if name not in syms:
        raise KeyError(f'symbol {lib_id} not in library')
    sym = syms[name]
    ext = kids(sym, 'extends')
    if ext:
        return kicad_symbol_pins(f'{lib}:{ext[0][1]}')
    pins = {}
    for sub in kids(sym, 'symbol'):
        for p in kids(sub, 'pin'):
            num = kids(p, 'number')[0][1]
            nm = kids(p, 'name')[0][1]
            pins[str(num)] = (nm, p[1])
    return pins


def symbol_pins(lib_id, radio_syms):
    if lib_id.startswith('radio:'):
        name = lib_id.split(':', 1)[1]
        if name not in radio_syms:
            raise KeyError(f'{lib_id} not defined in radio_symbols.json')
        return {k: (v['name'], v['type']) for k, v in radio_syms[name]['pins'].items()}
    return kicad_symbol_pins(lib_id)


# ------------------------------------------------------------------ Croco netlist
def croco_netlist(xml_path=None):
    if xml_path is None:
        xml_path = os.path.join(tempfile.mkdtemp(), 'croco.xml')
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', xml_path, CROCO_SCH],
                       check=True, capture_output=True)
    t = ET.parse(xml_path)
    # once the generated Radio sheet is in the project, its parts are ours, not Croco's: leave them out
    radio = {c.get('ref') for c in t.iter('comp') if (c.find('sheetpath') is not None
                                                      and c.find('sheetpath').get('names', '').startswith('/Radio/'))}
    refs = {c.get('ref') for c in t.iter('comp')} - radio
    nets = {}
    for n in t.iter('net'):
        nodes = [(x.get('ref'), x.get('pin'), x.get('pintype') or '') for x in n.iter('node') if x.get('ref') not in radio]
        if nodes:
            nets[n.get('name')] = nodes
    return refs, nets


# ------------------------------------------------------------------ checks
def check(data, radio_syms, croco_refs, croco_nets):
    errs = []

    def E(cat, msg):
        errs.append((cat, msg))

    parts = data['parts']
    glob = set(data.get('global_nets', []))
    iface = set(data.get('interface_nets', {}))
    ext_src = set(data.get('external_sources', {}))

    # Croco netlist with the requested edits applied (pin moved from one net to another)
    cnets = {k: list(v) for k, v in croco_nets.items()}
    for ed in data.get('croco_edits', []):
        src = cnets.get(ed['from'], [])
        hit = [x for x in src if x[0] == ed['ref'] and x[1] == str(ed['pin'])]
        if not hit and any(x[0] == ed['ref'] and x[1] == str(ed['pin']) for x in cnets.get(ed['to'], [])):
            continue    # already applied to the schematic by hw/rev-b/tools/apply_croco_edits.py
        if not hit:
            E('CROCO_EDIT', f"{ed['ref']}.{ed['pin']} is not on Croco net {ed['from']}")
            continue
        src.remove(hit[0])
        cnets.setdefault(ed['to'], []).append(hit[0])

    # references
    seen = set()
    for p in parts:
        r = p.get('ref', '')
        if r in seen:
            E('REF_DUP', f'{r} used twice')
        seen.add(r)
        if r in croco_refs:
            E('REF_DUP', f'{r} clashes with a Croco reference')

    # pins, BOM fields, net map
    netmap = {}          # net -> [(ref, pin, type, name)]
    for p in parts:
        r = p['ref']
        if not re.fullmatch(r'C\d+', str(p.get('lcsc', ''))):
            E('BOM', f'{r}: missing/invalid LCSC number {p.get("lcsc")!r}')
        fp = p.get('footprint', '')
        if not re.fullmatch(r'[^:\s]+:[^:\s]+', fp or ''):
            E('BOM', f'{r}: missing/invalid footprint {fp!r}')
        else:
            lib, name = fp.split(':', 1)
            if lib == 'radio':
                pass        # project footprint to be created; listed in radio_symbols.json / DESIGN_CALCS
            elif lib == 'CrocoV2':
                if not os.path.exists(os.path.join(CROCO_FP_LIB, name + '.kicad_mod')):
                    E('BOM', f'{r}: footprint {fp} not found')
            elif not os.path.exists(os.path.join(FPDIR, lib + '.pretty', name + '.kicad_mod')):
                E('BOM', f'{r}: footprint {fp} not found in KiCad libraries')
        try:
            spins = symbol_pins(p['symbol'], radio_syms)
        except KeyError as e:
            E('SYMBOL', f'{r}: {e}')
            continue
        jp = {str(k): v for k, v in p.get('pins', {}).items()}
        for num in sorted(set(spins) - set(jp), key=lambda x: (len(x), x)):
            E('PIN_UNASSIGNED', f'{r} ({p["symbol"]}) pin {num} "{spins[num][0]}" has no net (use "NC")')
        for num in sorted(set(jp) - set(spins), key=lambda x: (len(x), x)):
            E('PIN_UNKNOWN', f'{r} ({p["symbol"]}) has no pin {num}')
        for num, net in jp.items():
            if num not in spins:
                continue
            name, typ = spins[num]
            if net in (None, '', 'NC'):
                if typ == 'power_in':
                    E('POWER_NC', f'{r} pin {num} "{name}" is power_in but NC')
                continue
            netmap.setdefault(net, []).append((r, num, typ, name))

    # nets
    for net, conns in sorted(netmap.items()):
        n_local = len(conns)
        n_croco = len(cnets.get(net, [])) if net in glob else 0
        if net in glob and net not in cnets and net not in iface:
            E('GLOBAL_UNKNOWN', f'global net {net} does not exist in Croco and is not an interface net')
        if n_local + n_croco < 2:
            E('SINGLE_NET', f'net {net} has a single connection {conns[0][0]}.{conns[0][1]}')
        drivers = {c[0] for c in conns if c[2] in DRIVER_TYPES}
        if net in glob:
            drivers |= {'croco:' + x[0] for x in cnets.get(net, []) if x[2] in DRIVER_TYPES}
        if len(drivers) > 1:
            E('MULTI_DRIVER', f'net {net} driven by {sorted(drivers)}')
        if any(c[2] == 'power_in' for c in conns):
            src = net in KNOWN_SOURCES or net in ext_src or any(c[2] == 'power_out' for c in conns)
            if net in glob:
                src = src or any(x[2] == 'power_out' for x in cnets.get(net, []))
            if not src:
                who = [f'{c[0]}.{c[1]}' for c in conns if c[2] == 'power_in']
                E('NO_SOURCE', f'net {net} feeds power_in pins {who} but has no power source')
    for net in glob:
        if net not in netmap:
            E('SINGLE_NET', f'global net {net} declared but not used by any part')

    # ESP32-C3 strapping pins
    rails = {'+3V3_RF': '1', 'GND': '0'}
    for p in parts:
        if p['symbol'] != ESP_SYMBOL:
            continue
        spins = symbol_pins(ESP_SYMBOL, radio_syms)
        rail_of_esp = p['pins'].get(next(k for k, v in spins.items() if v[0] == '3V3'))
        rails = {rail_of_esp: '1', 'GND': '0'}
        for num, (name, _t) in spins.items():
            if name not in STRAPS:
                continue
            net = p['pins'].get(num)
            levels = set()
            if net in rails:
                levels.add(rails[net])
            for q in parts:
                if q['symbol'] != 'Device:R':
                    continue
                a, b = q['pins'].get('1'), q['pins'].get('2')
                if a == net and b in rails:
                    levels.add(rails[b])
                if b == net and a in rails:
                    levels.add(rails[a])
            others = [c for c in netmap.get(net, []) if c[0] != p['ref'] and c[2] in DRIVER_TYPES]
            if net in (None, '', 'NC') or not levels:
                E('STRAP', f'{p["ref"]} {name} (pin {num}) has no pull resistor/tie to {rail_of_esp} or GND')
            elif len(levels) > 1:
                E('STRAP', f'{p["ref"]} {name} pulled both up and down')
            elif levels != {STRAPS[name]}:
                E('STRAP', f'{p["ref"]} {name} boot level {levels.pop()} but SPI boot needs {STRAPS[name]}')
            if others:
                E('STRAP', f'{p["ref"]} {name} net {net} also driven by {others} during reset')
    return errs, netmap, cnets


# ------------------------------------------------------------------ self test
def selftest(data, radio_syms, crefs, cnets):
    base, _, _ = check(data, radio_syms, crefs, cnets)
    if base:
        print('selftest needs a clean netlist first:')
        for c, m in base:
            print(f'  {c}: {m}')
        return False

    def part(d, ref):
        return next(p for p in d['parts'] if p['ref'] == ref)

    def m_drop_pin(d):
        del part(d, 'U104')['pins']['5']

    def m_unknown_pin(d):
        part(d, 'R101')['pins']['3'] = 'GND'

    def m_single_net(d):
        part(d, 'R104')['pins']['1'] = 'SELFTEST_ORPHAN'

    def m_no_source(d):
        part(d, 'U106')['pins']['8'] = 'SELFTEST_UNPOWERED'
        part(d, 'C117')['pins']['1'] = 'SELFTEST_UNPOWERED'

    def m_two_drivers(d):
        part(d, 'U108')['pins']['1'] = 'VMUX'

    def m_dup_ref(d):
        part(d, 'R102')['ref'] = 'R101'

    def m_croco_ref(d):
        part(d, 'C101')['ref'] = 'C1'

    def m_no_lcsc(d):
        part(d, 'U103')['lcsc'] = ''

    def m_bad_fp(d):
        part(d, 'R103')['footprint'] = 'Resistor_SMD:R_0402_DoesNotExist'

    def m_strap_low(d):
        part(d, 'R114')['pins']['1'] = 'GND'

    def m_strap_missing(d):
        d['parts'].remove(part(d, 'R112'))
        part(d, 'U104')['pins']['5'] = 'ESP_IO2_MISO'

    def m_power_nc(d):
        part(d, 'U107')['pins']['15'] = 'NC'

    def m_bad_symbol(d):
        part(d, 'U101')['symbol'] = 'radio:DoesNotExist'

    def m_bad_edit(d):
        d['croco_edits'][0]['ref'] = 'R99'

    cases = [('drop ESP pin 5', m_drop_pin, 'PIN_UNASSIGNED'), ('R101 pin 3', m_unknown_pin, 'PIN_UNKNOWN'),
             ('single-pin net', m_single_net, 'SINGLE_NET'), ('unpowered rail', m_no_source, 'NO_SOURCE'),
             ('comparator output onto VMUX', m_two_drivers, 'MULTI_DRIVER'), ('duplicate ref', m_dup_ref, 'REF_DUP'),
             ('Croco ref clash', m_croco_ref, 'REF_DUP'), ('missing LCSC', m_no_lcsc, 'BOM'),
             ('missing footprint', m_bad_fp, 'BOM'), ('GPIO9 strapped low', m_strap_low, 'STRAP'),
             ('GPIO2 pull-up removed', m_strap_missing, 'STRAP'), ('hub V5 NC', m_power_nc, 'POWER_NC'),
             ('unknown symbol', m_bad_symbol, 'SYMBOL'), ('bad Croco edit', m_bad_edit, 'CROCO_EDIT')]
    ok = True
    for label, fn, want in cases:
        d = copy.deepcopy(data)
        fn(d)
        errs, _, _ = check(d, radio_syms, crefs, cnets)
        cats = {c for c, _ in errs}
        hit = want in cats
        ok &= hit
        print(f'  [{"PASS" if hit else "FAIL"}] mutation "{label}" -> expected {want}, got {sorted(cats) or "no error"}')
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=os.path.join(ROOT, 'docs', 'design', 'netlist_radio.json'))
    ap.add_argument('--symbols', default=os.path.join(ROOT, 'docs', 'design', 'radio_symbols.json'))
    ap.add_argument('--croco-xml')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    data = json.load(open(a.json))
    radio_syms = json.load(open(a.symbols))
    crefs, cnets = croco_netlist(a.croco_xml)
    if a.selftest:
        print('netlist_check self test (each mutation must be detected):')
        ok = selftest(data, radio_syms, crefs, cnets)
        print('SELFTEST', 'PASS' if ok else 'FAIL')
        sys.exit(0 if ok else 1)
    errs, netmap, cn = check(data, radio_syms, crefs, cnets)
    npins = sum(len(p['pins']) for p in data['parts'])
    print(f'{len(data["parts"])} parts, {npins} pins, {len(netmap)} nets '
          f'({len([n for n in netmap if n in data.get("global_nets", [])])} shared with Croco/interface)')
    for net in sorted(n for n in netmap if n in data.get('global_nets', [])):
        print(f'  {net:14s} local {len(netmap[net]):2d}  croco {len(cn.get(net, [])):2d}')
    for c, m in errs:
        print(f'  {c}: {m}')
    print('RESULT:', 'FAIL' if errs else 'PASS', f'({len(errs)} errors)')
    sys.exit(1 if errs else 0)


if __name__ == '__main__':
    main()
