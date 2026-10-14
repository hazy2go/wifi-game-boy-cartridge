# Full-system simulation (gate S1)

Hazy's gate before ordering (LOGBOOK 14:57): a real Game Boy ROM, running in an emulated Game Boy, using the
new parts through the cartridge.

```
rom/wifitest.asm (RGBDS)  ->  Peanut-GB (third_party, MIT) runs it frame by frame
   cart bus  ->  sysim.c: MBC5 ROM/RAM as the RP2350 serves it; RAM bank 15 = WiFi mailbox
   RP2350 model: polls the mailbox SEQ every 1 ms and runs the SPI link protocol (docs/ARCHITECTURE.md) byte for
                 byte at 8 MHz through the reference master (firmware/esp32c3/components/esp_link)
   ESP32-C3 model: the SHIPPED SPI-slave half (esp_link.c), command handlers + WiFi traffic as TX/RX frames;
                   every TX frame goes through the SHIPPED power manager (power_mgr.c, compiled in);
                   SPI slave live 300 ms after power-good (assumed boot time)
   power plant: console rail (Thevenin), input regulator (behavioural, A8-audited), 150 mA limit,
                3 x T520B reservoir, power-good 4.2 / 2.0 V, TPS63802 efficiency, ESP current by radio state
```

Run: `python3 run_system.py` (needs `rgbds`, a C compiler, Pillow). Output in `out/`: screen PNGs, tile maps,
event logs, `report.txt`.

## Mailbox protocol (cart RAM bank 15, A000-BFFF)
| addr | meaning |
|---|---|
| A000 | CMD: 01 PING, 02 TIME (UTC via NTP), 03 HTTP GET (payload = URL), 04 POWER, 05 SCAN, 06 CONNECT (payload "n|password"), 07 BTC (3 text lines + 128 chart bytes, 0x20 + 0..63) |
| A001 | SEQ: the ROM writes it last; this write submits the request |
| A002 | STATUS: 0 idle, 1 busy, 2 done, 3 error |
| A003 | ACK: SEQ of the answered request |
| A004/5 | response length |
| A006 / A007 | console rail / reservoir in 20 mV steps (live) |
| A010-A0FF | request payload, 0-terminated |
| A100-A1FF | response, 0-terminated |

The ROM enables cart RAM (0x0A to 0000), selects RAM bank 15 (4000) and polls ACK/STATUS once per frame.
Only ROMs that opt in get the mailbox: `WIFICART` at 0x0150, MBC5, 128 KB RAM (0x0149 = 0x04). The ROM must
write the payload first, then a **new** SEQ value (increment it), because the cartridge spots requests by polling
SEQ for a change: Game Boy RAM writes reach memory by DMA, and no CPU sees them. The cartridge clears A000-A00F
before the game starts. The croco-fw2 side is in the fork (`src/esp_link.rs`).

## SPI link checks
- S1.6: the fresh scenario with bit errors (BER 1e-4) on MOSI and MISO must still show every line correctly.
- S1.7: the same ROM without `WIFICART` must get no mailbox (no SPI transactions).
- `el_vectors.c` writes `out/esp_link_vectors.txt`, a scripted run of the reference master against the shipped
  slave (ESP boot, corrupted body, corrupted request, ESP restart mid-job, RADIO TIMEOUT, NO RADIO POWER, SEQ
  wrap). The Rust master's host test (croco-fw2 `tests-host/`) replays it byte for byte. Build:
  `cc -O2 -std=c11 -I../../firmware/esp32c3/components/esp_link -o el_vectors el_vectors.c ../../firmware/esp32c3/components/esp_link/esp_link.c && ./el_vectors`

## What the test ROM does
PING, TIME, GET /hello, POWER, then 20 GETs back to back, then POWER again; prints every answer.

## What is real and what is modelled
- Real: the ROM (assembled, runs in a cycle-counting DMG emulator that passes Blargg's CPU tests), the radio power
  manager C code, the mailbox protocol as the ROM uses it.
- Modelled: RP2350 firmware (transaction level, no PIO timing; Croco's bus timing is proven hardware), ESP-IDF
  and the WiFi stack (frames + assumed round-trip times), the network (local server, fixed answers), the input
  regulator (behavioural; the transistor-level loop is audit A8 in `sim/power`).
- Timing granularity: one video frame for the emulator, 1 ms for firmware, 2 us for the power plant.

## BTC demo (`run_demo.py`, `rom/btcdemo.asm`)
Scan -> pick a network -> type the password on the on-screen keyboard -> connect -> live BTC/USD chart (last 24 h,
15 min candles) -> A refreshes. `host_net.py` gives the ESP model real inputs: the networks this Mac's radio sees,
this Mac's address, live Binance prices (`BTC_FIXTURE=file.json` replays a recorded series offline). Screenshots:
`out/demo_*.png`. The input script (sysim argv 6) presses the buttons; `wState` ($C000) gates it per screen.
