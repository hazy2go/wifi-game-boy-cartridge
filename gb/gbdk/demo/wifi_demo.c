/* GBDK-2020 demo for the WiFi cartridge: PING, TIME, GET and POWER through lib/wifi, answers on screen.
 * Build: make (see the Makefile). Also run by gate S1.9 in the full-system simulation. */
#include <gb/gb.h>
#include <stdio.h>

#include "../lib/wifi.h"

static void ask(const char *title, uint8_t cmd, const char *payload)
{
    printf("%s\n", title);
    uint8_t st = wifi_request(cmd, payload, 60 * 15);
    if (st == WIFI_TIMEOUT)
        printf("TIMEOUT\n");
    else
        printf("%s\n", wifi_response()); /* POWER is exactly 20 columns */
}

void main(void)
{
    printf("GBDK WIFI DEMO\n\n");
    wifi_open();
    ask("PING:", WIFI_PING, NULL);
    ask("TIME:", WIFI_TIME, NULL);
    ask("GET /hello:", WIFI_GET, "http://example.com/hello");
    ask("POWER:", WIFI_POWER, NULL);
    printf("\nDONE\n");
}
