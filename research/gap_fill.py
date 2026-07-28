#!/usr/bin/env python3
"""Nachlade-Lauf: holt die im full_inventory.py per Paketverlust ausgefallenen
GETs mit Retries; korrigierte Scheduler-Opcodes; liest bei belegten Scheduler-
Slots die Action-Eintraege (potenziell 'Day Rhythm'). Rein lesend."""

import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen

# (Name, op, status, params, is_app)
GETS = [
    ("Default TTL",       0x800C, 0x800D, b"", False),
    ("Relay",             0x8026, 0x8027, b"", False),
    ("Friend",            0x800F, 0x8011, b"", False),
    ("Beacon (SNB)",      0x8009, 0x800A, b"", False),
    ("Network Transmit",  0x8023, 0x8024, b"", False),
    ("Heartbeat Sub",     0x803A, 0x803B, b"", False),
    ("AppKey List(nk0)",  0x8001, 0x8003, b"\x00\x00", False),
    ("DefaultTransTime",  0x820D, 0x820E, b"", True),
    ("HSL-Hue",           0x826E, 0x826F, b"", True),
    ("HSL-Sat",           0x8270, 0x8271, b"", True),
    ("Scheduler(bitmap)", 0x8249, 0x824A, b"", True),   # korrigiert
]


async def get_once(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params):
    await proxy.send_access(cfg, key, is_app, lamp, op, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.8):
        if r and (want is None or r[1][0] == want):
            return r[1]
    return None


async def get_retry(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params, tries=4):
    for _ in range(tries):
        r = await get_once(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params)
        if r:
            return r
    return None


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        print("=== NACHLADEN (mit Retry) ===", flush=True)
        sched_bitmap = None
        for name, op, want, params, is_app in GETS:
            key = app if is_app else dev
            r = await get_retry(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params)
            if r:
                print(f"  {name:<20} 0x{r[0]:x} = {r[1].hex()}", flush=True)
                if name.startswith("Scheduler"):
                    sched_bitmap = int.from_bytes(r[1][:2], "little")
            else:
                print(f"  {name:<20} (weiter keine Antwort)", flush=True)

        # Scheduler-Action-Eintraege lesen, wenn Slots belegt
        if sched_bitmap:
            print(f"\n=== SCHEDULER-Eintraege (bitmap=0x{sched_bitmap:04x}) ===", flush=True)
            for idx in range(16):
                if sched_bitmap & (1 << idx):
                    r = await get_retry(proxy, cfg, app, True, lamp, iv, keys,
                                        0x8248, 0x5F, bytes([idx]))
                    print(f"  Slot {idx:>2}: {r[1].hex() if r else '(keine Antwort)'}", flush=True)
        else:
            print("\n# Scheduler: keine belegten Slots (kein 'Day Rhythm' als Zeitplan).", flush=True)

        save_cfg(CONFIG_FILE, cfg)
    print("\n# Nachladen fertig.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
