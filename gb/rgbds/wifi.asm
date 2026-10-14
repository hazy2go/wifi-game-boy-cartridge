; WiFi cartridge mailbox routines for RGBDS. See wifi.inc.
INCLUDE "wifi.inc"

SECTION "WiFi opt-in", ROM0
WifiOptIn:: db "WIFICART"   ; anywhere in bank 0 (docs/ARCHITECTURE.md, "Which ROMs get the mailbox")

SECTION "WiFi vars", WRAM0
wWifiSeq:: ds 1

SECTION "WiFi code", ROM0

; WifiOpen: cart RAM on, bank 15 selected, SEQ continues from the mailbox (survives a Game Boy soft reset).
; Clobbers A.
WifiOpen::
    ld a, $0A
    ld [$0000], a
    ld a, 15
    ld [$4000], a
    ld a, [WIFI_MB_SEQ]
    ld [wWifiSeq], a
    ret

; WifiSubmit: A = command, HL = 0-terminated payload (or HL = 0 for none). Returns at once.
; Clobbers A, DE, HL.
WifiSubmit::
    ld [WIFI_MB_CMD], a
    ld de, WIFI_MB_REQ
    ld a, h
    or l
    jr z, .end
.copy:
    ld a, [hli]
    ld [de], a
    inc de
    or a
    jr z, .submit
    ld a, e                 ; stop at 240 bytes (A010-A0FF)
    cp $00
    jr nz, .copy
    dec de
.end:
    xor a
    ld [de], a
.submit:
    ld a, [wWifiSeq]
    inc a
    ld [wWifiSeq], a
    ld [WIFI_MB_SEQ], a     ; last: this write submits
    ret

; WifiPoll: A = WIFI_PENDING, WIFI_DONE or WIFI_ERROR for the last submitted request. Clobbers B.
WifiPoll::
    ld a, [wWifiSeq]
    ld b, a
    ld a, [WIFI_MB_ACK]
    cp b
    jr nz, .pending
    ld a, [WIFI_MB_STATUS]
    cp WIFI_DONE
    ret nc                  ; 2 done, 3 error
.pending:
    ld a, WIFI_PENDING
    ret

; WifiRequest: A = command, HL = payload (or 0), BC = frames to wait (WIFI_FRAMES_DEFAULT).
; Returns A = WIFI_DONE, WIFI_ERROR or WIFI_TIMEOUT. Calls your WaitVBlank once per frame. Clobbers all.
WifiRequest::
    push bc
    call WifiSubmit
    pop bc
.wait:
    push bc
    call WifiPoll
    pop bc
    cp WIFI_PENDING
    ret nz
    push bc
    call WaitVBlank
    pop bc
    dec bc
    ld a, b
    or c
    jr nz, .wait
    ld a, WIFI_TIMEOUT
    ret
