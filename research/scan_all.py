#!/usr/bin/env python3
"""
Breiter BLE-Scan - listet ALLE Geraete (nicht nur die Lampe), um die
Original-Fernbedienung zu finden und zu sehen, ob sie sich provisionieren
laesst.

Marker:
  0x1827  Mesh Provisioning  -> UNPROVISIONIERT, koppelbar (das wollen wir!)
  0x1828  Mesh Proxy         -> schon provisioniert (z.B. unsere Lampe)

Werksfernbedienungen funken oft nur beim Tastendruck / im Pairing-Modus.
Deshalb laeuft der Scan laenger und zeigt auch schwache/namelose Geraete.
Tipp: waehrend des Scans Tasten an der Remote druecken bzw. sie in den
Reset-/Pairing-Modus bringen.

    sudo systemctl stop skylight-bridge
    python3 research/scan_all.py            # 20s
    python3 research/scan_all.py 40         # 40s
    sudo systemctl start skylight-bridge
"""

import asyncio
import sys

from bleak import BleakScanner

PROV = "1827"
PROXY = "1828"


def short(u: str) -> str:
    return u.split("-")[0][-4:]


async def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    seen = {}   # addr -> (name, rssi, set(short-uuids), service_data)

    def cb(dev, adv):
        uuids = {short(u) for u in (adv.service_uuids or [])}
        prev = seen.get(dev.address)
        # staerkstes RSSI behalten, UUIDs akkumulieren
        name = dev.name or adv.local_name or (prev[0] if prev else "")
        rssi = adv.rssi if not prev else max(prev[1], adv.rssi)
        if prev:
            uuids |= prev[2]
        seen[dev.address] = (name, rssi, uuids, adv.service_data or {})

    print(f"=== Breiter BLE-Scan {dur:.0f}s - jetzt an der Remote Tasten "
          f"druecken / Pairing-Modus ===", flush=True)
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(dur)
    await scanner.stop()

    print(f"\n{len(seen)} Geraet(e) gesehen:\n")
    # nach RSSI sortiert (naechste zuerst)
    for addr, (name, rssi, uuids, sdata) in sorted(
            seen.items(), key=lambda kv: -kv[1][1]):
        flags = []
        if PROV in uuids:
            flags.append("<== PROVISIONING 0x1827 (KOPPELBAR!)")
        if PROXY in uuids:
            flags.append("(Proxy 0x1828 - schon im Netz)")
        us = ",".join(sorted(uuids)) or "-"
        print(f"  {addr}  RSSI {rssi:>4} dBm  name={name or '(kein Name)':<18} "
              f"uuids={us}  {' '.join(flags)}")
        for u, d in (sdata or {}).items():
            print(f"        service_data {short(u)}: {d.hex()}")

    prov = [a for a, v in seen.items() if PROV in v[2]]
    print()
    if prov:
        print(f">>> {len(prov)} koppelbares Geraet(e) (0x1827): {prov}")
        print("    Wenn eins davon die Remote ist -> mit provision.py in unser "
              "Netz aufnehmen, dann Mode-Tasten druecken + mesh_monitor.py.")
    else:
        print(">>> Kein 0x1827 gesehen. Remote sendet evtl. nur kurz beim "
              "Tastendruck (Scan mit gedrueckter Taste wiederholen) oder laesst "
              "sich nicht provisionieren.")


if __name__ == "__main__":
    asyncio.run(main())
