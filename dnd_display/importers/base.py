"""
Scene importers — translate foreign VTT map formats into the canonical
``SceneData``.

Each adapter implements ``SceneImporter``; the registry (``importers/__init__``)
picks one by extension / content sniff.  The engine never sees a foreign
format — only ``SceneData`` — so adding a new system (Foundry, Roll20, a
proprietary format) is a single adapter against this interface and nothing
else in the codebase changes.  This is the reusability backbone for "load
pre-made maps from other systems".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..scene import SceneData


class SceneImporter(ABC):
    """One foreign-format → ``SceneData`` adapter."""

    #: Human-readable adapter name.
    name: str = "base"
    #: File extensions this adapter recognises (lowercase, with dot).
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def detect(self, path: Path, head: bytes) -> bool:
        """True if this adapter can handle ``path``.  ``head`` is the first
        few KB of the file for a cheap content sniff (no full read)."""

    @abstractmethod
    def load(self, path: Path) -> SceneData:
        """Parse ``path`` into a ``SceneData`` in map-pixel coordinates."""

    def image_bytes(self, path: Path) -> Optional[bytes]:
        """Embedded map image, if the format carries one (UVTT does).  The
        importer only decodes it; saving it as a usable map file is the
        caller's job.  Default: no embedded image."""
        return None
