/* WiFi (station only) under the power manager's rules (CLAUDE.md, step 4 hard rules):
 * - every data frame lwIP sends goes through pm_admit_tx, sized with the 1 Mbit/s worst case (gbwifi);
 * - while a frame waits for admission the radio holds in modem-sleep so the reservoir can rise (sim: tx_hold);
 * - 802.11b data rates off, no A-MPDU TX (sdkconfig), TCP MSS 300 by clamping the MSS option of incoming SYNs
 *   (gbw_clamp_mss; the netif keeps MTU 1500 because lwIP's DHCP refuses an MTU below 576);
 * - WiFi is only started if stored RF calibration data exists, or on USB power (where the full calibration that
 *   ESP-IDF falls back to is allowed). The cart slot never sees a full calibration.
 * Not covered by admission: management and EAPOL frames, which the WiFi library sends itself (probe requests of
 * the connect scan, auth/assoc, 4-way handshake, null/PS-poll frames, MAC retries). The SCAN command scans
 * passively (no probes), connects go to the channel of the scanned AP, and both only start while the pm is in RUN.
 * Frames wait in a small queue for admission (tx_task), never in lwIP's thread; see the TX admission section. */
#include <string.h>

#include "app.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_net_stack.h"
#include "esp_phy_init.h"
#include "esp_private/wifi.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_netif.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "radio";

#define EV_GOT_IP BIT0
#define EV_DISC BIT1
#define TX_MAX_AGE_US 2000000 /* a queued frame older than this is dropped (TCP retransmits) */
#define TX_QUEUE 8
#define RECONNECT_MIN_MS 5000
#define RECONNECT_MAX_MS 60000

static esp_netif_t *s_sta;
static EventGroupHandle_t s_ev;
static TaskHandle_t s_start_task;
static volatile radio_state_t s_state = RADIO_OFF;
static volatile bool s_hold, s_connected, s_connecting;
static volatile uint32_t s_drops;
static wifi_ps_type_t s_ps = WIFI_PS_NONE;
static SemaphoreHandle_t s_mode_mtx;

/* ---------------------------------------------------------------- TX admission (netif driver hook)
 * lwIP's tcpip thread must not block, so the hook only queues a copy of the frame. tx_task sends each frame once
 * pm_admit_tx admits it (the radio holds in modem-sleep meanwhile, so the reservoir rises). A frame is dropped
 * only when it is TX_MAX_AGE_US old or the queue is full; TCP retransmits it. sysim models the same rules. */
typedef struct {
    int64_t t_us;
    uint16_t len;
    uint8_t data[];
} txf_t;
static QueueHandle_t s_txq;
static TaskHandle_t s_tx_task;

static esp_err_t tx(void *h, void *buf, size_t len)
{
    txf_t *f = malloc(sizeof *f + len);
    if (!f)
        return ESP_ERR_NO_MEM;
    f->t_us = esp_timer_get_time();
    f->len = (uint16_t)len;
    memcpy(f->data, buf, len);
    if (xQueueSend(s_txq, &f, 0) != pdTRUE) {
        free(f);
        return ESP_ERR_NO_MEM; /* queue full: lwIP sees ERR_MEM */
    }
    xTaskNotifyGive(s_tx_task);
    return ESP_OK;
}

static esp_err_t tx_wrap(void *h, void *buf, size_t len, void *netstack_buf)
{
    return tx(h, buf, len);
}

