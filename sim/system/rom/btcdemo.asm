; btcdemo.gb - WiFi cartridge demo: scan for networks, pick one, type the password, connect, then fetch the
; BTC/USD price of the last 24 h and draw it as a chart. Talks to the cartridge mailbox (sim/system/README.md):
;   05 SCAN     -> display-ready lines "n BAND CHxx RSSI SEC", '\n'-separated
;   06 CONNECT  payload "n|password" -> "OK a.b.c.d" or "ERR ..."
;   07 BTC      -> "$price  +x.xx%\nH high  L low\nHH:MM UTC  24H\n" + 128 bytes (0x20 + 0..63, one per column)
; wState ($C000) tells the test harness which screen is waiting for input.

INCLUDE "hardware.inc"

DEF MAILBOX_BANK EQU $0F
DEF MB_CMD    EQU $A000
DEF MB_SEQ    EQU $A001
DEF MB_STATUS EQU $A002
DEF MB_ACK    EQU $A003
DEF MB_REQ    EQU $A010
DEF MB_RESP   EQU $A100

DEF ST_BOOT   EQU 1
DEF ST_LIST   EQU 2
DEF ST_PASS   EQU 3
DEF ST_CONN   EQU 4
DEF ST_LOAD   EQU 5
DEF ST_CHART  EQU 6
DEF ST_ERROR  EQU 7

DEF OAM_BASE  EQU $FE00
DEF KEY_COLS  EQU 16
DEF KEY_ROWS  EQU 4
DEF KEY_ROW0  EQU 7                     ; screen row of the first keyboard row
DEF KEY_COL0  EQU 2

SECTION "state", WRAM0[$C000]
wState:    ds 1

SECTION "chart", WRAM0[$C100]
wChart:    ds 128 * 16                  ; 16 x 8 tiles, 128 x 64 px

SECTION "vars", WRAM0
wSeq:      ds 1
wRow:      ds 1
wCol:      ds 1
wPad:      ds 1
wPadOld:   ds 1
wPadNew:   ds 1
wSel:      ds 1
wCount:    ds 1
wKx:       ds 1
wKy:       ds 1
wPassLen:  ds 1
wPass:     ds 24
wReq:      ds 32
wIp:       ds 24
wX:        ds 1
wPrevY:    ds 1
wY:        ds 1
wTop:      ds 1

SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150 - @, 0

SECTION "Main", ROM0
Start:
    di
    ld sp, $DFFF
    call LcdOff
    ld hl, Font
    ld de, $9000 + 32 * 16              ; BG tile n = ASCII n (block 2, signed addressing)
    ld bc, FontEnd - Font
    call MemCopy
    ld hl, CursorTile
    ld de, $8000                        ; OBJ tile 0
    ld bc, 16
    call MemCopy
    call ClearOam
    ld a, %11100100
    ldh [rBGP], a
    ld a, %11100100
    ldh [rOBP0], a
    xor a
    ldh [rSCY], a
    ldh [rSCX], a
    ld [wSeq], a
    ld [wPadOld], a
    ld a, $0A                           ; open the mailbox: cart RAM on, bank 15
    ld [$0000], a
    ld a, MAILBOX_BANK
    ld [$4000], a

; ---------------------------------------------------------------- scan
Scan:
    ld a, ST_BOOT
    ld [wState], a
    call LcdOff
    call ClearMap
    ld hl, sTitle
    ld bc, $0000
    call PrintAt
    ld hl, sScanning
    ld bc, $0200
    call PrintAt
    call LcdOn
    ld a, $05
    ld hl, sEmpty
    call Request
    cp 2
    jp nz, ShowError
    call LcdOff
    call ClearMap
    ld hl, sPick
    ld bc, $0000
    call PrintAt
    ld hl, MB_RESP
    ld bc, $0201                        ; list from row 2, column 1
    call PrintLines
    ld a, [wRow]
    sub 2
    ld [wCount], a
    ld hl, sListHelp
    ld bc, $1100
    call PrintAt
    xor a
    ld [wSel], a
    call LcdOn
    ld a, ST_LIST
    ld [wState], a
