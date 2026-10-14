/* A7 bench test: how long does the ESP32-C3 RF calibration take? (Seeed XIAO ESP32C3)
 *
 * Audit A7 (docs/LOGBOOK.md 08:46) needs the partial RF calibration to fit in 5 ms at 50 % TX duty,
 * i.e. about 5 ms x (0.5 x 350 mA + 0.5 x 84 mA) at the 3.3 V rail. This firmware times the calibration
 * itself and prints the A7 verdict from the timing plus the datasheet currents. With a current probe,
 * trigger the scope on the marker pin and read the real profile instead.
 *
 * ESP-IDF picks the calibration mode at compile time and calibrates once per boot, so this firmware calls
 * the PHY library's calibration entry (register_chipv7_phy, the same call esp_phy_load_cal_and_init makes)
 * with the mode it wants, records the time in NVS and reboots into the next step:
 *   step 0       erase stored calibration data, then FULL (stores fresh data, like the first boot on USB)
 *   steps 1-10   PARTIAL (what the cart does at every power-up)
 *   steps 11-15  NONE (the fallback: stored data only)
 *   steps 16-20  normal esp_wifi_start() with the default partial mode, timed end to end (context)
 * After that, every power-on (unplug / replug USB) adds one more PARTIAL sample tagged "poweron". Those are the
 * samples that match the cart best: there the radio always starts from a real power-on.
 *
 * Marker: GPIO3 (XIAO pin D1) is high while the calibration (or esp_wifi_start) runs.
 * Hold BOOT (GPIO9) for 2 s to clear the results and start over.
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_phy_init.h"
#include "esp_private/phy.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define PIN_MARK GPIO_NUM_3 /* XIAO D1 */
#define PIN_BOOT GPIO_NUM_9 /* XIAO BOOT button, active low */

/* A7 model (sim/power/audit.py, cosim.py): calibration = TX 350 mA / RX 84 mA at 50 % duty, budget 5 ms */
#define I_TX_A 0.350f
#define I_RX_A 0.084f
#define V_RAIL 3.3f
#define BUDGET_MS 5.0f

enum { M_PARTIAL = PHY_RF_CAL_PARTIAL, M_NONE = PHY_RF_CAL_NONE, M_FULL = PHY_RF_CAL_FULL, M_WIFI = 3 };
static const char *const MODE_NAME[] = {"PARTIAL", "NONE", "FULL", "WIFI_START"};

static const uint8_t SEQ[] = {M_FULL,
                              M_PARTIAL, M_PARTIAL, M_PARTIAL, M_PARTIAL, M_PARTIAL,
                              M_PARTIAL, M_PARTIAL, M_PARTIAL, M_PARTIAL, M_PARTIAL,
                              M_NONE, M_NONE, M_NONE, M_NONE, M_NONE,
                              M_WIFI, M_WIFI, M_WIFI, M_WIFI, M_WIFI};
#define NSEQ (sizeof SEQ / sizeof SEQ[0])
#define MAX_REC 64

typedef struct {
    uint8_t mode;
    uint8_t reset;   /* esp_reset_reason_t */
    int8_t ret;      /* register_chipv7_phy() / esp_wifi_start() result (0 = ok) */
    uint8_t pad;
    uint32_t us;
} rec_t;

static rec_t s_rec[MAX_REC];
static size_t s_nrec;
static uint8_t s_step;

static void load_results(nvs_handle_t h)
{
    size_t len = sizeof s_rec;
    if (nvs_get_blob(h, "res", s_rec, &len) != ESP_OK)
        len = 0;
    s_nrec = len / sizeof(rec_t);
    if (nvs_get_u8(h, "step", &s_step) != ESP_OK)
        s_step = 0;
}

static void save_results(nvs_handle_t h)
{
    ESP_ERROR_CHECK(nvs_set_blob(h, "res", s_rec, s_nrec * sizeof(rec_t)));
    ESP_ERROR_CHECK(nvs_set_u8(h, "step", s_step));
    ESP_ERROR_CHECK(nvs_commit(h));
}

