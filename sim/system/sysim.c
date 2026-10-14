/* Full-system simulation of the WiFi cartridge (gate before ordering, LOGBOOK 14:57).
 *
 *   Game Boy (Peanut-GB, MIT) running a real ROM
 *     -> cartridge bus: MBC5 ROM/RAM as the RP2350 firmware serves it; RAM bank 15 = WiFi mailbox
 *     -> RP2350 firmware model: polls mailbox writes, frames requests over SPI (CS = GPIO46/DEBUG_UART_TX,
 *        SCK/MOSI through U106, MISO through U105 which only drives while CS is low)
 *     -> ESP32-C3 firmware model: command handlers, WiFi traffic as TX/RX frames, every TX frame admitted by
 *        the SHIPPED power manager (firmware/esp32c3/components/power_mgr/power_mgr.c)
 *     -> power path plant: console rail (Thevenin), input regulator (TLV431 loop, behavioural, A8-audited),
 *        150 mA limit, reservoir (T520B 3 x 330 uF), power-good comparator (4.2 V on / 2.0 V off),
 *        TPS63802 (efficiency), ESP current from the radio state. Same equations as sim/power/cosim.py.
 *
 * Time: the emulator runs one video frame (70224 clocks = 16.74 ms); the cartridge/ESP/power models then run
 * that 16.74 ms in 1 ms ticks (plant at 2 us). Cartridge accesses within a frame are taken at frame start.
 * Outputs (out/<scenario>_*): screen.pgm, tilemap.txt, log.txt, result.json.
 *
 * Usage: sysim ROM.gb SCENARIO RSRC_OHM SECONDS OUTDIR
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ENABLE_SOUND 0
#define ENABLE_LCD 1
#include "third_party/Peanut-GB/peanut_gb.h"
#include "power_mgr.h"

/* ------------------------------------------------------------------ parameters (with sources) */
static double RSRC;                      /* console source resistance: 0.7 fresh / 1.2 tired / 2.0 weak (run_sim.py) */
#define GB_LOAD 0.100                    /* console draw (A) */
#define LOGIC_LOAD 0.060                 /* cart logic: RP2350 + HyperRAM + flash (A, audit corner nominal) */
#define GB_CAP 100e-6
#define ILIM 0.150                       /* TPS2553 at RILIM 180k */
#define VDPM 4.588                       /* TLV431B regulator set-point (A8.1) */
#define DPM_GAIN 15.0                    /* A/V, A8 load regulation */
#ifndef RPATH
#define RPATH 0.0                        /* board copper J5 VCC -> reservoir (-DRPATH from hw/rev-b/tools/path_res.py) */
#endif
#define RSW (0.30 + RPATH)               /* TPS2553 + SiA975DJ pair + routed copper */
#define CRES 0.99e-3                     /* 3 x T520B 330 uF (nominal) */
#ifndef RESR
#define RESR 0.0133                      /* 3 x T520B ESR in parallel; -DRESR adds each cap's copper to U103 VIN */
#endif
#define ETA 0.88
#define RX_A 0.084                       /* ESP32-C3 RX at 3.3 V */
#define TX_A 0.350                       /* ESP32-C3-MINI-1U TX peak (datasheet audit) */
#define MODEM_A 0.025
#define PARK_A 0.001
#define CAL_MS 5                         /* partial RF calibration budget (A7 requirement) */
#define PG_ON 4.2
#define PG_OFF 2.0
#define SPI_HZ 8e6                       /* RP2350 -> ESP SPI clock */
#define MAILBOX_BANK 15

/* ------------------------------------------------------------------ cartridge memory */
static uint8_t *rom;
static size_t rom_size;
static uint8_t cram[16 * 0x2000];
#define MB (cram + MAILBOX_BANK * 0x2000)
enum { MB_CMD = 0x00, MB_SEQ = 0x01, MB_STATUS = 0x02, MB_ACK = 0x03, MB_LENL = 0x04, MB_LENH = 0x05,
       MB_RAIL = 0x06, MB_RES = 0x07, MB_REQ = 0x10, MB_RESP = 0x100 };
static int mb_seq_pending = -1;          /* seq written by the ROM, not yet taken by the firmware */

