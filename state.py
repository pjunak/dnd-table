"""
DnD Table – Shared mutable state.

All state lives here so every module can import it without circular deps.
This module is the source of truth for the Flask control plane; the native
display app (`dnd_display`) subscribes to changes via the SSE bridge in
routes.py.
"""

# ─── Display state (what the native display app is showing) ──────
current_file = None       # filename only, for control panel display
current_file_path = None  # absolute path, native display loads from here
current_file_info = None  # dict with size, type, duration

# ─── Grid overlay state ─────────────────────────────────────────
# The native display app consumes this verbatim to render its grid layer.
grid_state = {
    "enabled": False,
    "type": "square",
    "size": 55,
    "thickness": 1,
    "opacity": 0.6,
    "color": "#000000",
    "offset_x": 0,
    "offset_y": 0,
    "ppi": 55,
    "calibration_mode": False,
}

# ─── Safe-area inset (pixels cropped from each edge of the render) ──
# Used to be "overscan" on the RPi (compensating for TV overscan). On the
# x86 + Wayland stack the TV handles its own scaling, so this is now a
# generic letterbox/safe-area for content placement. Data shape preserved
# to keep the control panel UI compatible during the migration.
overscan_state = {
    "top": 0,
    "bottom": 0,
    "left": 0,
    "right": 0,
    "calibration": False,
}

# ─── Volume (0–100) ──────────────────────────────────────────────
# Map / video output volume.  (Video audio isn't wired into the GStreamer
# pipeline yet; kept for the Map card + future use.  Music lives in the
# separate headless music-output client, not here.)
video_volume = 80

# ─── Display output preference ───────────────────────────────────
# Mode string like "1920x1080@60", or None to use the compositor default
# (whatever the TV's EDID reports as preferred).
display_mode_pref: str | None = None

# ─── Splash screen theme ─────────────────────────────────────────
# Name from dnd_display.themes.THEMES — controls the look of the
# rotating D20 splash (face material, rune effect, backdrop).
# The native display app reads this via the SSE bridge and on its
# own startup (restored from settings.json by main.py).
splash_theme: str = "arcane"

# ─── Scene (per-map VTT layer) ───────────────────────────────────
# The current map's interactive scene as a SceneData payload dict — walls,
# doors, lights, tokens, fog, markers — or None when no map/scene is loaded.
# Persisted per-map by scene_store (sidecar .scene.json) and pushed to the
# native display via the `scene` SSE event.
scene = None

# Native pixel size (width, height) of the current map, or None.  Authoring
# happens in map-pixel coordinates, so the panel and display both need this.
map_size = None
