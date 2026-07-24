#!/usr/bin/env python3
"""
Composition Data der Lampe auslesen -> gibt es ein Telink-VENDOR-Modell?

Der bestehende Stack sendet zwar segmentiert, dekodiert beim Empfang aber nur
UNsegmentierte Antworten. Composition Data ist zu gross -> segmentiert. Dieses
Skript ergaenzt genau das fehlende Stueck: RX-Reassembly der Lower-Transport-
Segmente, DevKey-Entschluesselung, und Parsing von Page 0.

Wir suchen ein Vendor-Modell (Company 0x0211 = Telink). Wenn eins existiert,
laeuft Helligkeit/Farbe vermutlich ueber dessen Vendor-Opcodes - erreichbar
mit unserem app_key ueber genau den Proxy, den der Stack schon spricht.

WICHTIG: Bridge vorher stoppen (belegt sonst die Verbindung):
    sudo systemctl stop skylight-bridge

    python3 read_composition.py
"""

import asyncio

from meshlib import crypto, network
from meshlib.skylight import CONFIG_FILE
from meshlib.state import load_cfg
from meshlib.proxy import MeshProxy

OP_COMPOSITION_GET = 0x8008
OP_COMPOSITION_STATUS = 0x02
COMPANY_TELINK = 0x0211


def _seq_auth(rseq: int, seq_zero: int) -> int:
    """SeqAuth aus Netzwerk-Seq eines Segments + SeqZero (13 Bit) rekonstruieren."""
    cand = (rseq & ~0x1FFF) | seq_zero
    if cand > rseq:
        cand -= 0x2000
    return cand


