"""Tiny S-expression reader/writer for KiCad files (enough for symbol libraries and schematics)."""
import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')


def parse(text):
    stack, cur = [], []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == '':
                break
            raise ValueError(f'bad token at {pos}: {text[pos:pos+40]!r}')
        pos = m.end()
        op, cl, st, at = m.groups()
        if op:
            stack.append(cur)
            cur = []
        elif cl:
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif st is not None:
            cur.append(Str(bytes(st[1:-1], 'utf-8').decode('unicode_escape') if '\\' in st else st[1:-1]))
        else:
            cur.append(at)
    return cur[0] if len(cur) == 1 else cur


class Str(str):
    """A quoted string (keeps quoting on output)."""


def dump(node, indent=0):
    if isinstance(node, list):
        if not node:
            return '()'
        simple = all(not isinstance(x, list) for x in node)
        if simple:
            return '(' + ' '.join(dump(x) for x in node) + ')'
        pad = '\t' * (indent + 1)
        parts = [dump(node[0])]
        out = '(' + parts[0]
        for x in node[1:]:
            if isinstance(x, list):
                out += '\n' + pad + dump(x, indent + 1)
            else:
                out += ' ' + dump(x)
        return out + '\n' + '\t' * indent + ')'
    if isinstance(node, Str):
        return '"' + node.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return str(node)


def find(node, key):
    """All direct children lists whose head is key."""
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def walk(node):
    if isinstance(node, list):
        yield node
        for c in node:
            yield from walk(c)
