# ESP32-C3 radio app (cart, U104)

ESP-IDF v5.5.1. It builds; **not run on hardware yet** (the board is not built).

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
  - `ota` and `fullcal` are USB-only too: a new PHY library can make ESP-IDF fall back to a full calibration.
- **11b data rates off** (`esp_wifi_config_11b_rate`), **no A-MPDU TX** (sdkconfig), **STA MTU 340**, so lwIP sends 300-byte TCP segments.
- **Every data frame goes through `pm_admit_tx`** (`radio.c`, a netif transmit hook). Admission uses the 1 Mbit/s worst-case airtime (`gbw_airtime_worst_s`), because ESP-IDF #19164 shows 11b rates in use even when switched off.
  - While a frame waits, the radio holds in modem-sleep so the reservoir can rise. Without that hold, the weak console (2.0 ohm) starved in S1: it hovered at the 3.5 V SLEEP line, where no worst-case frame fits.
  - A frame is dropped after 200 ms or when the pm leaves RUN; TCP retransmits it.
- **Not covered by admission:** management and EAPOL frames, which the WiFi library sends itself. Scans are passive (no probe requests), and scan/connect only start while the pm is in RUN.
- **PARK = auto light-sleep, still associated.** The SPI slave holds an APB lock that blocks light sleep, so the link frees it during PARK; the master repeats meanwhile.
  - Simulated with 1.4 mA and a silent link: S1 passes, and the sim/power audits A1-A7 pass at 1.4 mA too.
  - A modem-sleep park (25 mA) was simulated too and **rejected**: 117 radio resets on weak_plus (LOGBOOK).
- On USB, the cart rail is not watched (`gbw_rail_for_pm`): the mux feeds the radio from VBUS, and the cart rail may be 0 V.

## Pins (DESIGN_CALCS, U104 table)
CS IO10, SCK IO6, MOSI IO7, MISO IO2 (through U105), ADC_VRES IO0 (ADC1_CH0), ADC_VCC IO1 (ADC1_CH1), USB_PWR_ST IO5, LIM_FAULT_N IO4. The spare IO3/IO20/IO21 get pull-downs.

## USB console
`status` · `wifi <ssid> [<pass>]` · `cal vcc|res <volts>` (one-point ADC calibration against a meter, needed per board) · `fullcal` · `ota <https-url>`

## Still to measure on hardware
- RF calibration time (A7, XIAO test), then set `boot_ms` from it.
- PARK current (light sleep, associated): 1.4 mA is assumed.
- ESP boot time to the first queued SPI transaction: 300 ms is assumed in the sim.
- The SPI slave at 8 MHz mode 0 with the 100 us gap.
- RSSI with the FPC antenna (XIAO `rf_logger`).
