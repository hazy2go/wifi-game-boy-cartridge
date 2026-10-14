#!/usr/bin/env python3
"""Independent reference model of the proposed power path (audit cross-check for run_sim.py).

Same circuit as run_sim.netlist_new(), re-implemented from the equations as a fixed-step
time-domain integrator (numba). It shares no code with the ngspice path except the parameter
values, so agreement between the two is evidence that neither has a netlist/solver bug.

It also adds what SPICE models idealise away:
  gating='ideal'  firmware gates TX instantly and continuously (what run_sim.py assumes)
  gating='frame'  firmware decides once at the start of each TX burst from a 1 ms ADC sample and
                  cannot abort a frame once started; RF calibration at boot is not throttleable.
Extra metric: pg_drops = how many times the radio supply fell below power-good off (radio reset).
"""
import math
import numpy as np
from numba import njit

T_END = 2.0
T_BOOT = 0.020
CAL_S = 0.300


@njit(cache=True)
def _step(x, w):
    return 0.5 * (1.0 + math.tanh(x / w))


@njit(cache=True)
def _profile(t, burst, period, t_end):
    """Returns (tx_requested, is_cal, burst_start_time) for the radio load profile of run_sim.pwl_load."""
    if t < T_BOOT:
        return 0, 0, -1.0
    cal_end = T_BOOT + CAL_S
    if t < cal_end:
        k = math.floor((t - T_BOOT - 1e-3) / 2e-3)
        if k >= 0:
            s = T_BOOT + 1e-3 + k * 2e-3
            if t < s + 1e-3 and s < cal_end:
                return 1, 1, s
        return 0, 1, -1.0
    t0 = cal_end + 5e-3
    if t < t0:
        return 0, 0, -1.0
    k = math.floor((t - t0) / period)
    s = t0 + k * period
    if s < t_end - period and t < s + burst:
        return 1, 0, s
    return 0, 0, -1.0


@njit(cache=True)
def simulate(rsrc, ilim, vdpm, gain, rsw, vsup, cres, resr, burst, period, rx, tx,
             frame, gbwatch, psave, dt, t_end, adc_dt, gb_load, logic_load, gb_cap,
             v_throttle, v_psave, gb_watch_v, modem_a, eta, cal_throttle):
    n = int(t_end / dt)
    vgb = 5.0 - rsrc * gb_load
    vc = 0.0
    pg = 0.0
    vt = 0.0
    gb_min = 10.0
    unsafe = 0.0
    want = 0.0
    got = 0.0
    up = -1.0
    res_min_after_up = 10.0
    pg_drops = 0
    pg_hi = False
    e_src = 0.0
    e_load = 0.0
    # sampled firmware state (frame mode)
    adc_t = -1.0
    adc_gb = vgb
    adc_res = 0.0
    latched_start = -2.0
    latched_go = 0
    for i in range(n):
        t = i * dt
        en = _step(vgb - vsup, 0.01)
        if vdpm > 0:
            lim = max(0.0, min(ilim, gain * (vgb - vdpm)))
        else:
            lim = ilim
        req, is_cal, bstart = _profile(t, burst, period, t_end)
        # firmware decisions
        if frame:
            if t - adc_t >= adc_dt:
                adc_t = t
                adc_gb = vgb
                adc_res = vt
            gbw_ok = 1.0 if (not gbwatch or adc_gb > gb_watch_v) else 0.0
            ps = 1.0 if (not psave or (adc_res > v_psave and gbw_ok > 0.5)) else 0.0
            if req == 1 and bstart != latched_start:
                latched_start = bstart
                if is_cal == 1 and cal_throttle == 0:
                    latched_go = 1
                else:
                    latched_go = 1 if (adc_res > v_throttle and gbw_ok > 0.5) else 0
            thr = float(latched_go) if req == 1 else 0.0
        else:
            gbw = _step(vgb - gb_watch_v, 0.01) if gbwatch else 1.0
            thr = _step(vt - v_throttle, 0.05) * gbw
            ps = _step(vt - v_psave, 0.05) * gbw if psave else 1.0
        iprof = tx if req == 1 else (rx if t >= T_BOOT + 1e-6 else 0.0)
        if psave:
            base = min(iprof, rx) * ps + modem_a * (1.0 - ps)
        else:
            base = min(iprof, rx)
        iload = pg * (base + max(0.0, iprof - rx) * thr)
        # reservoir node: solve terminal voltage (ESR) by fixed-point iteration
        isw = 0.0
        ibb = 0.0
        for _ in range(6):
            isw = en * max(0.0, min(lim, (vgb - vt) / rsw))
            ibb = 3.3 * iload / (eta * max(vt, 1.0)) * _step(vt - 1.6, 0.02)
            icap = isw - ibb - vt / 1e5
            vt = vc + resr * icap
        icap = isw - ibb - vt / 1e5
        # metrics
        if vgb < gb_min:
            gb_min = vgb
        if vgb < 4.5:
            unsafe += dt
        if t > CAL_S:
            want += max(iprof - rx, 0.0) * dt
            got += max(iload - rx, 0.0) * dt
        if pg > 0.5:
            if up < 0:
                up = t
            if not pg_hi:
                pg_hi = True
        elif pg_hi and pg < 0.5:
            pg_hi = False
            pg_drops += 1
        if up >= 0 and t > up + 1e-3 and vt < res_min_after_up:
            res_min_after_up = vt
        isrc = (5.0 - vgb) / rsrc
        e_src += 5.0 * isrc * dt
        e_load += 3.3 * iload * dt
        # integrate
        dv = ((5.0 - vgb) / rsrc - gb_load - logic_load - isw) / gb_cap
        vgb += dv * dt
        vc += icap / cres * dt
        pgx = _step(vt - (4.2 - 2.2 * pg), 0.02)
        pg += (pgx - pg) / 1e-4 * dt
    return gb_min, unsafe, (got / want if want > 0 else 0.0), up, res_min_after_up, pg_drops, e_src, e_load, vc


