#!/usr/bin/env python3
"""Add (once) the 'Radio' hierarchical sheet to the root schematic and (re)generate radio.kicad_sch.

Usage: add_radio_sheet.py PROJECT_DIR NETLIST.json
"""
import os, subprocess, sys

ROOT = 'GameboyCartridgeV2.1.kicad_sch'
PROJECT = 'GameboyCartridgeV2.1'
SHEET_UUID = '5f3a9c2e-7b1d-4e6a-9c0f-1a2b3c4d5e60'  # fixed so regeneration keeps references stable

SHEET_BLOCK = f'''	(sheet
		(at 101.6 57.15)
		(size 36.83 41.91)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(fields_autoplaced yes)
		(stroke
			(width 0.1524)
			(type solid)
		)
		(fill
			(color 0 0 0 0.0000)
		)
		(uuid "{SHEET_UUID}")
		(property "Sheetname" "Radio"
			(at 101.6 56.4384 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(justify left bottom)
			)
		)
		(property "Sheetfile" "radio.kicad_sch"
			(at 101.6 99.6446 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(justify left top)
			)
		)
		(instances
			(project "{PROJECT}"
				(path "/ROOTUUID"
					(page "4")
				)
			)
		)
	)
'''


def main(pdir, netlist):
    root_path = os.path.join(pdir, ROOT)
    s = open(root_path).read()
    root_uuid = s.split('(uuid "', 1)[1].split('"', 1)[0]
    if SHEET_UUID not in s:
        i = s.index('\t(sheet_instances')
        s = s[:i] + SHEET_BLOCK.replace('ROOTUUID', root_uuid) + s[i:]
        open(root_path, 'w').write(s)
        print('added Radio sheet to root')
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.run([sys.executable, os.path.join(here, 'gen_radio_sheet.py'), netlist,
                    os.path.join(pdir, 'radio.kicad_sch'), root_uuid, SHEET_UUID, PROJECT], check=True)


if __name__ == '__main__':
    main(*sys.argv[1:3])
