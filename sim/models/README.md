# Vendor SPICE models (ngspice-47)

All models downloaded **2026-10-14** and verified with `/opt/homebrew/bin/ngspice` (ngspice-47).
Test netlists live in `tests/`; run them from inside `tests/` (e.g. `cd tests && ngspice -b pmos_rdson.cir`).

**Required ngspice setting:** `tests/.spiceinit` contains `set ngbehavior=psa` (PSpice compatibility).
The Vishay MOSFET models and both TI switcher/switch models need it. TLV431 runs with or without it.
If you include these models from another directory, put the same `.spiceinit` next to your netlist
(ngspice reads `.spiceinit` from the current working directory), or put `set ngbehavior=psa` in `~/.spiceinit`.

## License / terms (all files)

- **TI models**: header says "(C) Copyright Texas Instruments ... designed as an aid for customers of
  Texas Instruments ... provided solely on an 'as is' basis" (no warranty). Downloads are subject to the
  TI design-resource terms on ti.com. Fine for private simulation; do not redistribute publicly without
  checking TI's terms.
- **Vishay models**: header says "Copyright Vishay Intertechnology ... intended as a SPICE modeling guideline
  and does not constitute a commercial product datasheet". Same advice: use for simulation, don't redistribute.
- Vendor zips are kept verbatim next to the extracted models (SHA-256 at the end of this file).

---

## 1. TI TLV431 shunt reference  -> `ti_tlv431/`

| item | value |
|---|---|
| Source | https://www.ti.com/lit/zip/SLIM005 ("TLV431 PSpice model", linked from https://www.ti.com/product/TLV431) |
| Also exists | https://www.ti.com/lit/zip/SLVM088 (TINA-TI version, identical netlist, not kept) |
| File | `ti_tlv431/TLV431.lib` (= `TLV431_PSPICE/tlv431.lib` from the zip, **unmodified**) |
| Subckt | `.SUBCKT TLV431 A K Fdbk` -> **anode, cathode, ref** |
| Model | AEi Systems behavioral model, 2009, unencrypted. Generic TLV431 (no A/B grade distinction: nominal 1.240 V) |
| ngspice edits | none |

Test `tests/tlv431_shunt.cir` -> `tests/tlv431_shunt.out`:

| test | result | datasheet |
|---|---|---|
| K=REF, 1 k from 5 V (Ik=3.76 mA) | Vk = **1.2409 V** | 1.240 V typ (B grade +/-0.5 %) |
| Divider 16.5k/10k, 470 ohm from 5 V | Vout = **3.2883 V** (Vref 1.24088) | 1.24*(1+1.65)=3.286 V |
| Ik sweep (K=REF): 1 mA / 10 mA / 14 mA | 1.24024 / 1.24250 / 1.24351 V | - |
| Dynamic impedance 1-10 mA | **0.251 ohm** | 0.25 typ / 0.4 max |
| Ik needed to reach 1.230 V / 1.238 V | **362 uA / 397 uA** | Ik(min) 55 typ / 80 max (C/I), 100 max (Q) |

Caveats:
- **Model under-predicts low-current regulation**: Vk is only 1.006 V at 55 uA, 1.059 V at 100 uA, 1.139 V at 200 uA;
  it reaches 1.240 V only above ~0.5 mA. Bias the shunt at >= 0.5-1 mA in simulation (real part regulates from ~80 uA).
- **Hard sink ceiling ~15-16 mA** (TABLE-limited output). Above that the cathode voltage runs away (no convergence point
  at 16 mA). Datasheet abs-max cathode current is 15 mA anyway.
