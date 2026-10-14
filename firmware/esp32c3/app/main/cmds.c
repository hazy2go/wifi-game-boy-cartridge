/* Mailbox commands 01-07 (sim/system/README.md). One job at a time; every answer within JOB_MS, below the
 * master's 12 s RADIO TIMEOUT. Screen text comes from components/gbwifi, the same code gate S1 runs. */
#include <string.h>
#include <sys/time.h>
#include <time.h>

#include "app.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "freertos/task.h"

static const char *TAG = "cmds";

#define JOB_MS 9000
#define BTC_URL "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=96"

static gbw_ap_t s_aps[GBW_SCAN_MAX];
static int s_naps;
static char s_resp[EL_MAX_RESP + 1];

typedef struct {
    char *buf;
    int len, cap;
} body_t;

static esp_err_t on_http(esp_http_client_event_t *e)
{
    body_t *b = e->user_data;
    if (e->event_id == HTTP_EVENT_ON_DATA && b->len < b->cap) {
        int n = e->data_len < b->cap - b->len ? e->data_len : b->cap - b->len;
        memcpy(b->buf + b->len, e->data, n);
        b->len += n;
    }
    return ESP_OK;
}

/* GET into buf (cap bytes, 0-terminated, truncated). Returns the HTTP status, or -1. */
static int http_get(const char *url, char *buf, int cap, int timeout_ms)
{
    body_t b = {.buf = buf, .cap = cap - 1};
    esp_http_client_config_t c = {
        .url = url,
        .event_handler = on_http,
        .user_data = &b,
        .timeout_ms = timeout_ms,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 1024,
    };
    esp_http_client_handle_t h = esp_http_client_init(&c);
    if (!h)
        return -1;
    int st = esp_http_client_perform(h) == ESP_OK ? esp_http_client_get_status_code(h) : -1;
    esp_http_client_cleanup(h);
    buf[b.len] = 0;
    return st;
}

static int need_net(char *r)
{
    if (radio_state() == RADIO_NOCAL) {
        snprintf(r, EL_MAX_RESP + 1, "ERR NEEDS USB CAL");
        return 3;
    }
    if (!radio_wait_run(3000)) {
        snprintf(r, EL_MAX_RESP + 1, "ERR RADIO NOT READY");
        return 3;
    }
    if (!radio_connected()) {
        snprintf(r, EL_MAX_RESP + 1, "ERR NOT CONNECTED");
        return 3;
    }
    return 0;
}

static int cmd_time(char *r)
{
    int e = need_net(r);
    if (e)
        return e;
    static bool sntp_on;
    if (!sntp_on) {
        esp_sntp_config_t c = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
        esp_netif_sntp_init(&c);
        sntp_on = true;
    }
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(7000)) != ESP_OK && time(NULL) < 1700000000) {
        snprintf(r, EL_MAX_RESP + 1, "ERR NTP");
        return 3;
    }
    time_t now = time(NULL);
    struct tm t;
    gmtime_r(&now, &t);
    gbw_fmt_time(r, EL_MAX_RESP + 1, t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec);
    return 2;
}

static int cmd_get(const char *url, char *r)
{
    int e = need_net(r);
    if (e)
        return e;
    int st = http_get(url, r, EL_MAX_RESP + 1, JOB_MS - 1500);
    if (st != 200) {
        snprintf(r, EL_MAX_RESP + 1, st < 0 ? "ERR NO CONNECTION" : "ERR HTTP %d", st);
        return 3;
    }
    for (char *p = r; *p; p++) /* the Game Boy font is ASCII 32-127 plus line breaks */
        if ((*p < 32 || *p > 126) && *p != '\n')
            *p = '?';
    return 2;
}

