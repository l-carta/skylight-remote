#!/usr/bin/env python3
"""
Vendor-Attribut-Probe v2 - mit der KORREKTEN Payload-Struktur aus dem
Telink-SIG-Mesh-SDK (Ai-Thinker-Open/Telink_SIG_Mesh, RGBCW_Ali_Mesh).

Erkenntnis aus dem SDK (vendor_model.h):
  Opcodes (Company 0x0211):  0xD0 ATTR_GET, 0xD1 ATTR_SET,
                             0xD2 ATTR_SET_NACK, 0xD3 ATTR_STATUS
  Payload GET:  [tid][attr_type 2B LE]
  Payload SET:  [tid][attr_type 2B LE][attr_par ...]
  Modelle:      0x0000 = LIGHT_S (Server), 0x0001 = LIGHT_C (Client)
                -> exakt die zwei Vendor-Modelle unserer Lampe.
  Attribut-IDs: ATTR_ONOFF=0x0100, ATTR_TARGET_TEMP=0x010c,
                ATTR_CURRENT_TEMP=0x010d, ATTR_SCENE_MODE=0xf004,
                ATTR_WORKING_STATUS=0xf001, ATTR_VERSION=0xff01, ...

Fruehere Probes liessen den tid weg + rieten die IDs -> jede Payload malformed.

PHASE A (nicht-destruktiv, entscheidend): ATTR_GET fuer Kandidat-IDs, lauscht
auf ATTR_STATUS (0xD3). SCHON EIN Fehlercode (0x81 not-supported) beweist, dass
der Attribut-Modus lebt -> ab da enumerieren wir.

PHASE B (LAMPE BEOBACHTEN): ATTR_SET fuer SCENE_MODE 0..7, ONOFF, Helligkeit.

    sudo systemctl stop skylight-bridge
    python3 research/vendor_attr2.py          # A + B
    python3 research/vendor_attr2.py a        # nur GET-Hunt (unsichtbar)
    sudo systemctl start skylight-bridge
"""

# --- Pfad-Bootstrap ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import vendor_op, listen

ATTR_GET, ATTR_SET, ATTR_STS = 0xD0, 0xD1, 0xD3

# (Name, attr_type) - Kandidaten aus dem SDK + Ali-Standard-Guesses
ATTRS = [
    ("ONOFF",         0x0100),
    ("TARGET_TEMP",   0x010c),
    ("CURRENT_TEMP",  0x010d),
    ("SCENE_MODE",    0xf004),
    ("WORKING_STATUS", 0xf001),
    ("EVENT",         0xf009),
    ("ELEMENT_NUM",   0xf00c),
    ("VERSION",       0xff01),
    ("DEVICE_FEATURE", 0xff02),
    ("ALI_LUM?",      0x0121),
    ("ALI_TEMP?",     0x0122),
    ("ALI_COLOR?",    0x0123),
]


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


def le2(v):
    return v.to_bytes(2, "little")


async def attr_get(proxy, cfg, app, lamp, iv, keys, attr):
    payload = bytes([tid(cfg)]) + le2(attr)
    await proxy.send_access(cfg, app, True, lamp, vendor_op(ATTR_GET), payload)
    out = []
    for kind, r in await listen(proxy, lamp, iv, keys, 1.2):
        if r:
            out.append((kind, r[0], r[1][0], r[1][1]))
    return out


async def attr_set(proxy, cfg, app, lamp, iv, keys, attr, value: bytes):
    payload = bytes([tid(cfg)]) + le2(attr) + value
    await proxy.send_access(cfg, app, True, lamp, vendor_op(ATTR_SET), payload)
    out = []
    for kind, r in await listen(proxy, lamp, iv, keys, 1.0):
        if r:
            out.append((kind, r[0], r[1][0], r[1][1]))
    return out


