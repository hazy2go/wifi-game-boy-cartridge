# GBDK-2020 4.5.0. GBDK ?= path to gbdk (e.g. ~/opt/gbdk)
GBDK ?= $(HOME)/opt/gbdk
LCC = $(GBDK)/bin/lcc
# MBC5 + RAM + battery (0x1B) with 16 RAM banks (128 KB): bank 15 is the WiFi mailbox
CART = -Wm-yt0x1B -Wm-ya16 -Wm-yn"WIFIDEMO"

all: build/wifi_demo.gb

build/wifi_demo.gb: demo/wifi_demo.c lib/wifi.c lib/wifi.h
	mkdir -p build
	$(LCC) $(CART) -o $@ demo/wifi_demo.c lib/wifi.c

clean:
	rm -rf build
