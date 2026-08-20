#!/usr/bin/env python3
"""
WiFi Auto-Connect Main Daemon
Periodically checks Internet connectivity and connects to WiFi hotspots
when connectivity is lost. Handles captive portals for each hotspot.
"""

import os
import sys
import time
import logging
import signal
import importlib
import yaml
from pathlib import Path
from typing import Optional, List, Dict

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import WifiScanner, WifiNetwork
from connector import WifiConnector
from connectivity import ConnectivityChecker
from netmgr import NetworkManager
from handlers.base import get_handler, list_handlers, register_handler

logger = logging.getLogger("wifi-auto")


class WifiAutoConnect:
    """
    Main daemon that monitors Internet connectivity and manages
    WiFi connections to public hotspots.
    """

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self._setup_logging()

        # Initialize components
        iface_cfg = self.config.get('interfaces', {})
        wifi_iface = iface_cfg.get('wifi', 'wlan0')
        wired_iface = iface_cfg.get('wired', 'eth0')

        scan_cfg = self.config.get('scan', {})
        conn_cfg = self.config.get('connection', {})
        conn_cfg_full = self.config.get('connectivity', {})

        self.scanner = WifiScanner(
            iface=wifi_iface,
            timeout=scan_cfg.get('scan_timeout', 30),
            signal_threshold=scan_cfg.get('signal_threshold', -85.0)
        )
        self.connector = WifiConnector(
            iface=wifi_iface,
            connect_timeout=conn_cfg.get('connect_timeout', 30),
            dhcp_timeout=conn_cfg.get('dhcp_timeout', 45)
        )
        self.checker = ConnectivityChecker(
            test_urls=conn_cfg_full.get('test_urls'),
            dns_hosts=conn_cfg_full.get('dns_test_hosts'),
            ping_hosts=conn_cfg_full.get('ping_hosts'),
            ping_count=conn_cfg_full.get('ping_count', 2),
            ping_timeout=conn_cfg_full.get('ping_timeout', 3),
            test_timeout=conn_cfg_full.get('test_timeout', 10),
            iface=wifi_iface
        )
        self.netmgr = NetworkManager(
            wifi_iface=wifi_iface,
            wired_iface=wired_iface
        )

        self.wifi_iface = wifi_iface
        self.wired_iface = wired_iface
        self.prefer_wifi = iface_cfg.get('prefer_wifi', True)

        # Load hotspot priority list
        self.hotspots = self._load_hotspots()

        # Load portal handlers
        self._load_handlers()

        # State
        self._running = False
        self._connected_ssid: Optional[str] = None
        self._last_scan = 0.0
        self._scan_cache: List[WifiNetwork] = []

        # Install signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _load_config(self, config_path: str = None) -> dict:
        """Load configuration from YAML file."""
        if not config_path:
            # Default paths
            for p in [
                "/opt/wifi-auto/config.yaml",
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "config.yaml"),
                "config.yaml"
            ]:
                if os.path.exists(p):
                    config_path = p
                    break

        if not config_path or not os.path.exists(config_path):
            logger.warning("No config file found, using defaults")
            return self._default_config()

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        logger.info(f"Loaded config from {config_path}")
        return config

    def _default_config(self) -> dict:
        return {
            'interfaces': {'wifi': 'wlan0', 'wired': 'eth0',
                           'prefer_wifi': True},
            'connectivity': {
                'check_interval': 60,
                'test_urls': [
                    "http://connectivitycheck.gstatic.com/generate_204"
                ],
                'test_timeout': 10,
                'ping_hosts': ['8.8.8.8', '1.1.1.1'],
                'ping_count': 2,
                'ping_timeout': 3,
            },
            'scan': {'scan_timeout': 30, 'signal_threshold': -85.0},
            'connection': {'max_retries': 3, 'connect_timeout': 30,
                           'dhcp_timeout': 45, 'portal_timeout': 60},
            'hotspots': [],
            'logging': {'level': 'INFO', 'file': ''}
        }

    def _setup_logging(self):
        """Configure logging."""
        log_cfg = self.config.get('logging', {})
        level = getattr(logging, log_cfg.get('level', 'INFO').upper(),
                        logging.INFO)
        log_file = log_cfg.get('file', '')

        handlers = [logging.StreamHandler()]
        if log_file:
            from logging.handlers import RotatingFileHandler
            handlers.append(RotatingFileHandler(
                log_file,
                maxBytes=log_cfg.get('max_size_mb', 5) * 1024 * 1024,
                backupCount=log_cfg.get('backup_count', 3)
            ))

        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            handlers=handlers
        )

    def _load_hotspots(self) -> List[dict]:
        """Load and sort the priority hotspot list."""
        hotspots = self.config.get('hotspots', [])
        # Sort by priority (lower = higher priority)
        hotspots.sort(key=lambda h: h.get('priority', 999))
        logger.info(f"Loaded {len(hotspots)} configured hotspots:")
        for h in hotspots:
            logger.info(f"  [{h.get('priority', '?')}] {h['ssid']} "
                        f"(handler={h.get('handler', 'none')})")
        return hotspots

    def _load_handlers(self):
        """Dynamically load all portal handler modules."""
        handler_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'handlers'
        )
        if not os.path.isdir(handler_dir):
            logger.warning(f"Handler directory not found: {handler_dir}")
            return

        for filename in os.listdir(handler_dir):
            if filename.endswith('.py') and filename != '__init__.py' \
                    and filename != 'base.py':
                modname = filename[:-3]
                try:
                    mod = importlib.import_module(f'handlers.{modname}')
                    logger.debug(f"Loaded handler module: {modname}")
                except Exception as e:
                    logger.error(f"Failed to load handler {modname}: {e}")

        logger.info(f"Available handlers: {list_handlers()}")

    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def check_internet(self) -> bool:
        """Check if we have Internet connectivity."""
        return self.checker.has_internet()

    def find_matching_hotspots(self, networks: List[WifiNetwork]
                               ) -> List[tuple]:
        """
        Find configured hotspots that are available in the scan results.
        Returns list of (hotspot_config, WifiNetwork) tuples, sorted by priority.
        """
        matches = []
        for hotspot in self.hotspots:
            ssid = hotspot['ssid']
            for net in networks:
                if net.ssid == ssid:
                    matches.append((hotspot, net))
                    break

        logger.info(f"Found {len(matches)} matching hotspots "
                    f"out of {len(self.hotspots)} configured")
        return matches

    def connect_to_hotspot(self, hotspot: dict,
                           network: WifiNetwork) -> bool:
        """
        Attempt to connect to a specific hotspot and handle its captive portal.

        Args:
            hotspot: Configuration dict for this hotspot
            network: The scanned WifiNetwork object

        Returns:
            True if connected and Internet is available
        """
        ssid = hotspot['ssid']
        psk = hotspot.get('psk', '')
        hidden = hotspot.get('hidden', False)
        handler_name = hotspot.get('handler')
        max_retries = self.config.get('connection', {}).get('max_retries', 3)

        for attempt in range(1, max_retries + 1):
            logger.info(f"Attempt {attempt}/{max_retries} for '{ssid}'...")

            # Connect to the WiFi network
            if not self.connector.connect(network, psk=psk, hidden=hidden):
                logger.warning(f"WiFi connection to '{ssid}' failed "
                               f"(attempt {attempt})")
                time.sleep(5)
                continue

            # If we prefer WiFi, set up routing
            if self.prefer_wifi:
                wifi_gw = self.netmgr.get_wifi_gateway()
                if wifi_gw:
                    self.netmgr.remove_wired_gateway()
                    self.netmgr.set_wifi_as_default(wifi_gw)
                else:
                    logger.warning("No WiFi gateway found, trying without "
                                   "routing changes...")

            # Check if we already have Internet (no captive portal)
            time.sleep(3)
            if self.checker.has_internet():
                logger.info(f"Connected to '{ssid}' with Internet access!")
                self._connected_ssid = ssid
                return True

            # Handle captive portal if a handler is configured
            if handler_name:
                handler = get_handler(handler_name)
                if handler:
                    logger.info(f"Using portal handler '{handler_name}' "
                                f"for '{ssid}'...")
                    try:
                        if handler.handle():
                            # Verify connectivity after portal handling
                            time.sleep(3)
                            if self.checker.has_internet():
                                logger.info(f"Portal handled for '{ssid}'! "
                                            f"Internet is available.")
                                self._connected_ssid = ssid
                                return True
                            else:
                                logger.warning(f"Portal handler succeeded "
                                               f"but still no Internet")
                        else:
                            logger.warning(f"Portal handler '{handler_name}' "
                                           f"failed for '{ssid}'")
                    except Exception as e:
                        logger.error(f"Portal handler error: {e}")
                else:
                    logger.error(f"Handler '{handler_name}' not found. "
                                 f"Available: {list_handlers()}")
            else:
                # No handler configured, check if we're behind a portal
                if self.checker.check_captive_portal():
                    logger.info(f"Captive portal detected for '{ssid}' but "
                                f"no handler configured")
                else:
                    logger.warning(f"Connected to '{ssid}' but no Internet "
                                   f"and no portal detected")

            # Disconnect and retry
            self.connector.disconnect()
            time.sleep(5)

        logger.error(f"Failed to connect to '{ssid}' after {max_retries} "
                     f"attempts")
        return False

    def scan_and_connect(self) -> bool:
        """Scan for WiFi networks and try to connect to the best available."""
        logger.info("Starting scan and connect cycle...")

        # Scan for available networks
        networks = self.scanner.scan()
        self._scan_cache = networks
        self._last_scan = time.time()

        if not networks:
            logger.warning("No WiFi networks found in scan")
            return False

        # Log all found networks
        logger.info("Available networks:")
        for net in networks:
            logger.info(f"  {net.ssid:30s}  {net.signal:6.1f} dBm  "
                        f"{net.security}")

        # Find matching hotspots from our priority list
        matches = self.find_matching_hotspots(networks)

        if not matches:
            logger.warning("No configured hotspots found in scan results")
            return False

        # Try each hotspot in priority order
        for hotspot, network in matches:
            ssid = hotspot['ssid']
            logger.info(f"Trying hotspot '{ssid}' "
                        f"(priority={hotspot.get('priority', '?')})...")

            if self.connect_to_hotspot(hotspot, network):
                return True

            logger.info(f"Hotspot '{ssid}' failed, trying next...")

        logger.error("All configured hotspots exhausted, none succeeded")
        return False

    def run(self):
        """Main daemon loop."""
        logger.info("=" * 60)
        logger.info("WiFi Auto-Connect daemon starting")
        logger.info(f"  WiFi interface: {self.wifi_iface}")
        logger.info(f"  Wired interface: {self.wired_iface}")
        logger.info(f"  Prefer WiFi: {self.prefer_wifi}")
        logger.info(f"  Check interval: "
                    f"{self.config.get('connectivity', {}).get('check_interval', 60)}s")
        logger.info("=" * 60)

        self._running = True
        check_interval = self.config.get('connectivity', {}).get(
            'check_interval', 60)
        rescan_interval = self.config.get('scan', {}).get(
            'rescan_interval', 300)

        while self._running:
            try:
                # Check current connectivity
                has_internet = self.check_internet()

                if has_internet:
                    logger.debug("Internet is available, nothing to do")
                    time.sleep(check_interval)
                    continue

                # No Internet - need to act
                logger.info("Internet connectivity lost! "
                            "Initiating WiFi connection sequence...")

                # If currently connected to a WiFi network, disconnect first
                if self._connected_ssid:
                    logger.info(f"Disconnecting from "
                                f"'{self._connected_ssid}'...")
                    self.connector.disconnect()
                    self._connected_ssid = None
                    time.sleep(3)

                # Scan and connect
                if self.scan_and_connect():
                    logger.info(f"Successfully connected to "
                                f"'{self._connected_ssid}'")
                    # After successful connection, go back to monitoring
                    time.sleep(check_interval)
                else:
                    logger.warning("Failed to connect to any hotspot. "
                                   "Waiting before retry...")
                    # Wait longer after complete failure
                    time.sleep(max(check_interval, 30))

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(check_interval)

        logger.info("WiFi Auto-Connect daemon stopped")

    def run_once(self):
        """Run a single check-and-connect cycle (for testing)."""
        logger.info("Running single check-and-connect cycle...")

        has_internet = self.check_internet()
        if has_internet:
            logger.info("Internet is already available")
            return True

        logger.info("No Internet - scanning and connecting...")
        return self.scan_and_connect()


