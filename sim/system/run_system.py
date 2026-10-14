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
# weak_plus (2.5 ohm, beyond the design's weak console) at the default boot assumption (300 ms at 25 mA = 28 mJ, more than
# the ~6.75 mJ the reservoir holds above power-good) boot-loops: reported as INFO. weak_plus_fastboot is required.
STRESS = {'weak_plus'}
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
el = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'esp_link')
gw = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'gbwifi')
LAYOUT = os.environ.get('SIM_LAYOUT', '')   # e.g. '-DRPATH=0.0369 -DRESR=0.0156' (extracted copper)
if LAYOUT:
    log(f'layout parasitics: {LAYOUT}')
sh(f'cc -O2 -std=c11 {LAYOUT} -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} -o sysim sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
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
    wrong = [s for s in shown if s not in ('ERR', 'TIMEOUT', 'BURST FAIL') and not any((e.fullmatch(s) if hasattr(e, 'fullmatch') else e == s) for e in EXPECT)]
    if name in STRESS:
        log(f"INFO  S1.1 {name}: {'complete' if complete else 'not complete'}; radio resets {res['radio_resets']} "
            f"(boot energy at the default assumption, see weak_plus_fastboot)")
        check(f'S1.5 {name} no wrong answer', not wrong, f'unexpected lines {wrong}' if wrong else 'every printed result correct or explicit ERR/TIMEOUT')
        check(f'S1.2 {name} console rail', res['console_min_v'] >= 4.5 and res['time_below_4v5_ms'] == 0,
              f"min {res['console_min_v']:.3f} V, {res['time_below_4v5_ms']:.1f} ms below 4.5 V")
        continue
    check(f'S1.1 {name} screen', complete, 'all 13 lines as expected' if complete else f'got {shown}')
    check(f'S1.5 {name} no wrong answer', not wrong, f'unexpected lines {wrong}' if wrong else 'every printed result correct or explicit ERR/TIMEOUT')
    check(f'S1.2 {name} console rail', res['console_min_v'] >= 4.5 and res['time_below_4v5_ms'] == 0,
          f"min {res['console_min_v']:.3f} V, {res['time_below_4v5_ms']:.1f} ms below 4.5 V")
    check(f'S1.3 {name} radio resets', res['radio_resets'] == 0, f"{res['radio_resets']}; reservoir min while on {res['reservoir_min_v_while_on']:.2f} V")
    check(f'S1.4 {name} TX through power manager', res['frames_admitted'] == res['tx_frames'],
          f"{res['tx_frames']} data frames sent, {res['frames_admitted']} admitted, refused-ms {res['tx_refused_ms']}, "
          f"dropped {res['tx_drops']} (TCP retransmit), {res['tx_mgmt_unadmitted']} library frames unadmitted, "
          f"{res['connects']} connects, parks {res['pm_parks']}")

log('\n## S1.6 SPI link with bit errors on MOSI and MISO (fresh console)')
sh(f'cc -O2 -std=c11 {LAYOUT} -DSPI_BER=1e-4 -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} -o sysim_ber sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
sh('./sysim_ber rom/wifitest.gb fresh_ber 0.7 15 out', HERE)
res = json.load(open(os.path.join(OUT, 'fresh_ber_result.json')))
shown = [l.rstrip() for l in open(os.path.join(OUT, 'fresh_ber_tilemap.txt')) if l.strip()]
complete = len(shown) >= len(EXPECT) and all((e.fullmatch(s) if hasattr(e, 'fullmatch') else e == s) for e, s in zip(EXPECT, shown))
wrong = [s for s in shown if s not in ('ERR', 'TIMEOUT') and not any((e.fullmatch(s) if hasattr(e, 'fullmatch') else e == s) for e in EXPECT)]
check('S1.6 BER 1e-4 screen', complete and not wrong, f"{res['spi_bit_errors']} bit errors, {res['spi_bad_hdr']} bad headers, "
      f"{res['spi_bad_body']} bad bodies, ESP rejected {res['esp_rx_bad']}, {res['spi_resent']} requests re-sent; "
      + ('all 13 lines as expected' if complete else f'got {shown}'))

log('\n## S1.7 a ROM without the WIFICART opt-in gets no mailbox')
rom = bytearray(open(os.path.join(HERE, 'rom', 'wifitest.gb'), 'rb').read())
rom[0x150:0x158] = bytes(8)
open(os.path.join(OUT, 'nooptin.gb'), 'wb').write(rom)
sh('./sysim out/nooptin.gb nooptin 0.7 3 out', HERE)
res = json.load(open(os.path.join(OUT, 'nooptin_result.json')))
check('S1.7 no opt-in', res['wifi_rom'] == 0 and res['spi_xfers'] == 0 and res['cart_writes_mailbox'] == 0,
      f"wifi_rom {res['wifi_rom']}, {res['spi_xfers']} SPI transactions, {res['cart_writes_mailbox']} cart writes to bank 15")
rom = bytearray(open(os.path.join(HERE, 'rom', 'wifitest.gb'), 'rb').read())
rom[0x149] = 0x03                                  # WIFICART present, but only 32 KB RAM: bank 15 does not exist
x = 0
for i in range(0x134, 0x14D):                      # header checksum (the emulator checks it, like the boot ROM)
    x = (x - rom[i] - 1) & 0xFF
rom[0x14D] = x
open(os.path.join(OUT, 'nooptin32k.gb'), 'wb').write(rom)
sh('./sysim out/nooptin32k.gb nooptin32k 0.7 3 out', HERE)
res = json.load(open(os.path.join(OUT, 'nooptin32k_result.json')))
check('S1.7 WIFICART with 32 KB RAM', res['wifi_rom'] == 0 and res['spi_xfers'] == 0 and res['cart_writes_mailbox'] == 0,
      f"wifi_rom {res['wifi_rom']}, {res['spi_xfers']} SPI transactions")

log('\n## S1.8 shipped formatter / TX sizing (components/gbwifi) host test')
r = subprocess.run(f'cc -std=c11 -Wall -Wextra -Werror -I{gw} -o test_gbwifi test_gbwifi.c {gw}/gbwifi.c -lm && ./test_gbwifi',
                   cwd=HERE, shell=True, capture_output=True, text=True)
for l in r.stdout.splitlines():
    log(f'   | {l}')
check('S1.8 gbwifi', r.returncode == 0, 'scan lines <= 19 columns, connect parsing, airtime, power/time/BTC text')

log('\n## S1.9 GBDK-2020 library (gb/gbdk): demo ROM on the fresh and weak consoles')
sh(f'make -s GBDK={os.environ.get("GBDK", os.path.expanduser("~/opt/gbdk"))}', os.path.join(ROOT, 'gb', 'gbdk'))
GBDK_EXPECT = ['GBDK WIFI DEMO', 'PING:','PONG', 'TIME:', re.compile(r'2026-10-14 15:\d\d:\d\d'), 'GET /hello:',
               'Hello from WiFi!', 'POWER:', re.compile(r'RAIL 4\.\d\dV RES \d\.\d\dV'), 'DONE']
for name, r, secs in (('gbdk_fresh', 0.7, 10), ('gbdk_weak', 2.0, 20)):
    sh(f'./sysim ../../gb/gbdk/build/wifi_demo.gb {name} {r} {secs} out', HERE)
    tiles = open(os.path.join(OUT, f'{name}_tiles.bin'), 'rb').read()
    # GBDK's console font starts at tile 0 = ' '
    shown = [s for s in (''.join(chr(t + 32) if t < 96 else '?' for t in tiles[k * 20:k * 20 + 20]).rstrip() for k in range(18)) if s]
    res = json.load(open(os.path.join(OUT, f'{name}_result.json')))
    ok = len(shown) >= len(GBDK_EXPECT) and all((e.fullmatch(x) if hasattr(e, 'fullmatch') else e == x) for e, x in zip(GBDK_EXPECT, shown))
    for l in shown:
        log(f'   | {l}')
    check(f'S1.9 {name}', ok and res['radio_resets'] == 0 and res['time_below_4v5_ms'] == 0 and res['frames_admitted'] == res['tx_frames'],
          f"screen {'as expected' if ok else shown}; console min {res['console_min_v']:.3f} V, resets {res['radio_resets']}, "
          f"{res['tx_frames']} frames all admitted")

log('\n## S1.10 ADC error: the reservoir reads 2.5 % high (admission too generous), the rail 1 % high, weak consoles (fast boot, as S1.11)')
sh(f'cc -O2 -std=c11 {LAYOUT} -DADC_RES_GAIN=1.025 -DADC_RAIL_GAIN=1.01 -DESP_BOOT_MS=100 -DBOOT_CPU_A=0.020 -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} '
   f'-o sysim_adc sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
for name, r, secs in (('weak_adc', 2.0, 20), ('weak_plus_adc', 2.5, 60)):
    sh(f'./sysim_adc rom/wifitest.gb {name} {r} {secs} out', HERE)
    res = json.load(open(os.path.join(OUT, f'{name}_result.json')))
    check(f'S1.10 {name}', res['radio_resets'] == 0 and res['time_below_4v5_ms'] == 0,
          f"resets {res['radio_resets']}, reservoir min {res['reservoir_min_v_while_on']:.2f} V, console min {res['console_min_v']:.3f} V")

log('\n## S1.11 weak_plus (2.5 ohm) with a fast ESP boot: 100 ms at 20 mA to app_main (app sdkconfig fast boot; measure on the XIAO)')
sh(f'cc -O2 -std=c11 {LAYOUT} -DESP_BOOT_MS=100 -DBOOT_CPU_A=0.020 -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} '
   f'-o sysim_fastboot sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
sh('./sysim_fastboot rom/wifitest.gb weak_plus_fastboot 2.5 60 out', HERE)
res = json.load(open(os.path.join(OUT, 'weak_plus_fastboot_result.json')))
shown = [l.rstrip() for l in open(os.path.join(OUT, 'weak_plus_fastboot_tilemap.txt')) if l.strip()]
complete = len(shown) >= len(EXPECT) and all((e.fullmatch(x) if hasattr(e, 'fullmatch') else e == x) for e, x in zip(EXPECT, shown))
check('S1.11 weak_plus_fastboot', complete and res['radio_resets'] == 0 and res['time_below_4v5_ms'] == 0,
      f"screen {'complete' if complete else shown}; resets {res['radio_resets']}, {res['tx_frames']} frames, drops {res['tx_drops']}, "
      f"parks {res['pm_parks']}, console min {res['console_min_v']:.3f} V")

log('\n## S1.12 developer kit: RGBDS library example and GBDK template through run_rom.py (docs/ROM_DEV.md)')
sh('make -s', os.path.join(ROOT, 'gb', 'rgbds', 'example'))
for name, cmd in (('rgbds example (weak)', f'{sys.executable} run_rom.py ../../gb/rgbds/example/build/example.gb --console weak '
                   f'--seconds 8 --expect ../../gb/rgbds/example/expect.txt --out out/kit_rgbds'),
                  ('gbdk template (weak)', 'sh ../../gb/gbdk/template/test.sh --out ../../../sim/system/out/kit_template')):
    r = subprocess.run(cmd, cwd=HERE, shell=True, capture_output=True, text=True)
    check(f'S1.12 {name}', r.returncode == 0, (r.stdout.strip().splitlines() or ['no output'])[-1] if r.returncode == 0
          else ' | '.join(l for l in r.stdout.splitlines() if l.startswith('FAIL')) or r.stderr.strip()[-200:])

log('\n## S1.13 thermal: U102 TPS2553 (SOT-23, RthJA 182.6 C/W, TJ <= 125 C) and Q101 SiA975DJ (RthJA 110 C/W steady, TJ max 150 C)')
log('  Tj = 60 C ambient + worst rolling 1 s average power x steady-state RthJA (conservative: the real time constant is longer);')
log('  hot-plug pulse: worst 1 s energy into the die alone (~0.5 mJ/K, no spreading), an upper bound for the short peak')
sh(f'cc -O2 -std=c11 {LAYOUT} -DVSRC=5.25 -Wall -Wextra -Werror -Wno-unused-parameter -I{pm} -I{el} -I{gw} '
   f'-o sysim_usb sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
sh('./sysim_usb rom/wifitest.gb usb_like 0.2 12 out', HERE)
for name in ('fresh', 'tired', 'weak', 'weak_plus', 'weak_plus_fastboot', 'usb_like'):
    res = json.load(open(os.path.join(OUT, f'{name}_result.json')))
    tj_t = 60 + res['p_tps_max1s_w'] * 182.6
    tj_q = 60 + res['p_q101_max1s_w'] * 110
    pulse = res['p_tps_max1s_w'] / 0.5e-3          # J in the worst 1 s window / die heat capacity -> K
    check(f'S1.13 {name} thermal', tj_t < 125 and tj_q < 125 and 60 + pulse < 125,
          f"TPS2553 peak {res['p_tps_peak_w']:.2f} W, 1 s {res['p_tps_max1s_w'] * 1e3:.1f} mW -> Tj {tj_t:.0f} C "
          f"(pulse bound {60 + pulse:.0f} C); Q101 peak {res['p_q101_peak_w']:.2f} W, 1 s {res['p_q101_max1s_w'] * 1e3:.1f} mW -> Tj {tj_q:.0f} C")

log('\n## S1.14 sanitizers: randomised inputs to every parser of untrusted data, and the full system under ASan/UBSan')
cc_san = 'clang -std=c11 -g -O1 -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer'
sh(f'{cc_san} -I{el} -I{gw} -o fuzz/fuzz_c fuzz/fuzz_c.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
r = subprocess.run('./fuzz/fuzz_c 300000 12345', cwd=HERE, shell=True, capture_output=True, text=True)
check('S1.14 fuzz (esp_link, MSS clamp, klines, connect, scan, sanitize)', r.returncode == 0,
      r.stdout.strip() or r.stderr.strip()[-300:])
sh(f'{cc_san} {LAYOUT} -w -I{pm} -I{el} -I{gw} -o sysim_asan sysim.c {pm}/power_mgr.c {el}/esp_link.c {gw}/gbwifi.c -lm', HERE)
for name, rom, ohm, secs in (('asan_weak', 'rom/wifitest.gb', 2.0, 20), ('asan_gbdk', '../../gb/gbdk/build/wifi_demo.gb', 0.7, 10)):
    r = subprocess.run(f'./sysim_asan {rom} {name} {ohm} {secs} out', cwd=HERE, shell=True, capture_output=True, text=True)
    bad = [l for l in (r.stdout + r.stderr).splitlines() if 'runtime error' in l or 'ERROR: AddressSanitizer' in l]
    check(f'S1.14 {name} under ASan/UBSan', r.returncode == 0 and not bad, bad[0] if bad else 'no findings')

log('\n## S1.15 soak: gb/gbdk/soak loops PING/TIME/GET/POWER (SEQ wraps every 256), 5 simulated minutes per console')
sh(f'make -s GBDK={os.environ.get("GBDK", os.path.expanduser("~/opt/gbdk"))}', os.path.join(ROOT, 'gb', 'gbdk', 'soak'))
for con in ('fresh', 'weak'):
    out = os.path.join(OUT, f'soak_{con}')
    r = subprocess.run(f'{sys.executable} run_rom.py ../../gb/gbdk/soak/build/soak.gb --console {con} --seconds 300 --out {out}',
                       cwd=HERE, shell=True, capture_output=True, text=True)
    v = json.load(open(os.path.join(out, 'verdict.json')))
    m = re.search(r'OK (\d+) ERR (\d+) TO (\d+)', ' '.join(v['screen']))
    ok = r.returncode == 0 and m and int(m.group(1)) > 1000 and m.group(2) == '0' and m.group(3) == '0'
    check(f'S1.15 soak {con}', bool(ok), (m.group(0) if m else 'no counters') +
          f", resets {v['radio_resets']}, drops {v['tx_drops']}, console min {v['console_min_v']:.3f} V")
log(f"\nRESULT: {'ALL PASS' if not fails else str(len(fails)) + ' FAIL: ' + ', '.join(fails)}")
open(os.path.join(OUT, 'report.txt'), 'w').write('\n'.join(lines) + '\n')
sys.exit(1 if fails else 0)
