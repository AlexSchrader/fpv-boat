# Networking

Notes for keeping the Pi reachable — the recurring "unreachable / wrong IP"
pain has cost real time on this project.

## Field mode: the Pi as its own WiFi access point

Boat testing happens 30–40+ ft from the router with walls in between, and the
first water test showed exactly what that does: red HUD link, finicky throttle,
flickering lights under motor load. Fix: the Pi broadcasts its **own network**
and the headset connects directly — the signal only crosses open air between
headset and boat.

```sh
bash setup_ap.sh          # prompts for a WPA2 password (AP_SSID/AP_PASS/AP_IFACE env overrides)
```

- Uses **NetworkManager's native AP mode** (this image is Bookworm/NM — no
  hostapd/dnsmasq needed): one `fpv-ap` profile carries the static IP
  **192.168.4.1/24** *and* hands out DHCP (`ipv4.method shared`).
- **Radio:** the BrosTrend AC5L (external antenna) as `wlan1` is the AP;
  **onboard `wlan0` stays on the home network untouched**, so SSH at home works
  exactly as before. If the AC5L isn't detected the script says so (driver
  one-liner included) — or force the onboard radio with `AP_IFACE=wlan0`
  (which takes wlan0 off the home network while the AP is up).
- In the field: connect the Quest to `FPV-Boat`, open
  **`https://192.168.4.1:5000/viewer`**; SSH via `ssh acschrader@192.168.4.1`.
- Tear down: `sudo nmcli connection delete fpv-ap`.

**Field-test checklist**
1. Reboot; confirm `FPV-Boat` appears in a phone's WiFi list.
2. Connect a phone (cellular data OFF), confirm it gets a `192.168.4.x` IP.
3. Load `https://192.168.4.1:5000/viewer` — video + HUD with no home network involved.
4. Repeat the motor-load test that caused the dropout — link bars should hold
   green under throttle now.
5. Back home: SSH over `wlan0`/the router still works normally.

**Trade-off to know:** while joined to the boat's AP the headset has no
internet of its own (NM's shared mode will NAT through wlan0 when the Pi is
home, but in the field there's nothing behind it). Doesn't matter for flying —
worth knowing if something on the headset side wants the internet mid-session.

## Current identity

- **Hostname:** `FPV-boat`  → reachable at `fpv-boat.local` if mDNS/avahi is up
- **IP (DHCP):** `10.0.0.26` (was `10.0.0.20` earlier — it drifts, hence the fix below)

If the viewer says "unreachable," first SSH in (or check on the Pi) with:

```sh
hostname -I          # what IP did DHCP actually assign?
```

Try `ping fpv-boat.local` from the client — if mDNS resolves, you don't need to
chase the numeric IP at all.

## Stop the IP from changing

**Option A — router DHCP reservation (recommended, OS-agnostic).**
Bind the Pi's WiFi MAC to a fixed IP in your router's admin panel. Get the MAC:

```sh
ip link show wlan0     # the "link/ether xx:xx:xx:xx:xx:xx" line
```

**Option B — static IP on the Pi.** Depends on the OS:

- **Bookworm (NetworkManager)** — check with `nmcli -t -f NAME connection show`:
  ```sh
  sudo nmcli connection modify "<wifi-name>" \
    ipv4.method manual ipv4.addresses 10.0.0.26/24 ipv4.gateway 10.0.0.1 ipv4.dns 10.0.0.1
  sudo nmcli connection up "<wifi-name>"
  ```

- **Bullseye or older (dhcpcd)** — append to `/etc/dhcpcd.conf`:
  ```
  interface wlan0
  static ip_address=10.0.0.26/24
  static routers=10.0.0.1
  static domain_name_servers=10.0.0.1
  ```
  then `sudo systemctl restart dhcpcd`.

## Keep WiFi power management off (fixes intermittent SSH drops)

`iwconfig wlan0 power off` only lasts until reboot. Make it persist:

- **Bookworm (NetworkManager):**
  ```sh
  sudo nmcli connection modify "<wifi-name>" 802-11-wireless.powersave 2   # 2 = disable
  sudo nmcli connection up "<wifi-name>"
  ```

- **Older (rc.local):** ensure `/etc/rc.local` contains `iwconfig wlan0 power off`
  before the `exit 0` line.

**Verify after an actual reboot** (don't trust that it saved):

```sh
sudo reboot
# then, once back up:
iwconfig wlan0 | grep -i "power management"   # want: Power Management:off
```