.loop:
    call DrawListCursor
    call WaitVBlank
    call ReadPad
    ld a, [wPadNew]
    bit B_PAD_DOWN, a
    jr z, .notdown
    ld a, [wCount]
    ld b, a
    ld a, [wSel]
    inc a
    cp b
    jr nc, .loop
    ld [wSel], a
    jr .loop
.notdown:
    bit B_PAD_UP, a
    jr z, .notup
    ld a, [wSel]
    or a
    jr z, .loop
    dec a
    ld [wSel], a
    jr .loop
.notup:
    bit B_PAD_A, a
    jr z, .loop

; ---------------------------------------------------------------- password
Password:
    xor a
    ld [wPassLen], a
    ld [wPass], a
    ld [wKx], a
    ld [wKy], a
PasswordScreen:
    call LcdOff
    call ClearMap
    ld hl, sPassFor
    ld bc, $0000
    call PrintAt
    ld a, '#'                           ; "#n"
    ld [wReq], a
    ld a, [wSel]
    add '1'
    ld [wReq + 1], a
    xor a
    ld [wReq + 2], a
    ld hl, wReq
    ld bc, $0100
    call PrintAt
    ld hl, KeyRow0                      ; 4 rows of 16 keys (LCD is off: plain copies)
    ld de, $9800 + KEY_ROW0 * 32 + KEY_COL0
    ld c, KEY_ROWS
.krow:
    ld b, KEY_COLS
.kcol:
    ld a, [hli]
    ld [de], a
    inc de
    dec b
    jr nz, .kcol
    ld a, e
    add 32 - KEY_COLS
    ld e, a
    adc d
    sub e
    ld d, a
    dec c
    jr nz, .krow
    ld hl, sKeyHelp1
    ld bc, $0F00
    call PrintAt
    ld hl, sKeyHelp2
    ld bc, $1000
    call PrintAt
    call ShowPass
    ld a, LCDC_ON | LCDC_BG_ON | LCDC_OBJ_ON | LCDC_BLOCK21 | LCDC_BG_9800
    ldh [rLCDC], a
    ld a, ST_PASS
    ld [wState], a
.loop:
    call WaitVBlank
    call DrawKeyCursor
    call ReadPad
    ld a, [wPadNew]
    bit B_PAD_RIGHT, a
    jr z, .r
    ld a, [wKx]
    inc a
    and KEY_COLS - 1
    ld [wKx], a
    jp .loop
.r:
    bit B_PAD_LEFT, a
    jr z, .l
    ld a, [wKx]
    dec a
    and KEY_COLS - 1
    ld [wKx], a
    jp .loop
.l:
    bit B_PAD_DOWN, a
    jr z, .d
    ld a, [wKy]
    inc a
    and KEY_ROWS - 1
    ld [wKy], a
    jp .loop
.d:
    bit B_PAD_UP, a
    jr z, .u
    ld a, [wKy]
    dec a
    and KEY_ROWS - 1
    ld [wKy], a
    jp .loop
.u:
    bit B_PAD_A, a
    jr z, .a
    ld a, [wPassLen]                    ; append the key under the cursor
    cp 20
    jp nc, .loop
    ld a, [wKy]
    swap a                              ; * 16
    ld b, a
    ld a, [wKx]
    add b
    ld e, a
    ld d, 0
    ld hl, KeyRow0
    add hl, de
    ld b, [hl]
    ld a, [wPassLen]
    ld e, a
    ld hl, wPass
    add hl, de
    ld [hl], b
    inc hl
    ld [hl], 0
    inc a
    ld [wPassLen], a
    call ShowPass
    jp .loop
.a:
    bit B_PAD_B, a
    jr z, .b
    ld a, [wPassLen]
    or a
    jp z, .loop
    dec a
    ld [wPassLen], a
    ld e, a
    ld d, 0
    ld hl, wPass
    add hl, de
    ld [hl], 0
    call ShowPass
    jp .loop
.b:
    bit B_PAD_START, a
    jp z, .loop

; ---------------------------------------------------------------- connect
Connect:
    ld a, ST_CONN
    ld [wState], a
    call LcdOff
    call ClearOam
    call ClearMap
    ld hl, sConnecting
    ld bc, $0200
    call PrintAt
    call LcdOn
    ld hl, wReq                         ; payload "n|password"
    ld a, [wSel]
    add '1'
    ld [hli], a
    ld a, '|'
    ld [hli], a
    ld de, wPass
