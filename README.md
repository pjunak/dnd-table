# DnD Table

A TV mounted in a D&D gaming table, driven by a small x86 PC running Debian. The Game Master controls maps, ambience, fog of war, vision, and tokens from a phone or tablet on the same network; the table itself shows a slowly-rotating procedural D20 splash when nothing else is playing.

![status: works on my table](https://img.shields.io/badge/status-works%20on%20my%20table-c9a84c)

## What it does

- Plays map images / videos fullscreen with aspect-correct letterboxing and looping
- Overlays a calibrated square or pointy-top hex grid (DPI calibration in the panel)
- **Battle-map overlays** — fog of war with dynamic line-of-sight vision (walls block sight), token position tracking (disc or image tokens), and trap / environment markers, all authored on a still of the map from the panel
- Acts as a remote audio output for [pjunak/music](https://github.com/pjunak/music): plays whatever's on the music server through the table's speakers, with on/off · volume · mute from the panel
- Animates a 3D D20 splash when idle, with 5 selectable themes (arcane, flame, storm, ancient, verdant) and Elder Futhark runes
- Self-updates from GitHub via the panel ("Check for Updates" → "Update & Restart")

## Architecture

```
   Phone / tablet                            TV (under the table)
       │                                          ▲
       │ HTTP (port 80 → 5000)                    │ HDMI
       ▼                                          │
┌──────────────────────────────┐         ┌────────┴───────────────┐
│ Flask control plane          │   SSE   │ dnd_display            │
│  - REST API                  │ ──────▶ │  - pyglet + moderngl   │
│  - control.html              │         │  - GStreamer → texture │
│  - SSE on /display/stream    │         │  - layer compositor    │
│  systemd: dnd-table.service  │         │ launched by cage via   │
└──────────────────────────────┘         │ kiosk.sh, greetd boots │
                                         │ straight into it       │
                                         └────────────────────────┘
```

Two processes, one box. Flask owns state and persistence; the native display app subscribes to SSE and renders. No Chromium, no X11, no `/boot/firmware/config.txt`.

## Hardware

- Any x86 mini-PC with VA-API capable graphics (tested on Intel HD Graphics 400 / Braswell)
- HDMI to a TV; audio over PipeWire
- USB-A ports for thumb drives (auto-detected, browsable from the panel)

## Installation

Fresh Debian 13 (Trixie) install. Add a `dndtable` user. Then:

```bash
ssh dndtable@<box>
sudo apt install -y git
git clone https://github.com/pjunak/dnd-table ~/dnd-table
cd ~/dnd-table
bash install.sh
sudo reboot
```

`install.sh` is idempotent — re-run it any time you suspect drift. It:

1. Enables `contrib` + `non-free` (for `i965-va-driver-shaders`)
2. Installs apt packages: Flask, GStreamer + VA-API, mpv + libmpv + python3-mpv + python3-websocket (music output), greetd + cage + xwayland, PipeWire, Pillow, etc.
3. Creates `/opt/dnd-table/.venv` with `--system-site-packages` and pip-installs `requirements.txt` (moderngl, pyglet, sseclient-py)
4. Adds `dndtable` to `video`, `render`, `input`, `audio` groups
5. Writes a NOPASSWD sudoers rule for the specific commands Flask invokes
6. Swaps `/etc/greetd/config.toml` to autologin `dndtable` into `cage /opt/dnd-table/kiosk.sh`
7. Disables `getty@tty1` so greetd owns the console
8. Installs and enables `dnd-table.service`
9. Installs the headless music-output client to `/opt/music-output` + `music-output.service`, with `/etc/music-output.env` pointed at `music.junak.eu`

`uninstall.sh` reverses all of the above except the apt packages themselves.

## Using it

The control panel auto-loads at `http://dndtable.local` from any device on the same network (port 80 redirects to Flask on 5000).

- **Table tab** — the live map canvas: choose a Map, author fog / walls / tokens / markers on a still of it, control the Music output, and toggle the grid
- **Library tab** — upload / rename / delete files on the SD card; browse USB drives
- **Styles tab** — pick a splash theme; preview swatches show face / rune / rim colors over the theme's actual backdrop
- **Settings tab** — pick display mode, run a display test pattern, calibrate safe area + grid DPI, see the device's network addresses, check for updates, reboot/shutdown

### Keys at the table (USB keyboard)

| Key | What it does |
|-----|--------------|
| `T` | Cycle splash themes |
| `Esc` / `Q` | Exit the display app (greetd will restart it) |

## Battle maps — fog, vision, tokens & markers

The **Table tab** is a map-authoring canvas. Because a map is a static image or a looping video, the panel works on a *still* of it (`/scene/still` — the image itself, or one extracted video frame) while the table renders the live overlays. Everything is saved per-map in a `<map>.scene.json` sidecar and pushed to the display over SSE; geometry is stored in **map-image pixels**, so it stays aligned under any resolution or letterboxing.

From the tool palette you can:

- **Tokens** — place / drag / delete; a token is a coloured disc with a label, or an uploaded image. Mark some as **party** (they drive the shared reveal) and give them a **vision** range.
- **Walls** & **doors** — draw sight-blocking lines (doors can be open/closed). They feed the vision engine but aren't drawn on the table.
- **Fog of war** — toggle it on, then either let **dynamic vision** compute what the party sees (walls cast shadows; areas no party token can see stay dark), or switch the fog tool to **manual** and drag-reveal regions.
- **Markers** — traps / hazards / difficult terrain / notes. Hidden markers are GM-only and never reach the table.
- **Player View** (👁) grabs a screenshot of the actual TV so you can confirm what the players see.

### Importing maps from other VTTs

Scenes use one canonical model (`dnd_display/scene.py`) that mirrors the **Universal VTT** schema (walls = line-of-sight, doors = portals, lights, grid). Importers live in `dnd_display/importers/` behind a single `SceneImporter` interface — a UVTT / `.dd2vtt` adapter ships today; Foundry, Roll20, or a custom format are each just another adapter, with nothing else in the engine changing.

## Music output

The table is a headless **audio output** for [pjunak/music](https://github.com/pjunak/music). A small guest client (`music_output.py`, from that repo's `clients/headless/`) runs as `music-output.service`, follows the server's playback over a WebSocket, and plays it through mpv → PipeWire. You queue and control tracks from the music server's own web UI (`music.junak.eu`); the DnD panel's **Music** card drives only *this output*: on/off, volume, mute, and a connection indicator.

Config lives in `/etc/music-output.env`:

```
MUSIC_SERVER_URL=https://music.junak.eu   # the music server
MUSIC_OUTPUT_NAME=DnD Table               # shown in the server's Outputs picker
MUSIC_CONTROL_PORT=8731                   # localhost control surface Flask proxies
```

The panel talks to the client through Flask (`/music/*` → `127.0.0.1:8731/control`), so the control port never has to leave the box. Point the table at a different server by editing `MUSIC_SERVER_URL` and `sudo systemctl restart music-output`.

## Updating

From the panel: **Settings → Software Update → Check for Updates → Update & Restart**. The updater:

1. `git pull` the local clone
2. `rsync` to `/opt/dnd-table` (excluding `.venv`, `*.png`, `settings.json`)
3. Recreate the venv if it's missing, `pip install -r requirements.txt`
4. Re-fetch the music-output client and restart `music-output.service`
5. Restart `dnd-table.service`

The SSE bridge auto-reconnects within seconds of Flask coming back; the kiosk window doesn't go away.

## Development

You can run the display app windowed on a workstation:

```bash
DND_WINDOWED=1 python -m dnd_display
```

It still wants a Wayland session and a working GL 3.3 context. To run just the Flask side somewhere portable, point your browser at `http://localhost:5000` — the control panel works without the native app (you just won't see anything on a TV).

### Key files

- `main.py` — Flask entry; reads `settings.json`, registers routes
- `routes.py` — REST + SSE; **all path-touching endpoints validate against an allowlist** (`_path_in_allowed_roots`)
- `music.py` — proxies the local headless music-output client's control surface (the panel's `/music/*` routes)
- `dnd_display/app.py` — pyglet window + SSE subscriber + dispatcher
- `dnd_display/scene.py` — canonical scene model (walls / doors / tokens / fog / markers, in map pixels); `importers/` translate other VTT formats into it
- `dnd_display/vision.py` — pure visibility-polygon engine (angular sweep), unit-tested in `tests/`; `transform.py` is the one map-pixel→screen transform shared by the video + overlay layers
- `dnd_display/layers/{tokens,markers,fog}.py` — battle-map overlay layers (fog/vision composited above tokens)
- `dnd_display/compositor.py` — layer stack + safe-area inset viewport
- `dnd_display/gst_pipeline.py` — GStreamer pipelines built via `ElementFactory` (paths are passed as properties, never interpolated into a launch string)
- `dnd_display/themes.py` — splash themes as pure data; add a `SplashTheme(...)` to the registry to ship a new one
- `dnd_display/layers/splash.py` — D20 shaders with branched `face_effect` (smooth / cracked stone / mossy stone) and `rune_effect` (solid / flaming / lightning)
- `templates/control.html` — single file, no build step; Cinzel + Source Sans 3, gold-on-dark identity

### Adding a splash theme

```python
# dnd_display/themes.py
"frostbite": SplashTheme(
    name="frostbite",
    description="Pale blue die rimed with frost.",
    face_color=(0.55, 0.62, 0.72),
    rune_color=(0.78, 0.94, 1.00),
    rim_color=(0.85, 0.95, 1.00),
    backdrop_inner=(0.07, 0.10, 0.16),
    backdrop_outer=(0.005, 0.010, 0.020),
    # ...
),
```

It'll appear in the Styles tab automatically (the panel reads from `/api/splash/themes`).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Stuck on **gpu-probe** page | Missing `/opt/dnd-table/.venv/bin/python` | Re-run `bash install.sh` or rebuild the venv |
| "failed to spawn client: permission denied" | `kiosk.sh` lost +x bit | `sudo chmod +x /opt/dnd-table/kiosk.sh && sudo systemctl restart greetd.service` |
| Panel shows old theme after pressing T at the table | T is local-only; the panel learns on its next `/status` poll | Wait 5 s or tap any other control |
| SSE not connecting | Flask not up, or firewall on loopback | `sudo journalctl -u dnd-table.service -n 50` |
| Video plays silently | Audio sink isn't wired into the video pipeline yet | By design; table sound comes from the music output, not map videos |
| Music card shows **Output offline** | `music-output.service` down, or wrong `MUSIC_SERVER_URL` | `sudo systemctl status music-output`; check `/etc/music-output.env`, then `sudo systemctl restart music-output` |
| Not sure if a glitch is the TV or the app | — | **Settings → Display Test** shows SMPTE bars straight from GStreamer; if they look wrong too, it's the TV / HDMI link, not the media |
| Map goes all black after enabling **Fog** | Dynamic fog, but no party token has vision yet | Place a token, mark it **Party**, enable its **Vision** — or switch the fog tool to **Manual** and reveal areas with the brush |

## Project status

Personal tabletop kit. Maintained by [@pjunak](https://github.com/pjunak). Don't expect support beyond "file an issue, maybe I'll get to it".
