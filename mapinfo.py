"""
DnD Table – Map introspection helpers (Flask-side).

Two things the scene system needs about the current map:
  - its native PIXEL dimensions, so the panel can author walls/tokens/fog in
    map-pixel coordinates (the canonical, resolution-independent space);
  - a still backdrop for the authoring canvas — the image file itself for
    image maps, or one extracted frame for video maps (the map barely changes,
    so a single frozen frame is all the GM needs to draw on).

Uses Pillow (images) and ffprobe/ffmpeg (video), both already installed.
"""

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def dimensions(path, file_type):
    """Return ``(width, height)`` in pixels for the map, or ``(None, None)``."""
    path = str(path)
    if file_type == "image":
        try:
            from PIL import Image
            with Image.open(path) as im:
                return int(im.width), int(im.height)
        except Exception as e:
            log.warning("PIL could not size %s: %s", path, e)
        return None, None
    if file_type == "video":
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                 path],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "x" in r.stdout:
                w, h = r.stdout.strip().split("x")[:2]
                return int(w), int(h)
        except Exception as e:
            log.warning("ffprobe could not size %s: %s", path, e)
    return None, None


def extract_still(path, out_path):
    """Extract a single frame from a video map to ``out_path`` (PNG) via
    ffmpeg.  Returns True on success.  (Image maps need no extraction — serve
    the original file directly.)"""
    path, out_path = str(path), str(out_path)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-vframes", "1", "-q:v", "3", out_path],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0 and os.path.exists(out_path)
    except Exception as e:
        log.warning("ffmpeg frame extract failed for %s: %s", path, e)
        return False
