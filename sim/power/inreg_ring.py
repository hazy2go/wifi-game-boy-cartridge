"""Time-domain cross-check of the AC stability result (LOGBOOK 08:13): reservoir held at 3.0 V (as in the AC
analysis), regulator in steady regulation, then a 10 mA console load step. An unstable loop shows growing
oscillation; the AC peak predicts its frequency (~324 kHz)."""
import os, sys
import numpy as np
import inreg as I

m = I.Vendor


def ring(p, rsrc, tag):
    tag = f'{tag}_{os.getpid()}'
    p = dict(p, step_a=0.010, step_t=2e-3)
    net = f"""* inreg ring test
{m.include}
{I.console(rsrc, p)}
Vtop vmux top DC 0
{I.core(m, p)}
Vres vres_src 0 DC 3.0
Rres vres_src vres 0.01
{I.nodeset(rsrc, p)}
.options method=gear reltol=1e-4
.tran 20n 3e-3 0 20n
.control
run
wrdata {I.OUT}/inreg_ring_{tag}.dat v(vcc) v(g)
quit
.endc
.end
"""
    I.ngspice(net, f'ring_{tag}')
    d = np.loadtxt(os.path.join(I.OUT, f'inreg_ring_{tag}.dat'))
    t, vcc, g = d[:, 0], d[:, 1], d[:, 3]
    late = t > 2.8e-3
    pp_before = np.ptp(vcc[(t > 1.8e-3) & (t < 2e-3)])
    pp_late = np.ptp(g[late])
    # dominant frequency in the last 0.8 ms of the gate voltage
    seg = g[t > 2.2e-3]; tt = t[t > 2.2e-3]
    ts = np.linspace(tt[0], tt[-1], 2 ** 14); gs = np.interp(ts, tt, seg); gs -= gs.mean()
    gs *= np.hanning(len(gs))   # window against leakage from the slow settling tail
    sp = np.abs(np.fft.rfft(gs)); fr = np.fft.rfftfreq(len(gs), ts[1] - ts[0])
    # oscillation = content above 20 kHz (the loop crosses over at 0.2-2 kHz, so slow settling after the
    # step is not ringing); returned as peak-to-peak of the strongest HF line, and its frequency
    hf = fr > 20e3
    k = sp[hf].argmax()
    return pp_before, np.ptp(vcc[late]), 8 * sp[hf][k] / len(gs), fr[hf][k]   # 8/N: p-p with Hann gain 0.5


if __name__ == '__main__':
    cases = [('design Rk2.2k Cc2.2n', {}), ('Cc 4.7n', dict(Cc=4.7e-9)), ('Rg 2k', dict(Rg=2000))]
    for name, kw in cases:
        for rsrc in (1.4, 2.2):
            a, b, c, fdom = ring(dict(I.DESIGN, gb_cap=220e-6, **kw), rsrc, 'x')
            print(f'{name:22} R={rsrc}: VCC p-p before step {1e3*a:.2f} mV, last 0.2 ms {1e3*b:.2f} mV; '
                  f'gate >20 kHz {1e3*c:.2f} mV p-p at {fdom/1e3:.0f} kHz', flush=True)
