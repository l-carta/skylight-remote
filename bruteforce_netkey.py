#!/usr/bin/env python3
"""
Schritt X - NetKey-Kandidaten gegen einen Fernbedienungs-Mitschnitt testen.

Zweck: Die Skylight-Fernbedienung sendet ihre (proprietaeren Telink-Vendor-)
Nachrichten als BLE-SIG-Mesh-Broadcasts. Wir haben sie im *Werks*-Netz mit
*Werks*-Keys, die wir nicht kennen. Dieses Skript probiert bekannte/plausible
Default-NetKeys gegen eine mitgeschnittene Network-PDU. Trifft einer, ist die
CCM-MIC gueltig -> wir koennen den kompletten Remote-Traffic entschluesseln und
das Vendor-Opcode fuer Helligkeit/Farbe ablesen.

WICHTIG / ehrliche Einordnung:
  Ein per SIG-Mesh-Provisioning erzeugter NetKey ist normalerweise ZUFALLS-
  generiert - dann gibt es keinen "Default" und diese Brute-Liste trifft nicht.
  Sie trifft nur, wenn der Hersteller schlampig war (fester Key im Firmware-
  Image, all-zero, ASCII-Muster). Kostet nichts, es zu probieren. Wenn nichts
  trifft, ist der naechste Schritt die Vendor-App / das Firmware-Dump - dort
  liegt der Key oder das Vendor-Opcode direkt.

Braucht KEINEN Pi und KEIN BLE - reine Rechnerei. Laeuft auf Mac wie Pi.

--- So kommst du an die Network-PDU (--pdu) ---
Mit nRF-Sniffer + Wireshark den Remote-Tastendruck aufnehmen. Im Mesh-Adv-Paket
findest du die AD-Struktur [len][0x2A][network-pdu...]. Kopiere die Bytes NACH
0x2A (die Network-PDU beginnt mit dem IVI/NID-Byte). Dieses Skript strippt einen
fuehrenden [len][0x2A]-AD-Header aber auch automatisch, wenn du ihn mitgibst.

Beispiel:
    python3 bruteforce_netkey.py --pdu 68eca487...  --iv 0,1
    python3 bruteforce_netkey.py --pdu-file capture.hex --keys extra_keys.txt
    python3 bruteforce_netkey.py --selftest
"""

import argparse
import sys

from meshlib.network import NetContext, decode_network_pdu


# --- Kandidaten-NetKeys --------------------------------------------------
# 16 Byte hex. Frei erweiterbar. Brute ist instant, also ruhig grosszuegig.
def _ascii_key(s: str) -> str:
    """ASCII-String auf 16 Byte mit Nullen aufgefuellt -> hex. Fuer den
    'fauler Hersteller nimmt einen lesbaren String'-Fall."""
    return s.encode()[:16].ljust(16, b"\x00").hex()


BUILTIN_CANDIDATES = [
    ("all-zero",            "00000000000000000000000000000000"),
    ("all-ff",              "ffffffffffffffffffffffffffffffff"),
    ("0102..10",            "0102030405060708090a0b0c0d0e0f10"),
    ("SIG-spec-sample",     "f7a2a44f8e8a8029064f173ddc1e2b00"),  # nur Demo/Selbsttest
    # spekulative ASCII-Muster (Telink-SDK-typische Namen); kosten nichts:
    ("ascii:telink_mesh1",  _ascii_key("telink_mesh1")),
    ("ascii:TelinkMeshAll", _ascii_key("TelinkMeshAll")),
    ("ascii:telink",        _ascii_key("telink")),
    ("ascii:123",           _ascii_key("123")),
    ("ascii:BK_MESH_light", _ascii_key("BK_MESH_light")),
]


def load_key_file(path: str):
    out = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            h = line.strip().replace(" ", "").replace(":", "")
            if not h or h.startswith("#"):
                continue
            if len(h) != 32:
                print(f"  ! {path}:{i} uebersprungen (kein 16-Byte-Hex): {h}",
                      file=sys.stderr)
                continue
            out.append((f"{path}:{i}", h))
    return out


def strip_ad_header(pdu: bytes) -> bytes:
    """Entfernt einen fuehrenden BLE-AD-Header [len][0x2A|0x2B], falls
    vorhanden, sonst gibt die Bytes unveraendert zurueck."""
    if len(pdu) >= 2 and pdu[1] in (0x2A, 0x2B) and pdu[0] == len(pdu) - 1:
        return pdu[2:]
    return pdu


