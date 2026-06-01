"""
DnD Table – Media helpers (Flask-side).

File-type classification for the Library and play endpoints.  Actual
rendering happens elsewhere: visual media (images / video) flows as SSE
``play`` / ``stop`` events to the native ``dnd_display`` Wayland app
(GStreamer → moderngl texture).  Audio is no longer handled here — music
is delegated entirely to the headless music-output client (see music.py).
"""

from config import ALLOWED_EXTENSIONS


# ─── File type detection ─────────────────────────────────────────

def get_file_type(filename):
    """Classify a filename as 'image' or 'video' (None if unsupported)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ALLOWED_EXTENSIONS["video"]:
        return "video"
    if ext in ALLOWED_EXTENSIONS["image"]:
        return "image"
    return None