static void tx_task(void *arg)
{
    txf_t *f = NULL;
    for (;;) {
        if (!f && xQueueReceive(s_txq, &f, portMAX_DELAY) != pdTRUE)
            continue;
        if (esp_timer_get_time() - f->t_us > TX_MAX_AGE_US) {
            free(f); /* too old: TCP has retransmitted, or will */
            f = NULL;
            s_drops++;
            continue;
        }
        /* worst-case airtime: 1 Mbit/s (#19164); the Ethernet II header is not on the air */
        float t = gbw_airtime_worst_s(f->len > 14 ? f->len - 14u : f->len);
        xSemaphoreTake(gbw_pm_mtx, portMAX_DELAY);
        bool ok = pm_admit_tx(&gbw_pm, t, GBW_TX_W);
        pm_state_t st = pm_state(&gbw_pm);
        xSemaphoreGive(gbw_pm_mtx);
        if (ok) {
            esp_wifi_internal_tx(WIFI_IF_STA, f->data, f->len);
            free(f);
            f = NULL;
            s_hold = uxQueueMessagesWaiting(s_txq) > 0;
            continue;
        }
        s_hold = true;
        /* 1 ms while the reservoir may recover quickly; 10 ms in PARK so light sleep stays possible */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(st == PM_PARK || st == PM_OFF ? 10 : 1));
    }
}

static void free_rx(void *h, void *buf)
{
    if (buf)
        esp_wifi_internal_free_rx_buffer(buf);
}

/* every received frame: clamp the MSS of incoming SYN-ACKs, then hand it to the netif as ESP-IDF would */
static esp_err_t rx_clamp(esp_netif_t *netif, void *buf, size_t len, void *eb)
{
    gbw_clamp_mss(buf, len, GBW_MSS);
    return esp_netif_receive(netif, buf, len, eb);
}

/* ---------------------------------------------------------------- events */
static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        /* runs after ESP-IDF's own STA_START handler (registered earlier), which installs esp_netif_receive */
        esp_err_t e = esp_wifi_register_if_rxcb(esp_netif_get_io_driver(s_sta), rx_clamp, s_sta);
        if (e != ESP_OK)
            ESP_LOGE(TAG, "MSS clamp not installed (%s)", esp_err_to_name(e));
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        xEventGroupClearBits(s_ev, EV_GOT_IP);
        xEventGroupSetBits(s_ev, EV_DISC);
        ESP_LOGI(TAG, "disconnected, reason %d", ((wifi_event_sta_disconnected_t *)data)->reason);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_connected = true;
        xEventGroupSetBits(s_ev, EV_GOT_IP);
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&((ip_event_got_ip_t *)data)->ip_info.ip));
    }
}

/* ---------------------------------------------------------------- start (BOOT) */
static bool cal_data_stored(void)
{
    esp_phy_calibration_data_t *d = calloc(1, sizeof *d);
    bool ok = d && esp_phy_load_cal_data_from_nvs(d) == ESP_OK;
    free(d);
    return ok;
}

static bool load_creds(char *ssid, size_t ns, char *pass, size_t np, uint8_t *channel)
{
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READONLY, &h) != ESP_OK)
        return false;
    bool have = nvs_get_str(h, "ssid", ssid, &ns) == ESP_OK && nvs_get_str(h, "pass", pass, &np) == ESP_OK;
    if (nvs_get_u8(h, "chan", channel) != ESP_OK)
        *channel = 0;
    nvs_close(h);
    return have;
}

static void start_connect(const char *ssid, const char *pass, uint8_t channel)
{
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, pass, sizeof wc.sta.password);
    wc.sta.channel = channel; /* known channel: the connect scan probes one channel, not all 13 */
    if (esp_wifi_set_config(WIFI_IF_STA, &wc) == ESP_OK)
        esp_wifi_connect();
}

/* reconnect with backoff, only in RUN (association frames bypass admission) */
static void reconnect_tick(void)
{
    static int64_t next_us;
    static int backoff_ms = RECONNECT_MIN_MS;
    if (s_state != RADIO_STARTED || s_connected) {
        backoff_ms = RECONNECT_MIN_MS;
        return;
    }
    if (s_connecting || pm_state(&gbw_pm) != PM_RUN || esp_timer_get_time() < next_us)
        return;
    char ssid[33] = "", pass[65] = "";
    uint8_t ch;
    if (!load_creds(ssid, sizeof ssid, pass, sizeof pass, &ch))
        return;
    ESP_LOGI(TAG, "reconnecting to %s (next try in %d s)", ssid, backoff_ms / 1000);
    start_connect(ssid, pass, ch);
    next_us = esp_timer_get_time() + (int64_t)backoff_ms * 1000;
    backoff_ms = backoff_ms * 2 > RECONNECT_MAX_MS ? RECONNECT_MAX_MS : backoff_ms * 2;
}

