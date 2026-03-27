#!/usr/bin/env python3
"""
DnD Table Display Server
========================
Web interface for controlling a fullscreen display on a D&D table.

Architecture:
  Chromium kiosk  →  http://localhost:5000/display  (media + grid overlay)
  Flask server    →  http://dndtable.local:5000      (control panel)
  MPV subprocess  →  ambient audio playback

Usage:
    python3 main.py
"""

import atexit
import os
import subprocess
import threading
import time
import logging

from flask import Flask

from config import MEDIA_DIRS, UPLOAD_DIR, DISPLAY
from media import kill_audio
from files import ensure_default_folders
from routes import register_routes
import state
import settings as settings_store
from display import apply_all_tv_settings

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
register_routes(app)

# ─── Chromium kiosk ──────────────────────────────────────────────

_chromium_proc = None


def _launch_chromium():
    """Launch Chromium in kiosk mode."""
    global _chromium_proc
    time.sleep(3)  # wait for Flask to be ready

    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY

    # Hide the X11 cursor globally
    subprocess.Popen(
        ["unclutter", "-idle", "0", "-root"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    flags = [
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--check-for-update-interval=31536000",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--enable-gpu-rasterization",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        "--disable-accelerated-video-decode",
        "--force-color-profile=srgb",
        "--disable-low-res-tiling",
        "http://localhost:5000/display",
    ]

    for browser in ("chromium-browser", "chromium"):
        try:
            _chromium_proc = subprocess.Popen(
                [browser] + flags, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logging.info("Launched %s (pid %d)", browser, _chromium_proc.pid)
            return
        except FileNotFoundError:
            continue

    logging.error("Neither chromium-browser nor chromium found!")


def _cleanup():
    """Kill Chromium and audio on exit."""
    kill_audio()
    if _chromium_proc and _chromium_proc.poll() is None:
        _chromium_proc.terminate()
        try:
            _chromium_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _chromium_proc.kill()


# ─── Entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  \u2694  DnD Table Display Server")
    print("  \u2550" * 27)
    print(f"  SD card  : {MEDIA_DIRS['sdcard']}")
    print(f"  Uploads  : {UPLOAD_DIR}")
    print(f"  Display  : Chromium kiosk → http://localhost:5000/display")
    print(f"  Control  : http://dndtable.local:5000\n")

    ensure_default_folders()

    # ─── Restore persisted settings ─────────────────────────────
    saved = settings_store.load()
    state.display_mode = saved.get("display_mode", "display")
    state.tv_color_range = saved.get("tv_color_range", "full")
    state.tv_underscan = saved.get("tv_underscan", False)
    state.tv_sharpness = saved.get("tv_sharpness", False)
    if saved.get("grid"):
        state.grid_state.update(saved["grid"])
        state.grid_state["calibration_mode"] = False  # never persist cal mode
    if saved.get("overscan"):
        state.overscan_state.update(saved["overscan"])
        state.overscan_state["calibration"] = False
    if saved.get("volumes"):
        state.video_volume = saved["volumes"].get("map", 80)
        state.audio_volume = saved["volumes"].get("ambient", 80)
        state.sfx_volume = saved["volumes"].get("sfx", 80)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    atexit.register(_cleanup)

    # Apply TV display settings if in TV mode
    if state.display_mode == "tv":
        apply_all_tv_settings(state.tv_color_range, state.tv_underscan, state.tv_sharpness)

    # Launch Chromium kiosk after Flask starts
    threading.Thread(target=_launch_chromium, daemon=True).start()

    # Run Flask (blocks)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
