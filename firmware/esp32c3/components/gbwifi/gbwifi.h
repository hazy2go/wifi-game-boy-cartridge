/* WiFi cartridge ESP32-C3 app: the parts that decide what the Game Boy sees and how TX is sized.
 *
 * Plain C, no ESP-IDF dependency: the app (firmware/esp32c3/app) and the full-system simulation
 * (sim/system/sysim.c) both compile this file, so the screens and the TX admission estimate in gate S1 are the
 * shipped ones.
 */
#ifndef GBWIFI_H
#define GBWIFI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Largest IP packet the radio is expected to send: TCP segments of GBW_MSS bytes + 40 header bytes (A5: MSS about
 * 300). The netif keeps MTU 1500 (lwIP's DHCP refuses an MTU below 576); the MSS comes from gbw_clamp_mss. */
#define GBW_MSS 300
#define GBW_MTU (GBW_MSS + 40)
/* Power drawn from the reservoir while transmitting: 350 mA at 3.3 V through the buck-boost (eta 0.88). */
#define GBW_TX_W (0.350f * 3.3f / 0.88f)
/* Length of the pm's BOOT state = the RF calibration (A7: partial calibration assumed 5 ms at 50 % TX; replace with
 * the XIAO measurement). The audits A5-A7 and gate S1 use the same value. BOOT cannot sleep, so a longer BOOT than
 * needed drains a weak console's reservoir (S1 2026-10-14: 100 ms BOOT = 51 radio resets on the weak console). */
#define GBW_BOOT_MS 5

/* On-air time of a frame carrying ip_len bytes, worst case: 802.11b 1 Mbit/s with the long preamble (192 us),
 * 24-byte MAC header + 8-byte LLC/SNAP + 4-byte FCS. 11b data rates are off, but ESP-IDF issue #19164 shows a
 * station still using them, so admission must not assume OFDM rates. */
float gbw_airtime_worst_s(size_t ip_len);

/* What the power manager is given as the cart rail. On USB the mux feeds the radio from VBUS and the cart rail
 * (which may be absent, 0 V) is not loaded by the radio, so the rail watch does not apply. */
float gbw_rail_for_pm(bool on_usb, float v_rail);

/* MSS clamping on receive, as routers do: in an incoming Ethernet II / IPv4 / TCP SYN segment, lower the MSS option
 * to mss and fix the TCP checksum (RFC 1624). lwIP then sends at most mss bytes per segment on that connection.
 * Returns 1 if the frame was changed. */
int gbw_clamp_mss(uint8_t *frame, size_t len, uint16_t mss);

/* Binance /api/v3/klines JSON ([[open_time,"open","high","low","close",...,close_time,...],...]) without a JSON
 * tree: fills closes (oldest first, up to max) and *close_ms of the last kline. Returns the count. */
int gbw_parse_klines(const char *json, double *closes, int max, double *close_ms);

enum { GBW_AUTH_OPEN, GBW_AUTH_WEP, GBW_AUTH_WPA, GBW_AUTH_WPA2, GBW_AUTH_WPA3 };
typedef struct {
    char ssid[33];
    int8_t rssi; /* dBm, 0 = unknown */
    uint8_t channel;
    uint8_t auth;
} gbw_ap_t;
#define GBW_SCAN_MAX 8

/* SCAN response: up to 8 networks, strongest first, one 19-column line each ("1 MyNetwork  -45 W2").
 * Sorts aps in place. Returns the response length; "NO NETWORKS" if n == 0. */
size_t gbw_fmt_scan(char *out, size_t cap, gbw_ap_t *aps, int n);

/* CONNECT payload "n|password" (n = 1-based line of the last SCAN). Returns 0 and sets *idx (0-based) and *pass,
 * -1 if malformed, -2 if a WPA key is shorter than 8 characters (empty = open network). */
int gbw_parse_connect(const char *payload, int n_aps, int *idx, const char **pass);

size_t gbw_fmt_power(char *out, size_t cap, float rail_v, float res_v);
size_t gbw_fmt_time(char *out, size_t cap, int year, int mon, int day, int h, int m, int s);

/* BTC response: "$83,429  +0.08%\nH 83,922  L 83,082\nHH:MM UTC  15M BARS\n" then 128 chart bytes
 * 0x20 + 0..63 (closes resampled linearly to 128 columns, 63 = highest). closes: oldest first, n >= 2. */
size_t gbw_fmt_btc(char *out, size_t cap, const double *closes, int n, int utc_h, int utc_m);

#endif
