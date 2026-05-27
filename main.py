#!/usr/bin/env python3
"""
DnD Table Flask Control Plane
=============================
REST API + SSE bridge for the DnD table display.

Architecture (x86 + Wayland + native display app):

    ┌──────────────────────────────────┐
    │ Flask (this process, port 5000)  │
    │  - control.html for phone        │
    │  - REST API                      │
    │  - SSE stream on /display/stream │
    └─────────────┬────────────────────┘
                  │ SSE
                  ▼
    ┌──────────────────────────────────┐
    │ dnd_display (native Wayland app) │
    │  - GStreamer → GL texture        │
    │  - moderngl layer compositor     │
    │  - launched by cage via kiosk.sh │
    └──────────────────────────────────┘

No chromium, no xrandr, no RPi config.txt — those concerns belong to the
greetd/cage stack (kiosk.sh) and the native display app respectively.
"""

import atexit
import logging

from flask import Flask

from config import MEDIA_DIRS, UPLOAD_DIR
from media import kill_audio
from files import ensure_default_folders
from routes import register_routes
import state
import settings as settings_store


app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
# Reject uploads larger than 4 GB so a single bad request can't fill the
# SD card.  Tweak in config.py if you regularly host bigger map videos.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024
register_routes(app)


def _cleanup():
    """Stop ambient audio on exit."""
    kill_audio()


# ─── Entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  ⚔  DnD Table — Flask Control Plane")
    print("  ═" * 36)
    print(f"  SD card  : {MEDIA_DIRS['sdcard']}")
    print(f"  Uploads  : {UPLOAD_DIR}")
    print(f"  Control  : http://dndtable.local         (port 80 → 5000 via iptables)")
    print(f"  SSE      : http://localhost:5000/display/stream\n")

    ensure_default_folders()

    # ─── Restore persisted settings ─────────────────────────────
    saved = settings_store.load()
    if saved.get("grid"):
        state.grid_state.update(saved["grid"])
        state.grid_state["calibration_mode"] = False
    if saved.get("overscan"):
        state.overscan_state.update(saved["overscan"])
        state.overscan_state["calibration"] = False
    if saved.get("volumes"):
        state.video_volume = saved["volumes"].get("map", 80)
        state.audio_volume = saved["volumes"].get("ambient", 80)
        state.sfx_volume = saved["volumes"].get("sfx", 80)
    if saved.get("display"):
        state.display_mode_pref = saved["display"].get("mode") or None
    if saved.get("splash"):
        theme_name = saved["splash"].get("theme")
        if theme_name:
            state.splash_theme = theme_name

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    atexit.register(_cleanup)

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
