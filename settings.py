"""
DnD Table – Persistent settings (survives reboot).

Stores user-configurable state to a JSON file so it's preserved across
service restarts.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SETTINGS_FILE = Path("/home/dndtable/dnd-display/settings.json")

# Fallback: same directory as the script (for dev runs)
_FALLBACK_FILE = Path(__file__).resolve().parent / "settings.json"

_DEFAULTS = {
    "grid": {
        "enabled": False,
        "type": "square",
        "size": 55,
        "thickness": 1,
        "opacity": 0.6,
        "color": "#000000",
        "offset_x": 0,
        "offset_y": 0,
        "ppi": 55,
        "calibration_mode": False,
    },
    "overscan": {
        "top": 0,
        "bottom": 0,
        "left": 0,
        "right": 0,
    },
    "volumes": {
        "map": 80,
        "ambient": 80,
        "sfx": 80,
    },
    "display": {
        "mode": None,   # None = use compositor / EDID default
    },
}


def _settings_path():
    """Return the settings file path, preferring the canonical location."""
    if SETTINGS_FILE.parent.is_dir():
        return SETTINGS_FILE
    return _FALLBACK_FILE


def load():
    """Load settings from disk, returning a dict merged with defaults.

    Unknown keys from old (RPi-era) settings files are preserved on read
    and silently dropped on the next save, so migrations are painless.
    """
    path = _settings_path()
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
            log.info("Loaded settings from %s", path)
        except Exception as e:
            log.warning("Failed to read settings file: %s", e)

    return _deep_merge(_DEFAULTS, data)


def save(settings):
    """Write settings dict to disk."""
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2))
    except Exception as e:
        log.warning("Failed to save settings: %s", e)


def _deep_merge(defaults, overrides):
    """Recursively merge overrides into defaults."""
    result = dict(defaults)
    for key, val in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