def main():
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="WiFi Auto-Connect Daemon"
    )
    parser.add_argument(
        '-c', '--config',
        default=None,
        help='Path to config.yaml'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run a single check cycle instead of daemon mode'
    )
    parser.add_argument(
        '--scan',
        action='store_true',
        help='Only scan and list available networks'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show current connection status'
    )
    args = parser.parse_args()

    if args.scan:
        # Just scan
        config_path = args.config
        if config_path:
            with open(config_path) as f:
                config = yaml.safe_load(f)
        else:
            config = {}

        scan_cfg = config.get('scan', {})
        iface = config.get('interfaces', {}).get('wifi', 'wlan0')
        scanner = WifiScanner(
            iface=iface,
            timeout=scan_cfg.get('scan_timeout', 30),
            signal_threshold=scan_cfg.get('signal_threshold', -85.0)
        )
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s [%(levelname)s] %(message)s')
        networks = scanner.scan()
        print(f"\nFound {len(networks)} networks:")
        for net in networks:
            print(f"  {net.ssid:30s}  {net.signal:6.1f} dBm  "
                  f"{net.frequency:5d} MHz  {net.security}")
        return

    if args.status:
        config_path = args.config
        if config_path:
            with open(config_path) as f:
                config = yaml.safe_load(f)
        else:
            config = {}

        iface = config.get('interfaces', {}).get('wifi', 'wlan0')
        wired = config.get('interfaces', {}).get('wired', 'eth0')
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s [%(levelname)s] %(message)s')
        nm = NetworkManager(wifi_iface=iface, wired_iface=wired)
        connector = WifiConnector(iface=iface)
        checker = ConnectivityChecker(iface=iface)

        print("=== Network Status ===")
        print(f"Default gateway: {nm.get_default_gateway()}")
        print(f"Default interface: {nm.get_default_iface()}")
        print(f"WiFi IP ({iface}): {nm.get_interface_ip(iface) or 'none'}")
        print(f"Wired IP ({wired}): {nm.get_interface_ip(wired) or 'none'}")
        print(f"WiFi up: {nm.is_interface_up(iface)}")
        print(f"Wired up: {nm.is_interface_up(wired)}")
        conn = connector.get_current_connection()
        if conn:
            print(f"WiFi connected to: {conn.get('ssid', 'unknown')}")
            print(f"WiFi state: {conn.get('wpa_state', 'unknown')}")
        else:
            print("WiFi: not connected")
        print(f"Internet: {'YES' if checker.has_internet() else 'NO'}")
        return

    # Normal daemon mode
    daemon = WifiAutoConnect(config_path=args.config)

    if args.once:
        success = daemon.run_once()
        sys.exit(0 if success else 1)
    else:
        daemon.run()


if __name__ == '__main__':
    main()