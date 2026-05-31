"""
Splash layer — animated 3D D20 with Elder Futhark runes.

Designed to be extended:
- Future: "roll" animation that lands on a target face
- Future: particle effects on a critical hit

Visual recipe:
- Dark vignetted backdrop drawn first (covers underlying layers when shown)
- Icosahedron with flat-shaded faces + Lambert lighting + ambient term
- Rune atlas sampled per face, tinted slightly with the light direction
- Continuous rotation around a tilted axis for life
"""

from __future__ import annotations

import logging
import math
import os
import struct

import moderngl
from pyglet.math import Mat4, Vec3

from ..compositor import Layer
from ..mesh import build_icosahedron_buffer
from ..rune_atlas import build_rune_atlas, RUNE_ATLAS_COLS, RUNE_ATLAS_ROWS
from ..themes import SplashTheme, DEFAULT_THEME, get as get_theme

log = logging.getLogger(__name__)


# Fonts probed in order for the address overlay. Prefer Light / Regular
# for a refined feel; only fall back to Bold if nothing else is present.
_ADDR_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Light.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]


# Backdrop ────────────────────────────────────────────────────────
_BG_VERT = """
#version 330
in vec2 in_pos;
out vec2 v_pos;
void main() {
    v_pos = in_pos;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_BG_FRAG = """
#version 330
in vec2 v_pos;
out vec4 f_color;
uniform float u_opacity;
uniform vec3  u_bg_inner;
uniform vec3  u_bg_outer;
void main() {
    float r = length(v_pos);
    float vignette = smoothstep(1.45, 0.15, r);
    vec3 mid = (u_bg_inner + u_bg_outer) * 0.5;
    vec3 c = mix(u_bg_outer, mid, vignette);
    c = mix(c, u_bg_inner, pow(vignette, 2.0));
    f_color = vec4(c, u_opacity);
}
"""


# Address overlay (fullscreen RGBA texture stamped by PIL) ───────
_ADDR_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_ADDR_FRAG = """
#version 330
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D u_tex;
uniform float u_opacity;
void main() {
    vec4 t = texture(u_tex, v_uv);
    f_color = vec4(t.rgb, t.a * u_opacity);
}
"""


# D20 ─────────────────────────────────────────────────────────────
_D20_VERT = """
#version 330
uniform mat4 u_mvp;
uniform mat4 u_model;
in vec3 in_pos;
in vec3 in_normal;
in vec2 in_uv;
out vec3 v_world_pos;
out vec3 v_world_normal;
out vec2 v_uv;
void main() {
    vec4 wp = u_model * vec4(in_pos, 1.0);
    v_world_pos = wp.xyz;
    v_world_normal = normalize(mat3(u_model) * in_normal);
    v_uv = in_uv;
    gl_Position = u_mvp * vec4(in_pos, 1.0);
}
"""

_D20_FRAG = """
#version 330
in vec3 v_world_pos;
in vec3 v_world_normal;
in vec2 v_uv;
out vec4 f_color;

uniform sampler2D u_runes;
uniform int       u_have_runes;
uniform int       u_rune_effect;     // 0 = solid, 1 = flaming, 2 = lightning
uniform int       u_face_effect;     // 0 = smooth, 1 = cracked stone, 2 = mossy stone
uniform float     u_time;
uniform vec3      u_light_dir;
uniform float     u_ambient;
uniform vec3      u_camera_pos;
uniform vec3      u_face_color;
uniform vec3      u_face_color2;      // cracks / moss accent
uniform vec3      u_rune_color;
uniform vec3      u_rune_color2;
uniform vec3      u_rim_color;
uniform float     u_rim_strength;
uniform vec3      u_spec_color;
uniform float     u_spec_power;
uniform float     u_opacity;
uniform vec2      u_atlas_dims;       // (cols, rows) for fract-derived local UV

// ── Procedural noise helpers (value noise + 4-octave fBm) ────────
float hash21(vec2 p) {
    p = fract(p * vec2(127.1, 311.7));
    p += dot(p, p + 19.19);
    return fract(p.x * p.y);
}
float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 4; i++) {
        v += a * vnoise(p);
        p = p * 2.1 + 17.3;
        a *= 0.5;
    }
    return v;
}

