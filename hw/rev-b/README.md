# Rev B hardware (work in progress)

Base: **Croco Cartridge V2.1** by shilga, imported unmodified from
https://github.com/shilga/rp-gameboy-cartridge-hw (`KiCad/V2/GameboyCartridgeV2.1`, commit cb750a1, 2025-11-29).
Licence of the imported design: CERN Open Hardware Licence v2 Permissive (`LICENSE-CERN-OHL-P.md`). All credit for the cartridge core goes to the Croco project.

This fork adds a WiFi radio (ESP32-C3) and a console-safe power stage on top of it. See `../../docs/ARCHITECTURE.md` and `../../docs/LOGBOOK.md`.

Rules for changes here:
- The first commit of this folder is the untouched upstream design, so every later diff is ours.
- Nothing is ordered until ERC/DRC are clean against an explicit JLC rule set, the power stage passes `sim/power`, and the height/antenna checks in the logbook are closed.
- Croco's author recommends 0.8mm board thickness and ENIG finish.
