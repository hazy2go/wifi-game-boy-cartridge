#!/usr/bin/env python3
"""BTC demo on the simulated cartridge: rom/btcdemo.asm in the emulated Game Boy, buttons pressed by a script
like a player would: wait for the network list, pick the strongest network, type the password on the on-screen
keyboard, START, wait for the chart, press A once to refresh.
The ESP32-C3 model answers SCAN with the networks this Mac's radio really sees, CONNECT with this Mac's
address, BTC with live Binance data (host_net.py); the power path and the shipped power manager run as in S1.
Checks: chart screen reached, its text lines, the refresh, and the S1 power limits. Exit 1 on any FAIL.
Usage: python3 run_demo.py [password]   (env SIM_LAYOUT as for run_system.py)"""
import json, os, re, subprocess, sys
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); OUT = os.path.join(HERE, 'out')
KEYS = ['abcdefghijklmnop', 'qrstuvwxyz012345', '6789ABCDEFGHIJKL', 'MNOPQRSTUVWXYZ-_']
PASSWORD = sys.argv[1] if len(sys.argv) > 1 else 'gameboy123'
fails = []


def sh(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'{cmd}\n{r.stdout}\n{r.stderr}')


def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        fails.append(name)


def keyboard_moves(text):
    out, x, y = [], 0, 0
    for ch in text:
        ty = next(i for i, r in enumerate(KEYS) if ch in r); tx = KEYS[ty].index(ch)
        dx = (tx - x) % 16; out += ['R'] * dx if dx <= 8 else ['L'] * (16 - dx)
        dy = (ty - y) % 4; out += ['D'] * dy if dy <= 2 else ['U'] * (4 - dy)
        out.append('A'); x, y = tx, ty
    return out


os.makedirs(OUT, exist_ok=True)
sh('rgbasm -Werror -o btcdemo.o btcdemo.asm && rgblink -o btcdemo.gb -n btcdemo.sym btcdemo.o '
   '&& rgbfix -v -p 0xFF -m MBC5+RAM+BATTERY -r 0x04 -t BTCDEMO btcdemo.gb', os.path.join(HERE, 'rom'))
pm = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'power_mgr')
el = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'esp_link')
gw = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'gbwifi')
sh(f"cc -O2 -std=c11 {os.environ.get('SIM_LAYOUT', '')} -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} -o sysim sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm", HERE)
script = ['W2', 'F60', 'A', 'W3', 'F20'] + keyboard_moves(PASSWORD) + ['F20', 'P', 'F5', 'S', 'W6', 'F120', 'A', 'W5', 'W6', 'F60']
for f in os.listdir(OUT):
    if f.startswith('demo_') and f.endswith(('.pgm', '.png')):
        os.remove(os.path.join(OUT, f))
sh(f"./sysim rom/btcdemo.gb demo 0.7 60 out '{' '.join(script)}'", HERE)
shots = sorted(f for f in os.listdir(OUT) if f.startswith('demo_') and f.endswith('.pgm'))
for f in shots:
    Image.open(os.path.join(OUT, f)).resize((480, 432), Image.NEAREST).save(os.path.join(OUT, f[:-4] + '.png'))
tiles = [l.rstrip('\n') for l in open(os.path.join(OUT, 'demo_tilemap.txt'))]
res = json.load(open(os.path.join(OUT, 'demo_result.json')))
log = open(os.path.join(OUT, 'demo_log.txt')).read()
print('screens:', ' '.join(s[:-4] for s in shots))
print('final screen (text layer):'); [print('   |', l) for l in tiles]
states = [int(s) for s in re.findall(r'ROM: state (\d+)', log)]
states = [s for s in states if s]
check('D1 flow', states[:6] == [1, 2, 3, 4, 5, 6] and states.count(6) >= 2, f'ROM states {states}')
check('D2 chart text', tiles[0].startswith('BTC/USD') and re.match(r'\$[\d,]+  [+-]\d+\.\d\d%', tiles[1].strip()) is not None
      and re.match(r'H [\d,]+  L [\d,]+', tiles[11].strip()) is not None and tiles[14].startswith('OK '), f'rows 0/1/11/14 {tiles[0]!r} {tiles[1]!r} {tiles[11]!r} {tiles[14]!r}')
check('D3 console rail', res['console_min_v'] >= 4.5 and res['time_below_4v5_ms'] == 0, f"min {res['console_min_v']:.3f} V")
check('D4 radio resets', res['radio_resets'] == 0, f"{res['radio_resets']}, reservoir min {res['reservoir_min_v_while_on']:.2f} V")
check('D5 TX via power manager', res['frames_admitted'] == res['tx_frames'], f"{res['tx_frames']} frames, {res['frames_admitted']} admitted")
print('RESULT:', 'ALL PASS' if not fails else 'FAIL ' + ', '.join(fails))
sys.exit(1 if fails else 0)
