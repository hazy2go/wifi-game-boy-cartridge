# Rev B architecture: Croco Cartridge V2 + WiFi

Status: design in progress (2026-10-14). Nothing here has been built yet. See `LOGBOOK.md` for how each decision was reached.

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
                   │   NEW (bottom side)
                   └─► power mux (prefers USB) ─► TPS2553 (150mA cap) ─► BQ24075 (holds input >= 4.5V)
                        ─► 1.4mF polymer reservoir ─► TPS63802 buck-boost ─► 3.3V radio rail ─► ESP32-C3-MINI-1
                                                                                 │
   RP2350 SPI0 (GPIO0 MISO / GPIO2 SCK / GPIO3 MOSI, shared with SD + RTC) ◄────┤ SPI slave
   RP2350 GPIO46 (was debug UART TX) ─── ESP chip select ───────────────────────┘
```

## Why each block

| Block | Why |
|---|---|
| Croco V2 untouched on the top side | Proven on DMG and GBC, already JLC-assembly ready. Every Croco part sits on the top; the bottom is empty, so the radio lives there without moving anything that works. |
| Power stage taps the raw cart VCC, **before** Croco's D2 Schottky | The input-voltage regulation has to see the real console rail. Behind the diode it would see ~0.3V less and fold back on healthy consoles. |
| Power mux (prefers USB) | Lets the radio run (and be flashed) on USB alone, and when both are present takes power from USB so the console is spared. Part being selected (TPS2116 class ideal-diode mux). |
| TPS2553 at 150mA | Smooth, constant ceiling on what the cartridge can ever take for the radio, inrush-free hot-plug. |
| BQ24075 in USB500 mode | Datasheet-guaranteed VIN-DPM loop: cuts current so its input never falls below 4.5V typ (4.35-4.63V). Only works in USB modes (EN2 low), checked in datasheet. |
| Precision 4.45V comparator on the charge path | Hard floor that covers the BQ24075's 4.35V worst-case tolerance. |
| 1.4mF reservoir (3x Panasonic 6TPE470MI) | Carries 2-12ms TX bursts. Polymer: 18 mOhm ESR each, no supercap aging. |
| TPS63802 buck-boost | Keeps 3.3V down to ~1.6V reservoir, so the reservoir's whole charge is usable. |
| ESP32-C3-MINI-1 | In JLC stock, RISC-V, native USB for flashing, mature IDF/Arduino. Datasheet peak 335mA is absorbed by the reservoir. |
| Radio firmware owns its power policy | It reads its reservoir and the cart rail on its own ADC: below 4.0V reservoir no new TX bursts, below 3.5V modem-sleep, cart rail below 4.55V back off. Simulated in `sim/power`. |

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

The ESP32-C3 has native USB (Serial/JTAG), and esptool can put it into download mode over USB with no strap pins. Preferred: a small USB 2.0 hub chip behind Croco's USB-C so the RP2350 and the ESP32-C3 both enumerate when plugged in (flash either from a browser). Fallback: an analog USB switch with a slide switch. Part choice pending. After the first flash, the radio can also be updated over WiFi (OTA).

## Software

- Fork of `shilga/rp2350-gameboy-cartridge-firmware` (Rust/Embassy, GPL-3): add the SPI master driver for the ESP link, expose a mailbox to the Game Boy as memory-mapped registers in a free cartridge address window.
- New ESP32-C3 firmware: SPI slave mailbox, WiFi, power policy, OTA.
- Game Boy side: a small library (GBDK-2020) for the mailbox, then demos (Wikipedia, chat, ROM download).
