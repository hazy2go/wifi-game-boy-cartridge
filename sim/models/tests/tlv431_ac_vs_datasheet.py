#!/usr/bin/env python3
"""Does the TI TLV431 macro-model reproduce the datasheet's small-signal gain/phase (SLVS139Z Figure 5-14)?
The regulator loop now crosses over at 0.4-54 kHz, so the model has to be right up to ~100 kHz.
Test circuit exactly as Figure 5-14: 5 V -> 180 ohm -> cathode (output), 6.8k cathode->REF, 4.3k REF->GND,
signal into REF through 10 uF, IK ~10 mA. Datasheet values read off the figure (+-2 dB, +-10 deg).
The gain is V(cathode)/V(REF): the 6.8k feedback makes the REF node itself small at low frequency (a first
version divided by the source instead and got a meaningless low-frequency slope)."""
import os, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), 'ti_tlv431', 'TLV431.lib')
OUT = os.path.join(HERE, 'tlv431_ac.out')
net = f"""* TLV431 datasheet Figure 5-14
.include {LIB}
V5 vdd 0 5
R180 vdd k 180
R68 k ref 6.8k
R43 ref 0 4.3k
Vs src 0 DC 0 AC 1
C10 src ref 10u
XU 0 k ref TLV431
.control
op
print v(k) (v(vdd)-v(k))/180
ac dec 50 100 1e6
let A = v(k)/v(ref)
wrdata {OUT} mag(A) ph(A)
quit
.endc
.end
"""
open(os.path.join(HERE, 'tlv431_ac.cir'), 'w').write(net)
r = subprocess.run(['ngspice', '-b', os.path.join(HERE, 'tlv431_ac.cir')], capture_output=True, text=True, cwd=HERE)
print(r.stdout[-600:])
d = np.loadtxt(OUT)
f, db = d[:, 0], 20 * np.log10(d[:, 1])
ph = np.degrees(np.unwrap(d[:, 3]))
# datasheet (read off Figure 5-14): gain dB, phase shift deg
DS = {1e2: (59, 3), 1e3: (57, 45), 1e4: (40, 83), 1e5: (20, 94), 1e6: (-1, 119)}
fails = 0
for fx, (g_ds, p_ds) in DS.items():
    g = np.interp(np.log10(fx), np.log10(f), db)
    p = np.interp(np.log10(fx), np.log10(f), np.abs(ph - (180 if ph[0] > 90 else (-180 if ph[0] < -90 else 0))))
    ok = abs(g - g_ds) <= 3.5 and abs(p - p_ds) <= 10   # figure reading +-2 dB / +-5 deg, plus model spread
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {fx/1e3:7.0f} kHz: model {g:6.1f} dB, shift {p:5.1f} deg | datasheet {g_ds} dB, {p_ds} deg")
off = np.mean([np.interp(np.log10(fx), np.log10(f), db) - g for fx, (g, _) in DS.items()])
print(f'mean gain offset model - datasheet: {off:+.1f} dB (negative = the model has LESS gain than the typical part;'
      ' loop-gain margins from this model must keep that much in reserve)')
sys.exit(1 if fails else 0)
