#!/usr/bin/env python3
"""Schritt 2/3 - Skylight frisch vom Pi provisionieren und konfigurieren.

Erzeugt ein neues Mesh-Netz (NetKey/AppKey), nimmt die Lampe per PB-GATT auf,
bindet den AppKey an die Licht-Modelle und speichert alles in
skylight-mesh.json (geheim, gitignored).

Aufruf:  python3 provision.py [MAC-Adresse]
"""

import asyncio
import json
import os
import sys

from bleak import BleakClient, BleakScanner

from meshlib import network
from meshlib.provisioner import provision, PROV_SERVICE

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "skylight-mesh.json")

SRC_ADDR = 0x0001          # wir (Provisioner/Bridge)
LAMP_ADDR = 0x0002         # die Skylight

MODELS = {                 # SIG-Model-IDs, an die der AppKey gebunden wird
    0x1000: "Generic OnOff Server",
    0x1300: "Light Lightness Server",
    0x1303: "Light CTL Server",
    0x1307: "Light HSL Server",
}

OP_APPKEY_ADD = 0x00
OP_APPKEY_STATUS = 0x8003
OP_MODEL_APP_BIND = 0x803D
OP_MODEL_APP_STATUS = 0x803E


async def find_lamp() -> str:
    print("Suche unprovisionierte Lampe (Service 0x1827) ...")
    found = await BleakScanner.discover(timeout=15.0, return_adv=True)
    for addr, (dev, adv) in found.items():
        uuids = [u[4:8] for u in (adv.service_data or {})]
        if "1827" in uuids:
            name = dev.name or adv.local_name or "?"
            print(f"  gefunden: {addr} ({name}, RSSI {adv.rssi})")
            return addr
    raise SystemExit("Keine unprovisionierte Mesh-Lampe gefunden. "
                     "Lampe an? Reset ok? Reichweite?")


def save(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


async def main():
    cfg = None
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        if cfg.get("configured"):
            raise SystemExit(f"{CONFIG_FILE} existiert und ist fertig "
                             "konfiguriert. Datei loeschen fuer Neuanfang.")
        print("Unfertige Konfiguration gefunden - ueberspringe Provisioning, "
              "mache mit AppKey/Bind weiter.")

    if cfg is None:
        mac = sys.argv[1] if len(sys.argv) > 1 else await find_lamp()
        net_key, app_key = os.urandom(16), os.urandom(16)
        iv_index = 0

        print(f"Verbinde mit {mac} zum Provisionieren ...")
        client = BleakClient(mac, timeout=30)
        await client.connect()
        dev_key = await provision(client, net_key, 0, iv_index, LAMP_ADDR)

        # SOFORT speichern - die Lampe trennt nach Complete selbststaendig,
        # der Disconnect darf uns die Keys nicht mehr kosten.
        cfg = {
            "mac": mac,
            "net_key": net_key.hex(), "app_key": app_key.hex(),
            "dev_key": dev_key.hex(),
            "unicast": LAMP_ADDR, "src": SRC_ADDR,
            "iv_index": iv_index, "seq": 0, "tid": 0,
            "configured": False,
        }
        save(cfg)
        print(f"Keys gespeichert: {CONFIG_FILE}")
        try:
            await client.disconnect()
        except Exception:
            pass

    net_key = bytes.fromhex(cfg["net_key"])
    app_key = bytes.fromhex(cfg["app_key"])
    dev_key = bytes.fromhex(cfg["dev_key"])
    iv_index = cfg["iv_index"]
    mac = cfg["mac"]

    print("Warte 5 s, bis die Lampe als Proxy neu startet ...")
    await asyncio.sleep(5)

    from meshlib.proxy import MeshProxy
    ctx = network.NetContext(net_key, iv_index)
    seq_state = cfg

    async with MeshProxy(mac, ctx, SRC_ADDR) as proxy:
        print("AppKey an Lampe uebertragen ...")
        await proxy.send_access(seq_state, dev_key, False, LAMP_ADDR,
                                OP_APPKEY_ADD, b"\x00\x00\x00" + app_key)
        status = await proxy.wait_status(dev_key, False, OP_APPKEY_STATUS)
        if status[0] != 0:
            raise SystemExit(f"AppKey Add fehlgeschlagen: 0x{status[0]:02x}")
        print("  AppKey ok")

        for model_id, name in MODELS.items():
            params = (LAMP_ADDR.to_bytes(2, "little") + b"\x00\x00"
                      + model_id.to_bytes(2, "little"))
            await proxy.send_access(seq_state, dev_key, False, LAMP_ADDR,
                                    OP_MODEL_APP_BIND, params)
            status = await proxy.wait_status(dev_key, False,
                                             OP_MODEL_APP_STATUS)
            ok = "ok" if status[0] == 0 else f"FEHLER 0x{status[0]:02x}"
            print(f"  Bind {name}: {ok}")

    cfg["configured"] = True
    save(cfg)
    print("\nFertig! Test: python3 lamp.py on")


if __name__ == "__main__":
    asyncio.run(main())
