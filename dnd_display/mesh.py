"""
Procedural geometry generators.

For now: one icosahedron generator that produces a flat-shaded VBO with
per-face normals and rune-atlas UVs. Vertex layout:

    pos(3) | normal(3) | uv(2)   ← 8 floats / vertex

20 faces × 3 vertices = 60 vertices. Triangle list draw mode.
"""

from __future__ import annotations

import math
import struct
from typing import Tuple

PHI = (1.0 + math.sqrt(5.0)) / 2.0


# ── Atlas-tile sampling geometry ─────────────────────────────────
#
# Each face maps to an *equilateral* triangle inscribed in its atlas
# tile, with apex at top-centre and base across the bottom. A small
# safety margin is left inside the tile so anti-aliased pixels at face
# edges can't bleed into a neighbouring tile.
#
# These constants are also imported by rune_atlas.py so the rune is
# drawn at the triangle's centroid — the visual centre of what the face
# actually samples.
#
_TRIANGLE_APEX_Y = 1.0 - math.sqrt(3.0) / 2.0   # ≈ 0.134
_TRIANGLE_MARGIN = 0.10                          # 10% inset on each side

# Centroid of the (un-margined) inscribed equilateral triangle, in
# tile-normalised coordinates with origin at top-left.
RUNE_CENTROID_X = 0.5
RUNE_CENTROID_Y = (_TRIANGLE_APEX_Y + 1.0 + 1.0) / 3.0   # ≈ 0.711


# Standard icosahedron, before normalization. 12 vertices, 20 faces.
_ICOSA_VERTS: list[Tuple[float, float, float]] = [
    (-1.0,  PHI, 0.0), ( 1.0,  PHI, 0.0), (-1.0, -PHI, 0.0), ( 1.0, -PHI, 0.0),
    (0.0, -1.0,  PHI), (0.0,  1.0,  PHI), (0.0, -1.0, -PHI), (0.0,  1.0, -PHI),
    ( PHI, 0.0, -1.0), ( PHI, 0.0,  1.0), (-PHI, 0.0, -1.0), (-PHI, 0.0,  1.0),
]

# Faces wound counter-clockwise when viewed from outside (so backface
# culling with default GL convention hides the interior).
_ICOSA_FACES: list[Tuple[int, int, int]] = [
    (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
    (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
    (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
    (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
]


def _normalize(v: Tuple[float, float, float]) -> Tuple[float, float, float]:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def build_icosahedron_buffer(
    atlas_cols: int = 5,
    atlas_rows: int = 4,
) -> Tuple[bytes, int]:
    """Return ``(vbo_bytes, vertex_count)`` for a flat-shaded D20.

    UVs map each face's three vertices to a triangular slice of one tile
    in the rune atlas (apex at tile top, base across tile bottom). The
    face's first vertex gets the apex UV, so the rune "up" direction
    aligns with the face's first vertex on every face.
    """
    verts = [_normalize(v) for v in _ICOSA_VERTS]
    out: list[float] = []

    for face_id, (a, b, c) in enumerate(_ICOSA_FACES):
        va, vb, vc = verts[a], verts[b], verts[c]
        n = _normalize(_cross(_sub(vb, va), _sub(vc, va)))

        col = face_id % atlas_cols
        row = face_id // atlas_cols
        u_per_tile = 1.0 / atlas_cols
        v_per_tile = 1.0 / atlas_rows
        u_left = col * u_per_tile
        v_top = row * v_per_tile

        # Equilateral triangle inscribed in the tile with apex at top-centre
        # and base across the bottom, inset by _TRIANGLE_MARGIN on each side.
        apex_x = u_left + u_per_tile * 0.5
        apex_y = v_top + v_per_tile * (_TRIANGLE_APEX_Y + _TRIANGLE_MARGIN)
        base_y = v_top + v_per_tile * (1.0 - _TRIANGLE_MARGIN)
        base_lx = u_left + u_per_tile * _TRIANGLE_MARGIN
        base_rx = u_left + u_per_tile * (1.0 - _TRIANGLE_MARGIN)

        uvs = [
            (apex_x, apex_y),       # apex
            (base_lx, base_y),      # bottom-left
            (base_rx, base_y),      # bottom-right
        ]

        for (vx, vy, vz), (u, v) in zip((va, vb, vc), uvs):
            out.extend([vx, vy, vz, n[0], n[1], n[2], u, v])

    return struct.pack(f"{len(out)}f", *out), 60