// ── Worley F2-F1: classic cellular "crack" pattern ───────────────
// Returns small values along the cell boundaries (where two jittered
// centres are equidistant), large values inside the cells.  We
// threshold that into a dark vein mask for the cracked-stone face.
float worley_cracks(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float d1 = 1.0e9;
    float d2 = 1.0e9;
    for (int yy = -1; yy <= 1; yy++) {
        for (int xx = -1; xx <= 1; xx++) {
            vec2 g = vec2(float(xx), float(yy));
            vec2 jitter = vec2(hash21(i + g),
                               hash21(i + g + vec2(13.0, 7.0)));
            vec2 dv = g + jitter - f;
            float dd = dot(dv, dv);
            if (dd < d1) { d2 = d1; d1 = dd; }
            else if (dd < d2) { d2 = dd; }
        }
    }
    return sqrt(d2) - sqrt(d1);
}

// ── Fire palette: black → ember → base → hot → peak white ────────
// Uses u_rune_color as the "base orange" and u_rune_color2 as the
// "hot yellow"; peaks to a near-white for the brightest flickers.
vec3 fire_color(float h) {
    h = clamp(h, 0.0, 1.0);
    vec3 ember = u_rune_color * 0.35;
    vec3 base  = u_rune_color;
    vec3 hot   = u_rune_color2;
    vec3 peak  = vec3(1.0, 1.0, 0.96);
    vec3 c = mix(ember, base, smoothstep(0.05, 0.40, h));
    c = mix(c, hot,  smoothstep(0.40, 0.75, h));
    c = mix(c, peak, smoothstep(0.85, 1.0,  h));
    return c;
}

