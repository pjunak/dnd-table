"""
Elder Futhark rune atlas — generated at startup via PIL.

A 5×4 = 20 tile grid (one tile per icosahedron face). Each tile draws a
single rune centred and white-on-transparent. The atlas is uploaded once
to a moderngl texture; per-face UV coordinates index into it.

Falls back to numeric digits (1–20) if no font with the Unicode Runic
block (U+16A0–U+16F0) can be found on the system.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .mesh import RUNE_CENTROID_X, RUNE_CENTROID_Y

log = logging.getLogger(__name__)


# Elder Futhark (24 runes; we use 20, one per icosahedron face).
# Kenaz (ᚲ) is intentionally skipped — in most fonts it renders as a
# bare `<` wedge, which on a die face reads as the caron diacritic
# (ˇ) rather than a rune.  Dagaz (ᛞ) takes its slot: it's unambiguous
# as a rune at any size.
ELDER_FUTHARK = [
    "ᚠ",  # ᚠ Fehu
    "ᚢ",  # ᚢ Uruz
    "ᚦ",  # ᚦ Thurisaz
    "ᚨ",  # ᚨ Ansuz
    "ᚱ",  # ᚱ Raidho
    "ᛞ",  # ᛞ Dagaz — replaces Kenaz (which read as ˇ)
    "ᚷ",  # ᚷ Gebo
    "ᚹ",  # ᚹ Wunjo
    "ᚺ",  # ᚺ Hagalaz
    "ᚾ",  # ᚾ Naudiz
    "ᛁ",  # ᛁ Isaz
    "ᛃ",  # ᛃ Jera
    "ᛇ",  # ᛇ Eihwaz
    "ᛈ",  # ᛈ Pertho
    "ᛉ",  # ᛉ Algiz
    "ᛋ",  # ᛋ Sowilo
    "ᛏ",  # ᛏ Tiwaz
    "ᛒ",  # ᛒ Berkanan
    "ᛖ",  # ᛖ Ehwaz
    "ᛗ",  # ᛗ Mannaz
]

RUNE_ATLAS_COLS = 5
RUNE_ATLAS_ROWS = 4
RUNE_TILE_PX = 256

# Probed in order; first hit wins.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansRunic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansRunic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font_with_runic() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def build_rune_atlas():
    """Build the rune atlas as a PIL Image (RGBA, premultiplied alpha).

    Returns None if PIL or no usable font is available — the SplashLayer
    handles that gracefully (faces render with no rune overlay).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.warning("PIL/Pillow not available — splash will render plain faces")
        return None

    font_path = _find_font_with_runic()
    if font_path is None:
        log.warning("No suitable font found for runes — splash will render plain faces")
        return None
    log.info("Building rune atlas using %s", font_path)

    width = RUNE_ATLAS_COLS * RUNE_TILE_PX
    height = RUNE_ATLAS_ROWS * RUNE_TILE_PX
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(atlas)

    # Smaller font + position at the inscribed-triangle centroid (not the
    # tile centre) so the rune sits inside what the face actually samples.
    # 40% of tile width fits comfortably inside the inscribed circle of
    # the (margined) equilateral triangle for every Elder Futhark glyph,
    # including the wider ones like ᚹ Wunjo and ᛗ Mannaz.
    font_size = int(RUNE_TILE_PX * 0.40)
    try:
        font = ImageFont.truetype(font_path, size=font_size)
    except OSError as e:
        log.warning("Could not load font %s: %s", font_path, e)
        return None

    glyph_count = RUNE_ATLAS_COLS * RUNE_ATLAS_ROWS
    centroid_dx = int(RUNE_TILE_PX * RUNE_CENTROID_X)
    centroid_dy = int(RUNE_TILE_PX * RUNE_CENTROID_Y)
    for i in range(glyph_count):
        col = i % RUNE_ATLAS_COLS
        row = i // RUNE_ATLAS_COLS
        cx = col * RUNE_TILE_PX + centroid_dx
        cy = row * RUNE_TILE_PX + centroid_dy
        glyph = ELDER_FUTHARK[i % len(ELDER_FUTHARK)]
        try:
            draw.text((cx, cy), glyph, fill=(255, 255, 255, 255),
                      font=font, anchor="mm")
        except Exception:
            # Anchor argument requires Pillow >= 8. Fall back to manual centring.
            bbox = draw.textbbox((0, 0), glyph, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), glyph,
                      fill=(255, 255, 255, 255), font=font)

    return atlas
