"""
SplashLayer themes.

A theme is a bag of shader-driven parameters: face/spec/rim colours,
backdrop gradient, rune rendering mode, face surface treatment, and any
auxiliary values used by those modes (a secondary colour for the fire's
hot core, the moss tint on weathered stone, etc.).

Adding a new theme is just dropping another `SplashTheme(...)` in
`THEMES`.  Themes that fit the simple "swap colours" mould need no
shader work; effect-driven themes (fire, lightning, stone, moss, …)
declare a non-zero `rune_effect` or `face_effect` enum and the fragment
shader branches accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


# Rune effect enum — must match the values in the fragment shader.
RUNE_EFFECT_SOLID = 0
RUNE_EFFECT_FLAMING = 1
RUNE_EFFECT_LIGHTNING = 2
# Reserve 3, 4… for neon, bone, frozen, etc.

# Face effect enum — must match the values in the fragment shader.
FACE_EFFECT_SMOOTH = 0
FACE_EFFECT_CRACKED_STONE = 1
FACE_EFFECT_MOSSY_STONE = 2


@dataclass(frozen=True)
class SplashTheme:
    """Self-contained visual configuration for the splash."""

    name: str
    description: str

    # D20 face appearance
    face_color: Tuple[float, float, float] = (0.28, 0.22, 0.58)
    face_color2: Tuple[float, float, float] = (0.05, 0.04, 0.10)  # cracks / moss accent
    face_effect: int = FACE_EFFECT_SMOOTH
    spec_color: Tuple[float, float, float] = (1.00, 0.94, 0.80)
    spec_power: float = 28.0
    rim_color:  Tuple[float, float, float] = (0.85, 0.75, 1.00)
    rim_strength: float = 0.45

    # Rune appearance
    rune_effect: int = RUNE_EFFECT_SOLID
    rune_color:  Tuple[float, float, float] = (1.00, 0.86, 0.55)   # primary / cool
    rune_color2: Tuple[float, float, float] = (1.00, 1.00, 0.85)   # secondary / hot

    # Backdrop gradient
    backdrop_inner: Tuple[float, float, float] = (0.155, 0.115, 0.295)
    backdrop_outer: Tuple[float, float, float] = (0.008, 0.006, 0.022)

    # Lighting
    light_dir: Tuple[float, float, float] = (0.40, 0.70, 1.0)
    ambient:   float = 0.22


# ── Registry ─────────────────────────────────────────────────────

THEMES: dict[str, SplashTheme] = {

    "arcane": SplashTheme(
        name="arcane",
        description="The default — deep purple die with warm gold runes.",
    ),

    "flame": SplashTheme(
        name="flame",
        description="Dark forged-iron die with living flame runes.",
        face_color=(0.115, 0.085, 0.078),      # dark iron
        spec_color=(0.95, 0.65, 0.40),         # warm metal highlight
        spec_power=42.0,
        rim_color=(1.00, 0.45, 0.12),          # hot orange rim
        rim_strength=0.55,
        rune_effect=RUNE_EFFECT_FLAMING,
        rune_color=(1.00, 0.38, 0.04),         # base saturated orange
        rune_color2=(1.00, 0.92, 0.45),        # yellow-hot (mid-flame core)
        backdrop_inner=(0.18, 0.08, 0.04),
        backdrop_outer=(0.015, 0.008, 0.004),
        light_dir=(0.30, 0.55, 0.85),
        ambient=0.18,
    ),

    "storm": SplashTheme(
        name="storm",
        description="Stormcloud-grey die crackling with electric-blue runes.",
        face_color=(0.16, 0.18, 0.26),         # bluish slate
        spec_color=(0.85, 0.92, 1.00),         # cold metallic highlight
        spec_power=52.0,
        rim_color=(0.40, 0.70, 1.00),          # electric blue rim
        rim_strength=0.65,
        rune_effect=RUNE_EFFECT_LIGHTNING,
        rune_color=(0.55, 0.80, 1.00),         # pale blue glow
        rune_color2=(0.98, 0.99, 1.00),        # arc-white core
        backdrop_inner=(0.06, 0.08, 0.14),
        backdrop_outer=(0.004, 0.006, 0.014),
        light_dir=(0.20, 0.70, 0.85),
        ambient=0.20,
    ),

    "ancient": SplashTheme(
        name="ancient",
        description="Weathered grey stone, fractured by time, etched with amber runes.",
        face_color=(0.46, 0.43, 0.38),         # warm grey stone
        face_color2=(0.08, 0.06, 0.04),        # deep crack shadow
        face_effect=FACE_EFFECT_CRACKED_STONE,
        spec_color=(0.55, 0.50, 0.42),
        spec_power=14.0,                        # rough → broad, dim highlight
        rim_color=(0.95, 0.78, 0.50),          # golden-hour rim
        rim_strength=0.32,
        rune_color=(1.00, 0.78, 0.42),         # ochre engraved-rune glow
        rune_color2=(1.00, 0.90, 0.65),
        backdrop_inner=(0.13, 0.10, 0.07),
        backdrop_outer=(0.020, 0.014, 0.010),
        light_dir=(0.35, 0.60, 0.90),
        ambient=0.28,
    ),

    "verdant": SplashTheme(
        name="verdant",
        description="Old stone reclaimed by moss and lichen, runes glowing leaf-green.",
        face_color=(0.40, 0.40, 0.36),         # cooler weathered stone
        face_color2=(0.18, 0.34, 0.16),        # deep moss green
        face_effect=FACE_EFFECT_MOSSY_STONE,
        spec_color=(0.55, 0.62, 0.50),
        spec_power=12.0,                        # very matte
        rim_color=(0.50, 0.75, 0.45),          # soft green rim
        rim_strength=0.32,
        rune_color=(0.55, 0.95, 0.55),         # bright leaf-green
        rune_color2=(0.85, 1.00, 0.75),
        backdrop_inner=(0.07, 0.11, 0.07),
        backdrop_outer=(0.008, 0.018, 0.010),
        light_dir=(0.40, 0.70, 0.80),
        ambient=0.30,
    ),
}


DEFAULT_THEME = "arcane"


def get(name: str) -> SplashTheme:
    """Resolve a theme name, falling back to the default if unknown."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])
