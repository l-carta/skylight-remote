#!/bin/bash
# Wie imp_capture.sh, aber mit btmon-Mitschnitt der VERBINDUNGS-Events, um zu
# sehen, ob die Remote ueberhaupt connectet (und ob Bonding/SMP scheitert).
set -u
RUNTIME=${1:-45}
cd /home/pi/apps/skylight-remote
# Geraete-Identitaeten zur Laufzeit ermitteln (nichts hardcoden):
LAMP_MAC=$(python3 -c 'import json;print(json.load(open("skylight-mesh.json"))["mac"])')
PI_MAC=$(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1 | cut -d" " -f2)

cleanup() {
  echo "# --- Cleanup ---"
  printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1
  sudo btmgmt power off  >/dev/null 2>&1
  sudo btmgmt public-addr "$PI_MAC" >/dev/null 2>&1
  sudo btmgmt power on   >/dev/null 2>&1
  sudo systemctl start skylight-bridge
}
trap cleanup EXIT

sudo systemctl stop skylight-bridge; sleep 2
printf 'advertise off\n' | bluetoothctl >/dev/null 2>&1
sudo btmgmt power off >/dev/null 2>&1
sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
sudo btmgmt power on  >/dev/null 2>&1; sleep 1
echo "# MAC: $(sudo btmgmt info | grep -o 'addr [0-9A-F:]*' | head -1)"

sudo timeout $((RUNTIME + 6)) btmon > /tmp/btmon_imp.txt 2>&1 &
echo "# Fake-Lampe ${RUNTIME}s - JETZT Remote druecken ..."
sudo ~/imp-venv/bin/python imp_lamp.py "$RUNTIME"
sleep 1

echo "=== Verbindungs-Events ==="
grep -inE "Connection Complete|Device Connected|Device Disconnected|Disconnect|Reason:|SMP|Security Manager|Encrypt|Long.?Term|Pairing|Scan Request|Connect Request|Peer address|LL_" \
     /tmp/btmon_imp.txt | head -50
echo "=== Zusammenfassung ==="
echo "  Connection Complete: $(grep -c "Connection Complete" /tmp/btmon_imp.txt)"
echo "  Disconnects:         $(grep -c "Disconnect Complete\|Device Disconnected" /tmp/btmon_imp.txt)"
echo "  SMP/Pairing-Pakete:  $(grep -ci "SMP\|Security Manager\|Pairing" /tmp/btmon_imp.txt)"
