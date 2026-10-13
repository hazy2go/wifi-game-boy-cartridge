# Logbook: WiFi Game Boy cartridge, rock-solid revision

Running engineering log. Newest entries at the bottom. Every entry says what was done, what was found, why a decision was made, and what is still unverified.
Times are JST.

---

## 2026-10-14 04:05 Starting point

- Upstream: Staacks/wifi-game-boy-cartridge (2021). ESP8266 + 32KiB EEPROM + 74AHCT logic, two magic addresses (0x7FFE read, 0x7FFF write) mapped to the ESP.
- Upstream README: "The brown-out issues have never been resolved and this is more a proof of concept than a consumer product."
- Goal for this fork: a revision that is rock solid on every Game Boy and can be ordered fully assembled (JLCPCB PCBA). Nothing hand-soldered. Nothing gets ordered until simulation, design rule checks and a written test plan all pass.

## 2026-10-14 04:10 Fork

- Forked to hazy2go/wifi-game-boy-cartridge, `upstream` remote kept. Work happens on branch `redesign`.

## 2026-10-14 04:15 Audit of the original design

Method: parsed `pcb/wifi-gb-cart.kicad_pcb` (pad-to-net map), read the legacy schematic, BOM and every firmware file. Full findings in `docs/REDESIGN.md` section 1. The ones that matter most:

1. **Bulk capacitance on the wrong side of the regulator.** C5 680uF + C3 100uF sit on 3.3V after the NCP1117 linear regulator. A linear regulator draws from its input exactly what its output delivers, so every WiFi TX spike passes straight onto the Game Boy 5V. The caps only help once 5V has already sagged under ~4.4V (regulator dropout). This explains the author's "stable on fresh batteries only".
2. **Unlimited inrush.** Hot-plug or power-on charges ~780uF at the regulator's ~1A current limit straight from the console.
3. **Bus contention is possible in hardware.** The SN74LVC4245A transceiver direction is wired to A0 and its enable only to the address decode. /WR is not connected at all. Writing 0x7FFE makes the transceiver fight the CPU; any 0x7FFF access while ESP outputs are on shorts the 3.3V side.
4. **ESP boot-strap pins sit on the data bus** (GPIO0 = D0, GPIO2 = D2, GPIO15 = D7). A brown-out reset while the Game Boy touches 0x7FFF can strap the ESP into the wrong boot mode, so it hangs. Brown-out leads to reset leads to hang.
5. Firmware: no WiFi mode/TX power/sleep settings, ISRs mask interrupts and busy-wait with no guaranteed output-off timeout, open-drain pin init (D7 stuck low), samples taken after timed-out edge waits (ghost button presses), stack overflow in wiki.ino URL builder.
6. Board: no decoupling on U1-U5/U8/U9, top ground split into ~27 islands, status LED reversed.

## 2026-10-14 04:20 Prior art research

- Upstream issue #2 "Power instabilities" still open: 5V measured collapsing to ~3V on tired NiMH while the ESP boots. Author swapped NCP1117 for AP7361C (lower dropout), not confirmed as a fix.
- Upstream issue #6 (Jan 2026): author says he would design many things differently, BLE "might immediately solve the issue of brownouts", no further work planned.
- All 23 forks untouched. Nobody has fixed it.
- No documented current limit exists for the cartridge 5V pin on any Game Boy. DMG 5V is a crude transformer converter (2SD1664 + 4.7V zener feedback), not a regulated supply. Nintendo gave GBC rumble carts their own AAA battery. Croco Cartridge warns an overclocked RP2040 can already be too much for an original DMG.
- **Newer WiFi chips are worse on peaks, not better:** ESP8266 170mA, ESP32-C3 335mA, C6 354mA, S3 340mA, C5 up to 408mA, Pico W radio 271mA (all at 3.3V, datasheets). So the fix must be in the power path, not in the chip choice.

## 2026-10-14 04:25 Decision: Rev B (base on Croco Cartridge V2)

Options were Rev A (patch the Staacks design) or Rev B (start from Croco Cartridge V2 by shilga, add WiFi).

