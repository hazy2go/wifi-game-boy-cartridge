# WiFi Game Boy Cartridge: audit + redesign plan (2026-10)

> Historical: the audit and the first plan. The design that was actually built on (Croco Cartridge V2 + ESP32-C3) is in `ARCHITECTURE.md`; every later decision is in `LOGBOOK.md`.

Fork of Staacks/wifi-game-boy-cartridge. Goal: a rock-solid revision that can be ordered fully assembled (JLCPCB PCBA), nothing hand-soldered.

Status upstream: brown-outs never fixed. Issue #2 "Power instabilities" still open (5V measured collapsing to ~3V on tired NiMH while the ESP boots). In issue #6 (Jan 2026) the author says he would design many things differently and has no plans to continue. All 23 forks are untouched.

## 1. Root causes found in the current design

Power
1. **The 680uF bulk cap is on the wrong side.** C5 (680uF tantalum) and C3 (100uF) sit on 3.3V after the NCP1117. A linear regulator pulls from 5V exactly what the ESP pulls from 3.3V, so every WiFi TX spike (170mA+ on an ESP8266) goes straight onto the Game Boy's 5V rail. The cap only helps after 5V sags under ~4.4V and the LDO drops out, which matches "stable on fresh batteries only".
2. **No inrush limit.** At power-on the LDO charges ~780uF at its current limit (~1A+) from the Game Boy, then the ESP does RF calibration. Worst moment for the DMG's crude 5V converter (transformer + 2SD1664 + zener, not a real regulator).
3. **No decoupling** on the logic gates, EEPROM or level shifters (BOM has only 4x 100nF). Top-layer ground split into ~27 islands, 0.25mm ground traces.

Bus
4. **Transceiver (SN74LVC4245A) direction = A0, enable = address decode only.** Not gated by /RD or /WR (/WR is not even connected). A write to 0x7FFE makes it fight the Game Boy CPU on D0-D7. On the 3.3V side, any 0x7FFF access while ESP outputs are still on is a short, which also loads the rail.
5. **ESP boot pins are on the data bus** (GPIO0=D0, GPIO2=D2, GPIO15=D7). If a brown-out resets the ESP while the Game Boy touches 0x7FFF, the bus can strap it into the wrong boot mode and it hangs until power-cycle. Brown-outs cause resets cause hangs.
6. Status LED is wired reversed (cathode on +3V3), never lights.

Firmware
7. No `WiFi.mode(WIFI_STA)`, no sleep/PHY settings, TX power only lowered in color-stream. A persisted AP mode would beacon every ~100ms = periodic spikes (likely the speaker static).
8. ISRs disable interrupts and busy-wait a whole tile; no timeout guarantees outputs turn off (serial-test.ino:81-108, wiki.ino:87-114, stream.ino:87/:99). One missed edge leaves outputs driving the bus.
9. Pin init bug (open drain, D7 stuck low): serial-test.ino:41, wiki.ino:47. Correct version in stream.ino:35-42.
10. Ghost button presses: samples taken even when the clock-edge wait timed out (stream.ino:126-134 and siblings).
11. Stack overflow: wiki.ino:217-227 (`url[256]` vs ~103 fixed + 255 typed chars).
12. Toolchain: asm needs rgbasm 0.5.2.

## 2. Hard constraints (researched)

- There is **no documented current limit** for the cartridge 5V pin on any Game Boy. Nintendo put rumble motors on their own battery for GBC carts. Croco Cartridge warns an overclocked RP2040 can be too much for an original DMG. Design budget: ~150mA continuous extra, and never pass radio spikes through.
- No WiFi chip fixes it alone. Peak TX at 3.3V: ESP8266 170mA, ESP32-C3 335mA, C6 354mA, S3 340mA, C5 up to 408mA, Pico W/RM2 271mA. Newer chips are worse on peaks. It has to be solved in the power stage.

## 3. Proposed power stage (topology A)

```
Cart 5V ──> TPS2553 (current limit ~150mA, soft start, constant-current mode)
              EN <── APX803-44 supervisor (only on when cart 5V > 4.38V)
          ──> reservoir 2-3x 470uF polymer tantalum (6TPE470MI, 18mOhm) ≈ 1-1.4mF
          ──> TPS63802 buck-boost ──> 3.3V radio rail (works down to ~2V input)
Cart 5V ──> separate small LDO ──> 3.3V logic/MCU rail (never sees radio spikes)
```

Math: 350mA burst at 3.3V ≈ 1.28W. Limiter supplies ~0.5W, deficit ~0.76W.
- 2ms 802.11n frame: 1.5mJ, reservoir 5.0V -> 4.67V.
- 12ms worst case 802.11b 1Mbps frame: 9.1mJ with 1.41mF -> 3.48V. Still fine for the buck-boost.
The Game Boy only ever sees a steady <=150mA. Inrush is limited by the same switch. If the console's rail sags, the supervisor cuts the radio first instead of the whole system crashing.

JLC stock (checked 2026-10-14): TPS2553DBVR C55266, APX803-44 C155243, 6TPE470MI C402828, TPS63802DLAR C2845237, ESP32-C3-MINI-1-N4 C2838502, SN74LVC8T245PWR C27643.

Plus firmware: STA mode only, TX power ~13dBm, disable 802.11b rates, no persistent config.

## 4. Two ways forward

**Rev A, minimal (keep Staacks' architecture)**
- New power stage above.
- ESP8266 -> ESP32-C3-MINI-1 (in stock, Arduino/IDF supported, better ISR latency).
- Data bus moved off boot-strap pins. Transceiver enable gated by /RD|/WR and an MCU "bus enable" pin that defaults OFF.
- Write mailbox: 74x574 latch on /WR so the MCU no longer has to catch a 500ns window. Kills the double-write hack.
- Decoupling on every IC, solid ground plane, reset supervisor.
- EEPROM stays (or parallel flash), so the Game Boy program is still fixed at programming time.

**Rev B, modern (recommended)**
- Base on Croco Cartridge V2 (open hardware CERN-OHL-P, RP2350 serving the whole bus via PIO, already JLC-assembly ready with LCSC BOM, proven on DMG/GBC).
- Add ESP32-C3 on SPI/UART behind topology A.
- Wins: ROM loadable over WiFi/USB, no EEPROM programmer, any memory-mapped register layout, GBC support, a proper mailbox in RP2350 RAM instead of 2 magic bytes.
- Licence check needed (Croco CERN-OHL-P vs Staacks GPL-3), both permissive enough to combine for a personal build.

## 5. Plan to "rock solid before ordering"

1. Firmware fixes on the current code (free, no hardware): items 7-11.
2. Power stage: SPICE simulation (ngspice) with a measured-style load profile (170/335mA bursts of 2-12ms, Game Boy 5V modelled as a sagging source). Prove droop + current limit on paper.
3. KiCad 8 schematic + layout of the chosen rev, ERC/DRC clean, JLC BOM/CPL generated, 3D height check against the cartridge shell.
4. Bus timing check against GB/GBC timing (Pan Docs), and a bench test plan: DMG on weak NiMH, GBC, GBA, hot-plug, WiFi saturation.
5. Only then: order 5 assembled boards.

Unverified so far: official slot current, GB Camera/flash-cart draw, cap height vs shell, WiFi current at reduced TX power, r/thereoughtabe discussions (Reddit blocked).
