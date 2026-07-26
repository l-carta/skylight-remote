#!/usr/bin/env python3
"""
Remote ausquetschen: verbindet sich mit der Fernbedienung (Proxy-Server 0x1828)
und liest ALLES Lesbare raus - Gerätename, Appearance, Device-Info (Modell/
Firmware/Hersteller), custom Characteristics + das volle Advertising
(Hersteller-/Service-Daten).

Die Remote funkt nur kurz beim Tastendruck -> das Tool retryt ~40s lang.
DU musst waehrenddessen eine Taste an der Remote GEDRUECKT HALTEN, damit sie
wach bleibt.

    sudo systemctl stop skylight-bridge
    python3 research/remote_probe.py [MAC] [dauer_s]
    sudo systemctl start skylight-bridge
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

MAC = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
    "Usage: remote_probe.py <REMOTE_MAC> [dauer_s]  (MAC via scan_all.py finden)")
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0


def show(data: bytes) -> str:
    a = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{data.hex():<48} |{a}|"


async def grab_adv(timeout=8.0):
    """Scannt bis MAC auftaucht, gibt (device, advertisement) zurueck."""
    fut = asyncio.get_event_loop().create_future()

    def cb(dev, adv):
        if dev.address.upper() == MAC.upper() and not fut.done():
            fut.set_result((dev, adv))

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await scanner.stop()


async def dump_adv(adv):
    print(f"# --- Advertising ---", flush=True)
    print(f"#   local_name = {adv.local_name!r}  rssi={adv.rssi}", flush=True)
    for cid, data in (adv.manufacturer_data or {}).items():
        print(f"#   manufacturer 0x{cid:04x}: {bytes(data).hex()}", flush=True)
    for uuid, data in (adv.service_data or {}).items():
        print(f"#   service_data {uuid.split('-')[0][-4:]}: {bytes(data).hex()}",
              flush=True)
    print(f"#   service_uuids = {adv.service_uuids}", flush=True)


async def dump_gatt(dev):
    async with BleakClient(dev, timeout=20) as c:
        print(f"# verbunden: {c.is_connected}\n", flush=True)
        for svc in c.services:
            print(f"Service {svc.uuid}", flush=True)
            for ch in svc.characteristics:
                short = ch.uuid.split("-")[0][-4:]
                props = ",".join(ch.properties)
                if "read" in ch.properties:
                    try:
                        v = bytes(await c.read_gatt_char(ch))
                        print(f"    {short} [{props}] = {show(v)}", flush=True)
                    except Exception as e:
                        print(f"    {short} [{props}] = <read-Fehler: {e}>",
                              flush=True)
                else:
                    print(f"    {short} [{props}] = <nicht lesbar>", flush=True)


async def main():
    print(f"# Suche Remote {MAC} bis zu {DUR:.0f}s - "
          f"JETZT eine Taste GEDRUECKT HALTEN ...", flush=True)
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    while loop.time() - t0 < DUR:
        hit = await grab_adv(timeout=8.0)
        if hit:
            dev, adv = hit
            print(f"# >>> Remote gefunden: {dev.name or '(kein Name)'}",
                  flush=True)
            await dump_adv(adv)
            try:
                await dump_gatt(dev)
                print("\n# fertig - alles Lesbare oben.", flush=True)
                return
            except Exception as e:
                print(f"# GATT-Connect fehlgeschlagen ({e}), retry ...",
                      flush=True)
        await asyncio.sleep(0.5)
    print("# Remote nicht erreicht. Taste gedrueckt gehalten? Naeher ran?",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
