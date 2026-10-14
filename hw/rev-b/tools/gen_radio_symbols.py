#!/usr/bin/env python3
"""Generate the project symbol library radio.kicad_sym from docs/design/radio_symbols.json.

Each symbol is a plain box: pins listed in pin-number order, first half on the left, second half on the
right, 2.54 mm pitch. Pin names, numbers and electrical types come only from the JSON (which cites the
datasheet page each pinout was taken from), so the library cannot disagree with the netlist checker.

Usage: gen_radio_symbols.py SYMBOLS.json OUT.kicad_sym
"""
import json, sys
from sexp import dump, Str

PITCH, PIN_LEN, FONT = 2.54, 2.54, ['font', ['size', 1.27, 1.27]]


def key(n):
    return (0, int(n)) if n.isdigit() else (1, n)


def symbol(name, d):
    nums = sorted(d['pins'], key=key)
    left, right = nums[:(len(nums) + 1) // 2], nums[(len(nums) + 1) // 2:]
    rows = max(len(left), len(right))
    longest = max(len(p['name']) for p in d['pins'].values())
    half_w = max(5.08, round((longest * 1.27 + 2.54) / 2.54) * 2.54)
    top = round((rows - 1) * PITCH / 2 / 2.54) * 2.54
    box_top, box_bot = top + PITCH, top - rows * PITCH
    pins = []
    for side, lst in ((-1, left), (1, right)):
        for i, n in enumerate(lst):
            p = d['pins'][n]
            x = side * (half_w + PIN_LEN)
            y = round(top - i * PITCH, 2)
            pins.append(['pin', p['type'], 'line', ['at', x, y, 0 if side < 0 else 180], ['length', PIN_LEN],
                         ['name', Str(p['name']), ['effects', FONT]], ['number', Str(n), ['effects', FONT]]])
    hide = [['hide', 'yes']]
    return ['symbol', Str(name), ['pin_names', ['offset', 1.016]], ['exclude_from_sim', 'no'], ['in_bom', 'yes'],
            ['on_board', 'yes'],
            ['property', Str('Reference'), Str('U'), ['at', 0, box_top + 2.54, 0], ['effects', FONT]],
            ['property', Str('Value'), Str(name), ['at', 0, box_bot - 2.54, 0], ['effects', FONT]],
            ['property', Str('Footprint'), Str(d.get('footprint', '')), ['at', 0, 0, 0], ['effects', FONT] + hide],
            ['property', Str('Datasheet'), Str(d.get('datasheet', '')), ['at', 0, 0, 0], ['effects', FONT] + hide],
            ['property', Str('Description'), Str(d.get('description', '')), ['at', 0, 0, 0], ['effects', FONT] + hide],
            ['symbol', Str(f'{name}_0_1'), ['rectangle', ['start', -half_w, box_top], ['end', half_w, box_bot],
                                            ['stroke', ['width', 0.254], ['type', 'default']], ['fill', ['type', 'background']]]],
            ['symbol', Str(f'{name}_1_1')] + pins]


def main(src, out):
    syms = json.load(open(src))
    lib = ['kicad_symbol_lib', ['version', 20241209], ['generator', Str('gen_radio_symbols')],
           ['generator_version', Str('9.0')]] + [symbol(n, d) for n, d in sorted(syms.items())]
    open(out, 'w').write(dump(lib) + '\n')
    print(f'wrote {out}: {len(syms)} symbols')


if __name__ == '__main__':
    main(*sys.argv[1:3])
