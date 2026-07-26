#!/usr/bin/env python3
"""
Untersucht das custom Service f000ffc0 der Remote (ffc1/ffc2, read/write/notify).
ffc1 lieferte ARM-Maschinencode -> das riecht nach einem Speicher-/Firmware-
Fenster (TI-OAD/Debug-Base-UUID f000xxxx-0451-4000-b000-...).

PHASE READ-ONLY: verbindet (Retry, Remote muss wach sein -> Taste HALTEN),
liest ffc1/ffc2 mehrfach (aendert sich der Inhalt? = Streaming/Fenster) und
lauscht ~10s auf Notifications. Schreibt (noch) NICHTS.

    sudo systemctl stop skylight-bridge
    python3 research/remote_ffc0.py [MAC] [dauer_s]
    sudo systemctl start skylight-bridge
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

MAC = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
    "Usage: remote_ffc0.py <REMOTE_MAC> [dauer_s]  (MAC via scan_all.py finden)")
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0

FFC1 = "f000ffc1-0451-4000-b000-000000000000"
FFC2 = "f000ffc2-0451-4000-b000-000000000000"


def h(data: bytes) -> str:
    return bytes(data).hex()


async def find(timeout=8.0):
    fut = asyncio.get_event_loop().create_future()

    def cb(dev, adv):
        if dev.address.upper() == MAC.upper() and not fut.done():
            fut.set_result(dev)

    sc = BleakScanner(detection_callback=cb)
    await sc.start()
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        await sc.stop()


async def explore(dev):
    async with BleakClient(dev, timeout=20) as c:
        print(f"# verbunden: {c.is_connected}\n", flush=True)

        notes = []

        def on_note(handle, data):
            notes.append((handle, bytes(data)))
            print(f"    NOTIFY h={handle}: {h(data)}", flush=True)

        # Notifications an, damit ein evtl. Stream reinkommt
        for u in (FFC1, FFC2):
            try:
                await c.start_notify(u, on_note)
            except Exception as e:
                print(f"# notify {u[4:8]} n/a: {e}", flush=True)

        # ffc1/ffc2 mehrfach lesen -> aendert sich was?
        for i in range(6):
            for u in (FFC2, FFC1):
                try:
                    v = await c.read_gatt_char(u)
                    print(f"[{i}] {u[4:8]} = {h(v)}", flush=True)
                except Exception as e:
                    print(f"[{i}] {u[4:8]} = <err: {e}>", flush=True)
            await asyncio.sleep(1.0)

        print("# lausche 10s auf Notifications (Taste an der Remote druecken!) ...",
              flush=True)
        await asyncio.sleep(10)

        for u in (FFC1, FFC2):
            try:
                await c.stop_notify(u)
            except Exception:
                pass
        print(f"\n# {len(notes)} Notification(s) gesammelt.", flush=True)


async def main():
    print(f"# Suche Remote {MAC} bis {DUR:.0f}s - TASTE GEDRUECKT HALTEN ...",
          flush=True)
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    while loop.time() - t0 < DUR:
        dev = await find(8.0)
        if dev:
            print("# >>> gefunden, verbinde ...", flush=True)
            try:
                await explore(dev)
                print("\n# fertig.", flush=True)
                return
            except Exception as e:
                print(f"# Connect/Explore-Fehler ({e}), retry ...", flush=True)
        await asyncio.sleep(0.5)
    print("# Remote nicht erreicht. Taste gehalten? Naeher ran?", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
