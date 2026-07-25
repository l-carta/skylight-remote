#!/usr/bin/env python3
"""Wahrheitstabelle fuer Generic OnOff: rohe Status-Bytes gegen den echten
physischen Zustand.

Hintergrund: skylight.py nimmt an, dass die Firmware OnOff invertiert -- und
zwar "inkl. der Status-Antworten". Das Schalten ist nachweislich invertiert,
aber gemessen am Lichtsensor liefert get_power() den falschen Zustand. Dieses
Skript trennt beide Richtungen sauber:

  SET  -> welches Wire-Byte schaltet die Lampe wirklich an?
  GET  -> welches Wire-Byte meldet die Lampe in welchem Zustand?

Ausgegeben werden die ROHEN Bytes ohne jede Interpretation, dazu als
unabhaengige physische Referenz der Lichtsensor aus Home Assistant. Damit
laesst sich Wire-Byte <-> Helligkeit eindeutig zuordnen.

Achtung: Braucht die Proxy-Verbindung exklusiv.
    sudo systemctl stop skylight-bridge
    HA_TOKEN=... python3 research/onoff_truth.py
    sudo systemctl start skylight-bridge

Env:
    HA_URL     default http://127.0.0.1:8123
    HA_TOKEN   Long-Lived Token (ohne den laeuft es, aber ohne Lux-Referenz)
    LUX_ENTITY default sensor.bad_anwesenheitssensor_light_sensor_light_level
    SETTLE     Sekunden Wartezeit, bis der Sensor nachzieht (default 12)
"""

import asyncio
import json
import os as _os
import sys as _sys
import urllib.request

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

from meshlib.skylight import (  # noqa: E402
    OP_ONOFF_GET, OP_ONOFF_SET, SkylightClient,
)

HA_URL = _os.environ.get("HA_URL", "http://127.0.0.1:8123")
HA_TOKEN = _os.environ.get("HA_TOKEN", "")
LUX_ENTITY = _os.environ.get(
    "LUX_ENTITY", "sensor.bad_anwesenheitssensor_light_sensor_light_level")
SETTLE = float(_os.environ.get("SETTLE", "12"))


def lux() -> str:
    """Lichtsensor als unabhaengige physische Referenz."""
    if not HA_TOKEN:
        return "n/a"
    req = urllib.request.Request(
        f"{HA_URL}/api/states/{LUX_ENTITY}",
        headers={"Authorization": f"Bearer {HA_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)["state"] + " lx"
    except Exception as e:
        return f"Fehler: {e}"


def show(label: str, params: bytes) -> None:
    extra = ""
    if len(params) >= 3:
        extra = (f"  present=0x{params[0]:02x}"
                 f" target=0x{params[1]:02x} remaining=0x{params[2]:02x}")
    elif params:
        extra = f"  present=0x{params[0]:02x}"
    print(f"  {label:<26} -> [{params.hex(' ')}]{extra}", flush=True)


async def main() -> None:
    async with SkylightClient(log=lambda *_: None) as sky:
        print(f"\n=== Ausgangslage (Sensor: {lux()}) ===")
        show("GET", await sky._request(OP_ONOFF_GET, b""))

        for wire in (0x00, 0x01):
            print(f"\n=== SET wire=0x{wire:02x} ===")
            show(f"SET 0x{wire:02x}",
                 await sky._request(OP_ONOFF_SET,
                                    bytes([wire, sky._next_tid()])))
            print(f"  ... {SETTLE:.0f}s warten, bis der Sensor nachzieht",
                  flush=True)
            await asyncio.sleep(SETTLE)
            show("GET danach", await sky._request(OP_ONOFF_GET, b""))
            print(f"  PHYSISCH: {lux()}", flush=True)

        sky.save()
        print("\nFertig. Seq gespeichert.")


if __name__ == "__main__":
    asyncio.run(main())
