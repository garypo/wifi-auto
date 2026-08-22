#!/usr/bin/env python3
"""
Handler for Wi-Fi.HK public WiFi captive portal.
Uses headless Chrome (Selenium) to handle the portal, following the
approach from the user's wifi-docker project (portals.py).

Chrome navigates to an HTTP URL, the captive portal intercepts and
redirects to a Terms & Conditions page. Chrome clicks the "I Accept"
button, which authenticates the session.

The portal page title is "Terms & Conditions" and the accept button
has class "btn btn-default btn_gw_default wide".
"""

import logging
import time
import subprocess
import re
import urllib.parse
from typing import Optional
from handlers.base import BasePortalHandler, register_handler

logger = logging.getLogger(__name__)

# Portal page signature (same across all Wi-Fi.HK deployments)
WIFI_HK_TITLE = "Terms & Conditions"
WIFI_HK_BUTTON_XPATH = (
    "//button[@type='button']"
    "[@class='btn btn-default btn_gw_default wide']"
)


@register_handler
class WifiHkHandler(BasePortalHandler):
    """Handler for Wi-Fi.HK captive portal using headless Chrome."""

    name = "wifi_hk"
    PROBE_URL = "http://neverssl.com"

    def __init__(self, iface: str = "wlan0", timeout: int = 60):
        super().__init__(iface, timeout)
        self._chrome_proc = None

    def detect(self) -> bool:
        """Check if we are on a Wi-Fi.HK captive portal."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--connect-timeout", "10",
                 "-D", "-", "-o", "/dev/null", "http://1.1.1.1/"],
                capture_output=True, text=True, timeout=15
            )
            headers = result.stdout.lower()
            if "wifi.gov.hk" in headers or "wifi.ecp" in headers:
                logger.info("Wi-Fi.HK portal detected (redirect)")
                return True
            # Also check body for meta refresh redirect to wifi.gov.hk
            result2 = subprocess.run(
                ["curl", "-s", "--connect-timeout", "10", "http://1.1.1.1/"],
                capture_output=True, text=True, timeout=15
            )
            if "wifi.gov.hk" in result2.stdout:
                logger.info("Wi-Fi.HK portal detected (meta refresh)")
                return True
        except Exception as e:
            logger.debug(f"Detect error: {e}")
        return False

    def handle(self) -> bool:
        """Handle the Wi-Fi.HK captive portal using headless Chrome."""
        logger.info("Handling Wi-Fi.HK captive portal via Chrome...")

        try:
            browser = self._start_chrome()
            if not browser:
                logger.error("Failed to start browser")
                return False

            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            from selenium.common.exceptions import (
                TimeoutException, NoSuchElementException, WebDriverException
            )

            # Navigate to HTTP probe URL (portal will intercept)
            logger.info(f"Navigating to {self.PROBE_URL}...")
            try:
                browser.get(self.PROBE_URL)
            except Exception as e:
                logger.warning(f"Navigation exception: {e}")

            # Wait for page to load
            try:
                WebDriverWait(browser, 15).until(
                    lambda d: d.execute_script(
                        "return document.readyState"
                    ) == "complete"
                )
            except TimeoutException:
                logger.warning("Page load timeout, continuing anyway...")

            title = browser.title or ""
            url = browser.current_url or ""
            logger.info(f"Portal page: title={title!r}, url={url[:100]}")

            # Wait for the specific portal title
            try:
                WebDriverWait(browser, 15).until(EC.title_is(WIFI_HK_TITLE))
                logger.info("Terms & Conditions page loaded")
            except TimeoutException:
                logger.warning(f"Expected title {WIFI_HK_TITLE!r} but got {title!r}")
                # Check if already connected
                from connectivity import ConnectivityChecker
                checker = ConnectivityChecker(iface=self.iface)
                if checker.has_internet():
                    logger.info("Already online, no portal handling needed")
                    self._stop_chrome(browser)
                    return True
                if "wifi.gov.hk" not in url and "terms" not in title.lower():
                    logger.error("Unknown portal page, cannot handle")
                    self._stop_chrome(browser)
                    return False

            # Find and click the accept button
            logger.info("Looking for accept button...")
            try:
                button = WebDriverWait(browser, 10).until(
                    EC.presence_of_element_located(
                        (By.XPATH, WIFI_HK_BUTTON_XPATH)
                    )
                )
                logger.info(f"Found accept button, clicking...")
                button.click()

                # Wait for title to change (portal accepted)
                try:
                    WebDriverWait(browser, 10).until_not(
                        EC.title_is(WIFI_HK_TITLE)
                    )
                    logger.info("Portal accepted (title changed)")
                except TimeoutException:
                    logger.warning("Title did not change after clicking accept")

            except (TimeoutException, NoSuchElementException) as e:
                logger.warning(f"Could not find/click primary accept button: {e}")
                if not self._try_alternative_buttons(browser):
                    logger.error("No accept button found")
                    self._stop_chrome(browser)
                    return False

            # Wait for connectivity
            time.sleep(5)

            # Verify internet
            from connectivity import ConnectivityChecker
            checker = ConnectivityChecker(iface=self.iface)
            if checker.has_internet():
                logger.info("Wi-Fi.HK portal handled successfully!")
                self._stop_chrome(browser)
                return True

            logger.info("Waiting for portal provisioning...")
            time.sleep(10)
            if checker.has_internet():
                logger.info("Internet available after provisioning delay")
                self._stop_chrome(browser)
                return True

            logger.warning("Portal handled but no Internet connectivity")
            self._stop_chrome(browser)
            return False

        except Exception as e:
            logger.error(f"Portal handling error: {e}", exc_info=True)
            return False

    def _try_alternative_buttons(self, browser) -> bool:
        """Try alternative button selectors."""
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import (
            NoSuchElementException, WebDriverException
        )

        alternatives = [
            "//button[contains(@class, 'btn_gw_default')]",
            "//button[contains(@class, 'wide')]",
            "//button[contains(text(), 'Accept')]",
            "//button[contains(text(), 'agree')]",
            "//button[contains(text(), 'Agree')]",
            "//button[contains(text(), 'OK')]",
            "//button[contains(text(), 'Continue')]",
            "//input[@type='submit' and contains(@value, 'Accept')]",
            "//input[@type='submit' and contains(@value, 'Agree')]",
            "//input[@type='button' and contains(@value, 'Accept')]",
            "//button[@type='button']",
            "//input[@type='submit']",
        ]

        for xpath in alternatives:
            try:
                button = browser.find_element(By.XPATH, xpath)
                logger.info(f"Found button with xpath: {xpath}")
                button.click()
                time.sleep(3)
                return True
            except (NoSuchElementException, WebDriverException):
                continue
        return False

    def _start_chrome(self):
        """Start headless Chrome and return a Selenium WebDriver."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        # Find chromedriver -- try snap wrapper first, then direct path
        chromedriver_path = None
        for path in [
            "/usr/local/bin/chromedriver-snap",
            "/usr/local/bin/chromedriver",
        ]:
            try:
                import os
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    chromedriver_path = path
                    break
            except Exception:
                continue

        # Find chromium binary
        chrome_binary = None
        for path in [
            "/snap/bin/chromium",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/opt/chrome/chrome-linux64/chrome",
        ]:
            try:
                import os
                if os.path.isfile(path):
                    chrome_binary = path
                    break
            except Exception:
                continue

        if not chromedriver_path:
            logger.error("chromedriver not found")
            return None
        if not chrome_binary:
            logger.error("chromium browser not found")
            return None

        # Start chromium with remote debugging
        self._chrome_proc = subprocess.Popen(
            [
                chrome_binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--ignore-ssl-errors=yes",
                "--ignore-certificate-errors",
                "--remote-debugging-port=9222",
                "--remote-debugging-address=127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(3)

        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--ignore-ssl-errors=yes")
        opts.add_argument("--ignore-certificate-errors")
        opts.debugger_address = "127.0.0.1:9222"
        opts.page_load_strategy = "eager"

        service = Service(executable_path=chromedriver_path)
        browser = webdriver.Chrome(service=service, options=opts)
        browser.set_page_load_timeout(30)
        logger.debug(f"Browser started (chrome={chrome_binary}, driver={chromedriver_path})")
        return browser

    def _stop_chrome(self, browser):
        """Stop the browser and cleanup."""
        try:
            browser.quit()
        except Exception:
            pass
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_proc.kill()
                except Exception:
                    pass
            self._chrome_proc = None

    # --- Legacy openssl methods (kept for compatibility) ---
    def _get_redirect_url(self) -> Optional[str]:
        """Get the captive portal redirect URL from HTTP probe."""
        for attempt in range(3):
            try:
                result = subprocess.run(
                    ["curl", "-s", "--connect-timeout", "15",
                     "-D", "-", "-o", "/dev/null", "http://1.1.1.1/"],
                    capture_output=True, text=True, timeout=20
                )
                match = re.search(r"Location:\s*(\S+)", result.stdout, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
            except Exception:
                pass
            time.sleep(2)
        return None

    def _get_wifi_gateway(self) -> Optional[str]:
        """Get the WiFi gateway IP."""
        try:
            import os
            ip_cmd = os.environ.get("IP_CMD", "ip")
            if not os.path.isfile("/usr/sbin/ip"):
                ip_cmd = "/usr/sbin/ip"
            result = subprocess.run(
                [ip_cmd, "route", "show", "dev", self.iface],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if "default via" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2]
        except Exception:
            pass
        return None