static uint8_t gb_rom_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < rom_size ? rom[a] : 0xFF; }
static uint8_t gb_cart_ram_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < sizeof cram ? cram[a] : 0xFF; }
static void gb_cart_ram_write(struct gb_s *gb, const uint_fast32_t a, const uint8_t v)
{
    (void)gb;
    if (a >= sizeof cram)
        return;
    cram[a] = v;
    if (a == MAILBOX_BANK * 0x2000 + MB_SEQ)
        mb_seq_pending = v;              /* the RP2350's write-sniff DMA logs this address */
}
static void gb_error(struct gb_s *gb, const enum gb_error_e e, const uint16_t addr)
{
    (void)gb; fprintf(stderr, "emulator error %d at %04x\n", e, addr); exit(2);
}

static uint8_t fb[144][160];
static void lcd_draw_line(struct gb_s *gb, const uint8_t px[160], const uint_fast8_t line)
{
    (void)gb;
    for (int x = 0; x < 160; x++)
        fb[line][x] = px[x] & 3;
}

/* ------------------------------------------------------------------ power plant */
static double vgb, vc, vt, t_now, gb_min = 10, unsafe_s, res_min_on = 10;
static int powered, resets;
static double radio_current(void);
static void plant_ms(void)
{
    const double dt = 2e-6;
    for (int k = 0; k < 500; k++) {
        double iload = powered ? radio_current() : 0.0;
        double lim = fmax(0.0, fmin(ILIM, DPM_GAIN * (vgb - VDPM)));
        double isw = 0, ibb = 0;
        for (int it = 0; it < 6; it++) {
            isw = fmax(0.0, fmin(lim, (vgb - vt) / RSW));
            ibb = 3.3 * iload / (ETA * fmax(vt, 1.0)) * 0.5 * (1 + tanh((vt - 1.6) / 0.02));
            vt = vc + RESR * (isw - ibb - vt / 1e5);
        }
        double icap = isw - ibb - vt / 1e5;
        if (!powered && vt >= PG_ON)
            powered = 1;
        else if (powered && vt < PG_OFF) {
            powered = 0;
            resets++;
        }
        if (powered && vt < res_min_on)
            res_min_on = vt;
        if (t_now > 0.05 && vgb < gb_min)    /* after the 50 ms plug-in transient */
            gb_min = vgb;
        if (t_now > 0.05 && vgb < 4.5)
            unsafe_s += dt;
        vgb += ((5.0 - vgb) / RSRC - GB_LOAD - LOGIC_LOAD - isw) / GB_CAP * dt;
        vc += icap / CRES * dt;
        t_now += dt;
    }
}

/* ------------------------------------------------------------------ ESP32-C3 firmware model */
static pm_t pm;
static int esp_up;                       /* booted: power-good asserted, pm in BOOT/RUN/... */
static int wifi_assoc;                   /* associated to the AP */
static double tx_until;                  /* end of the TX frame on air (s) */
static double cal_until;
typedef struct { double tx_s; double wait_s; } step_t;
static step_t steps[64];
static int nsteps, cur_step;
static double step_wait_until, op_deadline;
static int op_active, op_cmd, op_failed;
static char op_req[240], op_resp[240];
static double spi_busy_until;
static int resp_ready;
static long tx_frames, tx_refused_ms;
static FILE *slog;

static double radio_current(void)
{
    if (!esp_up)
        return 0;
    if (t_now < cal_until)                    /* calibration: 50 % TX duty */
        return fmod(t_now * 1000, 1.0) < 0.5 ? TX_A : RX_A;
    switch (pm_state(&pm)) {
    case PM_OFF: return 0;
    case PM_BOOT: return RX_A;
    case PM_PARK: return PARK_A;
    case PM_SLEEP: return t_now < tx_until ? TX_A : MODEM_A;
    default: return t_now < tx_until ? TX_A : RX_A;
    }
}

static void plan_op(int cmd)
{
    nsteps = 0; cur_step = 0;
#define STEP(tx, w) (steps[nsteps++] = (step_t){ (tx), (w) })
    if (!wifi_assoc) {                        /* association: probe/auth/assoc/4-way handshake/DHCP */
        STEP(0.3e-3, 2e-3); STEP(0.3e-3, 3e-3); STEP(0.3e-3, 5e-3); STEP(0.4e-3, 5e-3); STEP(0.4e-3, 10e-3);
        STEP(0.5e-3, 50e-3); STEP(0.5e-3, 30e-3);
    }
    switch (cmd) {
    case 1: break;                                                     /* PING: answered locally */
    case 2: STEP(0.3e-3, 25e-3); break;                                /* NTP request, reply after RTT */
    case 3: STEP(0.3e-3, 20e-3);                                       /* DNS */
            STEP(0.3e-3, 30e-3); STEP(0.5e-3, 0); STEP(0.3e-3, 40e-3); /* SYN, ACK + GET, response */
            STEP(0.3e-3, 2e-3); STEP(0.3e-3, 0); break;                /* ACK, FIN */
    case 4: break;                                                     /* POWER: local ADC */
    }
}

