#!/usr/bin/env python3
"""Numeric companion to docs/design/DESIGN_CALCS.md (radio + power block).

Every value in DESIGN_CALCS.md that comes out of an equation is recomputed here, including
worst-case corners over resistor tolerance and the datasheet min/max of the IC parameter, so an
auditor can re-run the numbers instead of trusting the prose. Pure Python, no dependencies.

  python3 verify/design_calcs.py          print all results
  python3 verify/design_calcs.py --check  exit 1 if any design limit below is violated
"""
import itertools, sys

TOL = 0.01          # all resistors 1 % (UniOhm 0402WGF....TCE)
FAIL = []


def corners(*ranges):
    return itertools.product(*ranges)


def rr(r):                      # resistor tolerance band
    return (r * (1 - TOL), r * (1 + TOL))


def limit(name, ok, text):
    print(f'  [{"PASS" if ok else "FAIL"}] {name}: {text}')
    if not ok:
        FAIL.append(name)


def par(*rs):
    return 1 / sum(1 / r for r in rs)


# ---------------------------------------------------------------------------------------------
print('1. TPS2116 PR1 divider (VBUS priority threshold)  [SLVSFG1A Table 6.5 p6, 7.6.1.1 p12]')
RT, RB = 5.1e3, 2.0e3
VREF = (0.92, 1.00, 1.08)
nom = VREF[1] * (1 + RT / RB)
vals = [v * (1 + rt / rb) for v, rt, rb in corners((VREF[0], VREF[2]), rr(RT), rr(RB))]
print(f'  Vth = VREF*(1+Rt/Rb) = {nom:.3f} V nominal, {min(vals):.3f} .. {max(vals):.3f} V worst case')
limit('PR1 max threshold below USB device minimum 4.40 V', max(vals) < 4.40, f'{max(vals):.3f} V')
limit('PR1 min threshold above 3.0 V', min(vals) > 3.0, f'{min(vals):.3f} V')
ileak_to_trip = min(vals) / (RT + RB)
print(f'  VBUS pull-down when USB absent: {(RT + RB) / 1e3:.1f} kOhm; a reverse leakage of '
      f'{ileak_to_trip * 1e6:.0f} uA from D3 would be needed to float VBUS to the lowest threshold')
print(f'  load on VBUS when USB present: {5.25 / (RT + RB) * 1e3:.2f} mA at 5.25 V')

# ---------------------------------------------------------------------------------------------
print('\n2. TPS2553 ILIM  [SLVS841F Eq. 1 p15]')
R = 180.0  # kOhm


def ios(rk):
    return 22980 / rk ** 0.94, 23950 / rk ** 0.977, 25230 / rk ** 1.016   # max, nom, min (mA)


mx, nm, mn = ios(R)
mx_t = ios(R * (1 - TOL))[0]
mn_t = ios(R * (1 + TOL))[2]
print(f'  RILIM = {R:.0f}k: IOS min/nom/max = {mn:.1f} / {nm:.1f} / {mx:.1f} mA (eq. only)')
print(f'  with 1 % resistor: {mn_t:.1f} .. {mx_t:.1f} mA')
limit('RILIM inside 15k..232k', 15 <= R <= 232, f'{R}k')
limit('IOS nominal 150 mA +-5 %', abs(nm - 150) < 7.5, f'{nm:.1f} mA')

# ---------------------------------------------------------------------------------------------
print('\n3. Radio power-good: TLV3012B comparator + internal 1.242 V reference, push-pull output on VRES')
print('   [SBOS300C 6.9 p8 (VOS, VHYS, VREF), 9.4.5 p22 (POR)]  output drives TPS63802 EN [SLVSEU9D Table 8.5 p6]')
# IN+ node: RA from VRES, RB to GND, RC from OUT. OUT = 0 V while off, = VRES while on (push-pull,
# supplied from VRES, load only uA so VOH drop ~0). Both thresholds are therefore ratiometric to VRES.
RA, RB, RC = 1.3e6, 1.0e6, 1.2e6
VREFC = (1.242 * (1 - 0.015 - 0.0045), 1.242, 1.242 * (1 + 0.015 + 0.0045))  # +-1.5 % at 25 C, +-0.45 % drift 0..70 C
VOS = (-9e-3, 0, 9e-3)      # over temperature, VCM = V-; datasheet 6.9
VHYS = (2e-3, 6e-3, 8e-3)   # internal hysteresis, assumed split +-VHYS/2 around the trip point


