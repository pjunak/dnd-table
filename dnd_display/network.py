"""
Small networking helpers for the splash's address overlay.

`get_local_ip()` finds the primary outbound IPv4 by asking the kernel
which interface would be used to reach the public internet. No actual
packets get sent — `connect()` on a UDP socket just resolves the route.
"""

from __future__ import annotations

import socket
from typing import Optional


def get_hostname() -> str:
    """Return the machine hostname with `.local` appended if missing.

    The kiosk advertises itself over mDNS via avahi-daemon, so
    `dndtable.local` is the address users will type.
    """
    name = socket.gethostname()
    if "." not in name:
        name = name + ".local"
    return name


def get_local_ip() -> Optional[str]:
    """Return the primary outbound IPv4 address as a dotted string, or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 is just a route hint; no packets are actually sent.
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip == "0.0.0.0":
            return None
        return ip
    except OSError:
        return None
    finally:
        s.close()
