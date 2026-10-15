# ESP32-C3 radio app (cart, U104)

ESP-IDF v5.5.1. It builds and runs on a XIAO ESP32C3 with the XIAO profile on real WiFi (LOGBOOK 2026-10-16); **not on a cart yet** (the board is not built).

```
. ~/esp/esp-idf-v5.5.1/export.sh
cd firmware/esp32c3/app
idf.py build
idf.py -p /dev/cu.usbmodemXXXX flash monitor      # through Croco's USB-C (CH334P hub, port 2)
```

**The first flash must be on USB.** It wipes NVS, so the RF calibration data goes too. The next start on USB does the full calibration and stores it. In the cart slot without stored data, WiFi stays off and network commands answer `ERR NEEDS USB CAL`.

## What links unchanged from `../components`
| component | role | also runs in |
|---|---|---|
| `power_mgr` | per-frame TX admission, RUN/SLEEP/PARK, cart-rail watch 4.45/4.48 V | `sim/power` audits A5/A6, gate S1 |
| `esp_link` | SPI-slave half of the link protocol | gate S1 (byte for byte), Rust master vectors |
| `gbwifi` | screen text of every command, TX airtime estimate, MTU | gate S1, S1.8 host test, BTC demo |

## Rules from the audits, and where they are enforced
- **Full RF calibration only on USB** (`radio.c`, `start_task`): WiFi starts only if calibration data is stored in NVS, or on USB power (USB_PWR_ST, IO5).
  - In the cart slot, the start happens only in the pm's BOOT state (full reservoir). Retries outside BOOT are allowed on USB only.
  - `ota` and `fullcal` are USB-only: a new PHY library can make ESP-IDF fall back to a full calibration.
  - Remaining hole, which the app cannot close: a stored blob that passes ESP-IDF's format/MAC check but fails the PHY library's internal checksum still gets a full calibration (`ESP_CAL_DATA_CHECK_FAIL`, phy_init.c).
- **BOOT lasts `GBW_BOOT_MS`** (5 ms, the A7 calibration budget), not the power manager's default 100 ms. BOOT cannot sleep; 100 ms gave 51 radio resets on the weak console in S1.
- **11b data rates off** (`esp_wifi_config_11b_rate`), **no A-MPDU TX** (sdkconfig).
- **TCP MSS 300 by clamping:** the MSS option of every incoming SYN-ACK is rewritten to 300 (`gbw_clamp_mss`, an RX hook installed on every STA_START). The netif keeps MTU 1500, because lwIP's DHCP refuses an MTU below 576.
- **Every data frame goes through `pm_admit_tx`.**
  - The netif transmit hook only queues a copy (8 frames). `tx_task` sends each frame once it is admitted, sized with the 1 Mbit/s worst case (`gbw_airtime_worst_s`; ESP-IDF #19164 shows 11b rates even when they are off).
  - While a frame waits, the radio holds in modem-sleep. It is dropped only when 2 s old (TCP retransmits), so lwIP's thread never blocks.
- **Not admitted:** management and EAPOL frames, which the WiFi library sends itself (connect-scan probes, auth/assoc, 4-way handshake, null/PS-poll frames, MAC retries). To limit them:
  - the SCAN command is passive;
  - a connect goes to the scanned channel;
  - connect and reconnect (backoff 5-60 s) only start in PM_RUN.
- **PARK = auto light-sleep, still associated.**
  - The link disables the SPI slave (`spi_slave_disable`: it releases the APB lock that blocks light sleep; `spi_slave_free` would free queues the ISR still uses).
  - Every task idles for at least 10 ms there: the command task waits on a notification, not a 2 ms poll.
  - Simulated with 1.4 mA and a silent link: S1 passes, and sim/power A1-A7 pass at 1.4 mA. A 25 mA park was rejected (117 resets).
- **Jobs answer within 8 s** (`JOB_MS`): every wait in a job uses what is left of the budget. TIME does not wait once the clock is set; SNTP has no start-up delay.
- **On USB the cart rail is not watched** (`gbw_rail_for_pm`): the mux feeds the radio from VBUS, and the cart rail may be 0 V.
- **"On USB" means the ST pin AND a cart rail below 1 V** (`gbw_usb_trusted`): Croco's D3 leakage can fake ST in a hot cart (DESIGN_CALCS item 4). So **the first calibration needs the cart on USB and not in a Game Boy.**
- **Boot energy:** fast boot (no app hash check on power-on, quiet bootloader). The reservoir holds only ~6.75 mJ above power-good. A 2.5 ohm console needs the ESP at app_main within ~7.5 mJ (100 ms at 20 mA, S1.11); 300 ms at 25 mA boot-loops there.
- **OTA rollback on:** an image is marked valid after 5 s of running.

## Pins (DESIGN_CALCS, U104 table)
CS IO10, SCK IO6, MOSI IO7, MISO IO2 (through U105), ADC_VRES IO0 (ADC1_CH0), ADC_VCC IO1 (ADC1_CH1), USB_PWR_ST IO5, LIM_FAULT_N IO4. The spare IO3/IO20/IO21 get pull-downs.

## USB console
`status` · `wifi <ssid> [<pass>]` · `job <cmd> [payload]` (run a mailbox command locally) · `cal vcc|res <volts>` (one-point ADC calibration against a meter, needed per board) · `fullcal` · `ota <https-url>`

## XIAO ESP32C3 profile (bench tests)
`idf.py -B build_xiao -D SDKCONFIG=sdkconfig_xiao -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.xiao" build`. It uses the same SPI pins (the XIAO has IO2/IO6/IO7/IO10 on D0/D4/D5/D10) and fixed readings for the missing ADC dividers. **Never flash it onto a cart.** See `firmware/xiao/README.md` section 3.

## Still to measure on hardware
- RF calibration time (A7, XIAO test), then set `GBW_BOOT_MS` (components/gbwifi) from it and re-run S1 and the audits.
- Boot time to app_main and the boot current (a7_cal prints the time; current with a meter). Must stay within ~7.5 mJ.
- PARK current (light sleep, associated): 1.4 mA is assumed.
- The SPI slave at 8 MHz mode 0 with the 100 us gap.
- RSSI with the FPC antenna (XIAO `rf_logger`).
