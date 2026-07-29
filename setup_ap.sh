#!/usr/bin/env bash
# Field WiFi Access Point mode — the Pi broadcasts its own network so the
# headset connects DIRECTLY to the boat: no router, no walls, no range problem.
# Fixes the red-link / finicky-throttle dropouts seen 30-40 ft from the house.
#
# Run on the Pi:   bash setup_ap.sh
# Tear down with:  sudo nmcli connection delete fpv-ap
#
# Uses NetworkManager's native AP mode (this is a Bookworm image — NM manages
# WiFi here). That replaces the classic hostapd+dnsmasq recipe with ONE
# connection profile: NM handles the static IP AND runs DHCP for clients
# (ipv4.method shared), and never touches wlan0's home-network profile, so SSH
# at home keeps working exactly as before. In the field the Pi is reachable at
# 192.168.4.1 on its own SSID.
#
# Interface choice: the BrosTrend AC5L (external antenna, better range) should
# be the AP radio once plugged in — it normally appears as wlan1. The script
# auto-picks wlan1 when present; onboard wlan0 stays on the home network.
# Override with AP_IFACE=... ; force onboard AP with AP_IFACE=wlan0 (that takes
# wlan0 off the home network while the AP profile is active).
#
# Env overrides:
#   AP_SSID   network name        (default FPV-Boat)
#   AP_PASS   WPA2 password, >=8 chars (prompted if not set — never left open)
#   AP_IFACE  which wlan to use   (default: wlan1 if present)
set -uo pipefail

SSID="${AP_SSID:-FPV-Boat}"
CON_NAME="fpv-ap"
AP_IP="192.168.4.1"

echo "[ap] FPV boat — field access point setup"

# --- sanity: NetworkManager must be the one driving WiFi ---------------------
if ! command -v nmcli >/dev/null 2>&1 || ! systemctl is-active --quiet NetworkManager; then
  echo "[ap] ERROR: NetworkManager isn't running — this script targets the"
  echo "     Bookworm/NM stack. (On an older dhcpcd image you'd use the classic"
  echo "     hostapd+dnsmasq recipe instead.)"
  exit 1
fi

# --- pick the radio ----------------------------------------------------------
IFACE="${AP_IFACE:-}"
if [ -z "$IFACE" ]; then
  if ip link show wlan1 >/dev/null 2>&1; then
    IFACE=wlan1
  else
    echo "[ap] No wlan1 found — the BrosTrend adapter isn't detected."
    echo "     Interfaces present:"
    ip -brief link | awk '{print "       " $1}'
    echo "     - If the AC5L is plugged in but absent, it needs its driver:"
    echo "         sh -c 'wget linux.brostrend.com/install -O /tmp/i && sudo sh /tmp/i'"
    echo "       (recent kernels often support it out of the box — replug + reboot first)"
    echo "     - To run the AP on the ONBOARD radio instead (wlan0 leaves the home"
    echo "       network while the AP is up):  AP_IFACE=wlan0 bash setup_ap.sh"
    exit 1
  fi
fi
echo "[ap] using interface: $IFACE  (onboard home-network profile is left alone)"

# --- password (never an open network) ---------------------------------------
PASS="${AP_PASS:-}"
while [ "${#PASS}" -lt 8 ]; do
  read -r -s -p "[ap] choose a WPA2 password for '$SSID' (min 8 chars): " PASS
  echo
done

# --- (re)create the AP profile ----------------------------------------------
if nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
  echo "[ap] replacing existing '$CON_NAME' profile…"
  sudo nmcli connection delete "$CON_NAME" >/dev/null
fi

sudo nmcli connection add type wifi ifname "$IFACE" con-name "$CON_NAME" \
  autoconnect yes ssid "$SSID" mode ap
# 2.4 GHz carries farther in open air; channel 7 per the field plan
sudo nmcli connection modify "$CON_NAME" \
  802-11-wireless.band bg \
  802-11-wireless.channel 7 \
  802-11-wireless.powersave 2 \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.proto rsn \
  wifi-sec.pairwise ccmp \
  wifi-sec.group ccmp \
  wifi-sec.psk "$PASS" \
  ipv4.method shared \
  ipv4.addresses "$AP_IP/24" \
  ipv6.method disabled \
  connection.autoconnect-priority 10

# ipv4.method=shared makes NM hand out DHCP leases (192.168.4.x) on this
# interface itself — no separate dnsmasq/hostapd to install or keep alive.

echo "[ap] bringing the AP up…"
if sudo nmcli connection up "$CON_NAME"; then
  echo
  echo "[ap] done — network '$SSID' is broadcasting."
  echo "     Connect the headset/phone to it, then open:"
  echo "         https://$AP_IP:5000/viewer"
  echo "     SSH in the field:  ssh $(whoami)@$AP_IP"
  echo "     (At home, wlan0 + the router keep working as before.)"
else
  echo "[ap] ERROR: profile created but failed to come up. Debug with:"
  echo "         nmcli device status ; journalctl -u NetworkManager -n 50"
  exit 1
fi
