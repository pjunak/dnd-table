"""
DnD Display – top-level app.

Owns the pyglet Window and the moderngl Context, builds the Compositor
with the canonical layer stack, and drives the per-frame update/render
cycle.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
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
from .themes import THEMES           # noqa: E402


# Where the Flask control plane publishes state events.  Always
# loopback — Flask listens on 0.0.0.0:5000 but the display app
# lives on the same box.
_SSE_URL = "http://127.0.0.1:5000/display/stream"

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

        # Theme selection — replaced by the SSE init message as soon
        # as the subscriber connects (~100ms after Flask is up).  This
        # local default is just what's shown during that brief gap;
        # "ancient" is intentionally picked so even a fresh box with
        # no settings.json shows off the cracked-stone work.
        # Press T to cycle through all registered themes.
        self._theme_names = list(THEMES.keys())
        self._theme_idx = self._theme_names.index("ancient") \
            if "ancient" in self._theme_names else 0
        self.splash.set_theme(self._theme_names[self._theme_idx])

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

    # ── State hooks (called on main thread; safe for GL) ─────────

    def set_splash_theme(self, name: str) -> None:
        """Apply a theme by name and keep the T-cycle index in sync.

        Safe to call from the main thread only.  The SSE subscriber
        marshals onto the main thread via `pyglet.clock.schedule_once`.
        """
        if name not in THEMES:
            log.warning("Ignoring unknown splash theme: %r", name)
            return
        if name in self._theme_names:
            self._theme_idx = self._theme_names.index(name)
        self.splash.set_theme(name)

    # ── pyglet event hooks ───────────────────────────────────────

    def on_key_press(self, symbol, modifiers):
        if symbol in (pyglet.window.key.ESCAPE, pyglet.window.key.Q):
            log.info("Exit requested via keyboard")
            self.close()
        elif symbol == pyglet.window.key.T:
            # Cycle splash themes — quick preview without restarting.
            self._theme_idx = (self._theme_idx + 1) % len(self._theme_names)
            self.splash.set_theme(self._theme_names[self._theme_idx])

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


# ── SSE bridge ───────────────────────────────────────────────────

def _dispatch_event(window: "DndDisplay", data: dict) -> None:
    """Translate one SSE payload into main-thread UI updates.

    Runs on the subscriber thread; everything that touches GL or
    pyglet state is bounced onto the main thread via
    `pyglet.clock.schedule_once`.
    """
    evt = data.get("type")

    # Theme can ride along on either the init snapshot or a dedicated
    # `splash_theme` event — handle both the same way.
    theme: str | None = None
    if evt == "init":
        theme = data.get("splash_theme")
    elif evt == "splash_theme":
        theme = data.get("theme")
    if theme:
        pyglet.clock.schedule_once(
            lambda dt, n=theme: window.set_splash_theme(n), 0,
        )

    # Future: grid / overscan / play / stop dispatched here too.


def _start_sse_subscriber(window: "DndDisplay") -> None:
    """Start a daemon thread that subscribes to Flask's SSE stream.

    Theme changes flow through this; grid / video / overscan will be
    added once their consumer code exists.  Connection failures are
    retried with exponential backoff capped at 15 s so a Flask
    restart (e.g., after `Update & Restart` from the control panel)
    recovers automatically.
    """
    try:
        import requests
        import sseclient
    except ImportError:
        log.warning("requests/sseclient-py unavailable — SSE bridge disabled "
                    "(theme changes will only apply after restart)")
        return

    def _run() -> None:
        backoff = 1.0
        while True:
            try:
                # First number is connect timeout; None for read so SSE
                # heartbeats don't trip a timeout between events.
                resp = requests.get(_SSE_URL, stream=True, timeout=(5, None))
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                log.info("SSE connected to %s", _SSE_URL)
                backoff = 1.0
                client = sseclient.SSEClient(resp)
                for ev in client.events():
                    if not ev.data:
                        continue
                    try:
                        _dispatch_event(window, json.loads(ev.data))
                    except json.JSONDecodeError as e:
                        log.warning("Malformed SSE payload (%s): %r", e, ev.data[:80])
                    except Exception:
                        log.exception("Error dispatching SSE event")
            except Exception as e:
                log.warning("SSE disconnected (%s) — retrying in %.1fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 15.0)

    t = threading.Thread(target=_run, name="sse-subscriber", daemon=True)
    t.start()


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
        window = DndDisplay()
        # Subscribe to Flask's SSE stream so the control panel can
        # drive UI state (theme, grid, video) live.  The subscriber
        # is daemonised so process exit doesn't hang on it.
        _start_sse_subscriber(window)
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
