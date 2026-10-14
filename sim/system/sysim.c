/* Full-system simulation of the WiFi cartridge (gate before ordering, LOGBOOK 14:57).
 *
 *   Game Boy (Peanut-GB, MIT) running a real ROM
 *     -> cartridge bus: MBC5 ROM/RAM as the RP2350 firmware serves it; RAM bank 15 = WiFi mailbox
 *     -> RP2350 firmware model: polls the mailbox every 1 ms and talks to the ESP with the SPI link protocol
 *        (docs/ARCHITECTURE.md), byte for byte, through the reference master in esp_link.c (CS = GPIO46,
 *        SCK/MOSI through U106, MISO through U105 which only drives while CS is low). Only for ROMs that opt in
 *        ("WIFICART" at 0x0150 + MBC5 with 128 KB RAM).
 *     -> ESP32-C3 firmware model: the SHIPPED SPI-slave protocol half (firmware/esp32c3/components/esp_link),
 *        command handlers, WiFi traffic as TX/RX frames, every TX frame admitted by the SHIPPED power manager
 *        (firmware/esp32c3/components/power_mgr/power_mgr.c)
 *     -> power path plant: console rail (Thevenin), input regulator (TLV431 loop, behavioural, A8-audited),
 *        150 mA limit, reservoir (T520B 3 x 330 uF), power-good comparator (4.2 V on / 2.0 V off),
 *        TPS63802 (efficiency), ESP current from the radio state. Same equations as sim/power/cosim.py.
 *
 * Time: the emulator runs one video frame (70224 clocks = 16.74 ms); the cartridge/ESP/power models then run
 * that 16.74 ms in 1 ms ticks (plant at 2 us). Cartridge accesses within a frame are taken at frame start.
 * Outputs (out/<scenario>_*): screen.pgm, tilemap.txt, log.txt, result.json.
 *
 * Usage: sysim ROM.gb SCENARIO RSRC_OHM SECONDS OUTDIR [INPUT_SCRIPT]
 * INPUT_SCRIPT (demo ROMs): space-separated tokens, pressed one after another: U D L R A B S(tart);
 *   Wn = wait until the ROM's wState ($C000) is n; Fn = wait n frames; P = screenshot now. Each press: 4 frames held, 6 released.
 *   A screenshot (out/<scenario>_<k>_s<state>.pgm) is taken 45 frames after every wState change.
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
#include "esp_link.h"

/* ------------------------------------------------------------------ parameters (with sources) */
static double RSRC;                      /* console source resistance: 0.7 fresh / 1.2 tired / 2.0 weak (run_sim.py) */
#ifndef VSRC
#define VSRC 5.0                         /* console open-circuit rail (-DVSRC=4.8 etc.) */
#endif
#define GB_LOAD 0.100                    /* console draw (A) */
#define LOGIC_LOAD 0.060                 /* cart logic: RP2350 + HyperRAM + flash (A, audit corner nominal) */
#define GB_CAP 100e-6
#define ILIM 0.150                       /* TPS2553 at RILIM 180k */
#ifndef VDPM
#define VDPM 4.588                       /* TLV431B regulator set-point (A8.1); R126 sets it */
#endif
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
#ifndef SPI_BER
#define SPI_BER 0.0                      /* bit error rate on MOSI and MISO (-DSPI_BER=1e-3 for the robustness check) */
#endif
#define ESP_BOOT_MS 300                  /* power-good -> SPI slave queued (ESP-IDF boot to app_main; assumed, not measured) */
#define MAILBOX_BANK 15

/* ------------------------------------------------------------------ cartridge memory */
static uint8_t *rom;
static size_t rom_size;
static uint8_t cram[16 * 0x2000];
#define MB (cram + MAILBOX_BANK * 0x2000)
static int wifi_rom;                     /* the ROM opted in: the cartridge serves the mailbox */
static long cart_writes_mb;              /* bytes the cartridge (not the ROM) wrote into bank 15 */

