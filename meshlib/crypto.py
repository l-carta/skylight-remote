"""Bluetooth-Mesh-Kryptografie (Mesh Profile Spec 1.0.1, Kap. 3.8/3.9).

Alle Funktionen sind gegen die Sample-Daten aus Kap. 8 der Spec testbar
(siehe test_crypto.py).
"""

from cryptography.hazmat.primitives.cmac import CMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

ZERO16 = bytes(16)


def aes_cmac(key: bytes, msg: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def aes_ecb(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def s1(m: bytes) -> bytes:
    return aes_cmac(ZERO16, m)


def k1(n: bytes, salt: bytes, p: bytes) -> bytes:
    return aes_cmac(aes_cmac(salt, n), p)


def k2(n: bytes, p: bytes) -> tuple[int, bytes, bytes]:
    """-> (nid, encryption_key, privacy_key)"""
    t = aes_cmac(s1(b"smk2"), n)
    t1 = aes_cmac(t, p + b"\x01")
    t2 = aes_cmac(t, t1 + p + b"\x02")
    t3 = aes_cmac(t, t2 + p + b"\x03")
    return t1[15] & 0x7F, t2, t3


def k3(n: bytes) -> bytes:
    """-> 8-Byte Network ID"""
    t = aes_cmac(s1(b"smk3"), n)
    return aes_cmac(t, b"id64\x01")[8:]


def k4(n: bytes) -> int:
    """-> 6-Bit AID des AppKeys"""
    t = aes_cmac(s1(b"smk4"), n)
    return aes_cmac(t, b"id6\x01")[15] & 0x3F


def ccm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, mic_len: int,
                aad: bytes = b"") -> bytes:
    return AESCCM(key, tag_length=mic_len).encrypt(nonce, plaintext, aad)


def ccm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, mic_len: int,
                aad: bytes = b"") -> bytes:
    return AESCCM(key, tag_length=mic_len).decrypt(nonce, ciphertext, aad)
