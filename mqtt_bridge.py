#!/usr/bin/env python3
"""MQTT-Bridge: Home Assistant <-> Skylight (BLE Mesh).

Haelt eine dauerhafte Proxy-Verbindung zur Lampe, meldet sich per
MQTT-Discovery bei HA als schaltbares Licht an und setzt HA-Kommandos
(on/off) in Mesh-Nachrichten um. Zustand wird zurueckgemeldet.

Die Skylight kann von aussen nur an/aus (siehe README) - daher exponieren
wir sie bewusst als reines On/Off-Licht, damit HA nichts Nutzloses anzeigt.

    MQTT_HOST (default 127.0.0.1), MQTT_USER (default skylight),
    MQTT_PASS (default: aus ~/apps/mosquitto/mqtt-credentials.txt)
"""

import asyncio
import json
import os
import time

import paho.mqtt.client as mqtt

from meshlib.skylight import SkylightClient
from meshlib.state import load_cfg, save_cfg
from meshlib.skylight import CONFIG_FILE

TOPIC_SET = "skylight/set"
TOPIC_STATE = "skylight/state"
TOPIC_AVAIL = "skylight/availability"
DISCOVERY_TOPIC = "homeassistant/light/skylight/config"

# Standardmaessig rein ereignisgesteuert (0 = kein Poll): Zustand wird nach
# jedem Kommando und einmalig bei jedem (Re-)Connect gespeichert. Das deckt
# Command-Aenderungen und Stromausfall (Lampe kommt AN zurueck) ohne BLE-Dauer-
# last ab. Nur wer ein Ausschalten per Original-Fernbedienung zeitnah in HA
# sehen will, setzt POLL_INTERVAL (Sekunden) > 0.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "0"))
RECONNECT_DELAY = 5


def load_mqtt_pass() -> str:
    if os.environ.get("MQTT_PASS"):
        return os.environ["MQTT_PASS"]
    path = os.path.expanduser("~/apps/mosquitto/mqtt-credentials.txt")
    with open(path) as f:
        return f.read().strip()


class Bridge:
    def __init__(self):
        self.cfg = load_cfg(CONFIG_FILE)
        self.loop = None
        self.cmd_queue: asyncio.Queue = asyncio.Queue()
        self.state = "OFF"

        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                              client_id="skylight-bridge")
        self.mq.username_pw_set(os.environ.get("MQTT_USER", "skylight"),
                                load_mqtt_pass())
        self.mq.will_set(TOPIC_AVAIL, "offline", retain=True)
        self.mq.on_connect = self._on_connect
        self.mq.on_message = self._on_message

    # ---------- MQTT (paho-Thread) ----------

    def _on_connect(self, client, userdata, flags, reason, props):
        print(f"MQTT verbunden ({reason})", flush=True)
        client.subscribe(TOPIC_SET)
        client.publish(DISCOVERY_TOPIC, json.dumps({
            "name": "Skylight",
            "unique_id": "skylight_" + self.cfg["mac"].replace(":", "").lower(),
            "schema": "json",
            "command_topic": TOPIC_SET,
            "state_topic": TOPIC_STATE,
            "availability_topic": TOPIC_AVAIL,
            "device": {
                "identifiers": ["skylight"],
                "name": "Philips Skylight",
                "manufacturer": "Signify",
                "model": "Skylight (Telink, Bluetooth SIG Mesh)",
            },
        }), retain=True)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            print(f"Ungueltiges JSON: {msg.payload!r}", flush=True)
            return
        if "state" in payload:
            asyncio.run_coroutine_threadsafe(
                self.cmd_queue.put(payload["state"] == "ON"), self.loop)

    def _publish_state(self):
        self.mq.publish(TOPIC_STATE, json.dumps({"state": self.state}),
                        retain=True)

    # ---------- Hauptschleife ----------

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.mq.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), 1883, 60)
        self.mq.loop_start()

        while True:
            try:
                async with SkylightClient(self.cfg, log=lambda *_: None) as sky:
                    print("Mesh-Proxy verbunden", flush=True)
                    self.mq.publish(TOPIC_AVAIL, "online", retain=True)
                    # Einmaliger Read pro (Re-)Connect: reicht ereignisgesteuert
                    # voellig aus. Nach einem Stromausfall der Lampe reisst die
                    # Verbindung ab; beim Reconnect lesen wir den Ist-Zustand
                    # (dann AN, der physische Default) - ohne Dauer-Poll.
                    self.state = "ON" if await sky.get_power() else "OFF"
                    self._publish_state()
                    sky.save()
                    last_poll = time.monotonic()

                    while True:
                        # POLL_INTERVAL=0 -> rein ereignisgesteuert: wir warten
                        # unbegrenzt auf das naechste Kommando (kein Poll).
                        timeout = None
                        if POLL_INTERVAL > 0:
                            timeout = max(1.0, POLL_INTERVAL
                                          - (time.monotonic() - last_poll))
                        try:
                            want_on = await asyncio.wait_for(
                                self.cmd_queue.get(), timeout=timeout)
                            on = await sky.set_power(want_on)
                            self.state = "ON" if on else "OFF"
                            self._publish_state()
                            # Poll-Fenster auch nach einem Kommando neu
                            # aufziehen. Sonst steht der naechste Poll sofort
                            # an (last_poll waere uralt -> Timeout 1s) und
                            # liest die Lampe MITTEN im Dimm-Uebergang; dieser
                            # Zwischenwert ueberschreibt dann den gerade
                            # korrekt gemeldeten Zustand.
                            last_poll = time.monotonic()
                        except asyncio.TimeoutError:
                            on = await sky.get_power()
                            self.state = "ON" if on else "OFF"
                            self._publish_state()
                            last_poll = time.monotonic()
                        # Seq-Nummer sofort persistieren: nach hartem
                        # Stromausfall darf die Datei nie weiter als den
                        # SEQ_SAFETY_JUMP hinter der Lampe liegen, sonst
                        # verwirft deren Replay-Protection alles.
                        sky.save()
            except Exception as e:
                print(f"Mesh-Verbindung verloren: {e!r} - reconnect in "
                      f"{RECONNECT_DELAY}s", flush=True)
                self.mq.publish(TOPIC_AVAIL, "offline", retain=True)
                save_cfg(CONFIG_FILE, self.cfg)
                await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(Bridge().run())
