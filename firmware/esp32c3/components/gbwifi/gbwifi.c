#include "gbwifi.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

float gbw_airtime_worst_s(size_t ip_len)
{
    return 192e-6f + (float)(ip_len + 24 + 8 + 4) * 8.0f / 1e6f;
}

float gbw_rail_for_pm(bool on_usb, float v_rail)
{
    return on_usb ? 5.0f : v_rail;
}

static int cmp_rssi(const void *a, const void *b)
{
    const gbw_ap_t *x = a, *y = b;
    int rx = x->rssi ? x->rssi : -127, ry = y->rssi ? y->rssi : -127; /* unknown last */
    return ry - rx;
}

static size_t put(char *out, size_t cap, size_t at, const char *s)
{
    size_t n = strlen(s);
    if (at + n >= cap)
        n = at < cap ? cap - 1 - at : 0;
    memcpy(out + at, s, n);
    out[at + n] = 0;
    return at + n;
}

size_t gbw_fmt_scan(char *out, size_t cap, gbw_ap_t *aps, int n)
{
    static const char *const SEC[] = {"OP", "WE", "W1", "W2", "W3"};
    out[0] = 0;
    if (n <= 0)
        return put(out, cap, 0, "NO NETWORKS");
    qsort(aps, (size_t)n, sizeof *aps, cmp_rssi);
    size_t at = 0;
    for (int i = 0; i < n && i < GBW_SCAN_MAX; i++) {
        char name[11];
        const char *s = aps[i].ssid[0] ? aps[i].ssid : "(hidden)";
        size_t k = 0;
        for (; k < 10 && s[k]; k++)
            name[k] = (s[k] >= 32 && s[k] < 127) ? s[k] : '?';
        name[k] = 0;
        char rs[8], line[40];
        if (aps[i].rssi)
            snprintf(rs, sizeof rs, "%4d", aps[i].rssi);
        else
            snprintf(rs, sizeof rs, "  --");
        snprintf(line, sizeof line, "%d %-10s%s %s\n", i + 1, name, rs, SEC[aps[i].auth <= GBW_AUTH_WPA3 ? aps[i].auth : 0]);
        at = put(out, cap, at, line);
    }
    return at;
}

int gbw_parse_connect(const char *payload, int n_aps, int *idx, const char **pass)
{
    if (payload[0] < '1' || payload[0] > '9' || payload[1] != '|')
        return -1;
    int i = payload[0] - '1';
    if (i >= n_aps)
        return -1;
    size_t len = strlen(payload + 2);
    if (len > 0 && len < 8)
        return -2;
    *idx = i;
    *pass = payload + 2;
    return 0;
}

size_t gbw_fmt_power(char *out, size_t cap, float rail_v, float res_v)
{
    int n = snprintf(out, cap, "RAIL %.2fV RES %.2fV", rail_v, res_v);
    return n < 0 ? 0 : (size_t)n < cap ? (size_t)n : cap - 1;
}

size_t gbw_fmt_time(char *out, size_t cap, int year, int mon, int day, int h, int m, int s)
{
    int n = snprintf(out, cap, "%04d-%02d-%02d %02d:%02d:%02d", year, mon, day, h, m, s);
    return n < 0 ? 0 : (size_t)n < cap ? (size_t)n : cap - 1;
}

static void commas(long v, char *o)
{
    char t[32];
    snprintf(t, sizeof t, "%ld", v);
    int L = (int)strlen(t), j = 0;
    for (int i = 0; i < L; i++) {
        if (i && (L - i) % 3 == 0)
            o[j++] = ',';
        o[j++] = t[i];
    }
    o[j] = 0;
}

size_t gbw_fmt_btc(char *out, size_t cap, const double *closes, int n, int utc_h, int utc_m)
{
    double lo = closes[0], hi = closes[0];
    for (int i = 1; i < n; i++) {
        lo = closes[i] < lo ? closes[i] : lo;
        hi = closes[i] > hi ? closes[i] : hi;
    }
    double last = closes[n - 1], chg = (last / closes[0] - 1) * 100;
    char p[24], h[24], l[24];
    commas(lround(last), p);
    commas(lround(hi), h);
    commas(lround(lo), l);
    int k = snprintf(out, cap, "$%s  %+.2f%%\nH %s  L %s\n%02d:%02d UTC  15M BARS\n", p, chg, h, l, utc_h, utc_m);
    if (k < 0 || (size_t)k >= cap)
        return cap ? cap - 1 : 0;
    size_t at = (size_t)k;
    for (int c = 0; c < 128 && at + 1 < cap; c++) { /* linear resample to one value per pixel column */
        double x = c * (n - 1) / 127.0;
        int i = (int)x;
        double f = x - i, v = i + 1 >= n ? closes[i] : closes[i] * (1 - f) + closes[i + 1] * f;
        long y = hi > lo ? lround((v - lo) / (hi - lo) * 63) : 32;
        out[at++] = (char)(0x20 + (y < 0 ? 0 : y > 63 ? 63 : y));
    }
    out[at] = 0;
    return at;
}
