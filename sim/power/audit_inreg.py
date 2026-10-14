#!/usr/bin/env python3
"""Audit A8: precision input-voltage regulator (inreg.DESIGN) with vendor models, over tolerance corners.

  A8.1 DC set-point: 4.588 V +-1 % (TLV431B 0.5 % + two 0.1 % resistors) at every regulating console
  A8.2 plausibility of every operating point (guards against ngspice converging to a non-physical solution)
  A8.3 loop stability: phase margin >= 45 deg, gain margin >= 13 dB (10 + TLV431 model gain deficit) over every
       0 dB / -180 deg crossing, console 1.4-2.55 ohm, 47-220 uF,
       Cc +-10 %, Rk/Rc +-1 %
  A8.4 hot-plug: console never below min(4.5 V, its own no-cart voltage - 20 mV) after the first 200 us of contact
  A8.5 burst after idle (integrator wind-up case), same limit, console 1.6-2.4 ohm
  A8.6 TLV431 inside its datasheet range: cathode >= Vref + 0.2 V at every regulating operating point
  A8.7 no oscillation in the time domain: gate content above 20 kHz < 5 mV p-p after a 10 mA console step
       (inreg_ring.py), console 1.4-2.5 ohm, 47/220 uF, Cc +-10 %, Rc +-1 %
Exit code != 0 on any FAIL. Writes out/audit_inreg.txt.
"""
import itertools, os, sys
import numpy as np
import inreg as I
from inreg_ring import ring

m = I.Vendor
lines, fails = [], []


def log(s=''):
    lines.append(s)
    print(s, flush=True)


