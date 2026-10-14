---
name: wifi-cart-rom
description: Build, test or debug a Game Boy ROM that uses the WiFi cartridge (mailbox, GET/TIME/SCAN/CONNECT, GBDK or RGBDS), or add a new WiFi command. Use whenever someone wants an app/game/tool ROM for this cart.
---

# WiFi cartridge ROM development

1. Read `docs/ROM_DEV.md` first: the opt-in rules, the mailbox, the commands and their exact answer/error texts, the timing and power behaviour, testing and limits.
2. Start from a working example; don't write the mailbox code yourself:
   - C: copy `gb/gbdk/template/` (GBDK-2020 4.5.0 at `~/opt/gbdk`, `make`, `./test.sh`), library `gb/gbdk/lib/wifi.h`.
   - Assembly: `gb/rgbds/wifi.inc` + `wifi.asm`, example `gb/rgbds/example/`.
3. The ROM must be MBC5 + 128 KB RAM, and must link the library (it adds `WIFICART`). Keep game saves in RAM banks 0-14.
4. Use `wifi_submit` + `wifi_poll` once per frame with a visible waiting state. Answers are at most 255 bytes, one request at a time, and may take seconds on a weak console. Always show the error text (it is in the response).
5. Test with `python3 sim/system/run_rom.py ROM.gb --console weak --input "..." --fixtures urls.json --expect expect.txt`. Test the fresh and the weak console. Look at `screen.png` and `log.txt`. Exit code 0 is required.
6. Adding a radio command: follow `docs/ROM_DEV.md` section 8. Never change the SPI link protocol or the power rules without re-running `sim/system/run_system.py` (it must stay ALL PASS) and logging in `docs/LOGBOOK.md`.
