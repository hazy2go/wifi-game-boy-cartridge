; wifitest.gb - Game Boy test ROM for the WiFi cartridge (system simulation gate, LOGBOOK 14:57).
; Uses the cartridge "mailbox": MBC5 cart-RAM bank 15 is not save RAM but a request/response window that the
; RP2350 firmware bridges to the ESP32-C3 over SPI (protocol: sim/system/README.md).
;   A000 CMD   A001 SEQ (write last: submits)   A002 STATUS (0 idle, 1 busy, 2 done, 3 error)
;   A003 ACK   A004 LEN lo   A005 LEN hi   A006 RAIL (console mV / 20)   A007 RESERVOIR (mV / 20)
;   A010-A0FF request payload (0-terminated)   A100-A1FF response payload (0-terminated)
; Commands: 01 PING, 02 TIME (UTC from NTP, "YYYY-MM-DD HH:MM:SS"), 03 HTTP GET (payload = URL), 04 POWER.
; Test sequence: PING, TIME, GET http://example.com/hello, POWER, then 20 back-to-back GETs (burst traffic),
; then POWER again. Every result is printed; the harness reads the tile map to check the text.

INCLUDE "hardware.inc"

DEF MAILBOX_BANK EQU $0F
DEF MB_CMD    EQU $A000
DEF MB_SEQ    EQU $A001
DEF MB_STATUS EQU $A002
DEF MB_ACK    EQU $A003
DEF MB_RAIL   EQU $A006
DEF MB_RES    EQU $A007
DEF MB_REQ    EQU $A010
DEF MB_RESP   EQU $A100

SECTION "vars", WRAM0
wSeq:      ds 1
wRow:      ds 1
wLoop:     ds 1
wTimeout:  ds 2

SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150 - @, 0
    db "WIFICART"                ; opt-in: the cartridge serves the WiFi mailbox (docs/ARCHITECTURE.md)

SECTION "Main", ROM0
Start:
    di
    ld sp, $DFFF
    call WaitVBlank
    xor a
    ldh [rLCDC], a              ; LCD off to load VRAM
    ld hl, Font
    ld de, $9000 + 32 * 16      ; tile n = ASCII n (tiles 32..127)
    ld bc, FontEnd - Font
    call MemCopy
    ld hl, $9800                ; clear the BG map with spaces
    ld bc, 32 * 32
.clr:
    ld a, ' '
    ld [hli], a
    dec bc
    ld a, b
    or c
    jr nz, .clr
    ld a, %11100100
    ldh [rBGP], a
    xor a
    ldh [rSCY], a
    ldh [rSCX], a
    ld [wSeq], a
    ld [wRow], a
    ld a, LCDC_ON | LCDC_BG_ON | LCDC_BLOCK21 | LCDC_BG_9800
    ldh [rLCDC], a

    ld hl, sTitle
    call PrintLine
    ; open the mailbox: enable cart RAM, select bank 15
    ld a, $0A
    ld [$0000], a
    ld a, MAILBOX_BANK
    ld [$4000], a

    ; PING
    ld a, $01
    ld hl, sEmpty
    call Request
    ld hl, sPing
    call PrintResult
    ; TIME
    ld a, $02
    ld hl, sEmpty
    call Request
    ld hl, sTime
    call PrintResult
    ; HTTP GET
    ld a, $03
    ld hl, sUrl
    call Request
    ld hl, sGet
    call PrintResult
    ; POWER
    ld a, $04
    ld hl, sEmpty
    call Request
    ld hl, sPower
    call PrintResult
    ; burst: 20 GETs back to back
    ld a, 20
    ld [wLoop], a
.burst:
    ld a, $03
    ld hl, sUrl2
    call Request
    ld a, [MB_STATUS]
    cp 2
    jr nz, .burstfail
    ld a, [wLoop]
    dec a
    ld [wLoop], a
    jr nz, .burst
    ld hl, sBurstOk
    call PrintLine
    jr .after
.burstfail:
    ld hl, sBurstFail
    call PrintLine
.after:
    ld a, $04
    ld hl, sEmpty
    call Request
    ld hl, sPower
    call PrintResult
    ld hl, sDone
    call PrintLine
.halt:
    halt
    nop
    jr .halt

; Request: A = command, HL = 0-terminated payload. Returns when STATUS is done/error or on timeout.
Request:
    ld [MB_CMD], a
    ld de, MB_REQ
.cp:
    ld a, [hli]
    ld [de], a
    inc de
    or a
    jr nz, .cp
    ld a, [wSeq]
    inc a
    ld [wSeq], a
    ld [MB_SEQ], a               ; submit
    ld hl, 60 * 10                ; 10 s timeout in frames
.wait:
    push hl
    call WaitVBlank
    pop hl
    ld a, [wSeq]
    ld b, a
    ld a, [MB_ACK]
    cp b
    jr nz, .notyet
    ld a, [MB_STATUS]
    cp 2
    ret nc                        ; 2 done or 3 error
.notyet:
    dec hl
    ld a, h
    or l
    jr nz, .wait
    ld a, 4                       ; timeout marker
    ld [MB_STATUS], a
    ret

; PrintResult: HL = label. Prints "label" then the response (or ERR/TIMEOUT) on the next line.
PrintResult:
    call PrintLine
    ld a, [MB_STATUS]
    cp 2
    jr z, .ok
    cp 3
    jr z, .err
    ld hl, sTimeout
    jp PrintLine
.err:
    ld hl, sErr
    jp PrintLine
.ok:
    ld hl, MB_RESP
    jp PrintLine

; PrintLine: HL = 0-terminated string (max 20 chars), written at column 0 of row wRow, then wRow++.
PrintLine:
    ld a, [wRow]
    ld e, a
    ld d, 0
    REPT 5
    sla e
    rl d
    ENDR
    push hl
    ld hl, $9800
    add hl, de
    ld d, h
    ld e, l
    pop hl
    ld b, 20
.ch:
    ld a, [hli]
    or a
    jr z, .end
    push af
.vram:
    ldh a, [rSTAT]
    and STAT_BUSY
    jr nz, .vram
    pop af
    ld [de], a
    inc de
    dec b
    jr nz, .ch
.end:
    ld a, [wRow]
    inc a
    ld [wRow], a
    ret

WaitVBlank:
    ldh a, [rLY]
    cp 144
    jr nz, WaitVBlank
.out:
    ldh a, [rLY]
    cp 144
    jr z, .out
    ret

MemCopy:
    ld a, [hli]
    ld [de], a
    inc de
    dec bc
    ld a, b
    or c
    jr nz, MemCopy
    ret

sTitle:     db "WIFI CART TEST", 0
sEmpty:     db 0
sPing:      db "PING:", 0
sTime:      db "TIME (NTP):", 0
sGet:       db "GET /hello:", 0
sPower:     db "POWER:", 0
sUrl:       db "http://example.com/hello", 0
sUrl2:      db "http://example.com/burst", 0
sBurstOk:   db "BURST 20/20 OK", 0
sBurstFail: db "BURST FAIL", 0
sTimeout:   db "TIMEOUT", 0
sErr:       db "ERR", 0
sDone:      db "DONE", 0

SECTION "Font", ROM0
Font:
INCBIN "font.2bpp"
FontEnd:
