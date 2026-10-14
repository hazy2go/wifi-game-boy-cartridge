# CLAUDE.md: handoff for agents working on this repo

Read this first, then `docs/ARCHITECTURE.md` (what the board is) and the tail of `docs/LOGBOOK.md` (latest decisions).
Status as of 2026-10-14: **hardware design finished, not ordered; software mostly not written.**

## Rules (from the owner, Hazy)
- **Fork only.** `origin` = hazy2go/wifi-game-boy-cartridge. Never push, PR or open issues on `upstream` (Staacks). With `gh`, always pass `--repo hazy2go/wifi-game-boy-cartridge`, because gh defaults to the upstream on forks.
- **Keep the fork's `master` current.** Work on `redesign`, push it, open a PR into `master` on the fork, merge it, then fast-forward local `master` and `redesign`. Do this after every chunk of work.
- **Log every decision, check and result in `docs/LOGBOOK.md`.** Write each entry as a dated heading with a short "why". If you got something wrong, write it as a correction; don't silently rewrite it.
- **Don't claim anything without evidence.** Run the gate or simulation, or quote the datasheet, before you say it.
- **Writing style.** Hazy reads replies on Discord: keep them short and plain, and avoid long dashes as punctuation.
- **Never spend money or place orders.** Ordering (JLC, AliExpress) is Hazy's job.

## Where things stand
Hardware (`hw/rev-b/`, KiCad 10, Croco Cartridge V2.1 + ESP32-C3-MINI-1U + power stage):
- Fully routed, cleaned, silkscreen done. ERC, DRC and parity are clean (`verify/kicad_check.py`).
- JLC files are in `hw/rev-b/fab/`. `ORDER.md` holds the settings, the preview checklist and the hold points.
- Before ordering, test on the Seeed XIAO ESP32C3 dev board (Hazy is buying it):
  1. **A7, the calibration-energy budget.** Partial RF calibration must stay within 5 ms at 50 % TX (about 7 mJ above the input supply), measured on the dev board. This one is required.
  2. **Antenna RSSI** in three positions: free, about 2 mm over copper inside the shell, and with the cart inserted in a Game Boy.
  3. Console 5 V: now optional. The DMG power-board schematics put it at 4.9 to 5.5 V, typically 5.1 to 5.2 V (LOGBOOK, last entry).
- Antenna for the cart: Ebyte TX2400-FPC3-2509 with an IPEX3 / MHF III plug. **Not U.FL.**

Simulation (all passing):
- `sim/power/`: ngspice with TI and Vishay vendor models. `audit.py` runs A1 to A7, `audit_inreg.py` runs A8 (the regulator).
- `sim/system/`: `sysim.c`, the Peanut-GB emulator plus models of the RP2350, the ESP and the power plant.
  - `python3 run_system.py` is gate S1.
  - `python3 run_demo.py` runs the BTC demo.
  - Tools needed: `rgbds`, `cc`, Pillow, `ngspice`.

## Software to do (in this order)
1. **XIAO test firmware (ESP-IDF, blocks the order):**
   - A7: time and energy of the partial calibration, measured from the current profile, or from the timing plus the datasheet current if no current probe is available.
   - Antenna: an RSSI and throughput logger.
   - Results go into LOGBOOK, and A7 gets re-run with the measured number.
2. **RP2350 cart firmware.** Fork shilga/rp2350-gameboy-cartridge-firmware (Rust/Embassy, GPL-3) to hazy2go. It is checked out locally at `~/projects/croco-fw2` but not forked yet.
   - Add an SPI master on SPI0: GPIO0 MISO, GPIO2 SCK, GPIO3 MOSI. Chip select is **GPIO46** (it was the debug UART TX; logging moves to defmt-RTT).
   - **Drive GPIO46 high before the first SD or RTC access.** It has no pull-up, and the RP2350 resets with pull-downs.
   - The ESP's MISO goes through a 74LVC1G125 buffer gated by CS, so the shared bus is safe.
   - There is no IRQ line, so poll the ESP about every 1 ms or once per frame.
   - Serve the mailbox: MBC5 cart-RAM **bank 15**. The protocol table is in `sim/system/README.md`. React to the write to SEQ (A001) using Croco's existing write-sniff and DMA path for save RAM.
3. **SPI frame format between the RP2350 and the ESP: not specified yet.** Define it, write it down in `ARCHITECTURE.md`, and model it in `sysim.c` before both firmwares implement it. The simulation currently assumes 8 MHz and (len + 4)-byte frames.
4. **ESP32-C3 app (ESP-IDF):**
   - SPI-slave mailbox, and commands 01 to 07 as in the mailbox table.
   - WiFi in STA mode only, NVS credentials, OTA.
   - Link the existing `firmware/esp32c3/components/power_mgr`: plain C, already co-simulated. **Ship that code unchanged, or re-run the `sim/power` audits after any change.**
   - Hard rules from the audits:
     - Full RF calibration only on USB power; store the result in NVS. The cart slot must never see a full calibration.
     - 802.11b rates off. Use a small MSS (about 300 bytes) or count the guaranteed input current in admission.
     - Every TX frame goes through `pm_admit_tx`.
     - Cart-rail watch at 4.45 / 4.48 V.
5. **Game Boy side.** Test ROMs in RGBDS: `sim/system/rom/wifitest.asm` and `btcdemo.asm`. Later: a GBDK-2020 library plus demos.
6. `legacy/` holds the original ESP8266 cartridge's code, PCB and shell. It is kept for reference only; don't build on it.

## Tool notes
- KiCad Python: `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3`. It has no CA bundle, so use `curl` for HTTPS.
- KiCad gotchas:
  - After `BOARD.Remove`, objects go stale. Run the board-editing phases in separate processes (see `hw/rev-b/tools/route_clean.py`).
  - `GetBoardPolygonOutlines` needs a second argument, `True`.
- Gate: `verify/kicad_check.py`. Reviewed exceptions live in `verify/baseline/accepted.json`.
- Fab: `hw/rev-b/tools/make_bom.py` checks JLC stock and values live. `make_cpl.py` derives rotations from JLC's own footprints (EasyEDA API; falls back to lceda.cn when rate-limited). `pin_audit.py` checks every pin against the JLC part.
