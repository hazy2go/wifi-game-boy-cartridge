#!/usr/bin/env python3
"""Precision input-voltage regulator (replaces the BQ24075 VIN-DPM), transistor level with vendor models.

    cart VCC --[TPS2116 mux]-- VMUX --[TPS2553, 150 mA]-- A --[Q1 PMOS]--S--[Q2 PMOS]-- VRES (reservoir)
                                           gates G joined, sources S joined (back-to-back: blocks both ways)
    R1/R2 divider from VMUX -> TLV431 REF. TLV431 cathode pulls G down through Rk when VMUX is above the
    set-point, turning the PMOS pair on. Rg holds G at S (off) otherwise. Cc (+Rc) from cathode to REF =
    integrator compensation. Set-point = Vref * (1 + R1/R2) (+ Iref*R1).
    The divider senses VMUX, not raw cart VCC, so on USB power the regulator is simply fully on.

Analyses (each an audit check in audit_inreg.py):
  dc    regulation point vs load, reference tolerance
  ac    loop gain / phase margin, loop broken between VMUX and the divider (Middlebrook voltage injection:
        divider input impedance ~37k >> VMUX source impedance ~1 ohm)
  tran  hot-plug and radio bursts with the console models of run_sim.py
"""
import os, subprocess
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(os.path.dirname(HERE), 'models')
OUT = os.path.join(HERE, 'out')
os.makedirs(OUT, exist_ok=True)

# anti-wind-up clamp: two diodes from source to gate (LOGBOOK 06:03); generic small-signal diode model
CLAMP = '.model DSS D(Is=2.52n N=1.752 Rs=0.568 Cjo=4p)\nDcl1 s cl1 DSS\nDcl2 cl1 g DSS'
DESIGN = dict(R1=27.0e3, R2=10.0e3, Rg=680.0, Rk=2.2e3, Rbias=1e9, Cg=1e-12, Cc=2.2e-9, Rc=10e3, comp_node='g',
              clamp=CLAMP, t_en=15e-3, ilim=0.150, rsw=0.085, rmux=0.07,
              gb_cap=100e-6, gb_load=0.100, logic_load=0.060)


class Vendor:
    """Vendor models, verified against their datasheets in sim/models/README.md.
    TLV431: TI SLIM005 (AEi behavioural), pins A K REF. SiA975DJ: Vishay doc 65767, one channel, pins D G S."""
    include = (f".include {MODELS_DIR}/ti_tlv431/TLV431.lib\n"
               f".include {MODELS_DIR}/vishay_sia975dj/SiA975DJ_PS.lib")

    @staticmethod
    def pmos(name, d, g, s):
        return f'X{name} {d} {g} {s} SiA975DJ'

    @staticmethod
    def tlv431(name, k, a, ref):
        return f'X{name} {a} {k} {ref} TLV431'


def core(m, p, ramp=False):
    mr = 'min(1, max(1e-6, time/1e-3))' if ramp else '1'
    t_en = p.get('t_en', 0.5e-3)  # TPS2553 EN delay after VMUX (RC on EN)
    sr = f'min(1, max(1e-6, (time-{t_en})/1.5e-3))' if ramp else '1'
    rc = f"Rc ccn ref {p['Rc']}" if p['Rc'] > 0 else 'Vrc ccn ref DC 0'
    return f"""* --- precision input-voltage regulator (sim/power/inreg.py) ---
* TPS2116 mux: on-resistance with a 1 ms turn-on ramp (soft-start), so hot-plug starts from an empty cart
Bmux vcc vmux I = (V(vcc)-V(vmux))/{p['rmux']} * {mr}
Cmux vmux 0 1u
* TPS2553: constant-current limit, 1.5 ms output rise (datasheet tr), 85 mOhm
Bsw vmux a I = max(0, min({p['ilim']} * {sr}, (V(vmux)-V(a))/{p['rsw']}))
Ca a 0 1u
{m.pmos('Q1', 'a', 'g', 's')}
{m.pmos('Q2', 'vres', 'g', 's')}
Rg s g {p['Rg']}
Rk g k {p['Rk']}
Rbias {p.get('bias_node', 's')} k {p['Rbias']}
Cg s g {p['Cg']}
R1 top ref {p['R1']}
R2 ref 0 {p['R2']}
{m.tlv431('U1', 'k', '0', 'ref')}
Cc {p.get('comp_node', 'k')} ccn {p['Cc']}
{p.get('clamp', '')}
{rc}
"""


def console(rsrc, p):
    return f"""Vgb src 0 5.0
Rsrc src vcc {rsrc}
Cgb vcc 0 {p['gb_cap']}
Igb vcc 0 {p['gb_load'] + p['logic_load']}
{f"Istep vcc 0 PWL(0 0 {p['step_t']} 0 {p['step_t'] + 10e-6} {p['step_a']})" if p.get('step_a') else ''}
"""


def ngspice(net, tag):
    path = os.path.join(OUT, f'inreg_{tag}.cir')
    open(path, 'w').write(net)
    r = subprocess.run(['ngspice', '-b', path], capture_output=True, text=True, cwd=OUT)  # out/.spiceinit: ngbehavior=psa
    if r.returncode != 0 or 'Error' in r.stdout or 'error' in r.stderr.lower():
        raise RuntimeError(f'ngspice {tag}:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}')
    return r.stdout


