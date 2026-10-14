# XIAO ESP32C3 bench tests (hold points before ordering)

Two small ESP-IDF apps for the Seeed XIAO ESP32C3. They answer hold points 1 and 2 in `hw/rev-b/fab/ORDER.md`.
Both build with ESP-IDF v5.5.1 (`~/esp/esp-idf-v5.5.1`). They have not run on hardware yet.

```
. ~/esp/esp-idf-v5.5.1/export.sh
cd firmware/xiao/a7_cal          # or rf_logger
idf.py build
idf.py -p /dev/cu.usbmodemXXXX erase-flash flash monitor     # erase-flash only the first time
```
The XIAO has no on-board antenna: plug the FPC antenna in before anything uses the radio.
If flashing fails, hold BOOT, tap RESET, release BOOT, and try again.

## 1. `a7_cal`: RF calibration time (A7, required)
Takes 21 measurements by itself, rebooting between them, then prints a summary every 5 s:
- FULL once (it erases the stored calibration first, which is what the cart's first boot on USB does)
- PARTIAL 10 times: this is the one A7 is about
- NONE 5 times: the fallback that uses stored data only
- `esp_wifi_start()` 5 times, end to end, through the normal ESP-IDF path, for context

**Then unplug and replug USB 3 to 5 times.** Each power-on adds one PARTIAL sample, which matches the cart
best, because the cart's radio always starts from a real power-on. Copy the summary (or run
`python3 ../rf_host.py <port> --out a7.csv`, which logs every `A7,...` line) into the LOGBOOK.

The verdict line uses the timing plus the datasheet currents (TX 350 mA, RX 84 mA, 3.3 V):
- `A7 PASS`: under 3.1 ms. It fits the 5 ms at 50 % TX budget even at 100 % TX, so no current probe is needed.
- `A7 PASS ONLY AT <= 50 % TX DUTY`: 3.1 to 5 ms. Needs the current profile.
- `A7 FAIL at 50 % TX duty`: over 5 ms. Needs the current profile; the firmware fallback is in the message.

Current profile (optional): GPIO3 (pin D1) is high while the calibration runs. Feed the XIAO's 3V3 pin from a
bench supply through a 1 ohm shunt (USB unplugged, so the on-board regulator is out of the path), trigger the
scope on D1 and integrate the shunt voltage over the high time. Hold BOOT for 2 s to clear the results and start over.

How it works: ESP-IDF fixes the calibration mode at compile time and calibrates only once per boot. So the app
calls the PHY library's own calibration entry (`register_chipv7_phy`, exactly as `esp_phy_load_cal_and_init`
does in IDF v5.5.1) with the mode it wants, stores the result in NVS and reboots.

## 2. `rf_logger`: antenna RSSI and uplink throughput (hold point 2)
1. `idf.py menuconfig` -> *RF logger*: enter the SSID, the password and this Mac's IP address (`ipconfig getifaddr en0`).
2. Flash it, close the monitor, and run `python3 ../rf_host.py /dev/cu.usbmodemXXXX --out antenna.csv`.
3. Type the position name and press Enter, then leave each position for about a minute:
   `free`, then `copper2mm` (antenna inside the shell, about 2 mm over a copper-clad board),
   then `in_console` (shell in a Game Boy). Type `q` to finish.
4. Each position gets a line with the median, min and max RSSI and the uplink kbit/s, both as the board sent
   it and as the Mac received it.

802.11b data rates are off, as on the cart. Power save is off so the RSSI samples are steady.
