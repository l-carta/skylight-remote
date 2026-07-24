#!/usr/bin/env python3
"""
Das ECHTE Advertising der Lampe auslesen - Service-Data, Manufacturer-Data,
Name, Service-UUIDs - damit wir es im Fake (imp_lamp.py) 1:1 replizieren und
die Remote uns als 'ihre' Lampe akzeptiert.

Lampe muss an & unverbunden sein (Bridge gestoppt).

    python3 dump_lamp_adv.py            # MAC aus skylight-mesh.json
    python3 dump_lamp_adv.py <MAC>
"""

# --- Pfad-Bootstrap: dieses Tool liegt in research/, der Stack + die
# Config (skylight-mesh.json) liegen im Repo-Root eine Ebene hoeher. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio
import json
import sys

from bleak import BleakScanner


def load_mac():
    try:
        return json.load(open(_CFG))["mac"].upper()
    except Exception:
        return None


async def main():
    mac = (sys.argv[1] if len(sys.argv) > 1 else load_mac()).upper()
    print(f"Scanne 15s nach {mac} ...")
    hits = []

    def cb(dev, adv):
        if dev.address.upper() == mac:
            hits.append(adv)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(15)
    await scanner.stop()

    if not hits:
        print("Nicht gesehen. Lampe an? Bridge gestoppt?")
        return 1
    adv = hits[-1]
    print(f"\n=== Advertising von {mac} ({len(hits)} Pakete) ===")
    print(f"local_name     : {adv.local_name!r}")
    print(f"tx_power       : {adv.tx_power}")
    print(f"rssi           : {adv.rssi}")
    print(f"service_uuids  : {adv.service_uuids}")
    print("service_data   :")
    for u, d in (adv.service_data or {}).items():
        print(f"    {u} = {d.hex()}")
    print("manufacturer_data :")
    for cid, d in (adv.manufacturer_data or {}).items():
        print(f"    0x{cid:04x} = {d.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
