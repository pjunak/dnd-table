"""
DnD Table – Flask routes + SSE bridge.

Control plane only. Renders the phone/tablet control panel and exposes a
Server-Sent Events stream that the native `dnd_display` Wayland app
subscribes to. The display app receives state changes (current_file,
grid, overscan/safe-area, volume) and renders the actual scene.

This module is intentionally free of display-side rendering concerns —
no template for the kiosk page, no X11/xrandr, no RPi config.txt.
"""

import json
import queue
import shutil
import subprocess
import threading
import logging
from pathlib import Path
from urllib.parse import quote

from flask import request, jsonify, render_template, Response, send_file

import state
import settings as settings_store
from config import MEDIA_DIRS, UPLOAD_DIR, PROTECTED_FOLDERS
from media import get_file_type, kill_audio, play_audio, set_audio_volume
from files import detect_usb_drives, get_source_roots, browse_directory
from updater import check_for_update, apply_update
import display_modes

log = logging.getLogger(__name__)


# ─── SSE pub-sub for the native display app ──────────────────────

_display_clients = []
_clients_lock = threading.Lock()


def broadcast(event_type, data=None):
    """Push an event to all connected display clients via SSE."""
    msg = json.dumps({"type": event_type, **(data or {})})
    with _clients_lock:
        dead = []
        for q in _display_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _display_clients.remove(q)


def _persist():
    """Save current persistent state to disk."""
    data = {
        "grid": {k: v for k, v in state.grid_state.items() if k != "calibration_mode"},
        "overscan": {k: v for k, v in state.overscan_state.items() if k != "calibration"},
        "volumes": {
            "map": state.video_volume,
            "ambient": state.audio_volume,
            "sfx": state.sfx_volume,
        },
        "display": {
            "mode": state.display_mode_pref,
        },
    }
    settings_store.save(data)


# ─── Display helpers ─────────────────────────────────────────────

def _play_on_display(filepath):
    """Set state and broadcast play event to the display app."""
    filepath = Path(filepath)
    file_type = get_file_type(filepath.name)
    state.current_file = filepath.name
    state.current_file_path = str(filepath)

    # Compute file info
    size_bytes = filepath.stat().st_size
    if size_bytes >= 1_000_000:
        human_size = f"{size_bytes / 1_000_000:.1f} MB"
    elif size_bytes >= 1_000:
        human_size = f"{size_bytes / 1_000:.1f} KB"
    else:
        human_size = f"{size_bytes} B"

    duration = None
    if file_type == "video":
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'csv=p=0', str(filepath)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                total_secs = int(float(result.stdout.strip()))
                minutes, secs = divmod(total_secs, 60)
                duration = f"{minutes}:{secs:02d}"
        except Exception:
            pass

    state.current_file_info = {
        "size": human_size,
        "type": file_type,
        "duration": duration,
    }

    media_url = "/serve_media?path=" + quote(str(filepath), safe="")
    broadcast("play", {
        "url": media_url,
        "path": str(filepath),
        "file_type": file_type,
        "filename": filepath.name,
    })


def _stop_display():
    """Stop display and broadcast stop event."""
    state.current_file = None
    state.current_file_path = None
    state.current_file_info = None
    broadcast("stop")


# ─── Route registration ─────────────────────────────────────────

