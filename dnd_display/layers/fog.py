"""
Fog of war + dynamic vision (z=400) — drawn ABOVE tokens so anything the
party can't see (an unexplored corridor, a lurking monster) is covered.

Pipeline:
  - ``vision.py`` computes, in map pixels, one visibility polygon per party
    sight source (walls cast shadows, clipped to a radius).
  - We stamp each polygon as a triangle fan into a single-channel (R8) mask
    FBO — 1 where visible, 0 elsewhere; overlapping fans simply union.
  - Each frame we composite one fullscreen quad of black whose alpha is
    ``(1 - visible)`` — so seen areas are clear and everything else is dark.

The stamp happens only when the scene changes (a dirty flag), never per
frame.  Coordinates align with the map because the fans are converted through
the same shared ``MapTransform`` the video/token layers use.

v1 renders *currently-visible* fog only; "explored memory" (dim, remembered
areas) is a deliberate follow-up — it needs a second accumulation texture and
isn't required for the core fog-of-war + vision feature.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

import moderngl

from ..compositor import Layer
from ..transform import MapTransform

log = logging.getLogger(__name__)

_STAMP_VERT = """
#version 330
in vec2 in_ndc;
void main() { gl_Position = vec4(in_ndc, 0.0, 1.0); }
"""

_STAMP_FRAG = """
#version 330
out vec4 f_color;
void main() { f_color = vec4(1.0); }   // write 1.0 into the R8 visibility mask
"""

_COMP_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() { v_uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

_COMP_FRAG = """
#version 330
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_vis;
uniform float u_opacity;
uniform float u_dim;          // darkness of hidden areas (1.0 = full black)
void main() {
    float vis = texture(u_vis, v_uv).r;
    float darkness = (1.0 - vis) * u_dim;
    if (darkness <= 0.003) discard;          // fully-visible pixels stay clear
    f_color = vec4(0.0, 0.0, 0.0, darkness * u_opacity);
}
"""


class FogVisionLayer(Layer):
    def __init__(self, name: str = "fog", z_order: int = 400):
        super().__init__(name=name, z_order=z_order)
        self._stamp_prog: moderngl.Program | None = None
        self._comp_prog: moderngl.Program | None = None
        self._fs_vao: moderngl.VertexArray | None = None
        self._fan_vbo: moderngl.Buffer | None = None
        self._fan_vao: moderngl.VertexArray | None = None
        self._vis_tex: moderngl.Texture | None = None
        self._vis_fbo: moderngl.Framebuffer | None = None

        self._fans: list[list[tuple[float, float]]] = []     # [source, b0..bN]
        self._revealed: list[list[tuple[float, float]]] = []  # manual reveal polys
        self._transform: Optional[MapTransform] = None
        self._enabled: bool = False
        self._dim: float = 1.0
        self._dirty: bool = False
        self.visible = False

    # ── Setup / sizing ───────────────────────────────────────────

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._stamp_prog = ctx.program(vertex_shader=_STAMP_VERT, fragment_shader=_STAMP_FRAG)
        self._comp_prog = ctx.program(vertex_shader=_COMP_VERT, fragment_shader=_COMP_FRAG)
        # Fullscreen quad (NDC + uv) for the composite pass.
        fs = [-1.0, -1.0, 0.0, 0.0,  1.0, -1.0, 1.0, 0.0,
              -1.0,  1.0, 0.0, 1.0,  1.0,  1.0, 1.0, 1.0]
        fsbuf = ctx.buffer(struct.pack(f"{len(fs)}f", *fs))
        self._fs_vao = ctx.vertex_array(
            self._comp_prog, [(fsbuf, "2f 2f", "in_pos", "in_uv")])
        # Reused dynamic buffer for stamping fans (orphaned if a fan is huge).
        self._fan_vbo = ctx.buffer(reserve=1 << 16)   # 64 KB ≈ 8k verts
        self._fan_vao = ctx.vertex_array(
            self._stamp_prog, [(self._fan_vbo, "2f", "in_ndc")])

    def resize(self, width: int, height: int) -> None:
        super().resize(width, height)
        if self.ctx is not None:
            self._alloc(width, height)
            self._dirty = True   # restamp at the new size

    def _alloc(self, w: int, h: int) -> None:
        self._free_fbo()
        w, h = max(1, int(w)), max(1, int(h))
        self._vis_tex = self.ctx.texture((w, h), 1, dtype="f1")   # R8 mask
        self._vis_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)  # soft edge
        self._vis_tex.repeat_x = self._vis_tex.repeat_y = False
        self._vis_fbo = self.ctx.framebuffer(color_attachments=[self._vis_tex])
        self._vis_fbo.clear()

    # ── Public API (wired by app.set_scene) ──────────────────────

    def set_data(self, enabled: bool, fans, revealed, transform) -> None:
        """Replace the vision fans (+ manual reveal polys) and transform."""
        self._enabled = bool(enabled)
        self._fans = fans or []
        self._revealed = revealed or []
        self._transform = transform
        self.visible = self._enabled
        self._dirty = True

    def set_transform(self, transform: Optional[MapTransform]) -> None:
        self._transform = transform
        self._dirty = True   # the map→screen mapping moved; restamp

    # ── Lifecycle ────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        if self._dirty and self.ctx is not None:
            self._stamp()
            self._dirty = False

    def _stamp(self) -> None:
        """Rebuild the visibility mask by filling every vision/reveal fan.
        Runs on the GL thread; restores the screen framebuffer when done."""
        if self._vis_fbo is None:
            return
        self._vis_fbo.use()
        self._vis_fbo.clear()
        mt = self._transform
        if mt is not None and mt.valid:
            self.ctx.disable(moderngl.BLEND)
            fans = list(self._fans)
            for poly in self._revealed:                 # give reveal polys a centre
                if len(poly) >= 3:
                    cx = sum(p[0] for p in poly) / len(poly)
                    cy = sum(p[1] for p in poly) / len(poly)
                    fans.append([(cx, cy)] + list(poly))
            for fan in fans:
                self._stamp_fan(fan, mt)
            self.ctx.enable(moderngl.BLEND)
        self.ctx.screen.use()   # back to the window framebuffer for the layer loop

    def _stamp_fan(self, fan, mt: MapTransform) -> None:
        if len(fan) < 3:
            return
        verts: list[float] = []
        for x, y in fan:
            nx, ny = mt.map_px_to_ndc(x, y)
            verts.extend((nx, ny))
        # close the loop back to the first boundary point (index 1)
        bx, by = mt.map_px_to_ndc(fan[1][0], fan[1][1])
        verts.extend((bx, by))
        data = struct.pack(f"{len(verts)}f", *verts)
        if len(data) > self._fan_vbo.size:
            self._fan_vbo.orphan(len(data))
        self._fan_vbo.write(data)
        self._fan_vao.render(mode=moderngl.TRIANGLE_FAN, vertices=len(verts) // 2)

    def render(self) -> None:
        if (not self._enabled or self._vis_tex is None
                or self._transform is None or not self._transform.valid):
            return  # no valid map → don't paint the screen black
        assert self._comp_prog is not None and self._fs_vao is not None
        self.ctx.disable(moderngl.DEPTH_TEST)
        self._vis_tex.use(location=0)
        self._comp_prog["u_vis"].value = 0
        self._comp_prog["u_opacity"].value = self.opacity
        self._comp_prog["u_dim"].value = self._dim
        self._fs_vao.render(mode=moderngl.TRIANGLE_STRIP)

    # ── Cleanup ──────────────────────────────────────────────────

    def _free_fbo(self) -> None:
        for r in (self._vis_fbo, self._vis_tex):
            if r is not None:
                try:
                    r.release()
                except Exception:
                    pass
        self._vis_fbo = self._vis_tex = None

    def teardown(self) -> None:
        self._free_fbo()
        for r in (self._fs_vao, self._fan_vao, self._fan_vbo,
                  self._stamp_prog, self._comp_prog):
            if r is not None:
                try:
                    r.release()
                except Exception:
                    pass
        self._fs_vao = self._fan_vao = self._fan_vbo = None
        self._stamp_prog = self._comp_prog = None
