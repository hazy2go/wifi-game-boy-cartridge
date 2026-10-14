#!/usr/bin/env python3
"""Gate S1: full-system simulation with a real Game Boy ROM (LOGBOOK 14:57). Exit 1 on any FAIL.

Builds the test ROM (RGBDS) and the simulator (cc), runs every scenario, and checks:
  S1.1 the ROM's screen (tile map read from VRAM) shows the expected results, line by line
  S1.2 console rail never below 4.5 V after the 50 ms plug-in window, in every scenario
  S1.3 no uncontrolled radio reset (power-good dropping while the ESP is on)
  S1.4 every TX frame went through the shipped power manager (admitted == sent)
  S1.5 degraded consoles may be slow, but must never show a wrong answer: every printed result is either the
       correct one or an explicit ERR/TIMEOUT
Writes out/<scenario>_screen.png (2x) and out/report.txt.
"""
import json, os, re, subprocess, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, 'out')
SCEN = [('fresh', 0.7, 12), ('tired', 1.2, 12), ('weak', 2.0, 20), ('weak_plus', 2.5, 60)]
EXPECT = ['WIFI CART TEST', 'PING:', 'PONG', 'TIME (NTP):', re.compile(r'2026-10-14 15:\d\d:\d\d'), 'GET /hello:',
          'Hello from WiFi!', 'POWER:', re.compile(r'RAIL 4\.\d\dV RES \d\.\d\dV'), 'BURST 20/20 OK', 'POWER:',
          re.compile(r'RAIL 4\.\d\dV RES \d\.\d\dV'), 'DONE']
lines, fails = [], []


def log(s=''):
    lines.append(s); print(s, flush=True)


def check(name, ok, detail):
    log(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        fails.append(name)


def sh(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{cmd}\n{r.stdout}\n{r.stderr}')


os.makedirs(OUT, exist_ok=True)
sh(f'{sys.executable} make_font.py && rgbasm -Werror -o wifitest.o wifitest.asm && rgblink -o wifitest.gb -n wifitest.sym wifitest.o '
   '&& rgbfix -v -p 0xFF -m MBC5+RAM+BATTERY -r 0x04 -t WIFITEST wifitest.gb', os.path.join(HERE, 'rom'))
pm = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'power_mgr')
LAYOUT = os.environ.get('SIM_LAYOUT', '')   # e.g. '-DRPATH=0.0369 -DRESR=0.0156' (extracted copper)
if LAYOUT:
    log(f'layout parasitics: {LAYOUT}')
sh(f'cc -O2 -std=c11 {LAYOUT} -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -o sysim sysim.c {pm}/power_mgr.c -lm', HERE)
log('# S1 full-system simulation: Game Boy ROM -> cartridge mailbox -> RP2350 -> SPI -> ESP32-C3 firmware -> power path')
for name, r, secs in SCEN:
    sh(f'./sysim rom/wifitest.gb {name} {r} {secs} out', HERE)
    res = json.load(open(os.path.join(OUT, f'{name}_result.json')))
    screen = [l.rstrip() for l in open(os.path.join(OUT, f'{name}_tilemap.txt'))]
    Image.open(os.path.join(OUT, f'{name}_screen.pgm')).resize((320, 288), Image.NEAREST).save(os.path.join(OUT, f'{name}_screen.png'))
    log(f'\n## {name}: console source {r} ohm, {secs} s simulated')
    for l in screen:
        if l:
            log(f'   | {l}')
    shown = [l for l in screen if l]
    complete = len(shown) >= len(EXPECT) and all((e.fullmatch(s) if hasattr(e, 'fullmatch') else e == s) for e, s in zip(EXPECT, shown))
    wrong = [s for s in shown if s not in ('ERR', 'TIMEOUT') and not any((e.fullmatch(s) if hasattr(e, 'fullmatch') else e == s) for e in EXPECT)]
    if name in ('fresh', 'tired', 'weak'):
        check(f'S1.1 {name} screen', complete, 'all 13 lines as expected' if complete else f'got {shown}')
    else:
        log(f"INFO  S1.1 {name}: {'complete' if complete else 'not complete in %d s (degraded console)' % secs}")
    check(f'S1.5 {name} no wrong answer', not wrong, f'unexpected lines {wrong}' if wrong else 'every printed result correct or explicit ERR/TIMEOUT')
    check(f'S1.2 {name} console rail', res['console_min_v'] >= 4.5 and res['time_below_4v5_ms'] == 0,
          f"min {res['console_min_v']:.3f} V, {res['time_below_4v5_ms']:.1f} ms below 4.5 V")
    check(f'S1.3 {name} radio resets', res['radio_resets'] == 0, f"{res['radio_resets']}; reservoir min while on {res['reservoir_min_v_while_on']:.2f} V")
    check(f'S1.4 {name} TX through power manager', res['frames_admitted'] == res['tx_frames'],
          f"{res['tx_frames']} frames sent, {res['frames_admitted']} admitted, refused-ms {res['tx_refused_ms']}, parks {res['pm_parks']}")
log(f"\nRESULT: {'ALL PASS' if not fails else str(len(fails)) + ' FAIL: ' + ', '.join(fails)}")
open(os.path.join(OUT, 'report.txt'), 'w').write('\n'.join(lines) + '\n')
sys.exit(1 if fails else 0)
