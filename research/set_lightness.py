#!/usr/bin/env python3
"""
Sendet EIN Light-Lightness-Set (0x824C) mit gegebenem 16-Bit-Wert und gibt den
Status zurueck. Fuer den objektiven Lux-Test (aendert die Lampe -> danach Lux
ueber HA messen, ob die LED wirklich reagiert).

    sudo systemctl stop skylight-bridge
    python3 research/set_lightness.py <hex4>   # z.B. 4000 (25%) oder ffff (voll)
    sudo systemctl start skylight-bridge
"""

import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen

OP_LIGHTNESS_SET = 0x824C


async def main():
    val = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0xFFFF
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    params = val.to_bytes(2, "little") + bytes([cfg["tid"]])
    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print(f"Lightness Set -> 0x{val:04x}", flush=True)
        await proxy.send_access(cfg, app, True, lamp, OP_LIGHTNESS_SET, params)
        for _k, r in await listen(proxy, lamp, iv, keys, 1.8):
            if r:
                print(f"  <- status 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
        save_cfg(CONFIG_FILE, cfg)


if __name__ == "__main__":
    asyncio.run(main())