async def _collect_status(proxy, lamp_src: int, timeout: float):
    """Sammelt Segmente einer Access-Antwort von der Lampe und reassembliert
    die verschluesselte Upper-Transport-PDU. -> (seq_auth, src, dst, cipher,
    mic_len) oder ('unseg', ...) fuer eine unsegmentierte Antwort."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    segs, seg_n, seq_auth, msrc, mdst, szmic = {}, None, None, None, None, 0
    while True:
        rem = deadline - loop.time()
        if rem <= 0:
            raise TimeoutError("keine (vollstaendige) Composition-Antwort")
        ctl, ttl, seq, src, dst, transport = await asyncio.wait_for(
            proxy._rx.get(), timeout=rem)
        if ctl or src != lamp_src:
            continue
        if not (transport[0] & 0x80):                 # unsegmentiert
            return ("unseg", seq, src, dst, transport)
        hdr = int.from_bytes(transport[1:4], "big")
        akf, aid = (transport[0] >> 6) & 1, transport[0] & 0x3F
        szmic = (hdr >> 23) & 1
        seq_zero = (hdr >> 10) & 0x1FFF
        seg_o = (hdr >> 5) & 0x1F
        seg_n = hdr & 0x1F
        segs[seg_o] = transport[4:]
        seq_auth = _seq_auth(seq, seq_zero)
        msrc, mdst = src, dst
        print(f"  seg o={seg_o}/{seg_n} netseq={seq} seqzero={seq_zero} "
              f"szmic={szmic} akf={akf} aid=0x{aid:02x} "
              f"seqauth={seq_auth} data={transport[4:].hex()}")
        if len(segs) == seg_n + 1:
            cipher = b"".join(segs[i] for i in range(seg_n + 1))
            return ("seg", seq_auth, msrc, mdst, cipher, 8 if szmic else 4)


def parse_page0(data: bytes):
    cid, pid, vid, crpl, feat = (int.from_bytes(data[i:i + 2], "little")
                                 for i in range(0, 10, 2))
    print(f"CID=0x{cid:04x} PID=0x{pid:04x} VID=0x{vid:04x} "
          f"CRPL={crpl} Features=0x{feat:04x}")
    i = 10
    elem = 0
    vendor_hits = []
    while i + 4 <= len(data):
        loc = int.from_bytes(data[i:i + 2], "little")
        num_s, num_v = data[i + 2], data[i + 3]
        i += 4
        sig = [int.from_bytes(data[i + 2 * k:i + 2 * k + 2], "little")
               for k in range(num_s)]
        i += 2 * num_s
        vnd = []
        for k in range(num_v):
            comp = int.from_bytes(data[i:i + 2], "little")
            model = int.from_bytes(data[i + 2:i + 4], "little")
            vnd.append((comp, model))
            i += 4
        print(f"\nElement {elem} (loc=0x{loc:04x}): {num_s} SIG, {num_v} Vendor")
        print("  SIG   :", " ".join(f"0x{m:04x}" for m in sig) or "-")
        for comp, model in vnd:
            tag = "  <== TELINK!" if comp == COMPANY_TELINK else ""
            print(f"  Vendor: company=0x{comp:04x} model=0x{model:04x}{tag}")
            vendor_hits.append((elem, comp, model))
        elem += 1
    return vendor_hits


async def main():
    cfg = load_cfg(CONFIG_FILE)
    ctx = network.NetContext(bytes.fromhex(cfg["net_key"]), cfg["iv_index"])
    dev_key = bytes.fromhex(cfg["dev_key"])
    lamp = cfg["unicast"]

    async with MeshProxy(cfg["mac"], ctx, cfg["src"], log=print) as proxy:
        result = None
        for attempt in range(4):
            print(f"Composition Data Get (Versuch {attempt + 1}) ...")
            await proxy.send_access(cfg, dev_key, False, lamp,
                                    OP_COMPOSITION_GET, b"\x00")
            try:
                result = await _collect_status(proxy, lamp, timeout=4.0)
                break
            except TimeoutError as e:
                print(f"  {e}")
        if result is None:
            print("Keine Antwort. Bridge wirklich gestoppt? Lampe in Reichweite?")
            return 1

        if result[0] == "unseg":
            _, seq, src, dst, transport = result
            access = network.decrypt_access(ctx, dev_key, False, seq, src, dst,
                                             transport)
        else:
            _, seq_auth, src, dst, cipher, mic_len = result
            aszmic = 1 if mic_len == 8 else 0     # SZMIC -> ASZMIC-Bit der Nonce
            # Device-Nonce mit korrektem ASZMIC-Bit (Byte 1 = ASZMIC<<7).
            nonce = (bytes([0x02, aszmic << 7]) + seq_auth.to_bytes(3, "big")
                     + src.to_bytes(2, "big") + dst.to_bytes(2, "big")
                     + ctx.iv_index.to_bytes(4, "big"))
            try:
                access = crypto.ccm_decrypt(dev_key, nonce, cipher, mic_len)
            except Exception as e:
                print(f"Entschluesselung fehlgeschlagen: {e}")
                return 1

        opcode, params = network.parse_access(access)
        if opcode != OP_COMPOSITION_STATUS:
            print(f"Unerwartetes Opcode 0x{opcode:x}")
            return 1
        page, comp = params[0], params[1:]
        print(f"\n=== Composition Data Page {page} ({len(comp)} Byte) ===")
        print("raw:", comp.hex())
        hits = parse_page0(comp)

        print()
        telink = [h for h in hits if h[1] == COMPANY_TELINK]
        if telink:
            print(f">>> TELINK-VENDOR-MODELL gefunden: {telink}")
            print("    -> naechster Schritt: Vendor-Opcodes (0xC0.. mit Company "
                  "0x0211) fuer Helligkeit an dieses Element/Modell testen.")
        elif hits:
            print(f">>> Vendor-Modelle vorhanden, aber nicht Telink: {hits}")
        else:
            print(">>> KEIN Vendor-Modell. Dann ist Helligkeit ueber Mesh nicht "
                  "erreichbar -> Sniff-Weg (Remote<->Lampe) bleibt als Fallback.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
