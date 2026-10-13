"""Clamp-voltage trade-off: charge current capability vs wind-up (LOGBOOK 06:20)."""
import itertools
import inreg as I
from inreg_search import evaluate, m

DSS = '.model DSS D(Is=2.52n N=1.752 Rs=0.568 Cjo=4p)\n'
CLAMPS = {
    '2 diodes': DSS + 'Dcl1 s cl1 DSS\nDcl2 cl1 g DSS',
    'diode + 470R': DSS + 'Dcl1 s cl1 DSS\nRcl cl1 g 470',
    'diode + 820R': DSS + 'Dcl1 s cl1 DSS\nRcl cl1 g 820',
    '2 diodes + 220R': DSS + 'Dcl1 s cl1 DSS\nDcl2 cl1 cl2 DSS\nRcl cl2 g 220',
    '3 diodes': DSS + 'Dcl1 s cl1 DSS\nDcl2 cl1 cl2 DSS\nDcl3 cl2 g DSS',
}
for name, cl in CLAMPS.items():
    p = dict(I.DESIGN, clamp=cl)
    i_fresh = abs(I.dc_point(m, p, 0.7, 3.0)[1])
    i_14 = abs(I.dc_point(m, p, 1.4, 3.0)[1])
    v_14 = I.dc_point(m, p, 1.4, 3.0)[0]
    pm, hp, bi = evaluate(p)
    print(f"{name:16} | I@0.7ohm {1000*i_fresh:5.1f} mA, @1.4ohm {1000*i_14:5.1f} mA (VCC {v_14:.3f}) | minPM {pm:5.1f} | hot-plug {1000*hp:6.1f} mV | burst {1000*bi:6.1f} mV", flush=True)
