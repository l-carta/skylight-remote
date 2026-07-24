#!/usr/bin/env python3
"""
Schritt 1 - BLE-Sichtprüfung.
Findet die Skylight (BLE-Name "BK_MESH_light") und zeigt RSSI + Advertisement.

Zweck: bestätigen, dass der Raspberry Pi die Lampe über sein eigenes Bluetooth
überhaupt sieht - bevor wir uns an den Mesh-Stack machen.

Setup (auf dem Pi):
    sudo apt install -y python3-pip bluez
    pip3 install bleak
Ausführen:
    python3 scan.py
"""

import asyncio
from bleak import BleakScanner

TARGET = "BK_MESH"           # Namensteil der Skylight
MESH_PROV_UUID = "1827"      # Mesh Provisioning Service
MESH_PROXY_UUID = "1828"     # Mesh Proxy Service (nach Provisioning)


async def main():
    print("Scanne 15 s nach BLE-Geräten ... (Lampe sollte in Reichweite sein)\n")
    found = await BleakScanner.discover(timeout=15.0, return_adv=True)

    hits = []
    for addr, (dev, adv) in found.items():
        name = dev.name or adv.local_name or ""
        is_target = TARGET in name if name else False
        # auch anhand des Mesh-Service erkennen, falls kein Name kommt
        svc = [u.split("-")[0][-4:] for u in (adv.service_uuids or [])]
        is_mesh = any(u in (MESH_PROV_UUID, MESH_PROXY_UUID) for u in svc)

        if is_target or is_mesh:
            hits.append((addr, name or "(kein Name)", adv.rssi, svc, adv.service_data))

    if not hits:
        print("KEINE Mesh-Lampe gefunden.")
        print("- Lampe eingeschaltet & in Reichweite?")
        print("- Auf dem Pi Bluetooth aktiv? (bluetoothctl -> power on)")
        print("- nRF Connect/andere App, die die Verbindung blockiert, schließen.")
        return

    print("Gefunden:\n")
    for addr, name, rssi, svc, sdata in hits:
        print(f"  {addr}   RSSI {rssi} dBm   name={name}")
        if svc:
            print(f"    services: {', '.join(svc)}")
        for uuid, data in (sdata or {}).items():
            print(f"    service_data {uuid.split('-')[0][-4:]}: {data.hex()}")
        print()

    print("Wenn hier BK_MESH_light steht -> Pi sieht die Lampe. Weiter zu Schritt 2.")


if __name__ == "__main__":
    asyncio.run(main())
