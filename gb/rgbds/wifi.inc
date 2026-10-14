; WiFi cartridge mailbox for RGBDS ROMs. INCLUDE "wifi.inc" and link wifi.asm (wifi.o).
; Protocol: sim/system/README.md. The ROM must be MBC5 with 128 KB RAM: rgbfix -m MBC5+RAM+BATTERY -r 0x04.
; Linking wifi.o puts "WIFICART" into ROM0, which makes the cartridge serve the mailbox to this ROM.
; Your ROM must EXPORT a routine WaitVBlank (returns once per frame); WifiRequest calls it while it waits.
IF !DEF(WIFI_INC)
DEF WIFI_INC EQU 1

DEF WIFI_PING    EQU $01   ; -> "PONG"
DEF WIFI_TIME    EQU $02   ; -> "YYYY-MM-DD HH:MM:SS" (UTC)
DEF WIFI_GET     EQU $03   ; payload URL -> body (<= 255 bytes, ASCII 32-126 and \n)
DEF WIFI_POWER   EQU $04   ; -> "RAIL 4.59V RES 3.50V"
DEF WIFI_SCAN    EQU $05   ; -> up to 8 lines "1 MyNetwork  -45 W2"
DEF WIFI_CONNECT EQU $06   ; payload "n|password" -> "OK a.b.c.d"
DEF WIFI_BTC     EQU $07   ; -> 3 text lines + 128 chart bytes

DEF WIFI_PENDING EQU 1
DEF WIFI_DONE    EQU 2
DEF WIFI_ERROR   EQU 3     ; reason in the response, e.g. "ERR NOT CONNECTED"
DEF WIFI_TIMEOUT EQU 0     ; WifiRequest gave up after BC frames

DEF WIFI_FRAMES_DEFAULT EQU 60 * 13   ; > the cartridge's 12 s RADIO TIMEOUT

; mailbox (cart RAM bank 15)
DEF WIFI_MB_CMD    EQU $A000
DEF WIFI_MB_SEQ    EQU $A001
DEF WIFI_MB_STATUS EQU $A002
DEF WIFI_MB_ACK    EQU $A003
DEF WIFI_MB_LEN    EQU $A004   ; 16-bit little endian
DEF WIFI_MB_RAIL   EQU $A006   ; volts x 50
DEF WIFI_MB_RES    EQU $A007
DEF WIFI_MB_REQ    EQU $A010   ; payload, 0-terminated, max 240
DEF WIFI_MB_RESP   EQU $A100   ; response, 0-terminated

ENDC
