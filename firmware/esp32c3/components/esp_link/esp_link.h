/* SPI link between the RP2350 (master) and the ESP32-C3 (slave), protocol version 1.
 *
 * Spec: docs/ARCHITECTURE.md, "SPI link protocol". Plain C, no ESP-IDF dependency: the ESP firmware ships
 * the slave half, sim/system/sysim.c runs both halves, and sim/system/el_vectors.c writes the transcript the
 * Rust master (croco-fw2, src/esp_link.rs) is tested against. The master half here is the reference the Rust
 * code must match byte for byte.
 */
#ifndef ESP_LINK_H
#define ESP_LINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EL_VERSION 1
#define EL_MB_CLEAR 0x200            /* mailbox bytes the master clears at start: header, request, response */
#define EL_HDR 12
#define EL_MAX_REQ 240
#define EL_MAX_RESP 255
#define EL_MAX_XFER (EL_HDR + 260) /* 255 + CRC, padded to 4 */

#define EL_M_MAGIC 0xC5
#define EL_S_MAGIC 0x5C
enum { EL_POLL = 0, EL_REQUEST = 1 };
enum { EL_F_RADIO_UP = 1, EL_F_BUSY = 2, EL_F_RESP = 4, EL_F_ACCEPTED = 8 };

/* Mailbox (cart RAM bank 15) offsets, see sim/system/README.md */
enum { MB_CMD = 0x00, MB_SEQ = 0x01, MB_STATUS = 0x02, MB_ACK = 0x03, MB_LENL = 0x04, MB_LENH = 0x05,
       MB_RAIL = 0x06, MB_RES = 0x07, MB_REQ = 0x10, MB_RESP = 0x100 };

#define EL_GAP_US 100                /* CS high between transactions, minimum */
#define EL_NO_RADIO_MS 5000          /* no valid ESP header for this long while a request is pending */
#define EL_RADIO_TIMEOUT_MS 12000    /* accepted, but no response */
#define EL_LIVE_STALE_MS 1000        /* rail/reservoir bytes cleared after this long without a valid header */

uint16_t el_crc16(const uint8_t *p, size_t n);
/* Bytes a body of n payload bytes takes on the wire (payload + CRC, padded to 4); 0 for n == 0. */
size_t el_body_len(size_t n);

/* ---------------------------------------------------------------- slave (ESP32-C3) */
typedef struct {
    /* live state, set by the application */
    uint8_t flags_live; /* EL_F_RADIO_UP */
    uint8_t pm_state, rail, res;
    /* current job */
    bool job, job_taken;
    uint8_t job_seq, job_cmd, job_len;
    char job_data[EL_MAX_REQ + 1];
    bool accepted;
    uint8_t accepted_seq;
    uint8_t session;  /* master's session byte (0 = none seen yet); a new one drops all job state */
    /* response waiting to be fetched */
    bool resp;
    uint8_t resp_seq, resp_status, resp_len;
    uint8_t resp_data[EL_MAX_RESP];
    uint32_t rx_ok, rx_bad;
} el_slave_t;

void el_slave_init(el_slave_t *s);
void el_slave_set_live(el_slave_t *s, bool radio_up, uint8_t pm_state, float rail_v, float res_v);
/* Fills buf (EL_MAX_XFER bytes) with what the next transaction sends; returns the bytes that carry data. */
size_t el_slave_build_tx(const el_slave_t *s, uint8_t *buf);
/* A transaction finished: rx holds the n bytes the master sent. */
void el_slave_on_rx(el_slave_t *s, const uint8_t *rx, size_t n);
/* Takes a newly accepted job (once). payload gets EL_MAX_REQ + 1 bytes, 0-terminated. */
bool el_slave_take_job(el_slave_t *s, uint8_t *seq, uint8_t *cmd, char *payload);
/* Answer for job seq; ignored if a newer request replaced that job. len is cut to EL_MAX_RESP. */
void el_slave_respond(el_slave_t *s, uint8_t seq, uint8_t status, const uint8_t *data, size_t len);

/* ---------------------------------------------------------------- master (reference for the RP2350) */
typedef struct {
    uint8_t last_seen_seq;
    bool pending, accepted;
    uint8_t seq;
    bool ack_valid;
    uint8_t ack;
    bool fetch_due;
    uint8_t fetch_len;
    bool ever_valid;
    uint8_t session;  /* random per master boot, never 0 */
    uint32_t t_pending_ms, t_valid_ms, t_accept_ms, t_req_ms;
    uint32_t xfers, bad_hdr, bad_body, resent;
} el_master_t;

/* mb = the 0x2000-byte mailbox bank. Clears A000-A1FF; call before the Game Boy runs. session: random, != 0, new
 * on every master boot (the ESP may stay powered through an RP2350 restart and must drop its old job). */
void el_master_init(el_master_t *m, uint8_t *mb, uint32_t now_ms, uint8_t session);
/* Builds the next transaction into tx (EL_MAX_XFER bytes); returns its length (multiple of 4). */
size_t el_master_next(el_master_t *m, uint32_t now_ms, uint8_t *mb, uint8_t *tx);
/* The transaction finished: rx holds the n bytes the ESP sent. */
void el_master_done(el_master_t *m, uint32_t now_ms, uint8_t *mb, const uint8_t *rx, size_t n);
/* True if another transaction is due in this tick (a response fetch), after EL_GAP_US. */
bool el_master_more(const el_master_t *m);

#endif
