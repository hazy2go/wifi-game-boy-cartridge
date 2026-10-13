# Logbook: WiFi Game Boy cartridge, rock-solid revision

Running engineering log. Newest entries at the bottom. Every entry says what was done, what was found, why a decision was made, and what is still unverified.
Times are JST. (Corrected 04:58: the first entries were written with estimated times; all headings now match the git commit times.)

---

## 2026-10-14 04:04 Starting point

- Upstream: Staacks/wifi-game-boy-cartridge (2021). ESP8266 + 32KiB EEPROM + 74AHCT logic, two magic addresses (0x7FFE read, 0x7FFF write) mapped to the ESP.
- Upstream README: "The brown-out issues have never been resolved and this is more a proof of concept than a consumer product."
- Goal for this fork: a revision that is rock solid on every Game Boy and can be ordered fully assembled (JLCPCB PCBA). Nothing hand-soldered. Nothing gets ordered until simulation, design rule checks and a written test plan all pass.

## 2026-10-14 04:09 Fork

- Forked to hazy2go/wifi-game-boy-cartridge, `upstream` remote kept. Work happens on branch `redesign`.

## 2026-10-14 04:12 Audit of the original design

Method: parsed `pcb/wifi-gb-cart.kicad_pcb` (pad-to-net map), read the legacy schematic, BOM and every firmware file. Full findings in `docs/REDESIGN.md` section 1. The ones that matter most:

1. **Bulk capacitance on the wrong side of the regulator.** C5 680uF + C3 100uF sit on 3.3V after the NCP1117 linear regulator. A linear regulator draws from its input exactly what its output delivers, so every WiFi TX spike passes straight onto the Game Boy 5V. The caps only help once 5V has already sagged under ~4.4V (regulator dropout). This explains the author's "stable on fresh batteries only".
2. **Unlimited inrush.** Hot-plug or power-on charges ~780uF at the regulator's ~1A current limit straight from the console.
3. **Bus contention is possible in hardware.** The SN74LVC4245A transceiver direction is wired to A0 and its enable only to the address decode. /WR is not connected at all. Writing 0x7FFE makes the transceiver fight the CPU; any 0x7FFF access while ESP outputs are on shorts the 3.3V side.
4. **ESP boot-strap pins sit on the data bus** (GPIO0 = D0, GPIO2 = D2, GPIO15 = D7). A brown-out reset while the Game Boy touches 0x7FFF can strap the ESP into the wrong boot mode, so it hangs. Brown-out leads to reset leads to hang.
5. Firmware: no WiFi mode/TX power/sleep settings, ISRs mask interrupts and busy-wait with no guaranteed output-off timeout, open-drain pin init (D7 stuck low), samples taken after timed-out edge waits (ghost button presses), stack overflow in wiki.ino URL builder.
6. Board: no decoupling on U1-U5/U8/U9, top ground split into ~27 islands, status LED reversed.

## 2026-10-14 04:18 Prior art research

- Upstream issue #2 "Power instabilities" still open: 5V measured collapsing to ~3V on tired NiMH while the ESP boots. Author swapped NCP1117 for AP7361C (lower dropout), not confirmed as a fix.
- Upstream issue #6 (Jan 2026): author says he would design many things differently, BLE "might immediately solve the issue of brownouts", no further work planned.
- All 23 forks untouched. Nobody has fixed it.
- No documented current limit exists for the cartridge 5V pin on any Game Boy. DMG 5V is a crude transformer converter (2SD1664 + 4.7V zener feedback), not a regulated supply. Nintendo gave GBC rumble carts their own AAA battery. Croco Cartridge warns an overclocked RP2040 can already be too much for an original DMG.
- **Newer WiFi chips are worse on peaks, not better:** ESP8266 170mA, ESP32-C3 335mA, C6 354mA, S3 340mA, C5 up to 408mA, Pico W radio 271mA (all at 3.3V, datasheets). So the fix must be in the power path, not in the chip choice.

## 2026-10-14 04:26 Decision: Rev B (base on Croco Cartridge V2)

