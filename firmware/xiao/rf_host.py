#!/usr/bin/env python3
"""Host side of the XIAO antenna test (rf_logger) and log capture for a7_cal.

  python3 rf_host.py /dev/cu.usbmodemXXXX [--out results.csv] [--tcp 5201]

- Runs the TCP sink rf_logger uploads to, and counts what really arrived.
- Reads the board's serial lines and tags each with the current position. Type a new position name
  (e.g. free, copper2mm, in_console) and press Enter to switch; type q to finish.
- On exit prints per-position RSSI (median/min/max dBm) and uplink throughput (median kbit/s).
Needs pyserial (it is in the ESP-IDF Python env: run it after `. ~/esp/esp-idf-v5.5.1/export.sh`).
"""
import argparse
import csv
import socket
import statistics
import sys
import threading
import time

import serial

ap = argparse.ArgumentParser()
ap.add_argument('port')
ap.add_argument('--out', default='rf_results.csv')
ap.add_argument('--tcp', type=int, default=5201)
args = ap.parse_args()

label = ['free']
rows = []
lock = threading.Lock()
stop = threading.Event()


def sink():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', args.tcp))
    srv.listen(1)
    srv.settimeout(0.5)
    while not stop.is_set():
        try:
            c, _ = srv.accept()
        except socket.timeout:
            continue
        n, t0 = 0, time.time()
        with c:
            while chunk := c.recv(65536):
                n += len(chunk)
        dt = time.time() - t0
        with lock:
            rows.append((time.time(), label[0], 'HOST', n, f'{n * 8 / 1000 / dt:.0f}' if dt > 0 else '0'))


def reader(ser):
    while not stop.is_set():
        line = ser.readline().decode(errors='replace').strip()
        if not line:
            continue
        print(f'[{label[0]}] {line}')
        if line.split(',')[0] in ('RSSI', 'TPUT', 'INFO', 'A7'):
            with lock:
                rows.append((time.time(), label[0], *line.split(',')))


ser = serial.Serial(args.port, 115200, timeout=0.5)
threading.Thread(target=sink, daemon=True).start()
threading.Thread(target=reader, args=(ser,), daemon=True).start()
print(f'position: {label[0]}  (type a new name + Enter to switch, q to finish)')
for cmd in sys.stdin:
    cmd = cmd.strip()
    if cmd == 'q':
        break
    if cmd:
        label[0] = cmd
        print(f'position: {cmd}')
stop.set()
time.sleep(0.6)

with open(args.out, 'w', newline='') as f:
    csv.writer(f).writerows(rows)
print(f'\nwrote {len(rows)} rows to {args.out}')
for pos in dict.fromkeys(r[1] for r in rows):
    rssi = [int(r[4]) for r in rows if r[1] == pos and r[2] == 'RSSI']
    tput = [float(r[6]) for r in rows if r[1] == pos and r[2] == 'TPUT' and r[4] != 'ERR']
    host = [float(r[4]) for r in rows if r[1] == pos and r[2] == 'HOST']
    s = f'{pos:>14}: '
    s += (f'RSSI median {statistics.median(rssi):.0f} dBm (min {min(rssi)}, max {max(rssi)}, n={len(rssi)})'
          if rssi else 'no RSSI')
    if tput:
        s += f'; uplink median {statistics.median(tput):.0f} kbit/s (board), '
        s += f'{statistics.median(host):.0f} kbit/s (received)' if host else 'none received'
    print(s)
