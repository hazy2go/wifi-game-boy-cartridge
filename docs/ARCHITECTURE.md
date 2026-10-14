# Rev B architecture: Croco Cartridge V2 + WiFi

Status (2026-10-14): **layout complete, all pre-order gates green, not ordered or built yet.** 6-layer 0.8 mm board, fully routed (0 open connections), KiCad ERC/DRC/parity clean against Croco's baseline, full-system simulation with a Game Boy ROM passes on the final layout. JLC order files ready (`hw/rev-b/fab/ORDER.md`); not ordered until the ESP32-C3 calibration time is measured on a dev board. See `LOGBOOK.md` for how each decision was reached.

## Block diagram

```
                       Game Boy cartridge slot
   A0-A15, CLK, RD, CS ─────────────────────────────┐
   D0-D7 ──── TXB0108 ──────────────────────────────┤
                                                    ▼
   VCC (cart 5V) ──┬── D2 Schottky ── +5V ── AP2112 3.3V ── RP2350B + HyperRAM + flash + SD + RTC   (Croco V2, unchanged)
                   │                    ▲
   USB-C VBUS ─────┼── F1 ── D3 ────────┘
                   │
                   │   NEW (top side, Croco top side re-laid out; see LOGBOOK 04:54 / 04:57)
                   └─► TPS2116 power mux (prefers USB) ─► VMUX ─► TPS2553 (150mA cap, 129-174mA)
                        ─► precision input-voltage regulator (TLV431B + back-to-back PMOS, holds VMUX >= 4.58V +-1%)
                        ─► ~1mF reservoir (3x KEMET T520B 330uF, 2.1mm) ─► power-good (on 4.2V / off 2.0V)
                        ─► TPS63802 buck-boost ─► 3.3V radio rail ─► ESP32-C3-MINI-1U (u.FL, stick-on antenna)
                                                                                 │
   RP2350 SPI0 (GPIO0 MISO / GPIO2 SCK / GPIO3 MOSI, shared with SD + RTC) ◄────┤ SPI slave
   RP2350 GPIO46 (was debug UART TX) ─── ESP chip select ───────────────────────┘
```

## Why each block

