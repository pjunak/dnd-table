"""
DnD Table – Media helpers.

Audio (ambient) playback via MPV subprocess.
Video and image display is handled by the Chromium display page.
"""

import logging
import os
import socket
import json
import subprocess

import state
from config import ALLOWED_EXTENSIONS, MPV_AUDIO_SOCKET

log = logging.getLogger(__name__)


# ─── File type detection ─────────────────────────────────────────

def get_file_type(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ALLOWED_EXTENSIONS["video"]:
        return "video"
    if ext in ALLOWED_EXTENSIONS["image"]:
        return "image"
    if ext in ALLOWED_EXTENSIONS["audio"]:
        return "audio"
    return None


# ─── Process control ─────────────────────────────────────────────

def _terminate(proc):
    """Gracefully terminate a subprocess, escalating to kill if needed."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _cleanup_sock(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ─── Audio playback (ambient) ────────────────────────────────────

def kill_audio():
    """Kill the ambient audio MPV process."""
    _terminate(state.audio_process)
    state.audio_process = None
    _cleanup_sock(MPV_AUDIO_SOCKET)


def play_audio(filepath):
    """Play audio file in a headless MPV instance (no video)."""
    kill_audio()

    cmd = [
        "mpv",
        "--no-video",
        "--loop-file=inf",
        "--ao=alsa,pulse",
        f"--input-ipc-server={MPV_AUDIO_SOCKET}",
        f"--volume={int(state.audio_volume)}",
        str(filepath),
    ]

    try:
        state.audio_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.error("mpv binary not found — is mpv installed?")
        return
    except OSError as e:
        log.error("Failed to launch mpv audio: %s", e)
        return

    state.current_audio = os.path.basename(str(filepath))


# ─── MPV IPC (volume control for ambient audio) ──────────────────

def _mpv_command(sock_path, prop, val=None):
    """Send JSON command to MPV IPC Unix socket."""
    if not os.path.exists(sock_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(sock_path)
        if val is not None:
            cmd = {"command": ["set_property", prop, val]}
        else:
            cmd = {"command": [prop]}
        s.sendall((json.dumps(cmd) + "\n").encode())
        s.close()
        return True
    except (OSError, socket.error) as e:
        log.debug("IPC command failed on %s: %s", sock_path, e)
        return False


def set_audio_volume(percent):
    """Set ambient audio volume (0-100)."""
    percent = max(0, min(100, int(percent)))
    state.audio_volume = percent
    return _mpv_command(MPV_AUDIO_SOCKET, "volume", percent)
