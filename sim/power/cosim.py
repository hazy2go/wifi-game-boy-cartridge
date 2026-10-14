#!/usr/bin/env python3
"""Co-simulation: the real C power manager (firmware/esp32c3/components/power_mgr) driving the
power-path plant. The C file is compiled for the host and called through ctypes once per 1 ms ADC
tick; the plant (numba) integrates the analog circuit between ticks.

This is the second, independent implementation of the firmware spec (the first is
pyref.simulate_fw). Audit A6 requires both to agree.
"""
import ctypes, math, os, subprocess
import numpy as np
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, 'firmware', 'esp32c3', 'components', 'power_mgr')
LIB = os.path.join(HERE, 'out', 'libpower_mgr.so')
T_BOOT, CAL_S, T_END = 0.020, 0.300, 2.0


class Cfg(ctypes.Structure):
    _fields_ = [('v_start', ctypes.c_float), ('v_frame_floor', ctypes.c_float), ('v_psave', ctypes.c_float),
                ('v_park', ctypes.c_float), ('v_unpark', ctypes.c_float), ('gb_watch_v', ctypes.c_float),
                ('gb_resume_v', ctypes.c_float), ('c_res', ctypes.c_float), ('boot_ms', ctypes.c_uint32)]


class PM(ctypes.Structure):
    _fields_ = [('cfg', Cfg), ('state', ctypes.c_int), ('boot_elapsed_ms', ctypes.c_uint32),
                ('v_res', ctypes.c_float), ('v_gb', ctypes.c_float), ('gb_low', ctypes.c_bool), ('parks', ctypes.c_uint32),
                ('frames_admitted', ctypes.c_uint32), ('frames_refused', ctypes.c_uint32)]


def build():
    src = os.path.join(SRC, 'power_mgr.c')
    if not os.path.exists(LIB) or os.path.getmtime(LIB) < os.path.getmtime(src):
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-std=c11', '-shared', '-fPIC',
                        '-o', LIB, src], check=True)
    lib = ctypes.CDLL(LIB)
    lib.pm_tick_1ms.argtypes = [ctypes.POINTER(PM), ctypes.c_float, ctypes.c_float]
    lib.pm_admit_tx.argtypes = [ctypes.POINTER(PM), ctypes.c_float, ctypes.c_float]
    lib.pm_admit_tx.restype = ctypes.c_bool
    lib.pm_init.argtypes = [ctypes.POINTER(PM), ctypes.POINTER(Cfg)]
    lib.pm_default_config.argtypes = [ctypes.POINTER(Cfg)]
    return lib


@njit(cache=True)
def _st(x, w):
    return 0.5 * (1.0 + math.tanh(x / w))


@njit(cache=True)
def plant_slice(st, seg_t, seg_i, nseg, powered, dt, n, rsrc, ilim, vdpm, gain, rsw, vsup, cres, resr,
                eta, gb_load, logic_load, gb_cap, v_pg_off):
    """Integrate 1 ms. st = [vgb, vc, vt, gb_min, unsafe_s]. Load is piecewise constant: seg_i[k] from
    seg_t[k] (s, relative to slice start). Returns (min vt, reset flag)."""
    vgb, vc, vt = st[0], st[1], st[2]
    vmin = 10.0
    reset = 0
    k = 0
    for j in range(n):
        tr = j * dt
        while k + 1 < nseg and tr >= seg_t[k + 1]:
            k += 1
        iload = seg_i[k] if (powered and reset == 0) else 0.0
        en = _st(vgb - vsup, 0.01)
        lim = max(0.0, min(ilim, gain * (vgb - vdpm))) if vdpm > 0 else ilim
        isw = 0.0
        ibb = 0.0
        for _ in range(6):
            isw = en * max(0.0, min(lim, (vgb - vt) / rsw))
            ibb = 3.3 * iload / (eta * max(vt, 1.0)) * _st(vt - 1.6, 0.02)
            vt = vc + resr * (isw - ibb - vt / 1e5)
        icap = isw - ibb - vt / 1e5
        if powered and reset == 0 and vt < v_pg_off:
            reset = 1
        if vt < vmin:
            vmin = vt
        if vgb < st[3]:
            st[3] = vgb
        if vgb < 4.5:
            st[4] += dt
        vgb += ((5.0 - vgb) / rsrc - gb_load - logic_load - isw) / gb_cap * dt
        vc += icap / cres * dt
    st[0], st[1], st[2] = vgb, vc, vt
    return vmin, reset