/* Same steps as esp_phy_load_cal_and_init() (esp_phy/src/phy_init.c, IDF v5.5.1), but with our mode. */
static int run_cal(esp_phy_calibration_mode_t mode, uint32_t *us)
{
    esp_phy_common_clock_enable();
#if CONFIG_ESP_PHY_ENABLE_USB
    phy_bbpll_en_usb(true);
#endif
    const esp_phy_init_data_t *init = esp_phy_get_init_data();
    esp_phy_calibration_data_t *cal = calloc(1, sizeof *cal);
    if (init == NULL || cal == NULL)
        abort();
    if (esp_phy_load_cal_data_from_nvs(cal) != ESP_OK && mode != PHY_RF_CAL_FULL) {
        printf("A7 no stored calibration data: cannot run %s\n", MODE_NAME[mode]);
        free(cal);
        esp_phy_release_init_data(init);
        return -1;
    }
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
    memcpy(cal->mac, mac, sizeof mac);

    gpio_set_level(PIN_MARK, 1);
    int64_t t0 = esp_timer_get_time();
    int ret = register_chipv7_phy(init, cal, mode);
    int64_t t1 = esp_timer_get_time();
    gpio_set_level(PIN_MARK, 0);

    if (mode == PHY_RF_CAL_FULL || (mode != PHY_RF_CAL_NONE && ret == ESP_CAL_DATA_CHECK_FAIL))
        ESP_ERROR_CHECK(esp_phy_store_cal_data_to_nvs(cal));
    free(cal);
    esp_phy_release_init_data(init);
    *us = (uint32_t)(t1 - t0);
    return ret;
}

static int run_wifi_start(uint32_t *us)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    gpio_set_level(PIN_MARK, 1);
    int64_t t0 = esp_timer_get_time();
    esp_err_t err = esp_wifi_start();
    int64_t t1 = esp_timer_get_time();
    gpio_set_level(PIN_MARK, 0);
    *us = (uint32_t)(t1 - t0);
    return err == ESP_OK ? 0 : -2;
}

static int cmp_u32(const void *a, const void *b)
{
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return (x > y) - (x < y);
}

/* Prints n/min/median/max for one group; returns the max in ms (0 if empty). */
static float stats(const char *label, uint8_t mode, int poweron)
{
    uint32_t v[MAX_REC];
    size_t n = 0;
    for (size_t i = 0; i < s_nrec; i++) {
        const rec_t *r = &s_rec[i];
        if (r->mode != mode || r->ret < 0)
            continue;
        if (poweron >= 0 && (r->reset == ESP_RST_POWERON) != poweron)
            continue;
        v[n++] = r->us;
    }
    if (n == 0) {
        printf("  %-22s n=0\n", label);
        return 0;
    }
    qsort(v, n, sizeof v[0], cmp_u32);
    printf("  %-22s n=%-2u min %7.3f  median %7.3f  max %7.3f ms\n", label, (unsigned)n, v[0] / 1000.0,
           v[n / 2] / 1000.0, v[n - 1] / 1000.0);
    return v[n - 1] / 1000.0f;
}

