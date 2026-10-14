/* Soak test ROM: requests in an endless loop (PING, TIME, GET, POWER), counting the outcomes.
 * Run long in the simulator (sim/system/run_system.py S1.15, or run_rom.py --seconds 1800). Screen:
 *   SOAK  <n requests>
 *   OK <n>  ERR <n>  TO <n>   (TO = no answer within 13 s)
 *   last error text */
#include <gb/gb.h>
#include <gbdk/console.h>
#include <stdio.h>

#include "../lib/wifi.h"

static uint16_t n_ok, n_err, n_to, n_req;

static void row(uint8_t y, const char *s)
{
    uint8_t n = 0;
    gotoxy(0, y);
    for (; s[n] && n < 20; n++)
        putchar(s[n]);
    for (; n < 20; n++)
        putchar(' ');
}

void main(void)
{
    static const uint8_t cmds[4] = {WIFI_PING, WIFI_TIME, WIFI_GET, WIFI_POWER};
    char line[21];
    wifi_open();
    for (;;) {
        uint8_t c = cmds[n_req & 3];
        uint8_t st = wifi_request(c, c == WIFI_GET ? "http://example.com/hello" : NULL, WIFI_FRAMES_DEFAULT);
        n_req++;
        if (st == WIFI_DONE)
            n_ok++;
        else if (st == WIFI_ERROR) {
            n_err++;
            wifi_line(0, line, sizeof line);
            row(3, line);
        } else
            n_to++;
        gotoxy(0, 0);
        printf("SOAK %u     ", n_req);
        gotoxy(0, 1);
        printf("OK %u ERR %u TO %u   ", n_ok, n_err, n_to);
    }
}
