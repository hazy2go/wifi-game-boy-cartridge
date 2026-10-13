"""Firmware cart-rail watch thresholds vs the new regulator (LOGBOOK 08:55). The 4.55/4.60 V watch was set for the
BQ24075 (DPM 4.35-4.63 V). The TLV431B regulator holds the rail at 4.542-4.634 V, so a 4.55 V watch puts the
radio to sleep whenever the regulator is working at its low corner. Sweep sleep/resume pairs, report safety
(time below 4.5 V, radio resets) and busy-traffic TX airtime at the worst corners."""
import itertools
import pyref, run_sim as R

CHOSEN = dict(ilim=0.150, vdpm=4.588, psave=True, rsw=0.3, gain=15, gbwatch=True, vsup=0.0, cres=0.99e-3, resr=0.0083)
FW = dict(CHOSEN, tx=0.350); FW.pop('psave')
DPM = (4.542, 4.588, 4.634)
PAIRS = [(4.55, 4.60), (4.52, 4.535), (4.51, 4.53), (4.50, 4.52)]
for sleep_v, resume_v in PAIRS:
    worst_unsafe, worst_rst, tx = 0, 0, {}
    for cons in R.CONSOLES:
        for (tn, tp), ilim, vdpm, logic in itertools.product({'busy': (2, 10), 'stress': (2, 4)}.items(), (0.129, 0.174), DPM, (0.04, 0.10)):
            p = pyref.run_fw(R.CONSOLES[cons]['rsrc'], tp, cal_ms=5, cal_duty=0.5,
                             **dict(FW, ilim=ilim, vdpm=vdpm, logic_load=logic, eta=0.80, rsw=0.6, gb_watch_v=sleep_v, gb_resume_v=resume_v))
            worst_unsafe = max(worst_unsafe, p['unsafe_ms']); worst_rst = max(worst_rst, p['resets'])
            if tn == 'busy':
                tx.setdefault(cons, []).append(p['tx'])
    print(f"watch sleep {sleep_v} / resume {resume_v}: worst <4.5 V {worst_unsafe:.0f} ms, resets {worst_rst}, busy TX min "
          + ', '.join(f"{c} {100*min(v):.0f}%" for c, v in tx.items()), flush=True)
