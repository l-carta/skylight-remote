"""Mesh Network-/Transport-/Access-Layer fuer die Proxy-Verbindung.

Implementiert genau das, was skylight-cmd braucht: unsegmentierte und
TX-segmentierte Access-Messages mit App- oder DevKey, RX-Dekodierung
unsegmentierter Antworten (Status-Messages).
"""

from dataclasses import dataclass

from . import crypto


@dataclass
class NetContext:
    net_key: bytes
    iv_index: int

    def __post_init__(self):
        self.nid, self.enc_key, self.priv_key = crypto.k2(self.net_key, b"\x00")


def _net_nonce(ctl_ttl: int, seq: int, src: int, iv_index: int) -> bytes:
    return (bytes([0x00, ctl_ttl]) + seq.to_bytes(3, "big")
            + src.to_bytes(2, "big") + b"\x00\x00" + iv_index.to_bytes(4, "big"))


def _app_nonce(nonce_type: int, seq: int, src: int, dst: int,
               iv_index: int, aszmic: int = 0) -> bytes:
    # Byte 1 traegt das ASZMIC-Bit: bei SEGMENTIERTEN Access-Nachrichten mit
    # SZMIC=1 (64-Bit TransMIC) muss es gesetzt sein, sonst schlaegt die
    # CCM-(Ent)schluesselung fehl. Fuer unsegmentierte Nachrichten ist es 0.
    # (Der Bug fiel beim Lesen der segmentierten Composition Data auf, siehe
    # read_composition.py.)
    return (bytes([nonce_type, (aszmic & 1) << 7]) + seq.to_bytes(3, "big")
            + src.to_bytes(2, "big") + dst.to_bytes(2, "big")
            + iv_index.to_bytes(4, "big"))


def encode_network_pdu(ctx: NetContext, ctl: int, ttl: int, seq: int,
                       src: int, dst: int, transport: bytes) -> bytes:
    ctl_ttl = (ctl << 7) | ttl
    mic_len = 8 if ctl else 4
    nonce = _net_nonce(ctl_ttl, seq, src, ctx.iv_index)
    enc = crypto.ccm_encrypt(ctx.enc_key, nonce,
                             dst.to_bytes(2, "big") + transport, mic_len)
    header = bytes([ctl_ttl]) + seq.to_bytes(3, "big") + src.to_bytes(2, "big")
    pecb = crypto.aes_ecb(ctx.priv_key, bytes(5)
                          + ctx.iv_index.to_bytes(4, "big") + enc[:7])
    obfuscated = bytes(a ^ b for a, b in zip(header, pecb[:6]))
    ivi_nid = ((ctx.iv_index & 1) << 7) | ctx.nid
    return bytes([ivi_nid]) + obfuscated + enc


def decode_network_pdu(ctx: NetContext, pdu: bytes):
    """-> (ctl, ttl, seq, src, dst, transport_pdu) oder None (fremdes Netz)."""
    if pdu[0] & 0x7F != ctx.nid:
        return None
    pecb = crypto.aes_ecb(ctx.priv_key, bytes(5)
                          + ctx.iv_index.to_bytes(4, "big") + pdu[7:14])
    header = bytes(a ^ b for a, b in zip(pdu[1:7], pecb[:6]))
    ctl_ttl = header[0]
    ctl, ttl = ctl_ttl >> 7, ctl_ttl & 0x7F
    seq = int.from_bytes(header[1:4], "big")
    src = int.from_bytes(header[4:6], "big")
    mic_len = 8 if ctl else 4
    nonce = _net_nonce(ctl_ttl, seq, src, ctx.iv_index)
    try:
        plain = crypto.ccm_decrypt(ctx.enc_key, nonce, pdu[7:], mic_len)
    except Exception:
        return None
    dst = int.from_bytes(plain[:2], "big")
    return ctl, ttl, seq, src, dst, plain[2:]


def encode_access(opcode: int, params: bytes) -> bytes:
    if opcode <= 0x7F:                       # 1-Byte-Opcode
        return bytes([opcode]) + params
    if opcode <= 0xFFFF:                      # 2-Byte-Opcode (0x80..)
        return opcode.to_bytes(2, "big") + params
    # 3-Byte Vendor-Opcode: [0b11xxxxxx][Company-ID little-endian]
    op_byte = (opcode >> 16) & 0xFF
    company = opcode & 0xFFFF
    return bytes([op_byte]) + company.to_bytes(2, "little") + params


def parse_access(payload: bytes):
    """-> (opcode, params)"""
    if payload[0] & 0x80 == 0:
        return payload[0], payload[1:]
    if payload[0] & 0xC0 == 0x80:
        return int.from_bytes(payload[:2], "big"), payload[2:]
    return int.from_bytes(payload[:3], "big"), payload[3:]


def build_transport_pdus(ctx: NetContext, key: bytes, is_app_key: bool,
                         seq_start: int, src: int, dst: int,
                         access_pdu: bytes):
    """Verschluesselt Access-PDU (App- oder DevKey) und baut Lower-Transport-
    PDUs. -> Liste von Transport-PDUs (1 = unsegmentiert)."""
    aid = crypto.k4(key) if is_app_key else 0
    akf = 0x40 if is_app_key else 0x00
    nonce_type = 0x01 if is_app_key else 0x02
    nonce = _app_nonce(nonce_type, seq_start, src, dst, ctx.iv_index)
    enc = crypto.ccm_encrypt(key, nonce, access_pdu, 4)

    if len(enc) <= 15:
        return [bytes([akf | aid]) + enc]

    seq_zero = seq_start & 0x1FFF
    segs = [enc[i:i + 12] for i in range(0, len(enc), 12)]
    seg_n = len(segs) - 1
    pdus = []
    for seg_o, seg in enumerate(segs):
        h = (seq_zero << 10) | (seg_o << 5) | seg_n   # szmic=0
        pdus.append(bytes([0x80 | akf | aid]) + h.to_bytes(3, "big") + seg)
    return pdus


def decrypt_access(ctx: NetContext, key: bytes, is_app_key: bool, seq: int,
                   src: int, dst: int, transport: bytes):
    """Entschluesselt eine unsegmentierte Access-Transport-PDU. -> Access-PDU
    oder None."""
    if transport[0] & 0x80:
        return None                       # segmentierte RX: nicht benoetigt
    nonce_type = 0x01 if is_app_key else 0x02
    nonce = _app_nonce(nonce_type, seq, src, dst, ctx.iv_index)
    try:
        return crypto.ccm_decrypt(key, nonce, transport[1:], 4)
    except Exception:
        return None
