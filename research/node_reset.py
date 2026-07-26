#!/usr/bin/env python3
"""
Config Node Reset (0x8049): nimmt die Lampe aus UNSEREM Mesh - sie verwirft
NetKey/AppKey/DevKey und geht zurueck in den unprovisionierten Zustand
(advertised danach wieder 0x1827, also fuer die Remote koppelbar).

Wiederherstellbar: mit provision.py jederzeit neu ins eigene Netz aufnehmen.

Voraussetzung: Lampe am Strom + erreichbar, Bridge gestoppt.

    sudo systemctl stop skylight-bridge
    python3 research/node_reset.py
"""

import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy

OP_NODE_RESET = 0x8049
OP_NODE_RESET_STATUS = 0x804A


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp = cfg["unicast"]

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("# Sende Config Node Reset (0x8049) an die Lampe ...", flush=True)
        got = False
        for i in range(3):
            await proxy.send_access(cfg, dev, False, lamp, OP_NODE_RESET, b"")
            try:
                await proxy.wait_status(dev, False, OP_NODE_RESET_STATUS,
                                        timeout=4.0)
                got = True
                break
            except TimeoutError:
                print(f"#   Versuch {i + 1}: keine Status-Antwort", flush=True)
        save_cfg(CONFIG_FILE, cfg)

    if got:
        print("# >>> Node Reset bestaetigt. Die Lampe ist jetzt UNPROVISIONIERT.",
              flush=True)
    else:
        print("# Keine Bestaetigung - die Lampe resettet oft, OHNE noch zu "
              "antworten (sie ist beim Status-Senden schon aus dem Netz). "
              "Mit scan.py pruefen, ob sie wieder 0x1827 advertised.", flush=True)
    print("# Danach: an der Remote ON 10s halten (koppeln). Zum Zurueckholen "
          "in unser Netz: provision.py.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
