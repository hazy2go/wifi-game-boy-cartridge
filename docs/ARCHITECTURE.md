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
                        ─► TPS63802 buck-boost ─► 3.3V radio rail ─► ESP32-C3-MINI-1U (MHF III, stick-on antenna)
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
| ESP32-C3-MINI-1U | In JLC stock, native USB for flashing, MHF III (W.FL) plug to a stick-on antenna in the shell's grip (no soldering). TX peak 350 mA is absorbed by the reservoir. |
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

## SPI link protocol (RP2350 master, ESP32-C3 slave), version 1

Defined 2026-10-14 (LOGBOOK). Reference code: `firmware/esp32c3/components/esp_link` (plain C: the ESP side ships it, `sysim.c` runs it, and the Rust master is tested against vectors it generates).

**Wire:** SPI mode 0, MSB first, 8 MHz, CS = GPIO46 (active low). There is no IRQ or handshake line, so the protocol is built for that:
- **State, not messages.** Every transaction starts with both sides exchanging a 12-byte header. The ESP's header always describes its current state, so a lost or garbled transaction costs nothing: the next poll reads the state again.
- The ESP prepares its transmit buffer *before* the master clocks, right after every transaction (a buffer already queued for DMA is never touched). Its header therefore shows the state from the end of the previous transaction: a request sent now shows up in the next header, and a response the ESP finishes now shows up one transaction after that.
- The master keeps CS high for **at least 100 us** between ESP transactions, so the slave driver can queue the next buffer. A transaction that hits an unprepared slave fails the magic or CRC check and is simply repeated.
- Every transaction is a multiple of 4 bytes (ESP SPI-slave DMA). Bodies are zero-padded.
- CRC: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, check value 0x29B1 for "123456789"), stored big-endian.

**Master header (MOSI):**
| byte | meaning |
|---|---|
| 0 | magic 0xC5 |
| 1 | type: 0 POLL, 1 REQUEST |
| 2 | flags: bit0 = byte 3 is valid |
| 3 | ack: seq of the last response the master has stored in the mailbox |
| 4 | seq (REQUEST): the mailbox SEQ the Game Boy wrote |
| 5 | cmd (REQUEST): mailbox CMD |
| 6 | len (REQUEST): request body length, 0-240 (no terminator) |
| 7-9 | 0 (reserved) |
| 10-11 | CRC of bytes 0-9 |

REQUEST body (only when len > 0): `len` payload bytes, CRC of the payload, zero pad.

**ESP header (MISO):**
| byte | meaning |
|---|---|
| 0 | magic 0x5C |
| 1 | flags: bit0 RADIO_UP (power manager in RUN), bit1 BUSY (working on `seq`), bit2 RESP (response for `seq` is in the body), bit3 ACCEPTED (byte 8 is valid) |
| 2 | power-manager state (0 OFF ... 4 PARK) |
| 3 | seq of the request being worked on, or of the response |
| 4 | status with RESP: 2 done, 3 error |
| 5 | response length with RESP, 0-255 (no terminator) |
| 6 | cart rail in 20 mV steps (0 = not measured) |
| 7 | reservoir in 20 mV steps |
| 8 | seq of the last request accepted |
| 9 | protocol version (1) |
| 10-11 | CRC of bytes 0-9 |

With RESP and len > 0, the ESP's buffer carries the body right after the header: `len` bytes, CRC, zero pad.

**Master, every 1 ms (and right after a fetch is due):**
1. A new mailbox SEQ was written: the mailbox gets STATUS = 1 (busy), and that request becomes *pending*.
2. If the pending request has not been accepted, send a REQUEST (header + body), and repeat it every 10 ms until a header shows it accepted. Otherwise send a 12-byte POLL that carries the ack.
3. Check the received header (magic, CRC). If it is valid, copy the rail and reservoir to mailbox A006/A007.
   - ACCEPTED with byte 8 = pending seq: the request counts as accepted.
   - If it is accepted but the ESP shows neither BUSY nor RESP for it (the ESP restarted), clear "accepted" so the REQUEST is sent again.
4. RESP for the pending seq: fetch it with one POLL of 12 + body bytes. Store the response at A100, a 0 terminator, the length at A004/5 and the status at A002, then write **ACK (A003) last**. That seq is now the ack sent in the next POLL.
5. Timeouts: no valid header for 5 s while a request is pending gives STATUS 3 "NO RADIO POWER". Accepted but no response within 12 s gives STATUS 3 "RADIO TIMEOUT" (the ESP's own job deadline is 8 s).

**ESP:**
- On a valid REQUEST whose seq is not the current job: start it (this drops any older job or response) and set ACCEPTED/seq.
- A REQUEST repeated with the current seq is ignored.
- On a POLL whose ack equals the response seq: drop the response.
- Garbage transactions are ignored.

**Timing at 8 MHz:** a poll takes 12 us. The longest request is 12 + 244 bytes (256 us), the longest response fetch 12 + 260 bytes (272 us). Polling the bus every 1 ms costs about 1.2 % of it, so SD saves are not held up.

## Which ROMs get the mailbox

Only ROMs that ask for it. A ROM needs both:
- the 8 bytes `WIFICART` at ROM address 0x0150;
- an MBC5 header with 128 KB of RAM (0x0149 = 0x04), so that RAM bank 15 exists.

Every other game keeps all 16 banks as ordinary save RAM, so bank 15 of a normal 128 KB save is never overwritten.

## Flashing the radio without soldering

The ESP32-C3 has native USB (Serial/JTAG), and esptool can put it into download mode over USB with no strap pins. A CH334P USB 2.0 hub sits behind Croco's USB-C, so the RP2350 and the ESP32-C3 both enumerate when plugged in (flash either from a browser). After the first flash, the radio can also be updated over WiFi (OTA).

## Software

- Fork of `shilga/rp2350-gameboy-cartridge-firmware` (Rust/Embassy, GPL-3) at `hazy2go/rp2350-gameboy-cartridge-firmware`, branch `wifi`. It has the SPI master for the ESP link (`src/esp_link.rs`) and serves the mailbox (MBC5 cart-RAM bank 15, see `sim/system/README.md`) to WIFICART ROMs. It builds and passes the host tests; not run on hardware yet.
- New ESP32-C3 firmware: SPI slave mailbox, WiFi, power policy (`firmware/esp32c3/components/power_mgr`, written and simulated), OTA. The rest is not written yet.
- Game Boy side: test ROMs in RGBDS (`sim/system/rom`: `wifitest.asm` gate ROM, `btcdemo.asm` WiFi scan + password + live BTC chart); later a GBDK-2020 library and demos (Wikipedia, chat, ROM download).
