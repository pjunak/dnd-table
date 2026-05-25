"""
DnD Display – top-level app.

Owns the pyglet Window and the moderngl Context, builds the Compositor
with the canonical layer stack, and drives the per-frame update/render
cycle.
"""

from __future__ import annotations

import logging
import os
import sys
import time

# pyglet honours the backend env var only at import time.
os.environ.setdefault("PYGLET_BACKEND", "wayland")

import pyglet                       # noqa: E402
import moderngl                      # noqa: E402

from .compositor import Compositor   # noqa: E402
from .layers import (                # noqa: E402
    DebugTriangleLayer,
    GridLayer,
    SplashLayer,
    VideoLayer,
)
from .network import get_hostname, get_local_ip   # noqa: E402

log = logging.getLogger(__name__)


# Canonical layer z-order (low → high):
#   100  video        — map/ambient background
#   200  grid         — square/hex overlay
#   300  tokens       — sprites (future)
#   400  vfx          — fog/lighting/weather (future)
#   500  splash       — D20 splash overrides the scene (future)
#   900  debug        — dev-only overlays


class DndDisplay(pyglet.window.Window):

    def __init__(self) -> None:
        config = pyglet.gl.Config(
            major_version=3, minor_version=3,
            depth_size=24, double_buffer=True,
            sample_buffers=1, samples=4,
        )
        super().__init__(
            fullscreen=True, vsync=True, config=config,
            caption="DnD Display",
        )
        self.set_mouse_visible(False)

        self.ctx = moderngl.create_context(require=330)
        info = self.ctx.info
        log.info("GL_VENDOR    = %s", info.get("GL_VENDOR"))
        log.info("GL_RENDERER  = %s", info.get("GL_RENDERER"))
        log.info("GL_VERSION   = %s", info.get("GL_VERSION"))
        log.info("GLSL_VERSION = %s", info.get("GL_SHADING_LANGUAGE_VERSION"))

        self.compositor = Compositor(self.ctx, self.width, self.height)

        # Layer stack
        self.video = VideoLayer(z_order=100)
        self.grid = GridLayer(z_order=200)
        self.splash = SplashLayer(z_order=500)
        self.debug = DebugTriangleLayer(z_order=900)
        self.debug.opacity = 0.0  # off by default; flip in-code for debugging
        self.debug.visible = False
        self.compositor.add(self.video)
        self.compositor.add(self.grid)
        self.compositor.add(self.splash)
        self.compositor.add(self.debug)

        # ── Dev defaults (replaced by SSE state in task #10) ─────
        # Visible grid so we can see the compositor working.
        self.grid.set_state({
            "enabled": True,
            "type": "square",
            "size": 80,
            "thickness": 2,
            "opacity": 0.55,
            "color": "#22ff66",
            "offset_x": 0,
            "offset_y": 0,
        })
        # Start the videotestsrc smpte pattern as a placeholder background.
        # Real video plays via play_file() once the SSE bridge is wired.
        self.video.play_test_pattern("smpte")

        # Theme selection — eventually driven by Flask settings + SSE.
        # Try "flame" first to see the procedural fire effect; switch back
        # to "arcane" (default purple+gold) by changing this string.
        self.splash.set_theme("flame")

        # Show the splash on launch so we can see it; SSE will own this
        # decision once the bridge is wired.
        self.splash.show()

        # Populate the address overlay immediately and refresh periodically
        # so the splash always shows the right URL (e.g., after a DHCP renew).
        self._refresh_address(0.0)
        pyglet.clock.schedule_interval(self._refresh_address, 5.0)

        self._last_time = time.monotonic()
        self._frame_count = 0

    # ── Address refresh ──────────────────────────────────────────

    def _refresh_address(self, dt: float) -> None:
        hostname = get_hostname()
        ip = get_local_ip()
        # No-op if unchanged; SplashLayer dedupes.
        self.splash.set_address(hostname, ip)

    # ── pyglet event hooks ───────────────────────────────────────

    def on_key_press(self, symbol, modifiers):
        if symbol in (pyglet.window.key.ESCAPE, pyglet.window.key.Q):
            log.info("Exit requested via keyboard")
            self.close()

    def on_resize(self, width, height):
        super().on_resize(width, height)
        if hasattr(self, "compositor"):
            self.compositor.resize(width, height)
            self.ctx.viewport = (0, 0, width, height)

    def on_draw(self):
        now = time.monotonic()
        dt = now - self._last_time
        self._last_time = now

        self.compositor.update(dt)
        self.ctx.viewport = (0, 0, self.width, self.height)
        self.ctx.clear(0.05, 0.03, 0.08, 1.0)
        self.compositor.render()

        self._frame_count += 1
        if self._frame_count == 1:
            log.info("First composited frame rendered (%dx%d)",
                     self.width, self.height)

    def on_close(self):
        log.info("Window closing — tearing down compositor")
        self.compositor.teardown()
        super().on_close()


# ── Entry point ──────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log.info("dnd_display starting (pyglet %s, moderngl %s)",
             pyglet.version, moderngl.__version__)
    try:
        DndDisplay()
        # pyglet 2.x defaults to event-driven rendering (only redraws on
        # invalidation, to save power). For continuous video + animation
        # we want a fixed-rate loop; vsync still caps the actual rate to
        # the display refresh.
        pyglet.app.run(interval=1.0 / 60.0)
    except KeyboardInterrupt:
        log.info("Interrupted, exiting")
    except Exception:
        log.exception("Fatal error in display app")
        return 1
    return 0
