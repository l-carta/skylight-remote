#!/usr/bin/env python3
"""
Provisionee-Angriff, PHASE 1 (De-Risk): Der Pi gibt sich als UNPROVISIONIERTE
Mesh-Lampe aus (advertised Mesh Provisioning Service 0x1827 + Device-UUID) und
loggt JEDES eingehende Provisioning-PDU.

Ziel: pruefen, ob die Original-Remote (beim ON-10s-Halten wirkt sie als
Provisioner) unser Fake-Geraet provisionieren WILL. Ein `Provisioning Invite
(0x00)` im Log heisst: sie beisst an -> gruenes Licht fuer die volle
Provisionee-Statemachine (Phase 2), die den Handshake zu Ende faehrt und den
Werks-NetKey im Klartext rauszieht.

Anders als imp_lamp.py (spoofte die BEKANNTE Lampe, bewarb 0x1828/Proxy) treten
wir hier als FRISCHES, unprovisioniertes Geraet auf -> die Remote soll den
Provisioning-Handshake starten, nicht fertig-verschluesselte Mesh-Daten senden.

Start (im bless-venv, root fuer BLE-Advertising); vorher Bridge stoppen:
    sudo systemctl stop skylight-bridge
    sudo ~/imp-venv/bin/python research/imp_prov.py [laufzeit_s]
"""

import asyncio
import re
import subprocess
import sys
from datetime import datetime

from bless import (BlessServer, BlessGATTCharacteristic,
                   GATTCharacteristicProperties as Props,
                   GATTAttributePermissions as Perm)
from bless.backends.bluezdbus.dbus.application import BlueZGattApplication


# bless' eigenes Advertising klemmt auf BlueZ 5.82 -> raus, wir bewerben via
# btmgmt (zuverlaessige Adv-Instanz mit exakten Bytes).
async def _skip(self, adapter):
    pass


BlueZGattApplication.start_advertising = _skip
BlueZGattApplication.stop_advertising = _skip

NAME = "BK_MESH_light"

# Mesh Provisioning Service + PB-GATT-Characteristics (SIG-Standard)
PROV_SVC = "00001827-0000-1000-8000-00805f9b34fb"
PROV_IN = "00002adb-0000-1000-8000-00805f9b34fb"    # Data In  (write-no-resp)
PROV_OUT = "00002adc-0000-1000-8000-00805f9b34fb"   # Data Out (notify)

# Telink-Skylight-Device-UUID-Muster: <7B Prefix><reversed MAC><3B Suffix>.
# Prefix/Suffix sind (beobachtete) Produktkonstanten; der MAC-Teil wird zur
# LAUFZEIT aus dem - evtl. gespooften - Adapter abgeleitet, damit die UUID zur
# tatsaechlich advertisenden Adresse passt (keine hartkodierten Geraete-IDs).
UUID_PREFIX = bytes.fromhex("0064b4692d0900")
UUID_SUFFIX = bytes.fromhex("000001")
OOB_INFO = b"\x00\x00"


def _adapter_mac():
    out = subprocess.run(["btmgmt", "info"], capture_output=True, text=True).stdout
    m = re.search(r"addr ([0-9A-Fa-f:]{17})", out)
    if not m:
        raise RuntimeError("Adapter-MAC nicht ermittelbar (btmgmt info)")
    return m.group(1)


def _dev_uuid():
    mac = bytes.fromhex(_adapter_mac().replace(":", ""))
    return UUID_PREFIX + mac[::-1] + UUID_SUFFIX


DEV_UUID = _dev_uuid()

PDU_NAMES = {0: "INVITE", 1: "CAPABILITIES", 2: "START", 3: "PUBLIC_KEY",
             4: "INPUT_COMPLETE", 5: "CONFIRMATION", 6: "RANDOM", 7: "DATA",
             8: "COMPLETE", 9: "FAILED"}