@njit(cache=True)
def _traffic(t, burst, period, t_end):
    """Requested TX traffic (1 = TX wanted now) and burst start time; periodic from T_BOOT+CAL_S+5ms."""
    t0 = T_BOOT + CAL_S + 5e-3
    if t < t0:
        return 0, -1.0
    k = math.floor((t - t0) / period)
    s = t0 + k * period
    if s < t_end - period and t < s + burst:
        return 1, s
    return 0, -1.0


# firmware states
OFF, BOOT, RUN, SLEEP, PARK = 0, 1, 2, 3, 4


@njit(cache=True)
def simulate_fw(rsrc, ilim, vdpm, gain, rsw, vsup, cres, resr, burst, period, rx, tx, gbwatch,
                dt, t_end, adc_dt, gb_load, logic_load, gb_cap, eta, modem_a, park_a,
                cal_ms, cal_duty, v_frame_floor, v_psave, v_park, v_unpark, gb_watch_v, v_pg_on, v_pg_off,
                vin_sleep, gb_resume_v, fw_cres=0.0):
    """Radio firmware as a state machine (all decisions from a 1 ms ADC sample, frames never aborted):
      OFF   radio unpowered (reservoir below power-good)
      BOOT  WiFi start incl. RF calibration: cal_ms of TX pulses at cal_duty, uninterruptible
      RUN   RX baseline, TX frame admitted only if the reservoir is predicted to stay above v_frame_floor
            at the end of the frame (no input current assumed) and the cart rail is above gb_watch_v
      SLEEP modem-sleep (DTIM listen, modem_a) while reservoir < v_psave or cart rail low
            (cart rail low below gb_watch_v, ok again above gb_resume_v)
      PARK  controlled shutdown (light sleep, park_a) below v_park until reservoir >= v_unpark
    An uncontrolled reset = reservoir crossing v_pg_off while powered (radio supply lost).
    vin_sleep: input stage disconnects when cart rail < reservoir + vin_sleep (BQ24075 sleep comparator)."""
    n = int(t_end / dt)
    vgb = 5.0 - rsrc * gb_load
    vc = 0.0
    vt = 0.0
    state = OFF
    boot_t = 0.0
    adc_t = -1.0
    adc_gb = vgb
    adc_res = 0.0
    latched_start = -2.0
    latched_go = 0
    gb_min = 10.0
    unsafe = 0.0
    want = 0.0
    got = 0.0
    up = -1.0
    resets = 0
    parks = 0
    res_min = 10.0
    i_tx_in = tx * 3.3 / eta  # frame power at the reservoir, W
    boot_ticks = 0
    gb_low = False
    tick_every = int(round(adc_dt / dt))
    for i in range(n):
        t = i * dt
        if i % tick_every == 0:
            adc_t = t
            adc_gb = vgb
            adc_res = vt
            # cart-rail watch with hysteresis: low below gb_watch_v, ok again above gb_resume_v
            if gbwatch:
                if adc_gb < gb_watch_v:
                    gb_low = True
                elif adc_gb > gb_resume_v:
                    gb_low = False
            # state transitions on ADC tick
            if state == OFF and adc_res >= v_pg_on:
                state = BOOT
                boot_t = t
                boot_ticks = 0
                if up < 0:
                    up = t
            elif state == BOOT:
                boot_ticks += 1
                if boot_ticks >= cal_ms:
                    state = RUN
            elif state == RUN or state == SLEEP:
                gb_ok = not gb_low
                if adc_res < v_park:
                    state = PARK
                    parks += 1
                elif adc_res < v_psave or not gb_ok:
                    state = SLEEP
                else:
                    state = RUN
            elif state == PARK and adc_res >= v_unpark:
                state = RUN
        # radio current at 3.3V
        req, bstart = _traffic(t, burst, period, t_end)
        # TX admission happens once, at the first sample of a burst, whatever the state
        if req == 1 and bstart != latched_start:
            latched_start = bstart
            # predicted end-of-frame voltage with zero input: E = P*t -> V^2 drop
            v_end2 = adc_res * adc_res - 2.0 * i_tx_in * burst / (fw_cres or cres)   # firmware's configured C (nominal), not the part's actual C
            latched_go = 1 if (state == RUN and not gb_low and v_end2 > v_frame_floor * v_frame_floor) else 0
        in_frame = req == 1 and latched_go == 1 and bstart == latched_start
        if state == OFF:
            iload = 0.0
        elif state == BOOT:
            ph = ((t - boot_t) * 1000) % 2.0
            iload = tx if ph < 2.0 * cal_duty else rx
        elif state == RUN:
            iload = rx
        elif state == SLEEP:
            iload = modem_a
        else:
            iload = park_a
        # an admitted frame always finishes, even if the state machine moved on
        if state != OFF and in_frame:
            iload = tx
        if state == OFF:
            iload = 0.0
        en = _step(vgb - vsup, 0.01)
        if vdpm > 0:
            lim = max(0.0, min(ilim, gain * (vgb - vdpm)))
        else:
            lim = ilim
        isw = 0.0
        ibb = 0.0
        for _ in range(6):
            conn = 1.0 if vgb > vt + vin_sleep else 0.0
            isw = en * conn * max(0.0, min(lim, (vgb - vt) / rsw))
            ibb = 3.3 * iload / (eta * max(vt, 1.0)) * _step(vt - 1.6, 0.02)
            icap = isw - ibb - vt / 1e5
            vt = vc + resr * icap
        icap = isw - ibb - vt / 1e5
        if state != OFF and vt < v_pg_off:
            state = OFF
            resets += 1
            latched_go = 0
        if vgb < gb_min:
            gb_min = vgb
        if vgb < 4.5:
            unsafe += dt
        if t > CAL_S:
            want += (tx - rx) * dt if req == 1 else 0.0
            got += (tx - rx) * dt if (in_frame and state != OFF) else 0.0
        if t >= T_BOOT + CAL_S + 5e-3 and vt < res_min:
            res_min = vt
        vgb += ((5.0 - vgb) / rsrc - gb_load - logic_load - isw) / gb_cap * dt
        vc += icap / cres * dt
    return gb_min, unsafe, (got / want if want > 0 else 0.0), up, res_min, resets, parks


