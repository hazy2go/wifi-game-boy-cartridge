/* Test transcript for the RP2350 SPI master (croco-fw2 src/esp_link.rs).
 *
 * Runs the reference master and the shipped slave (firmware/esp32c3/components/esp_link) through a scripted
 * scenario, 1 ms per tick, and writes every event to out/esp_link_vectors.txt:
 *   W <ms> <off> <val>          the ROM writes a mailbox byte (hex), before the master's tick
 *   X <ms> <mosi hex> <miso hex> one transaction: the master must send exactly <mosi>, then gets <miso>
 *   R <n>                        the previous X line repeats n more times, at ms + 1, + 2, ...
 *   M <ms> <hex>                 mailbox bytes 0x00-0x0F after the tick (written when they change)
 *   P <ms> <hex>                 response area from 0x100 up to and including the 0 terminator (when ACK changes)
 * Scenario: ESP still booting, empty payload, corrupted response body, max-size request and response,
 * corrupted request, ESP restart mid-job, no answer (RADIO TIMEOUT), ESP gone (NO RADIO POWER), SEQ wrap.
 * Build + run: see sim/system/README.md.
 */
#include <stdio.h>
#include <string.h>

#include "esp_link.h"

static uint8_t mb[0x2000];
static el_master_t m;
static el_slave_t s;
static uint8_t stx[EL_MAX_XFER];
static int live;
static FILE *out;

/* fake ESP application: answer job seq after a delay (or never) */
static int app_seq = -1, app_at = -1, app_never;
static char app_resp[EL_MAX_RESP + 1];

static char last_x[2 * 2 * EL_MAX_XFER + 8];
static int repeat;

static void hex(char *o, const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        sprintf(o + 2 * i, "%02x", p[i]);
    o[2 * n] = 0;
}

static void flush_repeat(void)
{
    if (repeat)
        fprintf(out, "R %d\n", repeat);
    repeat = 0;
}

static void rom_write(uint32_t ms, int off, uint8_t v)
{
    flush_repeat();
    last_x[0] = 0;
    mb[off] = v;
    fprintf(out, "W %u %x %02x\n", ms, off, v);
}

static void rom_request(uint32_t ms, uint8_t cmd, const char *payload, uint8_t seq)
{
    rom_write(ms, MB_CMD, cmd);
    size_t n = strlen(payload);
    for (size_t i = 0; i <= n; i++)
        rom_write(ms, MB_REQ + (int)i, (uint8_t)payload[i]);
    rom_write(ms, MB_SEQ, seq);
}

static int arm_mosi; /* corrupt the next REQUEST on its way to the slave */

static void xfer(uint32_t ms, int corrupt_miso)
{
    uint8_t mosi[EL_MAX_XFER], miso[EL_MAX_XFER];
    size_t n = el_master_next(&m, ms, mb, mosi);
    if (live)
        memcpy(miso, stx, n);
    else
        memset(miso, 0, n);
    char a[2 * EL_MAX_XFER + 1], b[2 * EL_MAX_XFER + 1], line[sizeof last_x];
    hex(a, mosi, n);
    if (corrupt_miso)
        miso[n - 1 > EL_HDR ? EL_HDR + 1 : 3] ^= 0x10;
    hex(b, miso, n);
    snprintf(line, sizeof line, "%s %s", a, b);
    if (!strcmp(line, last_x)) {
        repeat++;
    } else {
        flush_repeat();
        fprintf(out, "X %u %s\n", ms, line);
        strcpy(last_x, line);
    }
    uint8_t rx[EL_MAX_XFER];
    memcpy(rx, mosi, n);
    if (arm_mosi && mosi[1] == EL_REQUEST) {
        rx[EL_HDR + 1] ^= 0x01;
        arm_mosi = 0;
    }
    if (live) {
        el_slave_on_rx(&s, rx, n);
        el_slave_build_tx(&s, stx);
    }
    el_master_done(&m, ms, mb, miso, n);
}

