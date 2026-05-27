"""
Grid overlay — procedural square and pointy-top hexagonal grids rendered
entirely in a fragment shader.  No textures, no per-frame uploads —
just uniforms.

Driven by Flask's ``grid_state`` dict via ``set_state()``.  The SSE
bridge wires state updates to this method.

Hex grid math: cells are pointy-top, ``u_size`` is interpreted as the
across-flats dimension so a "size 55" hex visually fills roughly the
same area as a square cell of side 55.  Cell centres are found by
checking two interleaved rectangular lattices (offset by half a cell)
and picking whichever is nearer — adequate for line-drawing (the only
inaccuracy is right at three-cell vertices, where the answer is
"on the boundary" either way).
"""

from __future__ import annotations

import struct
from typing import Mapping

import moderngl

from ..compositor import Layer


_GRID_TYPE_SQUARE = 0
_GRID_TYPE_HEX = 1


_VERT = """
#version 330
in vec2 in_pos;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
out vec4 f_color;

uniform vec2  u_offset;
uniform float u_size;        // cell side (square) / across-flats (hex)
uniform float u_thickness;
uniform vec4  u_color;
uniform float u_opacity;
uniform int   u_grid_type;   // 0 = square, 1 = hex (pointy-top)

const float SQRT3      = 1.7320508075688772;
const float HALF_SQRT3 = 0.8660254037844386;

// Find the nearest pointy-top hex centre given a pixel ``p`` and a
// vertex-to-centre radius ``R``.  Two interleaved rectangular lattices
// cover all cells: even rows at integer multiples of (W, 2H), odd rows
// at the same multiples offset by (W/2, H).  Picking the closer of the
// two nearest candidates suffices for boundary detection.
vec2 hex_nearest_centre(vec2 p, float R) {
    float W = R * SQRT3;     // horizontal spacing (vertex-to-vertex across flats)
    float H = R * 1.5;       // vertical spacing between alternate rows

    vec2 g1 = vec2(
        floor(p.x / W + 0.5) * W,
        floor(p.y / (2.0 * H) + 0.5) * 2.0 * H
    );
    vec2 g2 = vec2(
        floor((p.x - W * 0.5) / W + 0.5) * W + W * 0.5,
        floor((p.y - H) / (2.0 * H) + 0.5) * 2.0 * H + H
    );
    vec2 d1 = p - g1;
    vec2 d2 = p - g2;
    return dot(d1, d1) < dot(d2, d2) ? g1 : g2;
}

void main() {
    vec2 p = gl_FragCoord.xy + u_offset;
    float half_t = max(u_thickness * 0.5, 0.5);

    bool on = false;

    if (u_grid_type == 1) {
        // Pointy-top hex.  Treat u_size as the across-flats dimension;
        // vertex-to-centre radius is therefore u_size / sqrt(3).
        float R = max(u_size, 1.0) / SQRT3;
        vec2 centre = hex_nearest_centre(p, R);
        vec2 q = p - centre;
        // Signed distance from each of the 3 unique edge normals; the
        // hex boundary lies at the inscribed radius R·sqrt(3)/2 along
        // every normal, so the distance from the boundary is
        // (inscribed - max(|q·n|)) for the 3 normals.
        vec2 ab = abs(q);
        float d1 = ab.x;                                 // 0°   normal
        float d2 = 0.5 * ab.x + HALF_SQRT3 * ab.y;        // 60°  normal
        float d3 = -0.5 * ab.x + HALF_SQRT3 * ab.y;       // 120° normal
        float farthest = max(d1, max(d2, abs(d3)));
        float dist_to_edge = R * HALF_SQRT3 - farthest;
        on = abs(dist_to_edge) < half_t;
    } else {
        // Square grid.  ``gl_FragCoord`` lands at integer + 0.5 (pixel
        // centres), so a naive ``mod(p, size) < half_t`` with half_t = 0.5
        // never fires — a 1-pixel grid would render as nothing.  Bias by
        // -0.5 so grid lines align to pixel columns, then light up
        // ``u_thickness`` consecutive pixels per line.  That yields
        // exactly the requested visual thickness (1px draws 1px,
        // 2px draws 2px, etc.) with no off-by-half ghosts.
        vec2 m = mod(p - vec2(0.5), u_size);
        on = m.x < u_thickness || m.y < u_thickness;
    }

    if (!on) discard;
    f_color = vec4(u_color.rgb, u_color.a * u_opacity);
}
"""


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
        p["u_size"].value = max(self.size, 1.0)
        p["u_thickness"].value = self.thickness
        p["u_color"].value = self.color
        p["u_opacity"].value = self.opacity
        p["u_grid_type"].value = (
            _GRID_TYPE_HEX if self.grid_type == "hex" else _GRID_TYPE_SQUARE
        )
        self._vao.render(mode=moderngl.TRIANGLE_STRIP)

    def teardown(self) -> None:
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
