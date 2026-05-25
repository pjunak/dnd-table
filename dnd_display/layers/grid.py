"""
Grid overlay — procedural square (and later hex) grid rendered entirely
in a fragment shader. No textures, no per-frame uploads — just uniforms.

Driven by Flask's grid_state dict via `set_state()`. The SSE bridge wires
state updates to this method.
"""

from __future__ import annotations

import struct
from typing import Mapping

import moderngl

from ..compositor import Layer


_VERT = """
#version 330
in vec2 in_pos;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

# Square grid: draws lines whenever the current pixel is within half the
# line thickness of a grid edge. `u_offset` lets the calibration UI nudge
# the grid by sub-cell amounts. Hex variant is a TODO — flip the discard
# to a different parametric distance check.
_FRAG = """
#version 330
out vec4 f_color;

uniform vec2  u_offset;
uniform float u_size;
uniform float u_thickness;
uniform vec4  u_color;
uniform float u_opacity;

void main() {
    vec2 p = gl_FragCoord.xy + u_offset;
    vec2 m = mod(p, u_size);
    float half_t = max(u_thickness * 0.5, 0.5);
    bool on = m.x < half_t || m.y < half_t
           || m.x > (u_size - half_t) || m.y > (u_size - half_t);
    if (!on) discard;
    f_color = vec4(u_color.rgb, u_color.a * u_opacity);
}
"""
# Note: hex grid will need a different fragment path (parametric distance
# to hexagonal centres). When it lands, re-introduce a `u_grid_type` int
# uniform and switch on it inside main().


def _hex_to_rgba(hex_str: str) -> tuple[float, float, float, float]:
    s = hex_str.lstrip("#")
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (r / 255.0, g / 255.0, b / 255.0, 1.0)
    if len(s) == 8:
        r, g, b, a = (int(s[0:2], 16), int(s[2:4], 16),
                      int(s[4:6], 16), int(s[6:8], 16))
        return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
    return (0.0, 0.0, 0.0, 1.0)


class GridLayer(Layer):
    def __init__(self, name: str = "grid", z_order: int = 200):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        # Defaults mirror state.grid_state in the Flask process.
        self.enabled: bool = False
        self.grid_type: str = "square"
        self.size: float = 55.0
        self.thickness: float = 1.0
        self.color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
        self.offset_x: int = 0
        self.offset_y: int = 0

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        verts = [-1.0, -1.0,  1.0, -1.0,  -1.0, 1.0,  1.0, 1.0]
        buf = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
        self._vao = ctx.vertex_array(self._prog, [(buf, "2f", "in_pos")])

    def set_state(self, st: Mapping) -> None:
        """Apply a grid_state dict from the Flask SSE bridge."""
        self.enabled = bool(st.get("enabled", self.enabled))
        self.visible = self.enabled
        self.grid_type = str(st.get("type", self.grid_type))
        self.size = float(st.get("size", self.size))
        self.thickness = float(st.get("thickness", self.thickness))
        self.opacity = float(st.get("opacity", self.opacity))
        self.color = _hex_to_rgba(st.get("color", "#000000"))
        self.offset_x = int(st.get("offset_x", self.offset_x))
        self.offset_y = int(st.get("offset_y", self.offset_y))

    def render(self) -> None:
        assert self._prog is not None and self._vao is not None
        p = self._prog
        p["u_offset"].value = (float(self.offset_x), float(self.offset_y))
        p["u_size"].value = self.size
        p["u_thickness"].value = self.thickness
        p["u_color"].value = self.color
        p["u_opacity"].value = self.opacity
        # grid_type is tracked but unused until the hex path lands.
        self._vao.render(mode=moderngl.TRIANGLE_STRIP)

    def teardown(self) -> None:
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
