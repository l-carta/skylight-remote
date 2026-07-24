#!/usr/bin/env python3
"""Skylight-CLI - die Lampe steuern und diagnostizieren.

    python3 skylight.py on            # einschalten
    python3 skylight.py off           # ausschalten
    python3 skylight.py toggle        # umschalten
    python3 skylight.py status        # aktuellen Zustand abfragen
    python3 skylight.py scan          # BLE-Sicht + RSSI der Lampe
    python3 skylight.py provision     # frisch ins Mesh aufnehmen (siehe provision.py)

Hinweis: An der Skylight wirkt nur an/aus zuverlaessig. Helligkeit, Weisston
und die Modi laufen ueber ein proprietaeres Protokoll der Fernbedienung und
sind nicht ansteuerbar (siehe README).
"""

import asyncio
import sys

from bleak import BleakScanner

from meshlib.skylight import SkylightClient, CONFIG_FILE
from meshlib.state import load_cfg


def log(msg):
    print(msg, flush=True)


async def cmd_power(target):
    async with SkylightClient(log=log) as sky:
        if target == "toggle":
            target = not await sky.get_power()
        state = await sky.set_power(target)
        print(f"Lampe ist jetzt: {'AN' if state else 'AUS'}")


async def cmd_status():
    async with SkylightClient(log=log) as sky:
        print(f"Lampe ist: {'AN' if await sky.get_power() else 'AUS'}")


async def cmd_scan():
    cfg = load_cfg(CONFIG_FILE)
    mac = cfg["mac"].upper()
    print(f"Suche Lampe {mac} (8 s) ...")
    devs = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for addr, (d, adv) in devs.items():
        if addr.upper() == mac:
            q = ("sehr gut" if adv.rssi > -65 else "gut" if adv.rssi > -75
                 else "grenzwertig" if adv.rssi > -85 else "schwach")
            svc = [u[4:8] for u in (adv.service_data or {})]
            print(f"  gefunden: RSSI {adv.rssi} dBm ({q}), Services {svc}")
            return
    print("  NICHT gefunden - Lampe an & in Reichweite?")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("on", "off"):
        asyncio.run(cmd_power(cmd == "on"))
    elif cmd == "toggle":
        asyncio.run(cmd_power("toggle"))
    elif cmd == "status":
        asyncio.run(cmd_status())
    elif cmd == "scan":
        asyncio.run(cmd_scan())
    elif cmd == "provision":
        import provision
        asyncio.run(provision.main())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
