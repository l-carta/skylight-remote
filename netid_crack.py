#!/usr/bin/env python3
"""
Werks-NetKey gegen die bekannte Netz-ID der Remote testen.

Ein Mesh-Knoten advertised seine Network-ID = k3(NetKey). k3 ist
einweg (nicht umkehrbar), aber wir koennen JEDEN Kandidaten pruefen:
k3(kandidat) == Ziel?  Trifft ein Default -> Werks-NetKey gefunden (ohne
Hardware, ohne Ciphertext).

    python3 netid_crack.py <netid_hex>

Die 8-Byte Network-ID liest man aus dem 0x1828-Advertising-Service-Data des
Zielknotens (Byte 0 = 0x00 = Network-ID-Typ, danach die 8 Byte). Siehe
dump_lamp_adv.py.
"""

import sys

from meshlib import crypto
from bruteforce_netkey import BUILTIN_CANDIDATES


def ascii_key(s: str) -> str:
    return s.encode()[:16].ljust(16, b"\x00").hex()


# breite Default-/Rate-Liste (16-Byte hex)
EXTRA = [ascii_key(s) for s in (
    "telink_mesh1", "TelinkMeshAll", "telink", "123", "12345678",
    "telink_ble_mesh", "TelinkSigMesh", "BK_MESH_light", "BK_MESH",
    "Skylight", "skylight", "philips", "Philips", "signify", "Signify",
    "PhilipsSkylight", "hue", "Hue", "admin", "password", "casaAdmin",
    "1234567890123456", "0000000000000000",
)] + [
    "00000000000000000000000000000000",
    "ffffffffffffffffffffffffffffffff",
    "0102030405060708090a0b0c0d0e0f10",
    "000102030405060708090a0b0c0d0e0f",
    "1234567890abcdef1234567890abcdef",
    "deadbeefdeadbeefdeadbeefdeadbeef",
]


def main():
    if len(sys.argv) < 2:
        print("Aufruf: python3 netid_crack.py <netid_hex>  (8 Byte)")
        return 2
    target = bytes.fromhex(sys.argv[1])
    cands = list(BUILTIN_CANDIDATES) + [(f"extra{i}", h) for i, h in enumerate(EXTRA)]
    print(f"Ziel-Netz-ID: {target.hex()}  ({len(cands)} Kandidaten)")
    for name, hexk in cands:
        try:
            key = bytes.fromhex(hexk)
        except ValueError:
            continue
        if len(key) != 16:
            continue
        if crypto.k3(key) == target:
            print(f"\n>>> TREFFER! Werks-NetKey = {hexk}  ({name})")
            return 0
    print("\nKein Default trifft -> Werks-NetKey ist (wie zu erwarten) "
          "zufaellig. Nur ein Firmware-Dump liefert ihn.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