async def onoff_sig(proxy, cfg, app, lamp, on):
    await proxy.send_access(cfg, app, True, lamp, 0x8202,
                            bytes([0x00 if on else 0x01, tid(cfg)]))


async def phase_a(proxy, cfg, app, lamp, iv, keys):
    print("\n=== PHASE A: ATTR_GET (korrekte Struktur) -> Suche ATTR_STATUS 0xD3 ===",
          flush=True)
    live = []
    for name, attr in ATTRS:
        hits = await attr_get(proxy, cfg, app, lamp, iv, keys, attr)
        # Retry gegen Paketverlust
        if not hits:
            hits = await attr_get(proxy, cfg, app, lamp, iv, keys, attr)
        for kind, kname, rop, rparams in hits:
            marker = "  <<< ATTR-MODUS LEBT!" if rop == ATTR_STS else ""
            print(f"  {name:<14} 0x{attr:04x} -> [{kind}/{kname}] "
                  f"reply_op=0x{rop:x} params={rparams.hex()}{marker}", flush=True)
            live.append((name, attr, rop, rparams))
        if not hits:
            print(f"  {name:<14} 0x{attr:04x} -> (keine Antwort)", flush=True)
    return live


async def phase_b(proxy, cfg, app, lamp, iv, keys):
    print("\n=== PHASE B: ATTR_SET -- LAMPE BEOBACHTEN ===", flush=True)
    await onoff_sig(proxy, cfg, app, lamp, True)
    await asyncio.sleep(2)

    print("\n--- SCENE_MODE (0xf004) = 0..7 ---", flush=True)
    for m in range(8):
        r = await attr_set(proxy, cfg, app, lamp, iv, keys, 0xf004, bytes([m]))
        rs = r[0] if r else None
        print(f"  [mode={m}] {'reply 0x%x %s' % (rs[2], rs[3].hex()) if rs else '(keine Antwort)'}",
              flush=True)
        await asyncio.sleep(2.5)

    print("\n--- ONOFF (0x0100) aus/an ---", flush=True)
    for name, v in (("AUS", 0x00), ("AN", 0x01)):
        await attr_set(proxy, cfg, app, lamp, iv, keys, 0x0100, bytes([v]))
        print(f"  onoff={name}", flush=True)
        await asyncio.sleep(2.5)

    print("\n--- Helligkeit-Kandidaten dunkel/hell ---", flush=True)
    for attr in (0x0121, 0x0300, 0x0122):
        for lab, val in (("DUNKEL", b"\x05"), ("HELL", b"\xff")):
            await attr_set(proxy, cfg, app, lamp, iv, keys, attr, val)
            print(f"  attr=0x{attr:04x} {lab}", flush=True)
            await asyncio.sleep(2.0)
        await onoff_sig(proxy, cfg, app, lamp, True)
        await asyncio.sleep(1.0)


async def main():
    only = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv = cfg["unicast"], cfg["iv_index"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    live = []
    try:
        async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=lambda *_: None) as proxy:
            if only != "b":
                live = await phase_a(proxy, cfg, app, lamp, iv, keys)
            if only != "a":
                await phase_b(proxy, cfg, app, lamp, iv, keys)
            await onoff_sig(proxy, cfg, app, lamp, True)
    finally:
        save_cfg(CONFIG_FILE, cfg)

    print("\n=== FAZIT ===")
    if any(rop == ATTR_STS for _, _, rop, _ in live):
        print("ATTR_STATUS (0xD3) empfangen -> Attribut-Modus ist AKTIV.")
        print("Damit koennen wir Attribute gezielt lesen/schreiben.")
    elif live:
        print("Vendor-Antwort erhalten (nicht 0xD3) -> Details oben.")
    else:
        print("Keine Antwort auf ATTR_GET. Entweder DEFAULT-Modus (dann laufen")
        print("Modes ueber VD_RC_KEY_REPORT 0xC0) oder Modell antwortet nicht.")


if __name__ == "__main__":
    asyncio.run(main())
