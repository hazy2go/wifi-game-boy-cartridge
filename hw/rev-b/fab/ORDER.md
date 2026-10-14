# JLCPCB order: WiFi Game Boy cartridge Rev B

**Not ordered yet.** Hold points (LOGBOOK 18:20-19:05):
1. ESP32-C3 partial-calibration time on a dev board (A7: <= 5 ms at 50 % TX duty, else firmware falls back to stored calibration data).
2. FPC antenna over copper / inside a shell / in a console (RSSI).
3. **OPTIONAL: the real console 5 V.** The DMG power-board schematics put it at about 4.9-5.5 V, typically 5.1-5.2 V (LOGBOOK 2026-10-14), so measuring is not required. To confirm, use a multimeter on the link port while a game runs, or the XIAO ADC through a 2:1 divider. The power stage keeps the console >= 4.588 V (R126 27.0k); in simulation WiFi works down to ~4.8 V on a 1.2 ohm console (slower) and ~4.75 V on a 0.7 ohm one, and stays off (console safe) below that. A lower floor (26.7k) was tried and rejected: burst-after-idle dips below 4.5 V (audit A8.5).

## Files (this folder)
| file | upload as |
|---|---|
| `GameboyCartridgeV2.1-RevB-gerbers.zip` | Gerber file (13 Gerber layers + PTH/NPTH Excellon drill, mm, absolute origin) |
| `GameboyCartridgeV2.1-BOM-JLC.csv` | BOM (Comment, Designator, Footprint, LCSC Part #); DNP parts (D4-D8, R1, R17) are left out |
| `GameboyCartridgeV2.1-CPL-JLC.csv` | CPL / pick and place (Designator, Mid X, Mid Y, Layer, Rotation); all parts on top |
| `bom_check.txt` | live JLC library check: every part exists, package matches the footprint, value matches, stock covers 5 boards |
| `cpl_check.txt` | rotation/offset of every part derived from the footprint JLC will place (matched pad by pad) |

Regenerate: `tools/make_bom.py GameboyCartridgeV2.1.kicad_sch fab`, `tools/make_cpl.py GameboyCartridgeV2.1.kicad_pcb fab/...-BOM-JLC.csv fab/...-CPL-JLC.csv`, Gerbers/drill with `kicad-cli` as in LOGBOOK.

## PCB options
| option | value | why |
|---|---|---|
| Layers | **6** | |
| Dimensions | 52.95 x 61.34 mm (read from the Gerbers) | |
| PCB qty | 5 | |
| Thickness | **0.8 mm** | fits the cartridge slot (Croco's author recommends 0.8 mm) |
| Impedance control / stackup | **Yes, JLC06081H-7628** | the stackup the layout was designed on: F.Cu-In1 0.2104 mm, same as Croco's 4-layer board, so its USB/HyperRAM/QSPI routing keeps its impedance (LOGBOOK) |
| Via covering | **Epoxy filled & capped** (POFV) | 29 vias sit inside SMD pads (RP2350 exposed pad, ground pads); they must be filled or solder wicks away. Free on JLC 6-layer |
| Min via hole / diameter | 0.15 mm / 0.25 mm (board minimum used: 0.15 / 0.30) | |
| Surface finish | **ENIG** | edge-connector fingers and fine-pitch parts; Croco's recommendation |
| Gold fingers | No (ENIG covers them) | as Croco |
| Remove order number | **Specify a location** (the `JLCJLCJLCJLC` marker on the back) | keeps the number off the front |
| Solder mask / silkscreen | any colour (Croco: green / white) | |

## Assembly options (PCBA)
| option | value |
|---|---|
| PCBA type | Standard (BGA HyperRAM and 0.4 mm QFN need standard, not economic) |
| Assembly side | Top |
| PCBA qty | 5 (minimum 2) |
| Tooling holes | added by JLC |
| Confirm parts placement | **Yes**: check the photos/preview below before production |

## Before paying: check in JLC's placement preview
The CPL already carries the corrections JLC's own footprints need (e.g. USB-C J1 shifted 1.30 mm, ESP32-C3 module U104 0.70 mm, TPS63802 U103 0.075 mm; SOT/SOIC/MSOP/QFN rotations), each derived pad-by-pad, none guessed. Still look at these in the preview, pin 1 / polarity marks:
- U3 RP2350 (QFN-80), U4 HyperRAM (BGA, ball A1), U104 ESP32-C3 module (antenna end to the board edge), U103 buck-boost, U107 USB hub
- polarised: D1 LED, D2/D3/D101-D104 diodes (cathode band), C105-C107 tantalum (+), Q1/Q101/Q102/U109 (SOT), Y1/Y101 crystals
- J1 USB-C and J2 microSD sit on their pads and face the board edge

## Not on the board (buy separately)
- 2.4 GHz stick-on FPC antenna with an **I-PEX MHF III ("IPEX3" / "3rd generation", = Hirose W.FL)** plug. The ESP32-C3-MINI-1U uses this smaller connector (datasheet v2.2, 10.3), **not U.FL/IPEX1**: a U.FL antenna does not plug in. Goes inside the shell at the grip end (LOGBOOK 18:45). Not common in Japanese shops; AliExpress sells them (search "IPEX3 2.4G FPC antenna" / "MHF3"); prefer small ones (<= ~35 x 10 mm). **Pick (2026-10-14): CDEBYTE/Ebyte TX2400-FPC3-2509** (25 x 9 mm, IPEX3, 10 pcs about 663 yen, https://ja.aliexpress.com/item/1005012574238566.html, choose variant "TX2400-FPC3-2509"). A buyer review confirms it works on an ESP32-S3-MINI-1U, which uses the same connector.
- Cartridge shell (`3dprint/` or a standard DMG shell).

## Part changes vs Croco's schematic (2026-10-14, stock)
- U2 flash W25Q16JVSSIQ (0 stock) -> **W25Q128JVSIQ** C97521 (same family/footprint, 16 MB, JLC basic part)
- U4 HyperRAM W956A8MBYA5I (0 stock) -> **ISSI IS66WVH8M8BLL-100B1LI** C1349096 (3.0 V, 64 Mb, identical HyperBus ball map, checked ball by ball; 100 MHz max vs ~22 MHz used by Croco's firmware)
- U1 USBLC6-2P6: same part, in-stock listing C2827693
- C27/C28 RTC crystal load caps: schematic said 4.3 pF but linked a 15 pF part; now 4.3 pF C59526 (MCP7951x datasheet eq. 5-1 with the 7 pF crystal)
