"""
DnD Table – Canonical scene model (pure: no GL, no Flask).

One format-agnostic representation of a battle map's interactive layer —
walls (vision/movement blockers), doors, lights, tokens, fog, and markers.
Everything is in **map-image pixel** coordinates (origin top-left): the
resolution-independent space every VTT format can be translated into, and
that the display maps onto the screen via ``transform.MapTransform``.

This is intentionally a *superset* of the Universal VTT (UVTT/.dd2vtt)
schema — walls ≈ ``line_of_sight``, doors ≈ ``portals``, ``lights``, grid ≈
``resolution`` — plus a game-state layer (tokens, markers, fog) that map
formats don't carry.  Importers (``dnd_display.importers``) translate any
foreign format into ``SceneData``; the engine only ever sees this model.

Serialization is plain dict ⇄ dataclass so a scene round-trips unchanged
through the sidecar ``.scene.json`` and the SSE bridge.  Parsing is
deliberately tolerant (unknown keys ignored, missing keys defaulted) so the
format can evolve and importers can be lax.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

SCENE_VERSION = 1

# ── Enums (string-valued; mirror the control panel + importers) ──────
GRID_SQUARE, GRID_HEX = "square", "hex"
TOKEN_DISC, TOKEN_IMAGE = "disc", "image"          # token render kind
DOOR_OPEN, DOOR_CLOSED, DOOR_LOCKED = "open", "closed", "locked"
MARKER_TRAP, MARKER_HAZARD, MARKER_DIFFICULT, MARKER_NOTE = (
    "trap", "hazard", "difficult", "note")
FOG_DYNAMIC, FOG_MANUAL = "dynamic", "manual"       # vision-driven vs painted

Pt = tuple[float, float]


def _pt(p) -> Pt:
    return (float(p[0]), float(p[1]))


def _pts(raw) -> list[Pt]:
    return [_pt(p) for p in (raw or [])]


# ── Components ───────────────────────────────────────────────────────

@dataclass
class Grid:
    """The MAP's own grid, in map pixels — used for token sizing, snapping
    and import.  Distinct from the *physical table* grid (``state.grid_state``,
    which is in screen pixels); never conflate the two."""
    ppg: float = 70.0                       # pixels per cell (map px)
    origin: Pt = (0.0, 0.0)
    type: str = GRID_SQUARE


@dataclass
class Wall:
    """A vision/movement-blocking polyline (≥2 points), map px."""
    id: str = ""
    points: list[Pt] = field(default_factory=list)
    blocks_vision: bool = True
    blocks_movement: bool = True


@dataclass
class Door:
    """A toggleable wall segment (two endpoints), map px."""
    id: str = ""
    points: list[Pt] = field(default_factory=list)   # exactly 2 points
    state: str = DOOR_CLOSED
    blocks_vision_when_closed: bool = True


@dataclass
class Light:
    id: str = ""
    x: float = 0.0
    y: float = 0.0
    bright_radius: float = 0.0              # map px
    dim_radius: float = 0.0
    color: str = "#ffffff"
    enabled: bool = True


@dataclass
class TokenVision:
    enabled: bool = False
    range: float = 0.0                      # map px (0 = unlimited on-map)


@dataclass
class Token:
    id: str = ""
    x: float = 0.0                          # center, map px
    y: float = 0.0
    size_cells: float = 1.0                 # diameter in grid cells
    kind: str = TOKEN_DISC
    name: str = ""
    label: str = ""                         # short text drawn on a disc token
    color: str = "#c9a84c"
    image_ref: Optional[str] = None         # source path for image tokens
    vision: TokenVision = field(default_factory=TokenVision)
    is_party: bool = False                  # party tokens drive the shared reveal
    hidden: bool = False                    # GM-only (stripped before the display)


@dataclass
class Marker:
    id: str = ""
    type: str = MARKER_NOTE
    x: float = 0.0
    y: float = 0.0
    shape: str = "point"                    # point | circle | rect | poly
    size_cells: float = 1.0
    points: list[Pt] = field(default_factory=list)   # for rect/poly
    color: str = "#c84a4a"
    label: str = ""
    hidden: bool = True                     # traps start hidden; GM reveals
    payload: dict = field(default_factory=dict)      # free-form (DC, damage…)


@dataclass
class Fog:
    mode: str = FOG_DYNAMIC
    # Off until the GM turns it on, so a freshly-loaded map isn't all black
    # before any walls / party vision exist.
    enabled: bool = False
    # Manual reveal regions (polygons, map px): the whole fog in FOG_MANUAL,
    # or force-revealed areas layered on top of dynamic vision.
    revealed: list[list[Pt]] = field(default_factory=list)


@dataclass
class SceneData:
    map_path: str = ""
    width: int = 0                          # map native px
    height: int = 0
    grid: Grid = field(default_factory=Grid)
    walls: list[Wall] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)
    lights: list[Light] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    fog: Fog = field(default_factory=Fog)
    version: int = SCENE_VERSION

    # ── Serialization ────────────────────────────────────────────────

    def to_payload(self) -> dict:
        """Plain JSON-able dict (tuples serialize as arrays)."""
        return asdict(self)

    @classmethod
    def from_payload(cls, d: Optional[dict]) -> "SceneData":
        """Build from a (possibly partial / foreign-ish) dict.  Tolerant:
        missing keys default, unknown keys are ignored."""
        d = d or {}
        g = d.get("grid") or {}
        return cls(
            map_path=d.get("map_path", ""),
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            grid=Grid(
                ppg=float(g.get("ppg", 70.0) or 70.0),
                origin=_pt(g.get("origin", (0.0, 0.0))),
                type=g.get("type", GRID_SQUARE),
            ),
            walls=[
                Wall(id=w.get("id", ""), points=_pts(w.get("points")),
                     blocks_vision=bool(w.get("blocks_vision", True)),
                     blocks_movement=bool(w.get("blocks_movement", True)))
                for w in d.get("walls", [])
            ],
            doors=[
                Door(id=o.get("id", ""), points=_pts(o.get("points")),
                     state=o.get("state", DOOR_CLOSED),
                     blocks_vision_when_closed=bool(
                         o.get("blocks_vision_when_closed", True)))
                for o in d.get("doors", [])
            ],
            lights=[
                Light(id=l.get("id", ""), x=float(l.get("x", 0.0)),
                      y=float(l.get("y", 0.0)),
                      bright_radius=float(l.get("bright_radius", 0.0)),
                      dim_radius=float(l.get("dim_radius", 0.0)),
                      color=l.get("color", "#ffffff"),
                      enabled=bool(l.get("enabled", True)))
                for l in d.get("lights", [])
            ],
            tokens=[_token_from(t) for t in d.get("tokens", [])],
            markers=[_marker_from(m) for m in d.get("markers", [])],
            fog=_fog_from(d.get("fog")),
            version=int(d.get("version", SCENE_VERSION) or SCENE_VERSION),
        )

    # ── Display payload (strip GM-only data) ─────────────────────────

    def to_display_payload(self) -> dict:
        """Scene as the players' display should see it: hidden tokens and
        hidden markers are removed so a screenshot of the table can never
        leak a hidden trap or an unrevealed monster.  Walls/doors stay (the
        display needs them to compute vision; they aren't drawn)."""
        d = self.to_payload()
        d["tokens"] = [t for t in d["tokens"] if not t.get("hidden")]
        d["markers"] = [m for m in d["markers"] if not m.get("hidden")]
        return d


def _token_from(t: dict) -> Token:
    v = t.get("vision") or {}
    return Token(
        id=t.get("id", ""), x=float(t.get("x", 0.0)), y=float(t.get("y", 0.0)),
        size_cells=float(t.get("size_cells", 1.0) or 1.0),
        kind=t.get("kind", TOKEN_DISC), name=t.get("name", ""),
        label=t.get("label", ""), color=t.get("color", "#c9a84c"),
        image_ref=t.get("image_ref"),
        vision=TokenVision(enabled=bool(v.get("enabled", False)),
                           range=float(v.get("range", 0.0) or 0.0)),
        is_party=bool(t.get("is_party", False)),
        hidden=bool(t.get("hidden", False)),
    )


def _marker_from(m: dict) -> Marker:
    return Marker(
        id=m.get("id", ""), type=m.get("type", MARKER_NOTE),
        x=float(m.get("x", 0.0)), y=float(m.get("y", 0.0)),
        shape=m.get("shape", "point"),
        size_cells=float(m.get("size_cells", 1.0) or 1.0),
        points=_pts(m.get("points")), color=m.get("color", "#c84a4a"),
        label=m.get("label", ""), hidden=bool(m.get("hidden", True)),
        payload=dict(m.get("payload") or {}),
    )


def _fog_from(f: Optional[dict]) -> Fog:
    f = f or {}
    return Fog(
        mode=f.get("mode", FOG_DYNAMIC),
        enabled=bool(f.get("enabled", False)),
        revealed=[_pts(poly) for poly in f.get("revealed", [])],
    )
