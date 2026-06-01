"""
DnD Table – Music output proxy (Flask-side).

The table runs pjunak/music's headless ``music_output.py`` as a systemd
service (see system/music-output.service).  That client connects to the
remote music server (``music.junak.eu``) as an audio *output*, plays the
current track through mpv, and serves a tiny localhost control surface —
GET/POST ``/control`` on ``MUSIC_CONTROL_PORT`` — for on/off + volume.

This module proxies that surface so the control panel stays same-origin
(browser → Flask → 127.0.0.1:8731) and so "is the output reachable?"
becomes a clean boolean the panel can render.

Control-surface contract (from clients/headless/music_output.py):
    GET  /control → {"on", "volume" (0..1), "is_playing",
                     "track_id", "title", "artist"}
    POST /control  {"on": bool} and/or {"volume": 0..1}  → same shape
"""

import json
import logging
import urllib.error
import urllib.request

from config import MUSIC_CONTROL_URL

log = logging.getLogger(__name__)

_TIMEOUT_S = 1.0

# Shape returned when the local music-output client can't be reached
# (service down / not yet started).  Keeps the panel from special-casing
# None — it just sees connected=False.
_OFFLINE = {
    "connected": False,
    "on": False,
    "volume": 0.0,
    "is_playing": False,
    "track_id": None,
    "title": None,
    "artist": None,
}


def _request(method, payload=None):
    """Call the local control surface; return the parsed dict or None.

    None means the client isn't reachable (its systemd service is down).
    Any HTTP/JSON error is treated the same way — there's nothing the
    panel can do but show "offline".
    """
    url = MUSIC_CONTROL_URL.rstrip("/") + "/control"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.debug("music control %s failed: %s", method, e)
        return None


def _with_connected(data):
    """Tag a control-surface reply with connected=True, or return offline."""
    if data is None:
        return dict(_OFFLINE)
    return {**data, "connected": True}


def status():
    """Current output state plus a ``connected`` flag."""
    return _with_connected(_request("GET"))


def set_power(on):
    """Turn this output on/off; return the updated status."""
    return _with_connected(_request("POST", {"on": bool(on)}))


def set_volume(vol01):
    """Set this output's volume (0..1); return the updated status."""
    vol01 = max(0.0, min(1.0, float(vol01)))
    return _with_connected(_request("POST", {"volume": vol01}))
