#!/bin/bash
# Install script for WiFi Auto-Connect
# Run on the target machine as root or with sudo

set -e

INSTALL_DIR="/opt/wifi-auto"
SERVICE_NAME="wifi-auto"

echo "=== WiFi Auto-Connect Installer ==="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo ./install.sh"
    exit 1
fi

# Create install directory
echo "Creating install directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/handlers"

# Copy files
echo "Copying files..."
cp main.py "$INSTALL_DIR/"
cp scanner.py "$INSTALL_DIR/"
cp connector.py "$INSTALL_DIR/"
cp connectivity.py "$INSTALL_DIR/"
cp netmgr.py "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/"
cp handlers/__init__.py "$INSTALL_DIR/handlers/"
cp handlers/base.py "$INSTALL_DIR/handlers/"
cp handlers/wifi_hk.py "$INSTALL_DIR/handlers/"
cp handlers/generic_accept.py "$INSTALL_DIR/handlers/"
cp handlers/login_portal.py "$INSTALL_DIR/handlers/"

# Set permissions
chmod +x "$INSTALL_DIR/main.py"
chmod 644 "$INSTALL_DIR/config.yaml"

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install pyyaml 2>/dev/null || apt-get install -y python3-yaml

# Install system tools if not present
echo "Checking system tools..."
apt-get install -y iw rfkill wireless-tools curl 2>/dev/null || true

# Install systemd service
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
echo "Commands:"
echo "  Start:  sudo systemctl start $SERVICE_NAME"
echo "  Stop:   sudo systemctl stop $SERVICE_NAME"
echo "  Status: sudo systemctl status $SERVICE_NAME"
echo "  Logs:   sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "Manual testing:"
echo "  Scan:   sudo python3 $INSTALL_DIR/main.py --scan"
echo "  Status: sudo python3 $INSTALL_DIR/main.py --status"
echo "  Once:   sudo python3 $INSTALL_DIR/main.py --once"
echo ""
echo "Edit $INSTALL_DIR/config.yaml to configure your hotspots."
echo ""
echo "IMPORTANT: Before starting the service, test with --scan and --once"