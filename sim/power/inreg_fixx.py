"""Round 5 (LOGBOOK 08:27 ff.): topology X (integrator on the cathode) is clean on stability but winds up
(burst-after-idle -164 to -227 mV). Unwind time ~ dVk * Rth * Cc / dVref, so trade Cc down and Rc up
(more proportional action) while PM/GM/ring stay within the gates."""
import itertools
import inreg as I
from inreg_search import evaluate
from inreg_vka import statics

# round 5: Cc 1n/2.2n x Rc 10k/33k/100k (out/inreg_fixx_round5.log); best Cc 1n Rc 100k, burst -29.5 mV
CANDS = list(itertools.product((470e-12, 1e-9), (220e3, 470e3, 1e6)))

if __name__ == '__main__':
    for cc, rc in CANDS:
        g = dict(comp_node='k', Rk=680, Cc=cc, Rc=rc)
        vk, gm, rg = statics(dict(I.DESIGN, **g))
        pm, hp, bi = evaluate(g)
        print(f"X Cc {cc*1e9:.1f}n Rc {rc/1e3:.0f}k | Vka {vk:.2f} V | minGM {gm:5.1f} dB | ring {1e3*rg:6.1f} mV | "
              f"minPM {pm:5.1f} | hot-plug {1000*hp:6.1f} mV | burst {1000*bi:6.1f} mV", flush=True)
