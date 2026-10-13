"""Compensation search for the input regulator: minimum phase margin over console impedance and
capacitance, hot-plug and burst-after-idle (integrator windup) margins. Margin = VCC min minus
min(4.5 V, no-cart console voltage - 20 mV), ignoring the first 200 us of contact inrush."""
import itertools, sys
import numpy as np
import inreg as I

m = I.Vendor


def burst_after_idle(p, rsrc):
    """Reservoir full and radio idle (loop wound up), then a sustained 300 mA radio load at 0.3 s."""
    d = I.transient(m, dict(p), rsrc, t_end=0.5, burst_ms=100, period_ms=10000, tag='s', rx=0.0, t_first=0.35)
    t = d['t']
    nocart = 5 - rsrc * 0.16
    if d['vres'][(t > 0.3) & (t < 0.35)].min() < 4.0:
        return float('nan')  # console too weak to fill the reservoir: wind-up case does not apply
    return d['vcc'][t > 0.349].min() - min(4.5, nocart - 0.02)


def evaluate(kw):
    p = dict(I.DESIGN, **kw)
    pms = []
    for rsrc, cap in itertools.product((1.4, 1.8, 2.2, 2.5), (47e-6, 100e-6, 220e-6)):
        f, db, ph, fc, pm, gm = I.loop_gain(m, dict(p, gb_cap=cap), rsrc, 3.0)
        pms.append(pm if pm is not None else 999)
    hp = []
    for rsrc in (1.6, 2.0, 2.5, 3.0):
        d = I.transient(m, p, rsrc, t_end=0.15, tag='s', t_first=0.2)
        t = d['t']
        hp.append(d['vcc'][t > 0.2e-3].min() - min(4.5, 5 - rsrc * 0.16 - 0.02))
    bi = [burst_after_idle(p, r) for r in (1.8, 2.0, 2.3)]
    bi = [b for b in bi if b == b]
    return min(pms), min(hp), (min(bi) if bi else float('nan'))


if __name__ == '__main__':
    base = dict(Rg=680, Rbias=2.2e3, bias_node='vmux', t_en=15e-3)
    # anti-windup: integrator capacitor on the gate node, gate clamped to S - 2 Vf by two diodes
    clamp2 = ('.model DSS D(Is=2.52n N=1.752 Rs=0.568 Cjo=4p)\nDcl1 s cl1 DSS\nDcl2 cl1 g DSS')
    clamp3 = clamp2 + '\nDcl3 cl1 cl2 DSS'
    clamp3 = ('.model DSS D(Is=2.52n N=1.752 Rs=0.568 Cjo=4p)\nDcl1 s cl1 DSS\nDcl2 cl1 cl2 DSS\nDcl3 cl2 g DSS')
    base = dict(t_en=15e-3, comp_node='g')
    grid = [dict(Rg=680, Rk=rk, Cc=cc, Rc=rc, clamp=clamp2)
            for rk, cc, rc in itertools.product((2.2e3, 3.3e3), (2.2e-9, 3.3e-9), (0.0, 10e3))]
    for g in grid:
        pm, hp, bi = evaluate(dict(base, **g))
        print({k: v for k, v in g.items() if k != 'clamp'}, 'clamp', g['clamp'].count('Dcl'), 'diodes', flush=True)
        print(f"   minPM {pm:6.1f} | hot-plug {1000*hp:7.1f} mV | burst-after-idle {1000*bi:7.1f} mV", flush=True)