def run(cons_r, burst_ms, period_ms, *, ilim=0.150, vdpm=4.5, gain=5, rsw=0.285, vsup=4.45, cres=0.99e-3,
        resr=0.0083, eta=0.88, gb_load=0.100, logic_load=0.060, gb_cap=100e-6, rx=0.084, tx=0.350,
        modem_a=0.025, park_a=0.001, cal_ms=100, cal_duty=0.5, v_pg_off=2.0, dt=2e-6, lib=None, fw_cres=0.0, **cfg_over):
    lib = lib or build()
    cfg = Cfg()
    lib.pm_default_config(ctypes.byref(cfg))
    cfg.c_res = fw_cres or cres   # what the firmware is configured with (nominal), may differ from the part
    cfg.boot_ms = int(cal_ms)
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    pm = PM()
    lib.pm_init(ctypes.byref(pm), ctypes.byref(cfg))
    st = np.array([5.0 - cons_r * gb_load, 0.0, 0.0, 10.0, 0.0])
    n = int(round(1e-3 / dt))
    t0 = T_BOOT + CAL_S + 5e-3
    p_frame = tx * 3.3 / eta
    frame_end = -1.0  # absolute end time of the admitted frame in flight
    boot_start = None
    want = got = 0.0
    up = None
    resets = 0
    res_min = 10.0
    parks_total = 0
    seg_t = np.zeros(4)
    seg_i = np.zeros(4)
    for ms in range(int(round(T_END * 1000))):
        t = ms / 1000.0
        lib.pm_tick_1ms(ctypes.byref(pm), ctypes.c_float(st[2]), ctypes.c_float(st[0]))
        s = pm.state
        if s == 1 and boot_start is None:
            boot_start = t
            if up is None:
                up = t * 1000
        if s != 1:
            boot_start = None
        # TX request starting this ms?
        k = round((t - t0) * 1000) / period_ms if t >= t0 - 1e-9 else -1
        starts = t >= t0 - 1e-9 and abs(k - round(k)) < 1e-9 and t0 + round(k) * period_ms / 1000 < T_END - period_ms / 1000
        if starts:
            if t > CAL_S:
                want += (tx - rx) * burst_ms / 1000
            if lib.pm_admit_tx(ctypes.byref(pm), ctypes.c_float(burst_ms / 1000), ctypes.c_float(p_frame)):
                frame_end = t + burst_ms / 1000
                if t > CAL_S:
                    got += (tx - rx) * burst_ms / 1000
        base = {0: 0.0, 1: None, 2: rx, 3: modem_a, 4: park_a}[s]
        nseg = 1
        if s == 1:  # calibration: TX for the first 2*duty ms of every 2 ms
            ph = ((t - boot_start) * 1000) % 2.0
            hi = 2.0 * cal_duty
            if ph < hi:
                seg_t[0], seg_i[0] = 0.0, tx
                if hi - ph < 1.0:
                    seg_t[1], seg_i[1], nseg = (hi - ph) / 1000, rx, 2
            else:
                seg_t[0], seg_i[0] = 0.0, rx
                if 2.0 - ph < 1.0:
                    seg_t[1], seg_i[1], nseg = (2.0 - ph) / 1000, tx, 2
        elif frame_end > t:  # admitted frame in flight (never aborted)
            seg_t[0], seg_i[0] = 0.0, tx
            if frame_end < t + 1e-3 - 1e-9:
                seg_t[1], seg_i[1], nseg = frame_end - t, base, 2
        else:
            seg_t[0], seg_i[0] = 0.0, base
        powered = s != 0
        vmin, reset = plant_slice(st, seg_t, seg_i, nseg, powered, dt, n, cons_r, ilim, vdpm, gain, rsw, vsup,
                                  cres, resr, eta, gb_load, logic_load, gb_cap, v_pg_off)
        if t >= t0 - 1e-9:
            res_min = min(res_min, vmin)
        if reset:
            resets += 1
            if frame_end > t + 1e-3 and t > CAL_S:
                got -= (tx - rx) * (frame_end - t - 1e-3)  # rest of the frame never happened
            frame_end = -1.0
            parks_total += pm.parks
            lib.pm_init(ctypes.byref(pm), ctypes.byref(cfg))
    return dict(gb_min=st[3], unsafe_ms=st[4] * 1000, tx=got / want if want else 0.0, up_ms=up,
                res_min=res_min, resets=resets, parks=parks_total + pm.parks)
