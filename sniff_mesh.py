#!/usr/bin/env python3
"""
Schritt Z - Mesh-Adv-Pakete mitschneiden (Pi-Onboard-BLE, KEIN Dongle noetig).

Nutzt die BlueZ-Werkzeuge, die auf dem Pi eh da sind:
  - `btmon -w` schreibt einen rohen HCI-Mitschnitt (BTSnoop, monitor-Format),
  - `bluetoothctl scan on` laesst BlueZ ordentlich ueber alle 3 Adv-Kanaele
    scannen (besser als ein selbstgebauter Single-Channel-Socket).
Danach parsen wir den Mitschnitt, ziehen aus jedem Advertising Report die
AD-Struktur mit Type 0x2A (Mesh Message) / 0x2B (Mesh Beacon) / 0x29 (PB-ADV)
und geben die Payload als hex aus - copy-paste-fertig fuer decode_capture.py.

    sudo python3 sniff_mesh.py 30 > capture.hex   # 30 s mitschneiden
    python3 decode_capture.py --pdu-file capture.hex

Waehrend es laeuft: an der Fernbedienung Helligkeit/Farbe druecken. Ein Druck
wird viele Male wiederholt gesendet.

Braucht root (btmon-Monitor-Socket) -> mit sudo starten. NUR Linux/BlueZ.
Selbsttest des Parsers (laeuft ueberall, ohne BLE):

    python3 sniff_mesh.py --selftest
"""

import argparse
import struct
import subprocess
import sys
import tempfile

MESH_AD_TYPES = {0x2A: "mesh-message", 0x2B: "mesh-beacon", 0x29: "pb-adv"}


def extract_ad(adv_data: bytes):
    """BLE-AD-Struktur [len][type][data...] zerlegen. -> Liste (type, data)."""
    out, i = [], 0
    while i < len(adv_data):
        ln = adv_data[i]
        if ln == 0 or i + 1 + ln > len(adv_data):
            break
        out.append((adv_data[i + 1], adv_data[i + 2:i + 1 + ln]))
        i += 1 + ln
    return out


def mesh_payloads(adv_data: bytes):
    """-> Liste (ad_type, payload_hex) nur fuer Mesh-relevante AD-Typen."""
    return [(t, d.hex()) for t, d in extract_ad(adv_data) if t in MESH_AD_TYPES]


def parse_btsnoop(blob: bytes):
    """BTSnoop im btmon-'monitor'-Format zerlegen. -> Liste (addr_hex, rssi,
    adv_data) aus LE Advertising Reports (Legacy 0x02 UND Extended 0x0D)."""
    if blob[:8] != b"btsnoop\x00":
        raise ValueError("kein BTSnoop-File")
    reports = []
    i = 16                                   # File-Header ueberspringen
    while i + 24 <= len(blob):
        _orig, incl, flags, _drops = struct.unpack(">IIII", blob[i:i + 16])
        i += 24                              # + 8 Byte Timestamp
        pkt = blob[i:i + incl]
        i += incl
        if flags & 0xFFFF != 0x0003:         # nur Event-Pakete
            continue
        # HCI-Event ohne 0x04-Prefix: [evt][plen][params]
        if len(pkt) < 4 or pkt[0] != 0x3E:   # LE Meta Event
            continue
        sub = pkt[2]
        num = pkt[3]
        off = 4
        try:
            if sub == 0x02:                  # Legacy LE Advertising Report
                for _ in range(num):
                    addr = pkt[off + 2:off + 8][::-1].hex(":")
                    dlen = pkt[off + 8]
                    data = pkt[off + 9:off + 9 + dlen]
                    rssi = pkt[off + 9 + dlen]
                    reports.append((addr, rssi - 256 if rssi > 127 else rssi, data))
                    off += 9 + dlen + 1      # + RSSI
            elif sub == 0x0D:                # Extended Advertising Report
                for _ in range(num):
                    # evt_type(2) addr_type(1) addr(6) prim_phy(1) sec_phy(1)
                    # sid(1) tx(1) rssi(1) per_int(2) dir_addr_type(1)
                    # dir_addr(6) data_len(1) data(N)  = 24 Byte Kopf
                    addr = pkt[off + 2:off + 8][::-1].hex(":")
                    rssi = pkt[off + 8]
                    dlen = pkt[off + 23]
                    data = pkt[off + 24:off + 24 + dlen]
                    reports.append((addr, rssi - 256 if rssi > 127 else rssi, data))
                    off += 24 + dlen
        except IndexError:
            continue
    return reports


