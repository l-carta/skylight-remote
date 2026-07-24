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

# Firmware-Quirk (am Geraet verifiziert 2026-07-23): Generic OnOff invertiert.
# Wire 0x00 schaltet AN, 0x01 schaltet AUS - inkl. der Status-Antworten.
ONOFF_INVERTED = True


def onoff_wire(on: bool) -> int:
    return int(on ^ ONOFF_INVERTED)


def onoff_phys(wire: int) -> bool:
    return bool(wire) ^ ONOFF_INVERTED


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

    async def set_power(self, on: bool) -> bool:
        """Schaltet an/aus, wartet auf Status. -> tatsaechlicher Zustand."""
        await self._proxy.send_access(
            self.cfg, self.app_key, True, self.dst, OP_ONOFF_SET,
            bytes([onoff_wire(on), self._next_tid()]))
        params = await self._proxy.wait_status(
            self.app_key, True, OP_ONOFF_STATUS)
        # Status: present(1) [, target(1), remaining(1)]. Bei laufendem
        # Uebergang ist der Ziel-Zustand massgeblich, nicht der Momentanwert.
        return onoff_phys(params[1] if len(params) >= 3 else params[0])

    async def get_power(self) -> bool:
        """Fragt den aktuellen Zustand ab. -> True=an."""
        await self._proxy.send_access(
            self.cfg, self.app_key, True, self.dst, OP_ONOFF_GET, b"")
        params = await self._proxy.wait_status(
            self.app_key, True, OP_ONOFF_STATUS)
        return onoff_phys(params[0])
