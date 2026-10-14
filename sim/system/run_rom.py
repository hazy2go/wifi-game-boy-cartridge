#!/usr/bin/env python3
"""Run any Game Boy ROM on the simulated WiFi cartridge and check what it shows.

  python3 run_rom.py ROM.gb [--console fresh|tired|weak|weak_plus | --ohm R] [--seconds S]
                     [--input "A F30 D A"] [--expect expect.txt] [--fixtures urls.json | --offline]
                     [--font auto|ascii|gbdk] [--out DIR]

What runs: the ROM in an emulated DMG (Peanut-GB), the cartridge firmware model (mailbox + SPI link, byte for byte),
the shipped ESP32-C3 C components (link slave, power manager, screen text) and the power path. See README.md.

- --console picks the console's source resistance: fresh 0.7, tired 1.2, weak 2.0, weak_plus 2.5 ohm. A weak
  console makes WiFi slower (the radio waits for energy); design your ROM's UI for that.
- --input presses buttons: U D L R A B S(tart); Fn waits n frames; Wn waits until WRAM $C000 == n; P screenshots.
- --expect: one line per expected screen row, top to bottom, blank rows skipped. A line /regex/ is a regular
  expression (full match). Rows the file does not mention are not checked.
- GET: http(s)://example.com/... answers "Hello from WiFi!" for /hello, else "ok". Other URLs come from --fixtures
  (JSON {"url": "body"} or {"url": {"status": 404}}), are fetched live, or fail with ERR NO CONNECTION (--offline).
- Exit status 1 if an expectation fails or the power path breaks a rule (console < 4.5 V, radio brown-out, TX
  that skipped the power manager). Outputs in --out (default out/rom/): screen.png, screen.txt, log.txt, result.json.
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONSOLES = {'fresh': 0.7, 'tired': 1.2, 'weak': 2.0, 'weak_plus': 2.5}


def build_sim():
    comp = os.path.join(ROOT, 'firmware', 'esp32c3', 'components')
    srcs = [os.path.join(HERE, 'sysim.c')] + [os.path.join(comp, c, c + '.c') for c in ('power_mgr', 'esp_link', 'gbwifi')]
    exe = os.path.join(HERE, 'sysim')
    if os.path.exists(exe) and all(os.path.getmtime(exe) >= os.path.getmtime(s) for s in srcs):
        return exe
    inc = ' '.join(f'-I{os.path.join(comp, c)}' for c in ('power_mgr', 'esp_link', 'gbwifi'))
    r = subprocess.run(f'cc -O2 -std=c11 -Wall -Wextra -Werror -Wno-unused-parameter {inc} -o {exe} {" ".join(srcs)} -lm',
                       shell=True, capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr)
    return exe


def decode(tiles, font):
    rows = []
    for k in range(18):
        t = tiles[k * 20:k * 20 + 20]
        if font == 'gbdk':      # GBDK's console font: tile 0 = ' '
            s = ''.join(chr(c + 32) if c < 95 else ' ' for c in t)
        else:                   # tile n = ASCII n (the RGBDS examples and test ROMs)
            s = ''.join(chr(c) if 32 <= c < 127 else ' ' for c in t)
        rows.append(s.rstrip())
    return rows


def score(rows):
    return sum(ch.isalnum() for r in rows for ch in r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rom')
    ap.add_argument('--console', choices=CONSOLES, default='fresh')
    ap.add_argument('--ohm', type=float)
    ap.add_argument('--seconds', type=float, default=15)
    ap.add_argument('--input', default='')
    ap.add_argument('--expect')
    ap.add_argument('--fixtures')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--font', choices=('auto', 'ascii', 'gbdk'), default='auto')
    ap.add_argument('--out', default=os.path.join(HERE, 'out', 'rom'))
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ)
    if a.fixtures:
        env['GBW_FIXTURES'] = os.path.abspath(a.fixtures)
    if a.offline:
        env['GBW_OFFLINE'] = '1'
    ohm = a.ohm if a.ohm is not None else CONSOLES[a.console]
    cmd = [build_sim(), os.path.abspath(a.rom), 'rom', str(ohm), str(a.seconds), os.path.abspath(a.out)]
    if a.input:
        cmd.append(a.input)
    r = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'sysim failed: {r.stderr.strip()}')

    res = json.load(open(os.path.join(a.out, 'rom_result.json')))
    tiles = open(os.path.join(a.out, 'rom_tiles.bin'), 'rb').read()
    font = a.font
    if font == 'auto':
        font = 'gbdk' if score(decode(tiles, 'gbdk')) > score(decode(tiles, 'ascii')) else 'ascii'
    rows = decode(tiles, font)
    open(os.path.join(a.out, 'screen.txt'), 'w').write('\n'.join(rows) + '\n')
    try:
        from PIL import Image
        Image.open(os.path.join(a.out, 'rom_screen.pgm')).resize((320, 288), Image.NEAREST).save(os.path.join(a.out, 'screen.png'))
    except ImportError:
        pass
    os.replace(os.path.join(a.out, 'rom_log.txt'), os.path.join(a.out, 'log.txt'))

    print(f'ROM {os.path.basename(a.rom)} on a {ohm} ohm console, {a.seconds:g} s, font {font}:')
    print('+' + '-' * 20 + '+')
    for row in rows:
        print('|' + row.ljust(20) + '|')
    print('+' + '-' * 20 + '+')
    fails = []
    if not res['wifi_rom']:
        print('note: no mailbox for this ROM (needs WIFICART in bank 0, MBC5, 128 KB RAM)')
    if res['console_min_v'] < 4.5 or res['time_below_4v5_ms'] > 0:
        fails.append(f"console rail {res['console_min_v']:.3f} V (< 4.5 V)")
    if res['radio_resets']:
        fails.append(f"{res['radio_resets']} radio brown-outs (console too weak for this load, or boot energy)")
    if res['frames_admitted'] != res['tx_frames']:
        fails.append('TX outside the power manager')
    if a.expect:
        shown = [x for x in rows if x.strip()]
        for i, line in enumerate(l.rstrip('\n') for l in open(a.expect)):
            if not line.strip():
                continue
            want = line.strip()
            got = shown.pop(0) if shown else None
            ok = got is not None and (re.fullmatch(want[1:-1], got.strip()) if len(want) > 1 and want[0] == want[-1] == '/'
                                      else got.strip() == want)
            if not ok:
                fails.append(f'expect line {i + 1}: wanted {want!r}, got {got!r}')
                break
    print(f"WiFi: {res['tx_frames']} frames, {res['tx_drops']} dropped, {res['pm_parks']} parks; "
          f"console min {res['console_min_v']:.3f} V; SPI {res['spi_xfers']} transactions")
    print(f"outputs: {a.out}/screen.png screen.txt log.txt rom_result.json")
    for f in fails:
        print('FAIL', f)
    print('PASS' if not fails else 'FAIL')
    json.dump({'pass': not fails, 'fails': fails, 'screen': rows, 'font': font, **res},
              open(os.path.join(a.out, 'verdict.json'), 'w'), indent=1)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
