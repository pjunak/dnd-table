"""
DnD Table – Vision geometry (pure Python, no GL, unit-testable).

Computes a viewer's visibility polygon — the area it can see, with walls
casting shadows, clipped to a radius — via an angular sweep: rays to every
wall endpoint (±ε so the sweep slips past corners to the wall behind), plus a
regular arc sampling so open areas read as a clean disc.  The algorithm was
verified against a Node prototype (single-wall shadow, closed room, doorway
gap, radius clipping) before porting.

All coordinates are MAP PIXELS.  Returns one polygon per source; the *union*
of several sources is taken on the GPU (the fog layer stamps each as a
triangle fan), so this module never does polygon boolean ops.  Recompute only
on token-move / wall / door / scene change — never per frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .scene import SceneData, DOOR_OPEN

Pt = tuple[float, float]
Seg = tuple[float, float, float, float]   # ax, ay, bx, by

_EPS = 1e-4           # ± ray nudge so the sweep slips past wall corners
_ARC_N = 96           # arc samples → smooth radius circle in open areas


@dataclass(frozen=True)
class VisionSource:
    x: float
    y: float
    radius: float


def _ray_segment(ox: float, oy: float, dx: float, dy: float,
                 ax: float, ay: float, bx: float, by: float) -> float:
    """Distance along the unit ray (O, D) to segment A-B, or ``inf`` if it
    doesn't hit.  Cramer's rule on O + t·D = A + u·(B−A)."""
    ex, ey = bx - ax, by - ay
    det = ex * dy - ey * dx            # cross(E, D)
    if -1e-12 < det < 1e-12:
        return math.inf
    fx, fy = ax - ox, ay - oy
    t = (ex * fy - ey * fx) / det      # cross(E, F) / det  → ray distance
    u = (dx * fy - dy * fx) / det      # cross(D, F) / det  → segment param
    if t >= 0.0 and -1e-9 <= u <= 1.0 + 1e-9:
        return t
    return math.inf


def _seg_in_range(seg: Seg, x: float, y: float, r: float) -> bool:
    """Broad-phase cull: does the segment's AABB meet the source's radius box?"""
    ax, ay, bx, by = seg
    if max(ax, bx) < x - r or min(ax, bx) > x + r:
        return False
    if max(ay, by) < y - r or min(ay, by) > y + r:
        return False
    return True


def visibility_polygon(src: VisionSource, segments, arc_n: int = _ARC_N) -> list[Pt]:
    """Boundary of the area visible from ``src`` (map px), ordered by angle.
    Fan it from the source to fill the visible region."""
    ox, oy, r = src.x, src.y, src.radius
    near = [s for s in segments if _seg_in_range(s, ox, oy, r)]

    angles: list[float] = []
    for ax, ay, bx, by in near:
        for px, py in ((ax, ay), (bx, by)):
            base = math.atan2(py - oy, px - ox)
            angles.append(base - _EPS)
            angles.append(base)
            angles.append(base + _EPS)
    step = 2.0 * math.pi / arc_n
    for i in range(arc_n):
        angles.append(-math.pi + i * step)

    hits: list[tuple[float, float, float]] = []
    for a in angles:
        dx, dy = math.cos(a), math.sin(a)
        best = r
        for ax, ay, bx, by in near:
            t = _ray_segment(ox, oy, dx, dy, ax, ay, bx, by)
            if t < best:
                best = t
        hits.append((a, ox + dx * best, oy + dy * best))

    hits.sort(key=lambda h: h[0])
    return [(h[1], h[2]) for h in hits]


# ── Scene → vision inputs (pure) ─────────────────────────────────

def segments_from_scene(scene: SceneData) -> list[Seg]:
    """Vision-blocking edges: every wall polyline edge + every closed door."""
    segs: list[Seg] = []
    for w in scene.walls:
        if not w.blocks_vision:
            continue
        p = w.points
        for i in range(len(p) - 1):
            segs.append((p[i][0], p[i][1], p[i + 1][0], p[i + 1][1]))
    for d in scene.doors:
        if d.state == DOOR_OPEN or not d.blocks_vision_when_closed:
            continue
        if len(d.points) >= 2:
            (ax, ay), (bx, by) = d.points[0], d.points[1]
            segs.append((ax, ay, bx, by))
    return segs


def vision_sources_from_scene(scene: SceneData) -> list[VisionSource]:
    """Party tokens with vision become the shared-reveal sources.  A range of
    0 means 'unlimited on-map' → the map diagonal (reaches every edge)."""
    diag = math.hypot(scene.width or 1, scene.height or 1)
    out: list[VisionSource] = []
    for t in scene.tokens:
        if t.is_party and t.vision.enabled:
            radius = t.vision.range if t.vision.range > 0 else diag
            out.append(VisionSource(t.x, t.y, radius))
    return out


def compute_scene_fans(scene: SceneData) -> list[list[Pt]]:
    """One triangle-fan per party vision source: ``[source, b0, b1, …, bN]``
    in map px (the fog layer fans from element 0 and closes the loop).  Empty
    when nobody has vision — the caller decides what that means for fog."""
    segs = segments_from_scene(scene)
    fans: list[list[Pt]] = []
    for s in vision_sources_from_scene(scene):
        fans.append([(s.x, s.y)] + visibility_polygon(s, segs))
    return fans