def try_all(pdu: bytes, candidates, iv_indices):
    hits = []
    for name, key_hex in candidates:
        key = bytes.fromhex(key_hex)
        for iv in iv_indices:
            ctx = NetContext(net_key=key, iv_index=iv)
            # billiger Vorfilter: NID muss passen
            if pdu[0] & 0x7F != ctx.nid:
                continue
            res = decode_network_pdu(ctx, pdu)
            if res is not None:
                hits.append((name, key_hex, iv, res))
    return hits


def report(hits):
    if not hits:
        print("\n>> KEIN Kandidat passt.")
        print("   Der Werks-NetKey ist vermutlich zufaellig. Naechster Schritt:")
        print("   Vendor-App decompilen oder Telink-Firmware dumpen (SWS-Port).")
        return 1
    print(f"\n>> TREFFER ({len(hits)}):")
    for name, key_hex, iv, res in hits:
        ctl, ttl, seq, src, dst, transport = res
        line = (f"   key={key_hex} ({name}) iv={iv}  "
                f"ctl={ctl} ttl={ttl} seq={seq} "
                f"src=0x{src:04x} dst=0x{dst:04x}")
        print(line)
        # Achtung: transport ist noch APPKEY-verschluesselt. Wir sehen hier nur
        # den Lower-Transport-Header (AKF/AID), NICHT das Opcode.
        seg = bool(transport[0] & 0x80)
        akf = (transport[0] >> 6) & 1
        aid = transport[0] & 0x3F
        print(f"        lower-transport: {'segmentiert' if seg else 'unsegmentiert'}"
              f" akf={akf} aid=0x{aid:02x}  (Opcode braucht noch den AppKey)")
        print(f"        transport={transport.hex()}")
    print("\n   NetKey gefunden -> jetzt mit decode_capture.py + AppKey den "
          "Traffic voll entschluesseln und das Vendor-Opcode raussuchen.")
    return 0


def selftest():
    """Round-Trip: PDU mit bekanntem Key bauen, dann per Brute wiederfinden."""
    from meshlib.network import encode_network_pdu

    key_hex = "f7a2a44f8e8a8029064f173ddc1e2b00"  # SIG-Spec-Sample (in Liste)
    ctx = NetContext(net_key=bytes.fromhex(key_hex), iv_index=0)
    # eine plausible Access-Nachricht (Generic OnOff Set unacked = 0x8203)
    transport = b"\x00" + b"\x82\x03\x01\x00"  # AKF/AID=0 (DevKey-Style-Demo)
    pdu = encode_network_pdu(ctx, ctl=0, ttl=5, seq=1,
                             src=0x0002, dst=0x0001, transport=transport)
    print(f"Selbsttest-PDU: {pdu.hex()}")
    # Decoys + der echte Key stecken in BUILTIN_CANDIDATES
    hits = try_all(pdu, BUILTIN_CANDIDATES, [0, 1])
    ok = any(h[1] == key_hex for h in hits)
    report(hits)
    print("\nSELBSTTEST:", "OK - Brute findet den bekannten Key."
          if ok else "FEHLGESCHLAGEN!")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pdu", help="Network-PDU als hex (mit/ohne AD-Header)")
    g.add_argument("--pdu-file", help="Datei mit einer PDU-hex pro Zeile")
    g.add_argument("--selftest", action="store_true",
                   help="Round-Trip-Selbsttest ohne Mitschnitt")
    ap.add_argument("--keys", help="Zusatz-Datei mit NetKey-Kandidaten "
                                    "(16-Byte-hex pro Zeile)")
    ap.add_argument("--iv", default="0,1",
                    help="IV-Indizes, kommagetrennt (default: 0,1)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    candidates = list(BUILTIN_CANDIDATES)
    if args.keys:
        candidates += load_key_file(args.keys)

    iv_indices = [int(x) for x in args.iv.split(",")]

    pdus = []
    if args.pdu:
        pdus.append(args.pdu)
    elif args.pdu_file:
        with open(args.pdu_file) as f:
            pdus = [ln.strip() for ln in f if ln.strip()
                    and not ln.startswith("#")]
    else:
        ap.error("--pdu, --pdu-file oder --selftest angeben")

    print(f"{len(candidates)} Kandidaten x {len(iv_indices)} IV-Indizes "
          f"gegen {len(pdus)} PDU(s).")
    rc = 1
    for raw in pdus:
        pdu = strip_ad_header(bytes.fromhex(raw.replace(" ", "").replace(":", "")))
        print(f"\n--- PDU {pdu.hex()} (nid=0x{pdu[0] & 0x7f:02x}) ---")
        if report(try_all(pdu, candidates, iv_indices)) == 0:
            rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
