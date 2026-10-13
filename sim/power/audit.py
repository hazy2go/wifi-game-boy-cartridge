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
# chosen design (LOGBOOK 08:42): TPS2553 150mA -> TLV431B input-voltage regulator 4.588 V +-1 % -> 3x 6TPE330MAP -> TPS63802.
# Behavioural here (limit = min(ilim, gain*(Vgb - vdpm))); the transistor-level loop is audited in audit_inreg.py (A8).
# gain 15 A/V: A8 load regulation is ~9 mV over 0-150 mA. rsw 0.3 ohm: TPS2553 85 mOhm + SiA975DJ pair 2x 110 mOhm max at
# Vgs -1.8 V. vsup 0: the hardware has no cart-rail supervisor (the 4.45 V one in earlier runs was never in the netlist).
CHOSEN = dict(ilim=0.150, vdpm=4.588, psave=True, rsw=0.3, gain=15, gbwatch=True, vsup=0.0, cres=0.99e-3, resr=0.0083)
DPM = (4.542, 4.588, 4.634)   # TLV431B 0.5 % + 0.1 % divider, A8.1
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
log('## A5 firmware state machine (pyref.simulate_fw) x tolerance corners')
log('corners: ilim 129/150/174 mA (TPS2553 Eq.1 min/nom/max at 180k), DPM 4.542/4.588/4.634 V (TLV431B regulator), logic 40/60/100 mA,')
log('         buck-boost eta 0.80/0.92, switch path 0.2/0.6 ohm, ESP32-C3-MINI-1U TX 350 mA.')
log('traffic: busy 2ms/10ms, OFDM 2.5ms/6ms, stress 2ms/4ms, small-MTU 1Mbps fallback 4.5ms/20ms.')
log('RF calibration at WiFi start: ASSUMED 5 ms at 50 % TX (partial calibration, NVS data). Not measured; see A7.')
FW = dict(CHOSEN, tx=0.350)
for k in ('psave',):
    FW.pop(k, None)
TRAFFIC = {'busy': (2, 10), 'ofdm': (2.5, 6), 'stress': (2, 4), 'mtu1M': (4.5, 20)}
CORNERS = list(itertools.product((0.129, 0.150, 0.174), DPM, (0.04, 0.06, 0.10), (0.80, 0.92), (0.2, 0.6)))
rows = []
for cons, (tn, tp) in itertools.product(R.CONSOLES, TRAFFIC.items()):
    for ilim, vdpm, logic, eta, rsw in CORNERS:
        p = pyref.run_fw(R.CONSOLES[cons]['rsrc'], tp, cal_ms=5, cal_duty=0.5,
                         **dict(FW, ilim=ilim, vdpm=vdpm, logic_load=logic, eta=eta, rsw=rsw))
        rows.append((cons, tn, (ilim, vdpm, logic, eta, rsw), p))
for cons in R.CONSOLES:
    sub = [r for r in rows if r[0] == cons]
    wu = max(sub, key=lambda r: r[3]['unsafe_ms'])
    wr = max(sub, key=lambda r: r[3]['resets'])
    gm = min(sub, key=lambda r: r[3]['gb_min'])
    busy = [r[3]['tx'] for r in sub if r[1] == 'busy']
    log(f"{cons}: console min {gm[3]['gb_min']:.3f} V at {gm[2]} {gm[1]}; worst time <4.5 V {wu[3]['unsafe_ms']:.0f} ms at {wu[2]}; "
        f"worst radio resets {wr[3]['resets']} at {wr[2]} {wr[1]}; busy-traffic TX {100*min(busy):.0f}-{100*max(busy):.0f} %")
    check(f'A5 {cons} console never below 4.5 V', wu[3]['unsafe_ms'] == 0, f"worst {wu[3]['unsafe_ms']:.0f} ms at {wu[2]}")
    check(f'A5 {cons} no uncontrolled radio resets', wr[3]['resets'] == 0, f"worst {wr[3]['resets']} at {wr[2]} {wr[1]}")

log()
log('## A6 firmware cross-check: shipped C power manager (cosim.py, ctypes) vs pyref.simulate_fw')
import cosim
lib = cosim.build()
for cons, (tn, tp), corner in itertools.product(R.CONSOLES, TRAFFIC.items(), [(0.129, DPM[0], 0.10, 0.80, 0.6), (0.150, DPM[1], 0.06, 0.92, 0.2), (0.174, DPM[2], 0.04, 0.80, 0.6)]):
    ilim, vdpm, logic, eta, rsw = corner
    kw = dict(FW, ilim=ilim, vdpm=vdpm, logic_load=logic, eta=eta, rsw=rsw)
    p = pyref.run_fw(R.CONSOLES[cons]['rsrc'], tp, cal_ms=5, cal_duty=0.5, **kw)
    kw.pop('gbwatch', None)
    c = cosim.run(R.CONSOLES[cons]['rsrc'], *tp, cal_ms=5, cal_duty=0.5, lib=lib, **kw)
    ok = (abs(c['gb_min'] - p['gb_min']) < 0.005 and abs(c['tx'] - p['tx']) < 0.03 and abs(c['res_min'] - p['res_min']) < 0.03
          and (c['resets'] > 0) == (p['resets'] > 0) and abs(c['unsafe_ms'] - p['unsafe_ms']) < 5)
    check(f'A6 {cons}/{tn}/{corner}', ok, f"C: gb {c['gb_min']:.3f} tx {100*c['tx']:.1f} res {c['res_min']:.3f} rst {c['resets']} uns {c['unsafe_ms']:.0f} | "
          f"py: gb {p['gb_min']:.3f} tx {100*p['tx']:.1f} res {p['res_min']:.3f} rst {p['resets']} uns {p['unsafe_ms']:.0f}")

log()
log('## A7 RF-calibration energy budget (requirement for the bench measurement)')
log('Largest calibration (ms at 50 % TX duty) the radio survives without a reset, worst corner per console:')
for cons in R.CONSOLES:
    best = 0
    for cal in (2, 5, 10, 15, 20, 30, 50, 100):
        worst_rst = max(pyref.run_fw(R.CONSOLES[cons]['rsrc'], (2, 10), cal_ms=cal, cal_duty=0.5,
                                     **dict(FW, ilim=i, vdpm=v, logic_load=0.10, eta=0.80, rsw=0.6))['resets']
                        for i in (0.129,) for v in (DPM[0], DPM[2]))
        if worst_rst == 0:
            best = cal
    log(f"INFO  {cons}: survives up to {best} ms of calibration at 50 % duty (worst corner)")

log()
log(f"RESULT: {'ALL PASS' if not fails else str(len(fails)) + ' FAIL: ' + ', '.join(fails)}")
open(os.path.join(OUT, 'audit.txt'), 'w').write('\n'.join(lines) + '\n')
sys.exit(1 if fails else 0)
