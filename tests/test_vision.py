"""
Vision-geometry tests (pure, no GL — run with ``pytest`` from the repo root).

These mirror the Node prototype that validated the angular-sweep algorithm:
a point in front of a wall is visible; a point in its shadow is not; a closed
room lights its interior only; a doorway gap lets sight through; the radius
clips an open disc.
"""

from dnd_display.vision import VisionSource, visibility_polygon


def _point_in_poly(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def test_no_walls_clips_to_radius():
    poly = visibility_polygon(VisionSource(0, 0, 100), [])
    assert _point_in_poly(50, 0, poly)
    assert _point_in_poly(0, 90, poly)
    assert not _point_in_poly(200, 0, poly)      # beyond the radius


def test_single_wall_casts_shadow():
    poly = visibility_polygon(VisionSource(0, 0, 300), [(50, -20, 50, 20)])
    assert _point_in_poly(40, 0, poly)           # in front of the wall
    assert not _point_in_poly(60, 0, poly)       # directly behind → shadow
    assert _point_in_poly(60, 100, poly)         # clears the wall top


def test_closed_room_lights_interior_only():
    room = [(-30, -30, 30, -30), (30, -30, 30, 30),
            (30, 30, -30, 30), (-30, 30, -30, -30)]
    poly = visibility_polygon(VisionSource(0, 0, 500), room)
    assert _point_in_poly(10, 10, poly)
    assert _point_in_poly(28, 0, poly)           # right up against a wall
    assert not _point_in_poly(50, 50, poly)      # outside the room


def test_doorway_gap_lets_sight_through():
    door = [(50, -100, 50, -10), (50, 10, 50, 100)]   # gap in y∈[-10,10]
    poly = visibility_polygon(VisionSource(0, 0, 300), door)
    assert _point_in_poly(100, 0, poly)          # straight through the gap
    assert not _point_in_poly(100, 40, poly)     # blocked by the wall
