#!/usr/bin/env python3
"""Real-world network side of the BTC demo simulation (called by sysim's ESP32-C3 model through popen).

  scan     the networks this Mac's WiFi radio actually sees (system_profiler). macOS redacts SSIDs without
           location permission, so names come back as "<redacted>"; channel, band, security and signal are real.
           Output: one line per network "NAME|CH|BAND|SEC|RSSI" (RSSI 0 when macOS does not report it), strongest
           first, max 8.
  ip       this Mac's IPv4 address on its WiFi interface (the address the cart would get from the same AP).
  fetch U  GET of URL U for the GET command: prints the HTTP status, a newline, then up to 255 body bytes.
           GBW_FIXTURES=file.json ({"url": "body"} or {"url": {"status": 404}}) answers from the file only;
           GBW_OFFLINE=1 answers every URL with status 0 (ERR NO CONNECTION). Otherwise it fetches live.
  btc      live BTC/USDT from Binance (15 min candles, last 24 h; CoinGecko as fallback). Output:
           "UTC_HHMM|C0 C1 ... Cn" with the raw closes, oldest first.
Network failures print "ERR <reason>" so the ROM shows an explicit error, never made-up data.
Set BTC_FIXTURE=file.json (a list of closes) to replay a recorded series offline."""
import json, os, re, subprocess, sys, time, urllib.request


def scan():
    out = subprocess.run(['system_profiler', 'SPAirPortDataType', '-json'], capture_output=True, text=True, timeout=60).stdout
    nets = []
    for itf in json.loads(out)['SPAirPortDataType'][0].get('spairport_airport_interfaces', []):
        cur = itf.get('spairport_current_network_information')
        lst = list(itf.get('spairport_airport_other_local_wireless_networks', []))
        if cur:
            lst.insert(0, cur)
        for n in lst:
            ch = n.get('spairport_network_channel', '?')
            m = re.match(r'(\d+) \((\d)GHz', ch)
            sec = n.get('spairport_security_mode', '')
            sec = 'WPA3' if 'wpa3' in sec else 'WPA2' if 'wpa2' in sec else 'OPEN' if 'none' in sec else 'WPA'
            sn = n.get('spairport_signal_noise', '')
            rssi = int(sn.split()[0]) if sn else 0
            nets.append((n.get('_name', '?'), m.group(1) if m else '?', (m.group(2) + 'G') if m else '?', sec, rssi))
    nets.sort(key=lambda x: (x[4] == 0, -x[4]))
    for n in nets[:8]:
        print('|'.join(map(str, n)))


def ip():
    for itf in ('en1', 'en0'):
        out = subprocess.run(['ipconfig', 'getifaddr', itf], capture_output=True, text=True).stdout.strip()
        if out:
            print(out); return
    print('ERR no address')


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'wifi-gb-cart-sim'})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def btc():
    if os.environ.get('BTC_FIXTURE'):
        closes = json.load(open(os.environ['BTC_FIXTURE'])); t_last = time.time()
    else:
        try:
            k = get('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=96')
            closes = [float(x[4]) for x in k]; t_last = k[-1][6] / 1000
        except Exception:
            try:
                d = get('https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1')['prices']
                closes = [p[1] for p in d]; t_last = d[-1][0] / 1000
            except Exception as e:
                print(f'ERR {type(e).__name__}'); return
    # raw closes: the ESP's formatter (firmware/esp32c3/components/gbwifi) does the resampling and the text
    print(f"{time.strftime('%H%M', time.gmtime())}|" + ' '.join(f'{c:.2f}' for c in closes))
    json.dump(closes, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'btc_closes.json'), 'w'))


def fetch(url):
    fx = os.environ.get('GBW_FIXTURES')
    if fx:
        v = json.load(open(fx)).get(url)
        if v is None:
            print('404'); return
        if isinstance(v, dict):
            print(v.get('status', 200)); print(v.get('body', ''), end=''); return
        print('200'); print(v[:255], end=''); return
    if os.environ.get('GBW_OFFLINE'):
        print('0'); return
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'wifi-gb-cart-sim'})
        with urllib.request.urlopen(req, timeout=8) as r:
            print(r.status); print(r.read(255).decode('latin-1'), end='')
    except urllib.error.HTTPError as e:
        print(e.code)
    except Exception:
        print('0')


if __name__ == '__main__':
    if sys.argv[1] == 'fetch':
        fetch(sys.argv[2])
    else:
        {'scan': scan, 'ip': ip, 'btc': btc}[sys.argv[1]]()
