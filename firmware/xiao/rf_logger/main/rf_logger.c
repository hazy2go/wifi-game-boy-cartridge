/* Antenna bench test: RSSI and uplink throughput logger (Seeed XIAO ESP32C3).
 *
 * Hold point 2 in hw/rev-b/fab/ORDER.md: the FPC antenna (a) free, (b) about 2 mm over copper inside the
 * shell, (c) with the cart inserted in a Game Boy. This firmware joins the AP set in menuconfig, then loops:
 *   10 RSSI samples, 200 ms apart       ->  RSSI,<ms>,<dBm>,<channel>
 *   one TCP upload to rf_host.py         ->  TPUT,<ms>,<bytes>,<us>,<kbit/s>   (or TPUT,<ms>,ERR,<errno>)
 * rf_host.py (next to this folder) is the TCP sink, tags every line with the position you type, and
 * summarises per position.
 *
 * Radio settings follow the cart rules where they affect the result: 802.11b data rates off. Power save is
 * off so RSSI is sampled steadily.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#define GOT_IP BIT0

static EventGroupHandle_t s_ev;

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        xEventGroupClearBits(s_ev, GOT_IP);
        printf("INFO,%" PRIu32 ",disconnected,reason %d,rssi %d\n", now_ms(), d->reason, d->rssi);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = data;
        printf("INFO,%" PRIu32 ",got ip " IPSTR "\n", now_ms(), IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_ev, GOT_IP);
    }
}

static void wifi_up(void)
{
    s_ev = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, CONFIG_RFLOG_WIFI_SSID, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, CONFIG_RFLOG_WIFI_PASSWORD, sizeof wc.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_config_11b_rate(WIFI_IF_STA, true));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void log_rssi(void)
{
    for (int i = 0; i < 10; i++) {
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK)
            printf("RSSI,%" PRIu32 ",%d,%u\n", now_ms(), ap.rssi, ap.primary);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static void log_tput(void)
{
    static uint8_t buf[1460];
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(CONFIG_RFLOG_PORT)};
    inet_pton(AF_INET, CONFIG_RFLOG_HOST, &a.sin_addr);
    int s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0 || connect(s, (struct sockaddr *)&a, sizeof a) != 0) {
        printf("TPUT,%" PRIu32 ",ERR,%d\n", now_ms(), errno);
        if (s >= 0)
            close(s);
        return;
    }
    struct timeval tv = {.tv_sec = 2};
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int64_t t0 = esp_timer_get_time(), end = t0 + CONFIG_RFLOG_TPUT_SECONDS * 1000000LL;
    uint64_t sent = 0;
    while (esp_timer_get_time() < end) {
        int n = send(s, buf, sizeof buf, 0);
        if (n <= 0)
            break;
        sent += (uint64_t)n;
    }
    int64_t us = esp_timer_get_time() - t0;
    close(s);
    printf("TPUT,%" PRIu32 ",%" PRIu64 ",%" PRId64 ",%.0f\n", now_ms(), sent, us, us > 0 ? sent * 8000.0 / us : 0.0);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    if (strlen(CONFIG_RFLOG_WIFI_SSID) == 0) {
        for (;;) {
            printf("INFO,%" PRIu32 ",no SSID set: run idf.py menuconfig -> RF logger\n", now_ms());
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }
    wifi_up();
    for (;;) {
        xEventGroupWaitBits(s_ev, GOT_IP, pdFALSE, pdTRUE, portMAX_DELAY);
        int8_t pwr = 0;
        esp_wifi_get_max_tx_power(&pwr);
        printf("INFO,%" PRIu32 ",max tx power %.2f dBm\n", now_ms(), pwr * 0.25);
        log_rssi();
        log_tput();
    }
}
