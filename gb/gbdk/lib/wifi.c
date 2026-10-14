/* GBDK-2020 library for the WiFi cartridge mailbox. See wifi.h. */
#include "wifi.h"

#include <gb/gb.h>

#define MB ((volatile uint8_t *)0xA000)
#define MB_CMD 0x00
#define MB_SEQ 0x01
#define MB_STATUS 0x02
#define MB_ACK 0x03
#define MB_LENL 0x04
#define MB_LENH 0x05
#define MB_RAIL 0x06
#define MB_RES 0x07
#define MB_REQ 0x10
#define MB_RESP 0x100
#define MAX_REQ 240

/* Opt-in: the cartridge serves the mailbox only to ROMs that carry these 8 bytes in bank 0 (docs/ARCHITECTURE.md).
 * Non-static and referenced below, so the linker keeps it; plain const data lands in bank 0 (_CODE). */
const char wifi_cart_optin[9] = "WIFICART";

static uint8_t s_seq;

void wifi_open(void)
{
    ENABLE_RAM;
    SWITCH_RAM(15);
    /* continue from the mailbox, not from 0: after a Game Boy soft reset the cartridge still holds the old SEQ,
     * and it only sees a request when SEQ changes */
    s_seq = MB[MB_SEQ];
    (void)wifi_cart_optin[0];
}

void wifi_submit(uint8_t cmd, const char *payload)
{
    uint8_t i = 0;
    MB[MB_CMD] = cmd;
    if (payload)
        for (; i < MAX_REQ && payload[i]; i++)
            MB[MB_REQ + i] = payload[i];
    MB[MB_REQ + i] = 0;
    s_seq++;
    MB[MB_SEQ] = s_seq; /* last: this write submits */
}

uint8_t wifi_poll(void)
{
    if (MB[MB_ACK] != s_seq)
        return WIFI_PENDING;
    return MB[MB_STATUS] >= WIFI_DONE ? MB[MB_STATUS] : WIFI_PENDING;
}

uint8_t wifi_request(uint8_t cmd, const char *payload, uint16_t frames)
{
    wifi_submit(cmd, payload);
    while (frames--) {
        uint8_t st = wifi_poll();
        if (st != WIFI_PENDING)
            return st;
        wait_vbl_done();
    }
    return WIFI_TIMEOUT;
}

const char *wifi_response(void)
{
    return (const char *)(MB + MB_RESP);
}

uint16_t wifi_response_len(void)
{
    return MB[MB_LENL] | ((uint16_t)MB[MB_LENH] << 8);
}

uint8_t wifi_rail_20mv(void)
{
    return MB[MB_RAIL];
}

uint8_t wifi_reservoir_20mv(void)
{
    return MB[MB_RES];
}
