"""
Universal VTT importer — ``.dd2vtt`` / ``.uvtt`` / ``.df2vtt``.

The de-facto interchange format emitted by Dungeondraft (and read by
Foundry/Roll20): JSON with the map image embedded as base64 and all geometry
in GRID units.  We scale everything by ``pixels_per_grid`` into map pixels and
subtract ``map_origin`` so the scene sits in 0..(cols·ppg) × 0..(rows·ppg)
image space.

Reference shape::

    {
      "resolution": {"map_origin": {x,y}, "map_size": {x,y}, "pixels_per_grid": N},
      "line_of_sight": [[{x,y}, ...], ...],         # walls (grid units)
      "objects_line_of_sight": [[{x,y}, ...], ...], # object edges
      "portals": [{"bounds": [{x,y},{x,y}], "closed": bool, ...}],  # doors
      "lights":  [{"position": {x,y}, "range": N, "color": "rrggbbaa"}],
      "image": "<base64 png>"
    }
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from ..scene import (
    SceneData, Grid, Wall, Door, Light, Fog,
    GRID_SQUARE, DOOR_CLOSED, DOOR_OPEN, FOG_DYNAMIC,
)
from .base import SceneImporter


def _norm_color(c) -> str:
    """UVTT colours are hex strings, often 8-digit (with alpha).  Take the
    last 6 hex digits as RGB; fall back to white."""
    s = str(c or "").lstrip("#")
    if len(s) >= 6:
        return "#" + s[-6:]
    return "#ffffff"


def _to_scene(data: dict, map_path: str) -> SceneData:
    res = data.get("resolution") or {}
    ppg = float(res.get("pixels_per_grid", 70) or 70)
    msize = res.get("map_size") or {}
    morigin = res.get("map_origin") or {}
    cols = float(msize.get("x", 0) or 0)
    rows = float(msize.get("y", 0) or 0)
    ogx = float(morigin.get("x", 0) or 0)
    ogy = float(morigin.get("y", 0) or 0)

    def gp(pt) -> tuple[float, float]:
        # grid units (relative to map_origin) → map pixels
        return ((float(pt["x"]) - ogx) * ppg, (float(pt["y"]) - ogy) * ppg)

    walls: list[Wall] = []
    for poly in (data.get("line_of_sight") or []):
        walls.append(Wall(id=f"w{len(walls)}", points=[gp(p) for p in poly]))
    for poly in (data.get("objects_line_of_sight") or []):
        walls.append(Wall(id=f"w{len(walls)}", points=[gp(p) for p in poly]))

    doors: list[Door] = []
    for pr in (data.get("portals") or []):
        bounds = pr.get("bounds") or []
        if len(bounds) < 2:
            continue
        closed = bool(pr.get("closed", True))
        doors.append(Door(
            id=f"d{len(doors)}",
            points=[gp(bounds[0]), gp(bounds[1])],
            state=DOOR_CLOSED if closed else DOOR_OPEN,
        ))

    lights: list[Light] = []
    for lt in (data.get("lights") or []):
        pos = lt.get("position") or {}
        if "x" not in pos:
            continue
        x, y = gp(pos)
        lights.append(Light(
            id=f"l{len(lights)}", x=x, y=y,
            bright_radius=float(lt.get("range", 0) or 0) * ppg,
            color=_norm_color(lt.get("color")),
        ))

    return SceneData(
        map_path=map_path,
        width=int(round(cols * ppg)),
        height=int(round(rows * ppg)),
        grid=Grid(ppg=ppg, origin=(0.0, 0.0), type=GRID_SQUARE),
        walls=walls, doors=doors, lights=lights,
        tokens=[], markers=[],            # a map format carries no game state
        fog=Fog(mode=FOG_DYNAMIC, enabled=True),
    )


class UvttImporter(SceneImporter):
    name = "uvtt"
    extensions = (".dd2vtt", ".uvtt", ".df2vtt")

    def detect(self, path: Path, head: bytes) -> bool:
        if path.suffix.lower() in self.extensions:
            return True
        # Content sniff: a JSON object carrying the UVTT markers.
        txt = head.decode("utf-8", "ignore")
        return '"resolution"' in txt and (
            '"line_of_sight"' in txt or '"pixels_per_grid"' in txt)

    def load(self, path: Path) -> SceneData:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return _to_scene(data, str(path))

    def image_bytes(self, path: Path) -> Optional[bytes]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        b64 = data.get("image")
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