static void start_task(void *arg)
{
    for (;;) {
        reconnect_tick();
        bool boot = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000)) > 0;
        if (s_state == RADIO_STARTED || pm_state(&gbw_pm) == PM_OFF)
            continue;
        /* the calibration is only budgeted in BOOT (full reservoir, audit A7). Outside BOOT the start is retried
         * once a second, but only on USB power (cart without calibration data, USB plugged in later) */
        if (!boot && !power_on_usb())
            continue;
        bool stored = cal_data_stored();
        if (!stored && !power_on_usb()) {
            if (s_state != RADIO_NOCAL)
                ESP_LOGW(TAG, "no RF calibration data and not on USB: WiFi stays off (plug in USB once)");
            s_state = RADIO_NOCAL;
            continue; /* re-checked every second: plugging USB in starts it */
        }
        int64_t t0 = esp_timer_get_time();
        esp_err_t e = esp_wifi_config_11b_rate(WIFI_IF_STA, true); /* before esp_wifi_start (API note) */
        if (e == ESP_OK)
            e = esp_wifi_start(); /* partial calibration here, or the first full one on USB */
        if (e != ESP_OK) {
            ESP_LOGE(TAG, "WiFi start failed: %s", esp_err_to_name(e)); /* stays off; no reboot loop */
            continue;
        }
        ESP_LOGI(TAG, "WiFi started in %lld ms (%s calibration)", (esp_timer_get_time() - t0) / 1000,
                 stored ? "partial" : "full, on USB");
        s_state = RADIO_STARTED;
        radio_apply_mode(pm_state(&gbw_pm));
        radio_autoconnect();
    }
}

void radio_init(void)
{
    s_ev = xEventGroupCreate();
    s_mode_mtx = xSemaphoreCreateMutex();
    s_txq = xQueueCreate(TX_QUEUE, sizeof(txf_t *));
    xTaskCreate(tx_task, "radio_tx", 3072, NULL, 19, &s_tx_task); /* below the power and link tasks, above lwIP (18) */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_sta = esp_netif_create_default_wifi_sta();
    /* replace the WiFi driver's transmit functions (installed when the netif was attached) with admitted ones */
    esp_netif_driver_ifconfig_t drv = {
        .handle = esp_netif_get_io_driver(s_sta),
        .transmit = tx,
        .transmit_wrap = tx_wrap,
        .driver_free_rx_buffer = free_rx,
    };
    ESP_ERROR_CHECK(esp_netif_set_driver_config(s_sta, &drv));
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM)); /* credentials: our own NVS keys */
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    xTaskCreate(start_task, "radio_start", 4096, NULL, 5, &s_start_task);
}

radio_state_t radio_state(void) { return s_state; }
bool radio_tx_hold(void) { return s_hold; }
uint32_t radio_tx_drops(void) { return s_drops; }
bool radio_connected(void) { return s_connected; }

void radio_boot(void)
{
    xTaskNotifyGive(s_start_task);
}

void radio_apply_mode(pm_state_t st)
{
    if (s_state != RADIO_STARTED)
        return;
    /* RUN: normal power save; SLEEP / PARK / waiting for admission: modem-sleep at the longest interval (PARK adds
     * auto light-sleep once the link has freed the SPI slave) */
    wifi_ps_type_t ps = st == PM_RUN && !s_hold ? WIFI_PS_MIN_MODEM : WIFI_PS_MAX_MODEM;
    xSemaphoreTake(s_mode_mtx, portMAX_DELAY); /* called from the power task and the start task */
    if (ps != s_ps && esp_wifi_set_ps(ps) == ESP_OK)
        s_ps = ps;
    xSemaphoreGive(s_mode_mtx);
}

