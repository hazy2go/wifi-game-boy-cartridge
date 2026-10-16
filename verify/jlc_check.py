#!/usr/bin/env python3
"""JLCPCB manufacturability gate: KiCad's DRC on a copy of the board with JLC's 6-layer limits as the rules.

Limits (jlcpcb.com/capabilities/pcb-capabilities, 6-layer, 1 oz, checked 2026-10-16): trace/space 0.09 mm, via hole
0.15 mm / diameter 0.25 mm, copper to routed edge 0.2 mm, via-via hole spacing 0.2 mm, silkscreen text 1.0 mm.

PASS = no copper, hole or edge error anywhere except inside three footprints taken unchanged from Croco V2.1 (built
and working), each checked by hand on 2026-10-16:
  J5  edge connector: one copper finger per pin, run to the edge on purpose (gold fingers), mask open on purpose;
  SW1 printed button: two separate copper combs (BUTTON, GND), no copper joins them;
  J1  USB-C receptacle: GCT's land pattern (0.19 mm pad to NPTH peg hole).
Mask bridges are reported only for those footprints (exposed contacts). Silkscreen text under 1.0 mm is a warning:
JLC prints it, it may just be hard to read. Dangling vias are a warning too: a plated hole with copper on one layer
only is not a fab problem. This board has three, all in the first KiCad baseline of unmodified Croco V2.1 (+3V3 at
(122.4, 64.79), J2 DET_A / DET_B stubs).
Copied from the kagiboy wallet cart's gate (gb-wallet hardware/cart/verify/jlc_check.py), 2026-10-16.

Run from the repo root: python3 verify/jlc_check.py   (needs kicad-cli)
"""
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BOARD = HERE / "hw/rev-b/GameboyCartridgeV2.1"
JLC = dict(min_clearance=0.09, min_track_width=0.09, min_via_diameter=0.25, min_through_hole_diameter=0.15,
           min_via_annular_width=0.05, min_copper_edge_clearance=0.2, min_hole_to_hole=0.2, min_text_height=1.0)
INHERITED = ("J5", "SW1", "J1")
COSMETIC = {"via_dangling", "lib_footprint_mismatch", "lib_footprint_issues", "silk_overlap", "text_height", "text_thickness"}


def in_inherited(v):
    text = " ".join(i["description"] for i in v["items"])
    return any(f" of {r}" in text or f"{r} on " in text or f"of {r} " in text for r in INHERITED)


def main():
    with tempfile.TemporaryDirectory() as t:
        for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
            if Path(str(BOARD) + ext).exists():
                shutil.copy(str(BOARD) + ext, Path(t) / (BOARD.name + ext))
        pro = Path(t) / (BOARD.name + ".kicad_pro")
        d = json.loads(pro.read_text())
        d["board"]["design_settings"]["rules"].update(JLC)
        pro.write_text(json.dumps(d, indent=2))
        out = Path(t) / "drc.json"
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "-o", str(out),
                        str(Path(t) / (BOARD.name + ".kicad_pcb"))], check=True, capture_output=True)
        r = json.loads(out.read_text())
    counts = collections.Counter(v["type"] for v in r["violations"])
    print("DRC at JLC limits:", dict(counts))
    real = [v for v in r["violations"] if v["type"] not in COSMETIC and not in_inherited(v)]
    for v in real:
        print("  FAIL", v["type"], v["description"][:110], "|", " / ".join(i["description"][:50] for i in v["items"]))
    small = [v["items"][0]["description"][:60] for v in r["violations"] if v["type"] == "text_height"]
    if small:
        print(f"  warn: {len(small)} silkscreen texts under 1.0 mm (Croco pin labels, JLC's order-number field)")
    dangling = sum(v["type"] == "via_dangling" for v in r["violations"])
    if dangling:
        print(f"  warn: {dangling} dangling vias (Croco V2.1's, in the first baseline)")
    unconnected = len(r["unconnected_items"])
    ok = not real and unconnected == 0
    print(f"unconnected {unconnected}")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
