#!/usr/bin/env python3
"""
Hypothese (von dir): Die Modes laufen ueber den On/Off-Pfad - der EINZIGE
SIG-Befehl, der den LED-Treiber wirklich ansteuert. Vielleicht ist das
OnOff-Byte ein MODE-Selektor: 0x00=an, 0x01=aus, 0x02+=an mit Mode 1,2,3...

Tests (Lampe beobachten):
  A) OnOff-Set mit Byte 0x00,0x02..0x0A  (Mode-als-Wert?)
  B) Wiederholtes AN mit neuem TID       (durchtippen -> Mode-Zyklus?)
  C) AN mit Transition/Delay-Bytes        (Mode in Extra-Feldern?)

Bridge muss gestoppt sein.
"""

import asyncio

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen

OP_ONOFF_SET = 0x8202


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def fire(proxy, cfg, app, lamp, iv, keys, label, params):
    print(f">>> {label}: params={params.hex()}", flush=True)
    await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.5):
        if r:
            print(f"    <- 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
    await asyncio.sleep(3)


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("Lampe AN (0x00) ...")
        await fire(proxy, cfg, app, lamp, iv, keys, "AN baseline", bytes([0x00, tid(cfg)]))

        print("\n=== A) OnOff-Byte als Mode-Selektor (0x02..0x0A) ===")
        for w in [0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A]:
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"OnOff wire=0x{w:02x}", bytes([w, tid(cfg)]))
            # zwischendurch sicher wieder AN, falls ein Wert = AUS wirkte
            await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                    bytes([0x00, tid(cfg)]))
            await asyncio.sleep(0.5)

        print("\n=== B) Wiederholtes AN (durchtippen -> Mode-Zyklus?) ===")
        for i in range(6):
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"AN re-press #{i + 1}", bytes([0x00, tid(cfg)]))

        print("\n=== C) AN mit Transition/Delay-Bytes (Mode im Extra-Feld?) ===")
        for t in [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]:
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"AN transition=0x{t:02x}", bytes([0x00, tid(cfg), t, 0x00]))

        await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                bytes([0x00, tid(cfg)]))
        save_cfg(CONFIG_FILE, cfg)
        print("\n=== fertig. Bei WELCHEM Label hat sich die Lampe veraendert? ===")


if __name__ == "__main__":
    asyncio.run(main())
