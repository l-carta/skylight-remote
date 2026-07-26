#!/bin/bash
# Provisionee-Angriff mit MAC-Spoof + garantiertem Cleanup.
# Spooft die Pi-MAC auf die Lampen-MAC und startet den Provisionee-Logger
# (imp_prov.py, advertised 0x1827). Ziel: die Remote soll die "frisch
# zurueckgesetzte Lampe" wiedererkennen und ein Provisioning INVITE schicken.
#
# WICHTIG: Die ECHTE Lampe muss STROMLOS sein (sonst MAC-Konflikt)!
#
#   sudo -v ; ./research/imp_capture_prov.sh [laufzeit_s]
set -u
RUNTIME=${1:-90}
cd /home/pi/apps/skylight-remote
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
printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1

echo "# MAC spoofen -> $LAMP_MAC (echte Lampe MUSS stromlos sein!) ..."
sudo btmgmt power off >/dev/null 2>&1
sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
sudo btmgmt power on  >/dev/null 2>&1; sleep 1
echo "# MAC jetzt: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"

echo "# Provisionee-Logger starten (${RUNTIME}s) - JETZT wiederholt ON 10s halten ..."
sudo ~/imp-venv/bin/python research/imp_prov.py "$RUNTIME"
