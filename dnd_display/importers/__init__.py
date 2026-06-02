"""
Scene importer registry.

``load_scene(path)`` / ``image_bytes(path)`` pick the right adapter by
extension + content sniff and return canonical ``SceneData`` (and the
embedded map image, for formats that carry one).  Add a new format by
appending its ``SceneImporter`` to ``_IMPORTERS`` — nothing else changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..scene import SceneData
from .base import SceneImporter
from .uvtt import UvttImporter

# Order matters only if two adapters could both claim a file; keep the most
# specific first.  Future: FoundryImporter(), Roll20Importer(), the user's
# proprietary adapter — each just implements SceneImporter and lands here.
_IMPORTERS: list[SceneImporter] = [
    UvttImporter(),
]


def pick(path) -> Optional[SceneImporter]:
    """Return the adapter that recognises ``path``, or None."""
    p = Path(path)
    head = b""
    try:
        with open(p, "rb") as f:
            head = f.read(8192)
    except OSError:
        pass
    for imp in _IMPORTERS:
        try:
            if imp.detect(p, head):
                return imp
        except Exception:
            continue
    return None


def load_scene(path) -> Optional[SceneData]:
    """Translate a foreign map file into canonical ``SceneData``, or None if
    no adapter handles it."""
    imp = pick(path)
    return imp.load(Path(path)) if imp else None


def image_bytes(path) -> Optional[bytes]:
    """Decode the embedded map image from a foreign map file, or None."""
    imp = pick(path)
    return imp.image_bytes(Path(path)) if imp else None
