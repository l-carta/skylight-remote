#!/usr/bin/env python3
"""
Proxy-Listener: Pi verbindet sich als GATT-Client mit der Remote (Proxy-Server
0x1828), abonniert Proxy-Data-Out (2ade) und loggt ALLES, was die Remote beim
Tastendruck emittiert.

Sicher: nur Notifications aktivieren (CCCD-Write) + lauschen. KEINE OAD-/
Firmware-Writes.

Vorbehalt: Traffic ist mit dem Werks-NetKey verschluesselt -> wir sehen roh
(Chiffretext). Aber wir sehen, OB und WAS die Remote pro Tastendruck sendet
(Groesse/Timing), und ob evtl. unverschluesselte Beacons/Proxy-Config dabei
sind. Ein Decode-Versuch mit UNSEREM NetKey laeuft mit (wird i.d.R. scheitern).

Remote muss wach sein -> Taste GEDRUECKT HALTEN, dann Modes durchdruecken.

    sudo systemctl stop skylight-bridge
    python3 research/remote_listen.py [MAC] [dauer_s]
    sudo systemctl start skylight-bridge
"""

import asyncio
import os
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from meshlib import network                     # noqa: E402
from meshlib.skylight import CONFIG_FILE        # noqa: E402
from meshlib.state import load_cfg              # noqa: E402

MAC = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
    "Usage: remote_listen.py <REMOTE_MAC> [dauer_s]  (MAC via scan_all.py finden)")
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"   # notify
PROXY_IN = "00002add-0000-1000-8000-00805f9b34fb"    # write-no-resp


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


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


async def listen(dev, ctx):
    rx = []

    def on_note(handle, data):
        b = bytes(data)
        ptype = b[0] & 0x3F if b else -1
        line = f"[{stamp()}] 2ade <- {b.hex()} (proxytype=0x{ptype:02x})"
        # Decode-Versuch mit UNSEREM NetKey (Werksnetz -> erwartet: None)
        if ptype == 0x00 and len(b) > 1:
            dec = network.decode_network_pdu(ctx, b[1:])
            line += "  decode(unsere Keys)=" + (
                "MOEGLICH!" if dec else "nein (fremdes Netz)")
        print(line, flush=True)
        rx.append(b)

    async with BleakClient(dev, timeout=20) as c:
        print(f"# verbunden: {c.is_connected}. Abonniere 2ade ...", flush=True)
        await c.start_notify(PROXY_OUT, on_note)
        print(f"# Lausche {DUR:.0f}s - JETZT Modes an der Remote durchdruecken!",
              flush=True)
        await asyncio.sleep(DUR)
        try:
            await c.stop_notify(PROXY_OUT)
        except Exception:
            pass
    return rx


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])

    print(f"# Suche Remote {MAC} - Taste HALTEN ...", flush=True)
    for _ in range(6):
        dev = await find(8.0)
        if dev:
            try:
                rx = await listen(dev, ctx)
                print(f"\n# === {len(rx)} Notification(s) von der Remote ===",
                      flush=True)
                if not rx:
                    print("# Nichts gestreamt. Proxy leitet ohne gueltigen "
                          "Filter evtl. nichts weiter.", flush=True)
                return
            except Exception as e:
                print(f"# Fehler ({e}), retry ...", flush=True)
        await asyncio.sleep(0.5)
    print("# Remote nicht erreicht. Taste gehalten? Naeher ran?", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
