#!/usr/bin/env python3
"""
Schritt Y - Remote-Mitschnitt VOLL entschluesseln und Vendor-Opcodes finden.

Kette pro mitgeschnittener Network-PDU:
  1. NetKey-Kandidaten durchprobieren -> Network-Layer entschluesseln
     (liefert src/dst + Lower-Transport-PDU, aber Opcode noch verschluesselt).
  2. AppKey-Kandidaten durchprobieren -> Access-Layer entschluesseln
     (liefert das echte Opcode + Params).
  3. Vendor-Access-Nachrichten (Company-ID-Opcodes) rausfiltern und anzeigen -
     genau da steckt das Telink-Helligkeits-/Farbkommando der Fernbedienung.

Nutzt die Kandidatenliste aus bruteforce_netkey.py (all-zero, ASCII-Muster, ...)
plus optionale Zusatzdateien fuer NetKey und AppKey. Ein AID-Vorfilter sorgt
dafuer, dass nur AppKeys mit passendem AID ueberhaupt CCM rechnen.

Grenze: nur UNSEGMENTIERTE Access-Nachrichten. Kurze Kommandos (an/aus,
Helligkeit, Farbe) passen fast immer in eine unsegmentierte PDU. Segmentierte
(>11 Byte Access) muesste man erst reassemblieren - wird hier bewusst nur
gemeldet, nicht dekodiert.

Braucht KEINEN Pi und KEIN BLE - reine Rechnerei.

    python3 decode_capture.py --pdu-file capture.hex
    python3 decode_capture.py --pdu 68eca4...  --net-keys nk.txt --app-keys ak.txt
    python3 decode_capture.py --selftest
"""

# --- Pfad-Bootstrap: dieses Tool liegt in research/, der Stack + die
# Config (skylight-mesh.json) liegen im Repo-Root eine Ebene hoeher. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
_CFG = _os.path.join(_ROOT, "skylight-mesh.json")

import argparse
import sys

from meshlib import crypto
from meshlib.network import NetContext, decode_network_pdu, decrypt_access

from bruteforce_netkey import BUILTIN_CANDIDATES, load_key_file, strip_ad_header

TELINK_COMPANY = 0x0211  # zur Info: Telink Semiconductor SIG-Company-ID


def parse_vendor_or_sig(access: bytes):
    """-> dict mit Opcode-Infos. Unterscheidet 1-/2-Byte-SIG von 3-Byte-Vendor
    (Company-ID little-endian, korrekt interpretiert)."""
    b0 = access[0]
    if b0 & 0x80 == 0:                                  # 1-Byte SIG
        return {"kind": "sig", "opcode": b0, "params": access[1:]}
    if b0 & 0xC0 == 0x80:                                # 2-Byte SIG
        return {"kind": "sig", "opcode": int.from_bytes(access[:2], "big"),
                "params": access[2:]}
    # 3-Byte Vendor: [0b11 + 6-bit op][company-id LE]
    return {"kind": "vendor", "vendor_op": b0 & 0x3F,
            "company": int.from_bytes(access[1:3], "little"),
            "params": access[3:]}


def app_candidates_for_aid(aid: int, app_keys):
    """Nur AppKeys, deren k4-AID zum Lower-Transport-AID passt (Vorfilter)."""
    for name, key_hex in app_keys:
        key = bytes.fromhex(key_hex)
        if crypto.k4(key) == aid:
            yield name, key_hex, key


def decode_one(pdu, net_keys, app_keys, iv_indices):
    results = []
    for nk_name, nk_hex in net_keys:
        nk = bytes.fromhex(nk_hex)
        for iv in iv_indices:
            ctx = NetContext(net_key=nk, iv_index=iv)
            if pdu[0] & 0x7F != ctx.nid:
                continue
            net = decode_network_pdu(ctx, pdu)
            if net is None:
                continue
            ctl, ttl, seq, src, dst, transport = net
            if ctl == 1:            # Control-Message (kein Access) -> ignorieren
                continue
            if transport[0] & 0x80:                       # segmentiert
                results.append({"net": (nk_name, nk_hex, iv), "seq": seq,
                                "src": src, "dst": dst, "segmented": True})
                continue
            akf = (transport[0] >> 6) & 1
            aid = transport[0] & 0x3F
            if akf == 0:
                # DevKey-Nachricht (Konfiguration) - meist nicht das Ziel
                results.append({"net": (nk_name, nk_hex, iv), "seq": seq,
                                "src": src, "dst": dst, "devkey": True})
                continue
            for ak_name, ak_hex, ak in app_candidates_for_aid(aid, app_keys):
                access = decrypt_access(ctx, ak, True, seq, src, dst, transport)
                if access is None:
                    continue
                info = parse_vendor_or_sig(access)
                results.append({"net": (nk_name, nk_hex, iv),
                                "app": (ak_name, ak_hex), "seq": seq,
                                "src": src, "dst": dst, "access": access,
                                "info": info})
    return results


