; RGBDS example for the WiFi cartridge: PING, GET and POWER through ../wifi.asm, answers on screen.
; Build: make (see Makefile). Runs in the full-system simulation: sim/system/run_rom.py build/example.gb
INCLUDE "hardware.inc"
INCLUDE "wifi.inc"

SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150 - @, 0

SECTION "vars", WRAM0
wRow: ds 1

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
    ld hl, $9800                ; clear the map with spaces
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
    ld [wRow], a
    ld a, LCDC_ON | LCDC_BG_ON | LCDC_BLOCK21 | LCDC_BG_9800
    ldh [rLCDC], a

    ld hl, sTitle
    call PrintText
    call WifiOpen

    ld hl, sPing
    call PrintText
    ld a, WIFI_PING
    ld hl, 0
    call Ask

    ld hl, sGet
    call PrintText
    ld a, WIFI_GET
    ld hl, sUrl
    call Ask

    ld hl, sPower
    call PrintText
    ld a, WIFI_POWER
    ld hl, 0
    call Ask

    ld hl, sDone
    call PrintText
.halt:
    jr .halt

; Ask: A = command, HL = payload or 0. Prints the answer (or TIMEOUT).
Ask:
    ld bc, WIFI_FRAMES_DEFAULT
    call WifiRequest
    cp WIFI_TIMEOUT
    jr nz, .answer
    ld hl, sTimeout
    jp PrintText
.answer:                        ; WIFI_DONE or WIFI_ERROR: the response says what happened
    ld hl, WIFI_MB_RESP
    jp PrintText

; PrintText: HL = 0-terminated text, '\n' starts a new row, 20 columns per row.
PrintText:
.row:
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
    cp 10                       ; '\n'
    jr z, .newline
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
.newline:                       ; also after 20 columns
    ld a, [wRow]
    inc a
    ld [wRow], a
    ld a, [hl]
    or a
    ret z
    jr .row
.end:
    ld a, [wRow]
    inc a
    ld [wRow], a
    ret

WaitVBlank::                    ; required by wifi.asm
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

sTitle:   db "RGBDS WIFI EXAMPLE", 0
sPing:    db "PING:", 0
sGet:     db "GET /hello:", 0
sUrl:     db "http://example.com/hello", 0
sPower:   db "POWER:", 0
sTimeout: db "TIMEOUT", 0
sDone:    db "DONE", 0

SECTION "Font", ROM0
Font:
INCBIN "font.2bpp"
FontEnd:
