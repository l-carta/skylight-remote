#!/usr/bin/env python3
"""
Vollstaendiges, rein lesendes Inventar der Lampe: fragt so viel State ab, wie
ohne Firmware-Dump ueberhaupt geht -> Config-Server (DevKey), Health-Faults,
und ALLE Modell-States (inkl. Defaults/Ranges/Scheduler/Time). AENDERT NICHTS.

Zweck: "was traegt die Lampe in sich" komplett sichtbar machen. Ergaenzt
state_dump.py (nur die Kern-States) um Config, Health und die selten gelesenen
Detail-States.

Bridge vorher stoppen (Proxy exklusiv):
    sudo systemctl stop skylight-bridge
    python3 research/full_inventory.py
"""

import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen

# Vendor-Modell der Lampe (aus Composition): Element 0x0002, Company 0x0211
ELEM = 0x0002
CID = 0x0211
VMODEL = 0x0000

# --- Config-Server (mit DevKey) : (Name, GET-Op, Status-Op, Params) ---
CONFIG_GETS = [
    ("Default TTL",       0x800C, 0x800D, b""),
    ("Relay",             0x8026, 0x8027, b""),
    ("GATT Proxy",        0x8012, 0x8014, b""),
    ("Friend",            0x800F, 0x8011, b""),
    ("Beacon (SNB)",      0x8009, 0x800A, b""),
    ("Network Transmit",  0x8023, 0x8024, b""),
    ("Heartbeat Pub",     0x8038, 0x06,   b""),
    ("Heartbeat Sub",     0x803A, 0x803B, b""),
    ("NetKey List",       0x8042, 0x8043, b""),
    ("AppKey List(nk0)",  0x8001, 0x8003, b"\x00\x00"),
    ("Node Identity(nk0)",0x8046, 0x8048, b"\x00\x00"),
]

# Vendor-Modell-Bindings/Pub/Sub (elem + company + model, little-endian)
VM_PARAMS = ELEM.to_bytes(2, "little") + CID.to_bytes(2, "little") + VMODEL.to_bytes(2, "little")
CONFIG_MODEL_GETS = [
    ("Vendor AppBind list", 0x804D, 0x804E, VM_PARAMS),
    ("Vendor Sub list",     0x802B, 0x802C, VM_PARAMS),
    ("Vendor Publication",  0x8018, 0x8019, ELEM.to_bytes(2, "little") + CID.to_bytes(2, "little") + VMODEL.to_bytes(2, "little")),
]

# --- Health (mit AppKey) : Fault-Get fuer Company 0x0211 ---
HEALTH_GETS = [
    ("Health Fault(0x0211)", 0x8031, 0x05, CID.to_bytes(2, "little")),
]

# --- Alle Modell-States (mit AppKey) ---
SIG_GETS = [
    ("OnOff",             0x8201, 0x8204, b""),
    ("Level",             0x8205, 0x8208, b""),
    ("PowerOnUp",         0x8211, 0x8212, b""),
    ("DefaultTransTime",  0x820D, 0x820E, b""),
    ("Lightness",         0x824B, 0x824E, b""),
    ("Lightness-Linear",  0x824F, 0x8252, b""),
    ("Lightness-Last",    0x8253, 0x8254, b""),
    ("Lightness-Default", 0x8255, 0x8256, b""),
    ("Lightness-Range",   0x8257, 0x8258, b""),
    ("CTL",               0x825D, 0x8260, b""),
    ("CTL-Temp",          0x8261, 0x8266, b""),
    ("CTL-Temp-Range",    0x8262, 0x8263, b""),
    ("CTL-Default",       0x8267, 0x8268, b""),
    ("HSL",               0x826D, 0x8278, b""),
    ("HSL-Hue",           0x826E, 0x826F, b""),
    ("HSL-Sat",           0x8270, 0x8271, b""),
    ("HSL-Target",        0x8279, 0x827A, b""),
    ("HSL-Default",       0x827B, 0x827C, b""),
    ("HSL-Range",         0x827D, 0x827E, b""),
    ("Scene(cur)",        0x8241, 0x5E,   b""),
    ("SceneReg",          0x8244, 0x8245, b""),
    ("Scheduler(bitmap)", 0x8248, 0x8249, b""),
    ("Time",              0x8237, 0x5D,   b""),
]


async def get(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params=b""):
    await proxy.send_access(cfg, key, is_app, lamp, op, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.8):
        if r and (want is None or r[1][0] == want):
            return r[1]
    return None


async def run_block(title, proxy, cfg, key, is_app, lamp, iv, keys, gets):
    print(f"\n=== {title} ===", flush=True)
    for name, op, want, params in gets:
        try:
            r = await get(proxy, cfg, key, is_app, lamp, iv, keys, op, want, params)
        except Exception as e:
            print(f"  {name:<20} FEHLER: {e}", flush=True)
            continue
        if r:
            print(f"  {name:<20} 0x{r[0]:x} = {r[1].hex()}", flush=True)
        else:
            print(f"  {name:<20} (keine Antwort)", flush=True)


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
        await run_block("CONFIG-SERVER (DevKey)", proxy, cfg, dev, False, lamp, iv, keys, CONFIG_GETS)
        await run_block("CONFIG-MODELL Vendor 0x0211/0x0000", proxy, cfg, dev, False, lamp, iv, keys, CONFIG_MODEL_GETS)
        await run_block("HEALTH (AppKey)", proxy, cfg, app, True, lamp, iv, keys, HEALTH_GETS)
        await run_block("MODELL-STATES (AppKey)", proxy, cfg, app, True, lamp, iv, keys, SIG_GETS)
        save_cfg(CONFIG_FILE, cfg)

    print("\n# Fertig - vollstaendiges lesbares Inventar oben.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