def nodeset(rsrc, p):
    """Initial guess for the operating point. Without it ngspice can land on a non-physical solution of the
    vendor models (seen: VCC = 1.09 V with the bias resistor on VMUX), and an AC analysis around that
    point is meaningless. Every op result is also checked for plausibility by the audit."""
    x = 5.0 - rsrc * (p['gb_load'] + p['logic_load'])
    return f".nodeset v(vcc)={x:.3f} v(vmux)={x:.3f} v(top)={x:.3f} v(a)={x:.3f} v(s)={x - 0.2:.3f} v(g)={x - 1.0:.3f} v(k)={x - 1.1:.3f} v(ref)=1.24"


def dc_point(m, p, rsrc, vres):
    """Operating point with the reservoir held at vres (worst case for current: empty reservoir = low vres).
    Returns (V(vcc), I into reservoir)."""
    net = f"""* inreg dc
{m.include}
{console(rsrc, p)}
Vtop vmux top DC 0
{core(m, p)}
Vres vres_src 0 DC {vres}
Rres vres_src vres 0.01
{nodeset(rsrc, p)}
.control
op
print v(vcc)
print v(vmux)
print i(Vres)
print v(g)-v(s)
print v(k)
quit
.endc
.end
"""
    out = ngspice(net, f'dc_{rsrc}_{vres}')
    vals = {}
    for line in out.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            vals[k.strip()] = float(v.split()[0])
    return vals['v(vcc)'], -vals['i(vres)'], vals['v(g)-v(s)'], vals['v(k)']


def loop_gain(m, p, rsrc, vres, fstart=10, fstop=10e6):
    """Middlebrook voltage injection between VMUX and divider top. T = -V(vmux)/V(top).
    Returns (f, |T| dB, phase deg, crossover Hz, phase margin deg, gain margin dB)."""
    net = f"""* inreg loop gain
{m.include}
{console(rsrc, p)}
Vtop top vmux DC 0 AC 1
{core(m, p)}
Vres vres_src 0 DC {vres}
Rres vres_src vres 0.01
{nodeset(rsrc, p)}
.control
op
print v(vcc)
ac dec 50 {fstart} {fstop}
let T = -v(vmux)/v(top)
wrdata {OUT}/inreg_ac.dat mag(T) ph(T)
quit
.endc
.end
"""
    ngspice(net, f'ac_{rsrc}_{vres}')
    d = np.loadtxt(os.path.join(OUT, 'inreg_ac.dat'))
    f, db = d[:, 0], 20 * np.log10(d[:, 1])
    ph = np.degrees(np.unwrap(d[:, 3]))       # ngspice ph() is in radians
    ph = ph - 360 * np.round(ph[0] / 360)      # start in (-180, 180]
    idx = np.where((db[:-1] > 0) & (db[1:] <= 0))[0]
    fc = pm = gm = None
    if len(idx):
        i = idx[0]
        fc = 10 ** np.interp(0, [db[i + 1], db[i]], [np.log10(f[i + 1]), np.log10(f[i])])
        pm = 180 + np.interp(np.log10(fc), np.log10(f), ph)   # distance of the phase from -180 deg
    cross = np.where((ph[:-1] > -180) & (ph[1:] <= -180))[0]
    if len(cross):
        gm = -db[cross[0]]
    return f, db, ph, fc, pm, gm


def transient(m, p, rsrc, t_end=0.6, burst_ms=2.0, period_ms=10.0, cres=0.99e-3, resr=0.0083, eta=0.88,
              tx=0.350, rx=0.084, tag='tran', t_first=0.1):
    """Hot-plug at t=0 (reservoir empty), radio enabled at 4.2 V (ideal power-good, off at 2.0 V), RX
    baseline plus TX bursts from 100 ms. Radio = constant-power load through the TPS63802 (eta).
    Returns dict of arrays t, vcc, vres, i_in."""
    n = max(1, int(t_end * 1000 / period_ms))
    pts = ['0 0', f'{t_first} 0']
    for k in range(n):
        t0 = t_first + k * period_ms / 1000
        if t0 + burst_ms / 1000 > t_end:
            break
        pts += [f'{t0:.6f} 0', f'{t0 + 5e-6:.6f} 1', f'{t0 + burst_ms / 1000:.6f} 1', f'{t0 + burst_ms / 1000 + 5e-6:.6f} 0']
    net = f"""* inreg transient
{m.include}
{console(rsrc, p)}
Vtop vmux top DC 0
{core(m, p, ramp=True)}
Cres vres cresn {cres}
Resr cresn 0 {resr}
Bpgx pgx 0 V = 0.5*(1+tanh((V(vres)-(4.2-2.2*V(pg)))/0.02))
Rpg pgx pg 1k
Cpg pg 0 100n
Vtx txp 0 PWL({' '.join(pts)})
Bbb vres 0 I = V(pg) * 3.3*({rx} + ({tx}-{rx})*V(txp))/({eta}*max(V(vres),1.0)) * 0.5*(1+tanh((V(vres)-1.6)/0.02))
.ic v(vcc)={5.0 - rsrc * (p['gb_load'] + p['logic_load'])} v(vmux)=0 v(a)=0 v(s)=0 v(g)=0 v(k)=0 v(ref)=0 v(top)=0 v(vres)=0 v(pg)=0
.options method=gear reltol=1e-3
.tran 5u {t_end} 0 5u uic
.control
run
wrdata {OUT}/inreg_{tag}.dat v(vcc) v(vres) v(pg) i(Vtop)
quit
.endc
.end
"""
    ngspice(net, tag)
    d = np.loadtxt(os.path.join(OUT, f'inreg_{tag}.dat'))
    return dict(t=d[:, 0], vcc=d[:, 1], vres=d[:, 3], pg=d[:, 5])
