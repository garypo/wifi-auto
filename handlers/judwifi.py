#!/usr/bin/env python3
"""
Handler for JudWiFi (Judiciary WiFi) captive portal.
The portal at https://wifi1.judiciary.hk:20000/login accepts anonymous
access with a simple POST form submission. No username/password needed
when anonymous=ENABLE.
"""

import logging
import time
import subprocess
import re
import urllib.parse
from typing import Optional
from handlers.base import BasePortalHandler, register_handler

logger = logging.getLogger(__name__)


@register_handler
class JudWiFiHandler(BasePortalHandler):
    """Handler for JudWiFi captive portal."""

    name = "judwifi"

    PORTAL_LOGIN_URL = "https://wifi1.judiciary.hk:20000/login"
    PORTAL_TITLE = "JudWiFi Service"
    PROBE_URL = "http://1.1.1.1/"

    def detect(self) -> bool:
        """Check if we're on a JudWiFi captive portal."""
        try:
            result = subprocess.run(
                ['curl', '-s', '--connect-timeout', '10',
                 self.PROBE_URL],
                capture_output=True, text=True, timeout=15
            )
            body = result.stdout
            if 'judiciary.hk' in body or 'JudWiFi' in body:
                logger.info("JudWiFi portal detected")
                return True
        except Exception as e:
            logger.debug(f"Detect error: {e}")
        return False

    def handle(self) -> bool:
        """Handle the JudWiFi captive portal."""
        logger.info("Handling JudWiFi captive portal...")

        # Step 1: Get the HTTP response body (JudWiFi uses meta-refresh, not
        # Location header, so we need the body to detect the portal)
        try:
            result = subprocess.run(
                ['curl', '-s', '--connect-timeout', '10', self.PROBE_URL],
                capture_output=True, text=True, timeout=15
            )
            body = result.stdout
        except Exception as e:
            logger.error(f"Failed to probe: {e}")
            return False

        # Check if we're actually behind a portal
        if 'judiciary.hk' not in body and 'JudWiFi' not in body:
            # Maybe already authenticated
            from connectivity import ConnectivityChecker
            checker = ConnectivityChecker(iface=self.iface)
            if checker.has_internet():
                logger.info("Already online, no portal handling needed")
                return True
            logger.error("No JudWiFi portal detected")
            return False

        # Extract the RedirectUrl from the meta refresh in the body
        redirect_url = ""
        match = re.search(
            r'URL=(https?://[^\s">]+)', body, re.IGNORECASE
        )
        if match:
            portal_url = match.group(1)
            parsed = urllib.parse.urlparse(portal_url)
            params = urllib.parse.parse_qs(parsed.query)
            redirect_url = params.get('RedirectUrl', [''])[0]

        if not redirect_url:
            redirect_url = self.PROBE_URL

        logger.info(f"RedirectUrl: {redirect_url}")

        # Step 2: POST to the login URL with anonymous access
        post_data = urllib.parse.urlencode({
            'username': 'John',
            'password': 'P@ssw0rd',
            'RedirectUrl': redirect_url,
            'anonymous': 'ENABLE',
            'anonymousurl': 'www.judiciary.hk',
            'checkbox': 'on',
            'checkbox1': 'on',
            'accesscode': '',
        })

        logger.info(f"Submitting login to {self.PORTAL_LOGIN_URL}")

        try:
            result = subprocess.run(
                ['curl', '-s', '--connect-timeout', '10', '-k',
                 '-X', 'POST',
                 '-H', 'Content-Type: application/x-www-form-urlencoded',
                 '-d', post_data,
                 self.PORTAL_LOGIN_URL],
                capture_output=True, text=True, timeout=15
            )
            logger.debug(f"Login response: {result.stdout[:200]}")
        except Exception as e:
            logger.error(f"Login POST failed: {e}")
            return False

        # Step 3: Verify connectivity
        time.sleep(3)
        from connectivity import ConnectivityChecker
        checker = ConnectivityChecker(iface=self.iface)
        if checker.has_internet():
            logger.info("JudWiFi portal handled successfully!")
            return True

        # Wait for provisioning
        logger.info("Waiting for portal provisioning...")
        time.sleep(5)
        if checker.has_internet():
            logger.info("Internet available after provisioning delay")
            return True

        logger.warning("Portal handled but no Internet connectivity")
        return False