.cp:
    ld a, [de]
    ld [hli], a
    inc de
    or a
    jr nz, .cp
    ld a, $06
    ld hl, wReq
    call Request
    cp 2
    jp nz, ConnectFailed
    ld hl, MB_RESP                      ; keep "OK a.b.c.d" for the chart screen
    ld de, wIp
    ld bc, 20
    call MemCopy
    xor a
    ld [wIp + 20], a
    ld hl, MB_RESP
    ld bc, $0400
    call PrintAtSafe

; ---------------------------------------------------------------- BTC chart
LoadBtc:
    ld a, ST_LOAD
    ld [wState], a
    ld hl, sLoading
    ld bc, $0600
    call PrintAtSafe
    ld a, $07
    ld hl, sEmpty
    call Request
    cp 2
    jp nz, ShowError
    call DrawChart
    ld a, ST_CHART
    ld [wState], a
.wait:
    call WaitVBlank
    call ReadPad
    ld a, [wPadNew]
    bit B_PAD_A, a
    jr z, .wait
    ld hl, sRefreshing
    ld bc, $1100
    call PrintAtSafe
    jr LoadBtc

ConnectFailed:
    ld hl, MB_RESP
    ld bc, $0400
    call PrintAtSafe
    ld hl, sRetry
    ld bc, $0600
    call PrintAtSafe
    ld a, ST_ERROR
    ld [wState], a
.w:
    call WaitVBlank
    call ReadPad
    ld a, [wPadNew]
    bit B_PAD_A, a
    jr z, .w
    jp Password

ShowError:
    ld hl, sErr
    ld bc, $0800
    call PrintAtSafe
    ld hl, MB_RESP
    ld bc, $0900
    call PrintAtSafe
    ld a, ST_ERROR
    ld [wState], a
.w:
    call WaitVBlank
    call ReadPad
    ld a, [wPadNew]
    bit B_PAD_A, a
    jr z, .w
    jp Scan

; ---------------------------------------------------------------- chart rendering
; Response: 3 text lines then 128 column values (0x20 + 0..63, 63 = top).
DrawChart:
    ld hl, wChart                       ; clear the bitmap
    ld bc, 128 * 16
.clr:
    xor a
    ld [hli], a
    dec bc
    ld a, b
    or c
    jr nz, .clr
    ld hl, MB_RESP                      ; skip 3 lines to the column data
    ld c, 3
.skip:
    ld a, [hli]
    cp 10
    jr nz, .skip
    dec c
    jr nz, .skip
    xor a
    ld [wX], a
    ld a, [hl]                          ; first point: previous = itself
    sub $20
    cpl
    and 63                              ; y = 63 - v
    ld [wPrevY], a
.col:
    push hl
    ld a, [hl]
    sub $20
    cpl
    and 63
    ld [wY], a
    ld b, a                             ; top = min(y, prev), bottom = max(y, prev)
    ld a, [wPrevY]
    cp b
    jr c, .prevsmaller
    ld c, a                             ; bottom = prev
    ld a, b                             ; top = y
    jr .have
.prevsmaller:
    ld c, b                             ; bottom = y, top = prev
.have:
    ld [wTop], a
    ; line pixels (colour 3) from top to bottom
.line:
    ld a, [wTop]
    push bc
    ld c, a
    ld a, [wX]
    ld b, a
    ld e, 3
    call Plot
    pop bc
    ld a, [wTop]
    cp c
    jr z, .fill
    inc a
    ld [wTop], a
    jr .line
.fill:                                  ; area under the line (colour 1) from bottom + 1 to 63
    ld a, c
    cp 63
    jr z, .next
    inc a
    ld c, a
    push bc
    ld a, [wX]
    ld b, a
    ld e, 1
    call Plot
    pop bc
    jr .fill
.next:
    ld a, [wY]
    ld [wPrevY], a
    pop hl
    inc hl
    ld a, [wX]
    inc a
    ld [wX], a
    cp 128
    jr nz, .col

    call LcdOff
    call ClearMap
    ld hl, wChart                       ; tiles 128..255 at $8800
    ld de, $8800
    ld bc, 128 * 16
    call MemCopy
    ld hl, $9800 + 3 * 32 + 2           ; map rows 3..10, columns 2..17
    ld a, 128
    ld c, 8
