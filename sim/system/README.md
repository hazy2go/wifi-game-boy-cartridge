# Full-system simulation (gate S1)

Hazy's gate before ordering (LOGBOOK 14:57): a real Game Boy ROM, running in an emulated Game Boy, using the
new parts through the cartridge.

```
rom/wifitest.asm (RGBDS)  ->  Peanut-GB (third_party, MIT) runs it frame by frame
   cart bus  ->  sysim.c: MBC5 ROM/RAM as the RP2350 serves it; RAM bank 15 = WiFi mailbox
   RP2350 model: sees the SEQ write (Croco's write-sniff DMA), frames the request over SPI at 8 MHz
   ESP32-C3 model: command handlers + WiFi traffic as TX/RX frames; every TX frame goes through the SHIPPED
                   power manager (firmware/esp32c3/components/power_mgr/power_mgr.c, compiled in)
   power plant: console rail (Thevenin), input regulator (behavioural, A8-audited), 150 mA limit,
                3 x T520B reservoir, power-good 4.2 / 2.0 V, TPS63802 efficiency, ESP current by radio state
```

Run: `python3 run_system.py` (needs `rgbds`, a C compiler, Pillow). Output in `out/`: screen PNGs, tile maps,
event logs, `report.txt`.

## Mailbox protocol (cart RAM bank 15, A000-BFFF)
| addr | meaning |
|---|---|
| A000 | CMD: 01 PING, 02 TIME (UTC via NTP), 03 HTTP GET (payload = URL), 04 POWER |
| A001 | SEQ: the ROM writes it last; this write submits the request |
| A002 | STATUS: 0 idle, 1 busy, 2 done, 3 error |
| A003 | ACK: SEQ of the answered request |
| A004/5 | response length |
| A006 / A007 | console rail / reservoir in 20 mV steps (live) |
| A010-A0FF | request payload, 0-terminated |
| A100-A1FF | response, 0-terminated |

The ROM enables cart RAM (0x0A to 0000), selects RAM bank 15 (4000) and polls ACK/STATUS once per frame.
On the real cart the mailbox is a region the RP2350 already serves from memory by DMA (like save RAM); the
firmware only reacts to the logged SEQ write. **The croco-fw2 side of this is not written yet**; the simulator
models it.

## What the test ROM does
PING, TIME, GET /hello, POWER, then 20 GETs back to back, then POWER again; prints every answer.

## What is real and what is modelled
- Real: the ROM (assembled, runs in a cycle-counting DMG emulator that passes Blargg's CPU tests), the radio power
  manager C code, the mailbox protocol as the ROM uses it.
- Modelled: RP2350 firmware (transaction level, no PIO timing; Croco's bus timing is proven hardware), ESP-IDF
  and the WiFi stack (frames + assumed round-trip times), the network (local server, fixed answers), the input
  regulator (behavioural; the transistor-level loop is audit A8 in `sim/power`).
- Timing granularity: one video frame for the emulator, 1 ms for firmware, 2 us for the power plant.
