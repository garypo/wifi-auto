#!/usr/bin/env python3
"""
WiFi Auto-Connect Connectivity Checker Module
Tests whether the machine has Internet connectivity.
"""

import subprocess
import socket
import time
import logging
import urllib.request
import urllib.error
from typing import List, Optional

logger = logging.getLogger(__name__)


class ConnectivityChecker:
    """Check Internet connectivity through multiple methods."""

    def __init__(self, test_urls: List[str] = None,
                 dns_hosts: List[str] = None,
                 ping_hosts: List[str] = None,
                 ping_count: int = 2,
                 ping_timeout: int = 3,
                 test_timeout: int = 10,
                 iface: str = None):
        self.test_urls = test_urls or [
            "http://connectivitycheck.gstatic.com/generate_204",
            "http://captive.apple.com/hotspot-detect.html",
        ]
        self.dns_hosts = dns_hosts or ["google.com", "cloudflare.com"]
        self.ping_hosts = ping_hosts or ["8.8.8.8", "1.1.1.1"]
        self.ping_count = ping_count
        self.ping_timeout = ping_timeout
        self.test_timeout = test_timeout
        self.iface = iface  # If set, test through this specific interface

    def _run(self, cmd: list, timeout: int = 10) -> tuple:
        """Run a command, return (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except FileNotFoundError:
            return -1, "", "command not found"
        except Exception as e:
            return -1, "", str(e)

    def check_http(self) -> tuple:
        """
        Check connectivity by trying to fetch known URLs.
        Returns (has_internet, is_captive_portal).
        """
        for url in self.test_urls:
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent", "Mozilla/5.0 (compatible; connectivity-check)")
                with urllib.request.urlopen(req, timeout=self.test_timeout) as resp:
                    code = resp.getcode()
                    # 204 = no content = pure internet access
                    # 200 = could be captive portal redirect
                    if code == 204:
                        logger.debug(f"HTTP check: {url} returned 204 -> Internet OK")
                        return True, False
                    elif code == 200:
                        # Check if response body matches expected content
                        body = resp.read(1024).decode('utf-8', errors='ignore')
                        # Apple's hotspot detect returns specific HTML when not captive
                        if "hotspot-detect" in url:
                            if "<HTML><HEAD><TITLE>Success</TITLE>" in body:
                                logger.debug(f"HTTP check: {url} -> Internet OK")
                                return True, False
                            else:
                                logger.info(f"HTTP check: {url} returned unexpected "
                                            f"content -> likely captive portal")
                                return False, True
                        # For generate_204 URLs, 200 means captive portal
                        # (the real endpoint returns 204, not 200)
                        if "generate_204" in url:
                            logger.info(f"HTTP check: {url} returned 200 (expected 204) "
                                        f"-> captive portal")
                            return False, True
                        # For other URLs, 200 is acceptable
                        logger.debug(f"HTTP check: {url} returned 200 -> Internet OK")
                        return True, False
                    else:
                        logger.debug(f"HTTP check: {url} returned {code}")
            except urllib.error.HTTPError as e:
                # Some captive portals return error codes
                if e.code in (302, 301):
                    logger.info(f"HTTP check: {url} redirected -> likely captive portal")
                    return False, True
                logger.debug(f"HTTP check: {url} -> HTTPError {e.code}")
            except urllib.error.URLError as e:
                logger.debug(f"HTTP check: {url} -> URLError: {e}")
            except Exception as e:
                logger.debug(f"HTTP check: {url} -> Exception: {e}")

        return False, False

    def check_dns(self) -> bool:
        """Check connectivity by trying DNS resolution."""
        for host in self.dns_hosts:
            try:
                socket.setdefaulttimeout(self.test_timeout)
                socket.getaddrinfo(host, 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
                logger.debug(f"DNS check: resolved {host} -> OK")
                return True
            except socket.gaierror:
                logger.debug(f"DNS check: failed to resolve {host}")
            except Exception as e:
                logger.debug(f"DNS check: {host} -> Exception: {e}")
            finally:
                socket.setdefaulttimeout(None)

        return False

    def check_ping(self) -> bool:
        """Check connectivity by pinging known hosts."""
        for host in self.ping_hosts:
            cmd = ["ping", "-c", str(self.ping_count),
                   "-W", str(self.ping_timeout), host]
            if self.iface:
                cmd.extend(["-I", self.iface])

            rc, out, err = self._run(cmd, timeout=self.ping_timeout + 5)
            if rc == 0 and "0% packet loss" in (out + err):
                logger.debug(f"Ping check: {host} reachable -> OK")
                return True
            logger.debug(f"Ping check: {host} unreachable (rc={rc})")

        return False

    def check_captive_portal(self) -> bool:
        """
        Specifically check if we're behind a captive portal.
        Returns True if a captive portal is detected.
        """
        has_internet, is_captive = self.check_http()
        if is_captive:
            return True
        if has_internet:
            # HTTP check confirmed internet, no portal
            return False

        # Double-check: try to fetch a known non-redirecting URL
        try:
            req = urllib.request.Request(
                "http://detectportal.firefox.com/canonical.html",
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=self.test_timeout) as resp:
                body = resp.read(256).decode('utf-8', errors='ignore')
                if "success" not in body.lower():
                    return True
        except Exception:
            pass

        return False

    def has_internet(self) -> bool:
        """
        Comprehensive Internet connectivity check.
        Uses HTTP, DNS, and ping in sequence.
        """
        # Quick HTTP check first (also detects captive portals)
        has_http, is_captive = self.check_http()
        if has_http and not is_captive:
            return True

        if is_captive:
            logger.info("Captive portal detected, internet not directly available")
            return False

        # If HTTP returned 200 (not 204), it's likely a captive portal
        # Don't fall back to DNS/ping which can work through portals
        # DNS check
        if self.check_dns():
            # DNS works but HTTP didn't return 204 - likely a portal
            logger.info("DNS works but HTTP check failed - possible captive portal")
            return False

        # Ping check as last resort
        if self.check_ping():
            logger.info("Ping works but HTTP and DNS failed - limited connectivity")
            return False

        logger.info("No Internet connectivity detected")
        return False

    def get_status(self) -> dict:
        """Get detailed connectivity status."""
        return {
            "has_internet": self.has_internet(),
            "http": self.check_http(),
            "dns": self.check_dns(),
            "ping": self.check_ping(),
            "timestamp": time.time(),
        }