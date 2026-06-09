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
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import logging
from pathlib import Path
from urllib.parse import quote

from flask import request, jsonify, render_template, Response, send_file

import state
import settings as settings_store
import scene_store
import mapinfo
from config import MEDIA_DIRS, UPLOAD_DIR, PROTECTED_FOLDERS
from media import get_file_type
from files import detect_usb_drives, get_source_roots, browse_directory
from paths import safe_resolve, is_within_any
from updater import check_for_update, apply_update
from dnd_display.scene import SceneData
import scene_import
import display_modes
import music

log = logging.getLogger(__name__)


# ─── SSE pub-sub for the native display app ──────────────────────

# Per-event-type coalescing: high-frequency UI events (overscan drag,
# grid sliders) get drained before the new one is pushed, so a slow
# consumer can't queue up dozens of intermediate states.
_COALESCING_EVENTS = frozenset({"overscan", "grid", "volume", "scene"})

_display_clients = []
_clients_lock = threading.Lock()


def _drain_coalesced(q: "queue.Queue", event_type: str) -> None:
    """Remove pending events of the same coalescing type from a client queue.

    Called under ``_clients_lock`` so the queue isn't being mutated by
    another broadcast while we filter.  Cheap: max queue size is small.
    """
    if event_type not in _COALESCING_EVENTS:
        return
    keep = []
    try:
        while True:
            keep.append(q.get_nowait())
    except queue.Empty:
        pass
    needle = f'"type": "{event_type}"'
    for item in keep:
        if needle in item:
            continue  # drop stale state of the same type
        try:
            q.put_nowait(item)
        except queue.Full:
            break


def broadcast(event_type, data=None):
    """Push an event to all connected display clients via SSE.

    For high-frequency events (overscan / grid / volume drag) we
    drain stale instances first so the consumer always lands on the
    latest value instead of replaying intermediate slider positions.
    """
    msg = json.dumps({"type": event_type, **(data or {})})
    with _clients_lock:
        dead = []
        for q in _display_clients:
            _drain_coalesced(q, event_type)
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _display_clients.remove(q)


# ─── Persistence (coalesced) ─────────────────────────────────────

_persist_timer: "threading.Timer | None" = None
_persist_lock = threading.Lock()
_PERSIST_DEBOUNCE_S = 0.5


def _persist_now():
    """Snapshot current state to disk.  Called from the debounce timer."""
    data = {
        "grid": {k: v for k, v in state.grid_state.items() if k != "calibration_mode"},
        "overscan": {k: v for k, v in state.overscan_state.items() if k != "calibration"},
        "volumes": {
            "map": state.video_volume,
        },
        "display": {
            "mode": state.display_mode_pref,
        },
        "splash": {
            "theme": state.splash_theme,
        },
    }
    settings_store.save(data)


def _persist():
    """Debounced save — coalesces bursts of slider/calibration updates
    into one disk write 500 ms after the last change."""
    global _persist_timer
    with _persist_lock:
        if _persist_timer is not None:
            _persist_timer.cancel()
        _persist_timer = threading.Timer(_PERSIST_DEBOUNCE_S, _persist_now)
        _persist_timer.daemon = True
        _persist_timer.start()


# ─── Scene persistence (per-map sidecar, coalesced) ──────────────

_scene_timer: "threading.Timer | None" = None
_scene_lock = threading.Lock()


def _persist_scene_now():
    """Write the current scene to its map's sidecar.  From the debounce timer."""
    if state.current_file_path and state.scene is not None:
        scene_store.save(state.current_file_path, state.scene)


def _persist_scene():
    """Debounced sidecar save — coalesces a burst of authoring edits into one
    disk write 500 ms after the last change."""
    global _scene_timer
    with _scene_lock:
        if _scene_timer is not None:
            _scene_timer.cancel()
        _scene_timer = threading.Timer(_PERSIST_DEBOUNCE_S, _persist_scene_now)
        _scene_timer.daemon = True
        _scene_timer.start()


# ─── Path safety ─────────────────────────────────────────────────

def _path_in_allowed_roots(p: Path) -> bool:
    """True iff ``p`` resolves to a path under a browsable source root.

    Browsable sources = the SD-card upload dir + any currently-mounted
    USB drive (as enumerated by ``files.get_source_roots()``).  The
    play / serve endpoints all defer here so a malicious POST can't
    point GStreamer / send_file at arbitrary filesystem paths.  Delegates
    to the shared :func:`paths.is_within_any` guard.
    """
    return is_within_any(get_source_roots().values(), p) is not None


