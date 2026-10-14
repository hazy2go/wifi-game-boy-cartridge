/* WiFi cartridge ESP32-C3 app (step 4 in CLAUDE.md). The radio is powered only when the reservoir has passed
 * power-good (4.2 V), so app_main runs on a full reservoir; the power manager then decides everything else. */
#include "app.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_pm.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "main";

/* OTA rollback (sdkconfig): an image that has not run 5 s with all tasks up is rolled back at the next boot */
static void mark_valid(void *arg)
{
    esp_ota_mark_app_valid_cancel_rollback();
    ESP_LOGI(TAG, "image marked valid");
}

void app_main(void)
{
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* this also erases the stored RF calibration: the cart then refuses WiFi until it has seen USB */
        ESP_ERROR_CHECK(nvs_flash_erase());
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);
    ESP_LOGI(TAG, "nvs ok");

    /* auto light-sleep, which only happens in PARK: the SPI slave holds an APB lock the rest of the time */
    esp_pm_config_t pmc = {.max_freq_mhz = 160, .min_freq_mhz = 160, .light_sleep_enable = true};
    if (esp_pm_configure(&pmc) == ESP_OK) /* fails only without CONFIG_PM_ENABLE (QEMU test build): PARK then */
        ESP_LOGI(TAG, "pm ok");           /* cannot light-sleep */
    else
        ESP_LOGW(TAG, "power management not available: no light sleep in PARK");

    gbw_link_mtx = xSemaphoreCreateMutex();
    el_slave_init(&gbw_link);
    power_init();
    ESP_LOGI(TAG, "power ok");
    radio_init();
    ESP_LOGI(TAG, "radio ok");

    xTaskCreate(link_task, "link", 4096, NULL, configMAX_PRIORITIES - 2, NULL);
    xTaskCreate(power_task, "power", 4096, NULL, configMAX_PRIORITIES - 1, NULL);
    xTaskCreate(cmds_task, "cmds", 12288, NULL, 5, NULL);
    xTaskCreate(console_task, "console", 8192, NULL, 3, NULL);
    esp_timer_handle_t t;
    esp_timer_create_args_t ta = {.callback = mark_valid, .name = "ota_valid"};
    ESP_ERROR_CHECK(esp_timer_create(&ta, &t));
    ESP_ERROR_CHECK(esp_timer_start_once(t, 5 * 1000 * 1000));
    ESP_LOGI(TAG, "WiFi cartridge radio up (SPI link protocol v%d)", EL_VERSION);
}
