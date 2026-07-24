#!/usr/bin/env python3
"""
Modes = SIG-Szenen? app_key an Scene Server (0x1203) binden, gespeicherte
Szenen auflisten (Scene Register Get) und optional per Scene Recall umschalten.

    python3 scene_probe.py                 # bind + Szenen auflisten
    python3 scene_probe.py recall 1        # Szene 1 aufrufen (Lampe beobachten)
    python3 scene_probe.py sweep 1 8       # Szenen 1..8 durchschalten

Bridge muss gestoppt sein.
"""

# --- Pfad-Bootstrap: dieses Tool liegt in research/, der Stack + die
# Config (skylight-mesh.json) liegen im Repo-Root eine Ebene hoeher. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen                 # ASZMIC-faehiger Empfang

OP_BIND, OP_BIND_STATUS = 0x803D, 0x803E
SCENE_SERVER = 0x1203
OP_SCENE_GET, OP_SCENE_STATUS = 0x8241, 0x5E
OP_SCENE_RECALL = 0x8242
OP_SCENE_REG_GET, OP_SCENE_REG_STATUS = 0x8244, 0x8245


def decoded(results):
    out = []
    for _kind, r in results:
        if r:
            _kn, (op, pa) = r
            out.append((op, pa))
    return out


async def bind_scene(proxy, cfg, dev, lamp, iv, keys):
    params = (lamp.to_bytes(2, "little") + (0).to_bytes(2, "little")
              + SCENE_SERVER.to_bytes(2, "little"))
    for _ in range(3):
        await proxy.send_access(cfg, dev, False, lamp, OP_BIND, params)
        for op, pa in decoded(await listen(proxy, lamp, iv, keys, 3.0)):
            if op == OP_BIND_STATUS:
                print(f"  bind Scene 0x1203: {pa.hex()} "
                      f"({'OK' if pa and pa[0] == 0 else 'FEHLER '+hex(pa[0])})")
                return
    print("  keine Bind-Bestaetigung")


async def next_tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app, dev = bytes.fromhex(cfg["app_key"]), bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("Bind app_key -> Scene Server ...")
        await bind_scene(proxy, cfg, dev, lamp, iv, keys)

        if mode in ("list", "recall", "sweep"):
            print("\nScene Register Get (welche Szenen sind gespeichert?) ...")
            await proxy.send_access(cfg, app, True, lamp, OP_SCENE_REG_GET, b"")
            for op, pa in decoded(await listen(proxy, lamp, iv, keys, 3.0)):
                print(f"  antwort opcode=0x{op:x} params={pa.hex()}")
                if op == OP_SCENE_REG_STATUS and len(pa) >= 3:
                    status, cur = pa[0], int.from_bytes(pa[1:3], "little")
                    scenes = [int.from_bytes(pa[i:i + 2], "little")
                              for i in range(3, len(pa) - 1, 2)]
                    print(f"  >>> status={status} aktuelle_Szene={cur} "
                          f"GESPEICHERTE SZENEN (=Modes?): {scenes}")

        if mode == "recall":
            n = int(sys.argv[2])
            print(f"\nScene Recall {n} - LAMPE BEOBACHTEN ...")
            await proxy.send_access(cfg, app, True, lamp, OP_SCENE_RECALL,
                                    n.to_bytes(2, "little") + bytes([await next_tid(cfg)]))
            for op, pa in decoded(await listen(proxy, lamp, iv, keys, 3.0)):
                print(f"  antwort opcode=0x{op:x} params={pa.hex()}")

        if mode == "sweep":
            a, b = int(sys.argv[2]), int(sys.argv[3])
            print(f"\nSweep Scene Recall {a}..{b} im ~4s-Takt - LAMPE BEOBACHTEN")
            for n in range(a, b + 1):
                print(f"[Szene {n}]", flush=True)
                await proxy.send_access(cfg, app, True, lamp, OP_SCENE_RECALL,
                                        n.to_bytes(2, "little") + bytes([await next_tid(cfg)]))
                await asyncio.sleep(4)

        save_cfg(CONFIG_FILE, cfg)


if __name__ == "__main__":
    asyncio.run(main())
