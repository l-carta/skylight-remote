"""Laden/Speichern der Mesh-Konfiguration (skylight-mesh.json).

Wichtig: Die Mesh-Replay-Protection verwirft Nachrichten mit bereits
gesehener Sequenznummer stillschweigend. Damit ein abgestuerzter Lauf
(gesendet, aber nicht gespeichert) uns nicht lahmlegt, springt die
Sequenznummer bei jedem Laden um eine Sicherheitsmarge nach vorn und wird
sofort zurueckgeschrieben.
"""

import json

SEQ_SAFETY_JUMP = 512


def load_cfg(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    cfg["seq"] = cfg.get("seq", 0) + SEQ_SAFETY_JUMP
    save_cfg(path, cfg)
    return cfg


def save_cfg(path: str, cfg: dict):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
