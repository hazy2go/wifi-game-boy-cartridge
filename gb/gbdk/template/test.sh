#!/bin/sh
# Build and run the template on the simulated cartridge: press A (GET), wait, scroll down once, check the screen.
set -e
cd "$(dirname "$0")"
make -s
python3 ../../../sim/system/run_rom.py build/app.gb --console weak --seconds 12 \
    --input "F60 A F400 D F30" --fixtures test/fixtures.json --expect test/expect.txt --out build/sim "$@"