.mrow:
    ld b, 16
.mcol:
    ld [hli], a
    inc a
    dec b
    jr nz, .mcol
    ld de, 32 - 16
    add hl, de
    dec c
    jr nz, .mrow
    ld hl, sBtcTitle
    ld bc, $0000
    call PrintAt
    ld hl, MB_RESP                      ; "$price  change"
    ld bc, $0100
    call PrintLine1
    call NextLine
    ld bc, $0B00                        ; "H .. L .."
    call PrintLine1
    call NextLine
    ld bc, $0C00                        ; "HH:MM UTC 24H"
    call PrintLine1
    ld hl, wIp
    ld bc, $0E00
    call PrintAt
    ld hl, sChartHelp
    ld bc, $1100
    call PrintAt
    jp LcdOn

; NextLine: HL -> after the next '\n'
NextLine:
    ld a, [hli]
    cp 10
    jr nz, NextLine
    ret

; Plot: B = x (0..127), C = y (0..63), E = colour (1 or 3). Bitmap tile = (y/8)*16 + x/8, row = y%8.
Plot:
    ld a, c
    and %00111000
    rrca
    rrca
    rrca
    add HIGH(wChart)
    ld h, a
    ld a, b
    and %01111000
    add a                               ; (x/8) * 16
    ld l, a
    ld a, c
    and 7
    add a
    add l
    ld l, a
    push hl
    ld a, b
    and 7
    ld hl, BitMask
    add l
    ld l, a
    adc h
    sub l
    ld h, a
    ld d, [hl]
    pop hl
    ld a, [hl]                          ; low bit plane: colour bit 0
    or d
    ld [hli], a
    bit 1, e
    ret z
    ld a, [hl]                          ; high bit plane: colour bit 1
    or d
    ld [hl], a
    ret

; ---------------------------------------------------------------- helpers
; Request: A = command, HL = payload. Returns A = STATUS (2 done, 3 error, 4 timeout).
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
    ld [MB_SEQ], a
    ld hl, 60 * 20                      ; 20 s
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
    ret nc
.notyet:
    dec hl
    ld a, h
    or l
    jr nz, .wait
    ld a, 4
    ret

; ReadPad: wPad = buttons (1 = pressed, PAD_* bit order), wPadNew = newly pressed this frame
ReadPad:
    ld a, JOYP_GET_CTRL_PAD
    ldh [rJOYP], a
    ldh a, [rJOYP]
    ldh a, [rJOYP]
    cpl
    and $0F
    swap a
    ld b, a
    ld a, JOYP_GET_BUTTONS
    ldh [rJOYP], a
    ldh a, [rJOYP]
    ldh a, [rJOYP]
    ldh a, [rJOYP]
    cpl
    and $0F
    or b
    ld b, a
    ld a, JOYP_GET_NONE
    ldh [rJOYP], a
    ld a, [wPadOld]
    cpl
    and b
    ld [wPadNew], a
    ld a, b
    ld [wPad], a
    ld [wPadOld], a
    ret

DrawListCursor:                         ; '>' in column 0 of the selected row, blank elsewhere
    ld a, [wCount]
    ld c, a
    ld b, 0
.r:
    ld a, b
    add 2
    ld e, a
    ld d, 0
    REPT 5
    sla e
    rl d
    ENDR
    ld hl, $9800
    add hl, de
    ld a, [wSel]
    cp b
    ld a, ' '
    jr nz, .put
    ld a, '>'
.put:
    ld d, a
.v:
    ldh a, [rSTAT]
    and STAT_BUSY
    jr nz, .v
    ld [hl], d
    inc b
    ld a, b
    cp c
    jr nz, .r
    ret

DrawKeyCursor:                          ; OBJ 0 over the selected key
    ld a, [wKy]
    add KEY_ROW0 + 2                    ; OAM y = row * 8 + 16
    add a
    add a
    add a
    ld [OAM_BASE], a
    ld a, [wKx]
    add KEY_COL0 + 1                    ; OAM x = col * 8 + 8
    add a
    add a
    add a
    ld [OAM_BASE + 1], a
    xor a
    ld [OAM_BASE + 2], a
    ld [OAM_BASE + 3], a
    ret

