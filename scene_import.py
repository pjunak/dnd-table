"""
DnD Table – VTT import glue (pure: no Flask, no GL).

Bridges the import *engine* (``dnd_display.importers``) to the on-disk map
library.  The engine knows how to decode a foreign VTT file's embedded image
and translate its geometry into the canonical :class:`SceneData`; it
deliberately does **not** know where maps live or how they're persisted (see
the ``image_bytes`` docstring — "saving it as a usable map file is the
caller's job").  This module is that caller, factored out of the Flask route
so the security-relevant file handling — extension gating, destination-name
derivation, and the ``safe_resolve`` containment check — is unit-tested
without standing up Flask, matching the repo's "logic lives in a pure module,
not the route" convention.

The thin ``POST /scene/import`` route (``routes.py``) handles the multipart
upload, then calls :func:`import_vtt`, persists the returned scene to the
map's ``.scene.json`` sidecar, and starts playback.
"""

from __future__ import annotations

from pathlib import Path

from dnd_display import importers
from dnd_display.scene import SceneData
from paths import safe_resolve

#: Foreign VTT extensions the import engine understands.  Mirrors
#: ``UvttImporter.extensions`` — kept here too so the route can gate uploads
#: without reaching into the engine's adapter list.  These are intentionally
#: NOT in ``config.ALLOWED_EXTENSIONS``: a ``.dd2vtt`` is a scene description,
#: not playable media, so ``/upload`` rejects it and it arrives here instead.
VTT_EXTENSIONS = (".dd2vtt", ".uvtt", ".df2vtt")


class SceneImportError(Exception):
    """A VTT import failed for a reason worth telling the user about
    (unrecognised file, no embedded image, unsafe destination name)."""


def is_vtt_filename(name: str) -> bool:
    """True if *name* looks like a Universal VTT file by extension."""
    return Path(name).suffix.lower() in VTT_EXTENSIONS


def derive_image_name(src_name: str) -> str:
    """Map a VTT filename to the PNG we'll save its embedded image as:
    ``"Goblin Cave.dd2vtt"`` → ``"Goblin Cave.png"``.

    ``Path.stem`` already drops any directory components a crafted name might
    carry (``"../../x.uvtt"`` → ``"x"``), so the result is a bare filename;
    :func:`import_vtt` still runs it through ``safe_resolve`` as the real
    containment guard.
    """
    stem = Path(src_name).stem or "imported_map"
    return stem + ".png"


def import_vtt(src_path, maps_dir, image_name: str) -> tuple[Path, SceneData]:
    """Decode a VTT file at *src_path*, writing its embedded map image into
    *maps_dir* and returning ``(image_path, scene)``.

    * The embedded PNG is written to ``maps_dir / image_name`` — but only
      after ``safe_resolve`` confirms that path stays inside *maps_dir*, so a
      crafted ``image_name`` can't escape into the wider filesystem (same
      guard the delete / play routes rely on).
    * ``scene.map_path`` is set to the saved image so the scene and its map
      travel together (the sidecar lives next to the image; the display loads
      the image, not the original ``.dd2vtt``).

    Raises :class:`SceneImportError` on any failure; the caller maps it to a
    400.  The geometry transform itself is covered by
    ``tests/test_uvtt_importer.py`` — here we only own the file handling.
    """
    src_path = Path(src_path)

    img = importers.image_bytes(src_path)
    if img is None:
        raise SceneImportError(
            "File carries no embedded map image (not a Universal VTT export?)")

    dest = safe_resolve(maps_dir, image_name)
    if dest is None:
        raise SceneImportError("Unsafe map image name")

    sd = importers.load_scene(src_path)
    if sd is None:
        raise SceneImportError("No importer recognised this file")

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(img)
    except OSError as e:
        raise SceneImportError(f"Could not save map image: {e}") from e

    sd.map_path = str(dest)
    return dest, sd
