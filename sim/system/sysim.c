/* Full-system simulation of the WiFi cartridge (gate before ordering, LOGBOOK 14:57).
 *
 *   Game Boy (Peanut-GB, MIT) running a real ROM
 *     -> cartridge bus: MBC5 ROM/RAM as the RP2350 firmware serves it; RAM bank 15 = WiFi mailbox
 *     -> RP2350 firmware model: polls the mailbox every 1 ms and talks to the ESP with the SPI link protocol
 *        (docs/ARCHITECTURE.md), byte for byte, through the reference master in esp_link.c (CS = GPIO46,
 *        SCK/MOSI through U106, MISO through U105 which only drives while CS is low). Only for ROMs that opt in
 *        ("WIFICART" in bank 0 + MBC5 with 128 KB RAM).
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
#include "gbwifi.h"

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
#ifndef PARK_A
#define PARK_A 0.0014                    /* auto light-sleep, DTIM1, still associated (LOGBOOK 05:29 research: 1.4 mA).
                                            -DPARK_A=0.025 (modem-sleep park) gives radio resets on weak_plus: rejected */
#endif
#define CAL_MS 5                         /* partial RF calibration budget (A7 requirement) */
#define PG_ON 4.2
#define PG_OFF 2.0
#define SPI_HZ 8e6                       /* RP2350 -> ESP SPI clock */
#ifndef SPI_BER
#define SPI_BER 0.0                      /* bit error rate on MOSI and MISO (-DSPI_BER=1e-3 for the robustness check) */
#endif
#ifndef ESP_BOOT_MS
#define ESP_BOOT_MS 300                  /* power-good -> app_main: SPI slave queued, power manager starts (assumed, not measured) */
#endif
#ifndef BOOT_CPU_A
#define BOOT_CPU_A 0.025                 /* ESP current while booting (CPU, radio off) */
#endif
#define TX_MAX_AGE 2.0                   /* app radio.c: a frame waits in the TX queue until admitted, dropped when 2 s old */
#define TCP_RTO 1.5                      /* a dropped frame is sent again after the retransmission timeout (sdkconfig) */
#define NET_WAIT 1.0                     /* app cmds.c: a job waits this long for RUN */
#ifndef ADC_RES_GAIN
#define ADC_RES_GAIN 1.0                 /* reservoir ADC reading / true value (-DADC_RES_GAIN=1.025: reads 2.5 % high) */
#endif
#ifndef ADC_RAIL_GAIN
#define ADC_RAIL_GAIN 1.0
#endif
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
typedef struct { double tx_s; double wait_s; int mgmt; } step_t;   /* mgmt: sent by the WiFi library, not admitted */
static step_t steps[64];
static int nsteps, cur_step;
static double step_wait_until, op_deadline;
static int op_active, op_cmd, op_failed;
static char op_req[240], op_resp[256];
static int op_seq;
static int tx_hold;                      /* app: a frame waits for admission -> radio in modem-sleep so the reservoir can rise */
static double hold_since;                /* when the current frame started waiting */
static long tx_drops, tx_mgmt;
static int wifi_started;                 /* esp_wifi_start done (pm left BOOT once) */
static int assoc_active, assoc_step;     /* app: autoconnect from stored credentials, in RUN only */
static double assoc_until;
static long assoc_count;
static double esp_app_at;                /* SPI slave live from this time (after ESP_BOOT_MS) */
static el_slave_t esl;
static uint8_t esp_tx[EL_MAX_XFER];      /* the buffer the slave has queued for the next transaction */
static long tx_frames, tx_refused_ms;
static FILE *slog;

static double radio_current(void)
{
    if (!esp_up)
        return 0;
    if (t_now < esp_app_at)                   /* booting: CPU only */
        return BOOT_CPU_A;
    if (t_now < cal_until)                    /* calibration: 50 % TX duty */
        return fmod(t_now * 1000, 1.0) < 0.5 ? TX_A : RX_A;
    switch (pm_state(&pm)) {
    case PM_OFF: return 0;
    case PM_BOOT: return RX_A;
    case PM_PARK: return PARK_A;
    case PM_SLEEP: return t_now < tx_until ? TX_A : MODEM_A;
    default: return t_now < tx_until ? TX_A : tx_hold ? MODEM_A : RX_A;
    }
}

