"""
DnD Table – Path-containment guard (pure, no Flask, unit-testable).

Every endpoint that touches the filesystem from a client-supplied path routes
through here so traversal (``..``), absolute escapes, and symlink tricks are
neutralised in exactly one place.  Before this existed each route hand-rolled
its own ``".." in rel_path`` / ``startswith`` / ``resolve().relative_to()``
dance — and they disagreed, which is how ``/delete`` ended up able to unlink
files outside the media root.  One helper, one behaviour.

The containment check resolves both sides (``Path.resolve()`` collapses ``..``
and follows symlinks) and then asks ``relative_to`` — a prefix check on the
*resolved* paths, so ``/media/dnd_media_evil`` is correctly rejected as NOT
under ``/media/dnd_media`` (string ``startswith`` would have accepted it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional


def safe_resolve(root, candidate) -> Optional[Path]:
    """Resolve ``candidate`` and return it only if it stays within ``root``.

    ``candidate`` may be absolute (validated to live under ``root``) or relative
    (joined onto ``root``).  Returns the resolved :class:`Path` on success, or
    ``None`` for any traversal, absolute escape, or unresolvable path — callers
    map ``None`` to a 400/403.  Resolution is non-strict, so a not-yet-existing
    target (e.g. a folder about to be created) still validates.
    """
    try:
        root_resolved = Path(root).resolve()
    except OSError:
        return None

    cand = Path(candidate)
    target = cand if cand.is_absolute() else (root_resolved / cand)
    try:
        resolved = target.resolve()
    except OSError:
        return None

    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def is_within_any(roots: Iterable, candidate) -> Optional[Path]:
    """Resolved ``candidate`` if it lies under ANY of ``roots``, else ``None``."""
    for r in roots:
        p = safe_resolve(r, candidate)
        if p is not None:
            return p
    return None
