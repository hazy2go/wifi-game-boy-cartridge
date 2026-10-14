/* SPI link protocol v1, both halves. Spec: docs/ARCHITECTURE.md, "SPI link protocol". */
#include "esp_link.h"

#include <string.h>

#define EL_RESEND_MS 10 /* a REQUEST not yet seen as accepted is sent again after this long */

uint16_t el_crc16(const uint8_t *p, size_t n)
{
    uint16_t c = 0xFFFF;
    while (n--) {
        c ^= (uint16_t)(*p++ << 8);
        for (int i = 0; i < 8; i++)
            c = (uint16_t)(c & 0x8000 ? (c << 1) ^ 0x1021 : c << 1);
    }
    return c;
}

size_t el_body_len(size_t n)
{
    return n ? (n + 2 + 3) & ~(size_t)3 : 0;
}

static void put_crc(uint8_t *at, const uint8_t *p, size_t n)
{
    uint16_t c = el_crc16(p, n);
    at[0] = (uint8_t)(c >> 8);
    at[1] = (uint8_t)c;
}

static bool crc_ok(const uint8_t *p, size_t n)
{
    uint16_t c = el_crc16(p, n);
    return p[n] == (uint8_t)(c >> 8) && p[n + 1] == (uint8_t)c;
}

static bool hdr_ok(const uint8_t *h, size_t n, uint8_t magic)
{
    return n >= EL_HDR && h[0] == magic && crc_ok(h, 10);
}

static uint8_t volts_to_20mv(float v)
{
    float x = v * 50.0f;
    return x <= 0 ? 0 : x >= 255 ? 255 : (uint8_t)x;
}

/* ---------------------------------------------------------------- slave */
void el_slave_init(el_slave_t *s)
{
    memset(s, 0, sizeof *s);
}

void el_slave_set_live(el_slave_t *s, bool radio_up, uint8_t pm_state, float rail_v, float res_v)
{
    s->flags_live = radio_up ? EL_F_RADIO_UP : 0;
    s->pm_state = pm_state;
    s->rail = volts_to_20mv(rail_v);
    s->res = volts_to_20mv(res_v);
}

size_t el_slave_build_tx(const el_slave_t *s, uint8_t *buf)
{
    memset(buf, 0, EL_MAX_XFER);
    buf[0] = EL_S_MAGIC;
    buf[1] = (uint8_t)(s->flags_live | (s->job && !s->resp ? EL_F_BUSY : 0) | (s->resp ? EL_F_RESP : 0) |
                       (s->accepted ? EL_F_ACCEPTED : 0));
    buf[2] = s->pm_state;
    buf[3] = s->resp ? s->resp_seq : s->job_seq;
    buf[4] = s->resp ? s->resp_status : 0;
    buf[5] = s->resp ? s->resp_len : 0;
    buf[6] = s->rail;
    buf[7] = s->res;
    buf[8] = s->accepted ? s->accepted_seq : 0;
    buf[9] = EL_VERSION;
    put_crc(buf + 10, buf, 10);
    if (!s->resp || s->resp_len == 0)
        return EL_HDR;
    memcpy(buf + EL_HDR, s->resp_data, s->resp_len);
    put_crc(buf + EL_HDR + s->resp_len, buf + EL_HDR, s->resp_len);
    return EL_HDR + el_body_len(s->resp_len);
}

void el_slave_on_rx(el_slave_t *s, const uint8_t *rx, size_t n)
{
    if (!hdr_ok(rx, n, EL_M_MAGIC)) {
        s->rx_bad++;
        return;
    }
    if ((rx[2] & 1) && s->resp && rx[3] == s->resp_seq)
        s->resp = false; /* the master has stored it */
    if (rx[1] == EL_REQUEST) {
        uint8_t seq = rx[4], len = rx[6];
        if (len > EL_MAX_REQ || (len && (n < EL_HDR + el_body_len(len) || !crc_ok(rx + EL_HDR, len)))) {
            s->rx_bad++;
            return;
        }
        if (!(s->accepted && seq == s->accepted_seq)) { /* a repeat of the current request is ignored */
            s->job = true;
            s->job_taken = false;
            s->job_seq = seq;
            s->job_cmd = rx[5];
            s->job_len = len;
            memcpy(s->job_data, rx + EL_HDR, len);
            s->job_data[len] = 0;
            s->accepted = true;
            s->accepted_seq = seq;
            s->resp = false;
        }
    }
    s->rx_ok++;
}

bool el_slave_take_job(el_slave_t *s, uint8_t *seq, uint8_t *cmd, char *payload)
{
    if (!s->job || s->job_taken)
        return false;
    s->job_taken = true;
    *seq = s->job_seq;
    *cmd = s->job_cmd;
    memcpy(payload, s->job_data, (size_t)s->job_len + 1);
    return true;
}

void el_slave_respond(el_slave_t *s, uint8_t seq, uint8_t status, const uint8_t *data, size_t len)
{
    if (!s->job || seq != s->job_seq)
        return;
    if (len > EL_MAX_RESP)
        len = EL_MAX_RESP;
    memcpy(s->resp_data, data, len);
    s->resp_len = (uint8_t)len;
    s->resp_seq = seq;
    s->resp_status = status;
    s->resp = true;
    s->job = false;
}

