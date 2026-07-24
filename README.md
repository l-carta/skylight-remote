# skylight-remote

Die **Philips Skylight** LED-Deckenleuchte per **Home Assistant** steuerbar
machen — obwohl sie ab Werk **kein** Smart-Home-Interface hat (kein Wi-Fi, kein
offizielles Zigbee/Matter, nur die mitgelieferte Fernbedienung).

Die Lampe ist ein **Bluetooth-SIG-Mesh**-Knoten (2,4 GHz, Telink-Chip). Dieses
Projekt implementiert einen eigenen, schlanken Mesh-Stack in Python, nimmt die
Lampe ins Netz (PB-GATT-Provisioning), verbindet sich als **Proxy** und
exponiert sie über **MQTT** an Home Assistant.

**Status:** ✅ läuft. An/Aus ist zuverlässig steuerbar — als CLI *und* als
MQTT-Bridge (systemd-Dienst auf einem Raspberry Pi 4). Siehe [Was geht (und
was nicht)](#was-geht-und-was-nicht).

---

## Das Gerät

| | |
|---|---|
| Produkt | Philips Skylight (Signify) |
| Funk | **Bluetooth LE / SIG Mesh, 2,4 GHz** |
| BLE-Name | `BK_MESH_light` |
| Chip | Telink |
| Provisioning | Mesh Provisioning Service `0x1827` |
| Proxy | Mesh Proxy Service `0x1828` |

Die Composition Data meldet Standard-SIG-Modelle (`Generic OnOff`,
`Light Lightness`, `Light CTL`, `Light HSL`, `Scene`, `Scheduler`, …) — aber
nur ein Teil davon wirkt an der realen Lampe.

## Was geht (und was nicht)

- ✅ **Generic OnOff** — an/aus, zuverlässig.
  **Firmware-Quirk:** OnOff ist **invertiert** — Wire `0x00` schaltet AN,
  `0x01` schaltet AUS (am Gerät verifiziert). Der Code kapselt das in
  `onoff_wire`/`onoff_phys`, nach außen ist alles normal.
- ❌ **Lightness / CTL / HSL / Level / Szenen** — die Firmware *quittiert* diese
  Nachrichten, **ignoriert sie aber**. Helligkeit, Weißton und Farbe laufen
  ausschließlich über ein proprietäres Telink-Vendor-Protokoll der
  Fernbedienung, das wir nicht erreichen. Deshalb exponieren wir die Lampe
  bewusst als **reines On/Off-Licht**.

## Architektur

```
Home Assistant ──MQTT──> skylight-remote (Raspberry Pi 4)
                              │  eigener SIG-Mesh-Stack (meshlib/)
                              ▼  BLE (Mesh Proxy 0x1828, via bleak)
                        BK_MESH_light (Telink, SIG Mesh)
```

Der Pi hält die Mesh-Keys (NetKey/AppKey/DevKey + Unicast-Adresse der Lampe in
`skylight-mesh.json`), verbindet sich als Proxy und mappt MQTT-Kommandos auf
Mesh-Nachrichten. Warum der Pi und kein Extra-Gateway: Home Assistant läuft eh
dort, BLE ist onboard.

---

## Benutzung

### CLI

```bash
python3 skylight.py on         # einschalten
python3 skylight.py off        # ausschalten
python3 skylight.py toggle     # umschalten
python3 skylight.py status     # aktuellen Zustand abfragen
python3 skylight.py scan       # BLE-Sicht + RSSI der Lampe
python3 skylight.py provision  # frisch ins Mesh aufnehmen
```

### MQTT-Bridge

`mqtt_bridge.py` hält eine dauerhafte Proxy-Verbindung und meldet die Lampe per
**HA-MQTT-Discovery** automatisch als `light.skylight` an (JSON-Schema, on/off).

| Topic | Payload | |
|---|---|---|
| `skylight/set` | `{"state": "ON"｜"OFF"}` | Kommando von HA |
| `skylight/state` | `{"state": "ON"｜"OFF"}` | Zustand (retained) |
| `skylight/availability` | `online` / `offline` | LWT (retained) |

Konfiguration über Env-Variablen: `MQTT_HOST` (default `127.0.0.1`),
`MQTT_USER` (default `skylight`), `MQTT_PASS` (default aus
`~/apps/mosquitto/mqtt-credentials.txt`), `POLL_INTERVAL` (default `0`).

**Zustandslogik — rein ereignisgesteuert:** Der Zustand wird nach jedem
Kommando gespeichert und zusätzlich einmalig bei jedem (Re-)Connect gelesen.
Das deckt zwei Fälle ohne Dauer-Poll ab: Schalten über HA/HomeKit (Trigger)
und **Stromausfall der Lampe** — dabei reißt die Proxy-Verbindung ab, und beim
Reconnect liest die Bridge den Ist-Zustand, der dann `ON` ist (physischer
Default der Lampe). Kommt gar keine Antwort mehr, gilt die Lampe als offline
(HA „unavailable", HomeKit „Reagiert nicht") statt auf veraltetem `ON` zu
hängen; Reads werden bei Paketverlust vorher mehrfach wiederholt.

Nur ein Ausschalten über die **Original-Fernbedienung** (kein Kommando, kein
Stromausfall) wird ereignisgesteuert nicht erkannt. Wer das zeitnah in HA sehen
will, setzt `POLL_INTERVAL` (Sekunden) > 0.

---

## Deployment (Raspberry Pi 4)

Der Dienst läuft auf `pi@raspberrypi.local` unter
`/home/pi/apps/skylight-remote` als systemd-Unit `skylight-bridge.service`.

```bash
# einmalig
sudo apt install -y python3-pip bluez
pip3 install bleak paho-mqtt
git clone https://github.com/l-carta/skylight-remote.git ~/apps/skylight-remote
sudo cp ~/apps/skylight-remote/skylight-bridge.service /etc/systemd/system/
sudo systemctl enable --now skylight-bridge

# updaten
cd ~/apps/skylight-remote && git pull && sudo systemctl restart skylight-bridge
```

`skylight-mesh.json` (Keys + Zählerstände) ist **gitignored** und bleibt bei
jedem Pull unberührt.

---

## Projektstruktur

| Datei | Zweck |
|---|---|
| `skylight.py` | CLI — on/off/toggle/status/scan/provision |
| `mqtt_bridge.py` | MQTT ↔ Mesh, HA-Discovery, systemd-Dienst |
| `provision.py` | Lampe frisch ins Mesh aufnehmen |
| `scan.py` | BLE-Scan, findet die Lampe |
| `meshlib/crypto.py` | Mesh-Krypto-Primitive (s1/k2, AES-CMAC/CCM) |
| `meshlib/network.py` | Network-/Transport-PDU-Kodierung, Segmentierung |
| `meshlib/proxy.py` | Mesh-Proxy-Verbindung über BLE (bleak) |
| `meshlib/provisioner.py` | PB-GATT-Provisioning |
| `meshlib/skylight.py` | `SkylightClient` — High-Level-Steuerung |
| `meshlib/state.py` | Laden/Speichern von `skylight-mesh.json` |
| `skylight-bridge.service` | systemd-Unit |
| `test_crypto.py` | Krypto-Tests (Bluetooth-Mesh-Spec-Testvektoren) |

---

## Betrieb & Fallstricke

**Replay-Protection:** Die Lampe verwirft Mesh-Nachrichten mit einer bereits
gesehenen Sequenznummer **stillschweigend**. Nach einem harten Stromausfall
kann die gespeicherte `seq` hinter dem Stand der Lampe liegen → die Lampe
reagiert nicht mehr. Gegenmaßnahmen im Code:

- `mqtt_bridge.py` persistiert die `seq` nach **jedem Poll** (nicht nur beim
  Disconnect).
- `state.py` springt beim Laden zusätzlich um `SEQ_SAFETY_JUMP = 512` nach vorn.

Tritt es trotzdem auf: `seq` in `skylight-mesh.json` weit nach vorn setzen (der
24-Bit-Raum reicht bis 16,7 Mio) und den Dienst neu starten.

## Sicherheit

`skylight-mesh.json` enthält NetKey/AppKey/DevKey der Lampe → geheim halten,
**niemals committen** (steht in `.gitignore`). Wer die Lampe neu ins eigene
Netz provisioniert, kann der Original-Fernbedienung vorübergehend die Kontrolle
entziehen; ein Factory-Reset der Lampe stellt das wieder her.

---

## Die Reise (damit „future me" die Sackgassen kennt)

**Sackgasse 433 MHz ❌** — Erste Annahme: die Remote sendet auf 433,92 MHz OOK,
also fangen wir das Signal mit einem CC1101 ab und spielen es nach. Aufbau mit
Arduino MKR + CC1101. Gelernt: erstes Modul war 868 MHz (am 433-Band taub),
zweites hatte einen wackligen SMA-Kontakt — und der eigentliche Grund, warum nie
ein sauberes Signal kam: die „Ausschläge" waren **Fremdfunk**. Die Skylight
sendet gar nicht auf 433. **Lehre:** erst die Funktechnik verifizieren, dann
Hardware kaufen — ein BLE-Scan am Anfang hätte Stunden gespart.

**Durchbruch: es ist Bluetooth ✅** — Ein BLE-Scan zeigt sofort `BK_MESH_light`
mit dem Mesh-Provisioning-Service `0x1827`. Erst mit der App **nRF Mesh**
provisioniert und On/Off bestätigt, dann den Mesh-Stack selbst in Python
nachgebaut (`meshlib/`) — inklusive eigenem PB-GATT-Provisioning, sodass wir
kein fremdes Tool mehr brauchen.
