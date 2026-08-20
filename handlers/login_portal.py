#!/usr/bin/env python3
"""
Handler for captive portals that require login with username/password.
Template for portals that need credentials (e.g., hotel/airport WiFi).
"""

import re
import logging
import time
from typing import Optional
from handlers.base import BasePortalHandler, register_handler

logger = logging.getLogger(__name__)


@register_handler
class LoginPortalHandler(BasePortalHandler):
    """Handler for portals requiring username/password login."""

    name = "login_portal"

    def __init__(self, iface: str = "wlan0", timeout: int = 60,
                 username: str = "", password: str = ""):
        super().__init__(iface, timeout)
        self.username = username
        self.password = password

    def detect(self) -> bool:
        """Check if portal has a login form."""
        portal_url = self._get_portal_url()
        if portal_url:
            body = self._http_get(portal_url)
            if body:
                lower = body.lower()
                if any(kw in lower for kw in [
                    "username", "user_id", "login", "password",
                    "userid", "user name", "account"
                ]):
                    logger.info("Login portal detected")
                    return True
        return False

    def handle(self) -> bool:
        """Handle a login-based captive portal."""
        if not self.username or not self.password:
            logger.error("No credentials configured for login portal")
            return False

        logger.info("Handling login captive portal...")

        portal_url = self._get_portal_url()
        if not portal_url:
            for url in ["http://192.168.1.1/", "http://1.1.1.1/",
                        "http://neverssl.com/"]:
                body = self._http_get(url)
                if body and "password" in body.lower():
                    portal_url = url
                    break

        if not portal_url:
            logger.error("Could not find login portal URL")
            return False

        body = self._http_get(portal_url)
        form_action, form_method, inputs = self._parse_form(body, portal_url)

        if not form_action:
            logger.error("No form found in login portal")
            return False

        # Fill in credentials
        post_data = {}
        for inp in inputs:
            name = inp.get('name', '')
            inp_type = inp.get('type', 'text').lower()
            value = inp.get('value', '')

            if inp_type == 'password':
                post_data[name] = self.password
            elif inp_type in ('text', 'email', 'tel') and any(
                kw in name.lower() for kw in ['user', 'login', 'account', 'name']
            ):
                post_data[name] = self.username
            elif inp_type == 'hidden':
                post_data[name] = value
            elif inp_type == 'submit':
                post_data[name] = value or 'Login'
            elif inp_type == 'checkbox':
                post_data[name] = 'on'
            elif name:
                post_data[name] = value

        logger.info(f"Submitting login form to {form_action}")
        response = self._http_post(form_action, post_data)

        time.sleep(5)

        from connectivity import ConnectivityChecker
        checker = ConnectivityChecker(iface=self.iface)
        return checker.has_internet()

    def _parse_form(self, html: str, base_url: str):
        """Parse HTML form."""
        form_action = base_url
        form_method = "POST"
        inputs = []

        form_match = re.search(r'<form([^>]*)>', html, re.IGNORECASE)
        if form_match:
            form_attrs = form_match.group(1)
            action_m = re.search(r'action=["\']?([^"\'>\s]+)', form_attrs,
                                 re.IGNORECASE)
            if action_m:
                action = action_m.group(1)
                if action.startswith("http"):
                    form_action = action
                elif action.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(base_url)
                    form_action = f"{parsed.scheme}://{parsed.netloc}{action}"
                else:
                    from urllib.parse import urljoin
                    form_action = urljoin(base_url, action)

            method_m = re.search(r'method=["\']?([^"\'>\s]+)', form_attrs,
                                 re.IGNORECASE)
            if method_m:
                form_method = method_m.group(1).upper()

        for inp_match in re.finditer(
            r'<input([^>]*)/?>', html, re.IGNORECASE
        ):
            attrs = inp_match.group(1)
            inp = {}
            for attr_m in re.finditer(
                r'(\w+)=["\']?([^"\'>\s]+)', attrs
            ):
                inp[attr_m.group(1).lower()] = attr_m.group(2)
            if inp:
                inputs.append(inp)

        return form_action, form_method, inputs