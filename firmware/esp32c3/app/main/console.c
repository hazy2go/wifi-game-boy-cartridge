/* USB Serial/JTAG console (cart plugged into a computer through Croco's USB-C and the CH334P hub):
 *   status                 power, radio, link counters
 *   wifi <ssid> [<pass>]   store credentials and connect
 *   cal vcc|res <volts>    one-point ADC calibration against a multimeter (DESIGN_CALCS: required per board)
 *   fullcal                USB only: erase the RF calibration and reboot; the next start does the full one
 *   ota <https-url>        USB only: firmware update. Also USB only because a new PHY library can invalidate the
 *                          stored calibration, and ESP-IDF then falls back to a full one at the next start. */
#include <stdio.h>
#include <string.h>

#include "app.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_crt_bundle.h"
#include "esp_https_ota.h"
#include "esp_phy_init.h"
#include "esp_system.h"
#include "freertos/task.h"

static void status(void)
{
    xSemaphoreTake(gbw_pm_mtx, portMAX_DELAY);
    pm_t p = gbw_pm;
    xSemaphoreGive(gbw_pm_mtx);
    xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
    uint32_t ok = gbw_link.rx_ok, bad = gbw_link.rx_bad;
    xSemaphoreGive(gbw_link_mtx);
    float kv, kr;
    power_get_cal(&kv, &kr);
    printf("pm state %d  res %.3f V  rail %.3f V  usb %d  limiter-saturated %d\n", p.state, power_v_res(),
           power_v_rail(), power_on_usb(), power_lim_fault());
    printf("radio %s  connected %d  tx admitted %lu refused %lu  parks %lu\n",
           radio_state() == RADIO_STARTED ? "started" : radio_state() == RADIO_NOCAL ? "NO CAL DATA" : "off",
           radio_connected(), (unsigned long)p.frames_admitted, (unsigned long)p.frames_refused, (unsigned long)p.parks);
    printf("link rx ok %lu bad %lu  adc k_vcc %.4f k_res %.4f  tx dropped %lu\n", (unsigned long)ok, (unsigned long)bad, kv,
           kr, (unsigned long)radio_tx_drops());
}

static void handle(char *line)
{
    char *argv[4] = {0};
    int argc = 0;
    for (char *t = strtok(line, " \t\r\n"); t && argc < 4; t = strtok(NULL, " \t\r\n"))
        argv[argc++] = t;
    if (!argc)
        return;
    if (!strcmp(argv[0], "status")) {
        status();
    } else if (!strcmp(argv[0], "wifi") && argc >= 2) {
        radio_store_creds(argv[1], argc >= 3 ? argv[2] : "", 0);
        radio_autoconnect();
        printf("stored; connecting\n");
    } else if (!strcmp(argv[0], "cal") && argc == 3) {
        float kv, kr, v = strtof(argv[2], NULL);
        power_get_cal(&kv, &kr);
        if (!strcmp(argv[1], "vcc") && power_v_rail() > 0.5f)
            power_set_cal(kv * v / power_v_rail(), kr);
        else if (!strcmp(argv[1], "res") && power_v_res() > 0.5f)
            power_set_cal(kv, kr * v / power_v_res());
        else
            printf("usage: cal vcc|res <volts measured with a meter>\n");
        status();
    } else if (!strcmp(argv[0], "fullcal")) {
        if (!power_on_usb()) {
            printf("refused: only on USB power\n");
            return;
        }
        esp_phy_erase_cal_data_in_nvs();
        printf("calibration erased, rebooting\n");
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_restart();
    } else if (!strcmp(argv[0], "ota") && argc == 2) {
        if (!power_on_usb()) {
            printf("refused: only on USB power\n");
            return;
        }
        esp_http_client_config_t http = {.url = argv[1], .crt_bundle_attach = esp_crt_bundle_attach, .timeout_ms = 15000};
        esp_https_ota_config_t c = {.http_config = &http};
        esp_err_t e = esp_https_ota(&c);
        printf("ota: %s\n", esp_err_to_name(e));
        if (e == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        }
    } else {
        printf("commands: status | wifi <ssid> [<pass>] | cal vcc|res <volts> | fullcal | ota <https-url>\n");
    }
}

void console_task(void *arg)
{
    usb_serial_jtag_driver_config_t c = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&c));
    usb_serial_jtag_vfs_use_driver();
    setvbuf(stdin, NULL, _IONBF, 0);
    char line[160];
    for (;;) {
        if (fgets(line, sizeof line, stdin))
            handle(line);
        else
            vTaskDelay(pdMS_TO_TICKS(50));
    }
}
