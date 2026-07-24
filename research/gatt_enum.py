#!/usr/bin/env python3
"""
Diagnose - GATT-Services der Lampe auflisten.

Zweck: herausfinden, ob die Skylight NEBEN dem SIG-Mesh (0x1827/0x1828) auch
den Telink-proprietaeren Mesh-GATT-Service anbietet. Wenn ja, koennen wir
Helligkeit/Farbe ueber dessen Command-Characteristic (Opcode 0xD2) direkt
steuern - ganz ohne Remote-Sniff oder Firmware-Dump.

Telink-proprietaerer Mesh-Service (Basis-UUID):
    00010203-0405-0607-0809-0a0b0c0d19xx
    ..1911 Notify/Status   ..1912 Command   ..1913 OTA   ..1914 Pair

WICHTIG: Die Lampe advertised nur, wenn sie NICHT verbunden ist. Vorher also
den Bridge-Dienst stoppen:  sudo systemctl stop skylight-bridge

    python3 gatt_enum.py               # nutzt MAC aus skylight-mesh.json
    python3 gatt_enum.py <MAC>
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

from bleak import BleakClient, BleakScanner

TELINK_PREFIX = "00010203-0405-0607-0809-0a0b0c0d19"
SIG_PROV = "1827"
SIG_PROXY = "1828"


def load_mac():
    try:
        return json.load(open(_CFG))["mac"]
    except Exception:
        return None


def tag(uuid: str) -> str:
    u = uuid.lower()
    if u.startswith(TELINK_PREFIX):
        return "  <== TELINK proprietaer!"
    short = u.split("-")[0][-4:]
    if short == SIG_PROV:
        return "  (SIG Mesh Provisioning)"
    if short == SIG_PROXY:
        return "  (SIG Mesh Proxy)"
    return ""


async def main():
    mac = sys.argv[1] if len(sys.argv) > 1 else load_mac()
    if not mac:
        print("Keine MAC. Uebergib sie als Argument.")
        return 1
    print(f"Suche Lampe {mac} ... (Bridge muss gestoppt sein)")
    dev = await BleakScanner.find_device_by_address(mac, timeout=20.0)
    if not dev:
        print("Nicht gefunden. Advertised sie? -> Bridge gestoppt? "
              "Lampe stromlos neustarten?")
        return 1
    print(f"Gefunden: {dev.name or '(kein Name)'}  Verbinde ...")
    async with BleakClient(dev) as client:
        print(f"Verbunden: {client.is_connected}\n")
        found_telink = False
        for svc in client.services:
            print(f"Service {svc.uuid}{tag(svc.uuid)}")
            if svc.uuid.lower().startswith(TELINK_PREFIX):
                found_telink = True
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"    char {ch.uuid}  [{props}]{tag(ch.uuid)}")
        print()
        if found_telink:
            print(">>> TELINK-Service vorhanden! Wir koennen Helligkeit/Farbe "
                  "ueber die Command-Char (..1912, Opcode 0xD2) direkt steuern.")
        else:
            print(">>> Kein Telink-Service. Dann laeuft Helligkeit vermutlich "
                  "ueber ein SIG-VENDOR-Modell (Company 0x0211) -> Composition "
                  "Data der Lampe auf Vendor-Modelle pruefen.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
