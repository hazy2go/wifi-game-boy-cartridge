/* Calibration current with a plain multimeter (Seeed XIAO ESP32C3).
 *
 * a7_cal measured the partial RF calibration at about 30 ms (LOGBOOK 2026-10-16), far above the 5 ms the power
 * audit assumed. Whether that matters depends on the current during those 30 ms, and a multimeter cannot see a
 * 30 ms pulse. Only the first calibration after a boot is slow (back-to-back repeats take 0.74 ms), so this app
 * reboots in a loop and alternates two phases of PHASE_S seconds:
 *   CAL    boot, partial calibration (the cart's mode), reboot
 *   DUMMY  boot, a busy wait as long as the measured calibration, reboot
 * The meter shows each phase's average. Extra current in the calibration = (CAL - DUMMY) x period / cal time;
 * the serial log prints the period and the calibration time. Start the reading a few seconds into a phase.
 * Needs stored calibration data: run a7_cal first (it leaves it in NVS). Brown-out resets are counted.
 * Meter: in series with the XIAO's 5V pin, XIAO USB unplugged (the on-board LDO passes the current through).
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_mac.h"
#include "esp_phy_init.h"
#include "esp_private/periph_ctrl.h"
#include "esp_private/phy.h"
#include "esp_rom_sys.h"
#include "esp_rtc_time.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"

#define PHASE_S 20
#define MAGIC 0xCA11CA11u

/* survives esp_restart() (not a power-off) */
static RTC_NOINIT_ATTR struct {
    uint32_t magic, boots, brownouts, n[2];
    uint64_t t0_us, cal_us_sum, last_print_us;
    uint32_t last_cal_us;
} s;

void app_main(void)
{
    uint64_t now = esp_rtc_get_time_us();
    esp_reset_reason_t why = esp_reset_reason();
    if (s.magic != MAGIC || why == ESP_RST_POWERON) {
        memset(&s, 0, sizeof s);
        s.magic = MAGIC;
        s.t0_us = now;
        s.last_cal_us = 30000;
    }
    if (why == ESP_RST_BROWNOUT || why == ESP_RST_POWERON) {
        /* brown-outs also go to NVS: the run on the meter ends with a power-off, which clears s */
        ESP_ERROR_CHECK(nvs_flash_init());
        nvs_handle_t h;
        ESP_ERROR_CHECK(nvs_open("calcur", NVS_READWRITE, &h));
        uint32_t bo = 0;
        nvs_get_u32(h, "brownout", &bo);
        if (why == ESP_RST_BROWNOUT) {
            s.brownouts++;
            nvs_set_u32(h, "brownout", ++bo);
            nvs_commit(h);
        } else {
            printf("CC power-on. Brown-outs in NVS since the flash: %" PRIu32 "\n", bo);
        }
        nvs_close(h);
    }
    s.boots++;
    int cal = ((now - s.t0_us) / 1000000 / PHASE_S) % 2 == 0;

    if (now - s.last_print_us > 5000000) {
        uint32_t nb = s.n[0] + s.n[1];
        printf("CC t=%3" PRIu64 " s phase %-5s boots %" PRIu32 " period %.1f ms cal mean %.2f ms brown-outs %" PRIu32 "\n",
               (now - s.t0_us) / 1000000, cal ? "CAL" : "DUMMY", s.boots,
               nb ? (now - s.t0_us) / 1000.0 / s.boots : 0.0, s.n[1] ? s.cal_us_sum / 1000.0 / s.n[1] : 0.0,
               s.brownouts);
        s.last_print_us = now;
    }

    if (cal) {
        ESP_ERROR_CHECK(nvs_flash_init());
        /* as in a7_cal run_cal(): what esp_wifi_init + esp_phy_enable do before the PHY calibrates */
        esp_wifi_bt_power_domain_on();
        wifi_module_enable();
        esp_phy_common_clock_enable();
#if CONFIG_ESP_PHY_ENABLE_USB
        phy_bbpll_en_usb(true);
#endif
        const esp_phy_init_data_t *init = esp_phy_get_init_data();
        esp_phy_calibration_data_t *c = calloc(1, sizeof *c);
        if (init == NULL || c == NULL || esp_phy_load_cal_data_from_nvs(c) != ESP_OK) {
            printf("CC no stored calibration data: run a7_cal first\n");
            abort();
        }
        uint8_t mac[6];
        ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
        memcpy(c->mac, mac, sizeof mac);
        int64_t t0 = esp_timer_get_time();
        register_chipv7_phy(init, c, PHY_RF_CAL_PARTIAL);
        s.last_cal_us = (uint32_t)(esp_timer_get_time() - t0);
        s.cal_us_sum += s.last_cal_us;
        s.n[1]++;
    } else {
        /* the same flash init as the CAL path, then wait as long as the calibration took */
        ESP_ERROR_CHECK(nvs_flash_init());
        esp_rom_delay_us(s.last_cal_us);
        s.n[0]++;
    }
    esp_restart();
}