void main() {
    vec3 N = normalize(v_world_normal);
    vec3 L = normalize(u_light_dir);
    vec3 V = normalize(u_camera_pos - v_world_pos);
    vec3 H = normalize(L + V);

    float diff = max(dot(N, L), 0.0);
    float light = u_ambient + (1.0 - u_ambient) * diff;
    float spec = pow(max(dot(N, H), 0.0), u_spec_power);
    float rim  = pow(1.0 - max(dot(N, V), 0.0), 3.5);

    // Per-face local UV (0..1 inside one atlas tile) — shared by face
    // and rune procedural effects so they line up with the triangle.
    vec2 face_uv = fract(v_uv * u_atlas_dims);

    // ── Face surface ────────────────────────────────────────────
    vec3 base_face = u_face_color;
    float spec_attn = 1.0;   // matte surfaces dim their own highlight

    if (u_face_effect == 1 || u_face_effect == 2) {
        // Stone tonal mottling — broad fBm warms / cools the base.
        float mottle = fbm(face_uv * 3.2);
        vec3 stone = u_face_color * (0.72 + 0.55 * mottle);

        // Primary cracks (chunky veins).
        float crack = worley_cracks(face_uv * 5.5);
        float crack_mask = smoothstep(0.10, 0.00, crack);
        // Secondary hairline crackle for surface detail.
        float fine = worley_cracks(face_uv * 13.0 + 3.7);
        crack_mask = max(crack_mask, smoothstep(0.05, 0.0, fine) * 0.45);
        base_face = mix(stone, u_face_color2, crack_mask);

        // Cracks aren't shiny.
        spec_attn = 0.35;

        if (u_face_effect == 2) {
            // Moss / lichen — soft green patches in the "valleys" of
            // another fBm field, modulated by fine noise so the edge
            // looks ragged rather than airbrushed.
            float moss = fbm(face_uv * 2.0 + vec2(5.3, 9.1));
            float moss_mask = smoothstep(0.50, 0.72, moss);
            moss_mask *= 0.55 + 0.45 * fbm(face_uv * 8.5);
            // Vary moss tint slightly per-face for richness.
            vec3 moss_color = u_face_color2 * (0.78 + 0.45 * mottle);
            base_face = mix(base_face, moss_color, clamp(moss_mask, 0.0, 0.92));
            // Moss is matte too.
            spec_attn = 0.20;
        }
    }

    vec3 face = base_face * light
              + u_spec_color * spec * 0.55 * spec_attn
              + u_rim_color  * rim  * u_rim_strength;

    vec3 final_rgb = face;

    if (u_have_runes == 1) {
        vec4 rune = texture(u_runes, v_uv);

        vec3 rune_visual;
        if (u_rune_effect == 1) {
            // ── Flaming: animated procedural fire inside the rune mask ─
            // Two noise octaves at very different frequencies — the low
            // one drives broad hot/cool patches, the high one adds the
            // crackling flicker. Both drift downward so the fire rises.
            vec2 q1 = face_uv * vec2(6.0, 10.0);
            q1.y -= u_time * 1.8;
            vec2 q2 = face_uv * vec2(14.0, 22.0) + vec2(7.3, 2.1);
            q2.y -= u_time * 3.1;
            float n = fbm(q1) * 0.65 + fbm(q2) * 0.35;
            // Keep most of the rune in the "base → hot" band; allow
            // occasional flickers through to the near-white peak.
            float heat = mix(0.30, 0.92, smoothstep(0.15, 0.85, n));
            rune_visual = fire_color(heat);
            // Faintly couple to face lighting so the rune still tracks
            // orientation, but mostly self-illuminated.
            rune_visual *= 0.92 + 0.18 * light;
        } else if (u_rune_effect == 2) {
            // ── Lightning: steady electric hum + occasional sharp flashes ─
            // Per-face seed so faces flash independently rather than
            // strobing in unison (that would be migraine-inducing).
            float fcol = floor(v_uv.x * u_atlas_dims.x);
            float frow = floor(v_uv.y * u_atlas_dims.y);
            float seed = fcol * 5.31 + frow * 11.7;
            // Gentle baseline shimmer.
            float hum = 0.50 + 0.18 * sin(u_time * 3.0 + seed);
            // Every ~0.25s each face rolls a die for a flash; ~20% hit.
            float bucket = floor(u_time * 4.0 + seed * 3.1);
            float roll = hash21(vec2(seed, bucket));
            float strobe = smoothstep(0.80, 0.99, roll);
            // Decay tail within the bucket so the flash feels electric.
            float tail = 1.0 - fract(u_time * 4.0 + seed * 3.1);
            tail = tail * tail;
            float intensity = max(hum, strobe * (0.65 + 0.55 * tail));
            // Blue glow at idle, near-white at peak.
            vec3 base_glow = mix(u_rune_color * 0.85, u_rune_color2,
                                 smoothstep(0.55, 1.20, intensity));
            rune_visual = base_glow * intensity;
            rune_visual *= 0.90 + 0.15 * light;
        } else {
            // ── Solid: classic lit rune
            rune_visual = u_rune_color * (0.60 + 0.55 * light)
                        + vec3(0.18) * spec;
        }

        final_rgb = mix(face, rune_visual, rune.a);
    }
    f_color = vec4(final_rgb, u_opacity);
}
"""


class SplashLayer(Layer):
    def __init__(self, name: str = "splash", z_order: int = 500):
        super().__init__(name=name, z_order=z_order)

        self._bg_prog: moderngl.Program | None = None
        self._bg_vao: moderngl.VertexArray | None = None

        self._prog: moderngl.Program | None = None
        self._vao: moderngl.VertexArray | None = None
        self._vcount: int = 0

        self._rune_tex: moderngl.Texture | None = None
        self._have_runes: bool = False

        # Address overlay
        self._addr_prog: moderngl.Program | None = None
        self._addr_vao: moderngl.VertexArray | None = None
        self._addr_tex: moderngl.Texture | None = None
        self._addr_lines: list[str] = []
        self._addr_dirty: bool = False

        # Active theme (drives all colour + effect uniforms)
        self._theme: SplashTheme = get_theme(DEFAULT_THEME)

        self._angle: float = 0.0
        self._elapsed: float = 0.0
        self._fade: float = 0.0     # 0..1 fade-in factor
        self._fade_target: float = 0.0

        self.visible = False

    # ── Setup ────────────────────────────────────────────────────

    def setup(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx

        # Backdrop fullscreen quad
        self._bg_prog = ctx.program(vertex_shader=_BG_VERT, fragment_shader=_BG_FRAG)
        bg_verts = [-1.0, -1.0,  1.0, -1.0,  -1.0, 1.0,  1.0, 1.0]
        bg_buf = ctx.buffer(struct.pack(f"{len(bg_verts)}f", *bg_verts))
        self._bg_vao = ctx.vertex_array(self._bg_prog, [(bg_buf, "2f", "in_pos")])

        # D20 mesh
        self._prog = ctx.program(vertex_shader=_D20_VERT, fragment_shader=_D20_FRAG)
        vbo_bytes, vcount = build_icosahedron_buffer(
            atlas_cols=RUNE_ATLAS_COLS, atlas_rows=RUNE_ATLAS_ROWS,
        )
        mesh_buf = ctx.buffer(vbo_bytes)
        self._vao = ctx.vertex_array(
            self._prog,
            [(mesh_buf, "3f 3f 2f", "in_pos", "in_normal", "in_uv")],
        )
        self._vcount = vcount

        # Rune atlas (may be None if PIL/font unavailable)
        atlas_img = build_rune_atlas()
        if atlas_img is not None:
            self._rune_tex = ctx.texture(atlas_img.size, 4, atlas_img.tobytes())
            self._rune_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._rune_tex.repeat_x = False
            self._rune_tex.repeat_y = False
            self._have_runes = True
            log.info("Rune atlas uploaded (%dx%d)", *atlas_img.size)
        else:
            log.info("Splash will render without rune texture")

        # Address overlay program (fullscreen-sized texture stamped by PIL)
        self._addr_prog = ctx.program(
            vertex_shader=_ADDR_VERT, fragment_shader=_ADDR_FRAG,
        )
        # UVs flipped on Y because PIL canvas origin is top-left.
        addr_verts = [
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
            -1.0,  1.0,  0.0, 0.0,
             1.0,  1.0,  1.0, 0.0,
        ]
        addr_buf = ctx.buffer(struct.pack(f"{len(addr_verts)}f", *addr_verts))
        self._addr_vao = ctx.vertex_array(
            self._addr_prog,
            [(addr_buf, "2f 2f", "in_pos", "in_uv")],
        )

    # ── Public API (wired by SSE later) ──────────────────────────

    def show(self) -> None:
        """Make the splash visible, fade in."""
        self.visible = True
        self._fade_target = 1.0

    def hide(self) -> None:
        """Fade the splash out (it becomes invisible once fade reaches 0)."""
        self._fade_target = 0.0

    def set_theme(self, name: str) -> None:
        """Switch to a named theme. Unknown names fall back to the default."""
        new_theme = get_theme(name)
        if new_theme is not self._theme:
            self._theme = new_theme
            log.info("Splash theme: %s", new_theme.name)

    def set_address(self, hostname: str, ip: str | None) -> None:
        """Set the hostname/IP shown at the bottom of the splash.

        Pass an empty/None value to omit that line. Texture rebuild is
        deferred to the next update() so we don't block whoever called us.
        """
        lines: list[str] = []
        if hostname:
            lines.append(f"http://{hostname}")
        if ip:
            lines.append(f"http://{ip}")
        if lines != self._addr_lines:
            self._addr_lines = lines
            self._addr_dirty = True

    # ── Lifecycle ────────────────────────────────────────────────

    def resize(self, width: int, height: int) -> None:
        super().resize(width, height)
        # Address texture has display-pixel dimensions; regen on resize.
        self._addr_dirty = True

    def update(self, dt: float) -> None:
        self._elapsed += dt
        self._angle += dt * 0.55  # rad/sec — slow, contemplative

        # Smooth fade between 0 and 1 (~0.3s)
        fade_speed = 3.5
        if self._fade < self._fade_target:
            self._fade = min(self._fade_target, self._fade + dt * fade_speed)
        elif self._fade > self._fade_target:
            self._fade = max(self._fade_target, self._fade - dt * fade_speed)

        if self._fade <= 0.001 and self._fade_target == 0.0:
            self.visible = False

        # Rebuild the address texture if anything that affects it changed.
        if self._addr_dirty and self.ctx is not None and self.width > 0:
            self._rebuild_address_texture()
            self._addr_dirty = False

    def render(self) -> None:
        assert self.ctx is not None
        if self._fade <= 0.001:
            return

        op = self.opacity * self._fade

        th = self._theme

        # Backdrop (no depth)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self._bg_prog["u_opacity"].value = op
        self._bg_prog["u_bg_inner"].value = th.backdrop_inner
        self._bg_prog["u_bg_outer"].value = th.backdrop_outer
        self._bg_vao.render(mode=moderngl.TRIANGLE_STRIP)

        # D20 (depth test + backface culling)
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.depth_func = "<"
        self.ctx.enable(moderngl.CULL_FACE)
        self.ctx.cull_face = "back"
        self.ctx.clear(depth=1.0)  # local depth for the 3D mesh

        aspect = self.width / max(self.height, 1)
        proj = Mat4.perspective_projection(aspect, 0.1, 100.0, 30.0)
        camera_pos = Vec3(0.0, 0.0, 4.8)
        view = Mat4.look_at(camera_pos,
                            Vec3(0.0, 0.0, 0.0),
                            Vec3(0.0, 1.0, 0.0))
        # Tilt the rotation axis off vertical so we see top + front faces.
        axis = Vec3(0.25, 1.0, 0.12).normalize()
        rot = Mat4.from_rotation(self._angle, axis)
        # Static tilt toward camera so the die never sits perfectly horizontal.
        tilt = Mat4.from_rotation(math.radians(18.0), Vec3(1.0, 0.0, 0.0))
        model = tilt @ rot
        mvp = proj @ view @ model

        p = self._prog
        p["u_mvp"].value = tuple(mvp)
        p["u_model"].value = tuple(model)
        p["u_camera_pos"].value = (camera_pos.x, camera_pos.y, camera_pos.z)
        p["u_time"].value = self._elapsed
        p["u_atlas_dims"].value = (float(RUNE_ATLAS_COLS), float(RUNE_ATLAS_ROWS))
        p["u_light_dir"].value = th.light_dir
        p["u_ambient"].value = th.ambient
        p["u_face_color"].value = th.face_color
        p["u_face_color2"].value = th.face_color2
        p["u_face_effect"].value = th.face_effect
        p["u_rune_color"].value = th.rune_color
        p["u_rune_color2"].value = th.rune_color2
        p["u_rim_color"].value = th.rim_color
        p["u_rim_strength"].value = th.rim_strength
        p["u_spec_color"].value = th.spec_color
        p["u_spec_power"].value = th.spec_power
        p["u_rune_effect"].value = th.rune_effect
        p["u_opacity"].value = op
        p["u_have_runes"].value = 1 if self._have_runes else 0

        if self._rune_tex is not None:
            self._rune_tex.use(location=0)
            p["u_runes"].value = 0

        self._vao.render(mode=moderngl.TRIANGLES, vertices=self._vcount)

        # Restore for the address overlay + subsequent layers
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.CULL_FACE)

        # Address overlay drawn on top of the D20
        if self._addr_tex is not None and self._addr_prog is not None and self._addr_vao is not None:
            self._addr_tex.use(location=0)
            self._addr_prog["u_tex"].value = 0
            self._addr_prog["u_opacity"].value = op
            self._addr_vao.render(mode=moderngl.TRIANGLE_STRIP)

    # ── Address texture (PIL-baked) ──────────────────────────────

    def _find_address_font(self) -> str | None:
        for p in _ADDR_FONT_CANDIDATES:
            if os.path.isfile(p):
                return p
        return None

    def _rebuild_address_texture(self) -> None:
        if not self._addr_lines:
            return
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            log.warning("PIL not available — address overlay disabled")
            return

        font_path = self._find_address_font()
        if font_path is None:
            log.warning("No suitable font for address overlay")
            return

        w, h = int(self.width), int(self.height)
        if w <= 0 or h <= 0:
            return

        # Light weight + medium size: airy, refined, still very readable.
        # Warm cream sits next to the gold runes without competing.
        font_size = max(26, h // 26)
        font = ImageFont.truetype(font_path, size=font_size)

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        line_gap = max(6, font_size // 4)
        line_height = font_size + line_gap
        total_h = line_height * len(self._addr_lines)
        margin_bottom = max(48, h // 14)
        y0 = h - total_h - margin_bottom

        text_color = (248, 234, 200, 240)
        shadow_color = (8, 6, 18, 140)
        shadow_offset = max(2, font_size // 14)

        for i, line in enumerate(self._addr_lines):
            y = y0 + i * line_height
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            x = (w - tw) // 2
            draw.text((x + shadow_offset, y + shadow_offset),
                      line, font=font, fill=shadow_color)
            draw.text((x, y), line, font=font, fill=text_color)

        data = img.tobytes()
        if self._addr_tex is not None and self._addr_tex.size == (w, h):
            self._addr_tex.write(data)
        else:
            if self._addr_tex is not None:
                self._addr_tex.release()
            assert self.ctx is not None
            self._addr_tex = self.ctx.texture((w, h), 4, data)
            self._addr_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._addr_tex.repeat_x = False
            self._addr_tex.repeat_y = False

    # ── Cleanup ──────────────────────────────────────────────────

    def teardown(self) -> None:
        for r in (self._vao, self._prog, self._bg_vao, self._bg_prog,
                  self._rune_tex, self._addr_vao, self._addr_prog,
                  self._addr_tex):
            if r is not None:
                r.release()
        self._vao = None
        self._prog = None
        self._bg_vao = None
        self._bg_prog = None
        self._rune_tex = None
        self._addr_vao = None
        self._addr_prog = None
        self._addr_tex = None
