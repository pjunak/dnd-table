"""
Video layer — textured quad fed by a GStreamer pipeline.

The texture is uploaded once per frame from the latest decoded RGBA
buffer and drawn aspect-correct (letterboxed / pillarboxed) inside the
compositor's safe-area inset viewport.
"""

from __future__ import annotations

import logging
import struct

import moderngl

from ..compositor import Layer
from ..gst_pipeline import VideoPipeline

log = logging.getLogger(__name__)


_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
uniform vec2 u_scale;     // aspect-fit scale ≤ 1 on the over-fitting axis
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos * u_scale, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_tex;
uniform float u_opacity;
void main() {
    vec4 c = texture(u_tex, v_uv);
    f_color = vec4(c.rgb, c.a * u_opacity);
}
"""


class VideoLayer(Layer):
    def __init__(self, name: str = "video", z_order: int = 100):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        self._tex: moderngl.Texture | None = None
        self._tex_size: tuple[int, int] = (0, 0)
        self._pipeline = VideoPipeline()

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        # Fullscreen quad. UVs flipped on Y because GStreamer hands us
        # image-space buffers (origin top-left) but GL textures are
        # sampled with origin bottom-left.
        verts = [
            # x      y       u    v
            -1.0,  -1.0,    0.0, 1.0,
             1.0,  -1.0,    1.0, 1.0,
            -1.0,   1.0,    0.0, 0.0,
             1.0,   1.0,    1.0, 0.0,
        ]
        buf = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
        self._vao = ctx.vertex_array(
            self._prog,
            [(buf, "2f 2f", "in_pos", "in_uv")],
        )
        # Visible only when we have content to show.
        self.visible = False

    # ── Public API (wired up by the SSE bridge later) ────────────

    def play_file(self, path: str) -> None:
        self._pipeline.play_file(path)
        self.visible = True

    def play_test_pattern(self, pattern: str = "smpte") -> None:
        """Show a GStreamer test pattern (Settings → Display Test)."""
        self._pipeline.play_test_pattern(pattern)
        self.visible = True

    def stop(self) -> None:
        self._pipeline.stop()
        self.visible = False

    # ── Lifecycle ────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        assert self.ctx is not None
        frame = self._pipeline.pull_latest_rgba()
        if frame is None:
            return
        data, w, h = frame
        if (w, h) != self._tex_size or self._tex is None:
            if self._tex is not None:
                self._tex.release()
            self._tex = self.ctx.texture((w, h), 4, data)
            self._tex.repeat_x = False
            self._tex.repeat_y = False
            self._tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._tex_size = (w, h)
            log.info("VideoLayer texture (re)allocated: %dx%d", w, h)
        else:
            self._tex.write(data)

    def _compute_aspect_scale(self) -> tuple[float, float]:
        """Letterbox/pillarbox scale for the textured quad.

        The quad is at NDC corners (-1,-1)..(1,1); multiplying ``in_pos``
        by these values shrinks it along whichever axis would otherwise
        stretch the source frame.  Returns (1, 1) until both viewport and
        texture sizes are known.
        """
        tw, th = self._tex_size
        vw, vh = self.width, self.height
        if tw <= 0 or th <= 0 or vw <= 0 or vh <= 0:
            return 1.0, 1.0
        viewport_aspect = vw / vh
        video_aspect = tw / th
        if video_aspect > viewport_aspect:
            # Video wider than viewport — fit width, letterbox top/bottom.
            return 1.0, viewport_aspect / video_aspect
        else:
            # Video taller than viewport — fit height, pillarbox left/right.
            return video_aspect / viewport_aspect, 1.0

    def render(self) -> None:
        if self._tex is None or self._prog is None or self._vao is None:
            return
        sx, sy = self._compute_aspect_scale()
        self._tex.use(location=0)
        self._prog["u_tex"].value = 0
        self._prog["u_opacity"].value = self.opacity
        self._prog["u_scale"].value = (sx, sy)
        self._vao.render(mode=moderngl.TRIANGLE_STRIP)

    def teardown(self) -> None:
        self._pipeline.stop()
        if self._tex is not None:
            self._tex.release()
            self._tex = None
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
