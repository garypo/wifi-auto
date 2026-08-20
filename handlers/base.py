#!/usr/bin/env python3
"""
Base class for captive portal handlers.
Each handler knows how to interact with a specific captive portal
(accept terms, click buttons, fill forms, etc.)
"""

import logging
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BasePortalHandler(ABC):
    """Abstract base class for captive portal handlers."""

    name = "base"

    def __init__(self, iface: str = "wlan0", timeout: int = 60):
        self.iface = iface
        self.timeout = timeout

    @abstractmethod
    def handle(self) -> bool:
        """
        Handle the captive portal.

        Returns:
            True if the portal was successfully handled and Internet
            access should now be available.
        """
        pass

    @abstractmethod
    def detect(self) -> bool:
        """
        Check if this handler is appropriate for the current captive portal.

        Returns:
            True if this handler can handle the current portal.
        """
        pass

    def _get_portal_url(self) -> Optional[str]:
        """Try to find the captive portal redirect URL."""
        import urllib.request
        import urllib.error

        # Try to follow redirects from known check URLs
        check_urls = [
            "http://neverssl.com/",
            "http://1.1.1.1/",
            "http://example.com/",
        ]

        for url in check_urls:
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("User-Agent",
                               "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
                               "Gecko/20100101 Firefox/120.0")
                # Don't follow redirects
                opener = urllib.request.build_opener(NoRedirectHandler)
                opener.open(req, timeout=10)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    location = e.headers.get("Location", "")
                    if location:
                        logger.debug(f"Portal redirect: {url} -> {location}")
                        return location
            except Exception as e:
                logger.debug(f"Portal detection {url}: {e}")

        # If no redirect, try fetching a page and look for meta refresh
        try:
            req = urllib.request.Request("http://neverssl.com/")
            req.add_header("User-Agent",
                           "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
                           "Gecko/20100101 Firefox/120.0")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read(4096).decode('utf-8', errors='ignore')
                # Look for meta refresh redirect
                import re
                m = re.search(
                    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+'
                    r'content=["\']?\d+;\s*url=([^"\'>\s]+)',
                    body, re.IGNORECASE
                )
                if m:
                    url = m.group(1)
                    if not url.startswith("http"):
                        url = "http://" + url
                    logger.debug(f"Meta refresh portal URL: {url}")
                    return url
                # The page itself might be the portal
                if "<form" in body.lower() or "accept" in body.lower():
                    logger.debug("Portal page detected (form/accept found)")
                    return "http://neverssl.com/"
        except Exception as e:
            logger.debug(f"Portal page fetch: {e}")

        return None

    def _http_get(self, url: str, follow_redirects: bool = True) -> Optional[str]:
        """HTTP GET and return body text."""
        import urllib.request
        import urllib.error

        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent",
                           "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
                           "Gecko/20100101 Firefox/120.0")
            if follow_redirects:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read().decode('utf-8', errors='ignore')
            else:
                opener = urllib.request.build_opener(NoRedirectHandler)
                resp = opener.open(req, timeout=15)
                return resp.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            if follow_redirects:
                return e.read().decode('utf-8', errors='ignore')
            return None
        except Exception as e:
            logger.debug(f"HTTP GET {url}: {e}")
            return None

    def _http_post(self, url: str, data: dict = None,
                   content_type: str = "application/x-www-form-urlencoded"
                   ) -> Optional[str]:
        """HTTP POST and return body text."""
        import urllib.request
        import urllib.error
        import urllib.parse

        try:
            if data:
                encoded = urllib.parse.urlencode(data).encode('utf-8')
            else:
                encoded = b""

            req = urllib.request.Request(url, data=encoded, method="POST")
            req.add_header("User-Agent",
                           "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
                           "Gecko/20100101 Firefox/120.0")
            req.add_header("Content-Type", content_type)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            return e.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.debug(f"HTTP POST {url}: {e}")
            return None


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that raises HTTPError on redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, msg, headers, fp)


# Handler registry
_handlers = {}


def register_handler(handler_class):
    """Register a portal handler class."""
    instance = handler_class()
    _handlers[instance.name] = instance
    logger.debug(f"Registered portal handler: {instance.name}")
    return handler_class


def get_handler(name: str) -> Optional[BasePortalHandler]:
    """Get a handler by name."""
    return _handlers.get(name)


def list_handlers() -> list:
    """List all registered handler names."""
    return list(_handlers.keys())