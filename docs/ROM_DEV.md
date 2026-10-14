# Building ROMs for the WiFi cartridge

This guide is for anyone writing a Game Boy ROM that uses the cartridge's WiFi, agents included. Everything here is checked by gate S1 (`sim/system/run_system.py`). The hardware is not built yet, so the simulator is the reference.

## 1. What the ROM sees

The cartridge is a flash cart (RP2350, Croco Cartridge V2.1) with an ESP32-C3 radio. ROMs live on its microSD card, and the boot menu loads one into RAM. A ROM that **opts in** gets a **mailbox**: an 8 KB cart-RAM bank that the ROM writes a request into. The cartridge passes the request to the radio and writes the answer back into the same bank.

A ROM opts in when it has all three:
- the 8 bytes `WIFICART` anywhere in ROM bank 0 (linking either library adds them);
- an MBC5 header (0x147 = 0x19-0x1E);
- 128 KB of cart RAM (0x149 = 0x04).

Any other ROM gets plain save RAM, and bank 15 is never touched.

## 2. Quick start

**C (GBDK-2020 4.5.0)**: copy `gb/gbdk/template/`, then `make`, then `./test.sh`, which runs your ROM on the simulated cart.
```c
#include "../lib/wifi.h"            // gb/gbdk/lib
wifi_open();                         // once: cart RAM on, bank 15
if (wifi_get("https://example.org/hello.txt") == WIFI_DONE)
    printf("%s", wifi_response());   // <= 255 chars, 0-terminated
// without blocking: wifi_submit(cmd, payload) once, then wifi_poll() every frame
```
Build flags: `lcc -Wm-yt0x1B -Wm-ya16` (MBC5+RAM+BATTERY, 16 RAM banks).

**Assembly (RGBDS)**: `INCLUDE "wifi.inc"` and link `gb/rgbds/wifi.asm`. Your ROM must export `WaitVBlank`. Example: `gb/rgbds/example/` (`make`). Header: `rgbfix -m MBC5+RAM+BATTERY -r 0x04`.
```asm
    call WifiOpen
    ld a, WIFI_GET
    ld hl, sUrl                      ; 0-terminated URL
    ld bc, WIFI_FRAMES_DEFAULT
    call WifiRequest                 ; A = WIFI_DONE / WIFI_ERROR / WIFI_TIMEOUT
    ld hl, WIFI_MB_RESP              ; the answer
```

## 3. The mailbox (cart RAM bank 15, A000-BFFF)

| addr | who writes | meaning |
|---|---|---|
| A000 | ROM | CMD (section 4) |
| A001 | ROM | SEQ. **Write it last and change it for every request** (increment); the cartridge spots a request by this value changing |
| A002 | cart | STATUS: 1 busy, 2 done, 3 error |
| A003 | cart | ACK: the SEQ this answer belongs to. It is written last, so ACK == your SEQ and STATUS >= 2 means the answer is complete |
| A004/5 | cart | answer length, little endian |
| A006 / A007 | cart | live cart rail / reservoir, volts x 50 (0 = radio not reporting) |
| A010-A0FF | ROM | payload, 0-terminated, at most 240 bytes. The cart clears it after each answer (it may hold a WiFi password) |
| A100-A1FF | cart | answer, 0-terminated, at most 255 bytes |
| A200-BFFF | | free, but do not keep game data in bank 15 |

Rules:
- **One request at a time.** A new SEQ replaces the request in flight; its answer is then discarded.
- At cart start the cartridge clears A000-A1FF. Continue SEQ from the value you find (the libraries do), so a Game Boy soft reset cannot repeat a SEQ.
- Banks 0-14 are normal battery save RAM. Bank 15 also lands in the `.sav` file.
- After `SWITCH_RAM(n)` for your own saves, call `wifi_open()` again before the next request.

## 4. Commands

| CMD | payload | answer (status 2) |
|---|---|---|
| 01 PING | none | `PONG`, answered by the radio without WiFi |
| 02 TIME | none | `YYYY-MM-DD HH:MM:SS`, UTC from NTP |
| 03 GET | URL, `http://` or `https://` (public CAs) | the first 255 bytes of the body; bytes outside ASCII 32-126 except `\n` become `?` |
| 04 POWER | none | `RAIL 4.59V RES 3.50V` (cart rail, reservoir) |
| 05 SCAN | none | up to 8 lines `1 MyNetwork  -45 W2` (number, SSID cut to 10, RSSI dBm, OP/WE/W1/W2/W3), strongest first; or `NO NETWORKS`. Passive scan, ~1.6 s |
| 06 CONNECT | `n\|password`, n = SCAN line 1-8, password empty for open networks | `OK 192.168.1.23`. The credentials are stored; the radio reconnects by itself after power-up and drops |
| 07 BTC | none | 3 text lines + 128 chart bytes (0x20 + 0..63), see `sim/system/rom/btcdemo.asm` |

**Error answers** (status 3; the text is in A100, so show it):