FW_DEFAULTS = dict(cal_ms=100.0, cal_duty=0.5, v_frame_floor=3.0, v_psave=3.5, v_park=3.0, v_unpark=4.2,
                   v_pg_on=4.2, v_pg_off=2.0, park_a=0.001, vin_sleep=0.0, adc_dt=1e-3, gb_resume_v=4.48)


def run_fw(cons_r, prof, dt=2e-6, **kw):
    """Firmware state-machine model. kw: circuit params as in run(), plus FW_DEFAULTS overrides."""
    import run_sim as R
    p = dict(ilim=0.150, cres=1.41e-3, vdpm=None, rsw=0.085, gain=None, gbwatch=True, vsup=4.38, resr=0.006,
             gb_load=R.GB_LOAD_A, logic_load=0.060, gb_cap=R.GB_CAP, gb_watch_v=R.GB_WATCH_V,
             modem_a=R.MODEM_SLEEP_A, eta=0.88, rx=R.ESPC3['rx'], tx=R.ESPC3['tx'], psave=True)
    p.update(FW_DEFAULTS)
    p.update(kw)
    b, per = R.PROFILES[prof] if isinstance(prof, str) else prof
    out = simulate_fw(cons_r, p['ilim'], p['vdpm'] or 0.0, p['gain'] or R.DPM_GAIN, p['rsw'], p['vsup'], p['cres'],
                      p['resr'], b / 1000, per / 1000, p['rx'], p['tx'], p['gbwatch'], dt, T_END, p['adc_dt'],
                      p['gb_load'], p['logic_load'], p['gb_cap'], p['eta'], p['modem_a'], p['park_a'],
                      p['cal_ms'], p['cal_duty'], p['v_frame_floor'], p['v_psave'], p['v_park'], p['v_unpark'],
                      p['gb_watch_v'], p['v_pg_on'], p['v_pg_off'], p['vin_sleep'], p['gb_resume_v'], p.get('fw_cres', 0.0))
    d = dict(zip(('gb_min', 'unsafe_ms', 'tx', 'up_ms', 'res_min', 'resets', 'parks'), out))
    d['unsafe_ms'] *= 1000
    d['up_ms'] = d['up_ms'] * 1000 if d['up_ms'] >= 0 else None
    return d


