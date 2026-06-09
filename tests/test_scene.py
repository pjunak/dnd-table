"""
Scene-model tests: round-trip fidelity, tolerant parsing, and the
security-critical hidden-token / hidden-marker stripping.

``to_display_payload`` is what reaches the table; if it ever leaked a hidden
token or marker, a player glancing at the TV (or a Player-View screenshot)
would see a trap or an unrevealed monster.  That invariant gets the most
attention here.
"""

from dnd_display.scene import (
    SceneData, Token, Marker, TokenVision, Wall, FOG_DYNAMIC,
)


def test_roundtrip_preserves_a_populated_scene():
    sd = SceneData(
        map_path="/m/x.png", width=1000, height=800,
        walls=[Wall(id="w0", points=[(0, 0), (10, 10)])],
        tokens=[Token(id="t0", x=5, y=6, label="A", is_party=True,
                      vision=TokenVision(enabled=True, range=120))],
        markers=[Marker(id="m0", x=1, y=2, type="trap", hidden=True)],
    )
    again = SceneData.from_payload(sd.to_payload())
    assert (again.width, again.height) == (1000, 800)
    assert again.walls[0].points == [(0.0, 0.0), (10.0, 10.0)]
    assert again.tokens[0].vision.enabled and again.tokens[0].vision.range == 120
    assert again.markers[0].hidden is True


def test_from_payload_is_tolerant():
    # Missing keys default, unknown keys ignored, strings coerced to numbers.
    sd = SceneData.from_payload(
        {"width": "640", "tokens": [{"x": "3", "y": "4", "bogus": 1}]})
    assert sd.width == 640
    assert (sd.tokens[0].x, sd.tokens[0].y) == (3.0, 4.0)
    assert sd.fog.mode == FOG_DYNAMIC and sd.fog.enabled is False


def test_from_payload_none_yields_empty_scene():
    sd = SceneData.from_payload(None)
    assert sd.tokens == [] and sd.walls == [] and sd.markers == []


def test_display_payload_strips_hidden_tokens_and_markers():
    sd = SceneData(
        tokens=[Token(id="seen", hidden=False), Token(id="secret", hidden=True)],
        markers=[Marker(id="shown", hidden=False), Marker(id="trap", hidden=True)],
    )
    disp = sd.to_display_payload()
    assert [t["id"] for t in disp["tokens"]] == ["seen"]
    assert [m["id"] for m in disp["markers"]] == ["shown"]


def test_display_payload_keeps_walls_and_doors_for_vision():
    # Walls/doors aren't drawn on the table but the display needs them to
    # compute the visibility polygon — they must survive the strip.
    sd = SceneData(walls=[Wall(id="w0", points=[(0, 0), (5, 5)])])
    assert len(sd.to_display_payload()["walls"]) == 1
