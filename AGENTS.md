# AGENTS.md

Guidance for working in this repo. Prose docs (install, usage, troubleshooting) live in [README.md](README.md); this file is the mental model, the invariants, and the gotchas.

## What this is

A TV in a D&D table driven by an x86 Debian box. **Two processes, one box:**

- **Flask control plane** (`main.py`, port 5000) — owns *all* state and persistence, serves the phone/tablet panel (`templates/control.html`), exposes a REST API + an SSE stream.
- **Native display app** (`dnd_display/`, pyglet + moderngl) — owns the GL context, subscribes to the SSE stream, renders. Launched by cage via `kiosk.sh`.

They communicate **only** over SSE (`/display/stream`). Flask never renders; the display app never owns state. `state.py` is the single source of truth, imported everywhere (no circular deps).

## Commands

```bash
# Flask control plane (dev) — http://localhost:5000, panel works without the display app
python main.py

# Native display app, windowed (needs Wayland + GL 3.3)
DND_WINDOWED=1 python -m dnd_display

# Tests — pure, no Flask/pyglet/GStreamer needed; runs on any OS
pip install -r requirements-dev.txt && pytest

# Syntax gate (what CI runs first; never imports, so it covers the GL code too)
python -m compileall -q .
```

The table **self-updates**: the panel's updater does `git pull` on the repo clone, `rsync`s to `/opt/dnd-table`, and restarts the service. A broken commit on `main` ships straight to hardware — CI (`.github/workflows/ci.yml`) gates `compileall` + `pytest` to catch the cheap breakage.

## Three directories, don't confuse them

| Path | What | Git? |
|---|---|---|
| repo clone (e.g. `~/dnd-table`) | source of truth for updates | yes |
| `/opt/dnd-table` | deployed copy the services run | **no** (plain rsync target) |
| `/home/dndtable/dnd-display/settings.json` | persisted user settings | excluded from rsync/updates |

## Invariants — break these and you ship a bug

- **Path safety.** Every client-supplied path goes through `paths.safe_resolve(root, value)` / `is_within_any(roots, value)`. **Never** hand-roll `".." in p` or `str(p).startswith(root)` checks — a `startswith` is exactly how `/delete` once allowed `…/dnd_media/../../home/…` (a prefix check accepts the sibling `dnd_media_evil` too). The guard resolves `..`/symlinks *then* checks containment. Tested in `tests/test_paths.py`.
- **GM secrets never reach the table.** Anything sent to the display goes through `SceneData.to_display_payload()`, which strips `hidden` tokens and markers. Never broadcast raw `to_payload()` on the `scene` SSE event — a Player-View screenshot would leak hidden traps/monsters. Tested in `tests/test_scene.py`.
- **No GStreamer launch strings.** Build pipelines element-by-element via `Gst.ElementFactory.make` and set paths as properties (`dnd_display/gst_pipeline.py`). Never `Gst.parse_launch` with a client string — embedded `!`/quotes would inject elements.
- **GL is single-threaded.** The SSE subscriber runs on a background thread and marshals every state change onto the main thread via `pyglet.clock.schedule_once`. Don't touch GL/pyglet off the main thread.
- **No local audio.** The table plays no sound itself; map-video audio is intentionally not wired into the pipeline. Music is a *separate* headless client (pjunak/music) that Flask only proxies (`music.py`). Don't add audio decode/output here.

## Conventions & gotchas

- **Geometry is always map-image pixels** in the scene model (`dnd_display/scene.py`) — resolution-independent. `dnd_display/transform.py` (`MapTransform`) is the one place that maps map-px → screen; reuse it.
- **Two different grids.** `scene.grid` (the map's own grid, map px) vs `state.grid_state` (the physical table grid, screen px). Never conflate them.
- **SSE coalescing.** High-frequency events (`overscan`/`grid`/`volume`/`scene`) are coalesced in `broadcast()` so a slow display can't replay intermediate slider positions. Persistence is debounced 500 ms.
- **Scene parsing is tolerant by design** — missing keys default, unknown keys ignored (so the format can evolve and importers stay lax). Keep it that way; don't add strict validation that rejects partial payloads.
- **VTT import path.** `dnd_display/importers/` (UVTT/`.dd2vtt` → `SceneData`) is the engine; `scene_import.py` is the pure glue (decode embedded image → `Maps/` via `safe_resolve`, build `SceneData`, set `map_path`), and `POST /scene/import` is the only route that reaches it. `/upload` still rejects `.dd2vtt` (it's a scene, not playable media) — the panel routes those extensions to `/scene/import` instead. A new foreign format = a new adapter in `importers/`; nothing else changes.
- **Subprocess wrappers degrade, never crash** — `ffprobe`/`ffmpeg`/`wlr-randr`/`grim`/`git` calls are best-effort with timeouts and logged failures. Match that pattern.
- Module docstrings are heavy here by convention — explain *why*, not just *what*. Match the surrounding density.

## Control panel (`templates/control.html`)

Single file, no build step, ES5-style vanilla JS (`var`, no modules, inline `onclick`) — match that idiom. The panel **polls** `/status` every 5 s and POSTs JSON to Flask; `:active`/`matches(':active')` guards stop the poll from overwriting a slider the user is dragging.

**Design system:** colors and the radius scale are CSS custom properties in `:root` (`--gold`, `--surface`, `--surface-active`, `--text-dim`, `--r-sm…--r-xl`, semantic `--red/green/blue/purple`). Prefer a token over a raw hex; use `.note` for secondary text. **Known divergence:** the scene canvas draws with its own literal color palette in JS (it can't read CSS vars cheaply per frame) — that's the one sanctioned place for literal colors.

## Testing conventions

Keep the suite **pure** (no Flask/pyglet/`gi` imports) so it runs anywhere with just `pytest`. New logic belongs in a pure module (like `paths.py`) with a unit test, not buried in a route or a GL layer. `pytest.ini` puts the repo root on `sys.path`.
