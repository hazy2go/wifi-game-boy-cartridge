# Rev B hardware

Status (2026-10-14): 6-layer 0.8 mm, all parts placed, fully routed and tidied, all gates green (see below). Not ordered yet.

Base: **Croco Cartridge V2.1** by shilga, imported unmodified from
https://github.com/shilga/rp-gameboy-cartridge-hw (`KiCad/V2/GameboyCartridgeV2.1`, commit cb750a1, 2025-11-29).
Licence of the imported design: CERN Open Hardware Licence v2 Permissive (`LICENSE-CERN-OHL-P.md`). All credit for the cartridge core goes to the Croco project.

This fork adds a WiFi radio (ESP32-C3) and a console-safe power stage on top of it. See `../../docs/ARCHITECTURE.md` and `../../docs/LOGBOOK.md`.

Rules for changes here:
- The first commit of this folder is the untouched upstream design, so every later diff is ours.
- Nothing is ordered until ERC/DRC are clean against an explicit JLC rule set, the power stage passes `sim/power`, the height/antenna checks in the logbook are closed, and the full-system simulation with a Game Boy ROM (`sim/system`) passes on the final layout. State 2026-10-14: all met; the order files still need Hazy's review.
- Gate commands: `python3 verify/kicad_check.py` (ERC/DRC/unconnected/parity vs Croco's baseline), `verify/netlist_check.py`, `verify/sim_vs_netlist.py`, `verify/design_calcs.py`, `sim/system/run_system.py`, `sim/system/run_demo.py`.
- Layout tools used: `tools/` (placement, Freerouting import, finisher, pull-tight cleaner, audits, copper-resistance extraction). Each is documented at the top of the file.
- Croco's author recommends 0.8mm board thickness and ENIG finish.