/* ---------------------------------------------------------------- master (reference) */
void el_master_init(el_master_t *m, uint8_t *mb, uint32_t now_ms)
{
    memset(m, 0, sizeof *m);
    memset(mb, 0, MB_REQ);
    m->t_pending_ms = m->t_valid_ms = m->t_accept_ms = now_ms;
    m->t_req_ms = now_ms;
}

static void finish(el_master_t *m, uint8_t *mb, uint8_t status, const uint8_t *data, size_t len)
{
    memcpy(mb + MB_RESP, data, len);
    mb[MB_RESP + len] = 0;
    mb[MB_LENL] = (uint8_t)len;
    mb[MB_LENH] = (uint8_t)(len >> 8);
    mb[MB_STATUS] = status;
    mb[MB_ACK] = m->seq; /* last: the ROM polls ACK */
    m->pending = m->accepted = m->fetch_due = false;
    m->ack_valid = true;
    m->ack = m->seq;
}

static void finish_msg(el_master_t *m, uint8_t *mb, const char *msg)
{
    finish(m, mb, 3, (const uint8_t *)msg, strlen(msg));
}

static void master_hdr(const el_master_t *m, uint8_t *tx, uint8_t type)
{
    tx[0] = EL_M_MAGIC;
    tx[1] = type;
    tx[2] = m->ack_valid ? 1 : 0;
    tx[3] = m->ack_valid ? m->ack : 0;
}

size_t el_master_next(el_master_t *m, uint32_t now_ms, uint8_t *mb, uint8_t *tx)
{
    uint8_t s = mb[MB_SEQ];
    if (s != m->last_seen_seq) {
        m->last_seen_seq = s;
        m->pending = true;
        m->accepted = m->fetch_due = false;
        m->seq = s;
        m->t_pending_ms = now_ms;
        m->t_req_ms = now_ms - EL_RESEND_MS;
        mb[MB_STATUS] = 1;
    }
    if (m->pending && !m->accepted && now_ms - m->t_pending_ms >= EL_NO_RADIO_MS)
        finish_msg(m, mb, m->ever_valid && now_ms - m->t_valid_ms < EL_NO_RADIO_MS ? "RADIO NOT ACCEPTING"
                                                                                   : "NO RADIO POWER");
    else if (m->pending && m->accepted && now_ms - m->t_accept_ms >= EL_RADIO_TIMEOUT_MS)
        finish_msg(m, mb, "RADIO TIMEOUT");
    if (now_ms - m->t_valid_ms >= EL_LIVE_STALE_MS)
        mb[MB_RAIL] = mb[MB_RES] = 0;

    memset(tx, 0, EL_MAX_XFER);
    size_t n = EL_HDR;
    if (m->fetch_due) {
        master_hdr(m, tx, EL_POLL);
        n += el_body_len(m->fetch_len);
    } else if (m->pending && !m->accepted && now_ms - m->t_req_ms >= EL_RESEND_MS) {
        uint8_t len = 0;
        while (len < EL_MAX_REQ && mb[MB_REQ + len])
            len++;
        master_hdr(m, tx, EL_REQUEST);
        tx[4] = m->seq;
        tx[5] = mb[MB_CMD];
        tx[6] = len;
        if (len) {
            memcpy(tx + EL_HDR, mb + MB_REQ, len);
            put_crc(tx + EL_HDR + len, tx + EL_HDR, len);
            n += el_body_len(len);
        }
        if (now_ms != m->t_pending_ms)
            m->resent++;
        m->t_req_ms = now_ms;
    } else {
        master_hdr(m, tx, EL_POLL);
    }
    put_crc(tx + 10, tx, 10);
    return n;
}

void el_master_done(el_master_t *m, uint32_t now_ms, uint8_t *mb, const uint8_t *rx, size_t n)
{
    bool was_fetch = m->fetch_due;
    m->fetch_due = false;
    m->xfers++;
    if (!hdr_ok(rx, n, EL_S_MAGIC)) {
        m->bad_hdr++;
        return;
    }
    m->ever_valid = true;
    m->t_valid_ms = now_ms;
    mb[MB_RAIL] = rx[6];
    mb[MB_RES] = rx[7];
    if (!m->pending)
        return;
    uint8_t f = rx[1];
    if (!m->accepted) {
        if ((f & EL_F_ACCEPTED) && rx[8] == m->seq) {
            m->accepted = true;
            m->t_accept_ms = now_ms;
        } else {
            return;
        }
    }
    bool mine = rx[3] == m->seq;
    if ((f & EL_F_RESP) && mine) {
        uint8_t len = rx[5];
        if (len == 0) {
            finish(m, mb, rx[4], rx, 0);
        } else if (was_fetch && n >= EL_HDR + el_body_len(len)) {
            if (crc_ok(rx + EL_HDR, len))
                finish(m, mb, rx[4], rx + EL_HDR, len);
            else
                m->bad_body++;
        } else {
            m->fetch_due = true;
            m->fetch_len = len;
        }
    } else if (!((f & EL_F_BUSY) && mine)) {
        m->accepted = false; /* the ESP lost the job (restart): send the request again */
        m->t_req_ms = now_ms - EL_RESEND_MS;
    }
}

bool el_master_more(const el_master_t *m)
{
    return m->fetch_due;
}
