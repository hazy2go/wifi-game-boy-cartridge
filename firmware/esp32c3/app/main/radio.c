/* WiFi (station only) under the power manager's rules (CLAUDE.md, step 4 hard rules):
 * - every data frame lwIP sends goes through pm_admit_tx, sized with the 1 Mbit/s worst case (gbwifi);
 * - while a frame waits for admission the radio holds in modem-sleep so the reservoir can rise (sim: tx_hold);
 * - 802.11b data rates off, no A-MPDU TX (sdkconfig), STA netif MTU 340 -> TCP MSS 300;
 * - WiFi is only started if stored RF calibration data exists, or on USB power (where the full calibration that
 *   ESP-IDF falls back to is allowed). The cart slot never sees a full calibration.
 * Not covered by admission: management and EAPOL frames, which the WiFi library sends itself. Scans are passive
 * (no probe requests) and scan/connect only start while the pm is in RUN. */
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
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/netif.h"
#include "nvs.h"

static const char *TAG = "radio";

#define EV_GOT_IP BIT0
#define EV_DISC BIT1
#define ADMIT_WAIT_US 200000 /* longest a frame waits for admission before it is dropped (TCP retransmits) */

static esp_netif_t *s_sta;
static EventGroupHandle_t s_ev;
static TaskHandle_t s_start_task;
static volatile radio_state_t s_state = RADIO_OFF;
static volatile bool s_hold, s_connected;
static wifi_ps_type_t s_ps = WIFI_PS_NONE;

/* ---------------------------------------------------------------- TX admission (netif driver hook) */
static esp_err_t admit(size_t len)
{
    float t = gbw_airtime_worst_s(len > 14 ? len - 14 : len); /* Ethernet II header is not on the air */
    int64_t t0 = esp_timer_get_time();
    for (;;) {
        xSemaphoreTake(gbw_pm_mtx, portMAX_DELAY);
        bool ok = pm_admit_tx(&gbw_pm, t, GBW_TX_W);
        pm_state_t st = pm_state(&gbw_pm);
        xSemaphoreGive(gbw_pm_mtx);
        if (ok) {
            s_hold = false;
            return ESP_OK;
        }
        if (st != PM_RUN || esp_timer_get_time() - t0 > ADMIT_WAIT_US) {
            s_hold = false;
            return ESP_FAIL; /* dropped: lwIP gets ERR_IF, TCP retransmits later */
        }
        s_hold = true;
        vTaskDelay(1);
    }
}

static esp_err_t tx(void *h, void *buf, size_t len)
{
    esp_err_t e = admit(len);
    return e == ESP_OK ? esp_wifi_internal_tx(WIFI_IF_STA, buf, len) : e;
}

static esp_err_t tx_wrap(void *h, void *buf, size_t len, void *netstack_buf)
{
    return tx(h, buf, len);
}

static void free_rx(void *h, void *buf)
{
    if (buf)
        esp_wifi_internal_free_rx_buffer(buf);
}

static void set_mtu(void)
{
    struct netif *n = esp_netif_get_netif_impl(s_sta);
    if (n)
        n->mtu = GBW_MTU;
}

/* ---------------------------------------------------------------- events */
static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        xEventGroupClearBits(s_ev, EV_GOT_IP);
        xEventGroupSetBits(s_ev, EV_DISC);
        ESP_LOGI(TAG, "disconnected, reason %d", ((wifi_event_sta_disconnected_t *)data)->reason);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        set_mtu();
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

static void start_task(void *arg)
{
    for (;;) {
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
        ESP_ERROR_CHECK(esp_wifi_config_11b_rate(WIFI_IF_STA, true)); /* before esp_wifi_start (API note) */
        int64_t t0 = esp_timer_get_time();
        ESP_ERROR_CHECK(esp_wifi_start()); /* partial calibration here, or the first full one on USB */
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
    set_mtu();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM)); /* credentials: our own NVS keys */
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    xTaskCreate(start_task, "radio_start", 4096, NULL, 5, &s_start_task);
}

radio_state_t radio_state(void) { return s_state; }
bool radio_tx_hold(void) { return s_hold; }
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
    if (ps != s_ps && esp_wifi_set_ps(ps) == ESP_OK)
        s_ps = ps;
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

int radio_connect(const char *ssid, const char *pass, char *ip, int ip_len, int timeout_ms)
{
    if (!radio_wait_run(timeout_ms / 2))
        return -2;
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, pass, sizeof wc.sta.password);
    esp_wifi_disconnect();
    xEventGroupClearBits(s_ev, EV_GOT_IP | EV_DISC);
    if (esp_wifi_set_config(WIFI_IF_STA, &wc) != ESP_OK || esp_wifi_connect() != ESP_OK)
        return -1;
    EventBits_t b = xEventGroupWaitBits(s_ev, EV_GOT_IP, pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    if (!(b & EV_GOT_IP))
        return -1;
    esp_netif_ip_info_t info;
    esp_netif_get_ip_info(s_sta, &info);
    snprintf(ip, ip_len, IPSTR, IP2STR(&info.ip));
    return 0;
}

void radio_store_creds(const char *ssid, const char *pass)
{
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READWRITE, &h) != ESP_OK)
        return;
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "pass", pass);
    nvs_commit(h);
    nvs_close(h);
}

void radio_autoconnect(void)
{
    char ssid[33] = "", pass[65] = "";
    size_t n1 = sizeof ssid, n2 = sizeof pass;
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READONLY, &h) != ESP_OK)
        return;
    bool have = nvs_get_str(h, "ssid", ssid, &n1) == ESP_OK && nvs_get_str(h, "pass", pass, &n2) == ESP_OK;
    nvs_close(h);
    if (!have || s_connected || !radio_wait_run(2000))
        return;
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, pass, sizeof wc.sta.password);
    if (esp_wifi_set_config(WIFI_IF_STA, &wc) == ESP_OK)
        esp_wifi_connect();
}