static void esp_finish(void)
{
    char *r = op_resp;
    if (op_failed) {
        snprintf(r, sizeof op_resp, "ERR");
    } else switch (op_cmd) {
    case 1: snprintf(r, sizeof op_resp, "PONG"); break;
    case 2: {
        int s = (int)t_now;                                             /* simulated NTP server time */
        snprintf(r, sizeof op_resp, "2026-10-14 15:%02d:%02d", (s / 60) % 60, s % 60);
        break;
    }
    case 3: snprintf(r, sizeof op_resp, strstr(op_req, "/hello") ? "Hello from WiFi!" : "ok"); break;
    case 4: snprintf(r, sizeof op_resp, "RAIL %.2fV RES %.2fV", vgb, vt); break;   /* ESP ADC1 via R118/R119, R116/R117 */
    default: snprintf(r, sizeof op_resp, "ERR");
    }
    wifi_assoc = wifi_assoc || (!op_failed && op_cmd == 3) || (!op_failed && op_cmd == 2);
    op_active = 0;
    resp_ready = 1;
    spi_busy_until = t_now + (strlen(r) + 4) * 8 / SPI_HZ;              /* response frame back over SPI */
}

static void esp_tick_1ms(void)
{
    if (!powered) {
        if (esp_up)
            fprintf(slog, "%8.3f  ESP: supply lost (power-good off) -> reset\n", t_now);
        esp_up = 0; wifi_assoc = 0;
        if (op_active) { op_failed = 1; esp_finish(); }
        return;
    }
    if (!esp_up) {                            /* power-on reset of the ESP */
        pm_config_t cfg; pm_default_config(&cfg); cfg.c_res = CRES; cfg.boot_ms = CAL_MS;
        pm_init(&pm, &cfg);
        esp_up = 1;
        fprintf(slog, "%8.3f  ESP: boot, reservoir %.2f V\n", t_now, vt);
    }
    pm_state_t before = pm_state(&pm);
    pm_tick_1ms(&pm, (float)vt, (float)vgb);
    if (before != PM_BOOT && pm_state(&pm) == PM_BOOT)
        cal_until = t_now + CAL_MS * 1e-3;
    if (pm_state(&pm) != before)
        fprintf(slog, "%8.3f  ESP: power state %d -> %d (res %.2f V, rail %.3f V)\n", t_now, before, pm_state(&pm), vt, vgb);
    if (!op_active)
        return;
    if (t_now > op_deadline) { op_failed = 1; esp_finish(); return; }
    if (cur_step >= nsteps) { esp_finish(); return; }
    if (t_now < step_wait_until || t_now < tx_until)
        return;
    step_t *s = &steps[cur_step];
    if (s->tx_s > 0 && !(pm_state(&pm) == PM_RUN && pm_admit_tx(&pm, (float)s->tx_s, (float)(TX_A * 3.3 / ETA)))) {
        tx_refused_ms++;                      /* not allowed now: retry next ms */
        return;
    }
    tx_frames++;
    tx_until = t_now + s->tx_s;
    step_wait_until = tx_until + s->wait_s;
    cur_step++;
}

