# RGBDS example. Font and hardware.inc come from sim/system/rom (rendered by its make_font.py).
ROMDIR = ../../../sim/system/rom
ASM = rgbasm -Werror -I.. -I$(ROMDIR)

all: build/example.gb

build/%.o: %.asm ../wifi.inc
	mkdir -p build
	$(ASM) -o $@ $<

build/wifi.o: ../wifi.asm ../wifi.inc
	mkdir -p build
	$(ASM) -o $@ $<

build/example.gb: build/main.o build/wifi.o
	rgblink -o $@ -n build/example.sym $^
	rgbfix -v -p 0xFF -m MBC5+RAM+BATTERY -r 0x04 -t WIFIEXAMPLE $@

clean:
	rm -rf build
