#!/usr/bin/env python3
"""
Frage: Laesst sich der Fade der Lampe per Mesh abstellen?

Die Lampe schaltet nicht, sie faehrt hoch. Gemessen am Helligkeitssensor im
Bad (2026-08-09): nach dem quittierten "an" stieg die Beleuchtungsstaerke
ueber rund 3 s von 0 auf 65 lx. Die Schaltkette davor braucht nur ~170 ms --
der Fade ist also praktisch die gesamte spuerbare Wartezeit.

Ein Generic OnOff Set darf hinter OnOff und TID zwei OPTIONALE Bytes fuehren:
Transition Time und Delay. Werden sie weggelassen -- wie bisher --, nimmt der
Server seine eigene Default Transition Time.

Dass die Firmware Uebergaenge ueberhaupt kennt, sagt sie selbst: Die Quittung
auf ein SET ist [present, target, remaining], und remaining ist 0x41 --
Aufloesung 1 s, 1 Schritt. Dieses Byte ist hier das Messinstrument:

    remaining == 0x00  ->  kein Uebergang mehr, das Feld wirkt
    remaining == 0x41  ->  unveraendert, die Firmware ignoriert das Feld

ACHTUNG: Die README notiert unter "On/Off-Pfad in allen Varianten
(... Transition-Bytes) -> nichts". Das war eine andere Frage: Dort wurde
gesucht, ob die Extra-Bytes einen MODUS umschalten, mit den Werten 0x01-0x06
und per Augenschein. Der Wert 0x00 kam nie vor, und die Uebergangszeit als
Uebergangszeit wurde nie gemessen. Beides holt dieses Skript nach.

Die Bridge muss gestoppt sein -- sie haelt sonst die BLE-Verbindung:
    sudo systemctl stop skylight-bridge
    python3 research/transition_probe.py
    sudo systemctl start skylight-bridge

Die Lampe schaltet dabei mehrfach an und aus.
"""

# --- Pfad-Bootstrap: dieses Tool liegt in research/, der Stack + die
# Config (skylight-mesh.json) liegen im Repo-Root eine Ebene hoeher. ---
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)

import asyncio
import time

from meshlib import skylight as sky_mod
from meshlib.skylight import SkylightClient, transition_wire

# (Label, Wert fuer SKYLIGHT_TRANSITION_MS)
FAELLE = [
    ("bisher (Felder weggelassen)", "default"),
    ("sofort (0 ms)", "0"),
    ("kurzer Fade (300 ms)", "300"),
]


def deute(b):
    """Transition-Time-Byte -> lesbar."""
    if b is None:
        return "keine Angabe (Quittung war kuerzer als 3 Bytes)"
    schritte = b & 0x3F
    ms = {0b00: 100, 0b01: 1000, 0b10: 10_000, 0b11: 600_000}[b >> 6]
    if schritte == 0x3F:
        return f"0x{b:02x} = unbekannt"
    return f"0x{b:02x} = {schritte} x {ms} ms = {schritte * ms / 1000:g} s"


async def main():
    print("Bridge gestoppt? Die Lampe schaltet gleich mehrfach.\n")
    async with SkylightClient() as sky:
        for label, wert in FAELLE:
            sky_mod.TRANSITION_MS = wert
            gesendet = sky_mod.transition_params()
            print(f"=== {label} ===")
            print(f"    angehaengte Bytes: "
                  f"{gesendet.hex() if gesendet else '(keine)'}")
            if wert not in ("default",):
                print(f"    davon Transition Time: "
                      f"{deute(transition_wire(int(wert)))}")

            for ziel in (True, False):
                t0 = time.monotonic()
                sky.last_remaining = None
                await sky.set_power(ziel)
                dt = (time.monotonic() - t0) * 1000
                print(f"    {'AN ' if ziel else 'AUS'} quittiert nach "
                      f"{dt:.0f} ms | remaining: {deute(sky.last_remaining)}")
                # Uebergang auslaufen lassen, sonst misst der naechste Fall
                # in eine noch laufende Rampe hinein.
                await asyncio.sleep(4)
            print()

    print("Auswertung: Faellt remaining bei '0 ms' auf 0x00, wirkt das Feld "
          "und der Fade ist abstellbar.\nBleibt es bei 0x41, ignoriert die "
          "Firmware es -- dann ist der Fade per Mesh nicht erreichbar.")


if __name__ == "__main__":
    asyncio.run(main())
