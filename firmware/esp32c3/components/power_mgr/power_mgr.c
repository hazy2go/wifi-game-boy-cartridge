#include "power_mgr.h"

#include <string.h>

void pm_default_config(pm_config_t *cfg)
{
    cfg->v_start = 4.2f;
    cfg->v_frame_floor = 3.0f;
    cfg->v_psave = 3.5f;
    cfg->v_park = 3.0f;
    cfg->v_unpark = 4.2f;
    cfg->gb_watch_v = 4.55f;
    cfg->gb_resume_v = 4.60f;
    cfg->c_res = 0.99e-3f;
    cfg->boot_ms = 100;
}

void pm_init(pm_t *pm, const pm_config_t *cfg)
{
    memset(pm, 0, sizeof(*pm));
    pm->cfg = *cfg;
    pm->state = PM_OFF;
}

static bool gb_ok(const pm_t *pm)
{
    return !pm->gb_low;
}

void pm_tick_1ms(pm_t *pm, float v_res, float v_gb)
{
    const pm_config_t *c = &pm->cfg;
    pm->v_res = v_res;
    pm->v_gb = v_gb;
    /* hysteresis, otherwise RUN/SLEEP chatters every tick on a weak console */
    if (v_gb < c->gb_watch_v)
        pm->gb_low = true;
    else if (v_gb > c->gb_resume_v)
        pm->gb_low = false;
    switch (pm->state) {
    case PM_OFF:
        if (v_res >= c->v_start) {
            pm->state = PM_BOOT;
            pm->boot_elapsed_ms = 0;
        }
        break;
    case PM_BOOT:
        /* the calibration cannot be interrupted; count it down */
        if (++pm->boot_elapsed_ms >= c->boot_ms)
            pm->state = PM_RUN;
        break;
    case PM_RUN:
    case PM_SLEEP:
        if (v_res < c->v_park) {
            pm->state = PM_PARK;
            pm->parks++;
        } else if (v_res < c->v_psave || !gb_ok(pm)) {
            pm->state = PM_SLEEP;
        } else {
            pm->state = PM_RUN;
        }
        break;
    case PM_PARK:
        if (v_res >= c->v_unpark)
            pm->state = PM_RUN;
        break;
    }
}

bool pm_admit_tx(pm_t *pm, float frame_s, float p_frame_w)
{
    if (pm->state != PM_RUN || !gb_ok(pm)) {
        pm->frames_refused++;
        return false;
    }
    /* worst case: no input current during the frame, energy comes from the reservoir alone:
     * 1/2 C (V0^2 - V1^2) = P t  ->  V1^2 = V0^2 - 2 P t / C */
    float v_end2 = pm->v_res * pm->v_res - 2.0f * p_frame_w * frame_s / pm->cfg.c_res;
    float floor2 = pm->cfg.v_frame_floor * pm->cfg.v_frame_floor;
    if (v_end2 > floor2) {
        pm->frames_admitted++;
        return true;
    }
    pm->frames_refused++;
    return false;
}

pm_state_t pm_state(const pm_t *pm)
{
    return pm->state;
}