static void print_summary(void)
{
    printf("\n==== A7 results (%u records, step %u/%u) ====\n", (unsigned)s_nrec, s_step, (unsigned)NSEQ);
    for (size_t i = 0; i < s_nrec; i++)
        printf("A7,%u,%s,%d,%d,%" PRIu32 "\n", (unsigned)i, MODE_NAME[s_rec[i].mode], s_rec[i].reset, s_rec[i].ret,
               s_rec[i].us);
    stats("FULL", M_FULL, -1);
    float p_sw = stats("PARTIAL after reboot", M_PARTIAL, 0);
    float p_po = stats("PARTIAL after power-on", M_PARTIAL, 1);
    stats("NONE", M_NONE, -1);
    stats("esp_wifi_start total", M_WIFI, -1);

    float t = p_po > p_sw ? p_po : p_sw;
    if (t <= 0) {
        printf("A7: no PARTIAL samples yet\n");
        return;
    }
    float e50 = t * 1e-3f * (0.5f * I_TX_A + 0.5f * I_RX_A) * V_RAIL * 1e3f; /* mJ at the 3.3 V rail */
    float e100 = t * 1e-3f * I_TX_A * V_RAIL * 1e3f;
    float budget = BUDGET_MS * 1e-3f * (0.5f * I_TX_A + 0.5f * I_RX_A) * V_RAIL * 1e3f;
    printf("A7: worst PARTIAL %.3f ms. Energy at the 3.3 V rail: %.2f mJ at 50 %% TX, %.2f mJ at 100 %% TX; "
           "budget %.2f mJ (5 ms at 50 %%)\n", t, e50, e100, budget);
    if (e100 <= budget)
        printf("A7 PASS: fits the budget even if the radio transmits for the whole calibration\n");
    else if (e50 <= budget)
        printf("A7 PASS ONLY AT <= 50 %% TX DUTY: the timing alone does not settle it, measure the current profile\n");
    else
        printf("A7 FAIL at 50 %% TX duty: measure the current profile; if it really is that high, the firmware "
               "must start WiFi only with a full reservoir and a healthy console, or use NONE (stored data)\n");
    if (p_po == 0)
        printf("A7 note: no power-on sample yet. Unplug and replug USB a few times to add them.\n");
}

static bool boot_held_2s(void)
{
    for (int i = 0; i < 200; i++) {
        if (gpio_get_level(PIN_BOOT) != 0)
            return false;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return true;
}

void app_main(void)
{
    gpio_reset_pin(PIN_MARK);
    gpio_set_direction(PIN_MARK, GPIO_MODE_OUTPUT);
    gpio_set_level(PIN_MARK, 0);
    gpio_reset_pin(PIN_BOOT);
    gpio_set_direction(PIN_BOOT, GPIO_MODE_INPUT);
    gpio_pullup_en(PIN_BOOT);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open("a7", NVS_READWRITE, &h));
    load_results(h);

    esp_reset_reason_t why = esp_reset_reason();
    uint8_t mode = 0xff;
    if (s_step < NSEQ)
        mode = SEQ[s_step];
    else if (why == ESP_RST_POWERON)
        mode = M_PARTIAL;

    if (mode != 0xff && s_nrec < MAX_REC) {
        if (s_step == 0)
            esp_phy_erase_cal_data_in_nvs();
        uint32_t us = 0;
        int ret = mode == M_WIFI ? run_wifi_start(&us) : run_cal((esp_phy_calibration_mode_t)mode, &us);
        s_rec[s_nrec++] = (rec_t){.mode = mode, .reset = (uint8_t)why, .ret = (int8_t)ret, .us = us};
        if (s_step < NSEQ)
            s_step++;
        save_results(h);
        printf("A7 sample: %s %.3f ms (reset reason %d, ret %d)\n", MODE_NAME[mode], us / 1000.0, why, ret);
        if (s_step < NSEQ) {
            vTaskDelay(pdMS_TO_TICKS(300)); /* let the console drain */
            esp_restart();
        }
    }

    for (;;) {
        print_summary();
        for (int i = 0; i < 50; i++) {
            vTaskDelay(pdMS_TO_TICKS(100));
            if (gpio_get_level(PIN_BOOT) == 0 && boot_held_2s()) {
                printf("A7: clearing results\n");
                ESP_ERROR_CHECK(nvs_erase_all(h));
                ESP_ERROR_CHECK(nvs_commit(h));
                vTaskDelay(pdMS_TO_TICKS(300));
                esp_restart();
            }
        }
    }
}
