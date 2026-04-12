"""
DnD Table – Shared mutable state.

All state lives here so every module can import it without circular deps.
"""

# ─── Audio process (MPV subprocess for ambient) ──────────────────
audio_process = None
current_audio = None

# ─── Display state (what the browser display page is showing) ────
current_file = None       # filename only, for control panel display
current_file_path = None  # absolute path, for building media URL
current_file_info = None  # dict with size, type, duration

# ─── Grid overlay state ─────────────────────────────────────────
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

# ─── Overscan calibration ──────────────────────────────────────────
overscan_state = {
    "top": 0,
    "bottom": 0,
    "left": 0,
    "right": 0,
    "calibration": False,
}

# ─── Volume (0–100) ──────────────────────────────────────────────
video_volume = 80
audio_volume = 80
sfx_volume = 80

# ─── Display mode & TV settings ─────────────────────────────────
display_mode = "display"       # "display" or "tv"
tv_color_range = "full"        # "full" or "limited"
tv_underscan = False           # enable underscan via xrandr
tv_sharpness = False           # disable GPU scaling (dot-by-dot)

# ─── TV property availability (probed at startup) ──────────────
tv_props_available = {"color_range": False, "underscan": False, "sharpness": False}

# ─── Platform info (detected at startup) ───────────────────────
platform_info = {}             # {"is_rpi": bool, "model": str|None}
boot_overscan = {}             # {"top": 0, "bottom": 0, "left": 0, "right": 0}