Options were Rev A (patch the Staacks design) or Rev B (start from Croco Cartridge V2 by shilga, add WiFi).

Chosen: **Rev B**. Why:
- Croco V2 is proven on real DMG and GBC hardware, open hardware (CERN-OHL-P v2), and already designed for JLCPCB assembly with LCSC part numbers.
- RP2350 serves the whole cartridge bus from PIO, so the fragile two-magic-byte EEPROM trick and its contention paths disappear. ROMs can be loaded over USB/WiFi instead of reprogramming an EEPROM.
- The only new risk is the radio + power stage, and that can be simulated before ordering.
- Cost: roughly $25-35 per assembled board vs ~$15 for Rev A. Worth it.

## 2026-10-14 04:27 Croco V2 board scan

Source: github.com/shilga/rp-gameboy-cartridge-hw, `KiCad/V2/GameboyCartridgeV2.1`.
- 4-layer board, outline 52.8 x 61.2 mm, 0.8mm thickness + ENIG recommended by the author.
- Contents: RP2350 (80QFN, RP2350B), W956A8MBYA5I HyperRAM, W25Q16JV flash, microSD, MCP79510 RTC + coin cell, USB-C, WS2812B LED, AP2112K-3.3 regulator, one TXB0108 level shifter, polyfuse F1, two Schottky diodes for USB/cartridge power OR-ing, TVS arrays on the bus.
- **Every component is on the top side.** The bottom side only has test points and logos. That is where the radio module and the power stage can go, without moving anything that already works.
- No bulk energy storage anywhere on V2.
- Open questions: free RP2350 GPIOs for an SPI link to the radio, shell clearance on the bottom side, antenna keep-out vs ground pours on a 4-layer board.

## 2026-10-14 04:27 Tooling

- Installed ngspice (brew) for the power simulation. KiCad 9 installing (cask) for ERC/DRC and BOM/CPL export from the command line.

## 2026-10-14 04:30 Power simulation, round 1 (plain limiter)

Built `sim/power/run_sim.py` (behavioural ngspice models, all assumptions in `sim/power/README.md`). Every run is a hot-plug into a running console, 2s, three radio load profiles, three console supply strengths.

- **Model sanity check passed:** the original design on a weak supply drags the console rail to 2.8V on hot-plug. Upstream issue #2 measured ~3V. Same failure, same size.
- Swapping the original's ESP8266 for an ESP32-C3 makes it much worse (tired console: 3ms below 4.5V becomes 353-747ms). Confirms "a newer chip alone is not a fix".
- A plain 150mA limiter + reservoir fixes fresh and tired consoles (rail never below 4.63V) but **fails on a weak console**: the rail sits at 4.39V the whole time, because 100mA console + 60mA Croco + 150mA radio is too much for a weak supply.

## 2026-10-14 04:32 Round 2: input-voltage regulation (DPM)

Idea taken from solar/USB battery chargers: instead of a fixed current, the cartridge only takes what the console can give while its rail stays above a threshold.
- With fold-back at 4.6V: console rail never below 4.62V in all 9 cases. But on a weak console the radio starved: reservoir fell to 2.05V, radio nearly lost power.
- Added a firmware power-save state (radio drops to modem-sleep when the reservoir is low). Still starved at 4.6V; at 4.5V fold-back there is enough headroom.

## 2026-10-14 04:34 Round 3: throttle thresholds (the non-obvious one)

- With the firmware throttle at 3.0V the reservoir lived at ~3V. A linear current limiter delivers I x V(reservoir) into the cap and burns the rest, so at 3V it only delivers ~60% of what it takes from the console. Throughput was capped at 79% on busy traffic.
- Raised throttle to 4.0V and power-save to 3.5V: the reservoir lives near 4-4.5V where the limiter is efficient. Busy-traffic throughput went to **99%** at the same 150mA.

