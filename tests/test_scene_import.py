"""
VTT import glue tests (``scene_import``) — the file-handling layer the
``POST /scene/import`` route delegates to.  The geometry transform is already
covered by ``tests/test_uvtt_importer.py``; here we own only the bits the
route would otherwise bury: extension gating, destination-name derivation,
writing the embedded image, and the ``safe_resolve`` containment guard that
keeps a crafted filename from escaping the Maps/ folder.
"""

import base64
import json

import pytest

import scene_import
from scene_import import (
    import_vtt, derive_image_name, is_vtt_filename, SceneImportError,
)

# A short byte string standing in for the embedded PNG.  The importer only
# base64-decodes the ``image`` field — it never validates it's a real PNG —
# so any bytes round-trip and keep the test free of Pillow.
_IMG = b"\x89PNG\r\n\x1a\nFAKE-MAP-IMAGE"


def _write_vtt(path, image=_IMG):
    """Write a minimal-but-complete UVTT file to *path* (3×2 cells @ 100 ppg)."""
    data = {
        "resolution": {"map_origin": {"x": 0, "y": 0},
                       "map_size": {"x": 3, "y": 2}, "pixels_per_grid": 100},
        "line_of_sight": [[{"x": 0, "y": 0}, {"x": 1, "y": 0}]],
        "portals": [{"bounds": [{"x": 1, "y": 1}, {"x": 2, "y": 1}], "closed": True}],
        "lights": [{"position": {"x": 1, "y": 1}, "range": 2, "color": "ff8800ff"}],
    }
    if image is not None:
        data["image"] = base64.b64encode(image).decode("ascii")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_is_vtt_filename():
    assert is_vtt_filename("cave.dd2vtt")
    assert is_vtt_filename("CAVE.UVTT")          # case-insensitive
    assert is_vtt_filename("x.df2vtt")
    assert not is_vtt_filename("map.png")
    assert not is_vtt_filename("clip.mp4")


def test_derive_image_name_strips_dirs_and_swaps_ext():
    assert derive_image_name("Goblin Cave.dd2vtt") == "Goblin Cave.png"
    assert derive_image_name("../../etc/evil.uvtt") == "evil.png"   # dirs dropped
    assert derive_image_name("") == "imported_map.png"


def test_import_writes_image_and_points_scene_at_it(tmp_path):
    src = _write_vtt(tmp_path / "cave.dd2vtt")
    maps = tmp_path / "Maps"

    dest, sd = import_vtt(src, maps, "cave.png")

    # The embedded image is written verbatim under Maps/.
    assert dest == (maps / "cave.png").resolve()
    assert dest.read_bytes() == _IMG
    # The scene's map_path points at the saved image, not the .dd2vtt.
    assert sd.map_path == str(dest)
    # Geometry came through (3×2 cells × 100 ppg → 300×200 px, one wall + door).
    assert (sd.width, sd.height) == (300, 200)
    assert len(sd.walls) == 1 and len(sd.doors) == 1


def test_import_rejects_traversal_in_image_name(tmp_path):
    src = _write_vtt(tmp_path / "cave.dd2vtt")
    maps = tmp_path / "Maps"
    maps.mkdir()

    # A crafted name that would escape Maps/ is refused by safe_resolve, and
    # nothing is written outside the folder.
    with pytest.raises(SceneImportError):
        import_vtt(src, maps, "../escape.png")
    assert not (tmp_path / "escape.png").exists()


def test_import_rejects_file_without_embedded_image(tmp_path):
    # Valid-shaped UVTT but no "image" key → nothing to save as a map.
    src = _write_vtt(tmp_path / "noimg.dd2vtt", image=None)
    with pytest.raises(SceneImportError):
        import_vtt(src, tmp_path / "Maps", "noimg.png")


def test_import_rejects_non_vtt_file(tmp_path):
    # A plain PNG isn't recognised by any importer → no embedded image.
    src = tmp_path / "photo.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(SceneImportError):
        import_vtt(src, tmp_path / "Maps", "photo.png")
