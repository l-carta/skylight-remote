#!/usr/bin/env python3
"""
Vendor-Opcode-Sweep: schickt der Lampe alle Vendor-Opcodes eines Bereichs mit
Kontrast-Payloads und laesst DICH die Reaktion beobachten.

Pro Opcode: erst 0x00 (Dunkel-Puls), dann 0xff (Hell). Beim RICHTIGEN
Helligkeits-Opcode blinkt die Lampe sichtbar dunkel->hell. Beim richtigen
Farb-Opcode aendert sich die Farbe. Bei allen anderen: nichts.

    # Bereich (hex) und Payload-Breite in Byte:
    python3 vendor_sweep.py C0 FF 1      # alle 64, 1-Byte-Payload
    python3 vendor_sweep.py D0 D5 2      # eng, 2-Byte-Payload
    python3 vendor_sweep.py D2 D2 1 raw 00ff  # ein Opcode, eigene Payloads

Bridge muss gestoppt sein.
"""

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy

COMPANY = 0x0211


def vop(b):
    return (b << 16) | COMPANY


async def send(proxy, cfg, key, op_byte, params):
    await proxy.send_access(cfg, key, True, cfg["unicast"], vop(op_byte), params)


async def onoff(proxy, cfg, key, on):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    # Firmware-Quirk: invertiert (0x00=AN)
    await proxy.send_access(cfg, key, True, cfg["unicast"], 0x8202,
                            bytes([0x00 if on else 0x01, cfg["tid"]]))


async def main():
    start = int(sys.argv[1], 16)
    end = int(sys.argv[2], 16)
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    custom = None
    if len(sys.argv) > 5 and sys.argv[4] == "raw":
        # eigene Payloads, kommagetrennt-hex nach 'raw': z.B. 00ff,0064
        custom = [bytes.fromhex(x) for x in sys.argv[5].split(",")]

    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("Lampe AN, Baseline hell ...", flush=True)
        await onoff(proxy, cfg, app, True)
        await asyncio.sleep(2)

        lo, hi = b"\x00" * width, b"\xff" * width
        n = end - start + 1
        print(f"=== Sweep 0x{start:02x}..0x{end:02x} ({n} Opcodes, {width}B) - "
              f"auf ein BLINKEN oder Farb-/Helligkeitswechsel achten ===",
              flush=True)
        for idx, op in enumerate(range(start, end + 1)):
            secs = idx * 2  # grobe Zeitmarke fuer die Korrelation
            print(f"[t~{secs:>3}s | #{idx:>2}] opcode 0x{op:02x}", flush=True)
            payloads = custom if custom else [lo, hi]
            for p in payloads:
                await send(proxy, cfg, app, op, p)
                await asyncio.sleep(1.0)

        # sauber hell/an lassen
        await onoff(proxy, cfg, app, True)
        save_cfg(CONFIG_FILE, cfg)
        print("\n=== Sweep fertig. WANN/bei welcher #-Nummer hat die Lampe "
              "reagiert (Blinken/Helligkeit/Farbe)? ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