static int cmd_btc(char *r)
{
    int e = need_net(r);
    if (e)
        return e;
    static char json[24 * 1024]; /* 96 klines are about 14 KB */
    int st = http_get(BTC_URL, json, sizeof json, JOB_MS - 1500);
    if (st != 200) {
        snprintf(r, EL_MAX_RESP + 1, st < 0 ? "ERR NO CONNECTION" : "ERR HTTP %d", st);
        return 3;
    }
    cJSON *a = cJSON_Parse(json);
    double closes[96];
    int n = 0;
    double close_ms = 0;
    cJSON *k;
    cJSON_ArrayForEach(k, a) {
        cJSON *c = cJSON_GetArrayItem(k, 4), *t = cJSON_GetArrayItem(k, 6);
        if (n < 96 && cJSON_IsString(c)) {
            closes[n++] = strtod(c->valuestring, NULL);
            close_ms = cJSON_IsNumber(t) ? t->valuedouble : close_ms;
        }
    }
    cJSON_Delete(a);
    if (n < 2) {
        snprintf(r, EL_MAX_RESP + 1, "ERR BAD DATA");
        return 3;
    }
    time_t ts = (time_t)(close_ms / 1000);
    struct tm t;
    gmtime_r(&ts, &t);
    gbw_fmt_btc(r, EL_MAX_RESP + 1, closes, n, t.tm_hour, t.tm_min);
    return 2;
}

static int cmd_scan(char *r)
{
    if (radio_state() == RADIO_NOCAL) {
        snprintf(r, EL_MAX_RESP + 1, "ERR NEEDS USB CAL");
        return 3;
    }
    gbw_ap_t all[16];
    int n = radio_scan(all, 16);
    if (n < 0) {
        snprintf(r, EL_MAX_RESP + 1, "ERR RADIO NOT READY");
        return 3;
    }
    gbw_fmt_scan(r, EL_MAX_RESP + 1, all, n); /* sorts strongest first; line i+1 = all[i] */
    s_naps = n < GBW_SCAN_MAX ? n : GBW_SCAN_MAX;
    memcpy(s_aps, all, sizeof(gbw_ap_t) * s_naps);
    return 2;
}

static int cmd_connect(const char *payload, char *r)
{
    int idx;
    const char *pass;
    int e = gbw_parse_connect(payload, s_naps, &idx, &pass);
    if (e == -2) {
        snprintf(r, EL_MAX_RESP + 1, "WPA2 KEY < 8 CHARS");
        return 3;
    }
    if (e) {
        snprintf(r, EL_MAX_RESP + 1, "BAD NETWORK");
        return 3;
    }
    if (radio_state() == RADIO_NOCAL) {
        snprintf(r, EL_MAX_RESP + 1, "ERR NEEDS USB CAL");
        return 3;
    }
    char ip[20];
    int c = radio_connect(s_aps[idx].ssid, pass, ip, sizeof ip, JOB_MS - 500);
    if (c) {
        snprintf(r, EL_MAX_RESP + 1, c == -2 ? "ERR RADIO NOT READY" : "ERR CONNECT FAILED");
        return 3;
    }
    radio_store_creds(s_aps[idx].ssid, pass);
    snprintf(r, EL_MAX_RESP + 1, "OK %s", ip);
    return 2;
}

void cmds_task(void *arg)
{
    for (;;) {
        uint8_t seq, cmd;
        char payload[EL_MAX_REQ + 1];
        xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
        bool got = el_slave_take_job(&gbw_link, &seq, &cmd, payload);
        xSemaphoreGive(gbw_link_mtx);
        if (!got) {
            vTaskDelay(pdMS_TO_TICKS(2));
            continue;
        }
        ESP_LOGI(TAG, "job seq %u cmd %u \"%s\"", seq, cmd, payload);
        int st = 2;
        switch (cmd) {
        case 1: snprintf(s_resp, sizeof s_resp, "PONG"); break;
        case 2: st = cmd_time(s_resp); break;
        case 3: st = cmd_get(payload, s_resp); break;
        case 4: gbw_fmt_power(s_resp, sizeof s_resp, power_v_rail(), power_v_res()); break;
        case 5: st = cmd_scan(s_resp); break;
        case 6: st = cmd_connect(payload, s_resp); break;
        case 7: st = cmd_btc(s_resp); break;
        default: snprintf(s_resp, sizeof s_resp, "ERR UNKNOWN CMD %u", cmd); st = 3;
        }
        xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
        el_slave_respond(&gbw_link, seq, (uint8_t)st, (const uint8_t *)s_resp, strlen(s_resp));
        xSemaphoreGive(gbw_link_mtx);
        ESP_LOGI(TAG, "seq %u status %d \"%.40s\"", seq, st, s_resp);
    }
}
