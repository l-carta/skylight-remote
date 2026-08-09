"""Skylight-Client: kapselt die Mesh-Steuerung der Lampe an einer Stelle.

Wird von der CLI (skylight.py) und der MQTT-Bridge (mqtt_bridge.py) genutzt.

Bewusst schlank: An der realen Lampe wirkt nur Generic OnOff (und das
INVERTIERT). Lightness/CTL/Level/Szenen werden von der Firmware quittiert,
aber ignoriert - die Modi laufen ausschliesslich ueber das proprietaere
Telink-Vendor-Protokoll der Fernbedienung (nicht erreichbar). Details siehe
README, Abschnitt "Was geht (und was nicht)".
"""

import os

from . import network
from .proxy import MeshProxy
from .state import load_cfg, save_cfg

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "skylight-mesh.json")

OP_ONOFF_GET = 0x8201
OP_ONOFF_SET = 0x8202
OP_ONOFF_STATUS = 0x8204

# BLE-Writes (ohne Response) und Mesh-Nachrichten koennen verloren gehen.
# Statt beim ersten Ausbleiben die ganze Proxy-Verbindung fallenzulassen (und
# HA/HomeKit auf einem veralteten "AN" sitzen zu lassen), senden wir die
# Anfrage mehrfach mit kurzem Timeout erneut, bevor wir aufgeben.
STATUS_TIMEOUT = 3.0   # Sekunden pro Versuch
STATUS_ATTEMPTS = 3    # Sendungen, bis wir aufgeben

# Firmware-Quirk (am Geraet gemessen, Wahrheitstabelle: research/onoff_truth.py).
# Die beiden Richtungen benutzen NICHT dieselbe Kodierung:
#
#   SET   Wire 0x00 schaltet AN, 0x01 schaltet AUS -- invertiert. Die
#         Status-Antwort auf ein SET spiegelt nur das gesendete Wire-Byte
#         zurueck (SET 0x00 -> [00 00 41]), ist also ebenfalls invertiert und
#         sagt nichts ueber den tatsaechlichen Zustand aus.
#   GET   Die Status-Antwort auf ein GET meldet den echten Zustand dagegen
#         STANDARDKONFORM: 0x01 = an, 0x00 = aus.
#
# Frueher wurde fuer beide Richtungen invertiert. Schalten funktionierte
# dadurch, Lesen lieferte aber konsequent das Gegenteil -- Lampe aus, GET
# liefert 0x00, invertiert -> HA zeigte "an".
ONOFF_SET_INVERTED = True

# Uebergangszeit (Fade). Ein Generic OnOff Set darf hinter OnOff und TID zwei
# OPTIONALE Bytes fuehren: Transition Time und Delay. Ohne sie nimmt der Server
# seine eigene Default Transition Time -- und die ist hier nicht 0: Die Lampe
# quittiert ein SET mit [present, target, remaining] und meldet als remaining
# 0x41, also "Aufloesung 1 s, 1 Schritt". Sie faehrt die Helligkeit hoch, statt
# zu schalten. Am Helligkeitssensor im Bad gemessen (2026-08-09): nach dem
# quittierten "an" stieg die Beleuchtungsstaerke ueber rund 3 s von 0 auf 65 lx.
#
# Genau das ist die Wartezeit, die als "die Lampe reagiert nicht sofort"
# ankommt -- die Schaltkette davor braucht nur ~170 ms (Sensor -> Lampe quittiert).
#
# SKYLIGHT_TRANSITION_MS steuert das: 0 = sofort (Default), sonst Millisekunden.
# "default" laesst die Felder weg und ueberlaesst der Lampe ihre Vorgabe -- der
# Stand vor dieser Aenderung, als Rueckfallebene.
TRANSITION_MS = os.environ.get("SKYLIGHT_TRANSITION_MS", "0")


def transition_wire(ms: int) -> int:
    """Millisekunden -> Transition-Time-Byte (Mesh 3.1.3).

    Bits 5-0 Schrittzahl, Bits 7-6 Aufloesung (100 ms / 1 s / 10 s / 10 min).
    0x00 = null Schritte = sofort, ohne Uebergang.
    """
    for res_bits, step_ms in ((0b00, 100), (0b01, 1000), (0b10, 10_000),
                              (0b11, 600_000)):
        steps = round(ms / step_ms)
        if steps <= 62:
            return (res_bits << 6) | steps
    return 0b11 << 6 | 62  # laenger als 620 min geht nicht


