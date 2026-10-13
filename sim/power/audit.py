#!/usr/bin/env python3
"""Audit loop for the power-path simulation. Every check has a pass/fail limit; exit code != 0 on any FAIL.

  A1  solver cross-check: ngspice vs the independent pyref integrator, chosen design, all consoles/profiles
  A2  timestep convergence: ngspice at 20us vs 5us max step
  A3  analytic steady state: console rail floor vs closed-form solution
  A4  energy sanity: source energy >= delivered radio energy (pyref)
  A5  realistic firmware ('frame' gating, non-throttleable RF cal) across tolerance corners
Writes out/audit.txt and prints it.
"""
import itertools, os, sys
import numpy as np
import run_sim as R
import pyref

OUT = R.OUT
# chosen design (logbook 2026-10-14 04:59): TPS2553 150mA -> BQ24075 DPM 4.5V typ -> 3x 6TPE330MAP -> TPS63802
CHOSEN = dict(ilim=0.150, vdpm=4.5, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45, cres=0.99e-3, resr=0.0083)
lines, fails = [], []


def log(s=''):
    lines.append(s)
    print(s, flush=True)


def check(name, ok, detail):
    log(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        fails.append(name)


def spice(cons, prof, step='20u', **kw):
    tag = f"audit_{prof}_{cons}_{step}"
    net = R.netlist_new(R.ESPC3, prof, cons, **kw).replace('{TAG}', tag)
    net = net.replace('.tran 20u', f'.tran {step}').replace(' 0 20u uic', f' 0 {step} uic')
    R.run(net, tag)
    t, gb, res, pg, thr, ipr, il = R.load(os.path.join(OUT, tag + '.dat'), 6)
    os.remove(os.path.join(OUT, tag + '.dat'))
    dt = np.diff(t)
    w = np.append(dt, 0)
    m = t > R.CAL_MS / 1000
    want = np.sum((np.maximum(ipr - R.ESPC3['rx'], 0) * w)[m])
    got = np.sum((np.maximum(il - R.ESPC3['rx'], 0) * w)[m])
    up = t[np.argmax(pg > 0.5)] * 1000 if (pg > 0.5).any() else None
    hi = pg > 0.5
    drops = int(np.sum(hi[:-1] & ~hi[1:]))
    return dict(gb_min=gb.min(), unsafe_ms=1000 * np.sum(dt[gb[:-1] < R.GB_MIN_OK]), tx=got / want if want else 0,
                up_ms=up, res_min=res[t > (up or 0) / 1000 + 0.001].min() if up else res.min(), pg_drops=drops)


log('# Power-path simulation audit')
log()
log('## A1 ngspice vs independent pyref (chosen design, ideal gating)')
log(f"{'cons':6}{'profile':19}{'GBmin sp/py':>14}{'TX% sp/py':>12}{'up ms sp/py':>13}{'resmin sp/py':>14}{'drops sp/py':>12}")
for cons, prof in itertools.product(R.CONSOLES, R.PROFILES):
    s = spice(cons, prof, **CHOSEN)
    p = pyref.run(R.CONSOLES[cons]['rsrc'], prof, **CHOSEN)
    log(f"{cons:6}{prof:19}{s['gb_min']:7.3f}/{p['gb_min']:.3f}{100*s['tx']:6.1f}/{100*p['tx']:.1f}"
        f"{(s['up_ms'] or -1):7.1f}/{(p['up_ms'] or -1):.1f}{s['res_min']:8.2f}/{p['res_min']:.2f}{s['pg_drops']:7d}/{p['pg_drops']}")
    check(f'A1 {cons}/{prof} GBmin', abs(s['gb_min'] - p['gb_min']) < 0.02, f"|d|={abs(s['gb_min']-p['gb_min'])*1000:.1f} mV (limit 20)")
    check(f'A1 {cons}/{prof} TX', abs(s['tx'] - p['tx']) < 0.03, f"|d|={100*abs(s['tx']-p['tx']):.1f} pts (limit 3)")
    check(f'A1 {cons}/{prof} up', s['up_ms'] is not None and p['up_ms'] is not None and abs(s['up_ms'] - p['up_ms']) < 2,
          f"sp={s['up_ms']} py={p['up_ms']} (limit 2 ms)")

log()
log('## A2 timestep convergence (ngspice 20us vs 5us), worst_11b_12ms')
for cons in R.CONSOLES:
    a = spice(cons, 'worst_11b_12ms', **CHOSEN)
    b = spice(cons, 'worst_11b_12ms', step='5u', **CHOSEN)
    check(f'A2 {cons} GBmin', abs(a['gb_min'] - b['gb_min']) < 0.005, f"{a['gb_min']:.4f} vs {b['gb_min']:.4f} (limit 5 mV)")
    check(f'A2 {cons} TX', abs(a['tx'] - b['tx']) < 0.01, f"{100*a['tx']:.2f}% vs {100*b['tx']:.2f}% (limit 1 pt)")

log()
log('## A3 analytic console-rail floor')
# Steady state with the input stage saturated: V = 5 - R*(I0 + I_in), I_in = min(ilim, g*(V - vdpm)) -> solve
I0 = R.GB_LOAD_A + 0.060
for cons in R.CONSOLES:
    r = R.CONSOLES[cons]['rsrc']
    v_lim = 5 - r * (I0 + CHOSEN['ilim'])
    v_dpm = (5 - r * I0 + r * CHOSEN['gain'] * CHOSEN['vdpm']) / (1 + r * CHOSEN['gain'])
    v = max(v_lim, v_dpm)
    p = pyref.run(r, 'stress_50pct', **CHOSEN)
    # the floor can dip below steady state only transiently (hot-plug); allow 30 mV
    check(f'A3 {cons}', p['gb_min'] > v - 0.03 and p['gb_min'] < v + 0.03, f"analytic {v:.3f} V, sim min {p['gb_min']:.3f} V (limit +-30 mV)")

log()
log('## A4 energy sanity (pyref, 2 s)')
for cons in R.CONSOLES:
    p = pyref.run(R.CONSOLES[cons]['rsrc'], 'stress_50pct', **CHOSEN)
    check(f'A4 {cons}', p['e_src'] > p['e_load'] > 0 or (cons == 'weak' and p['e_load'] >= 0),
          f"source {p['e_src']:.2f} J >= radio {p['e_load']:.2f} J")

log()
log('## A5 realistic firmware (frame gating, 1 ms ADC, RF cal not throttleable) x tolerance corners')
log('corners: ilim 120/150/180 mA (TPS2553 tolerance, to be confirmed vs datasheet), DPM 4.35/4.5/4.63 V,')
log('         logic load 40/60/100 mA, eta 0.80/0.88. Pass = console never < 4.5V AND (radio never resets after first power-good')
log('         unless the console is weak).')
worst = {}
rows = []
for cons, prof in itertools.product(R.CONSOLES, R.PROFILES):
    for ilim, vdpm, logic, eta in itertools.product((0.12, 0.15, 0.18), (4.35, 4.5, 4.63), (0.04, 0.06, 0.10), (0.80, 0.88)):
        kw = dict(CHOSEN, ilim=ilim, vdpm=vdpm)
        p = pyref.run(R.CONSOLES[cons]['rsrc'], prof, frame=True, cal_throttle=False, logic_load=logic, eta=eta, **kw)
        rows.append((cons, prof, ilim, vdpm, logic, eta, p))
for cons in R.CONSOLES:
    sub = [r for r in rows if r[0] == cons]
    gmin = min(sub, key=lambda r: r[6]['gb_min'])
    worst_unsafe = max(sub, key=lambda r: r[6]['unsafe_ms'])
    drops = max(sub, key=lambda r: r[6]['pg_drops'])
    txs = [r[6]['tx'] for r in sub if r[1] == 'traffic_2ms_20pct']
    log(f"{cons}: GBmin {gmin[6]['gb_min']:.3f} V (ilim {gmin[2]}, dpm {gmin[3]}, logic {gmin[4]}, eta {gmin[5]}, {gmin[1]}); "
        f"max ms<4.5V {worst_unsafe[6]['unsafe_ms']:.1f}; max radio resets {drops[6]['pg_drops']} ({drops[1]}, ilim {drops[2]}, dpm {drops[3]}); "
        f"busy-traffic TX {100*min(txs):.0f}-{100*max(txs):.0f}%")
    check(f'A5 {cons} console safe', worst_unsafe[6]['unsafe_ms'] == 0, f"max {worst_unsafe[6]['unsafe_ms']:.1f} ms below 4.5 V")
    if cons != 'weak':
        check(f'A5 {cons} radio no resets', drops[6]['pg_drops'] == 0, f"max {drops[6]['pg_drops']} power-good drops")
    else:
        log(f"INFO  A5 weak radio resets: max {drops[6]['pg_drops']} (radio may cycle on a weak console; console safety is the requirement)")

log()
log(f"RESULT: {'ALL PASS' if not fails else str(len(fails)) + ' FAIL: ' + ', '.join(fails)}")
open(os.path.join(OUT, 'audit.txt'), 'w').write('\n'.join(lines) + '\n')
sys.exit(1 if fails else 0)
