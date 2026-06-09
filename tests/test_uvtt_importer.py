"""
Universal VTT importer tests — the grid-units → map-pixel transform (the bit
that makes imported maps line up), portal→door translation, colour
normalisation, and format detection by extension + content sniff.
"""

from dnd_display.importers.uvtt import UvttImporter, _norm_color, _to_scene
from dnd_display.scene import DOOR_OPEN, DOOR_CLOSED


def _sample(ppg=100, origin=(0, 0)):
    return {
        "resolution": {"map_origin": {"x": origin[0], "y": origin[1]},
                       "map_size": {"x": 3, "y": 2}, "pixels_per_grid": ppg},
        "line_of_sight": [[{"x": 0, "y": 0}, {"x": 1, "y": 0}]],
        "portals": [
            {"bounds": [{"x": 1, "y": 1}, {"x": 2, "y": 1}], "closed": True},
            {"bounds": [{"x": 0, "y": 0}, {"x": 0, "y": 1}], "closed": False},
        ],
        "lights": [{"position": {"x": 1, "y": 1}, "range": 2, "color": "ff8800ff"}],
    }


def test_grid_units_scaled_to_pixels():
    sd = _to_scene(_sample(ppg=100), "/m/x.png")
    # 3×2 grid cells × 100 px/cell → 300×200 px map.
    assert (sd.width, sd.height) == (300, 200)
    # wall point (1,0) in grid units → (100, 0) px.
    assert sd.walls[0].points[1] == (100.0, 0.0)


def test_map_origin_is_subtracted():
    sd = _to_scene(_sample(ppg=10, origin=(1, 1)), "/m/x.png")
    # point (1,0) with origin (1,1): ((1-1)*10, (0-1)*10) = (0, -10)
    assert sd.walls[0].points[1] == (0.0, -10.0)


def test_portals_become_doors_with_open_closed_state():
    sd = _to_scene(_sample(), "/m/x.png")
    assert [d.state for d in sd.doors] == [DOOR_CLOSED, DOOR_OPEN]


def test_lights_range_scaled_by_ppg():
    sd = _to_scene(_sample(ppg=100), "/m/x.png")
    assert sd.lights[0].bright_radius == 200.0      # range 2 × 100 ppg


def test_color_normalisation():
    assert _norm_color("#abcdef") == "#abcdef"
    assert _norm_color("abcdef") == "#abcdef"
    assert _norm_color(None) == "#ffffff"
    assert _norm_color("xyz") == "#ffffff"          # too short → fallback
    # 8-digit (with alpha) → a valid 6-hex RGB; we don't pin which 6 channels
    # here because the alpha-channel position is format-ambiguous.
    out = _norm_color("ff8800ff")
    assert out.startswith("#") and len(out) == 7


def test_detect_by_extension(tmp_path):
    p = tmp_path / "map.dd2vtt"
    p.write_text("{}")
    assert UvttImporter().detect(p, b"{}")


def test_detect_by_content_sniff(tmp_path):
    p = tmp_path / "map.json"      # wrong extension, right content
    head = b'{"resolution": {"pixels_per_grid": 70}, "line_of_sight": []}'
    assert UvttImporter().detect(p, head)


def test_detect_rejects_unrelated_file(tmp_path):
    p = tmp_path / "photo.png"
    assert not UvttImporter().detect(p, b"\x89PNG\r\n\x1a\n")
