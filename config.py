"""
DnD Table – Configuration constants.
"""

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
# Audio is intentionally absent: the table no longer plays local audio.
# Music is delegated to the headless music-output client (see music.py /
# system/music-output.service), so only visual media is uploaded and
# shown in the Library.
ALLOWED_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"},
    "video": {"mp4", "mkv", "webm", "avi", "mov", "m4v", "ts"},
}

# ─── Folder structure ────────────────────────────────────────────
PROTECTED_FOLDERS = ["Maps", "Videos"]

# ─── Music output (headless client control surface) ──────────────
# The table runs pjunak/music's `music_output.py` as a systemd service;
# it serves a localhost on/off + volume control surface that Flask
# proxies (see music.py).  Host/port mirror MUSIC_CONTROL_PORT in
# /etc/music-output.env.
MUSIC_CONTROL_URL = "http://127.0.0.1:8731"

# ─── Native display app IPC ──────────────────────────────────────
# The Flask server emits state via SSE on /display/stream; the native
# `dnd_display` app subscribes there. No X11/Wayland socket constants
# needed here — the display app owns its own compositor connection.
