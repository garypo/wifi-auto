#!/bin/bash
# Install script for WiFi Auto-Connect
# Run on the target machine as root or with sudo
#
# Installs all required system packages and deploys the application to
# /opt/wifi-auto/. Works on Debian and Ubuntu.

set -e

INSTALL_DIR="/opt/wifi-auto"
SERVICE_NAME="wifi-auto"

echo "=== WiFi Auto-Connect Installer ==="
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# ---- Install system packages ----
echo "Installing system packages..."

apt-get update -qq

# WiFi tools: scanning, association, radio control
apt-get install -y \
    iw \
    wpasupplicant \
    wireless-tools \
    rfkill

# DHCP client: dhcpcd (preferred) for obtaining IP addresses
apt-get install -y dhcpcd || true

# Network tools: curl for connectivity checks and portal handlers,
# openssl for TLS-based portal handling (Wi-Fi.HK)
apt-get install -y curl openssl

# Python runtime and YAML parser
apt-get install -y python3 python3-yaml

# iproute2 provides 'ip' command (usually pre-installed but ensure)
apt-get install -y iproute2

# iputils-ping provides 'ping' for ICMP connectivity checks
apt-get install -y iputils-ping || true

echo "System packages installed."
echo ""

# ---- Install project files ----
echo "Creating install directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/handlers"

echo "Copying files..."
cp main.py "$INSTALL_DIR/"
cp scanner.py "$INSTALL_DIR/"
cp connector.py "$INSTALL_DIR/"
cp connectivity.py "$INSTALL_DIR/"
cp netmgr.py "$INSTALL_DIR/"
cp utils.py "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/"
cp handlers/__init__.py "$INSTALL_DIR/handlers/"
cp handlers/base.py "$INSTALL_DIR/handlers/"
cp handlers/judwifi.py "$INSTALL_DIR/handlers/"
cp handlers/wifi_hk.py "$INSTALL_DIR/handlers/"
cp handlers/generic_accept.py "$INSTALL_DIR/handlers/"
cp handlers/login_portal.py "$INSTALL_DIR/handlers/"

# Set permissions
chmod +x "$INSTALL_DIR/main.py"
chmod 644 "$INSTALL_DIR/config.yaml"

echo ""
echo "Files installed to $INSTALL_DIR"
echo ""

# ---- Verify tools ----
echo "Verifying installed tools..."
for tool in iw wpa_supplicant wpa_cli dhcpcd curl openssl ip; do
    # Check in common locations (some systems have /usr/sbin not in PATH)
    found=""
    for dir in /usr/sbin /usr/bin /sbin /bin /usr/local/sbin /usr/local/bin; do
        if [ -x "$dir/$tool" ]; then
            found="$dir/$tool"
            break
        fi
    done
    if [ -n "$found" ]; then
        echo "  OK: $tool -> $found"
    else
        echo "  WARNING: $tool not found!"
    fi
done

echo ""
echo "Python packages:"
python3 -c "import yaml; print(f'  OK: pyyaml {yaml.__version__}')" 2>/dev/null \
    || echo "  WARNING: pyyaml not found!"

# ---- Install systemd service ----
echo ""
echo "Installing systemd service..."
cp wifi-auto.service /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable service (but don't start yet)
systemctl enable $SERVICE_NAME

echo ""
echo "=== Installation Complete ==="
echo ""
echo "The service is installed at: $INSTALL_DIR"
echo "Configuration: $INSTALL_DIR/config.yaml"
echo ""
echo "IMPORTANT: Before starting the service:"
echo ""
echo "  1. Find your WiFi and wired interface names:"
echo "     ip -br link show"
echo ""
echo "  2. Edit config.yaml with the correct interfaces:"
echo "     sudo nano $INSTALL_DIR/config.yaml"
echo "     Set 'wifi' and 'wired' under 'interfaces:'"
echo ""
echo "  3. Configure your hotspots in the same file"
echo ""
echo "  4. Test with manual commands:"
echo "     sudo python3 $INSTALL_DIR/main.py --scan -c $INSTALL_DIR/config.yaml"
echo "     sudo python3 $INSTALL_DIR/main.py --status -c $INSTALL_DIR/config.yaml"
echo "     sudo python3 $INSTALL_DIR/main.py --once -c $INSTALL_DIR/config.yaml"
echo ""
echo "  5. Start the daemon:"
echo "     sudo systemctl start $SERVICE_NAME"
echo ""
echo "Commands:"
echo "  Start:  sudo systemctl start $SERVICE_NAME"
echo "  Stop:   sudo systemctl stop $SERVICE_NAME"
echo "  Status: sudo systemctl status $SERVICE_NAME"
echo "  Logs:   sudo journalctl -u $SERVICE_NAME -f"
echo "          tail -f /var/log/wifi-auto.log"