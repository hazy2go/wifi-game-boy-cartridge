#!/usr/bin/env python3
"""Power-path simulation: original Staacks design vs the proposed current-limited reservoir.

Generates ngspice netlists, runs them in batch mode, prints a results table and writes plots.
Usage: python run_sim.py            (needs ngspice on PATH, numpy + matplotlib)

Models are behavioural on purpose (see sim/power/README.md for every assumption and its source).
"""
import os, subprocess, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
os.makedirs(OUT, exist_ok=True)

# ---- Game Boy 5V rail (Thevenin model of the DMG DC/DC converter + batteries) ----
CONSOLES = {
    'fresh': dict(rsrc=0.7),   # assumption, see README
    'tired': dict(rsrc=1.2),   # in between
    'weak': dict(rsrc=2.0),    # fits issue #2: ~1A inrush dragged 5V to ~3V
}
GB_LOAD_A = 0.100      # console's own draw (Staacks: "a bit over 100mA at 5V")
GB_CAP = 100e-6        # bulk capacitance on the console rail (assumption)
MODEM_SLEEP_A = 0.025  # ESP32-C3 modem-sleep average with DTIM listen (assumption, see README)
V_THROTTLE = 4.0  # firmware stops starting TX bursts below this reservoir voltage
V_PSAVE = 3.5     # firmware drops to modem-sleep below this
GB_WATCH_V = 4.55  # firmware backs the radio off when the cart rail is below this
DPM_GAIN = 2.0     # A/V foldback slope of the input-voltage loop
GB_MIN_OK = 4.5        # 74xx/LR35902 5V logic: treat < 4.5V as unsafe

# ---- radio load profiles at 3.3V (A vs time) ----
ESP8266 = dict(rx=0.070, tx=0.170, name='ESP8266')
ESPC3 = dict(rx=0.084, tx=0.335, name='ESP32-C3')

PROFILES = {
    # name: (burst_ms, period_ms) of TX on top of RX baseline, after a 300 ms RF-cal phase
    'traffic_2ms_20pct': (2, 10),
    'worst_11b_12ms': (12, 50),
    'stress_50pct': (2, 4),
}
T_END = 2.0
T_BOOT = 0.020   # radio enabled 20 ms after power-on
CAL_MS = 300     # boot RF calibration: back-to-back 1 ms TX pulses at 50%


def pwl_load(chip, burst_ms, period_ms):
    """Piecewise-linear current source for the radio (A)."""
    pts = [(0, 0), (T_BOOT, 0), (T_BOOT + 1e-6, chip['rx'])]
    t = T_BOOT + 1e-3
    edge = 5e-6
    cal_end = T_BOOT + CAL_MS / 1000
    while t < cal_end:  # calibration
        pts += [(t, chip['rx']), (t + edge, chip['tx']), (t + 1e-3, chip['tx']), (t + 1e-3 + edge, chip['rx'])]
        t += 2e-3
    t = cal_end + 5e-3
    while t < T_END - period_ms / 1000:
        b = burst_ms / 1000
        pts += [(t, chip['rx']), (t + edge, chip['tx']), (t + b, chip['tx']), (t + b + edge, chip['rx'])]
        t += period_ms / 1000
    return ' '.join(f'{a:.6f} {b:.4f}' for a, b in pts)


def netlist_old(chip, prof, cons):
    """Original: GB 5V -> NCP1117 (linear) -> 3.3V with 100uF + 680uF tantalum -> ESP."""
    b, p = PROFILES[prof]
    return f"""* original design {chip['name']} {prof} {cons}
Vgb src 0 5.0
Rsrc src gb {CONSOLES[cons]['rsrc']}
Cgb gb 0 {GB_CAP}
Igb gb 0 {GB_LOAD_A}
* NCP1117: output tracks min(3.3, Vin-1.1), current limit 1A, 5mA ground current. Input current = output current.
Bldo gb v33 I = max(0, min(1.0, 20*(min(3.3, V(gb)-1.1) - V(v33)))) + 0.005
C680 v33 c680n 680u
Resr680 c680n 0 0.4
C100 v33 c100n 100u
Resr100 c100n 0 1.4
C10 v33 0 10u
Iesp v33 0 PWL({pwl_load(chip, b, p)})
.ic v(gb)={5.0 - CONSOLES[cons]['rsrc'] * GB_LOAD_A} v(v33)=0
.options method=gear reltol=1e-3
.tran 20u {T_END} 0 20u uic
.control
run
wrdata {OUT}/old_{chip['name']}_{prof}_{cons}.dat v(gb) v(v33)
quit
.endc
.end
"""


