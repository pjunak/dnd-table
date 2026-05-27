"""
DnD Table – Configuration constants.
"""

import os
from pathlib import Path

# ─── Media directories ───────────────────────────────────────────
# The SD card is the only fixed mount; USB drives are discovered at
# runtime by ``files.detect_usb_drives()`` (looks under /media/$USER
# and /run/media/$USER).  Only ``sdcard`` is referenced for the upload
# destination and the splash address overlay.
MEDIA_DIRS = {
    "sdcard": Path("/media/dnd_media"),
}
UPLOAD_DIR = Path("/media/dnd_media")

# ─── Allowed file extensions ─────────────────────────────────────
ALLOWED_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"},
    "video": {"mp4", "mkv", "webm", "avi", "mov", "m4v", "ts"},
    "audio": {"mp3", "ogg", "flac", "wav", "m4a", "aac"},
}

# ─── Folder structure ────────────────────────────────────────────
PROTECTED_FOLDERS = ["Maps", "Videos", "Ambient", "SFX"]

# ─── MPV IPC socket (ambient audio) ──────────────────────────────
# Per-user runtime dir (0700) instead of world-writable /tmp so other
# local users can't connect to the MPV control channel.  Falls back to
# /tmp when XDG_RUNTIME_DIR isn't set (e.g., dev runs outside systemd).
_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
if not os.path.isdir(_RUNTIME_DIR):
    _RUNTIME_DIR = "/tmp"
MPV_AUDIO_SOCKET = os.path.join(_RUNTIME_DIR, "dnd-mpv-audio.sock")

# ─── Native display app IPC ──────────────────────────────────────
# The Flask server emits state via SSE on /display/stream; the native
# `dnd_display` app subscribes there. No X11/Wayland socket constants
# needed here — the display app owns its own compositor connection.
