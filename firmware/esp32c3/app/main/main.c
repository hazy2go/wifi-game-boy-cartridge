/* WiFi cartridge ESP32-C3 app (step 4 in CLAUDE.md). The radio is powered only when the reservoir has passed
 * power-good (4.2 V), so app_main runs on a full reservoir; the power manager then decides everything else. */
#include "app.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "freertos/task.h"
#include "nvs_flash.h"

void app_main(void)
{
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* this also erases the stored RF calibration: the cart then refuses WiFi until it has seen USB */
        ESP_ERROR_CHECK(nvs_flash_erase());
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);

    /* auto light-sleep, which only happens in PARK: the SPI slave holds an APB lock the rest of the time */
    esp_pm_config_t pmc = {.max_freq_mhz = 160, .min_freq_mhz = 160, .light_sleep_enable = true};
    ESP_ERROR_CHECK(esp_pm_configure(&pmc));

    gbw_link_mtx = xSemaphoreCreateMutex();
    el_slave_init(&gbw_link);
    power_init();
    radio_init();

    xTaskCreate(link_task, "link", 4096, NULL, configMAX_PRIORITIES - 2, NULL);
    xTaskCreate(power_task, "power", 4096, NULL, configMAX_PRIORITIES - 1, NULL);
    xTaskCreate(cmds_task, "cmds", 12288, NULL, 5, NULL);
    xTaskCreate(console_task, "console", 8192, NULL, 3, NULL);
    ESP_LOGI("main", "WiFi cartridge radio up (SPI link protocol v%d)", EL_VERSION);
}
