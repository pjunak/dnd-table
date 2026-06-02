"""
DnD Table – Per-map scene persistence (sidecar files).

Each map's interactive scene (walls / vision / tokens / fog / markers) is
stored in a ``<map>.scene.json`` sidecar right next to the map file, keyed by
the map's path.  Kept out of the global settings.json so a scene travels with
its map — including maps on a USB drive, which carry their own sidecar.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def sidecar_path(map_path) -> Path:
    """The ``.scene.json`` path for a given map file."""
    return Path(str(map_path) + ".scene.json")


def load(map_path):
    """Return the stored scene dict for a map, or None if absent/unreadable."""
    p = sidecar_path(map_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to read scene sidecar %s: %s", p, e)
        return None


def save(map_path, scene: dict) -> bool:
    """Write a scene dict to the map's sidecar.  Returns False on failure
    (e.g. a read-only USB mount) — non-fatal; the in-memory scene still works."""
    p = sidecar_path(map_path)
    try:
        p.write_text(json.dumps(scene, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log.warning("Failed to write scene sidecar %s: %s", p, e)
        return False