int main(int argc, char **argv)
{
    out = fopen(argc > 1 ? argv[1] : "out/esp_link_vectors.txt", "w");
    el_master_init(&m, mb, 0);
    el_slave_init(&s);
    el_slave_build_tx(&s, stx);
    uint8_t mb_prev[MB_REQ] = {0}, ack_prev = 0;
    char big[EL_MAX_REQ + 1];
    memset(big, 'A', EL_MAX_REQ);
    big[EL_MAX_REQ] = 0;
    int bad_miso_done = 0;

    for (uint32_t ms = 0; ms <= 18300; ms++) {
        /* ---- script */
        if (ms == 20) live = 1;                                         /* ESP booted */
        if (ms == 5) rom_request(ms, 1, "", 1);
        if (ms == 60) rom_request(ms, 3, "http://example.com/hello", 2);
        if (ms == 120) rom_request(ms, 5, big, 3);
        if (ms == 200) { arm_mosi = 1; rom_request(ms, 4, "xy", 4); }
        if (ms == 300) rom_request(ms, 3, "http://example.com/a", 5);
        if (ms == 302) { el_slave_init(&s); el_slave_build_tx(&s, stx); live = 0; app_seq = -1; }   /* ESP reset */
        if (ms == 320) live = 1;
        if (ms == 400) { app_never = 1; rom_request(ms, 3, "http://slow.example/", 6); }
        if (ms == 13000) { live = 0; app_never = 0; }                    /* radio gone */
        if (ms == 13001) rom_request(ms, 1, "", 7);
        if (ms == 18100) { el_slave_init(&s); live = 1; }
        if (ms == 18110) rom_request(ms, 1, "", 0);                      /* SEQ wraps to 0 */

        /* ---- fake ESP application (runs between transactions, like the ESP's 1 ms task) */
        if (live) {
            el_slave_set_live(&s, ms > 40, ms > 40 ? 2 : 1, 4.62f, ms < 200 ? 3.5f : 3.3f);
            uint8_t seq, cmd;
            char pl[EL_MAX_REQ + 1];
            if (el_slave_take_job(&s, &seq, &cmd, pl)) {
                app_seq = seq;
                app_at = (int)ms + (cmd == 5 ? 7 : 3);
                if (cmd == 1) snprintf(app_resp, sizeof app_resp, "PONG");
                else if (cmd == 3) snprintf(app_resp, sizeof app_resp, "Hello from WiFi!");
                else if (cmd == 4) snprintf(app_resp, sizeof app_resp, "RAIL 4.62V RES 3.30V");
                else { memset(app_resp, 'z', EL_MAX_RESP); app_resp[EL_MAX_RESP] = 0; }
            }
            if (app_seq >= 0 && (int)ms >= app_at && !app_never) {
                el_slave_respond(&s, (uint8_t)app_seq, 2, (const uint8_t *)app_resp, strlen(app_resp));
                app_seq = -1;
            }
            el_slave_build_tx(&s, stx);
        }

        /* ---- master tick */
        int k = 0;
        do {
            int cs = 0;
            if (ms >= 60 && ms < 120 && m.fetch_due && !bad_miso_done) { cs = 1; bad_miso_done = 1; }
            xfer(ms, cs);
        } while (el_master_more(&m) && ++k < 2);

        if (memcmp(mb_prev, mb, MB_REQ)) {
            flush_repeat();
            last_x[0] = 0;
            char h[2 * MB_REQ + 1];
            hex(h, mb, MB_REQ);
            fprintf(out, "M %u %s\n", ms, h);
            memcpy(mb_prev, mb, MB_REQ);
        }
        if (mb[MB_ACK] != ack_prev) {
            flush_repeat();
            last_x[0] = 0;
            size_t n = strlen((char *)mb + MB_RESP) + 1;
            char h[2 * 256 + 1];
            hex(h, mb + MB_RESP, n);
            fprintf(out, "P %u %s\n", ms, h);
            ack_prev = mb[MB_ACK];
        }
    }
    flush_repeat();
    fprintf(stderr, "master: %u xfers, %u bad hdr, %u bad body, %u resent; slave: %u ok, %u bad\n", m.xfers, m.bad_hdr,
            m.bad_body, m.resent, s.rx_ok, s.rx_bad);
    fclose(out);
    return 0;
}
