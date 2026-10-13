"""Search with the TLV431 kept inside its datasheet range (LOGBOOK 08:13): cathode >= Vref + 0.2 V at every
regulating operating point, gain margin over every -180 deg crossing (200 points/decade), a time-domain ring
test (gate must not oscillate after a 10 mA load step, inreg_ring.py), then phase margin, hot-plug and
burst-after-idle margins.

Round 1 (Rg 680, Rk 470-1k, Cc 2.2/4.7n, out/inreg_vka_round1.log): Cc 2.2n keeps a 324 kHz loop-gain peak
(GM -15.9 dB); 4.7n removes it in AC but burst-after-idle fails by 26-52 mV. The ring test then showed every
round-1 candidate oscillating at 230-370 kHz at 1.4 ohm, including those with a clean AC result, so the ring
test is a separate gate. Only Rg = Rk = 4.7k and a gate-source capacitor of 47 nF stopped it in a quick scan.
"""
import itertools, sys
import inreg as I
from inreg_search import evaluate, m
from inreg_ring import ring

RS = (1.4, 1.6, 1.8, 2.2, 2.5)


def statics(p):
    vk, gms, rings = [], [], []
    for rsrc in RS:
        vk.append(I.dc_point(m, p, rsrc, 3.0)[3])
        for cap in (47e-6, 220e-6):
            gm = I.loop_gain(m, dict(p, gb_cap=cap), rsrc, 3.0)[5]
            gms.append(gm if gm is not None else 99)
            rings.append(ring(dict(p, gb_cap=cap), rsrc, f'v{rsrc}')[2])
    return min(vk), min(gms), max(rings)


if __name__ == '__main__':
    grid = [dict(Rg=rg, Rk=rk, Cc=cc, Cg=cg) for rg, rk, cc, cg in itertools.product(
        (1500, 2200), (1000, 2200), (2.2e-9, 3.3e-9), (10e-9, 22e-9, 47e-9))]
    for g in grid:
        p = dict(I.DESIGN, **g)
        vk, gm, rg = statics(p)
        head = f"{g} Vka {vk:.2f} V | minGM {gm:5.1f} dB | gate ring >20kHz {1e3*rg:6.1f} mV p-p"
        if vk < 1.44 or gm < 10 or rg > 5e-3:
            print(head, '| skip', flush=True)
            continue
        pm, hp, bi = evaluate(g)
        print(head, f'| minPM {pm:5.1f} | hot-plug {1000*hp:6.1f} mV | burst {1000*bi:6.1f} mV', flush=True)
