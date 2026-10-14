/* WiFi cartridge app template (GBDK-2020): a text viewer for a URL.
 *   A = fetch URL, UP/DOWN = scroll the answer, B = POWER (the cart's rail and reservoir).
 * The non-blocking pattern: wifi_submit() once, then wifi_poll() every frame, so the UI keeps running while the
 * cartridge works (a weak console can take seconds; the cart answers within 12 s, see docs/ROM_DEV.md).
 * Copy this folder, rename, change URL and the drawing. Test without hardware: ./test.sh */
#include <gb/gb.h>
#include <gbdk/console.h>
#include <stdio.h>
#include <string.h>

#include "../lib/wifi.h"

#define URL "https://example.org/hello.txt"
#define ROWS 14 /* rows 3..16 show the answer */

static uint8_t top;     /* first answer line shown */
static uint8_t waiting; /* a request is in flight */

/* GBDK's printf has no field widths (%-20s prints literally): pad by hand */
static void print_row(uint8_t y, const char *s)
{
    uint8_t n = 0;
    gotoxy(0, y);
    for (; s[n] && n < 20; n++)
        putchar(s[n]);
    for (; n < 20; n++)
        putchar(' ');
}

static void clear_rows(uint8_t from, uint8_t to)
{
    for (uint8_t y = from; y <= to; y++)
        print_row(y, "");
}

static void show_answer(void)
{
    char line[21];
    clear_rows(3, 3 + ROWS - 1);
    for (uint8_t i = 0; i < ROWS; i++) {
        if (!wifi_line(top + i, line, sizeof line) && top + i >= wifi_line_count())
            break;
        print_row(3 + i, line);
    }
}

static void status(const char *s)
{
    print_row(1, s);
}

void main(void)
{
    printf("WIFI TEMPLATE\n");
    status("A:GET B:POWER");
    gotoxy(0, 17);
    printf("UP/DOWN: SCROLL");
    wifi_open();
    uint8_t prev = 0;
    for (;;) {
        wait_vbl_done();
        uint8_t keys = joypad(), pressed = keys & ~prev;
        prev = keys;
        if (waiting) {
            uint8_t st = wifi_poll();
            if (st == WIFI_PENDING)
                continue; /* the UI could animate here */
            waiting = 0;
            top = 0;
            status(st == WIFI_DONE ? "DONE" : "ERROR");
            show_answer(); /* an error answer carries its reason, e.g. ERR NOT CONNECTED */
        } else if (pressed & J_A) {
            wifi_submit(WIFI_GET, URL);
            waiting = 1;
            status("WORKING...");
        } else if (pressed & J_B) {
            wifi_submit(WIFI_POWER, NULL);
            waiting = 1;
            status("WORKING...");
        } else if ((pressed & J_DOWN) && top + ROWS < wifi_line_count()) {
            top++;
            show_answer();
        } else if ((pressed & J_UP) && top) {
            top--;
            show_answer();
        }
    }
}
