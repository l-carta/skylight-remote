#!/usr/bin/env python3
"""
Sendet EINE Access-Message an die Lampe - mit Verzoegerung nach dem Verbinden,
damit ein Logic-Analyzer/Oszi am Debug-UART dazwischen scharfgeschaltet werden
kann ("READY" wird ausgegeben, sobald der Proxy steht).

    sudo systemctl stop skylight-bridge
    python3 research/probe_send.py <opcode-hex> <params-hex> [delay_s] [app|dev]
    sudo systemctl start skylight-bridge

Das Token TID in den Params wird durch die naechste Transaction-ID ersetzt.
Env SRC ueberschreibt die Quelladresse; Vendor-Befehle muessen von der
Remote-Adresse kommen:
    SRC=0x7fff python3 research/probe_send.py c90211 TID04000300000000   # Focus
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


async def main():
    opcode = int(sys.argv[1], 16)
    params_s = sys.argv[2] if len(sys.argv) > 2 else ""
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    use_dev = len(sys.argv) > 4 and sys.argv[4] == "dev"
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    params = bytes.fromhex(params_s.replace("TID", f"{cfg['tid']:02x}"))
    src = int(_os.environ.get("SRC", str(cfg["src"])), 0)
    async with MeshProxy(cfg["mac"], ctx, src, log=lambda *_: None) as proxy:
        print("READY", flush=True)
        await asyncio.sleep(delay)
        print(f"send op 0x{opcode:x} params {params.hex()}", flush=True)
        await proxy.send_access(cfg, dev if use_dev else app, not use_dev, lamp, opcode, params)
        try:
            for _k, r in await asyncio.wait_for(listen(proxy, lamp, iv, keys, 1.5), 5):
                if r:
                    print(f"  <- status 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
        except asyncio.TimeoutError:
            print("  (no reply)", flush=True)
        save_cfg(CONFIG_FILE, cfg)


if __name__ == "__main__":
    asyncio.run(main())
