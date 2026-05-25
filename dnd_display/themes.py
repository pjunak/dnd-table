"""
SplashLayer themes.

A theme is a bag of shader-driven parameters: face/spec/rim colours,
backdrop gradient, rune rendering mode, and any auxiliary values used by
that mode (a secondary colour for the fire's hot core, etc.).

Adding a new theme is just dropping another `SplashTheme(...)` in
`THEMES`. Themes that fit the simple "swap colours" mould need no shader
work; effect-driven themes (fire, neon, bone, …) declare a non-zero
`rune_effect` enum and the fragment shader branches accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


# Rune effect enum — must match the values in the fragment shader.
RUNE_EFFECT_SOLID = 0
RUNE_EFFECT_FLAMING = 1
# Reserve 2, 3, 4… for neon, bone, frozen, etc.


@dataclass(frozen=True)
class SplashTheme:
    """Self-contained visual configuration for the splash."""

    name: str
    description: str

    # D20 face appearance
    face_color: Tuple[float, float, float] = (0.28, 0.22, 0.58)
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
}


DEFAULT_THEME = "arcane"


def get(name: str) -> SplashTheme:
    """Resolve a theme name, falling back to the default if unknown."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])
