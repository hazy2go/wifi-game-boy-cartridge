#!/usr/bin/env python3
"""Bring the PCB in line with the schematic (KiCad's "Update PCB from Schematic", scripted).

Run with KiCad's bundled Python (it provides pcbnew):
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \
      hw/rev-b/tools/update_pcb.py hw/rev-b

What it does, from the schematic netlist exported by kicad-cli (the only source of truth):
  - every schematic component missing on the board is added with its footprint, value, LCSC field and the
    symbol link (path) that KiCad's parity check uses; new parts land in a staging grid to the right of the
    board, outside the outline, for placement;
  - every pad whose net differs from the schematic gets the schematic's net (this is how the Croco R9/R10
    re-netting reaches the copper); the tool prints each change. A pad whose old net has copper on it is only
    re-netted if that old net's copper (tracks, vias, zones) is ripped up explicitly with --rip NET (repeatable), so existing routing
    never silently changes meaning or shorts two nets.
Existing footprint positions, tracks and zones are never moved. Verify with verify/kicad_check.py (parity).
"""
import os, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
import pcbnew

KICAD_FP = '/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints'
STAGE_X, STAGE_Y, STAGE_W, STAGE_PITCH = 175.0, 60.0, 6, 8.0


def mm(v):
    return pcbnew.FromMM(v)


def main(pdir, rip=()):
    rip = set(rip)
    sch = os.path.join(pdir, 'GameboyCartridgeV2.1.kicad_sch')
    pcb_path = os.path.join(pdir, 'GameboyCartridgeV2.1.kicad_pcb')
    xml = os.path.join(tempfile.mkdtemp(), 'net.xml')
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', xml, sch], check=True,
                   capture_output=True)
    root = ET.parse(xml).getroot()
    comps = {}
    for c in root.iter('comp'):
        sp = c.find('sheetpath')
        fields = {f.get('name'): (f.text or '') for f in c.iter('field')}
        tstamps = [t.text for t in c.iter('tstamps')] or [c.find('tstamp').text if c.find('tstamp') is not None else '']
        props = {pp.get('name') for pp in c.iter('property')}
        comps[c.get('ref')] = dict(off_board='exclude_from_board' in props,
                                   value=c.findtext('value', ''), footprint=c.findtext('footprint', ''),
                                   sheet_tstamps=sp.get('tstamps'), sym=tstamps[0], fields=fields)
    pin_net = {}
    for n in root.iter('net'):
        for nd in n.iter('node'):
            name = n.get('name')
            if name.startswith('unconnected-('):      # KiCad escapes '/' inside pin names as {slash} on the board
                name = 'unconnected-(' + name[len('unconnected-('):].replace('/', '{slash}')
            pin_net[(nd.get('ref'), nd.get('pin'))] = name

    board = pcbnew.LoadBoard(pcb_path)
    nets = {}

    def net(name):
        if name not in nets:
            ni = board.FindNet(name)
            if ni is None:
                ni = pcbnew.NETINFO_ITEM(board, name)
                board.Add(ni)
            nets[name] = ni
        return nets[name]

    on_board = {fp.GetReference(): fp for fp in board.GetFootprints()}

    added = 0
    for ref in sorted(comps, key=lambda r: (r.rstrip('0123456789'), int(''.join(ch for ch in r if ch.isdigit()) or 0))):
        c = comps[ref]
        if ref.startswith('#') or not c['footprint']:
            continue
        if c['off_board']:
            continue
        if ref in on_board:
            old = on_board[ref]
            if old.GetFPIDAsString() == c['footprint']:
                continue
            # footprint changed in the schematic: swap it in place (same position/rotation), nets re-applied below
            pos, rot = old.GetPosition(), old.GetOrientationDegrees()
            print(f"{ref}: footprint {old.GetFPIDAsString()} -> {c['footprint']}")
            board.Delete(old)
            del on_board[ref]
        else:
            pos, rot = None, 0
        lib, name = c['footprint'].split(':', 1)
        libpath = os.path.join(pdir, lib + '.pretty') if os.path.isdir(os.path.join(pdir, lib + '.pretty')) \
            else os.path.join(KICAD_FP, lib + '.pretty')
        fp = pcbnew.FootprintLoad(libpath, name)
        if fp is None:
            sys.exit(f'{ref}: footprint {c["footprint"]} not found')
        fp.SetReference(ref)
        fp.SetValue(c['value'])
        fp.SetFPIDAsString(c['footprint'])
        path = pcbnew.KIID_PATH(c['sheet_tstamps'].rstrip('/') + '/' + c['sym'])
        fp.SetPath(path)
        for fname in ('LCSC', 'Datasheet', 'Description'):     # parity compares these with the symbol
            if c['fields'].get(fname):
                fp.SetField(fname, c['fields'][fname])
                fld = fp.GetField(fname)    # a new field lands visible on F.SilkS: hide it like Croco's
                fld.SetVisible(False)
                fld.SetLayer(pcbnew.F_Fab)
        k = added
        if pos is None:
            pos = pcbnew.VECTOR2I(mm(STAGE_X + (k % STAGE_W) * STAGE_PITCH), mm(STAGE_Y + (k // STAGE_W) * STAGE_PITCH))
        fp.SetPosition(pos)
        fp.SetOrientationDegrees(rot)
        board.Add(fp)
        on_board[ref] = fp
        added += 1

    all_tracks = [(t, t.GetNetname()) for t in board.GetTracks()]
    tracks_on = {n for _, n in all_tracks} - rip   # names first: deleted SWIG objects cannot be queried
    for t, n in all_tracks:
        if n in rip:
            board.Delete(t)
    zones = [(z, z.GetNetname()) for z in board.Zones()]
    for z, n in zones:
        if n in rip:
            board.Delete(z)
    if rip:
        print(f'ripped up tracks/vias/zones of {sorted(rip)}')
    changed, blocked = 0, []
    for ref, fp in on_board.items():
        if comps.get(ref, {}).get('off_board'):
            continue
        for pad in fp.Pads():
            want = pin_net.get((ref, pad.GetNumber()))
            have = pad.GetNetname()
            if want is None or want == have:
                continue
            if have in tracks_on:
                blocked.append(f'{ref}.{pad.GetNumber()} ({have} -> {want})')
                continue
            print(f'{ref}.{pad.GetNumber()}: {have or "(none)"} -> {want}')
            pad.SetNet(net(want))
            changed += 1
    if blocked:
        sys.exit('routed pads need --rip of their old net first: ' + ', '.join(blocked))
    # parts the schematic marks "not on board" (e.g. the removed coin-cell holder BT1) come off the board last
    # (removing a footprint before iterating tracks upsets the SWIG wrappers); their nets' copper is ripped
    # explicitly like any other net
    for ref, c in comps.items():
        if c['off_board'] and ref in on_board:
            board.Delete(on_board.pop(ref))
            print(f'{ref}: removed from the board (schematic: exclude_from_board)')
    board.Save(pcb_path)
    print(f'added {added} footprints (staged at x>{STAGE_X} mm), re-netted {changed} pads')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], [a[i + 1] for i, x in enumerate(a) if x == '--rip'])
