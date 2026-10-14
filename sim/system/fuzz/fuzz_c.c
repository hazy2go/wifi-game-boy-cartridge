/* Randomised robustness test of the C code that parses untrusted input (gate S1.14). Built with
 * -fsanitize=address,undefined: any out-of-bounds access, overflow or UB aborts the run.
 *   - esp_link: slave on_rx and master done with random, truncated and bit-flipped frames (SPI bus)
 *   - gbwifi: MSS clamp on random and near-valid IPv4/TCP frames, kline parser on random JSON-ish text,
 *     connect parser, scan formatter with random SSIDs, sanitizer (network data)
 * Inputs are exact-size heap buffers, so a read past the end is caught. Usage: fuzz_c [iterations] [seed] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_link.h"
#include "gbwifi.h"

static unsigned long long rs = 88172645463325252ull;
static unsigned rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (unsigned)(rs >> 32); }

static void *xmalloc(size_t n) { void *p = malloc(n ? n : 1); if (!p) abort(); return p; }
static uint8_t *dup(const uint8_t *p, size_t n) { uint8_t *b = xmalloc(n); memcpy(b, p, n); return b; }

int main(int argc, char **argv)
{
    long iters = argc > 1 ? atol(argv[1]) : 1000000;
    if (argc > 2) rs = strtoull(argv[2], NULL, 10) | 1;
    static uint8_t mb[0x2000];
    el_slave_t s; el_master_t m;
    el_slave_init(&s); el_master_init(&m, mb, 0, 0x5A);
    uint8_t valid[EL_MAX_XFER];
    for (long it = 0; it < iters; it++) {
        unsigned kind = rnd() % 8;
        size_t n = rnd() % (EL_MAX_XFER + 8);
        uint8_t buf[EL_MAX_XFER + 8];
        if (kind < 3) {                      /* a valid frame, then truncated / flipped */
            if (kind == 0) {
                mb[MB_SEQ] = (uint8_t)rnd(); mb[MB_CMD] = (uint8_t)rnd();
                size_t pl = rnd() % 260;
                for (size_t i = 0; i < pl && MB_REQ + i < MB_RESP; i++) mb[MB_REQ + i] = (uint8_t)(rnd() | 1);
                if (pl < MB_RESP - MB_REQ) mb[MB_REQ + pl] = 0;
                size_t k = el_master_next(&m, (uint32_t)it, mb, valid);
                memcpy(buf, valid, k); n = k;
            } else {
                el_slave_set_live(&s, rnd() & 1, rnd() % 5, (float)(rnd() % 600) / 100, (float)(rnd() % 600) / 100);
                if (rnd() % 3 == 0) el_slave_respond(&s, s.job_seq, 2 + rnd() % 2, (const uint8_t *)"resp", rnd() % 5);
                size_t k = el_slave_build_tx(&s, valid);
                memcpy(buf, valid, k); n = k;
            }
            if (rnd() & 1) n = rnd() % (n + 1);
            if (rnd() & 1 && n) buf[rnd() % n] ^= (uint8_t)(1u << (rnd() % 8));
        } else {
            for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)rnd();
            if (n > 0 && rnd() & 1) buf[0] = rnd() & 1 ? EL_M_MAGIC : EL_S_MAGIC;
        }
        uint8_t *in = dup(buf, n);
        if (rnd() & 1) el_slave_on_rx(&s, in, n); else el_master_done(&m, (uint32_t)it, mb, in, n);
        free(in);
        uint8_t seq, cmd; char payload[EL_MAX_REQ + 1];
        if (el_slave_take_job(&s, &seq, &cmd, payload) && strlen(payload) > EL_MAX_REQ) abort();
        if (rnd() % 4 == 0) {                /* the master side of a full exchange, with live slave state */
            uint8_t tx[EL_MAX_XFER], rx[EL_MAX_XFER];
            size_t k = el_master_next(&m, (uint32_t)it, mb, tx);
            el_slave_on_rx(&s, tx, k);
            el_slave_build_tx(&s, rx);
            uint8_t *r = dup(rx, k);
            el_master_done(&m, (uint32_t)it, mb, r, k);
            free(r);
        }

        /* gbwifi: MSS clamp on random / near-valid frames */
        size_t fl = rnd() % 120;
        uint8_t *f = xmalloc(fl);
        for (size_t i = 0; i < fl; i++) f[i] = (uint8_t)rnd();
        if (fl > 14 && rnd() & 1) { f[12] = 8; f[13] = 0; }
        if (fl > 24 && rnd() & 1) { f[14] = 0x40 | (rnd() % 16); f[23] = 6; f[20] = 0; f[21] = 0; }
        if (fl > 47 && rnd() & 1) { f[47] |= 2; f[46] = (uint8_t)((rnd() % 16) << 4); }
        gbw_clamp_mss(f, fl, (uint16_t)(rnd() % 1500));
        free(f);

        /* kline parser on random JSON-ish text */
        static const char alpha[] = "[]\",.-0123456789e+ {}:abc";
        size_t jl = rnd() % 400;
        char *j = xmalloc(jl + 1);
        for (size_t i = 0; i < jl; i++) j[i] = alpha[rnd() % (sizeof alpha - 1)];
        j[jl] = 0;
        double closes[8], cms = 0;
        int nk = gbw_parse_klines(j, closes, (int)(rnd() % 9), &cms);
        if (nk < 0 || nk > 8) abort();
        free(j);

        /* connect parser, scan formatter, sanitizer */
        char cp[32];
        size_t cl = rnd() % sizeof cp;
        for (size_t i = 0; i < cl; i++) cp[i] = (char)(rnd() % 255 + 1);
        cp[cl] = 0;
        int idx; const char *pass;
        if (gbw_parse_connect(cp, (int)(rnd() % 9), &idx, &pass) == 0 && (idx < 0 || idx > 8)) abort();
        gbw_ap_t aps[10];
        int na = (int)(rnd() % 11);
        for (int a = 0; a < na; a++) {
            for (size_t i = 0; i < sizeof aps[a].ssid; i++) aps[a].ssid[i] = (char)rnd();
            aps[a].ssid[rnd() % sizeof aps[a].ssid] = 0;
            aps[a].ssid[sizeof aps[a].ssid - 1] = 0;
            aps[a].rssi = (int8_t)rnd(); aps[a].channel = (uint8_t)rnd(); aps[a].auth = (uint8_t)rnd();
        }
        char out[256];
        size_t ol = gbw_fmt_scan(out, sizeof out, aps, na);
        if (ol >= sizeof out || strlen(out) != ol) abort();
        gbw_sanitize(cp);
        double cl2[3] = {1, 1 + (rnd() % 1000), 2};
        if (gbw_fmt_btc(out, 1 + rnd() % 256, cl2, 2 + rnd() % 2, rnd() % 24, rnd() % 60) > 255) abort();
    }
    printf("fuzz_c: %ld iterations, no sanitizer findings\n", iters);
    return 0;
}
