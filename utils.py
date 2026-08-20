#!/usr/bin/env python3
"""
Utility module for resolving system tool paths.
On some systems (e.g. Debian with non-login shells), /usr/sbin is not
in PATH, so tools like iw, wpa_cli, dhcpcd are not found by name.
This module searches common locations and caches the results.
"""

import shutil
import os

# Cache of resolved paths
_path_cache = {}

# Common directories where network tools are installed
_SEARCH_DIRS = [
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/usr/local/sbin",
    "/usr/local/bin",
]


def find_tool(name: str) -> str:
    """Find the full path of a system tool.

    Tries shutil.which first (uses PATH), then falls back to
    searching common directories. Caches the result.

    Args:
        name: Tool name (e.g. 'iw', 'wpa_cli')

    Returns:
        Full path to the tool, or just the name if not found
        (letting subprocess handle the error).
    """
    if name in _path_cache:
        return _path_cache[name]

    # Try PATH first
    path = shutil.which(name)
    if path:
        _path_cache[name] = path
        return path

    # Search common directories
    for d in _SEARCH_DIRS:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _path_cache[name] = candidate
            return candidate

    # Not found, return the name as-is (subprocess will fail clearly)
    _path_cache[name] = name
    return name


# Pre-resolve commonly used tools
def get_iw() -> str:
    return find_tool("iw")


def get_wpa_cli() -> str:
    return find_tool("wpa_cli")


def get_wpa_supplicant() -> str:
    return find_tool("wpa_supplicant")


def get_dhcpcd() -> str:
    return find_tool("dhcpcd")


def get_dhclient() -> str:
    return find_tool("dhclient")


def get_ip() -> str:
    return find_tool("ip")


def get_rfkill() -> str:
    return find_tool("rfkill")


def get_curl() -> str:
    return find_tool("curl")