**Result with the full design (150mA limit + 4.5V fold-back + 4.0V throttle + 3.5V power-save):**
- Console rail never below 4.5V in any of 9 scenarios (fresh 4.78V, tired 4.63V, weak 4.54V min). The original dropped to 4.23 / 3.67 / 2.80V.
- Radio never browns out (reservoir never below 3.5V after power-good).
- Throughput fresh/tired: 99% busy traffic, 72-80% worst-case 802.11b, 62% artificial stress. Weak console: radio stays connected in modem-sleep but sends almost nothing. The game never crashes, which is the trade we want.

Still unverified (needs real hardware): actual console supply impedance, Croco's real current, ESP32-C3 modem-sleep average, real fold-back circuit behaviour. A part search for a buildable fold-back circuit is running.

## 2026-10-14 04:37 Finding a real chip for the fold-back

Part search (datasheets downloaded, stock from JLC's API):
- **BQ24075** (TI Li-ion charger with power path, C15464, 16.7k in stock, ~$1.07). Has a VIN-DPM loop: below 4.5V typ (4.35 min, 4.63 max) on its input it cuts current. Checked in the datasheet text: VIN-DPM is only active in USB100/USB500 modes (EN2 low). Resistor-ILIM mode has no DPM. Used with charging disabled (CE high) and BAT switch off (SYSOFF high), its OUT pin is simply a current-limited, input-regulated supply for our reservoir.
- Caveat: datasheet recommends 4.7-47uF on OUT; we put ~1.4mF there. Soft-start charges OUT at 100mA until 0.9V, then the selected limit. Expected fine, must be bench-tested.
- TPS2553 ILIM cannot be used for fold-back (sinking current from ILIM *raises* the limit; ILIM resistor range 15-232k for loop stability).
- Discrete op-amp/TLV431 + PMOS fold-back = fallback (loop compensation unverified).
- MCP73871 (VPCC adjustable) rejected: VPCC only cuts battery charging, load on OUT is unprotected.
- Rejected for stock/specs: BQ25173 (no VINDPM, 0 stock), BQ25185 (100uF max on SYS), LTC4041/LTC3226 (cost/stock), MAX38889, BQ25190, BQ25504/BQ25570.

## 2026-10-14 04:39 Power simulation, round 4: real BQ24075 behaviour + tolerance corners

Ran the BQ24075 alone in both modes and then the combination:
- USB100 alone (95mA): console safe, but only 34% of busy-traffic airtime. Too little.
- USB500 alone (500mA + DPM): 100% airtime, but on a fresh console the rail rides down to ~4.59V on every burst (0.3-0.4V ripple on the console supply = speaker noise risk, the original's audio static complaint).
- **Chosen: TPS2553 at 150mA in front of BQ24075 in USB500.** The TPS2553 gives a smooth constant ceiling, the BQ24075 a guaranteed input-voltage loop. Typical part: identical to the idealised design (fresh/tired 99% airtime, rail >= 4.63V; weak rail 4.52V).
- Tolerance corners exposed two weak-console problems:
  - DPM at 4.35V (min): rail sat at 4.39V for most of the run.
  - DPM at 4.63V (max): radio starved, reservoir fell to 2.06V, radio power-good cycling.
- Fixes: (1) radio firmware also watches the cart rail on its ADC and backs off below 4.55V, (2) precision 4.45V comparator gating the charge path as a hard floor (instead of the 4.38V APX803).
- Result: worst corner is now a single 57ms dip to 4.46V right at plug-in on a weak console with a min-tolerance BQ24075, then >= 4.5V. Max-tolerance corner on a weak console: radio parks/cycles, console unaffected. Firmware rule added: if the reservoir falls below 3.0V the radio goes to deep sleep and retries later, instead of cycling.

## 2026-10-14 04:41 How the radio connects to Croco

- Mapped every RP2350B pin from the Croco V2 PCB netlist: **all 48 GPIOs are used** (GB bus 17-45, HyperRAM 6-16, SPI0 + SD/RTC 0-3/5, button 4, debug UART TX 46, WS2812 LED 47).
- Croco V2 firmware is Rust/Embassy (shilga/rp2350-gameboy-cartridge-firmware, GPL-3). GPIO46 is only defmt-serial logging.
- Croco power path (from netlist): cart VCC -> D2 Schottky -> +5V -> AP2112 3.3V; USB VBUS -> F1 -> D3 -> +5V. **The new power stage must tap raw VCC before D2**, otherwise the DPM loop sees ~0.3V less and folds back on healthy consoles.
- Decision: ESP32-C3 as an SPI slave on the existing SPI0 bus with chip select on GPIO46 (debug logging moves to defmt-RTT over the existing SWD Tag-Connect). MISO through a 74LVC1G125 tri-state buffer gated by that chip select. RP2350 polls, no IRQ line. Keeps LED, RTC and SD untouched.
- Power mux (prefers USB) in front of the stage so the radio works and can be flashed on USB alone.
- Radio flashing without soldering: ESP32-C3 native USB; preferred a USB hub chip behind the USB-C (both chips enumerate), fallback analog USB switch + slide switch.
- Details: `docs/ARCHITECTURE.md`.

## 2026-10-14 04:43 KiCad baseline

- KiCad 10.0.6 installed. Ran ERC/DRC on the **unmodified** Croco V2.1 files to have a baseline before touching anything.
- DRC (zones refilled): 199 solder-mask bridges, 107 clearance, 15 shorting items, 10 via diameter, 4 hole clearance, 3 unconnected, 2 items-not-allowed. ERC: 262 (mostly library/symbol link issues from opening a KiCad 8/9 project in 10).
- Spot checks: the "shorts" are copper graphics inside footprints (SW1 soft button, J5 edge connector polygon); clearance hits use KiCad 10's default 0.2mm board minimum, Croco was designed to tighter JLC 4-layer rules. These boards were ordered and work, so most of this is rule configuration, not real defects.
- Next: set an explicit JLC 4-layer rule set, re-run, and triage what is left into real vs. artefact **before** adding anything, so every new violation is ours.

## 2026-10-14 04:44 Open mechanical question: does anything fit on the bottom?

- A cartridge shell has limited interior depth. The coin-cell holder, USB-C and microSD on the top side set where the PCB sits. If the bottom only has ~1mm, a 2.4mm-thick ESP32-C3-MINI-1 does not fit there.
- Antenna: a PCB antenna needs a copper keep-out on all 4 layers and must sit in the part of the cart that sticks out of the console. The top edge of Croco V2 is crowded (USB-C, microSD, LED, RTC crystal). Leaning towards ESP32-C3-MINI-1U (u.FL) + a stick-on FPC antenna in the shell's label area: plugs in, no soldering.
- Research on shell clearances and stock for the remaining parts (power mux, USB hub, buffer, low-profile reservoir caps, MINI-1U, antenna) is running.

## 2026-10-14 04:54 Mechanical result: the bottom side is unusable (plan change)

Measured a community 3D model of an original-style DMG shell (Printables #804399, ±0.2mm, cross-checked against Wikipedia's 65.5 x 57 x 7.5mm):
- Back wall ~1.15mm, front (label) wall ~2.0mm, interior cavity 4.65mm (5.3mm near the connector).
- Full-width ribs on the label half come down to 2.5mm, at the shoulder and at the top edge, forming a 1.35mm slot the PCB sits in.
- Croco's 3.38mm coin-cell holder only fits on the rib side, so Croco's top side faces the label half and its **bottom side lies on the back wall: ~0mm usable, 0.55mm at best**. Even cutting a window through the back wall gives ~1.7mm, less than the module's 2.55mm max.
- Top side budget 3.3-3.85mm: a 2.4mm module fits in height, but a 2mm occupancy grid of Croco's top side shows **no free 13 x 17mm area**.
- Precedent: Staacks used a 1.6mm PCB and a modified 3D-printed shell and wrote he doubted it fits any normal cartridge.

**Conclusion: the earlier "put the radio on the empty bottom side" plan is dead.** The radio must go on the top side, which means changing Croco's top layout.

Other findings from the same search:
- 6TPE470MI reservoir caps are **3.8mm tall**, too tall for either side. Low-profile replacements: 6TPE330MAP (330uF, 1.8mm, 25 mOhm, C79112) or 6TPE220MI (220uF, 1.8mm, 18 mOhm, C129173). Reservoir has to be re-simulated with the smaller total.
- Stock (JLC API): ESP32-C3-MINI-1U-N4 C2911374 (1740), MINI-1-N4 C2838502 (8243), TPS2116DRLR power mux C3235557 (72k), CH334P USB hub C5373042 (28k, crystal optional per datasheet), TS3USB221 C128396, SN74LVC1G125 C23654, TLV431A C56765, u.FL U.FL-R-SMT-1 C88374. FPC antennas are not JLC-assembled (plug-in after delivery).
- Bare ESP32-C3FH4 QFN (0.9mm): JLC 13k stock but LCSC 0; needs our own RF matching network. Higher risk than a certified module.

Candidate ways to make room on the top side (decision pending with Hazy):
1. Replace the CR1220 coin-cell holder (largest top part, ~13 x 15mm footprint) with a small SMD rechargeable cell (e.g. ML621) and resync the clock over WiFi (NTP). Put the ESP32-C3-MINI-1U where the holder was, u.FL cable to a stick-on antenna in the shell's grip end.
2. Keep the coin cell, relayout the whole top side tighter (high effort, high risk on a proven board).
3. Bare ESP32-C3 chip with our own RF design (smallest, riskiest).

## 2026-10-14 04:57 Decision: option 2, keep the coin cell, relayout the top side (Hazy)

- Hazy chose to keep the CR1220 coin cell (clock retention for years) and relayout Croco's top side to fit the radio + power stage.
- Area budget from the board file (KiCad pcbnew API, courtyard areas, top side): board 52.95 x 61.34mm. Edge connector region ~540mm2. Existing top parts excluding the connector ~1370mm2 of ~2650mm2 usable (52%). New parts estimate ~520mm2 courtyard (MINI-1U ~190, 3 reservoir caps ~110, BQ24075/TPS2553/TPS63802/mux/hub/buffer + passives ~220). After: ~71% density. Tight but realistic for 4 layers with 0402 passives.
- Layout constraints to respect: top-side height 3.3-3.85mm, shell ribs at ~11mm and ~62mm from the connector end (keep tall parts out of those bands, Croco already does), antenna cable to the grip end.

## 2026-10-14 04:59 Power simulation, round 5: low-profile reservoir

The 3.8mm-tall 6TPE470MI does not fit, so re-ran the chosen design with 1.8mm-tall 6TPE330MAP (330uF, 25 mOhm) in parallel:

| caps | radio up after plug-in | busy traffic | worst 802.11b (fresh/tired) | stress | console rail |
|---|---|---|---|---|---|
| 2x 330uF | 18ms | 99% | 54% / 50% | 63% | unchanged |
| **3x 330uF** | 27ms | 99% | 65% / 59% | 62% | unchanged |
| 4x 330uF | 37ms | 99% | 76% / 68% | 62% | unchanged |

- Console safety does not depend on reservoir size at all (the limiter and the DPM loop decide that). Reservoir size only buys airtime for the long 12ms 802.11b frames.
- **Chosen: 3x 6TPE330MAP (~1mF).** Firmware will disable 802.11b rates (`esp_wifi_config_11b_rate`), which removes the 12ms frames that need the big reservoir in the first place.

## 2026-10-14 05:00 Schematic tooling

- Several new parts have no KiCad library symbol (TPS2553, TPS63802, ESP32-C3-MINI-1U, the comparator). Present in KiCad 10: TPS2116DRL, BQ24075RGT, CH334F/R, SN74LVC1G125DBV.
- Decision: the radio sheet is **generated** from one machine-readable netlist (`docs/design/netlist_radio.json`) by `hw/rev-b/tools/gen_radio_sheet.py`. Every pin gets a label, power symbol or no-connect marker; a pin missing from the JSON is a hard error. The same JSON will drive footprint/net assignment on the PCB, and KiCad's DRC schematic-parity check will prove the two match. Why: no hand-drawn wire can silently miss a pin, and the design is reviewable as a table.
- `tools/add_radio_sheet.py` adds the "Radio" hierarchical sheet to Croco's root schematic once (fixed sheet UUID so references stay stable across regenerations).
- Smoke test on a copy with 3 parts: sheet loads in KiCad 10, ERC runs and reports exactly the expected undriven-input errors for the test parts.
- A datasheet-level design pass (pin tables, component values with equations and citations, LCSC numbers, footprints) is running and will produce the JSON.

## 2026-10-14 05:02 Top-side occupancy map

![Croco top side](img/croco_top_occupancy.png)

- Rendered every top-side courtyard from the board file, with the approximate shell rib bands (red) and a 13.2 x 12.5mm MINI-1U outline for scale.
- Candidate areas: right column around SW1/J4/SW2 (x 150-166, y 76-100), lower-left around D2/D3/U6/C25/C26 (x 113-125, y 95-112), and the top-center if the debug header J3 goes (GPIO46 becomes the ESP chip select, so the UART header has no job left).
- Constraint: Croco's HyperRAM (U4) and RP2350 (U3) routing is the timing-critical core. The relayout should move peripheral parts and leave the U3/U4/U2/Y1 core routing untouched.

## 2026-10-14 05:04 Layout tooling

- Installed OpenJDK 21 and Freerouting 2.5.0 (jar, `~/Projects/tools`). Plan: lock Croco's existing core routing, export Specctra DSN from pcbnew, autoroute only the new and moved nets headless, import the session, then DRC + schematic parity. Every autorouted net gets reviewed against the board's rules before it is accepted.
- Logbook rule from now on: every heading time comes from the system clock at the moment of writing (two entries had been written with guessed times and were corrected).

## 2026-10-14 05:22 Audit loop (Hazy: "audit loop every test, simulate, emulate")

New rule for the rest of the project: **every result gets a scripted check with a pass/fail limit, and an independent second way of getting the same number.** A result that has not passed its audit is not reported as a result. Harness lives in `verify/` and `sim/power/audit.py`.

### What the audit is made of

| id | check | how it is independent |
|---|---|---|
| A1 | ngspice vs `sim/power/pyref.py` on the chosen design, 3 consoles x 3 profiles | pyref is a fresh fixed-step integrator written from the circuit equations; shares only parameter values with the SPICE netlists |
| A2 | ngspice at 20us vs 5us max step | catches timestep artefacts |
| A3 | console-rail floor vs closed-form steady state | pencil-and-paper algebra |
| A4 | energy sanity (source energy > radio energy) | conservation |
| A5 | realistic firmware across tolerance corners | see below |
| K1 | KiCad ERC/DRC/parity vs recorded baseline of unmodified Croco V2.1 (`verify/kicad_check.py`) | fingerprints every violation; any new one fails |

The KiCad gate was itself tested: deleting one track segment in a scratch copy made it FAIL with 2 new clearance violations (mutation test). A gate that cannot fail is not a gate.

### A1-A4 result: the math was right

- A1: ngspice and pyref agree to <= 0.2 mV on the console minimum, <= 0.3 points on TX airtime, <= 0.02 ms on radio start. All 27 checks PASS.
- A2: 20us vs 5us: <= 0.3 mV, <= 0.05 points. PASS.
- A3: console floor 4.783 / 4.628 / 4.516 V (fresh/tired/weak), identical to the closed-form solution. PASS.
- A4: PASS.

### Problems the audit found in my own earlier work

1. **Tolerance corners were run on the wrong reservoir.** `final_lo`/`final_hi` used the old 1.41 mF bank, not the 0.99 mF (3x 330uF) bank that was chosen at 04:59.
2. **A hidden failure in the committed results.** Weak console + DPM at its 4.63 V maximum: reservoir minimum 2.06 V, i.e. the radio was sitting on its power-good cut-off. It was in `results.txt` but I did not call it out.
3. **`results.txt` was not reproducible** from the committed script (a duplicated `final` row from an earlier variant list). Will be regenerated by the harness.
4. **The SPICE firmware model was too kind.** It throttles TX continuously, i.e. it can stop a WiFi frame half way. A real radio cannot. It also throttled the boot RF calibration, which is not throttleable.

### Datasheet audit (sub-agent, primary sources)

- TPS2553 "150 mA" (RILIM 180k) is really **129 / 150 / 174 mA** (datasheet Eq. 1 min/nom/max). Corner sweep now uses 129-174 mA.
- TPS2553 does **not** block reverse current instantly (3-7 ms deglitch). Reverse blocking has to come from the stage after it.
- **BQ24075 VIN-DPM only works in USB100/USB500 mode** (EN2 low); in ILIM-resistor mode there is no input-voltage regulation. USB500 behind the 150 mA TPS2553 is fine (that is TI's own Fig 9-3 case), but:
  - VIN-DPM is 4.35 / 4.50 / 4.63 V. At the 4.35 V corner a weak console sits at ~4.45 V while the reservoir charges.
  - It is a Li-ion charger used without a battery. Datasheet says it runs with an absent battery but runs a 250 ms battery-detection cycle forever; the sleep comparator (VIN > VBAT + 55-130 mV, BAT near 4.2 V with no cell) is only ~0.15 V above the DPM minimum. Thin.
  - Input FET drop implies 0.3-0.48 ohm (no RDS(on) given). Corner sweep now uses 0.2-0.6 ohm total switch path.
- ESP32-C3-MINI-1U: TX peak **350 mA** (11b 20.5 dBm), 295 mA (11g), RX 82-84 mA, modem-sleep 13-28 mA, light-sleep 130 uA. Datasheet wants a supply able to deliver >= 0.5 A (the reservoir + buck-boost provides the peak, the input does not).
- TPS63802: starts above 1.8 V, runs down to 1.3 V; ~91-94 % efficient at 3.6-4.2 V in, 300 mA out (read from curves). 0.88 was conservative.
- 6TPE330MAP: 330 uF, 25 mOhm, 1.8 mm, 6.3 V (79 % of rating on a 5 V rail), leakage up to 208 uA each.
- No published current budget for the cartridge slot on any Game Boy. Pan Docs: whole DMG is ~0.7 W. **Our 150 mA at 5 V is about the same power as the entire console.** This is the strongest argument for keeping the limit low.

### A5: firmware as a state machine (pyref.simulate_fw)

Radio states OFF / BOOT (RF calibration, uninterruptible) / RUN / SLEEP / PARK, all decisions from a 1 ms ADC sample, frames admitted only if the reservoir is predicted to stay above 3.0 V at the end of the frame, never aborted. Uncontrolled reset = reservoir falls through power-good (2.0 V) while the radio is on.

Findings:
- **Console safety holds on fresh and tired consoles in every corner** (129-174 mA, DPM 4.35-4.63 V, logic 40-100 mA, efficiency 0.80-0.92, switch path 0.2-0.6 ohm, three traffic profiles).
- **Weak console at the DPM 4.35 V corner: console sits at 4.46 V** for as long as the reservoir is charging. That is below the 4.5 V line. Root cause is the BQ24075 tolerance, not the topology.
- **The RF calibration at WiFi start decides whether the radio survives.** Usable reservoir energy 4.2 -> 2.0 V is 6.75 mJ. A TX-heavy calibration phase at 50 % duty drains it in ~13 ms at the worst corner and ~37 ms at nominal. Calibration of 5 ms is fine, 20 ms at 50 % causes a controlled park on fresh consoles and reset loops on weak ones. The real calibration current profile of the ESP32-C3 is the number that matters most now; research running.
- The 12 ms 802.11b frames are not survivable with this reservoir under realistic gating (radio reset loops at the low-current corner). **Firmware must disable 11b rates and A-MPDU TX** (frames <= ~2.5 ms). This becomes a hard firmware requirement, not an optimisation.