static uint8_t gb_rom_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < rom_size ? rom[a] : 0xFF; }
static uint8_t gb_cart_ram_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < sizeof cram ? cram[a] : 0xFF; }
static void gb_cart_ram_write(struct gb_s *gb, const uint_fast32_t a, const uint8_t v)
{
    (void)gb;
    if (a >= sizeof cram)
        return;
    cram[a] = v;                         /* the RP2350 sees these by polling the SEQ byte, no event */
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
        vgb += ((VSRC - vgb) / RSRC - GB_LOAD - LOGIC_LOAD - isw) / GB_CAP * dt;
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
static char op_req[240], op_resp[256];
static int op_seq;
static double esp_app_at;                /* SPI slave live from this time (after ESP_BOOT_MS) */
static el_slave_t esl;
static uint8_t esp_tx[EL_MAX_XFER];      /* the buffer the slave has queued for the next transaction */
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
    case 5: for (int ch = 0; ch < 13; ch++) STEP(0.3e-3, 100e-3); break;  /* active scan: probe + dwell per channel */
    case 6: if (wifi_assoc) {                                          /* (re)connect: always a full join */
                STEP(0.3e-3, 2e-3); STEP(0.3e-3, 3e-3); STEP(0.3e-3, 5e-3); STEP(0.4e-3, 5e-3); STEP(0.4e-3, 10e-3);
                STEP(0.5e-3, 50e-3); STEP(0.5e-3, 30e-3);
            }
            break;
    case 7: STEP(0.3e-3, 20e-3);                                       /* DNS api.binance.com */
            STEP(0.3e-3, 30e-3); STEP(0.5e-3, 40e-3);                  /* SYN; TLS ClientHello -> ServerHello+cert */
            STEP(0.6e-3, 40e-3); STEP(0.5e-3, 60e-3);                  /* key exchange; GET -> first data */
            for (int k = 0; k < 24; k++) STEP(0.2e-3, 3e-3);           /* ~24 KB JSON: ACK every 1-2 segments */
            STEP(0.3e-3, 2e-3); break;                                 /* close_notify / FIN */
    }
}

static const char *HOST_NET = "python3 host_net.py";   /* real scan / IP / BTC data (sim/system/host_net.py) */
static int host(const char *arg, char *out, size_t n)
{
    char cmd[128]; snprintf(cmd, sizeof cmd, "%s %s", HOST_NET, arg);
    FILE *p = popen(cmd, "r");
    if (!p) return -1;
    size_t k = fread(out, 1, n - 1, p); out[k] = 0;
    pclose(p);
    return (int)k;
}
static void commas(long v, char *o) { char t[32]; snprintf(t, sizeof t, "%ld", v); int L = strlen(t), j = 0;
    for (int i = 0; i < L; i++) { if (i && (L - i) % 3 == 0) o[j++] = ','; o[j++] = t[i]; } o[j] = 0; }
