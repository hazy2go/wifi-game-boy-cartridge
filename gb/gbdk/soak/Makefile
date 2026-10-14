GBDK ?= $(HOME)/opt/gbdk
build/soak.gb: soak.c ../lib/wifi.c ../lib/wifi.h
	mkdir -p build
	$(GBDK)/bin/lcc -Wm-yt0x1B -Wm-ya16 -Wm-yn"WIFISOAK" -o $@ soak.c ../lib/wifi.c