def _capture(seconds: int):
    snoop = tempfile.NamedTemporaryFile(suffix=".snoop", delete=False).name
    print(f"# Scanne {seconds}s (btmon -> {snoop}). Jetzt Remote-Tasten "
          f"druecken ...", file=sys.stderr)
    btmon = subprocess.Popen(["btmon", "-w", snoop],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        subprocess.run(["bluetoothctl", "--timeout", str(seconds), "scan", "on"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        btmon.terminate()
        try:
            btmon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            btmon.kill()
    with open(snoop, "rb") as f:
        return f.read()


def run_sniffer(seconds: int):
    reports = parse_btsnoop(_capture(seconds))
    seen = set()
    for _addr, _rssi, adv in reports:
        for ad_type, payload in mesh_payloads(adv):
            if payload not in seen:
                seen.add(payload)
                print(f"{payload}  # {MESH_AD_TYPES[ad_type]}")
    print(f"# {len(seen)} eindeutige Mesh-Payload(s) aus {len(reports)} "
          f"Adv-Reports.", file=sys.stderr)
    if not seen:
        print("# Kein SIG-Mesh (0x2A) gefunden -> evtl. proprietaeres Telink-Adv."
              " Diagnose mit:  sudo python3 sniff_mesh.py --diag", file=sys.stderr)


def run_diag(seconds: int, watch_mac: str = ""):
    """Zeigt pro Absender die AD-Typen und (bei 0xFF) die Company-ID. So sehen
    wir, ob beim Tastendruck ein Telink-Geraet (Company 0x0211) oder neuer
    Absender auftaucht - auch wenn es KEIN SIG-Mesh ist."""
    from collections import defaultdict
    reports = parse_btsnoop(_capture(seconds))
    per = defaultdict(lambda: {"n": 0, "rssi": -999, "ad": set(),
                               "companies": set(), "sample": ""})
    for addr, rssi, adv in reports:
        d = per[addr]
        d["n"] += 1
        d["rssi"] = max(d["rssi"], rssi)
        for t, data in extract_ad(adv):
            d["ad"].add(t)
            if t == 0xFF and len(data) >= 2:           # Manufacturer Specific
                d["companies"].add(int.from_bytes(data[:2], "little"))
                if not d["sample"]:
                    d["sample"] = data.hex()
            if t in MESH_AD_TYPES and not d["sample"]:
                d["sample"] = data.hex()
    print(f"# {len(reports)} Adv-Reports von {len(per)} Absendern\n", file=sys.stderr)
    # nach RSSI (Naehe) sortiert - die Remote in Pi-Naehe steht oben
    for addr, d in sorted(per.items(), key=lambda kv: -kv[1]["rssi"]):
        ads = " ".join(f"0x{t:02x}" for t in sorted(d["ad"]))
        comp = " ".join(f"0x{c:04x}" for c in sorted(d["companies"]))
        tag = ""
        if 0x0211 in d["companies"]:
            tag += "  <== TELINK(0x0211)!"
        if watch_mac and addr.upper() == watch_mac.upper():
            tag += "  <== LAMPE"
        if any(t in MESH_AD_TYPES for t in d["ad"]):
            tag += "  <== SIG-MESH"
        print(f"{addr}  rssi={d['rssi']:>4}  n={d['n']:>3}  AD:[{ads}]  "
              f"comp:[{comp}]  {d['sample'][:40]}{tag}")


def selftest():
    # 1) AD-Extractor
    mesh = bytes.fromhex("68aabbccddeeff")
    blob = (bytes([2, 0x01, 0x06]) + bytes([1 + len(mesh), 0x2A]) + mesh
            + bytes([3, 0x09]) + b"BK")
    ok_ad = mesh_payloads(blob) == [(0x2A, mesh.hex())]
    print("AD-Extractor:", mesh_payloads(blob))

    # 2) BTSnoop-Parser: ein Event-Record mit Legacy-Adv-Report, der eine
    #    Mesh-Message-AD traegt, in ein minimales monitor-BTSnoop verpacken.
    adv = bytes([2, 0x01, 0x06]) + bytes([1 + len(mesh), 0x2A]) + mesh
    report = (bytes([0x00, 0x01]) + bytes.fromhex("112233445566")
              + bytes([len(adv)]) + adv + bytes([0xC0]))       # +RSSI
    evt = bytes([0x3E, 2 + len(report), 0x02, 0x01]) + report  # LE Meta / sub 0x02
    rec_hdr = struct.pack(">IIII", len(evt), len(evt), 0x00000003, 0) + b"\x00" * 8
    snoop = b"btsnoop\x00" + struct.pack(">II", 1, 2001) + rec_hdr + evt
    got = parse_btsnoop(snoop)
    ok_snoop = (len(got) == 1 and got[0][0] == "66:55:44:33:22:11"
                and mesh_payloads(got[0][2]) == [(0x2A, mesh.hex())])
    print("BTSnoop-Parser:", [(a, mesh_payloads(adv)) for a, _r, adv in got])

    ok = ok_ad and ok_snoop
    print("SELBSTTEST:", "OK" if ok else "FEHLGESCHLAGEN!")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seconds", nargs="?", type=int, default=30,
                    help="Mitschnittdauer in Sekunden (default 30)")
    ap.add_argument("--selftest", action="store_true",
                    help="nur die Parser testen (laeuft ueberall)")
    ap.add_argument("--diag", action="store_true",
                    help="Diagnose: ALLE Absender + AD-Typen + Company-IDs")
    ap.add_argument("--watch-mac", default="",
                    help="diese MAC im Diag-Output markieren (z.B. Lampe)")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.diag:
        run_diag(args.seconds, args.watch_mac)
    else:
        run_sniffer(args.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
