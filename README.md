# WiFi Auto-Connect

A Python daemon for Linux that automatically scans and connects to public WiFi
hotspots to maintain Internet connectivity. Handles captive portals (terms of
service acceptance, login pages) through a pluggable handler system.

## Features

- Periodic Internet connectivity checking (HTTP, DNS, ICMP)
- Automatic WiFi scanning using `iw`
- Priority-based hotspot connection via `wpa_supplicant` / `wpa_cli`
- Pluggable captive portal handlers (accept terms, login forms, etc.)
- Routing management to prefer WiFi over wired when configured
- systemd service for automatic startup
- YAML configuration for hotspot priority list

## Requirements

- Linux with WiFi interface (tested on Ubuntu 24.04 / Raspberry Pi)
- `wpa_supplicant` and `wpa_cli`
- `iw` (for WiFi scanning)
- `rfkill` (for radio management)
- `dhcpcd` or `dhclient` (for DHCP)
- Python 3.10+ with `pyyaml`

## Installation

```bash
sudo ./install.sh
```

## Configuration

Edit `/opt/wifi-auto/config.yaml`:

```yaml
hotspots:
  - ssid: "Wi-Fi.HK"
    handler: "wifi_hk"
    priority: 1        # lower = higher priority

  - ssid: "MyHomeWiFi"
    psk: "mypassword"
    priority: 2
```

- `ssid`: Network name
- `psk`: WPA2/WPA3 password (omit for open networks)
- `handler`: Portal handler name (omit if no captive portal)
- `priority`: Connection order (1 = try first)
- `hidden`: Set to `true` for hidden SSIDs

## Available Portal Handlers

- `wifi_hk`: Handler for Wi-Fi.HK public WiFi (Hong Kong)
- `generic_accept`: Generic handler for portals with accept/agree forms
- `login_portal`: Handler for portals requiring username/password

## Usage

### Manual commands

```bash
# Scan for available networks
sudo python3 /opt/wifi-auto/main.py --scan

# Show current network status
sudo python3 /opt/wifi-auto/main.py --status

# Run a single check-and-connect cycle
sudo python3 /opt/wifi-auto/main.py --once

# Run as daemon (continuous)
sudo python3 /opt/wifi-auto/main.py
```

### systemd service

```bash
sudo systemctl start wifi-auto    # Start
sudo systemctl stop wifi-auto     # Stop
sudo systemctl status wifi-auto   # Status
sudo journalctl -u wifi-auto -f   # View logs
```

## How It Works

1. Every `check_interval` seconds, the daemon checks Internet connectivity
   using HTTP, DNS, and ICMP tests.
2. If Internet is available, it does nothing and waits.
3. If Internet is lost, it:
   a. Disconnects from any current WiFi network
   b. Scans for available WiFi networks
   c. Matches scan results against the priority hotspot list
   d. Tries to connect to each matching hotspot in priority order
   e. If a captive portal is detected, invokes the configured handler
   f. Verifies Internet connectivity after portal handling
4. On success, returns to monitoring mode. On failure, waits and retries.

## Testing Without Losing Wired Internet

To test WiFi connectivity while the machine is also connected via Ethernet:

1. Set `prefer_wifi: true` in config.yaml
2. The daemon will remove the wired default gateway and route traffic
   through WiFi when connecting
3. To restore wired connectivity, stop the service or run:
   ```bash
   sudo ip route add default via <wired_gateway> dev eth0
   ```

## Project Structure

```
wifi-auto/
  main.py              # Main daemon and CLI entry point
  scanner.py           # WiFi scanning module (iw)
  connector.py         # WiFi connection module (wpa_supplicant/wpa_cli)
  connectivity.py      # Internet connectivity checker
  netmgr.py            # Network routing manager
  config.yaml          # Configuration file
  requirements.txt     # Python dependencies
  install.sh           # Installation script
  wifi-auto.service    # systemd service file
  handlers/
    __init__.py
    base.py            # Base portal handler class
    wifi_hk.py         # Wi-Fi.HK portal handler
    generic_accept.py  # Generic accept-terms handler
    login_portal.py    # Login portal handler
```

## Adding Custom Portal Handlers

1. Create a new file in `handlers/` (e.g., `my_portal.py`)
2. Subclass `BasePortalHandler`
3. Implement `detect()` and `handle()` methods
4. Decorate with `@register_handler`
5. Reference by name in config.yaml

```python
from handlers.base import BasePortalHandler, register_handler

@register_handler
class MyPortalHandler(BasePortalHandler):
    name = "my_portal"

    def detect(self) -> bool:
        # Check if this is the right portal
        ...

    def handle(self) -> bool:
        # Interact with the portal
        ...
```