def _rgb_to_hex(t):
    """Convert (r, g, b) floats in 0..1 to CSS hex string."""
    r, g, b = t
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255)))),
    )


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

    # Load this map's interactive scene (walls/vision/tokens/fog/markers) and
    # push it to the display; map dimensions feed map-pixel authoring.
    w, h = mapinfo.dimensions(filepath, file_type)
    state.map_size = [int(w), int(h)] if w and h else None
    raw = scene_store.load(filepath)
    sd = SceneData.from_payload(raw) if raw else SceneData()
    sd.map_path = str(filepath)
    if w and h:
        sd.width, sd.height = int(w), int(h)
    state.scene = sd.to_payload()
    broadcast("scene", {"scene": sd.to_display_payload()})


def _stop_display():
    """Stop display and broadcast stop event."""
    state.current_file = None
    state.current_file_path = None
    state.current_file_info = None
    state.scene = None
    state.map_size = None
    broadcast("scene", {"scene": None})
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
                "splash_theme": state.splash_theme,
                "scene": (SceneData.from_payload(state.scene).to_display_payload()
                          if state.scene else None),
                "map_size": state.map_size,
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
        if not _path_in_allowed_roots(p):
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
            grid=state.grid_state,
            volumes={"map": state.video_volume},
            file_info=state.current_file_info,
            overscan=state.overscan_state,
            splash_theme=state.splash_theme,
            map_size=state.map_size,
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
        source = data.get("source", "sdcard")
        if source != "sdcard":
            return jsonify(error="Can only create folders on SD card"), 403
        target = safe_resolve(UPLOAD_DIR, data.get("path", ""))
        if target is None:
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

        _play_on_display(dest)
        return jsonify(filename=safe_name, status="playing")

    # ─── Playback controls ───────────────────────────────────────

    @app.route("/play", methods=["POST"])
    def play():
        data = request.get_json() or {}
        filepath = Path(data.get("path", ""))
        if not filepath.exists():
            return jsonify(error="File not found"), 404
        if not _path_in_allowed_roots(filepath):
            return jsonify(error="Forbidden"), 403
        _play_on_display(filepath)
        return jsonify(status="playing", filename=filepath.name)

    @app.route("/play_folder", methods=["POST"])
    def play_folder():
        data = request.get_json() or {}
        source = data.get("source", "sdcard")
        roots = get_source_roots()
        if source not in roots:
            return jsonify(error="Source not found"), 404
        target = safe_resolve(roots[source], data.get("path", ""))
        if target is None:
            return jsonify(error="Forbidden"), 403
        if not target.is_dir():
            return jsonify(error="Not a folder"), 404
        for f in sorted(target.iterdir()):
            ftype = get_file_type(f.name) if f.is_file() else None
            if ftype:
                _play_on_display(f)
                return jsonify(status="playing", filename=f.name)
        return jsonify(error="No playable files in folder"), 404

    @app.route("/stop", methods=["POST"])
    def stop():
        _stop_display()
        return jsonify(status="stopped")

    # ─── Delete ──────────────────────────────────────────────────

    @app.route("/delete", methods=["POST"])
    def delete():
        data = request.get_json()
        # Resolve under the SD card and reject anything that escapes it — a
        # bare ``startswith`` would let ``…/dnd_media/../../home/…`` through and
        # unlink files outside the media root (the dndtable user owns its own
        # home, settings, and ssh keys).  safe_resolve collapses ``..`` first.
        target = safe_resolve(MEDIA_DIRS["sdcard"], data.get("path", ""))
        if target is None:
            return jsonify(error="Cannot delete from external media"), 403
        if target.exists() and target.is_file():
            if state.current_file == target.name:
                _stop_display()
            target.unlink()
        return jsonify(status="deleted")

    @app.route("/delete_folder", methods=["POST"])
    def delete_folder():
        data = request.get_json()
        rel_path = data.get("path", "")
        source = data.get("source", "sdcard")
        if source != "sdcard":
            return jsonify(error="Cannot delete from external media"), 403
        parts = Path(rel_path).parts
        if len(parts) == 1 and parts[0] in PROTECTED_FOLDERS:
            return jsonify(error="Cannot delete protected folder"), 403
        target = safe_resolve(UPLOAD_DIR, rel_path)
        if target is None:
            return jsonify(error="Invalid path"), 400
        if target.exists() and target.is_dir():
            shutil.rmtree(str(target))
        return jsonify(status="deleted")

    # ─── Rename (files and folders, SD card only) ────────────────

    @app.route("/rename", methods=["POST"])
    def rename():
        data = request.get_json()
        new_name = data.get("new_name", "").strip()
        if not data.get("path") or not new_name:
            return jsonify(error="Missing path or new_name"), 400
        if "/" in new_name or "\\" in new_name or ".." in new_name:
            return jsonify(error="Invalid name"), 400

        old = safe_resolve(UPLOAD_DIR, data.get("path", ""))
        if old is None:
            return jsonify(error="Can only rename on SD card"), 403
        if not old.exists():
            return jsonify(error="Not found"), 404

        rel = old.relative_to(UPLOAD_DIR.resolve())
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
        """Set the map / video output volume.  (Music volume is separate —
        see the /music/* routes.)"""
        data = request.get_json()
        target = data.get("target", "")
        level = data.get("level", 80)
        if target != "map":
            return jsonify(error="Invalid target"), 400
        level = max(0, min(100, int(level)))
        state.video_volume = level
        broadcast("volume", {"level": level})
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

    @app.route("/api/display/test", methods=["POST"])
    def api_display_test():
        """Toggle the on-screen SMPTE test pattern (display diagnostic).

        Broadcast over SSE to the native display app; ``on=false``
        restores whatever was playing before.
        """
        data = request.get_json() or {}
        on = bool(data.get("on"))
        broadcast("test_pattern", {"on": on})
        return jsonify(ok=True, on=on)

    # ─── Splash theme ────────────────────────────────────────────

    @app.route("/api/splash/themes", methods=["GET"])
    def api_splash_themes():
        """List all registered splash themes with preview colours.

        Lazy-imports `dnd_display.themes` so Flask doesn't pay the cost
        of the broader display package at startup (it has no GL deps,
        but keeping the boundary explicit makes the dependency obvious).
        """
        from dnd_display.themes import THEMES
        out = []
        for name, th in THEMES.items():
            out.append({
                "name": th.name,
                "description": th.description,
                "preview": {
                    "face_color":     _rgb_to_hex(th.face_color),
                    "face_color2":    _rgb_to_hex(th.face_color2),
                    "rune_color":     _rgb_to_hex(th.rune_color),
                    "rune_color2":    _rgb_to_hex(th.rune_color2),
                    "rim_color":      _rgb_to_hex(th.rim_color),
                    "backdrop_inner": _rgb_to_hex(th.backdrop_inner),
                    "backdrop_outer": _rgb_to_hex(th.backdrop_outer),
                },
            })
        return jsonify(themes=out, current=state.splash_theme)

    @app.route("/api/splash/theme", methods=["POST"])
    def api_splash_theme_set():
        """Switch the active splash theme.  Persisted to settings.json
        and broadcast over SSE so the native display app updates live."""
        from dnd_display.themes import THEMES
        data = request.get_json() or {}
        name = data.get("theme", "")
        if name not in THEMES:
            return jsonify(ok=False, error=f"unknown theme: {name}"), 400
        state.splash_theme = name
        broadcast("splash_theme", {"theme": name})
        _persist()
        return jsonify(ok=True, theme=name)

    # ─── Music output (headless client proxy) ────────────────────

    @app.route("/music/status")
    def music_status():
        """Proxy the local music-output client's control surface."""
        return jsonify(music.status())

    @app.route("/music/power", methods=["POST"])
    def music_power():
        """Turn the table's music output on/off."""
        data = request.get_json() or {}
        return jsonify(music.set_power(bool(data.get("on"))))

    @app.route("/music/volume", methods=["POST"])
    def music_volume():
        """Set the music output volume (panel sends 0–100; client wants 0–1)."""
        data = request.get_json() or {}
        level = max(0, min(100, int(data.get("level", 0))))
        return jsonify(music.set_volume(level / 100.0))

    # ─── Scene (VTT overlay: walls / vision / tokens / fog / markers) ──

    @app.route("/scene", methods=["GET"])
    def scene_get():
        """Full authoring scene for the current map (includes GM-only hidden
        tokens/markers).  ``map_size`` lets the panel author in map pixels."""
        return jsonify(scene=state.scene or {}, map_size=state.map_size)

    @app.route("/scene", methods=["PUT"])
    def scene_put():
        """Replace the current map's scene.  Normalises via SceneData, stores
        it, broadcasts the player-safe payload to the display, and persists to
        the map's sidecar (debounced)."""
        if not state.current_file_path:
            return jsonify(error="no map loaded"), 409
        data = request.get_json() or {}
        sd = SceneData.from_payload(data.get("scene") or data)
        sd.map_path = str(state.current_file_path)
        if state.map_size:
            sd.width, sd.height = int(state.map_size[0]), int(state.map_size[1])
        state.scene = sd.to_payload()
        broadcast("scene", {"scene": sd.to_display_payload()})
        _persist_scene()
        return jsonify(scene=state.scene)

    @app.route("/scene/still")
    def scene_still():
        """A still backdrop of the current map for the authoring canvas — the
        image file itself, or one extracted frame for a video map."""
        if not state.current_file_path:
            return jsonify(error="no map loaded"), 404
        p = Path(state.current_file_path)
        ftype = get_file_type(p.name)
        if ftype == "image":
            return send_file(str(p), conditional=True)
        if ftype == "video":
            out = os.path.join(tempfile.gettempdir(), "dnd-map-still.png")
            if mapinfo.extract_still(p, out):
                return send_file(out, mimetype="image/png")
            return jsonify(error="could not extract frame"), 500
        return jsonify(error="unsupported map type"), 400

    @app.route("/scene/screenshot")
    def scene_screenshot():
        """Grab what's actually on the TV right now (via grim) — the GM's
        'what the players see' preview, with fog/vision baked in."""
        out = os.path.join(tempfile.gettempdir(), "dnd-grim.png")
        env = os.environ.copy()
        env.update(display_modes._wayland_env())
        try:
            r = subprocess.run(["grim", out], capture_output=True, text=True,
                               timeout=5, env=env)
        except FileNotFoundError:
            return jsonify(error="grim not installed"), 500
        except subprocess.TimeoutExpired:
            return jsonify(error="grim timed out"), 500
        if r.returncode != 0 or not os.path.exists(out):
            return jsonify(error=r.stderr.strip() or "grim failed"), 500
        return send_file(out, mimetype="image/png")

    @app.route("/scene/token-image", methods=["POST"])
    def scene_token_image():
        """Upload token art (PNG) for an image token.  Saved under the SD
        card's Tokens/ folder; returns the path (the token's ``image_ref``)
        and a serve URL (for the panel to preview)."""
        if "file" not in request.files:
            return jsonify(error="No file"), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify(error="No filename"), 400
        if get_file_type(f.filename) != "image":
            return jsonify(error="Token art must be an image"), 400
        dest_dir = UPLOAD_DIR / "Tokens"
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = f.filename.replace("/", "_").replace("\\", "_")
        dest = dest_dir / safe
        f.save(str(dest))
        return jsonify(path=str(dest),
                       url="/serve_media?path=" + quote(str(dest), safe=""))

    @app.route("/scene/import", methods=["POST"])
    def scene_import_route():
        """Import a Universal VTT map (``.dd2vtt`` / ``.uvtt`` / ``.df2vtt``):
        decode its embedded image into the SD card's ``Maps/`` folder,
        translate walls / doors / lights into the map's ``.scene.json``
        sidecar, and start playing it.

        This is the *only* entry point to the import engine — ``/upload``
        still rejects these files (they aren't ``ALLOWED_EXTENSIONS`` media).
        The heavy lifting (path-safe image write, geometry transform) lives in
        the pure ``scene_import`` module; this route is just upload plumbing.
        """
        if "file" not in request.files:
            return jsonify(error="No file"), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify(error="No filename"), 400
        if not scene_import.is_vtt_filename(f.filename):
            return jsonify(error="Not a VTT file (.dd2vtt/.uvtt/.df2vtt)"), 400

        maps_dir = UPLOAD_DIR / "Maps"
        image_name = scene_import.derive_image_name(f.filename)

        # The importer parses from a path, so spool the upload to a temp file
        # first; remove it once we've decoded the image + geometry out of it.
        fd, tmp = tempfile.mkstemp(suffix=Path(f.filename).suffix)
        os.close(fd)
        try:
            f.save(tmp)
            dest, sd = scene_import.import_vtt(tmp, maps_dir, image_name)
        except scene_import.SceneImportError as e:
            return jsonify(error=str(e)), 400
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        # Persist the translated scene to the map's sidecar *before* playing:
        # _play_on_display reloads the sidecar and re-sizes it against the
        # saved PNG, so walls/doors/lights come straight through.
        scene_store.save(dest, sd.to_payload())
        _play_on_display(dest)
        return jsonify(status="playing", filename=dest.name, path=str(dest))

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