/* ------------------------------------------------------------------ RP2350 firmware model (mailbox <-> SPI) */
static int fw_req_seq = -1;
static void rp2350_tick_1ms(void)
{
    MB[MB_RAIL] = (uint8_t)fmin(255, vgb * 1000 / 20);
    MB[MB_RES] = (uint8_t)fmin(255, vt * 1000 / 20);
    static double first_seen = -1;
    if (mb_seq_pending >= 0 && fw_req_seq < 0 && !esp_up) {   /* radio still powering up: keep BUSY, wait <= 5 s */
        MB[MB_STATUS] = 1;
        if (first_seen < 0)
            first_seen = t_now;
        if (t_now - first_seen < 5.0)
            return;
    }
    if (mb_seq_pending >= 0 && fw_req_seq < 0 && t_now >= spi_busy_until) {
        first_seen = -1;
        fw_req_seq = mb_seq_pending; mb_seq_pending = -1;
        MB[MB_STATUS] = 1;
        op_cmd = MB[MB_CMD];
        memcpy(op_req, MB + MB_REQ, sizeof op_req - 1);
        op_req[sizeof op_req - 1] = 0;
        double spi = (strlen(op_req) + 4) * 8 / SPI_HZ;               /* request frame over SPI */
        if (!esp_up) {                                                  /* ESP not powered: firmware answers */
            op_failed = 1; op_active = 0; snprintf(op_resp, sizeof op_resp, "NO RADIO POWER"); resp_ready = 1;
        } else {
            op_active = 1; op_failed = 0; plan_op(op_cmd);
            step_wait_until = t_now + spi + 0.2e-3;                    /* + ESP parse */
            op_deadline = t_now + 8.0;
        }
        fprintf(slog, "%8.3f  ROM: request seq %d cmd %d \"%s\"\n", t_now, fw_req_seq, op_cmd, op_req);
    }
    if (resp_ready && t_now >= spi_busy_until && fw_req_seq >= 0) {
        size_t n = strlen(op_resp);
        memcpy(MB + MB_RESP, op_resp, n + 1);
        MB[MB_LENL] = n & 0xFF; MB[MB_LENH] = n >> 8;
        MB[MB_STATUS] = op_failed ? 3 : 2;
        MB[MB_ACK] = (uint8_t)fw_req_seq;
        fprintf(slog, "%8.3f  ESP->ROM: seq %d status %d \"%s\"\n", t_now, fw_req_seq, MB[MB_STATUS], op_resp);
        fw_req_seq = -1; resp_ready = 0;
    }
}

/* ------------------------------------------------------------------ main */
int main(int argc, char **argv)
{
    if (argc < 6) { fprintf(stderr, "usage: sysim ROM SCENARIO RSRC SECONDS OUTDIR\n"); return 1; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); rom_size = ftell(f); fseek(f, 0, SEEK_SET);
    rom = malloc(rom_size); fread(rom, 1, rom_size, f); fclose(f);
    RSRC = atof(argv[3]);
    double seconds = atof(argv[4]);
    char path[512];
    snprintf(path, sizeof path, "%s/%s_log.txt", argv[5], argv[2]);
    slog = fopen(path, "w");
    vgb = 5.0 - RSRC * GB_LOAD;               /* console on, cartridge empty: hot-plug at t = 0 */

    static struct gb_s gb;
    if (gb_init(&gb, gb_rom_read, gb_cart_ram_read, gb_cart_ram_write, gb_error, NULL) != GB_INIT_NO_ERROR) {
        fprintf(stderr, "gb_init failed\n"); return 1;
    }
    gb_init_lcd(&gb, lcd_draw_line);
    int frames = (int)(seconds / (70224.0 / 4194304.0));
    for (int fr = 0; fr < frames; fr++) {
        gb_run_frame(&gb);
        double frame_end = (fr + 1) * (70224.0 / 4194304.0);
        while (t_now < frame_end) {
            plant_ms();
            esp_tick_1ms();
            rp2350_tick_1ms();
        }
    }
    /* screen and tile map */
    snprintf(path, sizeof path, "%s/%s_screen.pgm", argv[5], argv[2]);
    FILE *p = fopen(path, "wb");
    fprintf(p, "P5 160 144 255\n");
    for (int y = 0; y < 144; y++)
        for (int x = 0; x < 160; x++)
            fputc(255 - fb[y][x] * 85, p);
    fclose(p);
    snprintf(path, sizeof path, "%s/%s_tilemap.txt", argv[5], argv[2]);
    p = fopen(path, "w");
    for (int row = 0; row < 18; row++) {
        for (int col = 0; col < 20; col++) {
            uint8_t t = gb.vram[0x1800 + row * 32 + col];
            fputc(t >= 32 && t < 127 ? t : '?', p);
        }
        fputc('\n', p);
    }
    fclose(p);
    snprintf(path, sizeof path, "%s/%s_result.json", argv[5], argv[2]);
    p = fopen(path, "w");
    fprintf(p, "{\"scenario\": \"%s\", \"rsrc\": %.2f, \"seconds\": %.2f, \"console_min_v\": %.4f, \"time_below_4v5_ms\": %.3f, "
               "\"radio_resets\": %d, \"reservoir_min_v_while_on\": %.3f, \"tx_frames\": %ld, \"tx_refused_ms\": %ld, "
               "\"pm_parks\": %u, \"frames_admitted\": %u, \"frames_refused\": %u}\n",
            argv[2], RSRC, t_now, gb_min, unsafe_s * 1000, resets, res_min_on, tx_frames, tx_refused_ms,
            pm.parks, pm.frames_admitted, pm.frames_refused);
    fclose(p);
    fclose(slog);
    return 0;
}
