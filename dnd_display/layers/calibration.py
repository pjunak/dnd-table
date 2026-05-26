"""
Calibration overlay — shows safe-area guides while the user is dialling
in overscan from the control panel.

Two thin borders are drawn:

  - Red, at the absolute edge of the framebuffer.  If the TV crops the
    picture, the red line ends up behind the bezel.  The user nudges
    overscan inward until the red disappears completely.
  - Green, just inside the current inset.  This is where actual content
    will end up.  The user wants this just barely visible at the inside
    edge of the TV's bezel — so the safe-area is as large as possible
    without being clipped.

The layer is hidden the rest of the time.  Driven by the compositor's
overscan state plus a single boolean from the SSE bridge.
"""

from __future__ import annotations

import struct

import moderngl

from ..compositor import Layer


_VERT = """
#version 330
in vec2 in_pos;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

# Two-tier border driven entirely by gl_FragCoord, so we don't have to
# resize any geometry on overscan changes — just pass the framebuffer
# size and the current inset as uniforms.
_FRAG = """
#version 330
out vec4 f_color;

uniform vec2 u_fb_size;      // full framebuffer (px)
uniform vec4 u_inset;        // top, bottom, left, right (px)
uniform float u_thickness;   // line thickness in px
uniform float u_opacity;

void main() {
    vec2 p = gl_FragCoord.xy;       // origin bottom-left, px units
    float t = max(1.0, u_thickness);

    // ── Red: at the absolute edge of the framebuffer.
    bool red = p.x < t
            || p.y < t
            || p.x > (u_fb_size.x - t)
            || p.y > (u_fb_size.y - t);

    // ── Green: just inside the inset.  Note GL bottom-left origin —
    // "top" inset cuts the top of the screen (higher y), "bottom" the
    // bottom (lower y).
    float gx_lo = u_inset.z;                  // left
    float gx_hi = u_fb_size.x - u_inset.w;    // right
    float gy_lo = u_inset.y;                  // bottom
    float gy_hi = u_fb_size.y - u_inset.x;    // top
    bool green_horiz =
        (p.y >= gy_lo && p.y < gy_lo + t) ||
        (p.y <= gy_hi && p.y > gy_hi - t);
    bool green_vert =
        (p.x >= gx_lo && p.x < gx_lo + t) ||
        (p.x <= gx_hi && p.x > gx_hi - t);
    bool green = (green_horiz && p.x >= gx_lo && p.x <= gx_hi) ||
                 (green_vert  && p.y >= gy_lo && p.y <= gy_hi);

    if (red) {
        f_color = vec4(1.0, 0.20, 0.20, u_opacity);
    } else if (green) {
        f_color = vec4(0.25, 1.0, 0.30, u_opacity);
    } else {
        discard;
    }
}
"""


class CalibrationLayer(Layer):
    """Red/green safe-area guides — visible only while calibrating."""

    def __init__(self, name: str = "calibration", z_order: int = 950):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        # Driven by the compositor + SSE state.
        self.fb_width: int = 0
        self.fb_height: int = 0
        self.inset: tuple[int, int, int, int] = (0, 0, 0, 0)  # top, bot, left, right
        self.thickness: float = 2.0
        self.visible = False
        # Render at framebuffer size so the red edge-border isn't clipped
        # by the safe-area inset viewport.  See Compositor.render().
        self.full_framebuffer = True

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        verts = [-1.0, -1.0,  1.0, -1.0,  -1.0, 1.0,  1.0, 1.0]
        buf = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
        self._vao = ctx.vertex_array(self._prog, [(buf, "2f", "in_pos")])

    def set_framebuffer_size(self, w: int, h: int) -> None:
        self.fb_width = w
        self.fb_height = h

    def set_inset(self, top: int, bottom: int, left: int, right: int) -> None:
        self.inset = (top, bottom, left, right)

    def render(self) -> None:
        assert self._prog is not None and self._vao is not None
        p = self._prog
        p["u_fb_size"].value = (float(self.fb_width), float(self.fb_height))
        p["u_inset"].value = tuple(float(x) for x in self.inset)
        p["u_thickness"].value = self.thickness
        p["u_opacity"].value = self.opacity
        self._vao.render(mode=moderngl.TRIANGLE_STRIP)

    def teardown(self) -> None:
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