| Block | Why |
|---|---|
| Croco V2 core kept, top side re-laid out | Proven on DMG and GBC, JLC-assembly ready. The shell leaves ~0 mm under the board, so the radio has to go on the top side; the RP2350/HyperRAM/flash core routing stays untouched, peripheral parts move (LOGBOOK 04:54, 04:57). |
| Power stage taps the cart rail before Croco's D2 Schottky | The input-voltage regulation has to see the real console rail. Behind the diode it would see ~0.3V less and fold back on healthy consoles. |
| TPS2116 power mux (prefers USB) | Radio runs and can be flashed on USB alone; with both present the console is spared. The regulator senses the mux output, so on USB it is simply fully on. |
| TPS2553 at ~150mA (RILIM 180k: 129/150/174 mA min/nom/max) | Absolute ceiling and inrush-free hot-plug. 150 mA at 5 V is about the power of the whole DMG (Pan Docs: ~0.7 W), which is why it is not higher. |
| Precision input-voltage regulator (TLV431B + back-to-back PMOS) | Replaces the BQ24075 (LOGBOOK 05:22 / 05:29): its VIN-DPM tolerance (4.35 V min) let a weak console sit at 4.45 V, it is a Li-ion charger running without a battery, and its sleep comparator sits close to the DPM point. The TLV431B loop holds the console at 4.588 V +-1 %, set by one resistor (26.7k / 4.551 V was tried and rejected, LOGBOOK 19:30). Type-II compensation on the TLV431 cathode (470 pF + 330k to REF; on the gate it oscillated, LOGBOOK 08:27), Rk = Rg = 680 so the TLV431 stays in its datasheet range, 3-diode gate clamp, optional power-on hold. Proven with TI/Vishay models over 114 logged checks + 120 AC corners (audit A8, LOGBOOK 08:42): PM >= 58.8 deg, GM >= 16.5 dB, no ringing, hot-plug >= +4.8 mV, burst-after-idle >= +16.5 mV. Netlist U109, Q101-Q102, R126-R132, C123-C124, D101-D104. |
| ~1 mF reservoir (3x KEMET T520B337M006ATE040, B case, 2.1 mm) | Fits the shell height and the space left on the top side (the 7.3 x 4.3 mm 6TPE330MAP did not, LOGBOOK). 40 mOhm each, 6.3 V (79 % worst case). Carries 2-2.5 ms TX frames; console safety does not depend on its size. |
| Power-good on 4.2 V / off 2.0 V | The radio is either properly powered or completely off; never browns out (ESP-IDF issue #17881: a C3 brown-out mid-TX can leave the RF stuck on). |
| TPS63802 buck-boost | Keeps 3.3 V down to ~1.3 V input, so most of the reservoir's charge is usable. |
| ESP32-C3-MINI-1U | In JLC stock, native USB for flashing, u.FL to a stick-on antenna in the shell's grip (no soldering). TX peak 350 mA is absorbed by the reservoir. |
| Radio firmware owns its power policy | `firmware/esp32c3/components/power_mgr`: per-frame TX admission from reservoir energy, modem/light-sleep, controlled park, cart-rail watch with hysteresis. Co-simulated as shipped C code (`sim/power/cosim.py`, audit A6). Hard rules: full RF calibration only on USB, 11b data rates off, small MSS. |

## RP2350 pin budget

Croco V2 uses all 48 GPIOs of the RP2350B (mapped from the PCB netlist):
- GB bus: GPIO17-43 (CLK, RD, CS, A0-A15, D0-D7), GPIO44 bus enable, GPIO45 GB reset
- HyperRAM: GPIO6-16 · SD/RTC SPI0: GPIO0-3 + RTC CS GPIO5 · button GPIO4
- GPIO46 debug UART TX (defmt-serial) · GPIO47 WS2812 status LED

Link choice: **ESP32-C3 as an SPI slave on the existing SPI0 bus, chip select on GPIO46.**
- Costs only the debug UART. Logging moves to defmt-RTT over the SWD Tag-Connect that is already on the board.
- Keeps the save-status LED, the RTC and the SD card exactly as they are.
- The ESP's MISO goes through a 74LVC1G125 tri-state buffer enabled by its chip select, so it can never fight the SD card or RTC on the shared line (the ESP slave's MISO behaviour when deselected is not something to rely on).
- No interrupt line left, so the RP2350 polls the ESP over SPI (e.g. every 1ms or once per frame). Simple and deterministic.

Alternative kept in reserve: half-duplex single-wire UART on GPIO46 (PIO on the RP2350, open-drain on the ESP). Fewer parts, lower bandwidth, fiddlier.

## Flashing the radio without soldering

The ESP32-C3 has native USB (Serial/JTAG), and esptool can put it into download mode over USB with no strap pins. A CH334P USB 2.0 hub sits behind Croco's USB-C, so the RP2350 and the ESP32-C3 both enumerate when plugged in (flash either from a browser). After the first flash, the radio can also be updated over WiFi (OTA).

## Software

- Fork of `shilga/rp2350-gameboy-cartridge-firmware` (Rust/Embassy, GPL-3): add the SPI master driver for the ESP link and serve the mailbox to the Game Boy. Mailbox protocol (fixed and simulated): MBC5 cart-RAM bank 15, see `sim/system/README.md`. Not written yet.
- New ESP32-C3 firmware: SPI slave mailbox, WiFi, power policy (`firmware/esp32c3/components/power_mgr`, written and simulated), OTA. The rest is not written yet.
- Game Boy side: test ROMs in RGBDS (`sim/system/rom`: `wifitest.asm` gate ROM, `btcdemo.asm` WiFi scan + password + live BTC chart); later a GBDK-2020 library and demos (Wikipedia, chat, ROM download).
