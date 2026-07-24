"""PB-GATT-Provisioner: nimmt ein unprovisioniertes Gerät per GATT ins Netz auf.

Ablauf (Mesh Profile Spec Kap. 5): Invite -> Capabilities -> Start(No OOB) ->
ECDH-Public-Keys -> Confirmation/Random -> verschlüsselte Provisioning Data
(NetKey, Unicast) -> Complete. Liefert den DevKey des Geräts zurück.
"""

import asyncio

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from . import crypto

PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"
PROV_DATA_IN = "00002adb-0000-1000-8000-00805f9b34fb"
PROV_DATA_OUT = "00002adc-0000-1000-8000-00805f9b34fb"

PDU_INVITE, PDU_CAPS, PDU_START, PDU_PUBKEY = 0x00, 0x01, 0x02, 0x03
PDU_CONFIRM, PDU_RANDOM, PDU_DATA, PDU_COMPLETE, PDU_FAILED = (
    0x05, 0x06, 0x07, 0x08, 0x09)


class ProvisioningError(Exception):
    pass


def _sar_split(pdu: bytes, mtu: int) -> list[bytes]:
    """Proxy-PDU-Framing (Typ 0x03 = Provisioning) inkl. Segmentierung."""
    payload_max = mtu - 1
    if len(pdu) <= payload_max:
        return [bytes([0x03]) + pdu]
    chunks = [pdu[i:i + payload_max] for i in range(0, len(pdu), payload_max)]
    out = []
    for i, c in enumerate(chunks):
        if i == 0:
            sar = 0x40
        elif i == len(chunks) - 1:
            sar = 0xC0
        else:
            sar = 0x80
        out.append(bytes([sar | 0x03]) + c)
    return out


class SarReassembler:
    def __init__(self):
        self.buf = b""

    def feed(self, frame: bytes):
        """-> komplette PDU oder None, wenn noch Segmente fehlen."""
        sar, payload = frame[0] >> 6, frame[1:]
        if sar == 0:                       # komplett
            return payload
        if sar == 1:                       # erstes Segment
            self.buf = payload
            return None
        self.buf += payload
        if sar == 3:                       # letztes Segment
            out, self.buf = self.buf, b""
            return out
        return None


async def provision(client, net_key: bytes, key_index: int, iv_index: int,
                    unicast: int, log=print) -> bytes:
    """Provisioniert das verbundene Gerät. -> DevKey (16 Byte)."""
    rx_queue: asyncio.Queue[bytes] = asyncio.Queue()
    sar = SarReassembler()

    def on_notify(_, data: bytearray):
        pdu = sar.feed(bytes(data))
        if pdu is not None:
            rx_queue.put_nowait(pdu)

    await client.start_notify(PROV_DATA_OUT, on_notify)
    mtu = client.mtu_size or 23

    async def send(pdu: bytes):
        for frame in _sar_split(pdu, mtu - 3):
            await client.write_gatt_char(PROV_DATA_IN, frame, response=False)

    async def recv(expected: int) -> bytes:
        pdu = await asyncio.wait_for(rx_queue.get(), timeout=30)
        if pdu[0] == PDU_FAILED:
            raise ProvisioningError(f"Geraet meldet Provisioning Failed, "
                                    f"Grund 0x{pdu[1]:02x}")
        if pdu[0] != expected:
            raise ProvisioningError(f"PDU 0x{pdu[0]:02x} statt 0x{expected:02x}")
        return pdu[1:]

    # 1) Invite / Capabilities
    invite = bytes([0x00])                          # attention timer 0
    await send(bytes([PDU_INVITE]) + invite)
    caps = await recv(PDU_CAPS)
    log(f"Capabilities: elemente={caps[0]} algorithmen=0x{caps[1]:02x}{caps[2]:02x}")
    if not caps[2] & 0x01:
        raise ProvisioningError("Geraet kann kein FIPS P-256 (No-OOB)")

    # 2) Start: Algorithm P-256, kein OOB-Public-Key, No-OOB-Auth
    start = bytes([0x00, 0x00, 0x00, 0x00, 0x00])
    await send(bytes([PDU_START]) + start)

    # 3) ECDH Public Keys
    own_key = ec.generate_private_key(ec.SECP256R1())
    nums = own_key.public_key().public_numbers()
    own_pub = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    await send(bytes([PDU_PUBKEY]) + own_pub)
    dev_pub = await recv(PDU_PUBKEY)
    peer = ec.EllipticCurvePublicNumbers(
        int.from_bytes(dev_pub[:32], "big"),
        int.from_bytes(dev_pub[32:], "big"), ec.SECP256R1()).public_key()
    secret = own_key.exchange(ec.ECDH(), peer)
    log("ECDH-Schluesselaustausch ok")

    # 4) Confirmation / Random (Auth-Wert = 0, No OOB)
    conf_inputs = invite + caps + start + own_pub + dev_pub
    conf_salt = crypto.s1(conf_inputs)
    conf_key = crypto.k1(secret, conf_salt, b"prck")
    auth = bytes(16)
    import os
    own_random = os.urandom(16)
    own_conf = crypto.aes_cmac(conf_key, own_random + auth)
    await send(bytes([PDU_CONFIRM]) + own_conf)
    dev_conf = await recv(PDU_CONFIRM)
    await send(bytes([PDU_RANDOM]) + own_random)
    dev_random = await recv(PDU_RANDOM)
    if crypto.aes_cmac(conf_key, dev_random + auth) != dev_conf:
        raise ProvisioningError("Confirmation-Pruefung fehlgeschlagen")
    log("Authentisierung ok")

    # 5) Provisioning Data
    prov_salt = crypto.s1(conf_salt + own_random + dev_random)
    session_key = crypto.k1(secret, prov_salt, b"prsk")
    session_nonce = crypto.k1(secret, prov_salt, b"prsn")[3:]
    dev_key = crypto.k1(secret, prov_salt, b"prdk")
    data = (net_key + key_index.to_bytes(2, "big") + bytes([0x00])
            + iv_index.to_bytes(4, "big") + unicast.to_bytes(2, "big"))
    enc = crypto.ccm_encrypt(session_key, session_nonce, data, 8)
    await send(bytes([PDU_DATA]) + enc)
    await recv(PDU_COMPLETE)
    log(f"Provisioning abgeschlossen, Unicast 0x{unicast:04x}")

    await client.stop_notify(PROV_DATA_OUT)
    return dev_key
