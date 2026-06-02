"""
DnD Table – Map → screen coordinate transform (pure, no GL).

The video map is fit into the compositor's inset viewport with aspect-correct
letterboxing / pillarboxing.  Every overlay that must line up with the map
(tokens, walls, vision, fog, markers) has to reproduce that exact fit, so the
math lives here once and is shared by ``VideoLayer`` and the overlay layers —
they can never drift apart.

Coordinate spaces:
  - map pixels      (0,0) top-left .. (tw, th) bottom-right of the map image.
  - inset viewport  what a layer's fragment shader sees: ``gl_FragCoord.xy``,
                    bottom-left origin, ``[0, vw) x [0, vh)``  (vw/vh are the
                    compositor's inset dimensions passed to ``Layer.resize``).
  - NDC             [-1, 1] x [-1, 1], the clip space layers draw into.

Pure functions + a frozen dataclass: unit-testable without a GL context.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def aspect_scale(tw: float, th: float, vw: float, vh: float) -> tuple[float, float]:
    """Letterbox/pillarbox scale ``(sx, sy)`` fitting a ``tw×th`` image into a
    ``vw×vh`` viewport while preserving aspect ratio.

    The map quad spans NDC (-1,-1)..(1,1); multiplying its corners by
    ``(sx, sy)`` shrinks it on whichever axis would otherwise stretch the
    image.  Returns ``(1.0, 1.0)`` until both sizes are known.  This is the
    exact math ``VideoLayer`` used to inline, so video + overlays stay locked.
    """
    if tw <= 0 or th <= 0 or vw <= 0 or vh <= 0:
        return 1.0, 1.0
    viewport_aspect = vw / vh
    image_aspect = tw / th
    if image_aspect > viewport_aspect:
        # Image wider than the viewport — fit width, letterbox top/bottom.
        return 1.0, viewport_aspect / image_aspect
    # Image taller than the viewport — fit height, pillarbox left/right.
    return image_aspect / viewport_aspect, 1.0


@dataclass(frozen=True)
class MapTransform:
    """Maps map-image pixels to the inset viewport's NDC / pixels.

    Built by whoever owns the current map size (the display app) from the
    map's native pixel size ``(tw, th)`` and the inset viewport size
    ``(vw, vh)``.  Frozen, with the letterbox scale derived once, so it's
    cheap to hand to every overlay layer and safe to share.
    """

    tw: float          # map image width  (px)
    th: float          # map image height (px)
    vw: float          # inset viewport width  (px)
    vh: float          # inset viewport height (px)
    sx: float = field(init=False)
    sy: float = field(init=False)

    def __post_init__(self) -> None:
        sx, sy = aspect_scale(self.tw, self.th, self.vw, self.vh)
        object.__setattr__(self, "sx", sx)
        object.__setattr__(self, "sy", sy)

    @property
    def valid(self) -> bool:
        return self.tw > 0 and self.th > 0 and self.vw > 0 and self.vh > 0

    def map_px_to_ndc(self, x: float, y: float) -> tuple[float, float]:
        """Map pixel ``(x, y)`` (top-left origin) → NDC, matching the video
        quad's letterbox and the image-space Y flip (map top → +Y)."""
        if not self.valid:
            return 0.0, 0.0
        u = x / self.tw
        v = y / self.th
        return (2.0 * u - 1.0) * self.sx, (1.0 - 2.0 * v) * self.sy

    def map_px_to_viewport_px(self, x: float, y: float) -> tuple[float, float]:
        """Map pixel → inset-viewport pixel (bottom-left origin, the space
        ``gl_FragCoord`` reports).  Useful for hit-testing / snapping."""
        ndc_x, ndc_y = self.map_px_to_ndc(x, y)
        return 0.5 * self.vw * (1.0 + ndc_x), 0.5 * self.vh * (1.0 + ndc_y)

    def map_len_to_ndc(self, dx: float, dy: float) -> tuple[float, float]:
        """Scale a map-pixel length/offset to NDC (no translation)."""
        if not self.valid:
            return 0.0, 0.0
        return (2.0 * dx / self.tw) * self.sx, (2.0 * dy / self.th) * self.sy

    @property
    def px_scale(self) -> float:
        """Single map-px → inset-px scale factor.  Equal on both axes because
        aspect is preserved, so a scalar length in map pixels (a vision
        radius, a token diameter) converts to screen pixels with one multiply.
        """
        if not self.valid:
            return 1.0
        return (self.vw * self.sx) / self.tw