Chosen: **Rev B**. Why:
- Croco V2 is proven on real DMG and GBC hardware, open hardware (CERN-OHL-P v2), and already designed for JLCPCB assembly with LCSC part numbers.
- RP2350 serves the whole cartridge bus from PIO, so the fragile two-magic-byte EEPROM trick and its contention paths disappear. ROMs can be loaded over USB/WiFi instead of reprogramming an EEPROM.
- The only new risk is the radio + power stage, and that can be simulated before ordering.
- Cost: roughly $25-35 per assembled board vs ~$15 for Rev A. Worth it.

## 2026-10-14 04:30 Croco V2 board scan

Source: github.com/shilga/rp-gameboy-cartridge-hw, `KiCad/V2/GameboyCartridgeV2.1`.
- 4-layer board, outline 52.8 x 61.2 mm, 0.8mm thickness + ENIG recommended by the author.
- Contents: RP2350 (80QFN, RP2350B), W956A8MBYA5I HyperRAM, W25Q16JV flash, microSD, MCP79510 RTC + coin cell, USB-C, WS2812B LED, AP2112K-3.3 regulator, one TXB0108 level shifter, polyfuse F1, two Schottky diodes for USB/cartridge power OR-ing, TVS arrays on the bus.
- **Every component is on the top side.** The bottom side only has test points and logos. That is where the radio module and the power stage can go, without moving anything that already works.
- No bulk energy storage anywhere on V2.
- Open questions: free RP2350 GPIOs for an SPI link to the radio, shell clearance on the bottom side, antenna keep-out vs ground pours on a 4-layer board.

## 2026-10-14 04:35 Tooling

- Installed ngspice (brew) for the power simulation. KiCad 9 installing (cask) for ERC/DRC and BOM/CPL export from the command line.

## 2026-10-14 04:40 Power simulation, round 1 (plain limiter)

Built `sim/power/run_sim.py` (behavioural ngspice models, all assumptions in `sim/power/README.md`). Every run is a hot-plug into a running console, 2s, three radio load profiles, three console supply strengths.

- **Model sanity check passed:** the original design on a weak supply drags the console rail to 2.8V on hot-plug. Upstream issue #2 measured ~3V. Same failure, same size.
- Swapping the original's ESP8266 for an ESP32-C3 makes it much worse (tired console: 3ms below 4.5V becomes 353-747ms). Confirms "a newer chip alone is not a fix".
- A plain 150mA limiter + reservoir fixes fresh and tired consoles (rail never below 4.63V) but **fails on a weak console**: the rail sits at 4.39V the whole time, because 100mA console + 60mA Croco + 150mA radio is too much for a weak supply.

## 2026-10-14 04:45 Round 2: input-voltage regulation (DPM)

Idea taken from solar/USB battery chargers: instead of a fixed current, the cartridge only takes what the console can give while its rail stays above a threshold.
- With fold-back at 4.6V: console rail never below 4.62V in all 9 cases. But on a weak console the radio starved: reservoir fell to 2.05V, radio nearly lost power.
- Added a firmware power-save state (radio drops to modem-sleep when the reservoir is low). Still starved at 4.6V; at 4.5V fold-back there is enough headroom.

## 2026-10-14 04:50 Round 3: throttle thresholds (the non-obvious one)

- With the firmware throttle at 3.0V the reservoir lived at ~3V. A linear current limiter delivers I x V(reservoir) into the cap and burns the rest, so at 3V it only delivers ~60% of what it takes from the console. Throughput was capped at 79% on busy traffic.
- Raised throttle to 4.0V and power-save to 3.5V: the reservoir lives near 4-4.5V where the limiter is efficient. Busy-traffic throughput went to **99%** at the same 150mA.

**Result with the full design (150mA limit + 4.5V fold-back + 4.0V throttle + 3.5V power-save):**
- Console rail never below 4.5V in any of 9 scenarios (fresh 4.78V, tired 4.63V, weak 4.54V min). The original dropped to 4.23 / 3.67 / 2.80V.
- Radio never browns out (reservoir never below 3.5V after power-good).
- Throughput fresh/tired: 99% busy traffic, 72-80% worst-case 802.11b, 62% artificial stress. Weak console: radio stays connected in modem-sleep but sends almost nothing. The game never crashes, which is the trade we want.

Still unverified (needs real hardware): actual console supply impedance, Croco's real current, ESP32-C3 modem-sleep average, real fold-back circuit behaviour. A part search for a buildable fold-back circuit is running.