def report(pdu, results):
    print(f"\n--- PDU {pdu.hex()} (nid=0x{pdu[0] & 0x7f:02x}) ---")
    decoded = [r for r in results if "info" in r]
    if not decoded:
        seg = any(r.get("segmented") for r in results)
        net_only = any("app" not in r and not r.get("segmented")
                       and not r.get("devkey") for r in results)
        if seg:
            print("  Network entschluesselt, aber Access ist SEGMENTIERT "
                  "-> Reassembly noetig (hier nicht dekodiert).")
        elif any(r.get("devkey") for r in results):
            print("  Network entschluesselt (DevKey-/Config-Nachricht), aber "
                  "kein passender AppKey-Kandidat.")
        elif net_only:
            print("  Network entschluesselt, aber kein AppKey-Kandidat passt.")
        else:
            print("  Kein NetKey-Kandidat passt.")
        return False
    for r in decoded:
        nk_name, nk_hex, iv = r["net"]
        ak_name, ak_hex = r["app"]
        info = r["info"]
        print(f"  [OK] netkey={nk_hex} ({nk_name}) appkey={ak_hex} ({ak_name}) iv={iv}")
        print(f"       src=0x{r['src']:04x} dst=0x{r['dst']:04x} seq={r['seq']}")
        if info["kind"] == "vendor":
            tag = " <-- TELINK" if info["company"] == TELINK_COMPANY else ""
            print(f"       >>> VENDOR  company=0x{info['company']:04x}{tag}  "
                  f"vendor-op=0x{info['vendor_op']:02x}  "
                  f"params={info['params'].hex()}")
        else:
            print(f"       SIG opcode=0x{info['opcode']:x} "
                  f"params={info['params'].hex()}")
    return True


def selftest():
    """Echter End-to-End-Round-Trip: Vendor-Nachricht verschluesseln (Net+App),
    dann blind ueber die Kandidatenliste zurueckholen."""
    from meshlib.network import encode_access, encode_network_pdu, build_transport_pdus

    net_hex = "f7a2a44f8e8a8029064f173ddc1e2b00"   # SIG-Spec-Sample (in Liste)
    app_hex = "0102030405060708090a0b0c0d0e0f10"   # (ebenfalls in Liste)
    ctx = NetContext(net_key=bytes.fromhex(net_hex), iv_index=0)

    # fiktives Telink-Vendor-Helligkeitskommando: op=0x05, company=0x0211, level=200
    access = encode_access(0xC00000 | (0x05 << 16) | TELINK_COMPANY, b"\xc8")
    seq, src, dst = 7, 0x0005, 0x0002
    tpdus = build_transport_pdus(ctx, bytes.fromhex(app_hex), True, seq, src, dst, access)
    assert len(tpdus) == 1, "Testnachricht sollte unsegmentiert sein"
    pdu = encode_network_pdu(ctx, 0, 5, seq, src, dst, tpdus[0])
    print(f"Selbsttest-PDU (verschluesselt): {pdu.hex()}")

    results = decode_one(pdu, BUILTIN_CANDIDATES, BUILTIN_CANDIDATES, [0, 1])
    ok = report(pdu, results)
    hit = next((r for r in results if r.get("info", {}).get("kind") == "vendor"), None)
    good = bool(hit and hit["info"]["company"] == TELINK_COMPANY
                and hit["info"]["vendor_op"] == 0x05
                and hit["info"]["params"] == b"\xc8")
    print("\nSELBSTTEST:", "OK - volle Kette (Net+App+Vendor) rekonstruiert."
          if good else "FEHLGESCHLAGEN!")
    return 0 if good else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pdu")
    g.add_argument("--pdu-file")
    g.add_argument("--selftest", action="store_true")
    ap.add_argument("--net-keys", help="Zusatz-NetKey-Kandidaten (hex/Zeile)")
    ap.add_argument("--app-keys", help="Zusatz-AppKey-Kandidaten (hex/Zeile)")
    ap.add_argument("--iv", default="0,1")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    net_keys = list(BUILTIN_CANDIDATES) + (load_key_file(args.net_keys) if args.net_keys else [])
    app_keys = list(BUILTIN_CANDIDATES) + (load_key_file(args.app_keys) if args.app_keys else [])
    iv_indices = [int(x) for x in args.iv.split(",")]

    if args.pdu:
        raw_list = [args.pdu]
    elif args.pdu_file:
        with open(args.pdu_file) as f:
            raw_list = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    else:
        ap.error("--pdu, --pdu-file oder --selftest angeben")

    print(f"{len(net_keys)} NetKey- x {len(app_keys)} AppKey-Kandidaten "
          f"x {len(iv_indices)} IV gegen {len(raw_list)} PDU(s).")
    rc = 1
    for raw in raw_list:
        pdu = strip_ad_header(bytes.fromhex(raw.replace(" ", "").replace(":", "")))
        if report(pdu, decode_one(pdu, net_keys, app_keys, iv_indices)):
            rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
