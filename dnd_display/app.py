"""
DnD Display – top-level app.

Owns the pyglet Window and the moderngl Context, builds the Compositor
with the canonical layer stack, and drives the per-frame update/render
cycle.  All cross-thread state changes from Flask flow in through the
SSE subscriber, which marshals them onto the main thread via
``pyglet.clock.schedule_once`` so GL work stays single-threaded.
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

import pyglet                        # noqa: E402
import moderngl                       # noqa: E402

from .compositor import Compositor    # noqa: E402
from .layers import (                 # noqa: E402
    CalibrationLayer,
    FogVisionLayer,
    GridLayer,
    MarkerLayer,
    SplashLayer,
    TokenLayer,
    VideoLayer,
)
from .network import get_hostname, get_local_ip   # noqa: E402
from .themes import THEMES            # noqa: E402
from .transform import MapTransform   # noqa: E402
from .scene import SceneData, FOG_DYNAMIC   # noqa: E402
from . import vision                  # noqa: E402


# Where the Flask control plane publishes state events.  Always
# loopback — Flask listens on 0.0.0.0:5000 but the display app
# lives on the same box.
_SSE_URL = "http://127.0.0.1:5000/display/stream"

log = logging.getLogger(__name__)


# Canonical layer z-order (low → high):
#   100  video        — map / video background (when a file is playing)
#   200  grid         — square / hex overlay
#   300  tokens       — sprites (future)
#   400  vfx          — fog / lighting / weather (future)
#   500  splash       — D20 splash; visible whenever nothing is playing
#   950  calibration  — red/green safe-area guides (only while calibrating)


class DndDisplay(pyglet.window.Window):
    """Pyglet window + moderngl context + canonical layer stack."""

    def __init__(self) -> None:
        config = pyglet.gl.Config(
            major_version=3, minor_version=3,
            depth_size=24, double_buffer=True,
            sample_buffers=1, samples=4,
        )
        # DND_WINDOWED=1 opens a regular 1280×720 window instead of
        # fullscreen — useful for dev runs on a workstation where you
        # don't want the app to take over the whole display.
        windowed = os.environ.get("DND_WINDOWED") == "1"
        if windowed:
            super().__init__(
                width=1280, height=720, vsync=True, config=config,
                caption="DnD Display (windowed)", resizable=True,
            )
        else:
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

        # ── Compositor + layer stack ────────────────────────────────
        self.compositor = Compositor(self.ctx, self.width, self.height)

        self.video = VideoLayer(z_order=100)
        self.grid = GridLayer(z_order=200)
        self.tokens = TokenLayer(z_order=300)
        self.markers = MarkerLayer(z_order=350)
        self.fog = FogVisionLayer(z_order=400)
        self.splash = SplashLayer(z_order=500)
        self.calibration = CalibrationLayer(z_order=950)

        self.compositor.add(self.video)
        self.compositor.add(self.grid)
        self.compositor.add(self.tokens)
        self.compositor.add(self.markers)
        self.compositor.add(self.fog)
        self.compositor.add(self.splash)
        self.compositor.add(self.calibration)
        self.calibration.set_framebuffer_size(self.width, self.height)

        # ── Provisional defaults (overwritten by SSE init ~100 ms in) ─
        # Grid invisible until the user enables it from the panel.
        self.grid.set_state({
            "enabled": False,
            "type": "square",
            "size": 55,
            "thickness": 1,
            "opacity": 0.6,
            "color": "#000000",
            "offset_x": 0,
            "offset_y": 0,
        })

        # Theme — local fallback during the brief gap before SSE init lands.
        # "ancient" picked so a fresh box shows off the cracked-stone work.
        self._theme_names = list(THEMES.keys())
        self._theme_idx = self._theme_names.index("ancient") \
            if "ancient" in self._theme_names else 0
        self.splash.set_theme(self._theme_names[self._theme_idx])

        # ── Playback state ──────────────────────────────────────────
        self._current_file_path: str | None = None

        # ── Scene state (VTT overlay: walls/tokens/fog/markers) ─────
        # Geometry is in map-image pixels; the shared MapTransform maps it
        # onto the letterboxed map.  Layers that consume it are added in
        # later workstreams; for now we hold the parsed scene + transform.
        self._scene = None
        self._map_size: tuple[int, int] | None = None
        self._map_transform: MapTransform | None = None

        self._update_splash_visibility()

        # Address overlay — refreshed periodically so DHCP renews show up.
        self._refresh_address(0.0)
        pyglet.clock.schedule_interval(self._refresh_address, 5.0)

        self._last_time = time.monotonic()
        self._frame_count = 0

    # ── Address refresh ──────────────────────────────────────────

    def _refresh_address(self, dt: float) -> None:
        hostname = get_hostname()
        ip = get_local_ip()
        # No-op if unchanged; SplashLayer dedupes internally.
        self.splash.set_address(hostname, ip)

    # ── State setters (main thread; safe for GL) ─────────────────

    def set_splash_theme(self, name: str) -> None:
        """Apply a theme by name, keeping the T-cycle index aligned."""
        if name not in THEMES:
            log.warning("Ignoring unknown splash theme: %r", name)
            return
        if name in self._theme_names:
            self._theme_idx = self._theme_names.index(name)
        self.splash.set_theme(name)

    def play_file(self, path: str | None) -> None:
        """Start playback of ``path`` and hide the splash.

        Passing None / empty path is a no-op; use ``stop()`` to clear.
        """
        if not path:
            return
        log.info("Playing: %s", path)
        self._current_file_path = path
        # GStreamer / PIL plumbing lives in VideoLayer; it handles both
        # video files and (via decodebin) still images.
        self.video.play_file(path)
        self._update_splash_visibility()

    def stop_playback(self) -> None:
        """Stop the video layer and bring the splash back."""
        if self._current_file_path is None and not self.video.visible:
            return
        log.info("Stopping playback")
        self._current_file_path = None
        self.video.stop()
        self._update_splash_visibility()

    def show_test_pattern(self) -> None:
        """Show SMPTE colour bars on the video layer (display diagnostic).

        Exposed from the panel as Settings → Display Test.  Replaces
        whatever the video layer was showing; ``stop_test_pattern``
        restores the previous scene.
        """
        log.info("Showing test pattern")
        self.video.play_test_pattern("smpte")
        self.splash.hide()

    def stop_test_pattern(self) -> None:
        """Hide the test pattern and restore the previous scene."""
        log.info("Stopping test pattern")
        if self._current_file_path:
            self.video.play_file(self._current_file_path)
        else:
            self.video.stop()
            self._update_splash_visibility()

    def set_grid_state(self, st: dict) -> None:
        """Forward a grid_state dict from Flask to the GridLayer."""
        self.grid.set_state(st)

    def set_overscan(self, ov: dict) -> None:
        """Update safe-area inset + calibration overlay.

        ``ov`` mirrors the Flask state dict: top/bottom/left/right (px)
        and a ``calibration`` boolean that toggles the red/green guides.
        """
        top = int(ov.get("top", 0) or 0)
        bottom = int(ov.get("bottom", 0) or 0)
        left = int(ov.get("left", 0) or 0)
        right = int(ov.get("right", 0) or 0)
        calibrating = bool(ov.get("calibration", False))

        self.compositor.set_overscan(top=top, bottom=bottom,
                                     left=left, right=right)
        self.calibration.set_inset(top, bottom, left, right)
        self.calibration.set_framebuffer_size(self.width, self.height)
        self.calibration.visible = calibrating
        # Inset changed → the map's on-screen rectangle moved; refresh the
        # transform the overlay layers use to align with it.
        self._rebuild_map_transform()

    # ── Scene (VTT overlay) ──────────────────────────────────────

    def set_scene(self, payload: dict | None) -> None:
        """Apply the current map's scene (walls/doors/lights/tokens/fog/
        markers) pushed from Flask.

        Scene geometry is in map-image pixels; we (re)build the shared
        ``MapTransform`` from the map's native size + the current inset
        viewport so every overlay layer lines up with the letterboxed map.
        """
        self._scene = SceneData.from_payload(payload) if payload else None
        if self._scene and self._scene.width > 0 and self._scene.height > 0:
            self._map_size = (self._scene.width, self._scene.height)
        self._rebuild_map_transform()
        self._apply_scene()

    def _rebuild_map_transform(self) -> None:
        """Recompute the map-px → screen transform from the current map size
        and inset viewport.  Cheap; safe on resize / overscan / scene change."""
        if not self._map_size:
            self._map_transform = None
        else:
            tw, th = self._map_size
            self._map_transform = MapTransform(
                tw, th, self.compositor.width, self.compositor.height)
        # Overlay layers reuse this transform; refresh theirs without rebaking
        # any textures (only the geometry mapping changed).
        self.tokens.set_transform(self._map_transform)
        self.markers.set_transform(self._map_transform)
        self.fog.set_transform(self._map_transform)

    def _apply_scene(self) -> None:
        """Push the parsed scene + transform into the overlay layers, and
        recompute vision (CPU) → fog mask."""
        self.tokens.set_scene(self._scene, self._map_transform)
        self.markers.set_scene(self._scene, self._map_transform)
        if self._scene is not None:
            fans = (vision.compute_scene_fans(self._scene)
                    if self._scene.fog.mode == FOG_DYNAMIC else [])
            self.fog.set_data(enabled=self._scene.fog.enabled, fans=fans,
                              revealed=self._scene.fog.revealed,
                              transform=self._map_transform)
            log.info("Scene applied: %d walls, %d doors, %d tokens, %d markers, %d fans",
                     len(self._scene.walls), len(self._scene.doors),
                     len(self._scene.tokens), len(self._scene.markers), len(fans))
        else:
            self.fog.set_data(enabled=False, fans=[], revealed=[], transform=None)

    # ── Internal helpers ─────────────────────────────────────────

    def _update_splash_visibility(self) -> None:
        """Splash is shown only when nothing is playing."""
        if self._current_file_path:
            self.splash.hide()
        else:
            self.splash.show()

    # ── pyglet event hooks ───────────────────────────────────────

    def on_key_press(self, symbol, modifiers):
        if symbol in (pyglet.window.key.ESCAPE, pyglet.window.key.Q):
            log.info("Exit requested via keyboard")
            self.close()
        elif symbol == pyglet.window.key.T:
            # Cycle splash themes — quick preview without restarting.
            # NB: this updates only the local app; Flask doesn't learn
            # about it until the next /api/splash/theme POST, so the
            # control panel may temporarily show a stale highlight.
            self._theme_idx = (self._theme_idx + 1) % len(self._theme_names)
            self.splash.set_theme(self._theme_names[self._theme_idx])

    def on_resize(self, width, height):
        super().on_resize(width, height)
        if hasattr(self, "compositor"):
            self.compositor.resize(width, height)
            self.calibration.set_framebuffer_size(width, height)
            self._rebuild_map_transform()

    def on_draw(self):
        now = time.monotonic()
        dt = now - self._last_time
        self._last_time = now

        self.compositor.update(dt)
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
    ``pyglet.clock.schedule_once``.

    The ``init`` event carries the entire state snapshot Flask had at
    the moment of connection; we apply each piece individually so a
    mid-session reconnect lands exactly where the user left it
    (current map, grid, overscan, theme).
    """
    evt = data.get("type")

    def schedule(fn, *args):
        # Capture args at call time so the lambda doesn't see later loop vars.
        pyglet.clock.schedule_once(lambda dt, fn=fn, a=args: fn(*a), 0)

    if evt == "init":
        if data.get("splash_theme"):
            schedule(window.set_splash_theme, data["splash_theme"])
        if isinstance(data.get("grid"), dict):
            schedule(window.set_grid_state, data["grid"])
        if isinstance(data.get("overscan"), dict):
            schedule(window.set_overscan, data["overscan"])
        if isinstance(data.get("scene"), dict):
            schedule(window.set_scene, data["scene"])
        if data.get("file_path"):
            schedule(window.play_file, data["file_path"])
        else:
            # Explicit stop in case the previous run left the layer playing.
            schedule(window.stop_playback)
        return

    if evt == "splash_theme":
        if data.get("theme"):
            schedule(window.set_splash_theme, data["theme"])
        return

    if evt == "play":
        # Prefer the absolute path; ``url`` is for legacy browser clients.
        path = data.get("path") or data.get("file_path")
        if path:
            schedule(window.play_file, path)
        return

    if evt == "stop":
        schedule(window.stop_playback)
        return

    if evt == "test_pattern":
        if data.get("on"):
            schedule(window.show_test_pattern)
        else:
            schedule(window.stop_test_pattern)
        return

    if evt == "scene":
        schedule(window.set_scene, data.get("scene"))
        return

    if evt == "grid":
        if isinstance(data.get("grid"), dict):
            schedule(window.set_grid_state, data["grid"])
        return

    if evt == "overscan":
        if isinstance(data.get("overscan"), dict):
            schedule(window.set_overscan, data["overscan"])
        return

    # `volume` isn't a display-side concern — Flask owns it (UI sync via
    # /status).  Unknown event types are silently ignored so future Flask
    # additions don't crash an older display app.


def _start_sse_subscriber(window: "DndDisplay") -> None:
    """Daemon thread that subscribes to Flask's SSE stream.

    Reconnects with capped exponential backoff so Flask restarts
    (including the updater's `dnd-table.service` bounce) recover
    automatically.
    """
    try:
        import requests
        import sseclient
    except ImportError:
        log.warning("requests/sseclient-py unavailable — SSE bridge disabled "
                    "(control panel changes will only apply after restart)")
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
        _start_sse_subscriber(window)
        # pyglet 2.x defaults to event-driven rendering (only redraws on
        # invalidation, to save power).  For continuous video + animation
        # we want a fixed-rate loop; vsync still caps the actual rate to
        # the display refresh.
        pyglet.app.run(interval=1.0 / 60.0)
    except KeyboardInterrupt:
        log.info("Interrupted, exiting")
    except Exception:
        log.exception("Fatal error in display app")
        return 1
    return 0
