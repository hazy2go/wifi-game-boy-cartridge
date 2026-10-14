/* SPI slave for the RP2350 link. The protocol is the shipped half in components/esp_link (spec:
 * docs/ARCHITECTURE.md). One transaction is always queued; its TX buffer is built right after the previous one
 * finished and is never touched while queued (the DMA owns it). */
#include <string.h>

#include "app.h"
#include "driver/spi_slave.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/task.h"

static const char *TAG = "link";

el_slave_t gbw_link;
SemaphoreHandle_t gbw_link_mtx;

static volatile bool s_pause_req, s_paused;
static bool s_pending; /* a queued transaction not yet collected */
static SemaphoreHandle_t s_resume;

static void slave_up(void)
{
    spi_bus_config_t bus = {
        .mosi_io_num = PIN_ESP_MOSI,
        .miso_io_num = PIN_ESP_MISO,
        .sclk_io_num = PIN_ESP_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = EL_MAX_XFER,
    };
    spi_slave_interface_config_t sl = {.spics_io_num = PIN_ESP_CS, .queue_size = 1, .mode = 0};
    ESP_ERROR_CHECK(spi_slave_initialize(SPI2_HOST, &bus, &sl, SPI_DMA_CH_AUTO));
}

void link_pause(bool pause)
{
    if (pause) {
        s_pause_req = true; /* takes effect after the queued transaction (or its timeout) */
    } else {
        s_pause_req = false;
        xSemaphoreGive(s_resume); /* a stale token is harmless: the pause loop re-checks the flag */
    }
}

void link_task(void *arg)
{
    s_resume = xSemaphoreCreateBinary();
    uint8_t *tx = heap_caps_malloc(EL_MAX_XFER, MALLOC_CAP_DMA);
    uint8_t *rx = heap_caps_malloc(EL_MAX_XFER, MALLOC_CAP_DMA);
    assert(tx && rx);
    slave_up();
    for (;;) {
        if (s_pause_req) {
            /* PARK: the slave driver holds an APB lock, which blocks light sleep (esp_pm: sleep only without
             * locks). The master sees invalid headers meanwhile and repeats (protocol: state, not messages). */
            spi_slave_disable(SPI2_HOST); /* releases the PM lock; queues stay valid (spi_slave_free would delete them
                                           * while a transaction is still mounted: use after free in the ISR) */
            s_paused = true;
            ESP_LOGI(TAG, "paused");
            while (s_pause_req) /* polls the flag too, so a resume between check and wait is never lost */
                xSemaphoreTake(s_resume, pdMS_TO_TICKS(20));
            s_paused = false;
            spi_slave_enable(SPI2_HOST);
            ESP_LOGI(TAG, "resumed");
        }
        static spi_slave_transaction_t t;
        if (!s_pending) {
            xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
            el_slave_build_tx(&gbw_link, tx);
            xSemaphoreGive(gbw_link_mtx);
            memset(rx, 0, EL_MAX_XFER);
            t = (spi_slave_transaction_t){.length = EL_MAX_XFER * 8, .tx_buffer = tx, .rx_buffer = rx};
            if (spi_slave_queue_trans(SPI2_HOST, &t, portMAX_DELAY) != ESP_OK)
                continue;
        }
        spi_slave_transaction_t *done = NULL;
        /* bounded wait so a PARK request is seen even when the master is quiet */
        while (spi_slave_get_trans_result(SPI2_HOST, &done, pdMS_TO_TICKS(50)) == ESP_ERR_TIMEOUT) {
            if (s_pause_req)
                break;
        }
        if (done == NULL) {
            /* pausing with a transaction still queued: it stays queued (the driver is only disabled) and completes
             * after resume; collect it before building the next one, so the DMA never owns two buffers */
            s_pending = true;
            continue;
        }
        s_pending = false;
        size_t n = done->trans_len / 8;
        xSemaphoreTake(gbw_link_mtx, portMAX_DELAY);
        el_slave_on_rx(&gbw_link, rx, n);
        bool job = gbw_link.job && !gbw_link.job_taken;
        xSemaphoreGive(gbw_link_mtx);
        if (job)
            cmds_notify();
    }
}
