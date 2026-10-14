/* GBDK-2020 library for the WiFi cartridge mailbox (cart RAM bank 15).
 *
 * Protocol: sim/system/README.md (mailbox table) and docs/ARCHITECTURE.md (which ROMs get the mailbox).
 * Build the ROM as MBC5 with 128 KB RAM:  lcc -Wl-yt0x1B -Wl-ya16 ...   (0x1B = MBC5+RAM+BATTERY, 16 RAM banks)
 * Linking this library puts the "WIFICART" opt-in into bank 0, which tells the cartridge to serve the mailbox.
 *
 * Bank 15 stays selected after every call. A game that also keeps saves in banks 0-14 switches back itself
 * (SWITCH_RAM), and must not use bank 15 for its own data.
 */
#ifndef GB_WIFI_H
#define GB_WIFI_H

#include <stdint.h>

#define WIFI_PING 0x01    /* -> "PONG" */
#define WIFI_TIME 0x02    /* -> "YYYY-MM-DD HH:MM:SS" (UTC, NTP) */
#define WIFI_GET 0x03     /* payload URL -> body, up to 255 bytes, ASCII 32-126 and '\n' */
#define WIFI_POWER 0x04   /* -> "RAIL 4.59V RES 3.50V" */
#define WIFI_SCAN 0x05    /* -> up to 8 lines "1 MyNetwork  -45 W2" */
#define WIFI_CONNECT 0x06 /* payload "n|password" (n = SCAN line, 1-8) -> "OK a.b.c.d" */
#define WIFI_BTC 0x07     /* -> 3 text lines + 128 chart bytes (0x20 + 0..63) */

#define WIFI_PENDING 1
#define WIFI_DONE 2
#define WIFI_ERROR 3      /* the response holds the reason, e.g. "ERR NOT CONNECTED", "NO RADIO POWER" */
#define WIFI_TIMEOUT 0    /* only from wifi_request(): no answer within the given frames */

/* Enable cart RAM and select bank 15. Call once at start (and after switching to other RAM banks). */
void wifi_open(void);

/* Submit a request (payload: 0-terminated, up to 240 bytes, may be NULL) and return at once. */
void wifi_submit(uint8_t cmd, const char *payload);
/* WIFI_PENDING until the answer to the last submitted request is in, then WIFI_DONE or WIFI_ERROR. */
uint8_t wifi_poll(void);
/* Submit and wait, one wait_vbl_done() per frame; WIFI_TIMEOUT after `frames` frames (60 = 1 s). */
uint8_t wifi_request(uint8_t cmd, const char *payload, uint16_t frames);

/* The response (0-terminated) and its length; valid until the next submit. */
const char *wifi_response(void);
uint16_t wifi_response_len(void);

/* Live values the cartridge refreshes every millisecond: volts x 50 (20 mV steps), 0 = not measured. */
uint8_t wifi_rail_20mv(void);
uint8_t wifi_reservoir_20mv(void);

#endif