def run(cons_r, prof, frame=False, dt=2e-6, adc_dt=1e-3, cal_throttle=True, **kw):
    """kw: same names as run_sim.netlist_new (ilim, cres, vdpm, psave, rsw, gain, gbwatch, vsup, resr) plus
    overrides for loads/thresholds. Returns a metrics dict."""
    import run_sim as R
    p = dict(ilim=0.150, cres=1.41e-3, vdpm=None, psave=False, rsw=0.085, gain=None, gbwatch=False,
             vsup=4.38, resr=0.006, gb_load=R.GB_LOAD_A, logic_load=0.060, gb_cap=R.GB_CAP,
             v_throttle=R.V_THROTTLE, v_psave=R.V_PSAVE, gb_watch_v=R.GB_WATCH_V,
             modem_a=R.MODEM_SLEEP_A, eta=0.88, rx=R.ESPC3['rx'], tx=R.ESPC3['tx'])
    p.update(kw)
    b, per = R.PROFILES[prof]
    out = simulate(cons_r, p['ilim'], p['vdpm'] or 0.0, p['gain'] or R.DPM_GAIN, p['rsw'], p['vsup'],
                   p['cres'], p['resr'], b / 1000, per / 1000, p['rx'], p['tx'], frame, p['gbwatch'],
                   p['psave'], dt, T_END, adc_dt, p['gb_load'], p['logic_load'], p['gb_cap'],
                   p['v_throttle'], p['v_psave'], p['gb_watch_v'], p['modem_a'], p['eta'],
                   1 if cal_throttle else 0)
    keys = ('gb_min', 'unsafe_ms', 'tx', 'up_ms', 'res_min', 'pg_drops', 'e_src', 'e_load', 'vc_end')
    d = dict(zip(keys, out))
    d['unsafe_ms'] *= 1000
    d['up_ms'] = d['up_ms'] * 1000 if d['up_ms'] >= 0 else None
    return d