class Sar:
    """PB-GATT/Proxy-PDU-Reassembly: byte0 = (SAR<<6)|msgtype, Rest = payload."""

    def __init__(self):
        self.buf = b""

    def feed(self, frame):
        sar, payload = frame[0] >> 6, frame[1:]
        if sar == 0:                 # komplett
            return payload
        if sar == 1:                 # erstes Segment
            self.buf = payload
            return None
        self.buf += payload
        if sar == 3:                 # letztes Segment
            out, self.buf = self.buf, b""
            return out
        return None                  # mittleres Segment


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _adv_data_hex():
    # Flags + Complete-16bit-UUID(0x1827) + Service-Data(0x1827: DevUUID+OOB)
    sd = bytes([0x16, 0x27, 0x18]) + DEV_UUID + OOB_INFO
    sd_ad = bytes([len(sd)]) + sd
    flags = bytes([0x02, 0x01, 0x06])
    uuid_ad = bytes([0x03, 0x03, 0x27, 0x18])
    return (flags + uuid_ad + sd_ad).hex()


def set_adv(on):
    # bluetoothctl sendet Service-Data non-interaktiv nicht zuverlaessig ->
    # robust ueber btmgmt (verbindbare Adv-Instanz mit exakten Bytes).
    subprocess.run(["btmgmt", "rm-adv", "1"], capture_output=True, timeout=10)
    if not on:
        return "adv aus"
    r = subprocess.run(["btmgmt", "add-adv", "-c", "-d", _adv_data_hex(), "1"],
                       capture_output=True, text=True, timeout=10)
    return (r.stdout or "") + (r.stderr or "")


sar = Sar()
got = []


def on_write(characteristic, value, **kwargs):
    data = bytes(value)
    msgtype = data[0] & 0x3F if data else -1
    print(f"[{stamp()}] WRITE {characteristic.uuid[4:8]} raw={data.hex()} "
          f"(msgtype=0x{msgtype:02x})", flush=True)
    pdu = sar.feed(data)
    if pdu:
        ptype = pdu[0] & 0x3F
        name = PDU_NAMES.get(ptype, f"UNBEKANNT_0x{ptype:02x}")
        print(f"    >>> Provisioning-PDU: {name}  payload={pdu[1:].hex()}",
              flush=True)
        got.append(name)
    characteristic.value = value


def on_read(characteristic, **kwargs):
    return characteristic.value


async def main():
    runtime = int(sys.argv[1]) if len(sys.argv) > 1 else 90

    server = BlessServer(name=NAME)
    server.read_request_func = on_read
    server.write_request_func = on_write

    await server.add_new_service(PROV_SVC)
    await server.add_new_characteristic(
        PROV_SVC, PROV_IN, Props.write | Props.write_without_response, b"",
        Perm.writeable)
    await server.add_new_characteristic(
        PROV_SVC, PROV_OUT, Props.notify, b"", Perm.readable)

    await server.start()
    adv_out = set_adv(True)
    if "added" not in adv_out.lower():
        print(f"# WARNUNG btmgmt-Advertising evtl. NICHT aktiv:\n{adv_out}",
              flush=True)
    else:
        print(f"# btmgmt-Adv aktiv ({adv_out.strip()}), "
              f"data={_adv_data_hex()}", flush=True)
    print(f"# Fake-UNPROVISIONED-Lampe '{NAME}' laeuft {runtime}s "
          f"(advertised: Mesh Provisioning 0x1827)", flush=True)
    print(f"# Device-UUID = {DEV_UUID.hex()}", flush=True)
    print("# ===> JETZT an der Remote ON 10s halten (mehrfach). "
          "Warte auf INVITE ...", flush=True)
    try:
        await asyncio.sleep(runtime)
    finally:
        set_adv(False)
        await server.stop()

    print(f"\n# === {len(got)} Provisioning-PDU(s) empfangen ===", flush=True)
    if "INVITE" in got:
        print("# >>> INVITE ERHALTEN! Die Remote will uns provisionieren.\n"
              "#     -> gruenes Licht fuer Phase 2 (volle Provisionee-"
              "Statemachine -> Werks-NetKey).", flush=True)
    elif got:
        print("# PDUs kamen an, aber kein INVITE - Log oben pruefen.", flush=True)
    else:
        print("# Nichts empfangen. Moegliche Gruende: Remote verbindet nicht /\n"
              "#     akzeptiert nur bekannte Geraete / Adv-Format (Service-Data)\n"
              "#     noch nicht passend. Naechster Schritt danach abstimmen.",
              flush=True)


if __name__ == "__main__":
    asyncio.run(main())
