#!/usr/bin/env python3
"""Download the vendor SPICE models used by sim/power/inreg.py, check their SHA-256, extract them and apply
the ngspice compatibility edits documented in README.md.

The vendor files are not committed: TI and Vishay allow use for simulation but not public redistribution.
Run once after cloning:  python3 sim/models/fetch_models.py
"""
import hashlib, io, os, re, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0'}

ZIPS = {  # dir: (url, sha256, member regex, output name)
    'ti_tlv431': ('https://www.ti.com/lit/zip/SLIM005',
                  'e71f754b5c473849cc0aabb5096536b10acb5a9b968e9e4234795ff460bbccb3', r'(?i)tlv431\.lib$', 'TLV431.lib'),
    'ti_tps2553': ('https://www.ti.com/lit/zip/SLVM425',
                   '615c68db56115e44fda07a3d871ea2d29f8890ebc26c4b90374291d003417f0e', r'(?i)tps2553_trans\.lib$', 'TPS2553_TRANS.lib'),
    'ti_tps63802': ('https://www.ti.com/lit/zip/SLVMCX1',
                    '19e0bd80a35629f3084746796a51c4724346337077cb54aa96f6012190b0c8e6', r'(?i)tps63802_trans\.lib$', 'TPS63802_TRANS.lib'),
    'vishay_si2371eds': ('https://www.vishay.com/docs/67353/si2371eds_ps_reva.zip',
                         '2bcbf8a9d465035793fc3e9f7000dc9ef5717c8cabbe80ce772bd07e732d7290', r'(?i)si2371eds_ps rev a\.txt$', 'Si2371EDS_PS_RevA.lib'),
}
PLAIN = {'vishay_sia975dj': ('https://www.vishay.com/docs/65767/sia975dj.txt', 'SiA975DJ_PS.lib')}

XNOR_OLD = re.compile(r'E_ABMGATE\s+YINT 0 VALUE \{\{IF\(V\(A\) > \{VTHRESH\}\s+\^\s*\n\+\s*V\(B\) > \{VTHRESH\},\{VSS\},\{VDD\}\)\}\}')
XNOR_NEW = ('* ngspice-compat edit: PSpice boolean XOR "^" is the power operator in ngspice\n'
            'E_ABMGATE  YINT 0 VALUE {IF((V(A) > {VTHRESH}) != (V(B) > {VTHRESH}),{VSS},{VDD})}')
ONESHOT_OLD = re.compile(r'e_abm3\s+out 0 value \{ if\( v\(meas\)<0\.5 & v\(ch\)>0\.5,1,0\)\s*\}')
ONESHOT_NEW = ('* ngspice-compat edit: break the zero-delay loop out->ch->out with a 10 ps RC\n'
               'e_abm3         out_int 0 value { if( v(meas)<0.5 & v(ch)>0.5,1,0)    }\n'
               'r_ngs_out      out_int out 1\nc_ngs_out      out 0 10p')


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def patch(name, text):
    text = text.replace('\r\n', '\n').replace('–', '-')
    if name in ('TPS2553_TRANS.lib', 'TPS63802_TRANS.lib'):
        text, n = XNOR_OLD.subn(XNOR_NEW, text)
        assert n == 1, f'{name}: XNOR patch matched {n} times'
    if name == 'TPS63802_TRANS.lib':
        text, n = ONESHOT_OLD.subn(ONESHOT_NEW, text)
        assert n == 1, f'{name}: one-shot patch matched {n} times'
    return text


def main():
    for d, (url, sha, member, out) in ZIPS.items():
        blob = get(url)
        h = hashlib.sha256(blob).hexdigest()
        if h != sha:
            sys.exit(f'{d}: SHA-256 mismatch ({h}); vendor changed the file, re-verify the model (README.md)')
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = [n for n in z.namelist() if re.search(member, n)]
        assert len(names) == 1, f'{d}: expected one member matching {member}, got {names}'
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
        open(os.path.join(HERE, d, out), 'w').write(patch(out, z.read(names[0]).decode('latin-1')))
        print('ok', d, out)
    for d, (url, out) in PLAIN.items():
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
        open(os.path.join(HERE, d, out), 'w').write(get(url).decode('latin-1').replace('\r\n', '\n'))
        print('ok', d, out)


if __name__ == '__main__':
    main()
