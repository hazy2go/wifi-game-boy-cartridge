/* Host test of firmware/esp32c3/components/gbwifi (gate S1.8). Prints FAIL lines and exits 1 on any failure. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "gbwifi.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main(void)
{
    /* airtime: 1 Mbit/s, 192 us preamble, 36 bytes of MAC/LLC/FCS */
    CHECK(GBW_MTU == 340 && fabsf(gbw_airtime_worst_s(GBW_MTU) - (192e-6f + (340 + 36) * 8e-6f)) < 1e-7f, "airtime MTU");
    CHECK(gbw_airtime_worst_s(GBW_MTU) < 3.3e-3f, "MTU frame %.3f ms (A5 covered 4.5 ms)", gbw_airtime_worst_s(GBW_MTU) * 1e3);
    CHECK(gbw_rail_for_pm(true, 0.0f) == 5.0f && gbw_rail_for_pm(false, 4.6f) == 4.6f, "rail for pm");
    CHECK(gbw_usb_trusted(true, 0.02f) && !gbw_usb_trusted(true, 4.6f) && !gbw_usb_trusted(false, 0.0f),
          "USB trusted only with the cart rail below 1 V (D3 leakage can fake ST)");

    /* scan: strongest first, 19 columns max (ROM prints from column 1), names sanitised, 8 lines max */
    gbw_ap_t aps[10] = {
        {"weak", -80, 1, GBW_AUTH_WPA2}, {"VeryLongNetworkName", -40, 6, GBW_AUTH_WPA3}, {"", -60, 11, GBW_AUTH_OPEN},
        {"caf\xc3\xa9", -55, 3, GBW_AUTH_WPA}, {"x", 0, 1, GBW_AUTH_WEP},
        {"a", -70, 1, 3}, {"b", -71, 1, 3}, {"c", -72, 1, 3}, {"d", -73, 1, 3}, {"e", -74, 1, 3},
    };
    char out[256];
    gbw_fmt_scan(out, sizeof out, aps, 10);
    int lines = 0;
    for (char *l = out, *nl; (nl = strchr(l, '\n')); l = nl + 1) {
        CHECK(nl - l <= 19, "scan line %d is %d columns", lines + 1, (int)(nl - l));
        for (char *c = l; c < nl; c++)
            CHECK(*c >= 32 && *c < 127, "non-ASCII in scan line %d", lines + 1);
        lines++;
    }
    CHECK(lines == 8, "scan lines %d", lines);
    CHECK(!strncmp(out, "1 VeryLongNe -40 W3\n", 20), "first line '%.20s'", out);
    CHECK(strstr(out, "(hidden)") && strstr(out, "caf??"), "hidden/sanitised names");
    gbw_fmt_scan(out, sizeof out, aps, 0);
    CHECK(!strcmp(out, "NO NETWORKS"), "empty scan");

    /* connect payload */
    int idx; const char *pass;
    CHECK(gbw_parse_connect("2|password1", 3, &idx, &pass) == 0 && idx == 1 && !strcmp(pass, "password1"), "connect ok");
    CHECK(gbw_parse_connect("1|", 3, &idx, &pass) == 0 && !*pass, "open network");
    CHECK(gbw_parse_connect("1|short", 3, &idx, &pass) == -2, "short key");
    CHECK(gbw_parse_connect("4|password1", 3, &idx, &pass) == -1, "index out of range");
    CHECK(gbw_parse_connect("x|password1", 3, &idx, &pass) == -1 && gbw_parse_connect("1password", 3, &idx, &pass) == -1, "malformed");

    /* power / time */
    gbw_fmt_power(out, sizeof out, 4.588f, 3.5f);
    CHECK(!strcmp(out, "RAIL 4.59V RES 3.50V"), "power '%s'", out);
    gbw_fmt_time(out, sizeof out, 2026, 10, 14, 9, 5, 7);
    CHECK(!strcmp(out, "2026-10-14 09:05:07"), "time '%s'", out);

    /* btc: 3 lines + 128 chart bytes within the 255-byte response, extremes map to 0 and 63 */
    double c[96];
    for (int i = 0; i < 96; i++)
        c[i] = 83000 + 900 * sin(i / 10.0);
    size_t n = gbw_fmt_btc(out, 256, c, 96, 9, 45);
    CHECK(n <= 255, "btc length %zu", n);
    const char *p = out;
    for (int k = 0; k < 3; k++)
        p = strchr(p, '\n') + 1;
    CHECK(strlen(p) == 128, "chart bytes %zu", strlen(p));
    int lo = 99, hi = -1;
    for (int i = 0; i < 128; i++) {
        int v = p[i] - 0x20;
        lo = v < lo ? v : lo;
        hi = v > hi ? v : hi;
    }
    CHECK(lo == 0 && hi == 63, "chart range %d..%d", lo, hi);
    CHECK(strstr(out, "09:45 UTC  15M BARS\n") != NULL, "btc time line");
    CHECK(!strncmp(out, "$82,932  ", 9), "btc price %.12s", out); /* last close 83000 + 900 sin(9.5) */

    /* MSS clamp: a real SYN-ACK (Ethernet II / IPv4 / TCP, MSS 1460 + SACK-permitted + window scale) */
    {
        uint8_t f[14 + 20 + 32] = {0};
        f[12] = 0x08; f[13] = 0x00;
        uint8_t *ip = f + 14, *t = f + 34;
        ip[0] = 0x45; ip[3] = 52; ip[8] = 64; ip[9] = 6;
        ip[12] = 93; ip[13] = 184; ip[14] = 216; ip[15] = 34;           /* src */
        ip[16] = 192; ip[17] = 168; ip[18] = 1; ip[19] = 50;            /* dst */
        t[0] = 0x01; t[1] = 0xBB; t[2] = 0xC0; t[3] = 0x01;             /* 443 -> 49153 */
        t[4] = 0x12; t[5] = 0x34; t[6] = 0x56; t[7] = 0x78; t[11] = 1;   /* seq, ack */
        t[12] = 8 << 4; t[13] = 0x12; t[14] = 0xFF; t[15] = 0xFF;      /* doff 32, SYN+ACK */
        uint8_t opts[12] = {2, 4, 0x05, 0xB4, 4, 2, 1, 3, 3, 7, 0, 0};
        memcpy(t + 20, opts, 12);
        /* full TCP checksum over the pseudo header */
        #define TCPSUM(out) do { uint32_t sum = 0; \
            for (int i = 12; i < 20; i += 2) sum += (uint32_t)(ip[i] << 8 | ip[i + 1]); \
            sum += 6 + 32; \
            for (int i = 0; i < 32; i += 2) if (i != 16) sum += (uint32_t)(t[i] << 8 | t[i + 1]); \
            while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16); \
            out = (uint16_t)~sum; } while (0)
        uint16_t c0; TCPSUM(c0); t[16] = (uint8_t)(c0 >> 8); t[17] = (uint8_t)c0;
        CHECK(gbw_clamp_mss(f, sizeof f, 300) == 1, "clamp SYN-ACK");
        CHECK((t[22] << 8 | t[23]) == 300, "MSS now %d", t[22] << 8 | t[23]);
        uint16_t c1; TCPSUM(c1);
        CHECK((t[16] << 8 | t[17]) == c1, "incremental checksum %04x, full recompute %04x", t[16] << 8 | t[17], c1);
        CHECK(gbw_clamp_mss(f, sizeof f, 300) == 0, "already clamped");
        t[13] = 0x10; /* plain ACK */
        CHECK(gbw_clamp_mss(f, sizeof f, 200) == 0, "non-SYN untouched");
        t[13] = 0x12; ip[9] = 17;
        CHECK(gbw_clamp_mss(f, sizeof f, 200) == 0, "non-TCP untouched");
    }

    /* klines (Binance format), no JSON tree */
    {
        const char *j = "[[1760400000000,\"83000.10\",\"83100.00\",\"82900.00\",\"83050.50\",\"12.3\",1760400899999,\"1.0\",10,\"1\",\"1\",\"0\"],"
                        "[1760400900000,\"83050.50\",\"83200.00\",\"83000.00\",\"83150.25\",\"9.1\",1760401799999,\"1.0\",8,\"1\",\"1\",\"0\"]]";
        double cl[4], ms = 0;
        int n = gbw_parse_klines(j, cl, 4, &ms);
        CHECK(n == 2 && cl[0] == 83050.50 && cl[1] == 83150.25 && ms == 1760401799999.0, "klines n %d %.2f %.2f %.0f", n, cl[0], cl[1], ms);
        CHECK(gbw_parse_klines("{\"code\":-1121,\"msg\":\"Invalid symbol.\"}", cl, 4, &ms) == 0, "error JSON");
        CHECK(gbw_parse_klines(j, cl, 1, &ms) == 1, "max respected");
    }

    printf("%s: %d failures\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
