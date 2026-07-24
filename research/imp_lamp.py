#!/usr/bin/env python3
"""
Fake-Lampe: Pi gibt sich als 'BK_MESH_light' aus, damit die Fernbedienung sich
mit UNS statt der echten Lampe verbindet - und wir jeden Write mitloggen.

So bekommen wir die ECHTEN Bytes, die die Remote pro Button schickt (Modes,
Dimmen) - Ground Truth, ohne Sniffer-Hardware.

Repliziert die Services der echten Lampe (aus gatt_enum/fda0_probe):
  0xFDA0 (custom base ...9b12ea): fda4[r] fda6[r/w] fda7[r/w] fda8[r/w]
  0x1828 SIG Mesh Proxy: 2add[write-no-resp] 2ade[notify]
fda7/fda8 werden mit den echten Werten der Lampe vorbelegt, damit die Remote
uns fuer echt haelt.

Ablauf (vom Orchestrator gesetzt):
  1. echte Lampe stromlos, Bridge gestoppt
  2. Pi-MAC auf Lampen-MAC gespooft (btmgmt public-addr)
  3. dieses Skript starten -> advertised -> Remote druecken

    sudo ~/imp-venv/bin/python imp_lamp.py [laufzeit_s]
"""

import asyncio
import subprocess
import sys
from datetime import datetime

from bless import (BlessServer, BlessGATTCharacteristic,
                   GATTCharacteristicProperties as Props,
                   GATTAttributePermissions as Perm)

# bless' eigenes LE-Advertisement lehnt BlueZ 5.82 ab ("Failed to register
# advertisement"). bluetoothctl-Advertising funktioniert dagegen. Also patchen
# wir bless' Advertising raus (GATT-Server bleibt) und bewerben separat.
from bless.backends.bluezdbus.dbus.application import BlueZGattApplication


async def _skip_adv(self, adapter):
    print("# (bless-Advertising uebersprungen)", flush=True)


async def _skip_stop_adv(self, adapter):
    pass


BlueZGattApplication.start_advertising = _skip_adv
BlueZGattApplication.stop_advertising = _skip_stop_adv

NAME = "BK_MESH_light"


def set_advertising(on: bool):
    if on:
        script = ("menu advertise\nname BK_MESH_light\nuuids 0x1828\n"
                  "back\nadvertise on\n")
    else:
        script = "advertise off\n"
    subprocess.run(["bluetoothctl"], input=script, text=True,
                   capture_output=True, timeout=15)

FDA0 = "0000fda0-0000-1000-8000-00805f9b12ea"
FDA4 = "0000fda4-0000-1000-8000-00805f9b12ea"
FDA6 = "0000fda6-0000-1000-8000-00805f9b12ea"
FDA7 = "0000fda7-0000-1000-8000-00805f9b12ea"
FDA8 = "0000fda8-0000-1000-8000-00805f9b12ea"

PROXY = "00001828-0000-1000-8000-00805f9b34fb"
PROXY_IN = "00002add-0000-1000-8000-00805f9b34fb"    # write-without-response
PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"   # notify

# Initial-Werte der 0xFDA0-Chars. Fuer maximale Echtheit hier die mit
# fda0_probe.py ausgelesenen Werte DEINER Lampe eintragen (fda7/fda8 sind
# geraetespezifisch, daher nicht im Repo hardcodiert).
INIT = {
    FDA4: b"",
    FDA6: b"",
    FDA7: b"",       # z.B. bytes.fromhex("....") aus fda0_probe.py
    FDA8: bytes.fromhex("01"),
}

log_lines = []


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def on_read(characteristic: BlessGATTCharacteristic, **kwargs):
    print(f"[{stamp()}] READ  {characteristic.uuid[4:8]} -> "
          f"{bytes(characteristic.value).hex() or '-'}", flush=True)
    return characteristic.value


def on_write(characteristic: BlessGATTCharacteristic, value, **kwargs):
    hx = bytes(value).hex()
    line = f"[{stamp()}] WRITE {characteristic.uuid[4:8]} = {hx}"
    print(line, flush=True)
    log_lines.append(line)
    characteristic.value = value


async def main():
    runtime = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    server = BlessServer(name=NAME)
    server.read_request_func = on_read
    server.write_request_func = on_write

    rw = Props.read | Props.write
    rperm = Perm.readable | Perm.writeable

    # PROXY (0x1828) ZUERST: bless bewirbt nur services[0] -> nur diese 16-bit
    # UUID landet im Advertising (klein genug). FDA0 ist 128-bit (zu gross fuers
    # Adv) und wird erst nach dem Connect per GATT gefunden.
    await server.add_new_service(PROXY)
    await server.add_new_characteristic(
        PROXY, PROXY_IN, Props.write | Props.write_without_response, b"",
        Perm.writeable)
    await server.add_new_characteristic(
        PROXY, PROXY_OUT, Props.notify, b"", Perm.readable)

    await server.add_new_service(FDA0)
    await server.add_new_characteristic(FDA0, FDA4, Props.read, INIT[FDA4], Perm.readable)
    await server.add_new_characteristic(FDA0, FDA6, rw, INIT[FDA6], rperm)
    await server.add_new_characteristic(FDA0, FDA7, rw, INIT[FDA7], rperm)
    await server.add_new_characteristic(FDA0, FDA8, rw, INIT[FDA8], rperm)

    await server.start()
    set_advertising(True)
    print(f"# Fake-Lampe '{NAME}' laeuft {runtime}s (advertised: 0x1828). "
          f"Jetzt Remote druecken (Modes durchgehen, dann dimmen).", flush=True)
    try:
        await asyncio.sleep(runtime)
    finally:
        set_advertising(False)
        await server.stop()

    print(f"\n# === {len(log_lines)} Writes empfangen ===", flush=True)
    for l in log_lines:
        print(l, flush=True)
    if not log_lines:
        print("# Kein Write. Hat sich die Remote verbunden? Ggf. MAC-Spoof "
              "noetig / Remote akzeptiert nur die echte Lampe.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
