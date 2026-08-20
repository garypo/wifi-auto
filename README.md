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
- Cross-platform: works on both ARM (Raspberry Pi) and x86_64 (Debian)

## Tested Platforms

| Platform | OS | Architecture | WiFi Interface | Wired Interface |
|---|---|---|---|---|
| Raspberry Pi | Ubuntu 24.04 LTS | aarch64 (ARM64) | wlan0 | eth0 |
| Intel NUC / PC | Debian 13 (trixie) | x86_64 (amd64) | wlp0s20f3 | eno1 |

## Requirements

### System packages

The following packages must be installed on the target machine. On a bare OS,
run the install script (below) which installs them automatically, or install
them manually:

| Package | Debian/Ubuntu package name | Purpose |
|---|---|---|
| `iw` | `iw` | WiFi scanning (`iw dev <iface> scan`) |
| `wpa_supplicant` | `wpasupplicant` (Debian) / `wpasupplicant` (Ubuntu) | WiFi association and authentication |
| `wpa_cli` | included with wpasupplicant | Control wpa_supplicant via command line |
| `rfkill` | `rfkill` | Unblock WiFi radio if soft-blocked |
| `wireless-tools` | `wireless-tools` | Legacy WiFi tools (`iwgetid`) |
| `dhcpcd` | `dhcpcd` | DHCP client to obtain IP addresses (preferred) |
| `dhclient` | `isc-dhcp-client` | Alternative DHCP client (fallback) |
| `curl` | `curl` | HTTP requests for connectivity checks and portal handlers |
| `openssl` | `openssl` | TLS for Wi-Fi.HK portal handler (openssl s_client) |
| `ip` | `iproute2` | Network interface and routing management |
| `ping` | `iputils-ping` | ICMP connectivity checks |

### Python packages

| Package | Debian/Ubuntu package name | Purpose |
|---|---|---|
| Python 3.10+ | `python3` | Runtime |
| PyYAML | `python3-yaml` | Parse config.yaml |

### Optional packages (for portal handlers)

| Package | Purpose |
|---|---|
| `chromium` (snap or Chrome for Testing) | Wi-Fi.HK portal handler fallback (headless Chrome via Selenium) |
| `selenium` (Python) | Selenium WebDriver for Chrome-based portal handling |

### User requirements

- The user running the daemon must be in the `netdev` group (for wpa_cli
  access) and have `sudo` privileges (for ip, wpa_supplicant, dhcpcd commands).
  The systemd service runs as root, so sudo is not needed in that mode.

## Installation

### Quick install (from project directory)

```bash
sudo ./install.sh
```

The install script:
1. Installs all required system packages via apt
2. Copies project files to `/opt/wifi-auto/`
3. Installs the systemd service
4. Enables the service for auto-start on boot

### Manual install (step by step)

**1. Install system packages:**

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y \
    iw wpasupplicant wireless-tools rfkill \
    dhcpcd curl openssl \
    python3 python3-yaml