static void plan_op(int cmd)
{
    nsteps = 0; cur_step = 0;
#define STEP(tx, w) (steps[nsteps++] = (step_t){ (tx), (w), 0 })
#define MGMT(tx, w) (steps[nsteps++] = (step_t){ (tx), (w), 1 })
    switch (cmd) {
    case 1: break;                                                     /* PING: answered locally */
    case 2: STEP(0.3e-3, 25e-3); break;                                /* NTP request, reply after RTT */
    case 3: STEP(0.3e-3, 20e-3);                                       /* DNS */
            STEP(0.3e-3, 30e-3); STEP(0.5e-3, 0); STEP(0.3e-3, 40e-3); /* SYN, ACK + GET, response */
            STEP(0.3e-3, 2e-3); STEP(0.3e-3, 0); break;                /* ACK, FIN */
    case 4: break;                                                     /* POWER: local ADC */
    case 5: for (int ch = 0; ch < 13; ch++) STEP(0, 120e-3); break;    /* app: passive scan, 120 ms per channel, no TX */
    case 6: wifi_assoc = 0;                                            /* app: disconnect, then a full join on the known */
            MGMT(0.3e-3, 2e-3); MGMT(0.3e-3, 3e-3); MGMT(0.3e-3, 5e-3); /* channel: probe, auth, assoc, 4-way handshake */
            MGMT(0.4e-3, 5e-3); MGMT(0.4e-3, 10e-3);                   /* (EAPOL), all sent by the library: not admitted */
            STEP(0.5e-3, 50e-3); STEP(0.5e-3, 30e-3);                  /* DHCP discover/request: lwIP data, admitted */
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
    case 4: gbw_fmt_power(r, sizeof op_resp, (float)vgb, (float)vt); break;   /* ESP ADC1 via R118/R119, R116/R117 */
    case 5: {                                  /* shipped formatter (components/gbwifi) */
        char buf[2048]; host("scan", buf, sizeof buf);
        gbw_ap_t aps[16]; int n = 0;
        char *sv = NULL;   /* strtok_r: the input script parser owns strtok */
        for (char *ln = strtok_r(buf, "\n", &sv); ln && n < 16; ln = strtok_r(NULL, "\n", &sv)) {
            char name[64], ch[8], band[8], sec[8]; int rssi;
            if (sscanf(ln, "%63[^|]|%7[^|]|%7[^|]|%7[^|]|%d", name, ch, band, sec, &rssi) != 5) continue;
            if (strcmp(band, "2G")) continue;     /* the ESP32-C3 is 2.4 GHz only */
            snprintf(aps[n].ssid, sizeof aps[n].ssid, "%s", name);
            aps[n].rssi = (int8_t)rssi; aps[n].channel = (uint8_t)atoi(ch);
            aps[n].auth = !strcmp(sec, "OPEN") ? GBW_AUTH_OPEN : !strcmp(sec, "WPA3") ? GBW_AUTH_WPA3 : !strcmp(sec, "WPA2") ? GBW_AUTH_WPA2 : GBW_AUTH_WPA;
            n++;
        }
        gbw_fmt_scan(r, sizeof op_resp, aps, n);
        scan_count = n < GBW_SCAN_MAX ? n : GBW_SCAN_MAX;
        break;
    }
    case 6: {
        int idx; const char *pass;
        int e = gbw_parse_connect(op_req, scan_count, &idx, &pass);
        if (e == -2) { op_failed = 1; snprintf(r, sizeof op_resp, "WPA2 KEY < 8 CHARS"); break; }
        if (e) { op_failed = 1; snprintf(r, sizeof op_resp, "BAD NETWORK"); break; }
        char ip[64]; host("ip", ip, sizeof ip); ip[strcspn(ip, "\n")] = 0;
        snprintf(r, sizeof op_resp, "OK %s", ip);
        wifi_assoc = 1;
        break;
    }
    case 7: {                                  /* host gives raw closes; shipped formatter resamples */
        char buf[4096]; host("btc", buf, sizeof buf);
        if (!strncmp(buf, "ERR", 3)) { op_failed = 1; buf[strcspn(buf, "\n")] = 0; snprintf(r, sizeof op_resp, "%.30s", buf); break; }
        int hh, mm, off = 0;
        if (sscanf(buf, "%2d%2d|%n", &hh, &mm, &off) < 2 || !off) { op_failed = 1; snprintf(r, sizeof op_resp, "BAD DATA"); break; }
        double closes[200]; int n = 0; char *q = buf + off, *e;
        while (n < 200) { double v = strtod(q, &e); if (e == q) break; closes[n++] = v; q = e; }
        if (n < 2) { op_failed = 1; snprintf(r, sizeof op_resp, "BAD DATA"); break; }
        gbw_fmt_btc(r, sizeof op_resp, closes, n, hh, mm);
        break;
    }
    default: snprintf(r, sizeof op_resp, "ERR");
    }
    if (!op_failed && op_cmd == 6)
        wifi_assoc = 1;                       /* CONNECT joined; otherwise only the autoconnect associates */
    op_active = 0;
    el_slave_respond(&esl, (uint8_t)op_seq, op_failed ? 3 : 2, (const uint8_t *)r, strlen(r));
    fprintf(slog, "%8.3f  ESP: response seq %d status %d \"%s\"\n", t_now, op_seq, op_failed ? 3 : 2, r);
}

