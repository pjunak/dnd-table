"""
Display mode helpers — list + set the Wayland output mode via wlr-randr.

Flask runs as the `dndtable` user, the same user that owns the cage
Wayland session, so we can connect to the kiosk's compositor by setting
XDG_RUNTIME_DIR and WAYLAND_DISPLAY explicitly (systemd's User= unit
doesn't propagate the user's graphical-session env).

Mode strings are normalised to ``WIDTHxHEIGHT@HZ`` (e.g. ``1920x1080@60``)
because that's exactly the syntax `wlr-randr --mode` accepts.
"""

from __future__ import annotations

import logging
import os
import pwd
import re
import subprocess
from typing import List, Optional, TypedDict

log = logging.getLogger(__name__)


_KIOSK_USER = "dndtable"


def _wayland_env() -> dict[str, str]:
    """Build the env vars wlr-randr needs to find the cage Wayland socket.

    The Flask service runs as ``dndtable`` (per ``dnd-table.service``)
    and so does cage (per ``greetd-config.toml``), but Flask is launched
    by systemd without the graphical-session env vars — so we have to
    point ``XDG_RUNTIME_DIR`` at /run/user/<uid> explicitly.  The UID
    is looked up rather than hardcoded so the install survives if the
    dndtable user ends up with a non-1000 uid (e.g., reinstall on a
    multi-user box).
    """
    try:
        uid = pwd.getpwnam(_KIOSK_USER).pw_uid
    except KeyError:
        # Fallback to the current user — the Flask service runs as
        # dndtable anyway, so its own uid is the right answer.
        uid = os.getuid()
    return {
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "WAYLAND_DISPLAY": "wayland-0",
    }


class DisplayState(TypedDict, total=False):
    available: bool
    error: str
    output: str
    current: str
    modes: List[str]


def _run(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(_wayland_env())
    return subprocess.run(
        ["wlr-randr", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


# Output header:  HDMI-A-3 "TV@ PHILCO (HDMI-A-3)"
_OUTPUT_RE = re.compile(r"^(\S+)\s+\".+\"\s*$")
# Mode line:      1920x1080 px, 60.000000 Hz (preferred, current)
_MODE_RE = re.compile(
    r"^\s+(\d+)x(\d+)\s*px,\s*(\d+(?:\.\d+)?)\s*Hz\s*(\(.*\))?\s*$"
)


def get_state() -> DisplayState:
    """Return the current first-output state, or an error dict."""
    try:
        result = _run()
    except FileNotFoundError:
        return {"available": False, "error": "wlr-randr not installed"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "wlr-randr timed out"}

    if result.returncode != 0:
        return {"available": False, "error": result.stderr.strip() or "wlr-randr failed"}

    output: Optional[str] = None
    current: Optional[str] = None
    # Keep w/h/hz alongside the mode string so we can sort the list
    # before returning — TVs often list 720p ahead of 1080p in EDID
    # order, which is confusing in a dropdown labelled "Resolution".
    mode_entries: list[tuple[int, int, int, str]] = []
    seen: set[str] = set()

    for line in result.stdout.splitlines():
        m = _OUTPUT_RE.match(line)
        if m:
            # First connected output wins. If you ever support multi-display,
            # this is the place to make the choice explicit.
            if output is None:
                output = m.group(1)
            continue
        m = _MODE_RE.match(line)
        if m and output is not None:
            w, h, hz = int(m.group(1)), int(m.group(2)), int(round(float(m.group(3))))
            flags = m.group(4) or ""
            mode = f"{w}x{h}@{hz}"
            if mode not in seen:
                seen.add(mode)
                mode_entries.append((w, h, hz, mode))
            if "current" in flags:
                current = mode

    if output is None:
        return {"available": False, "error": "no connected output"}

    # Highest resolution first, then highest refresh rate; "Resolution"
    # dropdown reads top-down, so the max-quality option should land at
    # the top.
    mode_entries.sort(key=lambda e: (e[0], e[1], e[2]), reverse=True)
    modes = [e[3] for e in mode_entries]

    return {
        "available": True,
        "output": output,
        "current": current or "",
        "modes": modes,
    }


def set_mode(mode: str) -> tuple[bool, str]:
    """Set the first connected output to ``mode`` (e.g. ``1920x1080@60``)."""
    state = get_state()
    if not state.get("available"):
        return False, state.get("error", "display state unavailable")
    if mode not in state.get("modes", []):
        return False, f"mode {mode!r} not supported by this output"
    try:
        result = _run("--output", state["output"], "--mode", mode)
    except subprocess.TimeoutExpired:
        return False, "wlr-randr timed out"
    if result.returncode != 0:
        return False, result.stderr.strip() or "wlr-randr failed"
    log.info("Display mode set to %s on %s", mode, state["output"])
    return True, ""