def transition_params() -> bytes:
    """Die beiden optionalen Bytes -- oder leer, wenn die Lampe entscheiden soll."""
    if TRANSITION_MS.strip().lower() in ("default", "none", ""):
        return b""
    # Delay (Wartezeit VOR dem Uebergang) bleibt 0: Wir wollen frueher fertig
    # sein, nicht spaeter anfangen.
    return bytes([transition_wire(int(TRANSITION_MS)), 0x00])


def onoff_wire(on: bool) -> int:
    """Parameter-Byte fuer ein OnOff-SET."""
    return int(on ^ ONOFF_SET_INVERTED)


def onoff_echo(wire: int) -> bool:
    """Status-Antwort auf ein SET -- gespiegeltes Wire-Byte, also invertiert."""
    return bool(wire) ^ ONOFF_SET_INVERTED


def onoff_phys(wire: int) -> bool:
    """Status-Antwort auf ein GET -- echter Zustand, standardkonform."""
    return bool(wire)


class SkylightClient:
    """Async-Context-Manager fuer eine Steuerungssitzung mit der Lampe."""

    def __init__(self, cfg: dict | None = None, log=lambda *_: None):
        self.cfg = cfg or load_cfg(CONFIG_FILE)
        self.ctx = network.NetContext(bytes.fromhex(self.cfg["net_key"]),
                                      self.cfg["iv_index"])
        self.app_key = bytes.fromhex(self.cfg["app_key"])
        self.dst = self.cfg["unicast"]
        self.log = log
        self._proxy = None
        # Restlaufzeit des Uebergangs aus der letzten SET-Quittung (roh).
        self.last_remaining = None

    async def __aenter__(self):
        self._proxy = MeshProxy(self.cfg["mac"], self.ctx, self.cfg["src"],
                                log=self.log)
        await self._proxy.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._proxy.__aexit__(*exc)
        self.save()

    def save(self):
        save_cfg(CONFIG_FILE, self.cfg)

    def _next_tid(self) -> int:
        self.cfg["tid"] = (self.cfg["tid"] + 1) & 0xFF
        return self.cfg["tid"]

    async def _request(self, opcode: int, params: bytes) -> bytes:
        """Sendet eine OnOff-Nachricht und wartet robust auf den Status.

        Bleibt die Antwort aus, wird erneut gesendet (bis STATUS_ATTEMPTS).
        Erst wenn keiner der Versuche eine Antwort bringt, wird der Fehler
        durchgereicht - dann ist die Verbindung wirklich weg und der Aufrufer
        markiert die Lampe als offline, statt einen falschen Zustand zu halten.
        params fuer OnOff-SET enthaelt eine TID; wir behalten sie ueber alle
        Versuche bei, damit der Server Wiederholungen als Duplikat erkennt und
        nicht doppelt schaltet.
        """
        last_err = None
        for _ in range(STATUS_ATTEMPTS):
            await self._proxy.send_access(
                self.cfg, self.app_key, True, self.dst, opcode, params)
            try:
                return await self._proxy.wait_status(
                    self.app_key, True, OP_ONOFF_STATUS,
                    timeout=STATUS_TIMEOUT)
            except TimeoutError as e:
                last_err = e
        raise last_err

    async def set_power(self, on: bool) -> bool:
        """Schaltet an/aus, wartet auf Status. -> quittierter Zustand."""
        params = await self._request(
            OP_ONOFF_SET,
            bytes([onoff_wire(on), self._next_tid()]) + transition_params())
        # Status: present(1) [, target(1), remaining(1)]. Bei laufendem
        # Uebergang ist der Ziel-Zustand massgeblich, nicht der Momentanwert.
        # Achtung: Das ist die Quittung des Wire-Bytes, kein Messwert -- die
        # Firmware spiegelt hier nur zurueck, was wir gesendet haben.
        #
        # remaining ist dagegen aussagekraeftig: Es ist die Restzeit des
        # Uebergangs, den die Lampe tatsaechlich faehrt. Bleibt es trotz
        # gesetzter Transition Time bei 0x41, hat die Firmware das Feld
        # ignoriert -- dann ist der Fade nicht per Mesh abstellbar.
        if len(params) >= 3:
            self.last_remaining = params[2]
        return onoff_echo(params[1] if len(params) >= 3 else params[0])

    async def get_power(self) -> bool:
        """Fragt den aktuellen Zustand ab. -> True=an."""
        params = await self._request(OP_ONOFF_GET, b"")
        return onoff_phys(params[0])