| text | meaning, what to do |
|---|---|
| `ERR NOT CONNECTED` | no WiFi yet: run SCAN + CONNECT once (it is remembered) |
| `ERR RADIO NOT READY` | the radio is saving energy (weak console) and did not get going within 1 s: try again |
| `ERR NEEDS USB CAL` | brand-new cart: plug it into USB once so the radio can calibrate |
| `ERR NO CONNECTION`, `ERR HTTP 404`, `ERR NTP`, `ERR BAD DATA` | network / server side |
| `ERR CONNECT FAILED`, `WPA2 KEY < 8 CHARS`, `BAD NETWORK` | CONNECT: wrong password, short key, bad line number |
| `NO RADIO POWER`, `RADIO NOT ACCEPTING` | from the cartridge: the radio did not take the request within 5 s (it is off, booting or parked) |
| `RADIO TIMEOUT` | from the cartridge: no answer within 12 s |
| `ERR UNKNOWN CMD n` | the command does not exist |

## 5. Timing and power: design the UI for it

The radio runs on the console's 5 V through a current limit (150 mA) and a ~1 mF reservoir. The cartridge never lets the console drop below 4.5 V, so **on a weak console WiFi gets slow, not unsafe**. Measured in the simulator:

| console | first answer after power-up | a GET |
|---|---|---|
| fresh (0.7 ohm) | ~0.4 s (radio boot + join) | ~0.1-0.2 s |
| weak (2.0 ohm) | ~0.4-0.6 s | ~0.2 s; bursts of requests slow down |
| very weak (2.5 ohm) | seconds; the radio parks and refills often | seconds |

So:
- **Use the non-blocking pattern** (`wifi_submit` + `wifi_poll` per frame) and show a "working" state. Never assume an answer within a frame.
- Wait at least 13 s before giving up (`WIFI_FRAMES_DEFAULT`). The cart answers or fails within 12 s.
- **Keep answers small:** 255 bytes is the whole answer. For more, fetch several URLs, or put a small server or proxy in front that returns compact text (the BTC command shows the idea: the radio turns a 14 KB JSON into 128 chart bytes).
- Fewer, smaller requests are faster on weak consoles, because every frame the radio sends has to fit in the reservoir.
- The radio only exists on this cartridge. In a PC emulator bank 15 is plain RAM, so nothing ever answers: check for the radio once at start with a short wait, e.g. `wifi_request(WIFI_PING, NULL, 60 * 6)`, and fall back to an offline mode on `WIFI_TIMEOUT`. Six seconds leaves room for a slow radio start on a weak console.

## 6. Testing without hardware

```
python3 sim/system/run_rom.py my.gb --console weak --seconds 15 \
    --input "F60 A F300" --fixtures urls.json --expect expect.txt
```
- **What runs:** your ROM in an emulated DMG (Peanut-GB), the cartridge firmware (mailbox and SPI link byte for byte, the same protocol code as the firmware), the radio's shipped C components (link, power manager, answer texts) and the power path. It prints the screen as text and writes `screen.png`, `log.txt` (every request and answer, with timing) and `verdict.json`.
- **Exit code 1** if your expectations fail, or if the power path breaks a rule (console < 4.5 V, radio brown-out).
- `--input`: buttons U D L R A B S(tart). `Fn` waits n frames; `Wn` waits until WRAM $C000 == n (a state byte your ROM can keep there); `P` takes a screenshot.
- `--expect`: expected screen rows, top to bottom, blank rows skipped; `/regex/` rows match a regular expression.
- **GET in the simulator:**
  - `http(s)://example.com/...` answers `Hello from WiFi!` for `/hello`, otherwise `ok`.
  - `--fixtures urls.json` (`{"url": "body"}` or `{"url": {"status": 404}}`) answers from the file only.
  - Without fixtures, URLs are fetched live from this computer; `--offline` gives `ERR NO CONNECTION`.
- **SCAN** lists the networks this Mac sees (macOS hides their names). **CONNECT** to line n always succeeds in the sim.
- **Not simulated:** the real radio (RF, range, AP behaviour), and the timing of the real chips (the boot time and calibration are assumptions until the dev-board measurements). The font is auto-detected (`--font ascii|gbdk`).

The whole system gate is `python3 sim/system/run_system.py`, and it must stay ALL PASS.

## 7. Putting ROMs on the cart

FAT32 microSD card, files in the root folder, `.GB` or `.GBC`, 8.3 names (`MYAPP.GB`), up to 255 ROMs (the menu stores the count in one byte). Pick a ROM in the menu with A. Saves go to the SD card when you press the cart's button.

## 8. Adding a command (radio side)

A new command touches every layer, and each layer has a test:
1. `firmware/esp32c3/components/gbwifi`: the answer formatting / parsing, as plain C, with a host test in `sim/system/test_gbwifi.c` (gate S1.8).
2. `firmware/esp32c3/app/main/cmds.c`: the handler, within the job budget (`left_ms()`), answering with `el_slave_respond`.
3. `sim/system/sysim.c`: `plan_op()` (the radio traffic, frame by frame) and `esp_finish()` (the answer, using the gbwifi function).
4. The command tables: `sim/system/README.md`, this file, `gb/gbdk/lib/wifi.h`, `gb/rgbds/wifi.inc`.
5. Run `run_system.py` and `run_rom.py` with a test ROM, then log the decision in `docs/LOGBOOK.md`.

**Do not change the SPI link protocol casually.** If you must, change the C reference (`components/esp_link`), regenerate the vectors (`sim/system/el_vectors.c`), copy them to the firmware fork's `tests-host/tests/`, and update the spec (`docs/ARCHITECTURE.md`). The hard power rules for radio code are in `CLAUDE.md` (step 4) and `firmware/esp32c3/app/README.md`.
