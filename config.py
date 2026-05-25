"""
DnD Table – Configuration constants.
"""

from pathlib import Path

# ─── Media directories ───────────────────────────────────────────
MEDIA_DIRS = {
    "usb": Path("/media/dnd_usb"),
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
MPV_AUDIO_SOCKET = "/tmp/mpv_audio.sock"

# ─── Native display app IPC ──────────────────────────────────────
# The Flask server emits state via SSE on /display/stream; the native
# `dnd_display` app subscribes there. No X11/Wayland socket constants
# needed here — the display app owns its own compositor connection.