def on_th(ra, rb, rc, vr, vos, vh):        # OUT low: RC is a pull-down
    rq = par(rb, rc)
    return (vr + vos + vh / 2) * (ra + rq) / rq


def off_th(ra, rb, rc, vr, vos, vh):       # OUT high (= VRES): RC is in parallel with RA
    rp = par(ra, rc)
    return (vr + vos - vh / 2) * (rb + rp) / rb


on_n = on_th(RA, RB, RC, VREFC[1], 0, VHYS[1])
off_n = off_th(RA, RB, RC, VREFC[1], 0, VHYS[1])
ons = [on_th(a, b, c, v, o, h) for a, b, c, v, o, h in corners(rr(RA), rr(RB), rr(RC), (VREFC[0], VREFC[2]), (VOS[0], VOS[2]), (VHYS[0], VHYS[2]))]
offs = [off_th(a, b, c, v, o, h) for a, b, c, v, o, h in corners(rr(RA), rr(RB), rr(RC), (VREFC[0], VREFC[2]), (VOS[0], VOS[2]), (VHYS[0], VHYS[2]))]
print(f'  ON  (VRES rising):  {on_n:.3f} V nominal, {min(ons):.3f} .. {max(ons):.3f} V worst case '
      f'({(min(ons) / on_n - 1) * 100:+.1f} / {(max(ons) / on_n - 1) * 100:+.1f} %)')
print(f'  OFF (VRES falling): {off_n:.3f} V nominal, {min(offs):.3f} .. {max(offs):.3f} V worst case '
      f'({(min(offs) / off_n - 1) * 100:+.1f} / {(max(offs) / off_n - 1) * 100:+.1f} %)')
limit('ON threshold 4.2 V +-5 % worst case', 4.2 * 0.95 <= min(ons) and max(ons) <= 4.2 * 1.05, f'{min(ons):.3f}..{max(ons):.3f}')
limit('OFF threshold 2.0 V +-5 % worst case', 2.0 * 0.95 <= min(offs) and max(offs) <= 2.0 * 1.05, f'{min(offs):.3f}..{max(offs):.3f}')
limit('OFF threshold above TLV3012B min supply 1.65 V (output valid until cut-off)', min(offs) > 1.65, f'{min(offs):.3f}')
limit('OFF threshold above TPS63802 UVLO rising max 1.79 V', min(offs) > 1.79, f'{min(offs):.3f}')
print(f'  divider current from VRES at 4.2 V (OUT low): {4.2 / (RA + par(RB, RC)) * 1e6:.2f} uA')
print(f'  IN+ at VRES = 5.5 V, OUT high: {5.5 * RB / (RB + par(RA, RC)):.2f} V (inside VCM range -0.2..V+ +0.2)')
print(f'  EN high level = VRES (>= {min(offs):.2f} V) vs TPS63802 VIH 1.2 V; EN low = VOL <= 0.2 V vs VIL 0.4 V')

# ---------------------------------------------------------------------------------------------
print('\n4. TPS63802 output divider  [SLVSEU9D Eq. 3 p19, Table 10-5 p20]')
RF1, RF2 = 511e3, 91e3
vo = 0.5 * (1 + RF1 / RF2)
vlo = 0.495 * (1 + RF1 * 0.99 / (RF2 * 1.01))
vhi = 0.505 * (1 + RF1 * 1.01 / (RF2 * 0.99))
ifb = 100e-9 * RF1  # worst-case IFB (100 nA) shifts VOUT by IFB*R1 (current out of the divider node)
print(f'  VOUT = 0.5*(1+R1/R2) = {vo:.3f} V; FB+-1 % and 1 % resistors: {vlo:.3f} .. {vhi:.3f} V')
print(f'  worst-case IFB 100 nA * R1 = {ifb * 1e3:.1f} mV extra (typ 5 nA -> {5e-9 * RF1 * 1e3:.2f} mV)')
limit('R2 <= 100k', RF2 <= 100e3, f'{RF2 / 1e3:.0f}k')
limit('VOUT inside ESP32-C3 3.0..3.6 V incl. IFB', 3.0 < vlo - ifb and vhi + ifb < 3.6, f'{vlo - ifb:.3f}..{vhi + ifb:.3f}')

print('\n5. TPS63802 inductor peak current  [SLVSEU9D Eq. 1-2 p18]')
for vin, iout, f in ((2.0, 0.50, 2.0e6), (2.3, 0.50, 2.1e6), (2.0, 0.35, 2.0e6)):
    D = (3.3 - vin) / 3.3
    for L in (0.47e-6 * 0.8, 0.47e-6):
        ipk = iout / (0.85 * (1 - D)) + vin * D / (2 * f * L)
        print(f'  VIN {vin} V, IOUT {iout} A, L {L * 1e6:.3f} uH: D={D:.3f}, IPEAK={ipk:.2f} A, x1.2={ipk * 1.2:.2f} A')
limit('DFE201612E-R47M Isat 4.5 A (LCSC C668312) above worst 1.2*IPEAK', 4.5 > 1.2 * (0.5 / (0.85 * (1 - 0.394)) + 2.0 * 0.394 / (2 * 2e6 * 0.376e-6)), 'see above')

# ---------------------------------------------------------------------------------------------
print('\n6. ADC dividers (ESP32-C3 ADC1, ATTEN=2: 0..1300 mV, +-10 mV)  [HW design guidelines 1.3.11]')
RT, RB, C = 680e3, 200e3, 2.2e-9
k = RB / (RT + RB)
kmin, kmax = RB * .99 / (RT * 1.01 + RB * .99), RB * 1.01 / (RT * .99 + RB * 1.01)
rth = par(RT, RB)
print(f'  ratio k = {k:.4f} ({kmin:.4f}..{kmax:.4f}); full scale 1.30 V <-> {1.3 / k:.2f} V')
print(f'  Thevenin {rth / 1e3:.1f} kOhm; IIH/IIL 50 nA max -> {50e-9 * rth * 1e3:.1f} mV at pin = {50e-9 * rth / k * 1e3:.0f} mV at input')
print(f'  ADC error +-10 mV -> +-{0.010 / k * 1e3:.0f} mV at input; ratio tolerance at 4.55 V: +-{4.55 * (kmax - k) / k * 1e3:.0f} mV (removable by one-point calibration)')
print(f'  RC time constant {rth * C * 1e3:.3f} ms (firmware samples every 1 ms)')
print(f'  firmware thresholds at pin: 4.60 V -> {4.60 * k:.3f} V, 4.55 V -> {4.55 * k:.3f}, 4.0 V -> {4.0 * k:.3f}, 3.5 V -> {3.5 * k:.3f}, 3.0 V -> {3.0 * k:.3f}')
print(f'  back-injection into an unpowered ESP pin (clamped ~0.3 V) at 5.25 V input: {(5.25 - 0.3) / RT * 1e6:.1f} uA')
print(f'  continuous drain on cart VCC: {5.0 / (RT + RB) * 1e6:.1f} uA at 5.0 V')
limit('5.5 V input stays below 1.3 V full scale', 5.5 * kmax < 1.30, f'{5.5 * kmax:.3f} V')

# ---------------------------------------------------------------------------------------------
print('\n7. ESP32-C3 CHIP_EN RC fed from TPS63802 PG  [HW design guidelines 1.3.3, MINI-1 DS Table 6-3]')
RPU, REN, CEN = 10e3, 10e3, 0.1e-6
import math
tau_c = (RPU + REN) * CEN
print(f'  release: tau = (Rpu+Ren)*C = {tau_c * 1e3:.1f} ms; EN reaches 0.75*VDD after {tau_c * math.log(4) * 1e3:.2f} ms '
      f'(>= tSTBL 50 us after PG says VOUT >= 95 %)')
print(f'  reset: tau = Ren*C = {REN * CEN * 1e3:.1f} ms; EN < 0.25*VDD after {REN * CEN * math.log(4) * 1e3:.2f} ms (tRST >= 50 us)')

print('\n8. Reservoir energy 4.2 V -> 2.0 V  (3x 330 uF 6TPE330MAP + 2x 10 uF)')
for c in (3 * 330e-6 * 0.8 + 20e-6 * 0.5, 3 * 330e-6 + 20e-6, 3 * 330e-6 * 1.2 + 20e-6):
    print(f'  C = {c * 1e6:.0f} uF: E = {0.5 * c * (4.2 ** 2 - 2.0 ** 2) * 1e3:.2f} mJ')

print('\n9. CH334P crystal load  [CH334 DS V2.91 6.1 p26: XI/XO internal ~16 pF each]')
cl = 16 * 16 / 32 + 2.0
print(f'  effective load = 16 pF || series 16 pF + ~2 pF stray = {cl:.1f} pF vs crystal CL 10 pF (C668845)')

print('\nRESULT:', 'FAIL ' + ', '.join(FAIL) if FAIL else 'all limits PASS')
if '--check' in sys.argv and FAIL:
    sys.exit(1)
