"""Debug overlay — rainbow triangle (dev-only, can be removed in production)."""

import struct

import moderngl

from ..compositor import Layer


_VERT = """
#version 330
in vec2 in_pos;
in vec3 in_color;
out vec3 v_color;
void main() {
    v_color = in_color;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
in vec3 v_color;
out vec4 f_color;
uniform float u_opacity;
void main() {
    f_color = vec4(v_color, u_opacity);
}
"""


class DebugTriangleLayer(Layer):
    def __init__(self, name: str = "debug-triangle", z_order: int = 900):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        verts = [
            #   x      y       r    g    b
             0.00,   0.55,   1.0, 0.2, 0.4,
            -0.55,  -0.40,   0.2, 0.6, 1.0,
             0.55,  -0.40,   0.6, 1.0, 0.4,
        ]
        buf = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
        self._vao = ctx.vertex_array(
            self._prog,
            [(buf, "2f 3f", "in_pos", "in_color")],
        )

    def render(self) -> None:
        assert self._prog is not None and self._vao is not None
        self._prog["u_opacity"].value = self.opacity
        self._vao.render(mode=moderngl.TRIANGLES)

    def teardown(self) -> None:
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