def netlist_new(chip, prof, cons, ilim=0.150, cres=1.41e-3, vdpm=None, psave=False, rsw=0.085, gain=None, gbwatch=False, vsup=4.38, resr=0.006):
    """Proposed: GB 5V -> current-limited switch (TPS2553 class, optional input-voltage regulation
    'DPM' loop) -> polymer reservoir -> TPS63802 buck-boost -> 3.3V radio.
    Croco logic (RP2350+HyperRAM via AP2112) sits on the raw cartridge rail.
    Radio is only enabled once the reservoir is charged (power-good, 4.2V on / 2.0V off) and the
    firmware skips TX bursts while the reservoir is below 3.0V (throttle)."""
    b, p = PROFILES[prof]
    v0 = 5.0 - CONSOLES[cons]['rsrc'] * GB_LOAD_A
    # firmware rail watch: the radio MCU reads the cart rail (ADC) and backs off below GB_WATCH_V
    gbw = f' * 0.5*(1+tanh((V(gb)-{GB_WATCH_V})/0.01))' if gbwatch else ''
    # power-save: below 3.0V the firmware drops from continuous RX to modem-sleep (DTIM) average
    base = f"(min(V(iprof), {chip['rx']})*V(ps) + {MODEM_SLEEP_A}*(1-V(ps)))" if psave else f"min(V(iprof), {chip['rx']})"
    lim = f'max(0, min({ilim}, {gain or DPM_GAIN}*(V(gb)-{vdpm})))' if vdpm else f'{ilim}'
    return f"""* proposed design {chip['name']} {prof} {cons} ilim={ilim} vdpm={vdpm}
Vgb src 0 5.0
Rsrc src gb {CONSOLES[cons]['rsrc']}
Cgb gb 0 {GB_CAP}
Igb gb 0 {GB_LOAD_A}
* Croco logic side (RP2350 + HyperRAM + flash + level shifter), steady (assumption 60 mA)
Ilogic gb 0 0.060
* Supervisor gating the switch (APX803-44 4.38V, or precision TLV431 comparator for the final design), smooth step for convergence
Bsup en 0 V = 0.5*(1+tanh((V(gb)-{vsup})/0.01))
Ren en 0 1meg
* current-limited switch: 85 mOhm, constant-current limit, no reverse current, optional DPM foldback
Bsw gb res I = V(en) * max(0, min({lim}, (V(gb)-V(res))/{rsw}))
Cres res cresn {cres}
Resr cresn 0 {resr}
Rbleed res 0 100k
* radio power-good with hysteresis (on at 4.2V, off at 2.0V)
Bpgx pgx 0 V = 0.5*(1+tanh((V(res)-(4.2-2.2*V(pg)))/0.02))
Rpg pgx pg 1k
Cpg pg 0 100n
* firmware throttle: TX bursts skipped while reservoir < V_THROTTLE; modem-sleep below V_PSAVE
Bthr thr 0 V = 0.5*(1+tanh((V(res)-{V_THROTTLE})/0.05)){gbw}
Bps ps 0 V = 0.5*(1+tanh((V(res)-{V_PSAVE})/0.05)){gbw}
Rps ps 0 1meg
Rthr thr 0 1meg
* radio current (1 V = 1 A): RX baseline + throttled TX bursts, only when power-good
Viprof iprof 0 PWL({pwl_load(chip, b, p)})
Bil iload 0 V = V(pg) * ({base} + max(0, V(iprof)-{chip['rx']}) * V(thr))
Ril iload 0 1meg
* TPS63802 buck-boost: constant input power = Pout/eta, off below 1.6V
Bbb res 0 I = 3.3*V(iload)/(0.88*max(V(res),1.0)) * 0.5*(1+tanh((V(res)-1.6)/0.02))
.ic v(gb)={v0} v(res)=0 v(pg)=0
.options method=gear reltol=1e-3
.tran 20u {T_END} 0 20u uic
.control
run
wrdata {OUT}/{{TAG}}.dat v(gb) v(res) v(pg) v(thr) v(iprof) v(iload)
quit
.endc
.end
"""