ShowPass:                               ; row 3: the password typed so far + cursor
    ld hl, sBlank
    ld bc, $0300
    call PrintAtSafe
    ld hl, wPass
    ld bc, $0301
    call PrintAtSafe
    ld a, [wPassLen]
    inc a
    ld c, a
    ld b, 3
    ld hl, sUnder
    jp PrintAtSafe

ClearOam:
    ld hl, OAM_BASE
    ld b, 160
    xor a
.c:
    ld [hli], a
    dec b
    jr nz, .c
    ret

ClearMap:                               ; LCD must be off
    ld hl, $9800
    ld bc, 32 * 32
.c:
    ld a, ' '
    ld [hli], a
    dec bc
    ld a, b
    or c
    jr nz, .c
    ret

LcdOff:
    ldh a, [rLCDC]
    bit 7, a
    ret z
    call WaitVBlank
    xor a
    ldh [rLCDC], a
    ret

LcdOn:
    ld a, LCDC_ON | LCDC_BG_ON | LCDC_OBJ_ON | LCDC_BLOCK21 | LCDC_BG_9800
    ldh [rLCDC], a
    ret

; PrintAt / PrintAtSafe: HL = 0-terminated string, B = row, C = column (max 20 chars). Safe = waits for VRAM.
PrintAt:
PrintAtSafe:
    ld a, b
    ld [wRow], a
    ld e, b
    ld d, 0
    REPT 5
    sla e
    rl d
    ENDR
    ld a, e
    add c
    ld e, a
    push hl
    ld hl, $9800
    add hl, de
    ld d, h
    ld e, l
    pop hl
.ch:
    ld a, [hli]
    or a
    ret z
    cp 10
    ret z
    push af
.v:
    ldh a, [rSTAT]
    and STAT_BUSY
    jr nz, .v
    pop af
    ld [de], a
    inc de
    jr .ch

; PrintLine1: like PrintAt but stops at '\n' or 0 and leaves HL on that character
PrintLine1:
    call PrintAt
    dec hl
    ret

; PrintLines: HL = text with '\n' separators, B = first row, C = column. wRow = row after the last line.
PrintLines:
.line:
    push bc
    call PrintAt
    pop bc
    inc b
    ld a, b
    ld [wRow], a
    dec hl
    ld a, [hli]
    or a
    ret z
    ld a, [hl]
    or a
    ret z
    jr .line

WaitVBlank:
    ldh a, [rLCDC]
    bit 7, a
    ret z
.w:
    ldh a, [rLY]
    cp 144
    jr nz, .w
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

BitMask:     db $80, $40, $20, $10, $08, $04, $02, $01
CursorTile:  db $FF, $FF, $81, $81, $81, $81, $81, $81, $81, $81, $81, $81, $81, $81, $FF, $FF
KeyRow0:     db "abcdefghijklmnop"
KeyRow1:     db "qrstuvwxyz012345"
KeyRow2:     db "6789ABCDEFGHIJKL"
KeyRow3:     db "MNOPQRSTUVWXYZ-_", 0

sTitle:      db "WIFI CART  BTC DEMO", 0
sScanning:   db "SCANNING FOR WIFI..", 0
sPick:       db "PICK A NETWORK:", 0
sListHelp:   db "UP/DOWN  A:SELECT", 0
sPassFor:    db "PASSWORD FOR NETWORK", 0
sKeyHelp1:   db "A:TYPE  B:DELETE", 0
sKeyHelp2:   db "START:CONNECT", 0
sConnecting: db "CONNECTING...", 0
sLoading:    db "LOADING BTC/USD...", 0
sRefreshing: db "REFRESHING...      ", 0
sBtcTitle:   db "BTC/USD   LAST 24H", 0
sChartHelp:  db "A:REFRESH", 0
sRetry:      db "A: TRY AGAIN", 0
sErr:        db "ERROR:", 0
sEmpty:      db 0
sBlank:      db "                    ", 0
sUnder:      db "_", 0

SECTION "Font", ROM0
Font:
INCBIN "font.2bpp"
FontEnd:
