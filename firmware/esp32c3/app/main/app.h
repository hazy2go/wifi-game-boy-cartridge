/* WiFi cartridge ESP32-C3 app. Pin map: docs/design/DESIGN_CALCS.md (U104 table). */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_link.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "gbwifi.h"
#include "power_mgr.h"

/* U104 pins */
#define PIN_ESP_CS 10    /* FSPICS0, from RP2350 GPIO46 through U106 */
#define PIN_ESP_SCK 6    /* FSPICLK */
#define PIN_ESP_MOSI 7   /* FSPID */
#define PIN_ESP_MISO 2   /* FSPIQ -> U105 (driven only while CS is low) */
#define PIN_USB_PWR_ST 5 /* TPS2116 ST: high = running from USB (VIN1) */
#define PIN_LIM_FAULT 4  /* TPS2553 /FAULT: low = limiter saturated (telemetry) */
/* ADC1_CH0 = IO0 = ADC_VRES, ADC1_CH1 = IO1 = ADC_VCC; dividers 680k / 200k */
#define ADC_DIV_K (200.0f / (680.0f + 200.0f))

/* ---------------------------------------------------------------- power.c */
extern pm_t gbw_pm;                 /* shipped power manager, guarded by gbw_pm_mtx */
extern SemaphoreHandle_t gbw_pm_mtx;
void power_init(void);
void power_task(void *arg);       /* 1 ms: ADC -> pm_tick_1ms, radio mode follows the pm state */
bool power_on_usb(void);
float power_v_res(void);
float power_v_rail(void);
bool power_lim_fault(void);
void power_set_cal(float k_vcc, float k_res); /* per-board ADC calibration factors (stored in NVS) */
void power_get_cal(float *k_vcc, float *k_res);

/* ---------------------------------------------------------------- link.c */
extern el_slave_t gbw_link;         /* shipped SPI-slave protocol half, guarded by gbw_link_mtx */
extern SemaphoreHandle_t gbw_link_mtx;
void link_task(void *arg);
void link_pause(bool pause);      /* PARK: free the SPI slave so the chip can light-sleep */

/* ---------------------------------------------------------------- radio.c */
typedef enum { RADIO_OFF, RADIO_NOCAL, RADIO_STARTED } radio_state_t;
void radio_init(void);            /* netif, MTU 340, TX admission hook; WiFi not started */
radio_state_t radio_state(void);
void radio_boot(void);            /* pm entered BOOT: start WiFi if calibration data allows it */
void radio_apply_mode(pm_state_t s); /* power save per pm state and TX hold */
bool radio_tx_hold(void);         /* a frame is waiting for admission */
bool radio_connected(void);
int radio_scan(gbw_ap_t *aps, int max);           /* blocking; waits for PM_RUN */
int radio_connect(const char *ssid, const char *pass, char *ip, int ip_len, int timeout_ms);
void radio_autoconnect(void);     /* stored credentials, once the pm is in RUN */
bool radio_wait_run(int timeout_ms);
void radio_store_creds(const char *ssid, const char *pass);

/* ---------------------------------------------------------------- cmds.c */
void cmds_task(void *arg);        /* takes jobs from the link and answers them */

/* ---------------------------------------------------------------- console.c */
void console_task(void *arg);     /* USB Serial/JTAG: status, wifi, cal, fullcal, ota */
