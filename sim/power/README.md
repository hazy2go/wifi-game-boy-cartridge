# Power-path simulation

`run_sim.py` builds ngspice netlists, runs them, and writes `out/results.txt` plus `plot_*.png`.

```
brew install ngspice
python3 -m venv .venv && .venv/bin/pip install numpy matplotlib
.venv/bin/python run_sim.py
```

Waveform data (`out/*.dat`, ~800MB) is git-ignored; the netlists (`out/*.cir`) and `results.txt` are committed.

## What is compared

| tag | design |
|---|---|
| `original` | Staacks rev: cart 5V -> NCP1117 linear -> 3.3V with 100uF + 680uF tantalum -> radio. Run with ESP8266 and with ESP32-C3 loads. |
| `lim150` | Proposed, limiter only: cart 5V -> 150mA constant-current switch (TPS2553 class) gated by a 4.38V supervisor -> 1.41mF polymer reservoir -> TPS63802 buck-boost -> 3.3V radio. Radio enabled at 4.2V reservoir (power-good, off at 2.0V). Firmware starts no TX burst below 4.0V. |
| `dpm45ps` | Proposed, full: as `lim150`, plus input-voltage regulation (the switch folds its current back so the cart rail stays at >= 4.5V, 2 A/V slope) and firmware power-save (radio drops to modem-sleep below 3.5V reservoir). |

Every run is a **hot-plug**: console rail already up, cartridge caps empty, at t=0. 2 seconds simulated.

Load profiles (radio current at 3.3V), after a 300ms boot RF-calibration phase (1ms TX pulses at 50%):
- `traffic_2ms_20pct`: 2ms TX bursts every 10ms (busy streaming).
- `worst_11b_12ms`: 12ms bursts every 50ms (longest 802.11b 1Mbps frames).
- `stress_50pct`: 2ms bursts every 4ms (more than any sustained use).

## Assumptions (and where they come from)

| value | source |
|---|---|
| Console rail = 5.0V behind 0.7 ohm (fresh), 1.2 ohm (tired), 2.0 ohm (weak) | Not documented anywhere. 2.0 ohm chosen because it reproduces upstream issue #2 (rail collapsing to ~3V under the original cart's ~1A inrush). DMG 5V is a crude transformer converter (Gekkio gb-schematics), so a resistive Thevenin model is a rough stand-in. **Must be measured on real consoles.** |
| Console own load 100mA, 100uF on its rail | Staacks write-up ("a bit over 100mA at 5V"); capacitance is a guess. |
| Croco logic side 60mA steady | Estimate for RP2350 + HyperRAM + flash + TXB0108; **measure on a Croco V2.** |
| Safe console rail >= 4.5V | 5V TTL/CMOS logic minimum (74xx datasheets 4.5-5.5V). The console's real minimum may be lower. |
| ESP8266 70/170mA RX/TX, ESP32-C3 84/335mA | Espressif datasheets (peak, 802.11b). |
| ESP32-C3 modem-sleep average 25mA | Estimate, **unverified**; to be measured. |
| TPS2553: 85 mOhm, constant current, no reverse flow | TI datasheet. The 2 A/V fold-back (DPM) is an idealised loop; the real circuit is being chosen. |
| TPS63802: 88% efficiency, works down to ~1.6V input | TI datasheet (efficiency is a typical figure). |
| Reservoir 3x 470uF 6TPE470MI, 6 mOhm combined ESR | Panasonic datasheet (18 mOhm each). |

## Results (2026-10-14)

`GBmin` = lowest console rail seen, including the hot-plug moment. `GB<4.5V ms` = time spent below 4.5V in 2s. `TX delivered` = share of requested TX airtime the firmware could actually send (throttle).

| console | design | GBmin | ms < 4.5V | TX delivered (traffic / worst / stress) |
|---|---|---|---|---|
| fresh | original + ESP8266 | 4.23 | 2.5 | 100% (no throttle) |
| fresh | proposed full | **4.78** | **0** | 99% / 80% / 62% |
| tired | original + ESP8266 | 3.67 | 3.1 | 100% |
| tired | original + ESP32-C3 | 3.67 | 353-747 | 100% |
| tired | proposed full | **4.63** | **0** | 99% / 72% / 62% |
| weak | original + ESP8266 | **2.80** | 387-809 | 100% |
| weak | proposed limiter only | 4.39 | 1130-2000 | 99% / 57% / 55% |
| weak | proposed full | **4.54** | **0** | 0% (radio stays connected in modem-sleep, never browns out) |

Reading it:
1. The original design crashes the console on hot-plug on tired or weak batteries, and an ESP32-C3 would make it far worse. This matches the upstream reports, which is the sanity check for the model.
2. A plain current limiter fixes fresh and tired consoles but not weak ones: 150mA extra is simply too much for a weak supply.
3. With input-voltage regulation the console rail **never** goes below 4.5V in any case. On a weak console the radio gets almost no power, so it stays associated in modem-sleep and waits. The game never crashes. That is the intended trade.
4. Thresholds matter: the first run throttled at 3.0V and the reservoir lived at 3V, where a linear current limiter only delivers I x 3V into it. Moving throttle/power-save to 4.0V/3.5V keeps the reservoir near 4-4.5V and lifted busy-traffic throughput from 79% to 99% at the same 150mA.

## Results, round 4: the chosen parts (TPS2553 150mA -> BQ24075 VIN-DPM)

Variants `final` (BQ24075 VIN-DPM at 4.5V typ), `final_lo` (4.35V, datasheet min) and `final_hi` (4.63V, max), all with a 4.45V precision supervisor and the firmware rail watch (radio backs off when the cart rail < 4.55V). Full table in `out/results.txt`.

| console | corner | GBmin | ms < 4.5V | TX delivered (traffic / worst / stress) |
|---|---|---|---|---|
| fresh | all three | 4.78 | 0 | 99% / 79% / 62% |
| tired | typ / min | 4.63 | 0 | 99% / 71% / 62% |
| tired | max | 4.66 | 0 | 98% / 61% / 41% |
| weak | typ | 4.52 | 0 | 6% / 1% / 6% (radio parked) |
| weak | min | **4.46** | **57 (once, at plug-in)** | 6% / 1% / 6% |
| weak | max | 4.63 | 0 | 0%, reservoir 2.06V: radio power cycles -> firmware must deep-sleep below 3.0V |

Open item for hardware: the 57ms plug-in dip to 4.46V on a weak console with a min-tolerance BQ24075. Likely harmless (CMOS logic, brief), but it is the single case below the conservative 4.5V line.

Also tested and rejected: BQ24075 alone in USB100 (95mA, only 34% busy-traffic airtime) and alone in USB500 (100% airtime but the console rail swings 0.3-0.4V on every burst on a fresh console). Those rows were produced by an earlier revision of the script (variants `bq100`, `bq100lo`, `bq500`).

Plots: `plot_traffic_2ms_20pct.png`, `plot_worst_11b_12ms.png`, `plot_stress_50pct.png` (tired-battery case).
