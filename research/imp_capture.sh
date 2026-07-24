#!/bin/bash
# Fake-Lampe-Capture mit garantiertem Cleanup.
# Stoppt Bridge, spooft Pi-MAC auf die Lampen-MAC, startet den Fake-GATT-Server
# + Advertising, und setzt am Ende IMMER MAC zurueck + Bridge wieder an.
#
#   sudo -v ; ./imp_capture.sh [laufzeit_s]
set -u
RUNTIME=${1:-60}
cd /home/pi/apps/skylight-remote
# Geraete-Identitaeten zur Laufzeit ermitteln (nichts hardcoden):
LAMP_MAC=$(python3 -c 'import json;print(json.load(open("skylight-mesh.json"))["mac"])')
PI_MAC=$(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1 | cut -d" " -f2)

cleanup() {
  echo "# --- Cleanup: Advertising aus, MAC zurueck, Bridge an ---"
  printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1
  sudo btmgmt power off  >/dev/null 2>&1
  sudo btmgmt public-addr "$PI_MAC" >/dev/null 2>&1
  sudo btmgmt power on   >/dev/null 2>&1
  sudo systemctl start skylight-bridge
  echo "# MAC zurueck: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"
}
trap cleanup EXIT

echo "# Bridge stoppen ..."
sudo systemctl stop skylight-bridge; sleep 2
printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1   # stale adv weg

echo "# MAC spoofen -> $LAMP_MAC ..."
sudo btmgmt power off >/dev/null 2>&1
sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
sudo btmgmt power on  >/dev/null 2>&1; sleep 1
echo "# MAC jetzt: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"

echo "# Fake-Lampe starten (${RUNTIME}s) ..."
sudo ~/imp-venv/bin/python research/imp_lamp.py "$RUNTIME"
