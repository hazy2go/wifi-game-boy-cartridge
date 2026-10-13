# Radio + power block: design calculations

Status: datasheet-level design pass, 2026-10-14. Nothing here is built or bench-tested.
Netlist: `docs/design/netlist_radio.json` (54 parts). Symbols that KiCad 10 does not ship: `docs/design/radio_symbols.json`.
Every number below is recomputed by `verify/design_calcs.py` (worst-case corners included); the netlist is checked by `verify/netlist_check.py`.

**Gap by design:** the precision input-voltage regulator between `VLIM` and `VRES` (TLV431 + back-to-back PMOS) is Hazy's and is not in this netlist. `VLIM` and `VRES` are global labels so it can attach from its own sheet; the checker treats `VRES` as an external source.

## 0. Sources

Page numbers are PDF page numbers (TI and WCH print the same number in the footer; Espressif's HW guide prints different numbers, so sections are given too).

| Short name | Document | Version |
|---|---|---|
| TPS2116 | TI SLVSFG1A, TPS2116 power mux | Rev. A, May 2021 |
| TPS2553 | TI SLVS841F, TPS2552/53(-1) | Rev. F, Aug 2016 |
| TPS63802 | TI SLVSEU9D, TPS63802 buck-boost | Rev. D, Jan 2021 |
| TLV3012 | TI SBOS300C, TLV3011/12/11B/12B | Rev. C, Apr 2023 |
| LVC1G125 | TI SCES223U, SN74LVC1G125 | Rev. U, Aug 2026 |
| LVC3G34 | TI SCES366L, SN74LVC3G34 | Rev. L |
| MINI-1 | Espressif ESP32-C3-MINI-1 & MINI-1U Datasheet | v2.2 |
| C3-DS | Espressif ESP32-C3 Series Datasheet | v2.4 |
| HWDG | Espressif ESP32-C3 Hardware Design Guidelines | "master", generated 2026-10-12 |
| CH334 | WCH CH334/CH335 datasheet (Chinese) | V2.91 |
| RP2350 | Raspberry Pi RP2350 datasheet, section 14.8.2, Table 1674 (pin types/reset state) | as used in logbook |

Stock figures: JLCPCB parts API, queried 2026-10-14.

## 1. Block and nets

```
VBUS ─┬─ U101 TPS2116 VIN1 (priority)          VMUX ── U102 TPS2553 (150 mA) ── VLIM ──[Hazy's regulator]── VRES
VCC ──┴─ U101 VIN2  ──────────────► VMUX                                                                     │
                                                                3x 330 uF + 2x 10 uF ──────────────────────────┤
          U108 TLV3012B (VRES >= 4.21 V on, < 2.01 V off) ── RF_EN ── U103 TPS63802 ── +3V3_RF ── U104 ESP32-C3-MINI-1U
                                                                         PG ── RC ── ESP_EN
SPI_CLK/SPI_MOSI/DEBUG_UART_TX(=GPIO46 CS) ── U106 3G34 (+3V3_RF) ── ESP IO6/IO7/IO10
ESP IO2 ── U105 1G125 (+3V3, /OE = CS) ── SPI_MISO
USB_D_prot± ── U107 CH334P ── port1: USB_RP_D± ── Croco R9/R10 ── RP2350 ;  port2 ── 22R ── ESP IO19/IO18
```

Croco nets used (from `kicad-cli sch export netlist` of `GameboyCartridgeV2.1.kicad_sch`): `VCC` (cart pin, before D2), `VBUS` (after F1, before D3), `+3V3`, `GND`, `SPI_CLK`, `SPI_MOSI`, `SPI_MISO`, `DEBUG_UART_TX` (RP2350 GPIO46, U3 pin 57), `USB_D_prot+/-` (USBLC6 U1 pins 1/3 to R9/R10).

**Required Croco edit (outside this sheet):** move R9 pin 2 from `USB_D_prot+` to new net `USB_RP_D+` and R10 pin 2 from `USB_D_prot-` to `USB_RP_D-` (listed in `croco_edits`; the checker applies them to the Croco netlist before counting). Also remove R11 + J3 (debug header): GPIO46 becomes the ESP chip select and the header would be a stub on it.

## 2. Power mux U101 TPS2116DRLR (C3235557, stock 72,694)

**Pinout** (TPS2116 Table 5-1, p3): 1 GND, 2 VOUT, 3 VIN1, 4 PR1, 5 MODE, 6 VIN2, 7 VOUT, 8 ST. Same in KiCad `Power_Management:TPS2116DRL`.

**Priority to VIN1 = USB.** Truth table 7.3.1 (p10): MODE tied to VIN1 = priority mode; VOUT = VIN1 while V(PR1) > VREF, else VIN2. Section 7.6.1 (p12): "connect MODE to VIN1 and set the proper threshold through a resistor divider from VIN1 to PR1". So VIN1 = `VBUS`, MODE = `VBUS`, PR1 from a divider on `VBUS`, VIN2 = raw cart `VCC` (taps before Croco's D2, logbook 04:41).

Threshold: Vth = VREF (1 + R101/R102), VREF = 0.92 / 1.00 / 1.08 V (Table 6.5, p6).
- R101 = 5.1k, R102 = 2.0k (E24, 1 %): Vth = 1.00 x 3.55 = **3.55 V nominal, 3.22 .. 3.89 V worst case**.
- Upper limit 3.89 V is below the USB 2.0 device minimum 4.40 V, so a legal USB supply always wins. Lower limit 3.22 V keeps the mux from using a collapsed VBUS.
- Why so low-impedance (7.1 kOhm, 0.74 mA from USB): with USB unplugged and the cart powered, Croco's D3 (SM5817PL Schottky, LCSC C669022) is reverse biased by +5V and leaks into `VBUS`. The divider must hold `VBUS` below the lowest threshold: that needs > 453 uA of leakage to fail. With MODE floating between 0.35 V and 1 V the state is not specified, but PR1 is then < 0.15 V (low), and both manual-mode rows with PR1 low select VIN2 (truth table p10), so the result is VIN2 either way. **Open item:** SM5817PL leakage at 4.7 V reverse and 70 C must be read from its datasheet (not downloaded).

**No back-feeding.**
- Unselected channel blocks both ways: reverse leakage out of VINx with VOUT = 5.5 V, VINx = 0 V is 0.15 uA max at 105 C (IREV, p6); shutdown currents ISD 1-3 uA (p5-6). So USB never reaches cart `VCC` through the mux and vice versa.
- Switchover is break-before-make (7.6.1, p12), and "if the output voltage is greater than the input supply being switched to, the device will not turn on the new channel until the output has discharged down to VIN + VRCB" (p12). So `VMUX` charged from USB cannot dump into a lower cart `VCC` on unplug.
- Selected channel has reverse current blocking at VOUT - VIN > 42 mV typ / 70 mV max (7.3.4 p11, p6).
- Note: VINx collapse faster than 1 V/10 us needs the other supply >= 2.5 V to avoid a reset (7.4, p11). The cart VCC is 5 V when present, so fine.

**Caps:** 1 uF on each input (section 9, p16: "1 uF is sufficient"), C101 (`VBUS`), C102 (`VCC`); C103 1 uF on `VMUX` (>= 0.1 uF for linear soft start, 7.3.2 p11; also the TPS2553 IN cap). Soft start 1.7 ms at 5 V (p7) limits inrush into C103 to ~3 mA.

**ST** (open drain, low when VIN1 is not used, 7.3.3 p11): R115 100k pull-up to `+3V3_RF` -> ESP IO3 (`USB_PWR_ST`). Firmware learns "running from USB" without an extra ADC channel. Pull-up is on the radio rail, so it cannot back-power the ESP.

## 3. Current limiter U102 TPS2553DBVR (C55266, stock 54,809)

**Pinout** (TPS2553 section 6, DBV SOT-23-6, p5): 1 IN, 2 GND, 3 EN, 4 /FAULT, 5 ILIM, 6 OUT. **EN polarity:** "EN = Active Low for the TPS2552, EN = Active High for the TPS2553" (p5). The ordered part is TPS2553DBVR (constant-current), not TPS2553DBVR-1 (latch-off, C111738). Latch-off would be wrong: charging 1 mF at the limit is an over-current condition by design and a -1 part would latch off after the 7.5 ms deglitch (10.1.1, p17). (The JLC description of C55266 mentions "latch protection"; the TI datasheet decides: non-"-1" = constant current with thermal cycling.)

**EN** tied to IN (`VMUX`): VEN allowed 0..6.5 V (7.3, p6), VIH 1.1 V. Always on whenever the mux has an output.

**ILIM** (9.5.1 eq. 1, p15), RILIM in kOhm:
- IOSnom = 23950 / R^0.977, IOSmax = 22980 / R^0.94, IOSmin = 25230 / R^1.016, valid 15k <= R <= 232k.
- R103 = 180k (E24, 1 %, C25760): IOS = **129.0 / 149.9 / 174.3 mA** (min/nom/max); including the resistor's 1 %: 127.7 .. 176.0 mA. Same corners the simulation already uses (logbook 05:22).

**/FAULT** (open drain, 9.3.3 p14): asserted after 7.5 ms (5-10 ms) of current limiting, i.e. while the reservoir is charging at the limit. R104 100k to `+3V3_RF` -> ESP IO4 (`LIM_FAULT_N`): a free "limiter is saturated" telemetry bit for the power manager. Sink current 33 uA << 10 mA (7.3).

**IN cap** >= 0.1 uF (7.3 p6): C103 1 uF. **OUT cap** C104 1 uF on `VLIM` (input of Hazy's regulator).

Reverse current: the TPS2553 only blocks after a 3-7 ms deglitch (p7, logbook 05:22); the back-to-back PMOS in Hazy's regulator has to provide the instant reverse blocking.

## 4. Reservoir on VRES

- C105-C107: Panasonic 6TPE330MAP (C79112, stock 8,843): 330 uF, 6.3 V, 25 mOhm, 7.3 x 4.3 x 1.8 mm (logbook 04:54). Footprint `CP_EIA-7343-20_Kemet-V` (EIA 7343 land; check against Panasonic's recommended land pattern before layout).
- C108: 10 uF 0603 X5R 10 V (C19702) as HF bypass at the bank; C109: 10 uF at the TPS63802 VIN pin (10.2.2.4 p19: "A 10 uF input capacitor is recommended").
- 6.3 V rating vs 5.25 V max on `VRES` = 83 %. Usable energy 4.2 V -> 2.0 V: 5.5 / 6.9 / 8.2 mJ for -20 % / nom / +20 % capacitance (`design_calcs.py` section 8).

## 5. Radio power-good: U108 TLV3012BIDBVR (C20345924, stock 2,386) driving TPS63802 EN

Requirement: radio converter on at VRES >= 4.2 V, off below 2.0 V, accurate to a few %.

**Options considered.**
1. TPS63802 EN pin alone with a divider + hysteresis from PG. EN thresholds are 1.07-1.13 V rising / 0.97-1.03 V falling (SLVSEU9D Table 8.5, p6). Calculated worst case: 3.99-4.43 V on, **1.74-2.26 V off (+-13 %)**, because the hysteresis comes from a 3.3 V injection that is not ratiometric, and EN leakage 0.2 uA (p6) on a 226k divider adds 45 mV. Also, with feedback taken from +3V3_RF, a lingering rail (FB divider is the only load) lowers the next turn-on threshold to ~3.4 V. Rejected.
2. Voltage supervisor (TPS3840 etc.): fixed thresholds, hysteresis only ~5 %. Cannot do 4.2 / 2.0.
3. **Chosen: TLV3012B** comparator with internal 1.242 V reference and push-pull output, supplied from VRES itself. Because the output swings between 0 V and VRES (the measured voltage), the hysteresis resistor is ratiometric and both thresholds depend only on VREF, offset and resistor ratios.

**Pinout** (TLV3012 Fig 5-1 / Table 5-1, p3): 1 OUT, 2 V-, 3 IN+, 4 IN-, 5 REF, 6 V+. TLV3012 = push-pull (TLV3011 = open drain), p1.

**Circuit:** REF -> IN- (C121 10 nF to GND); R123 (1.3M) VRES -> IN+; R124 (1.0M) IN+ -> GND; R125 (1.2M) OUT -> IN+; OUT -> TPS63802 EN (`RF_EN`); V+ = VRES with C122 100 nF.

**Equations** (Vr = VREF + VOS, hysteresis VHYS split +-VHYS/2):
- OUT low (radio off): V_on = (Vr + VHYS/2) x (R123 + R124||R125) / (R124||R125)
- OUT high = VRES (radio on): V_off = (Vr - VHYS/2) x (R124 + R123||R125) / R124
- Ideal ratios for 4.2 / 2.0 V with Rb = 1: Rc = 1.165, Ra = 1.282. Search over JLC-stocked E24/E96 0402 values (`design_calcs.py`): Ra = 1.3M (C423245), Rb = 1.0M (C26083), Rc = 1.2M (C43675).

**Parameters** (SBOS300C 6.9, p8, B versions): VREF 1.223 / 1.242 / 1.260 V (+-1.5 %) plus drift 100 ppm/C max (+-0.45 % over 0-70 C, assumed); VOS +-9 mV over temperature; VHYS 2 / 6 / 8 mV; IB +-10 pA (negligible on 0.5 MOhm). Resistors 1 %.

**Result:** **ON 4.212 V nominal, 4.036 .. 4.390 V worst case (+-4.2 %); OFF 2.012 V nominal, 1.942 .. 2.085 V worst case (+-3.6 %).** RSS estimate about +-2.2 %. Divider draw 2.3 uA from VRES.

**Behaviour at the edges.**
- POR (9.4.5, p22): below the 1.65 V minimum supply and for up to 1.9 ms after crossing it, the push-pull TLV3012B output is held **low**. So the TPS63802 cannot be enabled by an undefined comparator at low VRES. (The A version has no POR; that is why the B version is used.) REF cap <= 10 nF so the reference settles inside the POR window (same section).
- OFF threshold (>= 1.94 V) is above the TPS63802 UVLO rising max 1.79 V (Table 8.5 p6), so the converter is never asked to run below its own UVLO.
- EN levels: high = VRES >= 1.94 V vs VIH 1.2 V; low <= 0.2 V vs VIL 0.4 V (p6). EN abs max 6 V (8.1 p5) vs VRES <= 5.5 V.
- IN+ at VRES = 5.5 V and OUT high: 3.39 V, inside the -0.2 .. V+ +0.2 V common-mode range (6.6 p4).
- Worst-case ON at 4.39 V: if Hazy's regulator ever holds VRES below ~4.4 V on a weak console, a max-tolerance comparator would never start the radio. Check against the regulator's VRES range.

## 6. Buck-boost U103 TPS63802DLAR (C2845237, stock 41,640)

**Pinout** (Table 7-1, p4): 1 EN, 2 MODE, 3 AGND, 4 FB, 5 PG, 6 VOUT, 7 L2, 8 GND, 9 L1, 10 VIN. No exposed pad (DLA0010A outline, p35). No KiCad footprint: draw `radio:TI_DLA0010A_VSON-HR-10_2x3mm_P0.5mm` from the example board layout, p36.

**Adjustable, not fixed:** Device comparison (p3): TPS63802 "Adjustable" (TPS63810 is the fixed 3.3 V part). FB = 500 mV, +-1 % in PWM (Table 8.5 p6).

**Feedback** (eq. 3, 10.2.2.5 p19; Table 10-5 p20 gives exactly 511k / 91k for 3.3 V; R2 must be <= 100k):
- VOUT = 0.5 x (1 + 511/91) = **3.308 V**. With FB +-1 % and 1 % resistors: 3.220 .. 3.398 V. IFB is 5 nA typ / 100 nA max (p6): worst-case extra 100 nA x 511k = 51 mV -> 3.17 .. 3.45 V, inside the ESP32-C3 3.0-3.6 V range (C3-DS Table 5-2, p54). R109 511k (C64042), R110 91k (C4147).

**Inductor** (8.3 p5: effective L 0.37 / 0.47 / 0.57 uH; Table 10-2 p19 recommends 0.47 uH XFL4015 or DFE201612E): L101 Murata DFE201612E-R47M=P2 (C668312, stock 7,459), 0.47 uH, 26 mOhm, Isat 4.5 A / Irms 5.5 A per LCSC (TI table: 5.5 A sat). Peak current from eq. 1-2 (p18), worst case VIN = 2.0 V (radio cut-off), IOUT = 0.5 A (Espressif asks for a 500 mA capable supply, HWDG 1.3.2), f = 2.0 MHz, L = 0.376 uH (-20 %), eta 0.85: D = 0.394, IPEAK = 0.5/(0.85 x 0.606) + 2.0 x 0.394/(2 x 2 MHz x 0.376 uH) = 0.97 + 0.52 = **1.49 A**, x1.2 margin = 1.79 A << 4.5 A. Footprint `Inductor_SMD:L_Murata_DFE201610P` (same 2016 land; DFE201612E is 1.2 mm tall instead of 1.0 mm, check land pattern in the Murata sheet).

**Caps:** C110 22 uF 0603 6.3 V X5R (C59461) on VOUT: "single 22 uF for all programmed output voltages <= 3.6 V" (10.2.2.3 p19); effective >= 7 uF required at VO > 2.3 V (8.3 p5). At 3.3 V bias a 0603 22 uF X5R keeps roughly 10-12 uF; the ESP's C111 10 uF adds to it ("no upper limit", p19). C109 10 uF on VIN (10.2.2.4 p19), CI effective >= 4 uF (8.3) also met by the reservoir.

**MODE = GND:** "Set LOW for power save mode" (Table 7-1 p4, 9.3.3 p11): automatic PFM/PWM, best light-load efficiency for modem/light-sleep; must not float.

**PG** (9.3.10 p12, truth table 9-1 p13): open drain, high-Z when VOUT > 95 % of target, driven low when EN is low (VIN > UVLO falling) or VOUT < 90 %. R108 10k pull-up to `+3V3_RF`. Used as the ESP reset supervisor (section 7).

## 7. ESP32-C3-MINI-1U-N4 U104 (C2911374, stock 1,742)

**Pinout** (MINI-1 Fig 3-1 p10, Table 3-1 p11): 53 pins; GND 1, 2, 11, 14, 36-53; 3V3 3; NC 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32-35; IO2 5, IO3 6, EN 8, IO0 12, IO1 13, IO10 16, IO4 18, IO5 19, IO6 20, IO7 21, IO8 22, IO9 23, IO18 26, IO19 27, RXD0 30, TXD0 31. The u.FL connector is on the module (MINI-1U), no extra part. No KiCad symbol/footprint; land pattern MINI-1 DS p38-39.

**Pin assignment**

| Pin | GPIO | Function | Why |
|---|---|---|---|
| 16 | IO10 | ESP_CS | FSPICS0 on IO_MUX (HWDG Table 5) |
| 20 | IO6 | ESP_SCK | FSPICLK |
| 21 | IO7 | ESP_MOSI | FSPID |
| 5 | IO2 | ESP MISO (FSPIQ) -> U105 | strap, pulled up anyway |
| 12 | IO0 | ADC_VRES | ADC1_CH0 |
| 13 | IO1 | ADC_VCC | ADC1_CH1 |
| 6 | IO3 | USB_PWR_ST | TPS2116 ST |
| 18 | IO4 | LIM_FAULT_N | TPS2553 FAULT |
| 22 / 23 | IO8 / IO9 | straps | pull-ups |
| 26 / 27 | IO18 / IO19 | USB D- / D+ | native USB Serial/JTAG |
| 19, 30, 31 | IO5, RXD0, TXD0 | NC | spare; firmware enables internal pulls (HWDG 1.3.10) |

The IO_MUX SPI pins allow the fastest slave mode; IO6/IO7/IO10 have 5 ns power-up glitches (HWDG Table 6), harmless because they are inputs behind U106.

**Strapping** (MINI-1 Table 4-1 p12 defaults, Table 4-3 p13; HWDG 1.3.9 Table 3):
- SPI boot = GPIO2 1, GPIO8 any, GPIO9 1. Joint download = GPIO2 1, GPIO8 1, GPIO9 0.
- GPIO2: 10k pull-up R112 ("recommended to pull this pin up due to glitches", Table 4-3 note 2). The only other load is U105's input (high-Z), so the level is defined at reset.
- GPIO8: 10k pull-up R113. Default is floating (Table 4-1); 1 is what download mode needs and keeps the UART0 ROM-log state deterministic (Table 4-4 p14).
- GPIO9: 10k pull-up R114 ("recommended to place a pull-up resistor at the GPIO9 pin", "do not add high-value capacitors", HWDG 1.3.9). Internal weak pull-up is ~45k (Table 6-3 p21); external 10k makes it robust.
- Setup/hold: tSU >= 0, tH >= 3 ms after CHIP_EN (Table 4-2 p13): static pull-ups satisfy this.
- `netlist_check.py` verifies all three resolve to 1 through a resistor to the module's 3V3 net.

**CHIP_EN** (HWDG 1.3.3 attention box: RC R = 10 kOhm, C = 1 uF usually, "specific parameters should be adjusted"; for slow ramps/frequent power cycling "reserve a power monitor chip ... threshold around 3.0 V"):
- This radio rail is power-cycled on purpose (power-good), so a supervisor is used: the TPS63802 PG (95 % / 90 % of 3.308 V = 3.14 V release / 2.98 V reset) feeds EN through R111 10k, with R108 10k pull-up and C113 100 nF.
- Release: tau = 20k x 100 nF = 2 ms, EN reaches 0.75 VDD (VIH_nRST, Table 6-3 p21) 2.8 ms after PG; tSTBL >= 50 us (HWDG Table 1) is satisfied because PG only releases once VOUT is in regulation.
- Reset: PG low discharges C113 through R111: tau 1 ms, below 0.25 VDD after 1.4 ms (tRST >= 50 us). With 1 uF this would take 14 ms, longer than the fastest reservoir recharge (2.0 -> 4.2 V at 150 mA into 1 mF = 15 ms), so 100 nF was chosen.
- PG sink current: 0.33 mA (R108) + 0.33 mA peak (C113 via R111) = 0.66 mA <= 1 mA (9.3.10 p12).

**Decoupling:** C111 10 uF (HWDG 1.3.2: 10 uF on the rail for TX current steps) + C112 100 nF at pin 3, plus the converter's 22 uF.

**ADC dividers** (HWDG 1.3.11: ADC1 calibrated; ATTEN=2 range 0-1300 mV, +-10 mV; ATTEN=3 0-2500 mV, +-35 mV):
- ATTEN=2 is used because it is 3.5x more accurate at the pin than ATTEN=3.
- k = 200k / (680k + 200k) = 0.2273 (0.2238 .. 0.2308 with 1 % parts); full scale 1.30 V <-> 5.72 V input; 5.5 V max -> 1.27 V.
- Errors at the input: ADC +-10 mV -> +-44 mV; pin leakage IIH/IIL 50 nA max (MINI-1 Table 6-3) x 154.5 kOhm Thevenin = 7.7 mV -> 34 mV; ratio tolerance at 4.55 V +-71 mV (fixed per board, remove with a one-point calibration at test). The firmware's 4.55 / 4.60 V cart-rail hysteresis band (logbook 05:29) is narrower than the uncalibrated error, so **per-board calibration is required** (or 0.1 % resistors).
- Filter: 2.2 nF (C114, C115). HWDG recommends 0.1 uF for accuracy, but that would give a 15 ms time constant and the power manager samples every 1 ms during 2-12 ms bursts. 2.2 nF gives tau = 0.34 ms and is still ~1000x the ADC sample capacitor.
- Pin thresholds for firmware (VRES and VCC): 4.60 V -> 1.045 V, 4.55 V -> 1.034 V, 4.0 V -> 0.909 V, 3.5 V -> 0.795 V, 3.0 V -> 0.682 V.
- Cart-VCC divider and Croco: it is a 5.7 uA load to ground on `VCC` and has no path that can source current into `VCC` (the ADC pin is a high-impedance input). With the cart out and USB in, it actually keeps the floating `VCC` net (TXB0108 VCCB, TVS D5) at 0 V against TPS2116 VIN2 leakage (<= 2.9 uA).
- Divider into an unpowered ESP: the pin clamps to the dead 3V3 rail; injected current is at most (5.25 - 0.3) V / 680k = 7.3 uA. Accepted; see section 9.

**USB:** IO19 = D+, IO18 = D- (Table 3-1). R120/R121 22 Ohm series near the module (HWDG 1.3.12: 22/33 Ohm).

## 8. SPI link to the RP2350

- ESP is SPI slave on Croco's SPI0 (`SPI_CLK` = GPIO2, `SPI_MOSI` = GPIO3, `SPI_MISO` = GPIO0, shared with SD card J2 and RTC U5), CS = `DEBUG_UART_TX` (GPIO46).
- **Unpowered ESP problem:** ESP32-C3 VIH max = VDD + 0.3 V (C3-DS Table 5-4 p55, MINI-1 Table 6-3 p21). With the radio rail off (normal during reservoir charging and power-save), RP2350 activity on SCK/MOSI/CS at 3.3 V would exceed the rating and push current through the pad protection into `+3V3_RF`. Espressif's documents contain no statement allowing input voltage while unpowered; the VIH max bound is the only applicable spec. Series resistors would only limit, not stop, this current (3 pins x ~2.7 mA through 1k). So:
- **U106 SN74LVC3G34DCUR (C130024, stock 5,938), powered from `+3V3_RF`:** 1A=SPI_CLK -> 1Y, 2A=SPI_MOSI -> 2Y, 3A=GPIO46 -> 3Y=ESP_CS. Pinout SCES366L Pin Functions p3: 1 1A, 2 3Y, 3 2A, 4 GND, 5 2Y, 6 3A, 7 1Y, 8 VCC (matches KiCad `74xGxx:74LVC3G34`). Ioff (p1, Ioff +-10 uA at 5.5 V p5, feature text p8): with VCC = 0 the inputs and outputs do not conduct, so neither the RP2350 nor the ESP is back-powered. LVC inputs are 5.5 V tolerant at any VCC. R122 33 Ohm in series on SCK at the buffer output (HWDG 1.3.8).
- **U105 SN74LVC1G125DBVR (C23654, stock 59,583), powered from Croco `+3V3`:** pinout SCES223U Pin Functions p3: 1 OE, 2 A, 3 GND, 4 Y, 5 VCC (matches KiCad `SN74LVC1G125DBV`); function table p11: OE low -> Y = A, OE high -> Z. OE = GPIO46 (CS active low), A = ESP IO2, Y = `SPI_MISO`.
  - Why the Croco rail and not the radio rail: when Croco loses power while the reservoir still runs the ESP, a radio-rail buffer with a floating/low CS would drive 3.3 V into the unpowered RP2350, SD card and RTC. On `+3V3` the buffer is off then, and Ioff (7.3.2 p10) keeps Y high-Z. When the ESP is off but Croco is on, A is held at ~0 V by R112 to the dead rail, so a CS-low read returns 0x00 = "radio absent" and no current flows into the ESP (the buffer input is high-Z).
- GPIO46 is pulled down at RP2350 reset (RP2350 Table 1674: GPIO46_ADC6 "Digital IO / Analogue", reset state input, pull-down; it is **not** fault tolerant, unlike GPIO0-39). Effect: during RP2350 reset the ESP sees CS asserted and U105 drives MISO. Harmless: SD and RTC chip selects have Croco pull-ups (R6, R15) so they are deselected. No extra pull-up is added on CS (it would fight the internal pull-down and back-power the non-FT GPIO46 pad).
- Timing: two buffers add ~4 + 4 ns to the SCK -> MISO loop plus the ESP slave output delay; plan the SPI clock around 10 MHz and verify on hardware.

## 9. USB: CH334P hub U107 (C5373042, stock 27,999)

**Pinout** (CH334 Fig 1-1, p2, QFN16 3x3): 1 XO, 2 XI, 3 DM4, 4 DP4, 5 DM3, 6 DP3, 7 DM2, 8 DP2, 9 DM1, 10 DP1, 11 DMU, 12 DPU, 13 RESET#/CDP, 14 PGANG, 15 V5, 16 VDD33, 0 = exposed pad GND (numbered 17 in the symbol to match the KiCad `QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm` footprint; EP 1.75 mm from drawing 5.1, p21). KiCad ships CH334F/R/U only, so `radio:CH334P` is defined.

- **Crystal: required.** Table 1-1 (p3) lists crystal-free operation as "optional" (可选), but 6.2 (p27) says crystal-free is a configurable feature that is **not enabled by default on some packages and must be confirmed when ordering**; stock parts from JLC cannot be assumed to have it. Y101 12 MHz, CL 10 pF, 3225 (C668845, stock 4,120, the same part as Croco Y1). No load caps: XI/XO have ~16 pF built in, "recommend no external load capacitors" (6.1, p26). 16 pF in series with 16 pF + ~2 pF stray = 10 pF = crystal CL.
- **Supply: V5 = VDD33 = Croco `+3V3`.** Allowed external-3.3 V mode: V5 3.15-3.5 V "no internal LDO", VDD33 3.15-3.5 V (4.2, p19); WCH recommends it for lower dissipation (6.1 note, p26). Why not VBUS: a VBUS-powered hub is unpowered whenever the cart runs from the console, and then the RP2350's and ESP's D+ pull-ups would back-drive its USB pins (abs max VDD33 + 0.4 V, 4.1 p19). On `+3V3` the hub is powered whenever either chip is. Cost: hub sleep current 0.12-0.4 mA from the console (ISLP, p19), up to 42 mA (1 port HS) while USB is active, when VBUS also supplies Croco. Caps: C118 1 uF on V5, C119 1 uF + C120 100 nF on VDD33 (Table 1-3 p4).
- **RESET#/CDP:** internal ~25k pull-up, built-in POR (5-14 ms); "recommended to leave completely floating when not resetting" (Table 1-3 p4, 3.2.2 p11). NC. Driving it high at power-up would enable CDP, which we do not want.
- **PGANG:** internal pull-up, configures over-current mode during reset; the CH334P has no over-current detection anyway (3.1, p8). NC. Unused ports 3/4: NC (built-in downstream pull-downs, p1).
- Ports: upstream DMU/DPU -> `USB_D_prot-/+` (after Croco's USBLC6), port 1 DM1/DP1 -> `USB_RP_D-/+` -> Croco R9/R10 27 Ohm -> RP2350, port 2 DM2/DP2 -> R121/R120 22 Ohm -> ESP IO18/IO19.

**Fallback considered: TS3USB221A analog switch (C128396, UQFN-10 1.5 x 2 mm, stock 86,465) + slide switch.**

| | CH334P hub (chosen) | TS3USB221A + slide switch |
|---|---|---|
| Parts | hub, 12 MHz crystal, 3 caps (+2x 22R shared) | switch IC, 1 cap, slide switch, pull resistor |
| Area | ~3x3 + 3.2x2.5 + caps, ~25 mm2 | ~3 mm2 + switch 20-30 mm2 that must sit at the shell edge |
| Use | both chips enumerate together, flash either from a browser | user must open/poke the shell to select; or a GPIO select, but the RP2350 has no free GPIO and the ESP cannot select itself before it has firmware |
| Risk | Croco netlist edit (R9/R10), upstream D+ pull-up present without VBUS, crystal | mechanical access through a closed cartridge shell |

The slide switch is not reachable inside a standard shell (logbook 04:54), which decides it. **Hub chosen.**

Hub risks:
- With the hub on `+3V3` and no cable, the hub's upstream 1.5k pull-up on D+ is present without VBUS (USB 2.0 7.1.5 back-drive rule). Through the USBLC6 steering diode it lifts `VBUS` to ~2 V against R101+R102 (7.1k). PR1 is then ~0.6 V < VREF, so the TPS2116 still selects the cart (section 2). Measure on the first board.
- The ESP should disable its USB D+ pull-up when `USB_PWR_ST` is low (no USB); the RP2350 firmware is unchanged.

## 10. Back-powering summary

| Path | Mitigation |
|---|---|
| RP2350 SCK/MOSI/CS -> unpowered ESP | U106 on `+3V3_RF` with Ioff |
| ESP MISO -> unpowered RP2350/SD/RTC | U105 on `+3V3` with Ioff, OE from CS |
| Pull-ups on ST/FAULT/straps | all go to `+3V3_RF` (dead when the ESP is off) |
| ADC dividers -> unpowered ESP | <= 7.3 uA per pin through 680k; accepted |
| Hub (on `+3V3`) -> unpowered ESP USB pins | the hub only drives a port after it sees a device pull-up; with the ESP off there is none |
| VMUX/VLIM -> VBUS or VCC | TPS2116 blocking (section 2) |
| VBUS <-> VCC through the TPS2116 | unselected channel off, IREV <= 0.15 uA |

## 11. Parts list (radio sheet only, per board)

| Refs | Value | Footprint | LCSC | Stock 2026-10-14 | Qty |
|---|---|---|---|---|---|
| U101 | TPS2116DRLR | SOT-583-8 | C3235557 | 72,694 | 1 |
| U102 | TPS2553DBVR | SOT-23-6 | C55266 | 54,809 | 1 |
| U103 | TPS63802DLAR | radio:TI_DLA0010A (to draw) | C2845237 | 41,640 | 1 |
| U104 | ESP32-C3-MINI-1U-N4 | radio:ESP32-C3-MINI-1U (to draw/import) | C2911374 | 1,742 | 1 |
| U105 | SN74LVC1G125DBVR | SOT-23-5 | C23654 | 59,583 | 1 |
| U106 | SN74LVC3G34DCUR | VSSOP-8 2.3x2 | C130024 | 5,938 | 1 |
| U107 | CH334P | QFN-16 3x3 EP1.75 | C5373042 | 27,999 | 1 |
| U108 | TLV3012BIDBVR | SOT-23-6 | C20345924 | 2,386 | 1 |
| Y101 | 12 MHz 10 pF 3225 | Crystal_SMD_3225-4Pin | C668845 | 4,120 | 1 |
| L101 | DFE201612E-R47M 0.47 uH | L_Murata_DFE201610P | C668312 | 7,459 | 1 |
| C105-C107 | 6TPE330MAP 330 uF 6.3 V | CP_EIA-7343-20 | C79112 | 8,843 | 3 |
| C110 | 22 uF 6.3 V X5R 0603 | 0603 | C59461 | 7.4 M (basic) | 1 |
| C108, C109, C111 | 10 uF 10 V X5R 0603 | 0603 | C19702 | 9.4 M (basic) | 3 |
| C101-C104, C118, C119 | 1 uF 25 V X5R | 0402 | C52923 | 7.8 M (basic) | 6 |
| C112, C113, C116, C117, C120, C122 | 100 nF 16 V X7R | 0402 | C1525 | 23.5 M (basic) | 6 |
| C121 | 10 nF X7R | 0402 | C15195 | 4.0 M (basic) | 1 |
| C114, C115 | 2.2 nF X7R | 0402 | C84703 | 97,933 | 2 |
| R101 | 5.1k | 0402 | C25905 | 5.5 M (basic) | 1 |
| R102 | 2.0k | 0402 | C4109 | 7.9 M (basic) | 1 |
| R103 | 180k | 0402 | C25760 | 174,412 | 1 |
| R104, R115 | 100k | 0402 | C25741 | 10.2 M (basic) | 2 |
| R108, R111-R114 | 10k | 0402 | C25744 | 22.4 M (basic) | 5 |
| R109 | 511k | 0402 | C64042 | 171,181 | 1 |
| R110 | 91k | 0402 | C4147 | 4,326 | 1 |
| R116, R118 | 680k | 0402 | C4131 | 187,497 | 2 |
| R117, R119 | 200k | 0402 | C25764 | 2.2 M (basic) | 2 |
| R123 | 1.3M | 0402 | C423245 | 5,482 | 1 |
| R124 | 1.0M | 0402 | C26083 | 2.2 M (basic) | 1 |
| R125 | 1.2M | 0402 | C43675 | 12,605 | 1 |
| R120, R121 | 22R | 0402 | C25092 | 5.9 M (basic) | 2 |
| R122 | 33R | 0402 | C25105 | 1.5 M (basic) | 1 |

All resistors are UniOhm 0402WGF...TCE, 1 %, 100 ppm/C. Lowest stocks: ESP32-C3-MINI-1U (1,742) and TLV3012B (2,386); both are fine for prototype quantities but should be reserved at order time.

## 12. Open items and risks

1. Croco edit for the hub (R9/R10 pin 2 to `USB_RP_D+/-`) and removal of R11/J3; not done here (hw/ untouched).
2. Footprints to create: TPS63802 DLA (SLVSEU9D p36) and ESP32-C3-MINI-1U (MINI-1 DS p38-39; a draft `mini1u.kicad_mod` exists outside the repo in /tmp/espfp and must be checked against the datasheet before use).
3. `radio_symbols.json` uses KiCad's `open_collector` type for TPS2553 /FAULT and TPS63802 PG (open-drain); `gen_radio_sheet.py`'s symbol builder must accept it.
4. D3 (SM5817PL) reverse leakage at 4.7 V/70 C vs the 453 uA margin of the PR1 divider: read the diode datasheet.
5. ON threshold worst case 4.39 V vs the VRES ceiling of Hazy's regulator.
6. ADC divider needs per-board calibration for the 4.55/4.60 V firmware band.
7. CH334P upstream pull-up without VBUS (lifts VBUS to ~2 V through the USBLC6); verify on hardware that the mux and Croco ignore it.
8. SPI clock limit through two buffers + ESP slave output delay: measure.
9. A GPIO9 test pad (no BOM part) is recommended for recovery if the app firmware ever disables USB-Serial-JTAG; download mode then needs GPIO9 low at reset.
