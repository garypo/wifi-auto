#!/usr/bin/env python3
"""
Network Manager Module
Manages routing and gateway configuration to control which interface
provides Internet access.
"""

import subprocess
import logging
import time
from typing import Optional

from utils import get_ip, get_dhcpcd

logger = logging.getLogger(__name__)


class NetworkManager:
    """Manage network interfaces and routing tables."""

    def __init__(self, wifi_iface: str = "wlan0",
                 wired_iface: str = "eth0"):
        self.wifi_iface = wifi_iface
        self.wired_iface = wired_iface
        self._ip = get_ip()
        self._dhcpcd = get_dhcpcd()

    def _run(self, cmd: list, timeout: int = 10) -> tuple:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            return -1, "", str(e)

    def _run_shell(self, cmd: str, timeout: int = 10) -> tuple:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout + result.stderr, ""
        except Exception as e:
            return -1, "", str(e)

    def get_default_gateway(self) -> Optional[str]:
        """Get the current default gateway IP."""
        rc, out, _ = self._run([self._ip, "route", "show", "default"], timeout=5)
        if rc == 0:
            for line in out.split('\n'):
                if "default via" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2]
        return None

    def get_default_iface(self) -> Optional[str]:
        """Get the interface currently used for default route."""
        rc, out, _ = self._run([self._ip, "route", "show", "default"], timeout=5)
        if rc == 0:
            for line in out.split('\n'):
                if "default via" in line and "dev" in line:
                    parts = line.split()
                    if "dev" in parts:
                        idx = parts.index("dev")
                        if idx + 1 < len(parts):
                            return parts[idx + 1]
        return None

    def remove_wired_gateway(self) -> bool:
        """
        Remove the default gateway from the wired interface to force
        internet traffic through WiFi.
        """
        logger.info(f"Removing default gateway from {self.wired_iface}...")

        # Get current default route via wired interface
        rc, out, _ = self._run([self._ip, "route", "show", "default"], timeout=5)
        wired_gateway = None
        for line in out.split('\n'):
            if "default via" in line and self.wired_iface in line:
                parts = line.split()
                if len(parts) >= 3:
                    wired_gateway = parts[2]
                    break

        if not wired_gateway:
            logger.info(f"No default gateway on {self.wired_iface} to remove")
            return True

        # Remove the default route via wired interface
        rc, out, err = self._run_shell(
            f"sudo {self._ip} route del default via {wired_gateway} "
            f"dev {self.wired_iface} 2>&1", timeout=10
        )
        if rc == 0:
            logger.info(f"Removed default gateway {wired_gateway} "
                        f"from {self.wired_iface}")
        else:
            # Might need to try without the via part
            rc, out, err = self._run_shell(
                f"sudo {self._ip} route del default dev {self.wired_iface} 2>&1",
                timeout=10
            )
            if rc == 0:
                logger.info(f"Removed default route from {self.wired_iface}")
            else:
                logger.warning(f"Could not remove wired gateway: {out}")

        # Also reduce metric on wired interface to make it less preferred
        # (in case it gets re-added by DHCP)
        self._run_shell(
            f"sudo {self._ip} route flush cache 2>&1", timeout=5
        )

        return True

    def restore_wired_gateway(self) -> bool:
        """Restore the wired interface's default gateway."""
        logger.info(f"Restoring default gateway on {self.wired_iface}...")

        # The DHCP client should re-add the route
        # Try to re-trigger DHCP on wired interface
        rc, out, _ = self._run_shell(
            f"sudo {self._dhcpcd} -4 {self.wired_iface} 2>&1", timeout=30
        )

        # If dhcpcd doesn't work, try networkctl reconfigure
        if rc != 0:
            self._run_shell(
                f"sudo networkctl reconfigure {self.wired_iface} 2>&1",
                timeout=10
            )

        time.sleep(3)

        # Check if gateway is back
        gw = self.get_default_gateway()
        if gw:
            logger.info(f"Default gateway restored: {gw}")
            return True

        logger.warning("Could not restore wired gateway")
        return False

    def set_wifi_as_default(self, wifi_gateway: str) -> bool:
        """Set WiFi interface as the default route."""
        logger.info(f"Setting {self.wifi_iface} as default route "
                    f"via {wifi_gateway}...")

        # First remove any existing default routes
        self._run_shell(f"sudo {self._ip} route del default 2>/dev/null", timeout=5)

        # Add new default route via WiFi
        rc, out, _ = self._run_shell(
            f"sudo {self._ip} route add default via {wifi_gateway} "
            f"dev {self.wifi_iface} 2>&1", timeout=10
        )
        if rc == 0:
            logger.info(f"Default route set via {self.wifi_iface}")
            return True

        logger.error(f"Failed to set WiFi as default: {out}")
        return False

    def get_wifi_gateway(self) -> Optional[str]:
        """Get the gateway IP for the WiFi interface from DHCP."""
        rc, out, _ = self._run(
            [self._ip, "route", "show", "dev", self.wifi_iface], timeout=5
        )
        if rc == 0:
            for line in out.split('\n'):
                if "default via" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2]
        return None

    def get_interface_ip(self, iface: str) -> Optional[str]:
        """Get the IP address of an interface."""
        rc, out, _ = self._run([self._ip, "-br", "addr", "show", iface], timeout=5)
        if rc == 0:
            for line in out.split('\n'):
                if iface in line and "inet " in line:
                    parts = line.split("inet ")
                    if len(parts) > 1:
                        return parts[1].split()[0]
        return None

    def is_interface_up(self, iface: str) -> bool:
        """Check if an interface is up and has carrier."""
        rc, out, _ = self._run([self._ip, "-br", "link", "show", iface], timeout=5)
        if rc == 0:
            return "UP" in out and "DOWN" not in out
        return False

    def setup_wifi_routing(self) -> bool:
        """
        Configure routing so WiFi is used for Internet access.
        This removes the wired gateway and sets WiFi as default.
        """
        wifi_gw = self.get_wifi_gateway()
        if not wifi_gw:
            logger.error("No WiFi gateway found")
            return False

        # Remove wired gateway
        self.remove_wired_gateway()

        # Set WiFi as default
        return self.set_wifi_as_default(wifi_gw)

    def restore_wired_routing(self) -> bool:
        """Restore wired interface as the default route."""
        return self.restore_wired_gateway()