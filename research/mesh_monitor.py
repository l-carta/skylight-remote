#!/usr/bin/env python3
"""
Passiver Mesh-Mitschnitt - zeigt ALLES, was die Lampe im Netz emittiert.

Motivation: alle bisherigen Probes lauschen nur kurz nach einem eigenen Send
und filtern hart `if ctl or src != lamp`. Das verwirft (a) Control-Messages
(Heartbeats) und (b) alles, was NICHT von der primaeren Unicast-Adresse kommt.
Das Vendor-Modell sitzt aber auf Element-Adresse 0x0002 -- eine Status-
Publikation von dort waere nie sichtbar geworden.

Dieses Tool zieht JEDE netzentschluesselbare PDU aus dem Proxy (egal src/dst/
ctl), versucht Access-Decrypt mit app- UND dev-key (unsegmentiert + segmentiert,
inkl. ASZMIC) und loggt sonst Roh-Hex. Optional wird waehrenddessen On/Off
getoggelt, um eine zustandsgebundene Publikation zu provozieren.

    sudo systemctl stop skylight-bridge
    python3 research/mesh_monitor.py 45           # 45s rein passiv
    python3 research/mesh_monitor.py 60 toggle    # 60s + On/Off-Provokation
    sudo systemctl start skylight-bridge
"""

# --- Pfad-Bootstrap: dieses Tool liegt in research/, der Stack + die
# Config (skylight-mesh.json) liegen im Repo-Root eine Ebene hoeher. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import sys

from meshlib import network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg, save_cfg
from meshlib.proxy import MeshProxy
from vendor_probe import _try_decrypt, _seq_auth

OP_ONOFF_SET = 0x8202


def name_ctrl(op):
    return {0x0A: "HEARTBEAT", 0x00: "SEG-ACK", 0x0B: "FRIEND-POLL",
            0x0C: "FRIEND-UPDATE"}.get(op, f"CTRL-0x{op:02x}")


async def toggler(proxy, cfg, app, lamp, stop):
    """Toggelt On/Off im Takt, um eine Publikation zu provozieren."""
    on = True
    while not stop.is_set():
        cfg["tid"] = (cfg["tid"] + 1) & 0xFF
        wire = 0x00 if on else 0x01          # Firmware-Quirk: invertiert
        await proxy.send_access(cfg, app, True, lamp, OP_ONOFF_SET,
                                bytes([wire, cfg["tid"]]))
        print(f"    (toggle -> {'AN' if on else 'AUS'})", flush=True)
        on = not on
        try:
            await asyncio.wait_for(stop.wait(), timeout=6.0)
        except asyncio.TimeoutError:
            pass


async def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    do_toggle = len(sys.argv) > 2 and sys.argv[2].lower() == "toggle"

    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    app = bytes.fromhex(cfg["app_key"])
    dev = bytes.fromhex(cfg["dev_key"])
    lamp, iv, mysrc = cfg["unicast"], cfg["iv_index"], cfg["src"]
    keys = (("app", app, 0x01), ("dev", dev, 0x02))

    # Segment-Reassembly-Puffer je Quelle
    segs = {}

    n_total = n_ctrl = n_lamp = n_undec = 0

    async with MeshProxy(cfg["mac"], ctx, mysrc, log=lambda *_: None) as proxy:
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        stop = asyncio.Event()
        tog = asyncio.create_task(toggler(proxy, cfg, app, lamp, stop)) \
            if do_toggle else None

        print(f"=== Mitschnitt {dur:.0f}s "
              f"({'mit On/Off-Toggle' if do_toggle else 'rein passiv'}) - "
              f"mein src=0x{mysrc:04x}, Lampe unicast=0x{lamp:04x} ===",
              flush=True)

        while True:
            rem = dur - (loop.time() - t0)
            if rem <= 0:
                break
            try:
                ctl, ttl, seq, src, dst, tr = await asyncio.wait_for(
                    proxy._rx.get(), timeout=rem)
            except asyncio.TimeoutError:
                break

            n_total += 1
            ts = loop.time() - t0
            mine = " (=ICH)" if src == mysrc else ""
            head = f"[t~{ts:5.1f}] src=0x{src:04x}{mine} dst=0x{dst:04x} seq={seq}"

            if ctl:
                n_ctrl += 1
                print(f"{head}  CTRL {name_ctrl(tr[0] & 0x7f)} "
                      f"data={tr.hex()}", flush=True)
                continue

            if src == mysrc:                 # eigene Echo-Sends ignorieren
                continue

            if not (tr[0] & 0x80):           # unsegmentierte Access-PDU
                r = _try_decrypt(tr[1:], seq, src, dst, iv, 0, keys)
                if r:
                    kname, (op, params) = r
                    n_lamp += 1
                    print(f"{head}  ACCESS [{kname}] op=0x{op:x} "
                          f"params={params.hex()}  <== VON DER LAMPE",
                          flush=True)
                else:
                    n_undec += 1
                    print(f"{head}  <nicht dekodierbar> raw={tr.hex()}",
                          flush=True)
                continue

            # segmentierte Access-PDU -> reassemblieren
            hdr = int.from_bytes(tr[1:4], "big")
            szmic = (hdr >> 23) & 1
            seq_zero = (hdr >> 10) & 0x1FFF
            seg_o, seg_n = (hdr >> 5) & 0x1F, hdr & 0x1F
            buf = segs.setdefault(src, {})
            buf[seg_o] = tr[4:]
            if len(buf) == seg_n + 1:
                cipher = b"".join(buf[i] for i in range(seg_n + 1))
                sa = _seq_auth(seq, seq_zero)
                r = _try_decrypt(cipher, sa, src, dst, iv, szmic, keys)
                segs[src] = {}
                if r:
                    kname, (op, params) = r
                    n_lamp += 1
                    print(f"{head}  SEG-ACCESS [{kname}] op=0x{op:x} "
                          f"params={params.hex()}  <== VON DER LAMPE",
                          flush=True)
                else:
                    n_undec += 1
                    print(f"{head}  SEG <nicht dekodierbar> "
                          f"cipher={cipher.hex()}", flush=True)

        if tog:
            stop.set()
            await tog
        save_cfg(CONFIG_FILE, cfg)           # seq persistieren!

    print(f"\n=== FAZIT: {n_total} PDU(s) gesamt | {n_ctrl} Control | "
          f"{n_lamp} dekodierte Lampen-Access | {n_undec} undekodierbar ===")
    if n_lamp == 0 and n_undec == 0 and n_ctrl == 0:
        print("Die Lampe emittiert von sich aus NICHTS im Netz "
              "(keine Publikation, kein Heartbeat) -> publish-only-Hoffnung tot.")
    elif n_undec:
        print("Es gibt undekodierbaren Verkehr -> evtl. anderer Key/Netz. "
              "Roh-Hex oben analysieren.")


if __name__ == "__main__":
    asyncio.run(main())
