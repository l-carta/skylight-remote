#!/bin/bash
# Raw-HCI PB-ADV-Probe: sendet den Mesh Unprovisioned Device Beacon (AD 0x2B)
# ueber rohes HCI (bluetoothd gestoppt -> kein Permission-Denied) UND scannt
# gleichzeitig, um zu sehen, ob die Remote mit PB-ADV (Link Open) antwortet.
#
# Alle IDs kommen zur LAUFZEIT: Device-UUID = <Prefix><reversed Adapter-MAC>
# <Suffix>. Mit Argument "spoof" wird die Adapter-MAC vorher auf die MAC aus
# skylight-mesh.json gesetzt (echte Lampe MUSS dann stromlos sein!).
#
#   sudo -v ; ./research/pbadv_probe.sh [dauer_s] [spoof]
set -u
DUR=${1:-45}
SPOOF=${2:-}
HCI=hci0

mac_now() { sudo btmgmt info 2>/dev/null | grep -o 'addr [0-9A-F:]*' | head -1 | cut -d' ' -f2; }
PI_MAC=$(mac_now)
LAMP_MAC=$(python3 -c 'import json;print(json.load(open("skylight-mesh.json"))["mac"])' 2>/dev/null || true)

cleanup() {
  echo "# --- Cleanup ---"
  sudo hcitool -i $HCI cmd 0x08 0x000A 00    >/dev/null 2>&1
  sudo hcitool -i $HCI cmd 0x08 0x000C 00 00 >/dev/null 2>&1
  sudo systemctl start bluetooth; sleep 1
  if [ -n "$SPOOF" ] && [ -n "$PI_MAC" ]; then
    sudo btmgmt power off >/dev/null 2>&1
    sudo btmgmt public-addr "$PI_MAC" >/dev/null 2>&1
    sudo btmgmt power on  >/dev/null 2>&1
  fi
  sudo systemctl start skylight-bridge
  echo "# bluetoothd/Bridge wieder an, MAC: $(mac_now)"
}
trap cleanup EXIT

sudo systemctl stop skylight-bridge 2>/dev/null

if [ -n "$SPOOF" ]; then
  [ -n "$LAMP_MAC" ] || { echo "# Keine Lampen-MAC in skylight-mesh.json"; exit 1; }
  echo "# MAC spoofen -> Lampe (aus Config). Echte Lampe MUSS aus sein!"
  sudo btmgmt power off >/dev/null 2>&1
  sudo btmgmt public-addr "$LAMP_MAC" >/dev/null 2>&1
  sudo btmgmt power on  >/dev/null 2>&1; sleep 1
fi

sudo systemctl stop bluetooth; sleep 1
sudo hciconfig $HCI up
TARGET_MAC=$(sudo hciconfig $HCI | grep -o 'BD Address: [0-9A-F:]*' | cut -d' ' -f3)
echo "# Adapter-MAC: $TARGET_MAC"

# Beacon-Adv-Datenfeld (Laenge + 31 Byte) fuer LE Set Advertising Data ableiten:
ADV_DATA=$(python3 - "$TARGET_MAC" <<'PY'
import sys
mac = bytes.fromhex(sys.argv[1].replace(":", ""))
uuid = bytes.fromhex("0064b4692d0900") + mac[::-1] + bytes.fromhex("000001")
sig = bytes.fromhex("142b00") + uuid + bytes.fromhex("0000")   # Mesh-Beacon 0x2B
field = sig + bytes(31 - len(sig))
print(" ".join("%02x" % b for b in (bytes([len(sig)]) + field)))
PY
)

# non-connectable Adv + Beacon-Data + Scan (passiv)
sudo hcitool -i $HCI cmd 0x08 0x0006 A0 00 A0 00 03 00 00 00 00 00 00 00 00 07 00 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x0008 $ADV_DATA >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000A 01 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000B 00 10 00 10 00 00 00 >/dev/null
sudo hcitool -i $HCI cmd 0x08 0x000C 01 00 >/dev/null
echo "# PB-ADV-Beacon + Scan aktiv."

sudo btmon > /tmp/pbadv.txt 2>/dev/null &
BTM=$!
echo "# === ${DUR}s: JETZT wiederholt ON 10s an der Remote halten ==="
sleep "$DUR"
sudo kill $BTM 2>/dev/null; sleep 1

echo "# === btmon-Zeilen: $(wc -l < /tmp/pbadv.txt) ==="
echo "# === ECHTES PB-ADV / Provisioning (normaler Proxy/Secure-Beacon gefiltert) ==="
grep -iaE "PB-ADV|Provisioning|Unprovisioned|Link (Open|ACK|Close)|Transaction Start" /tmp/pbadv.txt | head -60
echo "# (leer = keine PB-ADV-Antwort der Remote)"
