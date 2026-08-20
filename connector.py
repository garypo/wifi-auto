#!/usr/bin/env python3
"""
WiFi Auto-Connect Connector Module
Manages wpa_supplicant to connect to WiFi networks.
"""

import subprocess
import time
import logging
import os
import tempfile
from typing import Optional, List

from scanner import WifiNetwork

logger = logging.getLogger(__name__)


class WifiConnector:
    """Manage WiFi connections via wpa_supplicant and wpa_cli."""

    def __init__(self, iface: str = "wlan0",
                 connect_timeout: int = 30,
                 dhcp_timeout: int = 45):
        self.iface = iface
        self.connect_timeout = connect_timeout
        self.dhcp_timeout = dhcp_timeout
        self._current_ssid: Optional[str] = None

    def _run(self, cmd: list, timeout: int = 10) -> tuple:
        """Run a command, return (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except Exception as e:
            return -1, "", str(e)

    def _run_shell(self, cmd: str, timeout: int = 10) -> tuple:
        """Run a shell command string, return (returncode, stdout+stderr)."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout + result.stderr, ""
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except Exception as e:
            return -1, "", str(e)

    def ensure_wpa_supplicant_running(self) -> bool:
        """Ensure wpa_supplicant is running with a control interface for our iface.

        On this system, wpa_supplicant is managed by systemd (global mode with
        -O DIR=/run/wpa_supplicant). It creates per-interface sockets at
        /run/wpa_supplicant/<iface>. We use that existing instance instead of
        starting a conflicting second one.
        """
        socket_path = f"/run/wpa_supplicant/{self.iface}"

        # Check if wpa_cli can already talk to the interface
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "status"], timeout=5
        )
        if rc == 0 and "Failed to connect" not in out:
            logger.debug("wpa_supplicant control interface is active")
            return True

        # Ensure the systemd wpa_supplicant service is running
        self._run_shell(
            "sudo systemctl start wpa_supplicant 2>/dev/null", timeout=10
        )
        time.sleep(1)

        # If the interface socket doesn't exist, add the interface to the
        # global wpa_supplicant via the global control socket
        rc, out, _ = self._run_shell(
            f"test -S {socket_path} && echo EXISTS || echo MISSING", timeout=5
        )
        if "MISSING" in out:
            logger.info(
                f"Adding interface {self.iface} to wpa_supplicant..."
            )
            # Create a per-interface config
            conf_path = f"/etc/wpa_supplicant/wpa_supplicant_{self.iface}.conf"
            conf_content = (
                "ctrl_interface=/run/wpa_supplicant\n"
                "ctrl_interface_group=netdev\n"
                "update_config=1\n"
                "ap_scan=1\n"
            )
            self._run_shell(
                f"echo '{conf_content}' | sudo tee {conf_path} > /dev/null",
                timeout=5,
            )
            # Add interface via global control socket
            self._run_shell(
                f"sudo wpa_cli -g /run/wpa_supplicant "
                f"interface_add {self.iface} {conf_path} nl80211 2>&1",
                timeout=10,
            )
            time.sleep(2)

        # Verify
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "status"], timeout=5
        )
        if rc == 0 and "Failed to connect" not in out:
            logger.info("wpa_supplicant ready")
            return True

        # Last resort: start a standalone wpa_supplicant for this interface
        # (only if the systemd one is not managing it)
        logger.warning(
            f"Could not use systemd wpa_supplicant, starting standalone..."
        )
        conf_path = f"/tmp/wpa_supplicant_{self.iface}.conf"
        conf_content = (
            "ctrl_interface=/run/wpa_supplicant\n"
            "ctrl_interface_group=netdev\n"
            "update_config=1\n"
            "ap_scan=1\n"
        )
        self._run_shell(
            f"echo '{conf_content}' | sudo tee {conf_path} > /dev/null",
            timeout=5,
        )
        self._run_shell(
            f"sudo wpa_supplicant -B -i {self.iface} -c {conf_path} "
            f"-D nl80211 2>&1",
            timeout=10,
        )
        time.sleep(2)

        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "status"], timeout=5
        )
        if rc == 0 and "Failed to connect" not in out:
            logger.info("wpa_supplicant started (standalone)")
            return True

        logger.error("wpa_supplicant failed to start")
        return False

    def connect(self, network: WifiNetwork, psk: str = "",
                hidden: bool = False) -> bool:
        """
        Connect to a WiFi network.

        Args:
            network: The WifiNetwork to connect to
            psk: WPA2/WPA3 password (empty for open networks)
            hidden: True if the network has a hidden SSID

        Returns:
            True if connected and got an IP address
        """
        logger.info(f"Connecting to '{network.ssid}' "
                    f"(signal={network.signal}dBm, security={network.security})")

        if not self.ensure_wpa_supplicant_running():
            return False

        # Clean up all existing networks to avoid junk entries
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "list_networks"], timeout=5
        )
        if rc == 0:
            for line in out.strip().split('\n')[1:]:  # skip header
                parts = line.split('\t')
                if parts and parts[0].strip():
                    self._run(
                        ["wpa_cli", "-i", self.iface, "remove_network",
                         parts[0].strip()],
                        timeout=5
                    )

        # Add a fresh network
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "add_network"], timeout=5
        )
        net_id = out.strip() if out.strip().isdigit() else "0"

        # Configure the network
        if network.is_open or not psk:
            self._run(
                ["wpa_cli", "-i", self.iface, "set_network", net_id,
                 "ssid", f'"{network.ssid}"'],
                timeout=5
            )
            self._run(
                ["wpa_cli", "-i", self.iface, "set_network", net_id,
                 "key_mgmt", "NONE"],
                timeout=5
            )
        else:
            self._run(
                ["wpa_cli", "-i", self.iface, "set_network", net_id,
                 "ssid", f'"{network.ssid}"'],
                timeout=5
            )
            self._run(
                ["wpa_cli", "-i", self.iface, "set_network", net_id,
                 "psk", f'"{psk}"'],
                timeout=5
            )

        if hidden:
            self._run(
                ["wpa_cli", "-i", self.iface, "set_network", net_id,
                 "scan_ssid", "1"],
                timeout=5
            )

        # Enable and select the network
        self._run(
            ["wpa_cli", "-i", self.iface, "enable_network", net_id],
            timeout=5
        )
        self._run(
            ["wpa_cli", "-i", self.iface, "select_network", net_id],
            timeout=5
        )
        self._run(
            ["wpa_cli", "-i", self.iface, "save_config"],
            timeout=5
        )

        # Wait for association
        if not self._wait_for_association(network.ssid):
            logger.warning(f"Failed to associate with '{network.ssid}'")
            return False

        # Get IP address via DHCP
        if not self._get_dhcp_lease():
            logger.warning(f"Failed to get DHCP lease on {self.iface}")
            return False

        self._current_ssid = network.ssid
        logger.info(f"Successfully connected to '{network.ssid}'")
        return True

    def _wait_for_association(self, ssid: str, timeout: int = None) -> bool:
        """Wait for wpa_supplicant to associate with the AP."""
        timeout = timeout or self.connect_timeout
        deadline = time.time() + timeout

        while time.time() < deadline:
            rc, out, _ = self._run(
                ["wpa_cli", "-i", self.iface, "status"], timeout=5
            )
            if rc == 0:
                if "wpa_state=COMPLETED" in out:
                    logger.info(f"Associated with '{ssid}'")
                    return True
                # Also check for ASSOCIATING / HANDSHAKE states (still progressing)
                if "wpa_state=4WAY_HANDSHAKE" in out:
                    logger.debug("4-way handshake in progress...")
                elif "wpa_state=ASSOCIATED" in out:
                    logger.debug("Associated, waiting for handshake...")
            time.sleep(2)

        # Final check
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "status"], timeout=5
        )
        if rc == 0 and "wpa_state=COMPLETED" in out:
            logger.info(f"Associated with '{ssid}' (late)")
            return True

        return False

    def _get_dhcp_lease(self, timeout: int = None) -> bool:
        """Obtain a DHCP lease on the WiFi interface."""
        timeout = timeout or self.dhcp_timeout

        logger.info(f"Requesting DHCP lease on {self.iface}...")

        # Kill any existing DHCP client on this interface gracefully.
        # Using -9 (SIGKILL) causes the interface to lose carrier, which
        # makes wpa_supplicant disconnect. Use dhcpcd -k for graceful release.
        self._run_shell(
            f"sudo dhcpcd -k {self.iface} 2>/dev/null", timeout=5
        )
        time.sleep(1)
        # Remove stale pid files
        self._run_shell(
            f"sudo rm -f /run/dhcpcd/{self.iface}*.pid 2>/dev/null",
            timeout=5
        )

        # Start dhcpcd in background mode (default), then poll for IP.
        # dhcpcd uses -t (not --timeout) for the lease timeout.
        self._run_shell(
            f"sudo dhcpcd -4 -t {timeout} {self.iface} 2>&1",
            timeout=10  # just wait for it to start
        )

        # Poll for IP address (dhcpcd runs in background, lease takes time)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            rc, out, _ = self._run(
                ["ip", "-br", "addr", "show", self.iface], timeout=5
            )
            if rc == 0 and out:
                # In brief format, IP appears as 3rd+ field, e.g.:
                # "wlan0  UP  172.16.120.249/22 fe80::..."
                # Skip the interface name and state, check remaining fields
                parts = out.strip().split()
                # parts[0]=iface, parts[1]=state, parts[2:]=addresses
                for part in parts[2:]:
                    # IPv4 addresses don't contain ':' (unlike IPv6)
                    if '.' in part and '/' in part:
                        ip = part.split('/')[0]
                        logger.info(f"Got IP {ip} on {self.iface}")
                        return True

        # Try dhclient as fallback
        logger.debug("dhcpcd didn't get an IP, trying dhclient...")
        self._run_shell(
            f"sudo dhclient {self.iface} 2>&1", timeout=timeout
        )
        rc, out, _ = self._run(
            ["ip", "-br", "addr", "show", self.iface], timeout=5
        )
        if rc == 0 and "inet " in out:
            logger.info(f"Got IP via dhclient on {self.iface}")
            return True

        logger.error(f"No IP address on {self.iface}")
        return False

    def disconnect(self) -> bool:
        """Disconnect from the current WiFi network."""
        logger.info(f"Disconnecting from {self.iface}...")

        # Release DHCP
        self._run_shell(f"sudo dhcpcd -k {self.iface} 2>/dev/null", timeout=5)

        # Tell wpa_supplicant to disconnect
        self._run(["wpa_cli", "-i", self.iface, "disconnect"], timeout=5)

        self._current_ssid = None
        time.sleep(1)
        return True

    def get_current_connection(self) -> Optional[dict]:
        """Get info about the current WiFi connection."""
        rc, out, _ = self._run(
            ["wpa_cli", "-i", self.iface, "status"], timeout=5
        )
        if rc != 0 or "Failed to connect" in out:
            return None

        info = {}
        for line in out.split('\n'):
            if '=' in line:
                key, val = line.split('=', 1)
                info[key] = val

        if info.get('wpa_state') == 'COMPLETED' and info.get('ssid'):
            return info
        return None

    def get_current_ip(self) -> Optional[str]:
        """Get the current IP address on the WiFi interface."""
        rc, out, _ = self._run(["ip", "-br", "addr", "show", self.iface], timeout=5)
        if rc == 0:
            for line in out.split('\n'):
                if self.iface in line and "inet " in line:
                    parts = line.split("inet ")
                    if len(parts) > 1:
                        return parts[1].split()[0]
        return None

    @property
    def current_ssid(self) -> Optional[str]:
        """Return the SSID we're currently connected to."""
        conn = self.get_current_connection()
        if conn:
            return conn.get('ssid')
        return self._current_ssid