# GBDK-2020 library for the WiFi cartridge

`lib/wifi.h` + `lib/wifi.c`: the mailbox (cart RAM bank 15) from C. Built and tested with GBDK-2020 4.5.0.

```c
#include "wifi.h"
wifi_open();                                         // cart RAM on, bank 15
if (wifi_request(WIFI_GET, "http://example.com/hello", 60 * 15) == WIFI_DONE)
    printf("%s", wifi_response());
// or without blocking: wifi_submit(...), then wifi_poll() once per frame
```

- Build your ROM as **MBC5 with 128 KB RAM**: `lcc -Wm-yt0x1B -Wm-ya16 ...`. Bank 15 is the mailbox, so a game keeps its own saves in banks 0-14.
- Linking the library puts `WIFICART` into bank 0, which is what makes the cartridge serve the mailbox to this ROM.
- `wifi_open()` continues from the SEQ value in the mailbox, so a Game Boy soft reset cannot repeat a SEQ that the cartridge would ignore.
- Commands and answers: `wifi.h` and `sim/system/README.md`. An error answer (`WIFI_ERROR`) carries its reason in `wifi_response()`, e.g. `ERR NOT CONNECTED`, `NO RADIO POWER`.

Demo: `make` builds `build/wifi_demo.gb` (PING, TIME, GET, POWER). Gate S1.9 runs it in the full-system simulation on the fresh and the weak console (`sim/system/run_system.py`). Not run on hardware yet.
