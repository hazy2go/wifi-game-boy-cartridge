#!/usr/bin/env python3
"""Apply the 'croco_edits' of docs/design/netlist_radio.json to Croco's schematic sheets.

An edit moves one pin of a Croco part from net 'from' to net 'to'. The tool finds the pin's position,
follows the wire from it to the global label that names its net, checks that this label is 'from' and that
nothing else hangs on that wire, and renames the label to 'to'. Anything else (no wire, a junction, a label
with another name) is a hard error, so an edit can never silently land on the wrong wire. Idempotent: an
edit whose label already reads 'to' is reported and skipped.

Usage: apply_croco_edits.py PROJECT_DIR NETLIST.json
Verify afterwards with verify/netlist_check.py (exports the Croco netlist and checks every edit).
"""
import glob, json, math, os, re, sys

EPS = 0.01


def pin_offsets(lib_block):
    """{number: (x, y)} in symbol coordinates (y up) from a lib_symbols entry."""
    res = {}
    for m in re.finditer(r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+)(?: [-\d.]+)?\).*?\(number "([^"]+)"', lib_block, re.S):
        res[m.group(3)] = (float(m.group(1)), float(m.group(2)))
    return res


def blocks(text, head):
    """Yield (start, end, block) for every top-level-ish s-expression starting with '(head'."""
    for m in re.finditer(r'\(' + re.escape(head) + r'[\s"]', text):
        i, depth = m.start(), 0
        for j in range(i, len(text)):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    yield i, j + 1, text[i:j + 1]
                    break


def pin_position(text, ref, pin):
    lib_ids = {}
    for _, _, b in blocks(text, 'symbol'):
        mref = re.search(r'\(property "Reference" "' + re.escape(ref) + '"', b)
        mlib = re.match(r'\(symbol\s*\(lib_id "([^"]+)"\)', b)
        if mref and mlib:
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', b)
            mirror = re.search(r'\(mirror (\w)\)', b)
            lib_ids[ref] = (mlib.group(1), tuple(map(float, at.groups())), mirror.group(1) if mirror else None)
    if ref not in lib_ids:
        return None
    lib_id, (sx, sy, rot), mirror = lib_ids[ref]
    lib_block = next(b for _, _, b in blocks(text, 'symbol') if b.startswith(f'(symbol "{lib_id}"'))
    px, py = pin_offsets(lib_block)[pin]
    if mirror == 'x':
        py = -py
    elif mirror == 'y':
        px = -px
    a = math.radians(rot)
    rx, ry = px * math.cos(a) - py * math.sin(a), px * math.sin(a) + py * math.cos(a)
    return round(sx + rx, 2), round(sy - ry, 2)          # schematic y points down


def same(p, q):
    return abs(p[0] - q[0]) < EPS and abs(p[1] - q[1]) < EPS


def apply(pdir, edit):
    ref, pin, src, dst = edit['ref'], edit['pin'], edit['from'], edit['to']
    for path in glob.glob(os.path.join(pdir, '*.kicad_sch')):
        text = open(path).read()
        pos = pin_position(text, ref, pin)
        if pos is None:
            continue
        wires = []
        for s, e, b in blocks(text, 'wire'):
            pts = [tuple(map(float, m)) for m in re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', b)]
            wires.append(pts)
        # walk the wire chain from the pin until a global label is found
        frontier, seen, hits = [pos], set(), []
        while frontier:
            p = frontier.pop()
            key = (round(p[0], 2), round(p[1], 2))
            if key in seen:
                continue
            seen.add(key)
            for s, e, b in blocks(text, 'global_label'):
                at = re.search(r'\(at ([-\d.]+) ([-\d.]+)', b)
                if same(p, tuple(map(float, at.groups()))):
                    hits.append((s, e, b))
            for pts in wires:
                if same(pts[0], p):
                    frontier.append(pts[1])
                elif same(pts[1], p):
                    frontier.append(pts[0])
        if any(re.search(r'\(junction\s*\(at ' + f'{x:g} {y:g}', text) for x, y in seen):
            sys.exit(f'{ref}.{pin}: wire from the pin has a junction, refusing to rename a shared label')
        names = {re.match(r'\(global_label "([^"]+)"', b).group(1) for _, _, b in hits}
        if names == {dst}:
            print(f'{ref}.{pin}: already on {dst}, skipped')
            return
        if names != {src} or len(hits) != 1:
            sys.exit(f'{ref}.{pin} in {os.path.basename(path)}: expected exactly one label {src!r} on the pin wire, found {sorted(names)} x{len(hits)}')
        s, e, b = hits[0]
        text = text[:s] + b.replace(f'(global_label "{src}"', f'(global_label "{dst}"', 1) + text[e:]
        open(path, 'w').write(text)
        print(f'{ref}.{pin} in {os.path.basename(path)}: label {src} -> {dst} (wire from pin at {pos})')
        return
    sys.exit(f'{ref} not found in any sheet of {pdir}')


if __name__ == '__main__':
    pdir, netlist = sys.argv[1:3]
    for edit in json.load(open(netlist))['croco_edits']:
        apply(pdir, edit)