- Iref = 0 (ideal ref input); no temperature drift, no tolerance, no noise; line-regulation term not modeled.
- TI TLV431 VKA max is **6 V** (onsemi/Diodes TLV431 are 16-18 V parts; this model is TI's).

## 2. Dual P-channel MOSFET: Vishay **SiA975DJ** (chosen)  -> `vishay_sia975dj/`

| item | value |
|---|---|
| Source (PSpice) | https://www.vishay.com/docs/65767/sia975dj.txt (Doc 65767, Rev A, 2010) -> `SiA975DJ_PS.lib` (unmodified, renamed) |
| Source (HSPICE) | https://www.vishay.com/docs/65766/sia975dj.txt -> `SiA975DJ_HSPICE.txt` (reference only, not tested) |
| Datasheet | https://www.vishay.com/docs/65710/sia975dj.pdf -> `sia975dj.pdf` |
| Product page | https://www.vishay.com/en/product/65710/ (Design Tools tab) |
| Subckt | `.SUBCKT SiA975DJ D G S` -> **drain, gate, source** (ONE channel; instantiate twice for the dual) |
| Part | Dual P-ch, -12 V VDS, +/-8 V VGS, PowerPAK SC-70-6L dual (2x2 mm), 4.5 A |
| ngspice edits | none (needs `ngbehavior=psa` for `TC=`, `T_MEASURED`, `TRS1`) |

Test `tests/pmos_rdson.cir` (Vds = -0.1 V Vgs sweep at 25 C, plus forced-current points) -> `tests/pmos_rdson.out`:

| Vgs | model Rds(on) @Vds=-0.1V | model @ datasheet Id | datasheet typ / max |
|---|---|---|---|
| -4.5 V | **38.8 mOhm** | 39.0 mOhm @ -4.3 A | 33 / 41 |
| -2.5 V | **54.8 mOhm** | 56.3 mOhm @ -3.6 A | 49 / 60 |
| -1.8 V | **79.2 mOhm** | - | 70 / 110 |
| Vgs(th) @ -250 uA | **-0.59 V** | | -0.4 / -1.0 |

Why this part: Vgs(th) -0.4...-1.0 V, Rds(on) 60 mOhm max at -2.5 V, ID 4.5 A, vendor model downloadable without login,
real JLC stock. JLC/LCSC: **C141597** `SIA975DJ-T1-GE3`, stock **2692** (extended part), $0.50 @1 / $0.28 @100.

Caveats: VDS rating is only **-12 V** (fine for 5 V USB, but no margin for a 9-12 V input); VGS max +/-8 V.
Level-3 static model: Cgd is a fixed 1 pF + Cgs 309 pF, so gate-charge/Miller plateau and switching edges are only
approximate; fine for DC/regulation-loop work. No self-heating. Package pinout: see datasheet page 1
(PowerPAK SC-70-6L dual footprint).

Other dual-PMOS candidates found in JLC stock (not modeled here):
- Diodes **DMP2065UFDB-7** C461059, stock 2985, U-DFN2020-6, -20 V, 55/100 mOhm typ/max @-2.5 V, Vth -0.4...-1.0 V.
  Better VDS margin, but diodes.com sits behind a Cloudflare JS challenge so the SPICE model could not be fetched by script
  (download manually from https://www.diodes.com/part/view/DMP2065UFDB -> "SPICE Model" if wanted).
- Vishay **SiA923EDJ-T1-GE3** C511005, stock 931, -20 V, 54 mOhm @-4.5 V (model not fetched; successor SiA923AEDJ has only 7 in stock).
- Rejected: AON2801 (120 mOhm @4.5 V), FDC6312P / FDC6306P / NTJD4152P / DMP2240UDM / Si5935CDC (Rds too high); AO6801/Si3911/
  FDC6312P clones from VBsemi/JSMSEMI/TECH PUBLIC have no vendor models.

## 2b. Single PMOS backup: Vishay **Si2371EDS** (SOT-23)  -> `vishay_si2371eds/`

| item | value |
|---|---|
| Source | https://www.vishay.com/docs/67353/si2371eds_ps_reva.zip (product page https://www.vishay.com/en/product/63924/) |
| Files | `Si2371EDS_PS_RevA.lib` (used, unmodified), `Si2371EDS_PS_RC_RevA.lib` (same model + thermal RC wrapper `Si2371EDS_PS_RC`; **not tested**, don't include both at once - both define `Si2371EDS`), zip kept |
| Datasheet | https://www.vishay.com/docs/63924/si2371eds.pdf -> `si2371eds.pdf` |
| Subckt | `.SUBCKT Si2371EDS D G S` -> **drain, gate, source** (includes gate ESD-diode subckt) |
| Part | P-ch -30 V, +/-12 V VGS, 4.8 A, SOT-23 (AO3401A-class) |

| Vgs | model @Vds=-0.1V | model @ datasheet Id | datasheet typ / max |
|---|---|---|---|
| -4.5 V | **47.9 mOhm** | - | 44 / 53 |
| -2.5 V | **76.5 mOhm** | 77.2 mOhm @ -2 A | 66 / 80 |
| -1.8 V | 148 mOhm | - | n/a |
| Vgs(th) @ -250 uA | **-0.98 V** | | -0.6 / -1.5 |

JLC/LCSC: **C467897** `SI2371EDS-T1-GE3`, stock **1611**, $0.22 @1.
Why not AO3401A / DMP2035U: AOS does not publish a model for AO3401A on aosmd.com (product page lists datasheet/marking/
reliability only), and Diodes (DMP2035U-7, C110499, stock 101597) is Cloudflare-blocked for scripted download.
AO3401A C15127 is a JLC *basic* part (stock 625k) with similar specs (30 V, 85 mOhm max @-2.5 V); Si2371EDS is a reasonable
stand-in model for it.

## 3a. TI TPS2553 current-limited load switch  -> `ti_tps2553/`

| item | value |
|---|---|
| Source | https://www.ti.com/lit/zip/SLVM425 (product page lists "SLVM425B.ZIP - PSpice model"; zip contents are folder `SLVM425A/TPS2553_PSPICE_TRANS`) |
| Also exists | https://www.ti.com/lit/zip/SLVM868 (TINA-TI transient model, not kept) |
| Files | `TPS2553_TRANS.lib.orig` (vendor file verbatim), `TPS2553_TRANS.lib` (ngspice-patched, **use this**), zip |
| Subckt | `.subckt TPS2553_TRANS IN GND EN PWPD FAULT_B ILIM OUT` |
| Model | TI 2011 transient model, unencrypted. Internal `.param LATCH=0` (auto-retry TPS2553; `-1` latch-off variant would need LATCH=1 edited inside the subckt), `.param EN_P=1` (active-high EN) |

ngspice edit (1): `XNOR2_BASIC_GEN` used `IF(V(A)>th ^ V(B)>th, ...)`. In PSpice `^` is boolean XOR; in ngspice it is the
**power operator**, which silently broke the EN-polarity logic (ENOK always low -> switch turned itself off ~1 ms after
power-up). Rewritten as `IF((V(A)>th) != (V(B)>th), VSS, VDD)`; original line kept as a comment.
Also converted CRLF->LF and added a header line.

Test `tests/tps2553_ilim.cir` (VIN=5 V, EN high at 0.2 ms, RILIM=20 k, 10 uF out):

| case | result | datasheet |
|---|---|---|
| A: 10 ohm load | Vout = **4.9506 V** (0.495 A -> ~100 mOhm switch drop) | Ron 85 mOhm typ (DBV) |
| B: 1 ohm load (overcurrent) | Iout clamps at **1.288 A** | IOS = 23950/R(kOhm)^0.977 = 1.28 A at 20 k |
| B: FAULT_B | asserts at t = **9.22 ms** (~9 ms after EN) | 7.5 ms typ deglitch (TPS2553) |
| Before EN | Vout = 0 V | - |

## 3b. TI TPS63802 buck-boost  -> `ti_tps63802/`

| item | value |
|---|---|
| Source | https://www.ti.com/lit/zip/SLVMCX1 (zip folder `SLVMCX1B/TPS63802_PSPICE_TRANS`), linked from https://www.ti.com/product/TPS63802 |
| Files | `TPS63802_TRANS.lib.orig` (verbatim), `TPS63802_TRANS.lib` (ngspice-patched, **use this**), zip |
| Subckt | `.SUBCKT TPS63802_TRANS AGND EN FB GND L1 L2 MODE PG VIN VOUT` |
| Model | TI 2019 "TRANSIENT" model, Final 1.00, unencrypted. Models switching, FPWM/PFM, min on-time, soft start, current limit, PG, IVP/OVP, UVLO. Not modeled: temperature, quiescent current. Grounds tied to 0 V internally. |

ngspice edits:
1. Same `XNOR2_BASIC_GEN` `^` -> `!=` fix as TPS2553.
2. `one_shot` subckt had a zero-delay algebraic loop (`out` -> `ch` -> `out`) which made ngspice abort with
   "Timestep too small" at ~0.41 ms (right when switching starts). Added `r_ngs_out 1 ohm` + `c_ngs_out 10 pF`
   (10 ps) on the one-shot output. Original line kept as a comment.
3. Replaced a non-ASCII en-dash in a comment; CRLF->LF; header line.

Test `tests/tps63802_startup.cir` (VIN 3.0 V -> 4.2 V step at 1.2 ms, VOUT set 3.30 V via 560k/100k, L=0.47 uH,
COUT=44 uF, MODE=0 (auto PFM/PWM), 33 ohm base load + 1 A step at 0.8 ms):

TPS63802_RESULTS_PLACEHOLDER

Runtime: about 4 min CPU per simulated ms (20 ns max step) on this Mac - use it for short start-up/load-step checks,
not long system sims.

## JLC / LCSC part numbers (checked 2026-10-14 via JLC parts API)

| function | part | LCSC | stock | notes |
|---|---|---|---|---|
| Shunt ref (B grade, SOT-23-3) | TI TLV431BIDBZR | **C398375** | 14879 | I grade -40..85 C; TI DBZ pinout (datasheet Table 4-1): 1=REF 2=CATHODE 3=ANODE |
| Shunt ref (B grade, SOT-23-3) | TI TLV431BQDBZR | **C398374** | 27096 | automotive Q grade, cheapest TI B-grade ($0.27 @1) |
| Shunt ref (B grade) alt | onsemi TLV431BSN1T1G | C91075 | 16937 | 16 V part; pinout not verified here - check the onsemi datasheet before treating as a drop-in |
| Shunt ref (A grade, 1 %) | TI TLV431AIDBZR | C56765 | 28513 | |
| Dual PMOS (chosen) | Vishay SiA975DJ-T1-GE3 | **C141597** | 2692 | model in `vishay_sia975dj/` |
| Dual PMOS alt (no model) | Diodes DMP2065UFDB-7 | C461059 | 2985 | |
| Single PMOS (model) | Vishay SI2371EDS-T1-GE3 | **C467897** | 1611 | model in `vishay_si2371eds/` |
| Single PMOS (basic part) | AOS AO3401A | C15127 | 625857 | no vendor model |
| Load switch | TI TPS2553DBVR | C55266 | 54809 | (-1 latch version: TPS2553DBVR-1 C111738, 12026) |
| Buck-boost | TI TPS63802DLAR | C2845237 | 41640 | |

## SHA-256 of vendor zips

```
e71f754b5c473849cc0aabb5096536b10acb5a9b968e9e4234795ff460bbccb3  ti_tlv431/SLIM005.zip
615c68db56115e44fda07a3d871ea2d29f8890ebc26c4b90374291d003417f0e  ti_tps2553/SLVM425.zip
19e0bd80a35629f3084746796a51c4724346337077cb54aa96f6012190b0c8e6  ti_tps63802/SLVMCX1.zip
2bcbf8a9d465035793fc3e9f7000dc9ef5717c8cabbe80ce772bd07e732d7290  vishay_si2371eds/si2371eds_ps_reva.zip
```
