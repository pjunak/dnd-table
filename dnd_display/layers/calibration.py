"""
Calibration overlay — shows safe-area guides while the user is dialling
in overscan from the control panel.

Three visual layers, all driven from the same inset values:

  - Thin red line at the absolute framebuffer edge — fixed reference.
    If the TV crops, this disappears behind the bezel, which is itself
    diagnostic information ("the TV is eating pixels here").
  - Semi-transparent red fill covering the area BETWEEN the framebuffer
    edge and the current inset — the "discard zone" where content will
    not be drawn.  Grows as the user increases an inset slider.
  - Bright green line at the inset boundary — the actual safe-area edge,
    where rendered content begins.  Moves as the user adjusts a slider.

The fill makes the relationship between the two lines obvious: as the
user widens an inset, the red band sweeps inward and the green line
moves with it, so the slider has visible feedback on both colours
(addressing the otherwise-confusing "only the green moves" UX where
the FB-edge red is cropped behind the bezel and invisible).

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

# Border + fill driven entirely by gl_FragCoord, so we don't have to
# resize any geometry on overscan changes — just pass the framebuffer
# size and the current inset as uniforms.  Render order inside the
# shader (highest priority first):
#
#   1. FB-edge red line  (fixed, "physical edge" reference)
#   2. Inset green line  (moves with sliders, "safe-area boundary")
#   3. Discard-zone red fill (between FB edge and inset, semi-transparent)
#
# Cases 2 and 3 both grow/move as a slider increases, so the user sees
# two simultaneously-changing visuals: the green line creeping inward
# and the red band sweeping with it.
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

    // Inset rectangle (the safe area).  GL has bottom-left origin —
    // "top" inset cuts the top of the screen (higher y), "bottom" the
    // bottom (lower y).
    float gx_lo = u_inset.z;                  // left
    float gx_hi = u_fb_size.x - u_inset.w;    // right
    float gy_lo = u_inset.y;                  // bottom
    float gy_hi = u_fb_size.y - u_inset.x;    // top

    bool inside_inset = p.x >= gx_lo && p.x <= gx_hi
                     && p.y >= gy_lo && p.y <= gy_hi;

    // ── FB-edge red line (fixed): often hidden behind TV bezel.
    bool red_edge = p.x < t || p.x > (u_fb_size.x - t)
                 || p.y < t || p.y > (u_fb_size.y - t);

    // ── Green line at the inset boundary (moves with sliders).
    //    Only drawn on the inside-inset side of the boundary so it
    //    stays clearly visible against the red fill outside.
    bool green_line = inside_inset
        && (p.x < gx_lo + t || p.x > gx_hi - t
         || p.y < gy_lo + t || p.y > gy_hi - t);

    if (red_edge) {
        // 1-2 px hard-red at the framebuffer edge.
        f_color = vec4(1.0, 0.20, 0.20, u_opacity);
    } else if (green_line) {
        f_color = vec4(0.25, 1.0, 0.30, u_opacity);
    } else if (!inside_inset) {
        // Anywhere outside the inset rectangle: tint red to show the
        // user the actual width of the discard band.  Stays out of
        // the way of content (low alpha) while still being obvious
        // when the inset changes.
        f_color = vec4(1.0, 0.30, 0.30, u_opacity * 0.35);
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