static int scan_count;

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
    case 5: {                                  /* the ESP formats display-ready lines for the 20-column screen */
        char buf[2048]; host("scan", buf, sizeof buf); r[0] = 0; scan_count = 0;
        char *sv = NULL;   /* strtok_r: the input script parser owns strtok */
        for (char *ln = strtok_r(buf, "\n", &sv); ln && scan_count < 8; ln = strtok_r(NULL, "\n", &sv)) {
            char name[64], ch[8], band[8], sec[8]; int rssi;
            if (sscanf(ln, "%63[^|]|%7[^|]|%7[^|]|%7[^|]|%d", name, ch, band, sec, &rssi) != 5) continue;
            char line[40], rs[8];
            if (rssi) snprintf(rs, sizeof rs, "%d", rssi); else snprintf(rs, sizeof rs, "--");
            snprintf(line, sizeof line, "%d %s CH%-3s %3s %s\n", ++scan_count, band, ch, rs, sec);
            strncat(r, line, sizeof op_resp - strlen(r) - 1);
        }
        if (!scan_count) snprintf(r, sizeof op_resp, "NO NETWORKS");
        break;
    }
    case 6: {
        const char *bar = strchr(op_req, '|');
        if (!bar || strlen(bar + 1) < 8) { op_failed = 1; snprintf(r, sizeof op_resp, "WPA2 KEY < 8 CHARS"); break; }
        char ip[64]; host("ip", ip, sizeof ip); ip[strcspn(ip, "\n")] = 0;
        snprintf(r, sizeof op_resp, "OK %s", ip);
        wifi_assoc = 1;
        break;
    }
    case 7: {
        char buf[2048]; host("btc", buf, sizeof buf);
        if (!strncmp(buf, "ERR", 3)) { op_failed = 1; buf[strcspn(buf, "\n")] = 0; snprintf(r, sizeof op_resp, "%.30s", buf); break; }
        double price, chg, hi, lo; char hm[8]; int off = 0;
        if (sscanf(buf, "%lf|%lf|%lf|%lf|%7[^|]|%n", &price, &chg, &hi, &lo, hm, &off) < 5 || !off) { op_failed = 1; snprintf(r, sizeof op_resp, "BAD DATA"); break; }
        char p[24], h[24], l[24]; commas((long)(price + 0.5), p); commas((long)(hi + 0.5), h); commas((long)(lo + 0.5), l);
        int n = snprintf(r, sizeof op_resp, "$%s  %+.2f%%\nH %s  L %s\n%.2s:%.2s UTC  15M BARS\n", p, chg, h, l, hm, hm + 2);
        char *q = buf + off;
        for (int c = 0; c < 128 && n < (int)sizeof op_resp - 1; c++) { int v = (int)strtol(q, &q, 10); r[n++] = (char)(0x20 + (v < 0 ? 0 : v > 63 ? 63 : v)); }
        r[n] = 0;
        break;
    }
    default: snprintf(r, sizeof op_resp, "ERR");
    }
    wifi_assoc = wifi_assoc || (!op_failed && (op_cmd == 3 || op_cmd == 2 || op_cmd == 6 || op_cmd == 7));
    op_active = 0;
    el_slave_respond(&esl, (uint8_t)op_seq, op_failed ? 3 : 2, (const uint8_t *)r, strlen(r));
    fprintf(slog, "%8.3f  ESP: response seq %d status %d \"%s\"\n", t_now, op_seq, op_failed ? 3 : 2, r);
}

static void esp_tick_1ms(void)
{
    if (!powered) {
        if (esp_up)
            fprintf(slog, "%8.3f  ESP: supply lost (power-good off) -> reset\n", t_now);
        esp_up = 0; wifi_assoc = 0; op_active = 0;
        el_slave_init(&esl);                  /* RAM lost: job, response, accepted seq */
        el_slave_build_tx(&esl, esp_tx);
        return;
    }
    if (!esp_up) {                            /* power-on reset of the ESP */
        pm_config_t cfg; pm_default_config(&cfg); cfg.c_res = CRES; cfg.boot_ms = CAL_MS;
#ifdef GB_WATCH
        cfg.gb_watch_v = GB_WATCH; cfg.gb_resume_v = GB_WATCH + 0.02f;   /* experiment: lower firmware rail watch */
#endif
        pm_init(&pm, &cfg);
        esp_up = 1;
        esp_app_at = t_now + ESP_BOOT_MS * 1e-3;
        fprintf(slog, "%8.3f  ESP: boot, reservoir %.2f V\n", t_now, vt);
    }
    pm_state_t before = pm_state(&pm);
    pm_tick_1ms(&pm, (float)vt, (float)vgb);
    if (before != PM_BOOT && pm_state(&pm) == PM_BOOT)
        cal_until = t_now + CAL_MS * 1e-3;
    if (pm_state(&pm) != before)
        fprintf(slog, "%8.3f  ESP: power state %d -> %d (res %.2f V, rail %.3f V)\n", t_now, before, pm_state(&pm), vt, vgb);
    el_slave_set_live(&esl, pm_state(&pm) == PM_RUN, (uint8_t)pm_state(&pm), (float)vgb, (float)vt);
    uint8_t seq, cmd;
    char payload[EL_MAX_REQ + 1];
    if (t_now >= esp_app_at && el_slave_take_job(&esl, &seq, &cmd, payload)) {
        op_seq = seq; op_cmd = cmd;
        snprintf(op_req, sizeof op_req, "%s", payload);
        op_active = 1; op_failed = 0; plan_op(op_cmd);
        step_wait_until = t_now + 0.2e-3;      /* parse */
        op_deadline = t_now + 8.0;
        fprintf(slog, "%8.3f  ESP: job seq %d cmd %d \"%s\"\n", t_now, seq, cmd, payload);
    }
    if (!op_active)
        goto out;
    if (t_now > op_deadline) { op_failed = 1; esp_finish(); goto out; }
    if (cur_step >= nsteps) { esp_finish(); goto out; }
    if (t_now < step_wait_until || t_now < tx_until)
        goto out;
    step_t *s = &steps[cur_step];
    if (s->tx_s > 0 && !(pm_state(&pm) == PM_RUN && pm_admit_tx(&pm, (float)s->tx_s, (float)(TX_A * 3.3 / ETA)))) {
        tx_refused_ms++;                      /* not allowed now: retry next ms */
        goto out;
    }
    tx_frames++;
    tx_until = t_now + s->tx_s;
    step_wait_until = tx_until + s->wait_s;
    cur_step++;
out:
    el_slave_build_tx(&esl, esp_tx);          /* re-queue between transactions with the current state */
}

