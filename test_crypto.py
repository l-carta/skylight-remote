"""Krypto-Selbsttest gegen die Sample-Daten aus Mesh Profile Spec 1.0.1 Kap. 8."""

from meshlib import crypto


def test_s1():
    assert crypto.s1(b"test").hex() == "b73cefbd641ef2ea598c2b6efb62f79c"


def test_k2_master():
    n = bytes.fromhex("f7a2a44f8e8a8029064f173ddc1e2b00")
    nid, enc, priv = crypto.k2(n, b"\x00")
    assert nid == 0x7F
    assert enc.hex() == "9f589181a0f50de73c8070c7a6d27f46"
    assert priv.hex() == "4c715bd4a64b938f99b453351653124f"


def test_k3():
    n = bytes.fromhex("f7a2a44f8e8a8029064f173ddc1e2b00")
    assert crypto.k3(n).hex() == "ff046958233db014"


def test_k4():
    n = bytes.fromhex("3216d1509884b533248541792b877f98")
    assert crypto.k4(n) == 0x38


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"OK {name}")
    print("Alle Krypto-Tests bestanden.")