```

**2. Copy project files:**

```bash
sudo mkdir -p /opt/wifi-auto/handlers
sudo cp *.py /opt/wifi-auto/
sudo cp config.yaml /opt/wifi-auto/
sudo cp wifi-auto.service /etc/systemd/system/
sudo cp handlers/*.py /opt/wifi-auto/handlers/
sudo chmod +x /opt/wifi-auto/main.py
```

**3. Install systemd service:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable wifi-auto
```

**4. Edit configuration:**

```bash
sudo nano /opt/wifi-auto/config.yaml
```

Set the correct interface names for your machine and configure your hotspots.

## Configuration

Edit `/opt/wifi-auto/config.yaml`:

```yaml
# Network interfaces -- IMPORTANT: set these to match your hardware
interfaces:
  wifi: "wlan0"        # WiFi interface (check with: ip link)
  wired: "eth0"         # Wired interface
  prefer_wifi: true     # Prefer WiFi for internet even if wired is connected

# Priority hotspot list
hotspots:
  - ssid: "JudWiFi"
    handler: "judwifi"
    priority: 1        # lower = higher priority

  - ssid: "MyHomeWiFi"
    psk: "mypassword"   # WPA2/WPA3 password (omit for open networks)
    priority: 2

  - ssid: "Wi-Fi.HK"
    handler: "wifi_hk"
    priority: 3
```

### Hotspot fields

- `ssid`: Network name
- `psk`: WPA2/WPA3 password (omit for open networks)
- `handler`: Portal handler name (omit if no captive portal)
- `priority`: Connection order (1 = try first)
- `hidden`: Set to `true` for hidden SSIDs

### Finding your interface names

```bash
ip -br link show
# Example output:
# lo               UNKNOWN        00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>
# eth0             UP             2c:cf:67:27:e8:ef <BROADCAST,MULTICAST,UP,LOWER_UP>
# wlan0            DOWN           2c:cf:67:27:e8:f1 <BROADCAST,MULTICAST>
```

WiFi interfaces typically start with `wlan` (Ubuntu/Raspberry Pi) or `wlp`
(Debian with predictable interface names). Wired interfaces typically start
with `eth` or `eno`/`enp`.

## Available Portal Handlers

- `judwifi`: Judiciary WiFi (JudWiFi) portal handler. Uses curl POST to
  `https://wifi1.judiciary.hk:20000/login` with anonymous access. Tested on
  both Raspberry Pi and Debian x86_64.
- `wifi_hk`: Wi-Fi.HK public WiFi portal handler. Uses openssl s_client for
  TLS (curl TLS doesn't work with this portal's proxy). Falls back to
  headless Chrome/Selenium if available.
- `generic_accept`: Generic handler for portals with accept/agree forms.
- `login_portal`: Handler for portals requiring username/password.

## Usage

### Manual commands

```bash
# Scan for available networks
sudo python3 /opt/wifi-auto/main.py --scan -c /opt/wifi-auto/config.yaml

# Show current network status
sudo python3 /opt/wifi-auto/main.py --status -c /opt/wifi-auto/config.yaml

# Run a single check-and-connect cycle
sudo python3 /opt/wifi-auto/main.py --once -c /opt/wifi-auto/config.yaml

# Run as daemon (continuous)
sudo python3 /opt/wifi-auto/main.py -c /opt/wifi-auto/config.yaml
```

### systemd service

```bash
sudo systemctl start wifi-auto    # Start
sudo systemctl stop wifi-auto     # Stop
sudo systemctl restart wifi-auto  # Restart
sudo systemctl status wifi-auto   # Status
sudo journalctl -u wifi-auto -f   # View live logs
tail -f /var/log/wifi-auto.log    # View log file
```

## How It Works

1. Every `check_interval` seconds, the daemon checks Internet connectivity
   using HTTP (generate_204 endpoint), DNS, and ICMP tests.
2. If Internet is available (HTTP 204), it does nothing and waits.
3. If Internet is lost, it:
   a. Disconnects from any current WiFi network
   b. Scans for available WiFi networks using `iw`
   c. Matches scan results against the priority hotspot list
   d. Tries to connect to each matching hotspot in priority order
   e. Associates via `wpa_supplicant`, obtains IP via `dhcpcd`
   f. If a captive portal is detected (HTTP 200/302 instead of 204),
      invokes the configured handler
   g. Verifies Internet connectivity after portal handling
4. On success, returns to monitoring mode. On failure, tries the next
   hotspot, then waits and retries.

## Testing Without Losing Wired Internet

To test WiFi connectivity while the machine is also connected via Ethernet:

1. Set `prefer_wifi: true` in config.yaml
2. The daemon will remove the wired default gateway and route traffic
   through WiFi when connecting
3. To restore wired connectivity, stop the service or run:
   ```bash
   sudo ip route add default via <wired_gateway> dev <wired_iface>
   ```

## Project Structure

```
wifi-auto/
  main.py              # Main daemon and CLI entry point
  scanner.py           # WiFi scanning module (iw)
  connector.py         # WiFi connection module (wpa_supplicant/wpa_cli/dhcpcd)
  connectivity.py      # Internet connectivity checker (HTTP/DNS/ICMP)
  netmgr.py            # Network routing manager (ip route)
  utils.py             # System tool path resolver (handles /usr/sbin not in PATH)
  config.yaml          # Configuration file
  requirements.txt     # Python dependencies
  install.sh           # Installation script
  wifi-auto.service    # systemd service file
  handlers/
    __init__.py
    base.py            # Base portal handler class
    judwifi.py         # Judiciary WiFi (JudWiFi) portal handler
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
        # Interact with the portal (curl, urllib, selenium, etc.)
        ...
```

## Troubleshooting

### WiFi tools not found

On Debian, tools like `iw`, `wpa_cli`, `dhcpcd` are in `/usr/sbin` which is
not in the default PATH for systemd services. The `utils.py` module handles
this automatically by searching common directories. If tools are still not
found, verify they are installed:

```bash
dpkg -l | grep -E 'iw|wpasupplicant|dhcpcd'
ls -la /usr/sbin/iw /usr/sbin/wpa_cli /usr/sbin/dhcpcd
```

### WiFi interface not found

Check available interfaces:
```bash
ip -br link show
iw dev
```

Update `config.yaml` with the correct interface name.

### DHCP fails (no IP address)

Ensure `dhcpcd` is installed and not conflicting with another DHCP client:
```bash
sudo systemctl stop dhcpcd  # Stop system-wide instance
sudo dhcpcd -4 -t 30 <wifi_iface>  # Test manually
```

### Captive portal not handled

Check the daemon log for details:
```bash
sudo journalctl -u wifi-auto -n 50
tail -50 /var/log/wifi-auto.log
```

Ensure `curl` is installed (required by most portal handlers):
```bash
which curl
```