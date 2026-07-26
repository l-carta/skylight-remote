# research/ — Diagnose- & Reverse-Engineering-Tools

Diese Skripte sind bei der (erfolglosen) Jagd nach **Helligkeits-/Farb-/Mode-
Steuerung** der Skylight entstanden. Für den **Normalbetrieb sind sie nicht
nötig** — On/Off läuft über den Kern-Code im Repo-Root (`skylight.py`,
`mqtt_bridge.py`). Hier liegt das Werkzeug für Analyse und einen späteren
Firmware-Dump-Anlauf. Hintergrund & Fazit: siehe [Haupt-README, „Die Reise"](../README.md).

## Ausführen

**Immer aus dem Repo-Root** starten (nicht aus `research/`), damit die
Config gefunden wird:

```bash
cd ~/apps/skylight-remote
python3 research/read_composition.py
```

Ein Pfad-Bootstrap oben in jedem Tool hängt das Repo-Root an `sys.path`, sodass
`from meshlib import …` und `skylight-mesh.json` sauber gefunden werden.

**Wichtig:** Die meisten Mesh-Tools brauchen die **Proxy-Verbindung exklusiv** —
vorher die Bridge stoppen:

```bash
sudo systemctl stop skylight-bridge
python3 research/<tool>.py
sudo systemctl start skylight-bridge
```

## Übersicht

| Tool | Zweck |
|---|---|
| `read_composition.py` | Composition Data auslesen (mit Segment-Reassembly) |
| `model_probe.py` | alle SIG-Modelle binden + SET-Befehle testen |
| `scene_probe.py` | Scene-Modell: gespeicherte Szenen listen + Recall |
| `vendor_probe.py` | Vendor-Modell binden + Opcodes senden/dekodieren |
| `vendor_sweep.py` | Vendor-Opcode-Bereich mit Kontrast-Payloads durchfahren |
| `vendor_attr2.py` | Attribut-Probe mit **korrekter** SDK-Struktur `[tid][attr 2B LE][value]`, sucht `ATTR_STATUS 0xD3` |
| `vendor_rc_sweep.py` | `VD_RC_KEY_REPORT 0xC0` korrekt (8-Byte-Payload) emulieren, Code-Sweep 0x00–0xFF |
| `final_probe.py` | „offene Ecken": Power-Level, RC-Key-Sweep, 0xFDA0-Writes |
| `onoff_modes.py` | OnOff-Pfad-Varianten als Mode-Selektor testen |
| `gatt_enum.py` | GATT-Services/Characteristics der Lampe auflisten |
| `fda0_probe.py` | das custom 0xFDA0-Service auslesen |
| `mesh_monitor.py` | passiver, **ungefilterter** Mesh-Mitschnitt (jede Element-Adresse + Control), optional mit On/Off-Toggle zur Provokation |
| `scan_all.py` | breiter BLE-Scan — findet die Remote / `0x1827`-Advertisements (koppelbare Geräte) |
| `dump_lamp_adv.py` | echtes Advertising der Lampe mitschneiden |
| `sniff_mesh.py` | Mesh-Adv passiv mitschneiden (btmon → BTSnoop-Parse) |
| `decode_capture.py` | Mitschnitt mit NetKey+AppKey voll entschlüsseln |
| `bruteforce_netkey.py` | NetKey-Kandidaten gegen eine Network-PDU testen |
| `netid_crack.py` | Default-NetKeys gegen eine bekannte Network-ID prüfen |
| `imp_lamp.py` | Pi als Fake-Lampe (bless GATT-Server) |
| `imp_capture.sh` / `imp_capture2.sh` | Fake-Lampe + MAC-Spoof + Cleanup-Orchestrierung |
| `node_reset.py` | `Config Node Reset 0x8049` — Lampe aus unserem Netz nehmen (wird wieder koppelbar) |
| `remote_probe.py` | mit der Remote (Proxy-Server) verbinden + alle lesbaren GATT-Chars/Adv auslesen |
| `remote_ffc0.py` | TI-OAD-Service `f000ffc0` der Remote untersuchen (read-only) |
| `remote_listen.py` | Proxy der Remote abonnieren (`2ade`) + mitschneiden (Werks-Key-verschlüsselt) |
| `imp_prov.py` | Pi als **unprovisionierte** Fake-Lampe (`0x1827`, btmgmt-Adv, UUID aus Adapter-MAC) — loggt Provisioning-`INVITE` |
| `imp_capture_prov.sh` | `imp_prov.py` + MAC-Spoof auf die Lampen-MAC + Cleanup |
| `pbadv_probe.sh` | Raw-HCI PB-ADV-Beacon (`0x2B`) senden + scannen (optional MAC-Spoof); prüft, ob die Remote per PB-ADV provisioniert |

Selbsttests ohne Hardware:

```bash
python3 research/decode_capture.py --selftest
python3 research/bruteforce_netkey.py --selftest
python3 research/sniff_mesh.py --selftest
```
