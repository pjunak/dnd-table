"""
Token layer — creature / object tokens on the battle map (z=300).

Two kinds, both positioned + sized via the shared ``MapTransform`` so they
line up with the map under any letterbox:
  - generated discs: a coloured disc + ring, optionally a short text label
    (initial / number) baked to a small texture;
  - image tokens: an uploaded PNG, circular-masked with the same ring.

Tokens render BELOW the fog/vision layer (z=400), so a token a party can't
see (e.g. a lurking monster) is hidden by fog with no per-token test here.

Coordinates: token x/y/size are in map pixels / grid cells (scene space).  We
convert to inset-viewport pixels and draw the circle with an SDF in
``gl_FragCoord`` space, so tokens stay perfectly round whatever the aspect /
inset.  Textures (labels, art) are (re)baked only when the token set changes.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Optional

import moderngl

from ..compositor import Layer
from ..transform import MapTransform
from ..scene import SceneData, Token, TOKEN_IMAGE

log = logging.getLogger(__name__)

# Fonts probed for disc labels (bold reads better at token size).
_LABEL_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_LABEL_PX = 128                      # baked label texture size (square)
_RING_COLOR = (0.05, 0.04, 0.03, 0.9)


_VERT = """
#version 330
in vec2 in_unit;                 // unit quad corners [-1, 1]
uniform vec2 u_center_ndc;
uniform vec2 u_half_ndc;         // quad half-extent in NDC (covers disc + ring)
void main() {
    gl_Position = vec4(u_center_ndc + in_unit * u_half_ndc, 0.0, 1.0);
}
"""

_FRAG = """
#version 330
out vec4 f_color;
uniform vec2  u_center_px;       // token centre, inset-viewport px
uniform float u_radius_px;       // disc radius, px
uniform vec4  u_fill;            // disc fill rgba
uniform vec4  u_ring;            // ring rgba
uniform float u_ring_px;         // ring thickness, px
uniform float u_opacity;
uniform int   u_mode;            // 0 = disc(+label), 1 = image
uniform int   u_has_tex;
uniform sampler2D u_tex;