/* app radio.c tx_task: 1 = on air now; 0 = still queued (radio holds in modem-sleep, also in PARK); -1 = dropped
 * after TX_MAX_AGE in the queue, the stack sends it again later (TCP RTO / DHCP retry) */
static int try_admit(double tx_s)
{
    double ip_len = fmin(GBW_MTU, fmax(0.0, (tx_s - 20e-6) * 6e6 / 8 - 36));   /* IP length from the OFDM airtime */
    if (pm_state(&pm) == PM_RUN && pm_admit_tx(&pm, gbw_airtime_worst_s((size_t)ip_len), GBW_TX_W)) {
        tx_hold = 0;
        tx_frames++;
        return 1;
    }
    tx_refused_ms++;
    if (!tx_hold) { tx_hold = 1; hold_since = t_now; }
    if (t_now - hold_since >= TX_MAX_AGE) {
        tx_hold = 0;
        tx_drops++;
        return -1;
    }
    return 0;
}

static void esp_tick_1ms(void)
{
    if (!powered) {
        if (esp_up)
            fprintf(slog, "%8.3f  ESP: supply lost (power-good off) -> reset\n", t_now);
        esp_up = 0; wifi_assoc = 0; op_active = 0; tx_hold = 0; wifi_started = 0; assoc_active = 0;
        el_slave_init(&esl);                  /* RAM lost: job, response, accepted seq */
        el_slave_build_tx(&esl, esp_tx);
        return;
    }
    if (!esp_up) {                            /* power-on reset of the ESP: ROM bootloader, then app_main */
        esp_up = 1;
        esp_app_at = t_now + ESP_BOOT_MS * 1e-3;
        fprintf(slog, "%8.3f  ESP: boot, reservoir %.2f V\n", t_now, vt);
    }
    if (t_now < esp_app_at)
        return;
    if (t_now - 1e-3 < esp_app_at) {          /* app_main: power manager with the app's config (boot_ms 100) */
        pm_config_t cfg; pm_default_config(&cfg); cfg.c_res = CRES; cfg.boot_ms = GBW_BOOT_MS;   /* as the app */
#ifdef GB_WATCH
        cfg.gb_watch_v = GB_WATCH; cfg.gb_resume_v = GB_WATCH + 0.02f;   /* experiment: lower firmware rail watch */
#endif
        pm_init(&pm, &cfg);
    }
    pm_state_t before = pm_state(&pm);
    pm_tick_1ms(&pm, (float)(vt * ADC_RES_GAIN), (float)(vgb * ADC_RAIL_GAIN));   /* the app sees ADC readings */
    if (before != PM_BOOT && pm_state(&pm) == PM_BOOT && !wifi_started) {
        cal_until = t_now + CAL_MS * 1e-3;    /* esp_wifi_start: partial calibration (A7: 5 ms at 50 % TX) */
        wifi_started = 1;
    }
    /* app: autoconnect / reconnect from stored credentials, only in RUN; join frames are not admitted */
    if (wifi_started && !wifi_assoc && !assoc_active && pm_state(&pm) == PM_RUN && t_now >= cal_until) {
        assoc_active = 1; assoc_step = 0; assoc_until = t_now; assoc_count++;
        fprintf(slog, "%8.3f  ESP: connecting (stored credentials)\n", t_now);
    }
    if (assoc_active && t_now >= assoc_until && t_now >= tx_until) {
        static const double JOIN[7][3] = { {0.3e-3, 2e-3, 1}, {0.3e-3, 3e-3, 1}, {0.3e-3, 5e-3, 1}, {0.4e-3, 5e-3, 1},
                                           {0.4e-3, 10e-3, 1}, {0.5e-3, 50e-3, 0}, {0.5e-3, 30e-3, 0} };
        if (assoc_step >= 7) {
            assoc_active = 0; wifi_assoc = 1;
            fprintf(slog, "%8.3f  ESP: connected\n", t_now);
        } else if (JOIN[assoc_step][2]) {     /* probe/auth/assoc/EAPOL: the library sends them, no admission */
            tx_mgmt++; tx_until = t_now + JOIN[assoc_step][0]; assoc_until = tx_until + JOIN[assoc_step][1]; assoc_step++;
        } else {                              /* DHCP: lwIP data, through admit() like any other frame */
            int a = try_admit(JOIN[assoc_step][0]);
            if (a > 0) { tx_until = t_now + JOIN[assoc_step][0]; assoc_until = tx_until + JOIN[assoc_step][1]; assoc_step++; }
            else if (a < 0) assoc_until = t_now + TCP_RTO;   /* DHCP retransmits */
        }
    }
    if (pm_state(&pm) != before)
        fprintf(slog, "%8.3f  ESP: power state %d -> %d (res %.2f V, rail %.3f V)\n", t_now, before, pm_state(&pm), vt, vgb);
    el_slave_set_live(&esl, pm_state(&pm) == PM_RUN, (uint8_t)pm_state(&pm), (float)vgb, (float)vt);
    uint8_t seq, cmd;
    char payload[EL_MAX_REQ + 1];
    if (!op_active && el_slave_take_job(&esl, &seq, &cmd, payload)) {   /* app: one job at a time */
        op_seq = seq; op_cmd = cmd;
        snprintf(op_req, sizeof op_req, "%s", payload);
        op_active = 1; op_failed = 0; plan_op(op_cmd);
        step_wait_until = t_now + 0.2e-3;      /* parse */
        op_deadline = t_now + 8.0;
        fprintf(slog, "%8.3f  ESP: job seq %d cmd %d \"%s\"\n", t_now, seq, cmd, payload);
    }
    if (!op_active)
        goto out;
    if (t_now > op_deadline) { op_failed = 1; tx_hold = 0; esp_finish(); goto out; }   /* app JOB_MS 8 s */
    if (cur_step >= nsteps) { tx_hold = 0; esp_finish(); goto out; }
    if (t_now < step_wait_until || t_now < tx_until)
        goto out;
    /* app need_net: commands that use the network wait for the connection (or answer ERR NOT CONNECTED) */
    if ((op_cmd == 2 || op_cmd == 3 || op_cmd == 7) && !wifi_assoc) {
        if (t_now > op_deadline - 1.5) { op_failed = 1; esp_finish(); }
        goto out;
    }
    step_t *s = &steps[cur_step];
    if (s->mgmt) {                            /* library frame: on air without admission */
        tx_mgmt++;
        tx_until = t_now + s->tx_s;
        step_wait_until = tx_until + s->wait_s;
        cur_step++;
        goto out;
    }
    /* the app only knows the frame length: it admits with the 1 Mbit/s worst case (gbwifi) */
    if (s->tx_s > 0) {
        int a = try_admit(s->tx_s);
        if (a == 0)
            goto out;
        if (a < 0) {                          /* dropped: TCP sends it again after the RTO */
            step_wait_until = t_now + TCP_RTO;
            goto out;
        }
    }
    tx_until = t_now + s->tx_s;
    step_wait_until = tx_until + s->wait_s;
    cur_step++;
out:;
    /* no rebuild here: the app re-queues its SPI buffer only after a transaction (a queued DMA buffer must not be
       touched), so new state shows up one transaction later, as on the hardware */
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
        int esp_live = esp_up && t_now >= esp_app_at && pm_state(&pm) != PM_PARK;   /* light sleep: SPI slave off */
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
    int optin = 0;                             /* "WIFICART" anywhere in bank 0 (GBDK's crt0 owns 0x0150) */
    for (size_t i = 0; i + 8 <= rom_size && i + 8 <= 0x4000 && !optin; i++)
        optin = !memcmp(rom + i, "WIFICART", 8);
    wifi_rom = optin && rom_size > 0x150 && rom[0x147] >= 0x19 && rom[0x147] <= 0x1E && rom[0x149] == 0x04;
    fprintf(slog, "%8.3f  cart: %s\n", t_now, wifi_rom ? "WiFi ROM (WIFICART in bank 0, MBC5, 128 KB RAM): mailbox on bank 15"
                                                      : "no WIFICART opt-in: bank 15 is plain save RAM");
    el_slave_init(&esl);
    el_slave_build_tx(&esl, esp_tx);
    if (wifi_rom)
        el_master_init(&elm, MB, 0, 0xA5);    /* the cart only touches bank 15 for WiFi ROMs */

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
    snprintf(path, sizeof path, "%s/%s_tiles.bin", argv[5], argv[2]);   /* raw 20x18 tile indexes (any font) */
    p = fopen(path, "wb");
    for (int row = 0; row < 18; row++)
        fwrite(gb.vram + 0x1800 + row * 32, 1, 20, p);
    fclose(p);
    snprintf(path, sizeof path, "%s/%s_result.json", argv[5], argv[2]);
    p = fopen(path, "w");
    fprintf(p, "{\"scenario\": \"%s\", \"rsrc\": %.2f, \"seconds\": %.2f, \"console_min_v\": %.4f, \"time_below_4v5_ms\": %.3f, "
               "\"radio_resets\": %d, \"reservoir_min_v_while_on\": %.3f, \"tx_frames\": %ld, \"tx_refused_ms\": %ld, "
               "\"pm_parks\": %u, \"frames_admitted\": %u, \"frames_refused\": %u, \"wifi_rom\": %d, "
               "\"cart_writes_mailbox\": %ld, \"spi_xfers\": %u, \"spi_bad_hdr\": %u, \"spi_bad_body\": %u, "
               "\"spi_resent\": %u, \"spi_bit_errors\": %ld, \"spi_bus_share\": %.4f, \"esp_rx_bad\": %u, "
               "\"tx_drops\": %ld, \"tx_mgmt_unadmitted\": %ld, \"connects\": %ld}\n",
            argv[2], RSRC, t_now, gb_min, unsafe_s * 1000, resets, res_min_on, tx_frames, tx_refused_ms,
            pm.parks, pm.frames_admitted, pm.frames_refused, wifi_rom, cart_writes_mb, elm.xfers, elm.bad_hdr,
            elm.bad_body, elm.resent, spi_bit_errors, t_now > 0 ? spi_bus_s / t_now : 0, esl.rx_bad, tx_drops, tx_mgmt,
            assoc_count);
    fclose(p);
    fclose(slog);
    return 0;
}