/* ------------------------------------------------------------------ RP2350 firmware model (mailbox <-> SPI) */
static el_master_t elm;
static double spi_bus_s;
static long spi_bit_errors;
static unsigned long long rng = 0x9E3779B97F4A7C15ull;
static double urand(void)
{
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return (rng >> 11) * (1.0 / 9007199254740992.0);
}
static void wire(uint8_t *b, size_t n)        /* bit errors on the line */
{
    if (SPI_BER <= 0)
        return;
    for (size_t i = 0; i < n * 8; i++)
        if (urand() < SPI_BER) { b[i / 8] ^= (uint8_t)(0x80 >> (i % 8)); spi_bit_errors++; }
}
static void rp2350_tick_1ms(void)
{
    if (!wifi_rom)
        return;
    uint32_t now = (uint32_t)llround(t_now * 1000);
    int was_pending = elm.pending;
    uint8_t ack_before = MB[MB_ACK], status_before = MB[MB_STATUS];
    uint8_t mb_before[EL_MAX_XFER];
    memcpy(mb_before, MB, sizeof mb_before);
    for (int k = 0; k < 2; k++) {                  /* a poll, then at most one fetch in the same tick */
        uint8_t mosi[EL_MAX_XFER], miso[EL_MAX_XFER];
        size_t n = el_master_next(&elm, now, MB, mosi);
        int esp_live = esp_up && t_now >= esp_app_at;
        if (esp_live)
            memcpy(miso, esp_tx, n);
        else
            memset(miso, 0, n);                    /* U105 off or ESP not running: nothing valid on MISO */
        wire(mosi, n); wire(miso, n);
        if (esp_live) {
            el_slave_on_rx(&esl, mosi, n);
            el_slave_build_tx(&esl, esp_tx);       /* slave driver re-queues during the >= 100 us gap */
        }
        el_master_done(&elm, now, MB, miso, n);
        spi_bus_s += n * 8 / SPI_HZ + (k ? EL_GAP_US * 1e-6 : 0);   /* the gap only holds the bus before a fetch */
        if (!el_master_more(&elm))
            break;
    }
    for (int i = 0; i < MB_REQ; i++)                /* count the cartridge's own writes into the mailbox header */
        if (MB[i] != mb_before[i] && i != MB_SEQ && i != MB_CMD)
            cart_writes_mb++;
    if (!was_pending && elm.pending)
        fprintf(slog, "%8.3f  ROM: request seq %d cmd %d \"%.60s\"\n", t_now, elm.seq, MB[MB_CMD], (char *)MB + MB_REQ);
    if (MB[MB_ACK] != ack_before || (was_pending && !elm.pending))
        fprintf(slog, "%8.3f  ESP->ROM: seq %d status %d \"%.80s\"\n", t_now, MB[MB_ACK], MB[MB_STATUS], (char *)MB + MB_RESP);
    (void)status_before;
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
    vgb = VSRC - RSRC * GB_LOAD;               /* console on, cartridge empty: hot-plug at t = 0 */
    wifi_rom = rom_size > 0x158 && !memcmp(rom + 0x150, "WIFICART", 8) && rom[0x147] >= 0x19 && rom[0x147] <= 0x1E &&
               rom[0x149] == 0x04;
    fprintf(slog, "%8.3f  cart: %s\n", t_now, wifi_rom ? "WiFi ROM (WIFICART, MBC5, 128 KB RAM): mailbox on bank 15"
                                                      : "no WIFICART opt-in: bank 15 is plain save RAM");
    el_slave_init(&esl);
    el_slave_build_tx(&esl, esp_tx);
    el_master_init(&elm, MB, 0);

    static struct gb_s gb;
    if (gb_init(&gb, gb_rom_read, gb_cart_ram_read, gb_cart_ram_write, gb_error, NULL) != GB_INIT_NO_ERROR) {
        fprintf(stderr, "gb_init failed\n"); return 1;
    }
    gb_init_lcd(&gb, lcd_draw_line);
    int frames = (int)(seconds / (70224.0 / 4194304.0));
    char *script = argc > 6 ? strdup(argv[6]) : NULL, *tok = script ? strtok(script, " ") : NULL;
    int hold = 0, gap = 0, waitf = 0, last_state = -1, shot_at = -1, shots = 0;
    uint8_t press = 0;
    for (int fr = 0; fr < frames; fr++) {
        uint8_t st = gb.wram[0];
        if (st != last_state) { last_state = st; shot_at = fr + 45; fprintf(slog, "%8.3f  ROM: state %d\n", t_now, st); }
        if (tok && tok[0] == 'P' && !hold && !gap && !waitf) { shot_at = fr; tok = strtok(NULL, " "); }
        if (fr == shot_at) {
            snprintf(path, sizeof path, "%s/%s_%02d_s%d.pgm", argv[5], argv[2], ++shots, st);
            FILE *q = fopen(path, "wb"); fprintf(q, "P5 160 144 255\n");
            for (int y = 0; y < 144; y++) for (int x = 0; x < 160; x++) fputc(255 - fb[y][x] * 85, q);
            fclose(q);
        }
        if (hold) { hold--; if (!hold) { press = 0; gap = 6; } }
        else if (gap) gap--;
        else if (waitf) waitf--;
        else if (tok) {
            if (tok[0] == 'W') { if (st == atoi(tok + 1)) tok = strtok(NULL, " "); }
            else if (tok[0] == 'F') { waitf = atoi(tok + 1); tok = strtok(NULL, " "); }
            else {
                const char *k = "UDLRABS"; const uint8_t m[] = { JOYPAD_UP, JOYPAD_DOWN, JOYPAD_LEFT, JOYPAD_RIGHT, JOYPAD_A, JOYPAD_B, JOYPAD_START };
                const char *c = strchr(k, tok[0]); press = c ? m[c - k] : 0; hold = 4;
                tok = strtok(NULL, " ");
            }
        }
        gb.direct.joypad = (uint8_t)~press;
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
               "\"pm_parks\": %u, \"frames_admitted\": %u, \"frames_refused\": %u, \"wifi_rom\": %d, "
               "\"cart_writes_mailbox\": %ld, \"spi_xfers\": %u, \"spi_bad_hdr\": %u, \"spi_bad_body\": %u, "
               "\"spi_resent\": %u, \"spi_bit_errors\": %ld, \"spi_bus_share\": %.4f, \"esp_rx_bad\": %u}\n",
            argv[2], RSRC, t_now, gb_min, unsafe_s * 1000, resets, res_min_on, tx_frames, tx_refused_ms,
            pm.parks, pm.frames_admitted, pm.frames_refused, wifi_rom, cart_writes_mb, elm.xfers, elm.bad_hdr,
            elm.bad_body, elm.resent, spi_bit_errors, t_now > 0 ? spi_bus_s / t_now : 0, esl.rx_bad);
    fclose(p);
    fclose(slog);
    return 0;
}
