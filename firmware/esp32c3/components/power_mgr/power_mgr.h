/* Radio power manager for the WiFi cartridge (ESP32-C3 side).
 *
 * Plain C, no ESP-IDF dependency, so the exact code that ships is also compiled on the host and
 * co-simulated against the power-path plant model (sim/power/cosim.py).
 *
 * Inputs every 1 ms: reservoir voltage and cartridge 5V rail voltage (ADC, volts).
 * Outputs: radio mode (what the WiFi stack is allowed to do) and a per-frame TX admission decision.
 * Spec: docs/LOGBOOK.md 2026-10-14 05:22, "A5: firmware as a state machine".
 */
#ifndef POWER_MGR_H
#define POWER_MGR_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    PM_OFF = 0,   /* radio unpowered (reservoir below power-good) */
    PM_BOOT = 1,  /* WiFi start incl. RF calibration, uninterruptible */
    PM_RUN = 2,   /* RX baseline, TX frames individually admitted */
    PM_SLEEP = 3, /* modem-sleep: reservoir low or cart rail low */
    PM_PARK = 4,  /* controlled shutdown (light sleep) until reservoir recovers */
} pm_state_t;

typedef struct {
    float v_start;       /* reservoir voltage to start WiFi (V) */
    float v_frame_floor; /* reservoir must stay above this at the end of an admitted frame (V) */
    float v_psave;       /* below: modem-sleep (V) */
    float v_park;        /* below: park (V) */
    float v_unpark;      /* park exit (V) */
    float gb_watch_v;    /* cart rail below this: no TX, modem-sleep (V) */
    float gb_resume_v;   /* cart rail above this: rail ok again (hysteresis, V) */
    float c_res;         /* reservoir capacitance (F) */
    uint32_t boot_ms;    /* duration of WiFi start / RF calibration (ms) */
} pm_config_t;

typedef struct {
    pm_config_t cfg;
    pm_state_t state;
    uint32_t boot_elapsed_ms;
    float v_res;
    float v_gb;
    bool gb_low;
    uint32_t parks;
    uint32_t frames_admitted;
    uint32_t frames_refused;
} pm_t;

void pm_default_config(pm_config_t *cfg);
/* Power-on reset of the radio MCU (also called after a brown-out). */
void pm_init(pm_t *pm, const pm_config_t *cfg);
/* Called every 1 ms with fresh ADC samples. */
void pm_tick_1ms(pm_t *pm, float v_res, float v_gb);
/* Called by the WiFi send path before handing a frame to the radio.
 * frame_s: on-air duration of the frame, p_frame_w: power drawn from the reservoir while sending. */
bool pm_admit_tx(pm_t *pm, float frame_s, float p_frame_w);
pm_state_t pm_state(const pm_t *pm);

#endif