def register_routes(app):
    """Attach all routes to *app*."""

    # ─── SSE stream consumed by the native display app ───────────

    @app.route("/display/stream")
    def display_stream():
        """SSE endpoint — the native display app connects here on launch."""
        q = queue.Queue(maxsize=50)
        with _clients_lock:
            _display_clients.append(q)

        def generate():
            # Send current state on connect so the display can sync immediately
            init = json.dumps({
                "type": "init",
                "grid": state.grid_state,
                "overscan": state.overscan_state,
                "volume": state.video_volume,
                "file": state.current_file,
                "file_path": state.current_file_path,
                "file_type": get_file_type(state.current_file) if state.current_file else None,
            })
            yield f"data: {init}\n\n"
            try:
                while True:
                    try:
                        msg = q.get(timeout=25)
                        yield f"data: {msg}\n\n"
                    except queue.Empty:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                pass
            finally:
                with _clients_lock:
                    if q in _display_clients:
                        _display_clients.remove(q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/serve_media")
    def serve_media():
        """Serve media files (with Range support) to the native display app
        and the control panel preview thumbnails."""
        filepath = request.args.get("path", "")
        p = Path(filepath)
        if not p.exists() or not p.is_file():
            return "Not found", 404
        allowed = False
        for d in MEDIA_DIRS.values():
            try:
                p.resolve().relative_to(d.resolve())
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            return "Forbidden", 403
        return send_file(str(p), conditional=True)

    # ─── Control panel ───────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("control.html")

    @app.route("/status")
    def status_route():
        return jsonify(
            current=state.current_file,
            current_audio=state.current_audio,
            grid=state.grid_state,
            volumes={
                "map": state.video_volume,
                "ambient": state.audio_volume,
                "sfx": state.sfx_volume,
            },
            file_info=state.current_file_info,
            overscan=state.overscan_state,
        )

    @app.route("/sources")
    def sources():
        result = []
        if MEDIA_DIRS["sdcard"].exists():
            result.append({"id": "sdcard", "label": "SD Card"})
        for usb in detect_usb_drives():
            result.append({"id": "usb:" + usb.name, "label": "USB: " + usb.name})
        return jsonify(sources=result)

    # ─── Browsing ────────────────────────────────────────────────

    @app.route("/browse")
    def browse():
        source = request.args.get("source", "sdcard")
        rel_path = request.args.get("path", "")
        data = browse_directory(source, rel_path)
        if data is None:
            return jsonify(error="Invalid path"), 400
        return jsonify(**data)

    # ─── Folder / file management (SD card only) ─────────────────

    @app.route("/mkdir", methods=["POST"])
    def mkdir():
        data = request.get_json()
        rel_path = data.get("path", "")
        source = data.get("source", "sdcard")
        if source != "sdcard":
            return jsonify(error="Can only create folders on SD card"), 403
        if ".." in rel_path or rel_path.startswith("/"):
            return jsonify(error="Invalid path"), 400
        target = UPLOAD_DIR / rel_path
        try:
            target.resolve().relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return jsonify(error="Invalid path"), 400
        target.mkdir(parents=True, exist_ok=True)
        return jsonify(status="created")

    @app.route("/upload", methods=["POST"])
    def upload():
        if "file" not in request.files:
            return jsonify(error="No file"), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify(error="No filename"), 400
        ftype = get_file_type(f.filename)
        if not ftype:
            return jsonify(error="Unsupported file type"), 400

        folder = request.form.get("folder", "")
        if ".." in folder or folder.startswith("/"):
            return jsonify(error="Invalid folder"), 400

        safe_name = f.filename.replace("/", "_").replace("\\", "_")
        dest_dir = UPLOAD_DIR / folder if folder else UPLOAD_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        f.save(str(dest))

        if ftype == "audio":
            play_audio(dest)
        else:
            _play_on_display(dest)
        return jsonify(filename=safe_name, status="playing")

    # ─── Playback controls ───────────────────────────────────────

    @app.route("/play", methods=["POST"])
    def play():
        data = request.get_json()
        filepath = Path(data.get("path", ""))
        if not filepath.exists():
            return jsonify(error="File not found"), 404
        _play_on_display(filepath)
        return jsonify(status="playing", filename=filepath.name)

    @app.route("/play_audio", methods=["POST"])
    def play_audio_route():
        data = request.get_json()
        filepath = Path(data.get("path", ""))
        if not filepath.exists():
            return jsonify(error="File not found"), 404
        play_audio(filepath)
        return jsonify(status="playing", filename=filepath.name)

    @app.route("/play_folder", methods=["POST"])
    def play_folder():
        data = request.get_json()
        rel_path = data.get("path", "")
        source = data.get("source", "sdcard")
        roots = get_source_roots()
        if source not in roots:
            return jsonify(error="Source not found"), 404
        target = roots[source] / rel_path
        if not target.is_dir():
            return jsonify(error="Not a folder"), 404
        for f in sorted(target.iterdir()):
            ftype = get_file_type(f.name) if f.is_file() else None
            if ftype:
                if ftype == "audio":
                    play_audio(f)
                else:
                    _play_on_display(f)
                return jsonify(status="playing", filename=f.name)
        return jsonify(error="No playable files in folder"), 404

    @app.route("/stop", methods=["POST"])
    def stop():
        _stop_display()
        return jsonify(status="stopped")

    @app.route("/stop_audio", methods=["POST"])
    def stop_audio_route():
        kill_audio()
        state.current_audio = None
        return jsonify(status="stopped")

    # ─── Delete ──────────────────────────────────────────────────

    @app.route("/delete", methods=["POST"])
    def delete():
        data = request.get_json()
        filepath = Path(data.get("path", ""))
        if not str(filepath).startswith(str(MEDIA_DIRS["sdcard"])):
            return jsonify(error="Cannot delete from external media"), 403
        if filepath.exists():
            if state.current_file == filepath.name:
                _stop_display()
            if state.current_audio == filepath.name:
                kill_audio()
                state.current_audio = None
            filepath.unlink()
        return jsonify(status="deleted")

    @app.route("/delete_folder", methods=["POST"])
    def delete_folder():
        data = request.get_json()
        rel_path = data.get("path", "")
        source = data.get("source", "sdcard")
        if source != "sdcard":
            return jsonify(error="Cannot delete from external media"), 403
        if ".." in rel_path or rel_path.startswith("/"):
            return jsonify(error="Invalid path"), 400
        parts = Path(rel_path).parts
        if len(parts) == 1 and parts[0] in PROTECTED_FOLDERS:
            return jsonify(error="Cannot delete protected folder"), 403
        target = UPLOAD_DIR / rel_path
        try:
            target.resolve().relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return jsonify(error="Invalid path"), 400
        if target.exists() and target.is_dir():
            shutil.rmtree(str(target))
        return jsonify(status="deleted")

    # ─── Rename (files and folders, SD card only) ────────────────

    @app.route("/rename", methods=["POST"])
    def rename():
        data = request.get_json()
        old_path = data.get("path", "")
        new_name = data.get("new_name", "").strip()
        if not old_path or not new_name:
            return jsonify(error="Missing path or new_name"), 400
        if "/" in new_name or "\\" in new_name or ".." in new_name:
            return jsonify(error="Invalid name"), 400

        old = Path(old_path)
        if not str(old).startswith(str(MEDIA_DIRS["sdcard"])):
            return jsonify(error="Can only rename on SD card"), 403
        if not old.exists():
            return jsonify(error="Not found"), 404

        try:
            rel = old.resolve().relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return jsonify(error="Invalid path"), 400
        if len(rel.parts) == 1 and rel.parts[0] in PROTECTED_FOLDERS:
            return jsonify(error="Cannot rename protected folder"), 403

        new = old.parent / new_name
        if new.exists():
            return jsonify(error="Name already exists"), 409
        old.rename(new)
        return jsonify(status="renamed", new_name=new_name)

    # ─── Volume control ──────────────────────────────────────────

    @app.route("/volume", methods=["POST"])
    def volume():
        data = request.get_json()
        target = data.get("target", "")
        level = data.get("level", 80)
        if target not in ("map", "ambient", "sfx"):
            return jsonify(error="Invalid target"), 400
        level = max(0, min(100, int(level)))
        if target == "map":
            state.video_volume = level
            broadcast("volume", {"level": level})
        elif target == "ambient":
            state.audio_volume = level
            set_audio_volume(level)
        elif target == "sfx":
            state.sfx_volume = level
        _persist()
        return jsonify(status="ok", target=target, level=level)

    # ─── Safe-area / letterbox inset ─────────────────────────────

    @app.route("/overscan", methods=["POST"])
    def overscan():
        data = request.get_json()
        valid_keys = {"top", "bottom", "left", "right", "calibration"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        state.overscan_state.update(filtered)
        broadcast("overscan", {"overscan": state.overscan_state})
        if not state.overscan_state.get("calibration"):
            _persist()
        return jsonify(overscan=state.overscan_state)

    # ─── Grid overlay ────────────────────────────────────────────

    @app.route("/grid", methods=["POST"])
    def grid():
        data = request.get_json()
        valid_keys = {
            "enabled", "type", "size", "thickness", "opacity",
            "color", "offset_x", "offset_y", "ppi", "calibration_mode",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        state.grid_state.update(filtered)
        broadcast("grid", {"grid": state.grid_state})
        if not state.grid_state.get("calibration_mode"):
            _persist()
        return jsonify(grid=state.grid_state)

    # ─── Software update ─────────────────────────────────────────────

    @app.route("/update/check", methods=["POST"])
    def update_check():
        """Check GitHub for available updates."""
        return jsonify(check_for_update())

    @app.route("/update/apply", methods=["POST"])
    def update_apply():
        """Pull latest code, deploy, and restart the service."""
        result = apply_update()
        if result.get("ok"):
            subprocess.Popen(["sudo", "systemctl", "restart", "dnd-table.service"])
        return jsonify(result)

    @app.route("/system", methods=["POST"])
    def system_action():
        data = request.get_json()
        action = data.get("action")
        if action == "restart":
            subprocess.Popen(["sudo", "systemctl", "restart", "dnd-table.service"])
        elif action == "reboot":
            subprocess.Popen(["sudo", "reboot"])
        elif action == "shutdown":
            subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        else:
            return jsonify(error="unknown action"), 400
        return jsonify(ok=True)

    # ─── Display output (resolution) ─────────────────────────────

    @app.route("/api/display/modes", methods=["GET"])
    def display_modes_list():
        """Return available output modes and the currently-active one."""
        data = display_modes.get_state()
        data["preferred"] = state.display_mode_pref
        return jsonify(data)

    @app.route("/api/display/mode", methods=["POST"])
    def display_mode_set():
        """Set the output mode, persist it, and restart greetd so the
        native app re-creates its window at the new size."""
        data = request.get_json() or {}
        mode = data.get("mode", "")
        if not mode:
            return jsonify(error="mode required"), 400
        ok, err = display_modes.set_mode(mode)
        if not ok:
            return jsonify(ok=False, error=err), 400
        state.display_mode_pref = mode
        _persist()
        # Bounce greetd so pyglet re-creates its window at the new size.
        # Without this, the existing window stays at the old dimensions
        # while cage's framebuffer is at the new resolution.
        subprocess.Popen(["sudo", "systemctl", "restart", "greetd.service"])
        return jsonify(ok=True, mode=mode)

    @app.route("/api/ips")
    def api_ips():
        """Return current IPv4 addresses of this machine."""
        try:
            result = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=2
            )
            ips = [ip for ip in result.stdout.strip().split() if ":" not in ip]
        except Exception:
            ips = []
        return jsonify(ips=ips)