bool radio_wait_connected(int timeout_ms)
{
    char ssid[33], pass[65];
    uint8_t ch;
    if (s_connected)
        return true;
    if (!load_creds(ssid, sizeof ssid, pass, sizeof pass, &ch))
        return false; /* nothing to wait for */
    EventBits_t b = xEventGroupWaitBits(s_ev, EV_GOT_IP, pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms > 0 ? timeout_ms : 1));
    return (b & EV_GOT_IP) != 0;
}

bool radio_wait_run(int timeout_ms)
{
    for (int t = 0; t < timeout_ms; t += 10) {
        if (s_state == RADIO_STARTED && pm_state(&gbw_pm) == PM_RUN)
            return true;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return false;
}

int radio_scan(gbw_ap_t *aps, int max)
{
    if (!radio_wait_run(5000))
        return -1;
    wifi_scan_config_t sc = {.scan_type = WIFI_SCAN_TYPE_PASSIVE, .scan_time.passive = 120};
    if (esp_wifi_scan_start(&sc, true) != ESP_OK)
        return -1;
    uint16_t n = 16;
    wifi_ap_record_t rec[16];
    if (esp_wifi_scan_get_ap_records(&n, rec) != ESP_OK)
        return -1;
    int k = 0;
    for (int i = 0; i < n && k < max; i++) {
        memcpy(aps[k].ssid, rec[i].ssid, sizeof aps[k].ssid - 1);
        aps[k].ssid[sizeof aps[k].ssid - 1] = 0;
        aps[k].rssi = rec[i].rssi;
        aps[k].channel = rec[i].primary;
        switch (rec[i].authmode) {
        case WIFI_AUTH_OPEN: aps[k].auth = GBW_AUTH_OPEN; break;
        case WIFI_AUTH_WEP: aps[k].auth = GBW_AUTH_WEP; break;
        case WIFI_AUTH_WPA_PSK: aps[k].auth = GBW_AUTH_WPA; break;
        case WIFI_AUTH_WPA3_PSK:
        case WIFI_AUTH_WPA2_WPA3_PSK: aps[k].auth = GBW_AUTH_WPA3; break;
        default: aps[k].auth = GBW_AUTH_WPA2; break;
        }
        k++;
    }
    return k;
}

int radio_connect(const char *ssid, const char *pass, uint8_t channel, char *ip, int ip_len, int timeout_ms)
{
    int64_t t0 = esp_timer_get_time();
    if (!radio_wait_run(timeout_ms / 4))
        return -2;
    s_connecting = true;
    esp_wifi_disconnect();
    xEventGroupClearBits(s_ev, EV_GOT_IP | EV_DISC);
    start_connect(ssid, pass, channel);
    int left = timeout_ms - (int)((esp_timer_get_time() - t0) / 1000);
    EventBits_t b = xEventGroupWaitBits(s_ev, EV_GOT_IP, pdFALSE, pdFALSE, pdMS_TO_TICKS(left > 0 ? left : 1));
    s_connecting = false;
    if (!(b & EV_GOT_IP)) {
        esp_wifi_disconnect();
        return -1;
    }
    esp_netif_ip_info_t info;
    esp_netif_get_ip_info(s_sta, &info);
    snprintf(ip, ip_len, IPSTR, IP2STR(&info.ip));
    return 0;
}

void radio_store_creds(const char *ssid, const char *pass, uint8_t channel)
{
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READWRITE, &h) != ESP_OK)
        return;
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "pass", pass);
    nvs_set_u8(h, "chan", channel);
    nvs_commit(h);
    nvs_close(h);
}

void radio_autoconnect(void)
{
    char ssid[33] = "", pass[65] = "";
    uint8_t ch;
    if (!load_creds(ssid, sizeof ssid, pass, sizeof pass, &ch) || !radio_wait_run(2000))
        return;
    if (s_connected)
        esp_wifi_disconnect(); /* new credentials from the console */
    start_connect(ssid, pass, ch);
}