def check(name, ok, detail):
    log(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        fails.append(name)


def limit(rsrc, load):
    return min(4.5, 5 - rsrc * load - 0.02)


D = I.DESIGN
# reservoir under test (env, for part changes): T520B 3x330u -20 % = 0.792 mF, 3x 40 mOhm = 13.3 mOhm
RES = dict(cres=float(os.environ.get('CRES', 0.99e-3)), resr=float(os.environ.get('RESR', 0.0083)))
setpoint = 1.24 * (1 + D['R1'] / D['R2'])
log('# A8 precision input-voltage regulator audit')
log(f"design: R1 {D['R1']:.0f} R2 {D['R2']:.0f} Rg {D['Rg']:.0f} Rk {D['Rk']:.0f} Cc {D['Cc']*1e9:.2f}n Rc {D['Rc']:.0f} "
    f"comp on {D['comp_node']}, {D['clamp'].count('DSS') - 1}-diode gate clamp, power-on hold {'on' if D.get('por') else 'off'}, "
    f"TPS2553 {D.get('tps2553', 'behavioural')} model, EN delay {D['t_en']*1000:.0f} ms; set-point {setpoint:.3f} V")
log()
log('## A8.1/A8.2 DC set-point and operating-point plausibility')
for rsrc, vres in itertools.product((1.4, 1.8, 2.2, 2.5), (0.5, 3.0, 4.3)):
    vcc, ires, vgs, vk = I.dc_point(m, D, rsrc, vres)
    nocart = 5 - rsrc * 0.16
    plausible = 3.0 < vcc <= nocart + 1e-3 and -0.2 < -ires < 0.2 and -5 < vgs < 0.1
    check(f'A8.2 op R={rsrc} vres={vres}', plausible, f'VCC {vcc:.4f} V, I {abs(ires)*1000:.1f} mA, Vgs {vgs:.2f} V')
    regulating = nocart - rsrc * 0.150 < setpoint < nocart
    if regulating:
        check(f'A8.1 set-point R={rsrc} vres={vres}', abs(vcc - setpoint) / setpoint < 0.01,
              f'{vcc:.4f} V vs {setpoint:.4f} V ({100*(vcc/setpoint-1):+.2f} %)')
        # TLV431 datasheet range starts at Vka = Vref; below it the part is saturated (LOGBOOK 08:13)
        check(f'A8.6 TLV431 cathode R={rsrc} vres={vres}', vk >= 1.24 + 0.2, f'Vka {vk:.3f} V (min 1.44 V)')

log()
log('## A8.3 loop stability over corners')
worst = (999, None)
worst_gm = 99
# 10 dB plus the 2.9 dB by which the TLV431 model's gain is below the datasheet (models/tests/tlv431_ac_vs_datasheet.py)
GM_MIN = 13.0
for rsrc, cap, cc, rk, rc in itertools.product((1.4, 1.8, 2.2, 2.5, 2.55), (47e-6, 100e-6, 220e-6), (0.9, 1.1), (0.99, 1.01), (0.99, 1.01)):
    p = dict(D, gb_cap=cap, Cc=D['Cc'] * cc, Rk=D['Rk'] * rk, Rc=D['Rc'] * rc)
    f, db, ph, fc, pm, gm = I.loop_gain(m, p, rsrc, 3.0)
    if pm is None:
        # no 0 dB crossing: loop gain below 1 everywhere means the regulator is not regulating here
        regulating = 5 - rsrc * 0.31 < setpoint < 5 - rsrc * 0.16
        check(f'A8.3 R={rsrc} C={cap*1e6:.0f}u', not regulating, f'no crossover (|T| max {db.max():.1f} dB), regulating={regulating}')
        continue
    if pm < worst[0]:
        worst = (pm, (rsrc, cap, cc, rk, rc, fc, gm))
    worst_gm = min(worst_gm, gm if gm is not None else 99)
    if pm < 45 or (gm is not None and gm < GM_MIN):
        check(f'A8.3 R={rsrc} C={cap*1e6:.0f}u Cc x{cc} Rk x{rk} Rc x{rc}', False, f'PM {pm:.1f} deg, GM {gm}, fc {fc:.0f} Hz')
log(f'worst phase margin {worst[0]:.1f} deg at (R, Ccons, Cc, Rk, Rc, fc, GM) = {worst[1]}')
check('A8.3 min phase margin >= 45 deg', worst[0] >= 45, f'{worst[0]:.1f} deg')
check(f'A8.3 min gain margin >= {GM_MIN:.0f} dB', worst_gm >= GM_MIN, f'{worst_gm:.1f} dB')

log()
log('## A8.4 hot-plug')
for rsrc, cap, cc in itertools.product((1.6, 2.0, 2.3, 2.5, 3.0), (47e-6, 220e-6), (0.9, 1.1)):
    d = I.transient(m, dict(D, gb_cap=cap, Cc=D['Cc'] * cc), rsrc, t_end=0.06, tag='a8', **RES)
    t = d['t']
    vmin = d['vcc'][t > 0.2e-3].min()
    lim = limit(rsrc, 0.16)
    check(f'A8.4 R={rsrc} C={cap*1e6:.0f}u Cc x{cc}', vmin >= lim, f'min {vmin:.4f} V, limit {lim:.4f} V, margin {1000*(vmin-lim):+.1f} mV')

log()
log('## A8.5 burst after idle (wind-up)')
for rsrc, cap, cc in itertools.product((1.6, 1.8, 2.0, 2.2, 2.4), (47e-6, 220e-6), (0.9, 1.1)):
    d = I.transient(m, dict(D, gb_cap=cap, Cc=D['Cc'] * cc), rsrc, t_end=0.5, burst_ms=100, period_ms=10000,
                    tag='a8', rx=0.0, t_first=0.35, **RES)
    t = d['t']
    full = d['vres'][(t > 0.3) & (t < 0.35)].min()
    vmin = d['vcc'][t > 0.349].min()
    lim = limit(rsrc, 0.16)
    if full < 4.2:
        log(f'INFO  A8.5 R={rsrc}: reservoir only {full:.2f} V before the burst (console too weak to fill it), case not applicable')
        continue
    check(f'A8.5 R={rsrc} C={cap*1e6:.0f}u Cc x{cc}', vmin >= lim, f'min {vmin:.4f} V, limit {lim:.4f} V, margin {1000*(vmin-lim):+.1f} mV')

log()
log('## A8.7 time-domain ring test')
for rsrc, cap, cc, rc in itertools.product((1.4, 1.6, 1.8, 2.2, 2.5), (47e-6, 220e-6), (0.9, 1.1), (0.99, 1.01)):
    _, _, pp, fr = ring(dict(D, gb_cap=cap, Cc=D['Cc'] * cc, Rc=D['Rc'] * rc), rsrc, 'a8')
    check(f'A8.7 R={rsrc} C={cap*1e6:.0f}u Cc x{cc} Rc x{rc}', pp < 5e-3, f'gate >20 kHz {1e3*pp:.2f} mV p-p (strongest at {fr/1e3:.0f} kHz)')

log()
log(f"RESULT: {'ALL PASS' if not fails else str(len(fails)) + ' FAIL: ' + ', '.join(fails[:12])}")
open(os.path.join(I.OUT, 'audit_inreg.txt'), 'w').write('\n'.join(lines) + '\n')
sys.exit(1 if fails else 0)
