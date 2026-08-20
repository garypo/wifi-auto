#!/usr/bin/env python3
"""
Generic captive portal handler that accepts terms and agreements.
Works with portals that have a simple "accept terms" form.
"""

import re
import logging
import time
from typing import Optional
from handlers.base import BasePortalHandler, register_handler

logger = logging.getLogger(__name__)


@register_handler
class GenericAcceptHandler(BasePortalHandler):
    """Generic handler for portals with a simple accept/agree form."""

    name = "generic_accept"

    def detect(self) -> bool:
        """Check if there's a generic captive portal with an accept form."""
        portal_url = self._get_portal_url()
        if portal_url:
            body = self._http_get(portal_url)
            if body:
                lower = body.lower()
                if any(kw in lower for kw in [
                    "accept", "agree", "terms", "condition",
                    "i agree", "checkbox", "<form"
                ]):
                    logger.info("Generic accept portal detected")
                    return True
        return False

    def handle(self) -> bool:
        """Handle a generic captive portal by finding and submitting forms."""
        logger.info("Handling generic captive portal...")

        portal_url = self._get_portal_url()
        if not portal_url:
            # Try common portal addresses
            for url in ["http://192.168.1.1/", "http://1.1.1.1/",
                        "http://neverssl.com/"]:
                body = self._http_get(url)
                if body and ("<form" in body.lower() or "accept" in body.lower()):
                    portal_url = url
                    break

        if not portal_url:
            logger.error("Could not find portal URL")
            return False

        logger.info(f"Portal URL: {portal_url}")

        # Try submitting forms up to 3 times (some portals are multi-step)
        current_url = portal_url
        for attempt in range(3):
            body = self._http_get(current_url)
            if not body:
                break

            form_action, form_method, inputs = self._parse_form(body, current_url)

            if not form_action:
                logger.info("No form found, checking connectivity...")
                break

            # Build post data - accept all terms
            post_data = {}
            for inp in inputs:
                name = inp.get('name', '')
                inp_type = inp.get('type', 'text').lower()
                value = inp.get('value', '')

                if inp_type == 'checkbox':
                    post_data[name] = 'on'
                elif inp_type == 'radio':
                    if any(kw in value.lower() for kw in ['accept', 'agree', 'yes']):
                        post_data[name] = value
                    elif name not in post_data:
                        post_data[name] = value
                elif inp_type == 'submit':
                    post_data[name] = value or 'Submit'
                elif inp_type == 'hidden':
                    post_data[name] = value
                elif name:
                    post_data[name] = value or ''

            logger.info(f"Attempt {attempt+1}: Submitting form to {form_action}")
            logger.debug(f"Post data: {post_data}")

            if form_method == "GET":
                import urllib.parse
                params = urllib.parse.urlencode(post_data)
                sep = "&" if "?" in form_action else "?"
                response = self._http_get(f"{form_action}{sep}{params}")
            else:
                response = self._http_post(form_action, post_data)

            time.sleep(3)

            # Check connectivity
            from connectivity import ConnectivityChecker
            checker = ConnectivityChecker(iface=self.iface)
            if checker.has_internet():
                logger.info(f"Portal handled on attempt {attempt+1}!")
                return True

            # Follow to next page if response has another form
            if response:
                current_url = form_action  # Stay on the form action URL

        logger.warning("Failed to handle generic portal after 3 attempts")
        return False

    def _parse_form(self, html: str, base_url: str):
        """Parse HTML form and extract action, method, and inputs."""
        form_action = None
        form_method = "POST"
        inputs = []

        form_match = re.search(r'<form([^>]*)>', html, re.IGNORECASE)
        if not form_match:
            return None, None, []

        form_attrs = form_match.group(1)

        # Extract action
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
        else:
            form_action = base_url

        # Extract method
        method_m = re.search(r'method=["\']?([^"\'>\s]+)', form_attrs,
                             re.IGNORECASE)
        if method_m:
            form_method = method_m.group(1).upper()

        # Find all input elements
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

        # Button elements
        for btn_match in re.finditer(
            r'<button([^>]*)>(.*?)</button>', html,
            re.IGNORECASE | re.DOTALL
        ):
            attrs = btn_match.group(1)
            inp = {}
            for attr_m in re.finditer(
                r'(\w+)=["\']?([^"\'>\s]+)', attrs
            ):
                inp[attr_m.group(1).lower()] = attr_m.group(2)
            inp['type'] = 'submit'
            inp['value'] = re.sub(r'<[^>]+>', '', btn_match.group(2)).strip()
            if inp:
                inputs.append(inp)

        return form_action, form_method, inputs