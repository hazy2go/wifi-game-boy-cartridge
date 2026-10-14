"""Round 4 (LOGBOOK 08:27): the two structural fixes for the 250-320 kHz gate oscillation, through every gate.
X: integrator capacitor back on the TLV431 cathode (classic TL431 Type-II position), gate clamp kept.
Y: integrator stays on the gate, plus a gate-source capacitor Cg; Rk = Rg so the cathode stays in range."""
import inreg as I
from inreg_search import evaluate
from inreg_vka import statics

grid = [('X comp k 10n Rk680', dict(comp_node='k', Cc=10e-9, Rk=680)),
        ('X comp k 4.7n Rk680', dict(comp_node='k', Cc=4.7e-9, Rk=680)),
        ('Y Cc2.2n Cg47n Rk680', dict(Rk=680, Cg=47e-9)),
        ('Y Cc2.2n Cg100n Rk680', dict(Rk=680, Cg=100e-9)),
        ('Y Cc3.3n Cg100n Rk680', dict(Rk=680, Cg=100e-9, Cc=3.3e-9))]
if __name__ == '__main__':
    for name, g in grid:
        vk, gm, rg = statics(dict(I.DESIGN, **g))
        pm, hp, bi = evaluate(g)
        print(f"{name:24} Vka {vk:.2f} V | minGM {gm:5.1f} dB | ring {1e3*rg:6.1f} mV | minPM {pm:5.1f} | "
              f"hot-plug {1000*hp:6.1f} mV | burst {1000*bi:6.1f} mV", flush=True)
