"""
DnD Table – Configuration constants.
"""

import os
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

# ─── X11 display ─────────────────────────────────────────────────
DISPLAY = os.environ.get("DISPLAY", ":0")

# ─── MPV IPC socket (ambient audio) ──────────────────────────────
MPV_AUDIO_SOCKET = "/tmp/mpv_audio.sock"
