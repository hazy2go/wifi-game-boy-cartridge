#!/usr/bin/env python3
"""Set property values of placed symbols in a .kicad_sch (text edit inside the one symbol block that carries
the given Reference; nothing else in the file changes). Fails if a reference or property is not found exactly once.
Usage: set_sch_props.py SCH REF PROP=VALUE [PROP=VALUE ...] [-- REF PROP=VALUE ...]"""
import re, sys


def symbol_block(s, ref):
    hits = [m.start() for m in re.finditer(r'\(property "Reference" "%s"\n' % re.escape(ref), s)]
    if len(hits) != 1:
        sys.exit(f'{ref}: found {len(hits)} times')
    i = hits[0]
    st = s.rfind('\t(symbol\n', 0, i)
    depth, k = 0, st + 1
    while True:                       # find the matching close paren of "(symbol"
        c = s[k]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return st + 1, k + 1
        elif c == '"':
            k = s.index('"', k + 1)
            while s[k - 1] == '\\':
                k = s.index('"', k + 1)
        k += 1


def main():
    path = sys.argv[1]
    s = open(path).read()
    groups, cur = [], []
    for a in sys.argv[2:]:
        if a == '--':
            groups.append(cur); cur = []
        else:
            cur.append(a)
    groups.append(cur)
    for g in groups:
        ref, props = g[0], g[1:]
        a, b = symbol_block(s, ref)
        blk = s[a:b]
        for pv in props:
            p, v = pv.split('=', 1)
            pat = r'\(property "%s" "((?:[^"\\]|\\.)*)"' % re.escape(p)
            m = list(re.finditer(pat, blk))
            if len(m) != 1:
                sys.exit(f'{ref}.{p}: found {len(m)} times')
            old = m[0].group(1)
            blk = blk[:m[0].start()] + f'(property "{p}" "{v}"' + blk[m[0].end():]
            print(f'{ref}: {p} "{old}" -> "{v}"')
        s = s[:a] + blk + s[b:]
    open(path, 'w').write(s)


if __name__ == '__main__':
    main()
