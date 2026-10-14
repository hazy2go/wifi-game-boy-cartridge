"""Is the 1.4 ohm oscillation an artifact of the behavioural TPS2553 (an ideal, instantaneous current limit)?
Same test as inreg_ring.py, but with TI's TPS2553 transient model. Its operating point does not converge,
so the run starts from an empty cartridge (uic), the switch enables at t_en = 15 ms, the reservoir is held
at 3.0 V, the power-on hold releases at ~45 ms (100 nF x 470k), the 10 mA console step is at 80 ms, and the gate
is analysed over 82-85 ms (200 ns max step, >= 20 points per cycle at 250 kHz)."""
import os
import numpy as np
import inreg as I

m = I.Vendor


def ring_vendor(p, rsrc, tag, tstep=80e-3, tend=85e-3):
    p = dict(p, step_a=0.010, step_t=tstep, tps2553='vendor')
    x = 5.0 - rsrc * (p['gb_load'] + p['logic_load'])
    net = f"""* inreg ring test, vendor TPS2553
{m.include}
{I.console(rsrc, p)}
Vtop vmux top DC 0
{I.core(m, p, ramp=True)}
Vres vres_src 0 DC 3.0
Rres vres_src vres 0.01
.ic v(vcc)={x} v(vmux)=0 v(a)=0 v(gpor)=0 v(s)=0 v(g)=0 v(k)=0 v(ref)=0 v(top)=0
{p.get('options', '.options method=trap reltol=1e-3')}
.tran 100n {tend} 0 200n uic
.control
run
wrdata {I.OUT}/inreg_ringv_{tag}.dat v(vcc) v(g) v(a) i(Vres)
quit
.endc
.end
"""
    I.ngspice(net, f'ringv_{tag}')
    d = np.loadtxt(os.path.join(I.OUT, f'inreg_ringv_{tag}.dat'))
    t = d[:, 0]
    if t[-1] < 0.999 * tend:
        raise RuntimeError('truncated')
    w = t > tstep + 2e-3
    tt, g = t[w], d[w, 3]
    ts = np.linspace(tt[0], tt[-1], 2 ** 15); gs = np.interp(ts, tt, g); gs -= gs.mean(); gs *= np.hanning(len(gs))
    sp = np.abs(np.fft.rfft(gs)); fr = np.fft.rfftfreq(len(gs), ts[1] - ts[0]); hf = fr > 20e3; k = sp[hf].argmax()
    return dict(vcc=d[w, 1].mean(), a=d[w, 5].mean(), ires=d[w, 7].mean(), ires_pp=np.ptp(d[w, 7]),
                gate_hf_pp=8 * sp[hf][k] / len(gs), f=fr[hf][k])


if __name__ == '__main__':
    for name, kw in [('design', {}), ('Rg1.5k Rk1k', dict(Rg=1500, Rk=1000))]:
        for rsrc in (1.4, 1.8):
            r = ring_vendor(dict(I.DESIGN, gb_cap=220e-6, **kw), rsrc, 'x')
            print(f"{name:12} R={rsrc}: VCC {r['vcc']:.4f} V, A {r['a']:.3f} V, I {1e3*r['ires']:.1f} mA "
                  f"(p-p {1e3*r['ires_pp']:.1f}), gate >20 kHz {1e3*r['gate_hf_pp']:.2f} mV p-p at {r['f']/1e3:.0f} kHz", flush=True)