def run(net, tag):
    path = os.path.join(OUT, tag + '.cir')
    open(path, 'w').write(net)
    r = subprocess.run(['ngspice', '-b', path], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit(f'ngspice failed on {tag}')


def load(path, n):
    d = np.loadtxt(path)
    return [d[:, 0]] + [d[:, 2 * i + 1] for i in range(n)]


VARIANTS = [  # (tag suffix, label, kwargs)
    ('lim150', 'proposed: 150mA limit only', dict(ilim=0.150)),
    # chosen: TPS2553 150mA in front of BQ24075 USB500 (VIN-DPM 4.35/4.5/4.63V) + firmware rail watch
    ('final', 'TPS2553 150mA + BQ24075 DPM 4.5V typ + fw', dict(ilim=0.150, vdpm=4.5, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45)),
    ('final_lo', 'same, DPM at 4.35V (min)', dict(ilim=0.150, vdpm=4.35, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45)),
    # low-profile reservoir (shell height): 6TPE330MAP 330uF 25mOhm 1.8mm, n in parallel
    ('c660', 'final, 2x 330uF 1.8mm', dict(ilim=0.150, vdpm=4.5, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45, cres=0.66e-3, resr=0.0125)),
    ('c990', 'final, 3x 330uF 1.8mm', dict(ilim=0.150, vdpm=4.5, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45, cres=0.99e-3, resr=0.0083)),
    ('c1320', 'final, 4x 330uF 1.8mm', dict(ilim=0.150, vdpm=4.5, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45, cres=1.32e-3, resr=0.00625)),
    ('final_hi', 'same, DPM at 4.63V (max)', dict(ilim=0.150, vdpm=4.63, psave=True, rsw=0.285, gain=5, gbwatch=True, vsup=4.45)),
]

def main():
    rows = []
    for cons in CONSOLES:
        for prof in PROFILES:
            for chip in (ESP8266, ESPC3):
                tag = f"old_{chip['name']}_{prof}_{cons}"
                run(netlist_old(chip, prof, cons), tag)
                t, gb, v33 = load(os.path.join(OUT, tag + '.dat'), 2)
                dt = np.diff(t)
                rows.append(dict(design='original', chip=chip['name'], prof=prof, cons=cons,
                                 gb_min=gb.min(), unsafe=1000 * np.sum(dt[gb[:-1] < GB_MIN_OK]),
                                 rail_min=v33[t > 0.1].min(), tx=1.0, radio_up_ms=None))
            for suf, lab, kw in VARIANTS:
                chip = ESPC3
                tag = f"new_{chip['name']}_{prof}_{cons}_{suf}"
                run(netlist_new(chip, prof, cons, **kw).replace('{TAG}', tag), tag)
                t, gb, res, pg, thr, ipr, il = load(os.path.join(OUT, tag + '.dat'), 6)
                dt = np.diff(t)
                want = np.sum((np.maximum(ipr - chip['rx'], 0) * dt.tolist().__add__([0]))[t > CAL_MS / 1000])
                got = np.sum((np.maximum(il - chip['rx'], 0) * dt.tolist().__add__([0]))[t > CAL_MS / 1000])
                up = t[np.argmax(pg > 0.5)] * 1000 if (pg > 0.5).any() else None
                rows.append(dict(design=suf, chip=chip['name'], prof=prof, cons=cons,
                                 gb_min=gb.min(), unsafe=1000 * np.sum(dt[gb[:-1] < GB_MIN_OK]),
                                 rail_min=res[t > (up or 0) / 1000 + 0.001].min() if up else res.min(),
                                 tx=got / want if want else 0, radio_up_ms=up))

    hdr = f"{'design':9} {'chip':9} {'profile':18} {'cons':5} {'GBmin':>6} {'GB<4.5V ms':>10} {'rail/res min':>12} {'TX delivered':>12} {'radio up ms':>11}"
    lines = [hdr]
    for r in rows:
        up = f"{r['radio_up_ms']:.0f}" if r['radio_up_ms'] else '-'
        lines.append(f"{r['design']:9} {r['chip']:9} {r['prof']:18} {r['cons']:5} {r['gb_min']:6.2f} {r['unsafe']:10.1f} "
                     f"{r['rail_min']:12.2f} {100 * r['tx']:11.0f}% {up:>11}")
    open(os.path.join(OUT, 'results.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))

    for prof in PROFILES:
        fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        for tag, lab in [(f'old_ESP8266_{prof}_tired', 'original + ESP8266'), (f'old_ESP32-C3_{prof}_tired', 'original + ESP32-C3')] + \
                [(f'new_ESP32-C3_{prof}_tired_{suf}', lab + ' + ESP32-C3') for suf, lab, _ in VARIANTS]:
            d = np.loadtxt(os.path.join(OUT, tag + '.dat'))
            ax[0].plot(d[:, 0] * 1000, d[:, 1], lw=0.8, label=lab)
            ax[1].plot(d[:, 0] * 1000, d[:, 3], lw=0.8, label=lab)
        ax[0].axhline(GB_MIN_OK, color='r', ls='--', lw=0.8)
        ax[0].set_ylabel('Game Boy 5V rail (V)')
        ax[1].set_ylabel('orig: 3.3V rail / new: reservoir (V)')
        ax[1].set_xlabel('ms after hot-plug (tired batteries, 1.2 ohm)')
        ax[0].legend(fontsize=7, loc='lower right')
        ax[0].set_title(f'Power path, profile {prof}, tired batteries')
        fig.tight_layout()
        fig.savefig(os.path.join(HERE, f'plot_{prof}.png'), dpi=110)
    print('plots written')


if __name__ == '__main__':
    main()
