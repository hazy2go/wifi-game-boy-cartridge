/* 1 ms power loop: reservoir + cart rail on ADC1 -> shipped power manager -> radio mode.
 * The power manager (components/power_mgr) is linked unchanged; this file only feeds it and acts on its state. */
#include <math.h>

#include "app.h"
#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "power";

pm_t gbw_pm;
SemaphoreHandle_t gbw_pm_mtx;

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static float s_k_vcc = 1.0f, s_k_res = 1.0f;   /* per-board one-point calibration (DESIGN_CALCS: required) */
static volatile float s_v_res, s_v_rail;
static volatile bool s_usb, s_fault;

/* a failed read keeps the last good value: a spurious 0 V would mean a spurious PARK */
static float read_v(adc_channel_t ch, float k, float last)
{
    int raw = 0, mv = 0;
    if (adc_oneshot_read(s_adc, ch, &raw) != ESP_OK)
        return last;
    if (!s_cali || adc_cali_raw_to_voltage(s_cali, raw, &mv) != ESP_OK)
        mv = raw * 1300 / 4095; /* no eFuse calibration: nominal 6 dB range, uncalibrated */
    return mv / 1000.0f / ADC_DIV_K * k;
}

void power_set_cal(float k_vcc, float k_res)
{
    s_k_vcc = k_vcc;
    s_k_res = k_res;
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_blob(h, "kvcc", &s_k_vcc, sizeof s_k_vcc);
        nvs_set_blob(h, "kres", &s_k_res, sizeof s_k_res);
        nvs_commit(h);
        nvs_close(h);
    }
}

void power_get_cal(float *k_vcc, float *k_res)
{
    *k_vcc = s_k_vcc;
    *k_res = s_k_res;
}

void power_init(void)
{
    gbw_pm_mtx = xSemaphoreCreateMutex();
    nvs_handle_t h;
    if (nvs_open("gbw", NVS_READONLY, &h) == ESP_OK) {
        size_t n = sizeof s_k_vcc;
        nvs_get_blob(h, "kvcc", &s_k_vcc, &n);
        n = sizeof s_k_res;
        nvs_get_blob(h, "kres", &s_k_res, &n);
        nvs_close(h);
    }
    adc_oneshot_unit_init_cfg_t ucfg = {.unit_id = ADC_UNIT_1};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&ucfg, &s_adc));
    /* ATTEN=2 (6 dB, 0-1.3 V): 3.5x more accurate at the pin than 12 dB (DESIGN_CALCS, ADC dividers) */
    adc_oneshot_chan_cfg_t ccfg = {.atten = ADC_ATTEN_DB_6, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CHANNEL_0, &ccfg));
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, ADC_CHANNEL_1, &ccfg));
    adc_cali_curve_fitting_config_t cal = {.unit_id = ADC_UNIT_1, .atten = ADC_ATTEN_DB_6, .bitwidth = ADC_BITWIDTH_DEFAULT};
    if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali) != ESP_OK) {
        s_cali = NULL; /* chip without ADC calibration eFuses: raw conversion (calibrate with `cal`) */
        ESP_LOGW(TAG, "no ADC curve-fitting calibration, using the nominal range");
    }

    gpio_config_t in = {.pin_bit_mask = (1ULL << PIN_USB_PWR_ST) | (1ULL << PIN_LIM_FAULT), .mode = GPIO_MODE_INPUT};
    ESP_ERROR_CHECK(gpio_config(&in)); /* both have 100k pull-ups to +3V3_RF on the board */
    /* spare pins (IO3, RXD0 = IO20, TXD0 = IO21): internal pull-downs (HWDG 1.3.10) */
    gpio_config_t spare = {.pin_bit_mask = (1ULL << 3) | (1ULL << 20) | (1ULL << 21), .mode = GPIO_MODE_INPUT,
                           .pull_down_en = GPIO_PULLDOWN_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&spare));

    pm_config_t cfg;
    pm_default_config(&cfg);
    cfg.boot_ms = GBW_BOOT_MS; /* the default (100) is not what the audits assume */
    pm_init(&gbw_pm, &cfg);
    ESP_LOGI(TAG, "ADC cal k_vcc %.4f k_res %.4f", s_k_vcc, s_k_res);
}

bool power_on_usb(void) { return s_usb; }
float power_v_res(void) { return s_v_res; }
float power_v_rail(void) { return s_v_rail; }
bool power_lim_fault(void) { return s_fault; }

void power_task(void *arg)
{
    TickType_t last = xTaskGetTickCount();
    pm_state_t prev = PM_OFF;
    bool hold_prev = false;
    for (;;) {
        /* PARK: wake every 10 ms only (light sleep), and give the pm 10 ticks of the same sample: in PARK only the
         * unpark threshold is evaluated, so this delays the exit by at most 10 ms */
        int ticks = prev == PM_PARK ? 10 : 1;
        vTaskDelayUntil(&last, pdMS_TO_TICKS(ticks));
        s_usb = gpio_get_level(PIN_USB_PWR_ST);
        s_fault = !gpio_get_level(PIN_LIM_FAULT);
        s_v_res = read_v(ADC_CHANNEL_0, s_k_res, s_v_res);
        s_v_rail = read_v(ADC_CHANNEL_1, s_k_vcc, s_v_rail);

        xSemaphoreTake(gbw_pm_mtx, portMAX_DELAY);
        for (int i = 0; i < ticks; i++)
            pm_tick_1ms(&gbw_pm, s_v_res, gbw_rail_for_pm(s_usb, s_v_rail));
        pm_state_t st = pm_state(&gbw_pm);
        xSemaphoreGive(gbw_pm_mtx);

        xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
        el_slave_set_live(&gbw_link, st == PM_RUN, (uint8_t)st, s_v_rail, s_v_res);
        xSemaphoreGive(gbw_link_mtx);

        bool hold = radio_tx_hold();
        if (st != prev) {
            ESP_LOGI(TAG, "pm %d -> %d (res %.2f V, rail %.3f V, usb %d)", prev, st, s_v_res, s_v_rail, s_usb);
            if (st == PM_BOOT)
                radio_boot();
            if (st == PM_PARK)
                link_pause(true);
            if (prev == PM_PARK)
                link_pause(false);
        }
        if (st != prev || hold != hold_prev)
            radio_apply_mode(st);
        prev = st;
        hold_prev = hold;
    }
}
