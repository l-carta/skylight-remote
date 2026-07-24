#!/usr/bin/env python3
"""
Finaler 'offene-Ecken'-Lauf. Deckt die letzten ungetesteten Wege ab:
  1) Generic Power Level Set (0x1006) - nie ge-Set-tet
  2) Vendor 0xC0 RC_KEY_REPORT mit Key-Codes 0x00..0x1F (Mode-Button-Emulation!)
  3) strukturierte Vendor-Payloads (GROUP/ATTR mit Index)
  4) Schreiben ins 0xFDA0-Service (fda6/fda8)

Phase A ueber Mesh-Proxy, Phase B ueber direkte GATT-Verbindung (0xFDA0).
Bridge muss gestoppt sein. Lampe beobachten!
"""

import asyncio

from bleak import BleakClient

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import listen, vendor_op

FDA6 = "0000fda6-0000-1000-8000-00805f9b12ea"
FDA8 = "0000fda8-0000-1000-8000-00805f9b12ea"


def tid(cfg):
    cfg["tid"] = (cfg["tid"] + 1) & 0xFF
    return cfg["tid"]


async def fire(proxy, cfg, app, lamp, iv, keys, label, op, params, wait=2.0):
    print(f">>> {label}", flush=True)
    await proxy.send_access(cfg, app, True, lamp, op, params)
    for _k, r in await listen(proxy, lamp, iv, keys, 1.1):
        if r:
            print(f"    <- 0x{r[1][0]:x} {r[1][1].hex()}", flush=True)
    await asyncio.sleep(wait)


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app, dev = bytes.fromhex(cfg["app_key"]), bytes.fromhex(cfg["dev_key"])
    lamp, iv, mac = cfg["unicast"], cfg["iv_index"], cfg["mac"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    # ---------- Phase A: Mesh ----------
    async with MeshProxy(mac, ctx, cfg["src"], log=lambda *_: None) as proxy:
        await fire(proxy, cfg, app, lamp, iv, keys, "AN baseline",
                   0x8202, bytes([0x00, tid(cfg)]))

        print("\n=== 1) Generic Power Level Set (0x8216) ===")
        await fire(proxy, cfg, app, lamp, iv, keys, "PowerLevel MAX",
                   0x8216, (0xFFFF).to_bytes(2, "little") + bytes([tid(cfg)]))
        await fire(proxy, cfg, app, lamp, iv, keys, "PowerLevel MIN",
                   0x8216, (0x0001).to_bytes(2, "little") + bytes([tid(cfg)]))
        await fire(proxy, cfg, app, lamp, iv, keys, "wieder AN",
                   0x8202, bytes([0x00, tid(cfg)]))

        print("\n=== 2) Vendor 0xC0 RC_KEY_REPORT: Key-Codes 0x00..0x1F ===")
        for code in range(0x00, 0x20):
            await fire(proxy, cfg, app, lamp, iv, keys,
                       f"RC_KEY code=0x{code:02x}", vendor_op(0xC0),
                       bytes([code]), wait=1.4)
        await fire(proxy, cfg, app, lamp, iv, keys, "wieder AN",
                   0x8202, bytes([0x00, tid(cfg)]))

        print("\n=== 3) Strukturierte Vendor-Payloads ===")
        for op, base, lab in [
            (0xC2, bytes([1]), "GROUP 0xC2 idx=1"),
            (0xC3, bytes([1]), "GROUP 0xC3 idx=1"),
            (0xC0, bytes([1, 1]), "RC_KEY [01,01] (Taste+Press)"),
            (0xC0, bytes([1, 0]), "RC_KEY [01,00] (Taste+Release)"),
            (0xD1, bytes([0, 0, 1]), "ATTR 0xD1 a0=1"),
            (0xD2, bytes([0, 0, 0xFF]), "ATTR 0xD2 a0=ff"),
        ]:
            await fire(proxy, cfg, app, lamp, iv, keys, lab, vendor_op(op), base)

        await proxy.send_access(cfg, app, True, lamp, 0x8202,
                                bytes([0x00, tid(cfg)]))
        save_cfg(CONFIG_FILE, cfg)

    # ---------- Phase B: 0xFDA0 direkt beschreiben ----------
    print("\n=== 4) 0xFDA0 schreiben (fda6/fda8) ===")
    await asyncio.sleep(3)
    client = None
    for attempt in range(4):
        try:
            client = BleakClient(mac, timeout=30)
            await client.connect()
            break
        except Exception as e:
            print(f"  connect Versuch {attempt + 1}: {e}")
            await asyncio.sleep(2)
    if client and client.is_connected:
        try:
            for uuid, name, vals in [(FDA6, "fda6", [b"\x00", b"\x01", b"\x02", b"\x03", b"\x04"]),
                                     (FDA8, "fda8", [b"\x00", b"\x02", b"\x03"])]:
                for v in vals:
                    print(f">>> write {name} = {v.hex()}", flush=True)
                    try:
                        await client.write_gatt_char(uuid, v, response=True)
                    except Exception as e:
                        print(f"    Fehler: {e}")
                    await asyncio.sleep(2)
            # fda8 auf Originalwert 0x01 zuruecksetzen
            try:
                await client.write_gatt_char(FDA8, b"\x01", response=True)
            except Exception:
                pass
        finally:
            await client.disconnect()
    else:
        print("  Konnte fuer 0xFDA0 nicht verbinden.")

    print("\n=== fertig. Bei WELCHEM Schritt hat die Lampe reagiert? ===")


if __name__ == "__main__":
    asyncio.run(main())
