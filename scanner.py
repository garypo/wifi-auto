#!/usr/bin/env python3
"""
WiFi Auto-Connect Scanner Module
Scans for available WiFi networks using iw / wpa_cli.
"""

import subprocess
import re
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WifiNetwork:
    ssid: str
    bssid: str = ""
    signal: float = 0.0       # dBm
    frequency: int = 0         # MHz
    channel: int = 0
    security: str = ""         # e.g. "WPA2-PSK-CCMP", "open"
    is_open: bool = False
    seen_at: float = field(default_factory=time.time)

    def __repr__(self):
        return (f"WifiNetwork(ssid={self.ssid!r}, signal={self.signal}dBm, "
                f"freq={self.frequency}MHz, security={self.security})")


class WifiScanner:
    """Scan for available WiFi networks."""

    def __init__(self, iface: str = "wlan0", timeout: int = 30,
                 signal_threshold: float = -85.0):
        self.iface = iface
        self.timeout = timeout
        self.signal_threshold = signal_threshold
        self._last_scan = 0.0

    def _run(self, cmd: list, timeout: int = None) -> str:
        """Run a command and return stdout."""
        timeout = timeout or self.timeout
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.warning(f"Command timed out: {' '.join(cmd)}")
            return ""
        except FileNotFoundError:
            logger.error(f"Command not found: {cmd[0]}")
            return ""
        except Exception as e:
            logger.error(f"Command failed: {cmd}: {e}")
            return ""

    def _run_shell(self, cmd: str, timeout: int = None) -> str:
        """Run a shell command string and return stdout+stderr."""
        timeout = timeout or self.timeout
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            logger.warning(f"Shell command timed out: {cmd}")
            return ""
        except Exception as e:
            logger.error(f"Shell command failed: {cmd}: {e}")
            return ""

    def ensure_interface_up(self) -> bool:
        """Make sure the WiFi interface is up."""
        output = self._run(["ip", "-br", "link", "show", self.iface])
        if "UP" in output:
            logger.debug(f"Interface {self.iface} is already UP")
            return True

        logger.info(f"Bringing interface {self.iface} up...")
        result = subprocess.run(
            ["sudo", "ip", "link", "set", self.iface, "up"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            logger.error(f"Failed to bring up {self.iface}: {result.stderr}")
            return False

        # Unblock radio if needed
        subprocess.run(
            ["sudo", "rfkill", "unblock", "wifi"],
            capture_output=True, text=True, timeout=5
        )

        time.sleep(2)
        output = self._run(["ip", "-br", "link", "show", self.iface])
        is_up = "UP" in output
        logger.info(f"Interface {self.iface} is {'UP' if is_up else 'still DOWN'}")
        return is_up

    def scan_iw(self) -> List[WifiNetwork]:
        """Scan using the iw command."""
        if not self.ensure_interface_up():
            logger.error("Cannot scan: interface is down")
            return []

        logger.info(f"Starting WiFi scan on {self.iface}...")
        # Try with sudo first, then without (user may be in netdev group)
        output = self._run_shell(f"sudo iw dev {self.iface} scan ap-force 2>&1",
                                 self.timeout)
        if not output or "command not found" in output:
            output = self._run(["iw", "dev", self.iface, "scan"], self.timeout)

        return self._parse_iw_scan(output)

    def _parse_iw_scan(self, output: str) -> List[WifiNetwork]:
        """Parse iw scan output into WifiNetwork objects."""
        networks = []
        current = {}

        for line in output.split('\n'):
            line = line.strip()

            # New BSS entry
            if line.startswith('BSS '):
                if current.get('ssid'):
                    net = self._make_network(current)
                    if net:
                        networks.append(net)
                current = {
                    'bssid': line.split()[1] if len(line.split()) > 1 else ''
                }
                continue

            if not current:
                continue

            # SSID
            if line.startswith('SSID: '):
                current['ssid'] = line[6:].strip()
            # Signal
            elif line.startswith('signal: '):
                m = re.search(r'signal:\s*(-?[\d.]+)\s*dBm', line)
                if m:
                    current['signal'] = float(m.group(1))
            # Frequency
            elif line.startswith('freq: '):
                try:
                    current['frequency'] = int(line[6:].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            # Channel
            elif line.startswith('channel '):
                m = re.match(r'channel\s+(\d+)\s+', line)
                if m:
                    current['channel'] = int(m.group(1))
            # Security
            elif 'capability:' in line.lower():
                if 'privacy' in line.lower():
                    current['has_privacy'] = True
            elif line.startswith('RSN:') or line.startswith('WPA:'):
                current['has_encryption'] = True
                if 'CCMP' in line:
                    current['cipher'] = 'CCMP'
                elif 'TKIP' in line:
                    current['cipher'] = 'TKIP'
            elif 'AKM' in line or 'Pairwise' in line or 'Group' in line:
                if 'PSK' in line:
                    current['auth'] = 'PSK'
                elif 'SAE' in line:
                    current['auth'] = 'SAE'

        # Last entry
        if current.get('ssid'):
            net = self._make_network(current)
            if net:
                networks.append(net)

        # Filter by signal threshold and deduplicate by SSID (keep strongest)
        seen = {}
        for net in networks:
            if net.signal < self.signal_threshold:
                continue
            if net.ssid not in seen or net.signal > seen[net.ssid].signal:
                seen[net.ssid] = net

        result = sorted(seen.values(), key=lambda n: n.signal, reverse=True)
        logger.info(f"Scan found {len(result)} unique networks "
                    f"(threshold={self.signal_threshold}dBm)")
        for n in result:
            logger.debug(f"  {n.ssid}: {n.signal}dBm, {n.security}")
        return result

    def _make_network(self, data: dict) -> Optional[WifiNetwork]:
        """Create a WifiNetwork from parsed scan data."""
        ssid = data.get('ssid', '')
        if not ssid or ssid == '\\x00':
            return None

        has_enc = (data.get('has_encryption', False) or
                   data.get('has_privacy', False))
        if has_enc:
            cipher = data.get('cipher', '')
            auth = data.get('auth', '')
            if auth == 'SAE':
                security = f"WPA3-{auth}-{cipher}" if cipher else f"WPA3-{auth}"
            elif auth == 'PSK':
                security = f"WPA2-{auth}-{cipher}" if cipher else "WPA2-PSK"
            else:
                security = "WPA-encrypted"
            is_open = False
        else:
            security = "open"
            is_open = True

        freq = data.get('frequency', 0)
        channel = data.get('channel', 0)
        if not channel and freq:
            channel = self._freq_to_channel(freq)

        return WifiNetwork(
            ssid=ssid,
            bssid=data.get('bssid', ''),
            signal=data.get('signal', -100.0),
            frequency=freq,
            channel=channel,
            security=security,
            is_open=is_open
        )

    @staticmethod
    def _freq_to_channel(freq: int) -> int:
        """Convert MHz frequency to channel number."""
        if freq == 2484:
            return 14
        if 2412 <= freq <= 2472:
            return (freq - 2407) // 5
        if 5160 <= freq <= 5885:
            return (freq - 5000) // 5
        return 0

    def scan(self) -> List[WifiNetwork]:
        """Perform a WiFi scan and return sorted list of networks."""
        self._last_scan = time.time()
        return self.scan_iw()