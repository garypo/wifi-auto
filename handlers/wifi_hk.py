#!/usr/bin/env python3
"""
Handler for Wi-Fi.HK public WiFi captive portal.
Uses openssl s_client to handle the portal's HTTPS redirects (curl's TLS
handshake times out against the captive portal's TLS proxy, but openssl
works). Falls back to Selenium/headless Chrome if available.

Portal flow:
1. HTTP to any external IP -> 302 redirect to
   https://free-m.wifi.gov.hk/ecp/api/create-session?...&form_action=https://free-m.wifi.ecp.forward:8000/portal.cgi
2. GET create-session URL -> returns HTML page with "Terms & Conditions" title
   and an accept button
3. Click/submit the accept button -> portal accepts, internet is enabled

The challenge: the portal's HTTPS server is a TLS proxy on the gateway
(10.30.32.1) that only works with openssl's TLS implementation, not curl's.
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
class WifiHkHandler(BasePortalHandler):
    """Handler for Wi-Fi.HK captive portal."""

    name = "wifi_hk"

    # Portal page signature
    PORTAL_TITLE = "Terms & Conditions"
    ACCEPT_BUTTON_XPATH = (
        "//button[@type='button']"
        "[@class='btn btn-default btn_gw_default wide']"
    )
    PROBE_URL = "http://1.1.1.1/"

    def __init__(self, iface: str = "wlan0", timeout: int = 60):
        super().__init__(iface, timeout)

    def detect(self) -> bool:
        """Check if we're on a Wi-Fi.HK captive portal."""
        # Check if HTTP is being intercepted
        try:
            result = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5',
                 '-D', '-', '-o', '/dev/null', self.PROBE_URL],
                capture_output=True, text=True, timeout=10
            )
            headers = result.stdout.lower()
            if 'wifi.gov.hk' in headers or 'wifi.ecp' in headers:
                logger.info("Wi-Fi.HK portal detected (redirect)")
                return True
        except Exception as e:
            logger.debug(f"Detect error: {e}")
        return False

    def handle(self) -> bool:
        """Handle the Wi-Fi.HK captive portal."""
        logger.info("Handling Wi-Fi.HK captive portal...")

        # Step 1: Get the redirect URL from HTTP probe
        redirect_url = self._get_redirect_url()
        if not redirect_url:
            logger.error("Could not get portal redirect URL")
            return False

        logger.info(f"Redirect URL: {redirect_url[:100]}...")

        # Parse the redirect URL
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)
        portal_host = parsed.hostname  # free-m.wifi.gov.hk
        portal_path = parsed.path + '?' + parsed.query

        # Get the gateway IP (the TLS proxy)
        gateway_ip = self._get_wifi_gateway()
        if not gateway_ip:
            logger.error("Could not determine WiFi gateway")
            return False

        logger.info(f"Portal host: {portal_host}, Gateway: {gateway_ip}")

        # Step 2: Fetch the create-session page via openssl
        logger.info("Fetching portal page via openssl...")
        page = self._https_get_via_openssl(
            gateway_ip, 443, portal_host, portal_path
        )

        if not page:
            logger.error("Failed to fetch portal page")
            return False

        logger.info(f"Got portal page ({len(page)} chars)")
        logger.debug(f"Page content: {page[:500]}")

        # Step 3: Parse the page for the form and submit it
        if self._handle_portal_page(page, params, gateway_ip):
            # Step 4: Verify connectivity
            time.sleep(5)
            from connectivity import ConnectivityChecker
            checker = ConnectivityChecker(iface=self.iface)
            if checker.has_internet():
                logger.info("Wi-Fi.HK portal handled successfully!")
                return True

            # Wait longer for provisioning
            logger.info("Waiting for portal provisioning...")
            time.sleep(10)
            if checker.has_internet():
                logger.info("Internet available after provisioning delay")
                return True

        # Fall back to Selenium/Chrome approach
        logger.info("OpenSSL approach failed, trying Chrome...")
        return self._handle_via_chrome()

    def _get_redirect_url(self) -> Optional[str]:
        """Get the captive portal redirect URL from HTTP probe."""
        for attempt in range(3):
            try:
                result = subprocess.run(
                    ['curl', '-s', '--connect-timeout', '15',
                     '-D', '-', '-o', '/dev/null', self.PROBE_URL],
                    capture_output=True, text=True, timeout=20
                )
                headers = result.stdout
                match = re.search(r'Location:\s*(\S+)', headers, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            except subprocess.TimeoutExpired:
                logger.debug(f"Get redirect attempt {attempt+1}: timeout")
            except Exception as e:
                logger.debug(f"Get redirect attempt {attempt+1}: {e}")
            time.sleep(2)
        logger.error("Could not get portal redirect URL after 3 attempts")
        return None

    def _get_wifi_gateway(self) -> Optional[str]:
        """Get the WiFi gateway IP."""
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'dev', self.iface],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'default via' in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2]
        except Exception:
            pass
        # Try from the redirect URL params
        return None

    def _https_get_via_openssl(self, ip: str, port: int,
                               hostname: str, path: str,
                               max_retries: int = 5) -> Optional[str]:
        """Make an HTTPS GET request using openssl s_client.
        The captive portal's TLS proxy is intermittent, so we retry."""
        http_request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {hostname}\r\n"
            f"User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
            f"Gecko/20100101 Firefox/120.0\r\n"
            f"Accept: text/html,application/xhtml+xml,*/*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )

        for attempt in range(max_retries):
            try:
                logger.debug(f"openssl GET attempt {attempt+1}/{max_retries}")
                result = subprocess.run(
                    ['openssl', 's_client', '-connect', f'{ip}:{port}',
                     '-servername', hostname, '-quiet'],
                    input=http_request,
                    capture_output=True, text=True, timeout=15
                )
                if result.stdout and 'HTTP/' in result.stdout:
                    return result.stdout
                logger.debug(f"Attempt {attempt+1}: no HTTP response")
            except subprocess.TimeoutExpired:
                logger.debug(f"Attempt {attempt+1}: timeout")
            except Exception as e:
                logger.debug(f"Attempt {attempt+1}: error: {e}")
            time.sleep(1)

        logger.warning(f"openssl GET failed after {max_retries} attempts")
        return None

    def _https_post_via_openssl(self, ip: str, port: int,
                                hostname: str, path: str,
                                data: str, max_retries: int = 5) -> Optional[str]:
        """Make an HTTPS POST request using openssl s_client.
        Retries because the portal's TLS proxy is intermittent."""
        http_request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {hostname}\r\n"
            f"User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
            f"Gecko/20100101 Firefox/120.0\r\n"
            f"Accept: text/html,application/xhtml+xml,*/*\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{data}"
        )

        for attempt in range(max_retries):
            try:
                logger.debug(f"openssl POST attempt {attempt+1}/{max_retries}")
                result = subprocess.run(
                    ['openssl', 's_client', '-connect', f'{ip}:{port}',
                     '-servername', hostname, '-quiet'],
                    input=http_request,
                    capture_output=True, text=True, timeout=15
                )
                if result.stdout and 'HTTP/' in result.stdout:
                    return result.stdout
                logger.debug(f"POST attempt {attempt+1}: no HTTP response")
            except subprocess.TimeoutExpired:
                logger.debug(f"POST attempt {attempt+1}: timeout")
            except Exception as e:
                logger.debug(f"POST attempt {attempt+1}: error: {e}")
            time.sleep(1)

        logger.warning(f"openssl POST failed after {max_retries} attempts")
        return None

    def _handle_portal_page(self, page: str, params: dict,
                            gateway_ip: str) -> bool:
        """Parse the portal page and submit the acceptance form."""
        # Check for the form
        form_match = re.search(r'<form([^>]*)>(.*?)</form>',
                               page, re.IGNORECASE | re.DOTALL)
        if not form_match:
            logger.warning("No form found in portal page")
            # Maybe the page itself contains a button we can "click"
            # by submitting to the form_action URL
            return self._submit_acceptance(params, gateway_ip)

        form_attrs = form_match.group(1)
        form_inner = form_match.group(2)

        # Extract form action
        action_match = re.search(r'action=["\']?([^"\'>\s]+)',
                                 form_attrs, re.IGNORECASE)
        if action_match:
            action = action_match.group(1)
        else:
            action = params.get('form_action', [''])[0]

        if not action:
            logger.error("No form action found")
            return False

        # Parse action URL
        action_parsed = urllib.parse.urlparse(action)
        action_host = action_parsed.hostname
        action_port = action_parsed.port or 443
        action_path = action_parsed.path

        # Extract all input fields
        inputs = {}
        for inp_match in re.finditer(
            r'<input([^>]*)/?>', form_inner, re.IGNORECASE
        ):
            attrs = inp_match.group(1)
            name = None
            value = ''
            for attr_match in re.finditer(
                r'(\w+)=["\']?([^"\'>\s]+)', attrs
            ):
                if attr_match.group(1).lower() == 'name':
                    name = attr_match.group(2)
                elif attr_match.group(1).lower() == 'value':
                    value = attr_match.group(2)
            if name:
                inputs[name] = value

        # Also look for button elements
        for btn_match in re.finditer(
            r'<button([^>]*)>(.*?)</button>',
            form_inner, re.IGNORECASE | re.DOTALL
        ):
            attrs = btn_match.group(1)
            btn_text = re.sub(r'<[^>]+>', '', btn_match.group(2)).strip()
            for attr_match in re.re.finditer(
                r'(\w+)=["\']?([^"\'>\s]+)', attrs
            ):
                if attr_match.group(1).lower() == 'name':
                    inputs[attr_match.group(2)] = btn_text

        # Add the redirect params if not already in inputs
        for k, v in params.items():
            if k not in inputs:
                inputs[k] = v[0]

        # Add accept button
        inputs.setdefault('buttonAccept', 'Accept')
        inputs.setdefault('accept', 'on')

        logger.info(f"Submitting form to {action_host}:{action_port}{action_path}")
        logger.debug(f"Form data: {inputs}")

        post_data = urllib.parse.urlencode(inputs)

        # Submit via openssl
        response = self._https_post_via_openssl(
            gateway_ip, action_port, action_host,
            action_path + '?' + urllib.parse.urlencode(inputs),
            post_data
        )

        if response:
            logger.info(f"Form submitted, response: {response[:200]}")
            return True

        return False

    def _submit_acceptance(self, params: dict, gateway_ip: str) -> bool:
        """Submit acceptance directly to portal.cgi."""
        form_action = params.get('form_action', [''])[0]
        if not form_action:
            return False

        parsed = urllib.parse.urlparse(form_action)
        host = parsed.hostname
        port = parsed.port or 443
        path = parsed.path

        # Build POST data from params
        post_data = urllib.parse.urlencode(
            {k: v[0] for k, v in params.items()}
        )
        post_data += '&buttonAccept=Accept'

        logger.info(f"Direct submit to {host}:{port}{path}")
        response = self._https_post_via_openssl(
            gateway_ip, port, host, path, post_data
        )

        if response and '502' not in response:
            logger.info(f"Submit response: {response[:200]}")
            return True

        return False

    def _handle_via_chrome(self) -> bool:
        """Fall back to Chrome/Selenium approach."""
        try:
            browser = self._start_chrome()
            if not browser:
                return False

            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            from selenium.common.exceptions import (
                TimeoutException, NoSuchElementException
            )

            browser.get(self.PROBE_URL)
            time.sleep(5)

            try:
                WebDriverWait(browser, 10).until(
                    EC.title_is(self.PORTAL_TITLE)
                )
                button = browser.find_element(
                    By.XPATH, self.ACCEPT_BUTTON_XPATH
                )
                button.click()
                time.sleep(5)
                return True
            except (TimeoutException, NoSuchElementException):
                pass

            browser.quit()
        except Exception as e:
            logger.warning(f"Chrome approach failed: {e}")
        return False

    def _start_chrome(self):
        """Start headless Chrome for fallback."""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            proc = subprocess.Popen(
                ['/snap/bin/chromium',
                 '--headless=new', '--no-sandbox',
                 '--disable-dev-shm-usage', '--disable-gpu',
                 '--ignore-ssl-errors=yes',
                 '--ignore-certificate-errors',
                 '--remote-debugging-port=9222',
                 '--remote-debugging-address=127.0.0.1'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(3)

            opts = Options()
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.debugger_address = '127.0.0.1:9222'
            service = Service(executable_path='/usr/local/bin/chromedriver')
            return webdriver.Chrome(service=service, options=opts)
        except Exception as e:
            logger.warning(f"Could not start Chrome: {e}")
            return None