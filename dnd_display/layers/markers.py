"""
Marker layer — trap / hazard / environment markers (z=350).

Drawn between tokens (300) and fog (400), so a marked area is fog-occluded
when out of sight.  Only NON-hidden markers ever reach the display — hidden
ones (an un-sprung trap) are stripped server-side by
``SceneData.to_display_payload`` and stay GM-panel-only.

v1 renders each marker as a translucent coloured region + outline:
  - point / circle → a tessellated disc;
  - rect / poly    → its polygon (assumed convex; filled as a triangle fan).
Type-specific icons are a later polish — the colour already distinguishes
trap / hazard / difficult-terrain / note (set per-type by the panel).
"""

from __future__ import annotations

import math
import struct
from typing import Optional

import moderngl

from ..compositor import Layer
from ..transform import MapTransform
from ..scene import SceneData, Marker

_CIRCLE_SEGMENTS = 32
_FILL_ALPHA = 0.30
_OUTLINE_ALPHA = 0.85
_POINT_CELLS = 0.35       # radius of a "point" marker, in grid cells

_VERT = """
#version 330
in vec2 in_ndc;
void main() { gl_Position = vec4(in_ndc, 0.0, 1.0); }
"""

_FRAG = """
#version 330
out vec4 f_color;
uniform vec4 u_color;
uniform float u_opacity;
void main() { f_color = vec4(u_color.rgb, u_color.a * u_opacity); }
"""


def _rgb(s, default=(0.78, 0.29, 0.29)):
    s = (s or "").lstrip("#")
    try:
        if len(s) >= 6:
            return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255)
    except ValueError:
        pass
    return default


class MarkerLayer(Layer):
    def __init__(self, name: str = "markers", z_order: int = 350):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vbo: moderngl.Buffer | None = None
        self._vao: moderngl.VertexArray | None = None
        self._markers: list[Marker] = []
        self._transform: Optional[MapTransform] = None
        self._ppg: float = 70.0
        self.visible = False

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self._vbo = ctx.buffer(reserve=1 << 12)   # 4 KB; orphaned if a poly is huge
        self._vao = ctx.vertex_array(self._prog, [(self._vbo, "2f", "in_ndc")])

    # ── Public API ───────────────────────────────────────────────

    def set_scene(self, scene: Optional[SceneData],
                  transform: Optional[MapTransform]) -> None:
        self._transform = transform
        self._markers = list(scene.markers) if scene else []
        self._ppg = (scene.grid.ppg or 70.0) if scene else 70.0
        self.visible = bool(self._markers)

    def set_transform(self, transform: Optional[MapTransform]) -> None:
        self._transform = transform

    # ── Render ───────────────────────────────────────────────────

    def _polygon_px(self, m: Marker) -> list[tuple[float, float]]:
        """Boundary points (map px) for a marker, by shape."""
        if m.shape in ("point", "circle"):
            cells = _POINT_CELLS if m.shape == "point" else max(0.1, m.size_cells * 0.5)
            r = cells * self._ppg
            return [(m.x + r * math.cos(2 * math.pi * i / _CIRCLE_SEGMENTS),
                     m.y + r * math.sin(2 * math.pi * i / _CIRCLE_SEGMENTS))
                    for i in range(_CIRCLE_SEGMENTS)]
        if m.shape == "rect":
            if len(m.points) >= 3:
                return list(m.points)
            h = max(0.1, m.size_cells * 0.5) * self._ppg
            return [(m.x - h, m.y - h), (m.x + h, m.y - h),
                    (m.x + h, m.y + h), (m.x - h, m.y + h)]
        return list(m.points) if len(m.points) >= 3 else []   # poly

    def render(self) -> None:
        if (not self._markers or self._transform is None
                or not self._transform.valid):
            return
        assert self._prog is not None and self._vao is not None
        self.ctx.disable(moderngl.DEPTH_TEST)
        mt, p = self._transform, self._prog
        p["u_opacity"].value = self.opacity
        for m in self._markers:
            poly = self._polygon_px(m)
            if len(poly) < 3:
                continue
            verts: list[float] = []
            for x, y in poly:
                nx, ny = mt.map_px_to_ndc(x, y)
                verts.extend((nx, ny))
            data = struct.pack(f"{len(verts)}f", *verts)
            if len(data) > self._vbo.size:
                self._vbo.orphan(len(data))
            self._vbo.write(data)
            n = len(verts) // 2
            r, g, b = _rgb(m.color)
            p["u_color"].value = (r, g, b, _FILL_ALPHA)
            self._vao.render(mode=moderngl.TRIANGLE_FAN, vertices=n)
            p["u_color"].value = (r, g, b, _OUTLINE_ALPHA)
            self._vao.render(mode=moderngl.LINE_LOOP, vertices=n)

    def teardown(self) -> None:
        for r in (self._vao, self._vbo, self._prog):
            if r is not None:
                try:
                    r.release()
                except Exception:
                    pass
        self._vao = self._vbo = self._prog = None
