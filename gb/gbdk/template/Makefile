# GBDK-2020 4.5.0. GBDK ?= path to gbdk
GBDK ?= $(HOME)/opt/gbdk
LCC = $(GBDK)/bin/lcc
# MBC5 + RAM + battery (0x1B), 16 RAM banks (128 KB): bank 15 is the mailbox, banks 0-14 are yours
CART = -Wm-yt0x1B -Wm-ya16 -Wm-yn"WIFITMPL"

build/app.gb: main.c ../lib/wifi.c ../lib/wifi.h
	mkdir -p build
	$(LCC) $(CART) -o $@ main.c ../lib/wifi.c

clean:
	rm -rf build