void main() {
    vec2 d = gl_FragCoord.xy - u_center_px;
    float dist = length(d);
    float aa = 1.0;
    if (dist > u_radius_px + aa) discard;

    // circle-local uv (y flipped so art / text is upright)
    vec2 uv = vec2(0.5 + 0.5 * d.x / u_radius_px,
                   0.5 - 0.5 * d.y / u_radius_px);

    vec4 col;
    if (u_mode == 1 && u_has_tex == 1) {
        vec4 img = texture(u_tex, uv);
        col = mix(u_fill, img, img.a);          // fill shows through transparency
    } else {
        col = u_fill;
        if (u_has_tex == 1) {
            vec4 lbl = texture(u_tex, uv);
            col = mix(col, lbl, lbl.a);          // label over the disc
        }
    }

    // ring band at the outer edge
    float ring = smoothstep(u_radius_px - u_ring_px - aa, u_radius_px - u_ring_px, dist);
    col = mix(col, u_ring, ring * u_ring.a);

    // outer-edge anti-aliasing
    float edge = 1.0 - smoothstep(u_radius_px - aa, u_radius_px + aa, dist);
    f_color = vec4(col.rgb, col.a * u_opacity * edge);
}
"""


def _hex_rgba(s, default=(0.79, 0.66, 0.30, 1.0)):
    s = (s or "").lstrip("#")
    try:
        if len(s) == 6:
            return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255,
                    int(s[4:6], 16) / 255, 1.0)
        if len(s) == 8:
            return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255,
                    int(s[4:6], 16) / 255, int(s[6:8], 16) / 255)
    except ValueError:
        pass
    return default


class TokenLayer(Layer):
    def __init__(self, name: str = "tokens", z_order: int = 300):
        super().__init__(name=name, z_order=z_order)
        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        self._tokens: list[Token] = []
        self._transform: Optional[MapTransform] = None
        self._ppg: float = 70.0
        self._dirty: bool = False
        # Texture caches keyed so repeated art / unchanged labels aren't rebaked.
        self._label_tex: dict[str, moderngl.Texture] = {}   # "id|label" -> tex
        self._image_tex: dict[str, moderngl.Texture] = {}   # path -> tex
        self.visible = False

    # ── Setup ────────────────────────────────────────────────────

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        verts = [-1.0, -1.0,  1.0, -1.0,  -1.0, 1.0,  1.0, 1.0]
        buf = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
        self._vao = ctx.vertex_array(self._prog, [(buf, "2f", "in_unit")])

    # ── Public API (wired by app.set_scene) ──────────────────────

    def set_scene(self, scene: Optional[SceneData],
                  transform: Optional[MapTransform]) -> None:
        """Replace the token set + transform.  Marks textures dirty so labels
        / art rebuild on the next update()."""
        self._transform = transform
        if scene is None:
            self._tokens = []
            self._ppg = 70.0
        else:
            self._tokens = list(scene.tokens)
            self._ppg = scene.grid.ppg or 70.0
        self.visible = bool(self._tokens)
        self._dirty = True

    def set_transform(self, transform: Optional[MapTransform]) -> None:
        """Update just the transform (window resize / overscan) without
        rebaking textures."""
        self._transform = transform

    # ── Lifecycle ────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        if self._dirty and self.ctx is not None:
            self._rebuild_textures()
            self._dirty = False

    def render(self) -> None:
        if not self._tokens or self._transform is None or not self._transform.valid:
            return
        assert self.ctx is not None and self._prog is not None and self._vao is not None
        self.ctx.disable(moderngl.DEPTH_TEST)   # 2D, painter's order
        mt, p = self._transform, self._prog
        for t in self._tokens:
            cx, cy = mt.map_px_to_ndc(t.x, t.y)
            px, py = mt.map_px_to_viewport_px(t.x, t.y)
            radius_px = max(2.0, 0.5 * t.size_cells * self._ppg * mt.px_scale)
            margin = 1.18
            p["u_center_ndc"].value = (cx, cy)
            p["u_half_ndc"].value = (radius_px * margin / (mt.vw * 0.5),
                                     radius_px * margin / (mt.vh * 0.5))
            p["u_center_px"].value = (px, py)
            p["u_radius_px"].value = radius_px
            p["u_fill"].value = _hex_rgba(t.color)
            p["u_ring"].value = _RING_COLOR
            p["u_ring_px"].value = max(2.0, radius_px * 0.10)
            p["u_opacity"].value = self.opacity
            p["u_tex"].value = 0

            tex, mode = None, 0
            if t.kind == TOKEN_IMAGE and t.image_ref:
                tex, mode = self._image_tex.get(t.image_ref), 1
            elif t.label:
                tex = self._label_tex.get(f"{t.id}|{t.label}")
            p["u_mode"].value = mode
            if tex is not None:
                tex.use(location=0)
                p["u_has_tex"].value = 1
            else:
                p["u_has_tex"].value = 0
            self._vao.render(mode=moderngl.TRIANGLE_STRIP)

    # ── Texture baking / loading ─────────────────────────────────

    def _rebuild_textures(self) -> None:
        """(Re)bake label textures and load image-token art for the current
        token set; evict anything no longer referenced."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            Image = None

        font_path = self._font()
        need_labels: set[str] = set()
        need_images: set[str] = set()

        for t in self._tokens:
            if t.kind == TOKEN_IMAGE and t.image_ref:
                need_images.add(t.image_ref)
                if t.image_ref not in self._image_tex:
                    tex = self._load_image(t.image_ref)
                    if tex is not None:
                        self._image_tex[t.image_ref] = tex
            elif t.label and Image is not None and font_path:
                key = f"{t.id}|{t.label}"
                need_labels.add(key)
                if key not in self._label_tex:
                    self._label_tex[key] = self._bake_label(
                        t.label, font_path, Image, ImageDraw, ImageFont)

        for key in [k for k in self._label_tex if k not in need_labels]:
            self._label_tex.pop(key).release()
        for path in [p for p in self._image_tex if p not in need_images]:
            self._image_tex.pop(path).release()

    @staticmethod
    def _font() -> Optional[str]:
        for p in _LABEL_FONT_CANDIDATES:
            if os.path.isfile(p):
                return p
        return None

    def _bake_label(self, text, font_path, Image, ImageDraw, ImageFont):
        n = _LABEL_PX
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        frac = 0.6 if len(text) <= 1 else (0.42 if len(text) <= 2 else 0.30)
        font = ImageFont.truetype(font_path, size=int(n * frac))
        color = (245, 240, 225, 255)
        try:
            draw.text((n / 2, n / 2), text, fill=color, font=font, anchor="mm")
        except Exception:  # Pillow < 8 has no anchor — centre by hand
            bb = draw.textbbox((0, 0), text, font=font)
            draw.text(((n - (bb[2] - bb[0])) // 2, (n - (bb[3] - bb[1])) // 2),
                      text, fill=color, font=font)
        tex = self.ctx.texture((n, n), 4, img.tobytes())
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        return tex

    def _load_image(self, path: str):
        try:
            from PIL import Image
            with Image.open(path) as im:
                rgba = im.convert("RGBA")
                tex = self.ctx.texture(rgba.size, 4, rgba.tobytes())
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
            return tex
        except Exception as e:
            log.warning("Token image load failed %s: %s", path, e)
            return None

    # ── Cleanup ──────────────────────────────────────────────────

    def teardown(self) -> None:
        for cache in (self._label_tex, self._image_tex):
            for tex in cache.values():
                try:
                    tex.release()
                except Exception:
                    pass
            cache.clear()
        if self._vao is not None:
            self._vao.release()
            self._vao = None
        if self._prog is not None:
            self._prog.release()
            self._prog = None
