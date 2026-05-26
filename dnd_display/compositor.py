"""
Layer-based compositor for the DnD display.

The Compositor owns a stack of Layers and renders them in z-order each
frame. Each Layer owns its own GL resources and exposes a small lifecycle
(setup → resize → update → render → teardown) that the compositor drives.

Design notes:
- Z-order is an int; lower draws first (background → foreground).
- Layers are responsible for their own blending semantics inside `render`,
  but the compositor sets a sensible default (SRC_ALPHA / ONE_MINUS_SRC_ALPHA).
- Find layers by name with `find()` — used by the SSE bridge to push
  state into specific layers (e.g. `compositor.find("grid").set_state(...)`).
- Hot-add/remove is supported; setup() is called on add, teardown() on remove.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import moderngl

log = logging.getLogger(__name__)


class Layer(ABC):
    """Abstract base for renderable layers."""

    def __init__(self, name: str = "", z_order: int = 0):
        self.name: str = name or type(self).__name__
        self.z_order: int = z_order
        self.opacity: float = 1.0
        self.visible: bool = True
        self.ctx: Optional[moderngl.Context] = None
        self.width: int = 0
        self.height: int = 0
        # Set True by layers that want the full framebuffer viewport
        # (escape the safe-area inset).  The calibration overlay needs
        # this so its red edge-border renders past the inset.
        self.full_framebuffer: bool = False

    @abstractmethod
    def setup(self, ctx: moderngl.Context) -> None:
        """Allocate GL resources. Called once when added to the compositor."""

    def resize(self, width: int, height: int) -> None:
        """Called on add and on window resize. Default just stores dimensions."""
        self.width = width
        self.height = height

    def update(self, dt: float) -> None:
        """Per-frame logic update (texture uploads, animation, etc.).
        Runs on the GL thread before render."""

    @abstractmethod
    def render(self) -> None:
        """Draw the layer using the bound framebuffer."""

    def teardown(self) -> None:
        """Release GL resources. Called when removed or compositor shuts down."""


class Compositor:
    """Renders an ordered stack of Layers.

    Owns the safe-area / overscan inset: layers render into a sub-rectangle
    of the framebuffer (top/bottom/left/right in pixels), with the strip
    outside cleared to black.  Layers compute their own aspect using the
    inset dimensions reported by ``resize()``, so the D20 stays round and
    videos preserve their aspect even with non-trivial insets.
    """

    def __init__(self, ctx: moderngl.Context, width: int, height: int):
        self.ctx = ctx
        # Full framebuffer dimensions — used to clear the outside-inset border.
        self.fb_width: int = width
        self.fb_height: int = height
        # Effective render area (framebuffer minus overscan).  Exposed to
        # layers as ``width`` / ``height`` for backwards-compat.
        self.width: int = width
        self.height: int = height
        # Overscan in pixels (clamped non-negative on assignment).
        self._overscan = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        self._layers: list[Layer] = []

    # ── Layer management ────────────────────────────────────────────

    def add(self, layer: Layer) -> Layer:
        layer.setup(self.ctx)
        layer.resize(self.width, self.height)
        self._layers.append(layer)
        self._layers.sort(key=lambda l: l.z_order)
        log.info("Added layer: %s (z=%d)", layer.name, layer.z_order)
        return layer

    def remove(self, layer: Layer) -> None:
        if layer in self._layers:
            self._layers.remove(layer)
            layer.teardown()
            log.info("Removed layer: %s", layer.name)

    def find(self, name: str) -> Optional[Layer]:
        for l in self._layers:
            if l.name == name:
                return l
        return None

    # ── Sizing & overscan ───────────────────────────────────────────

    def resize(self, width: int, height: int) -> None:
        self.fb_width = width
        self.fb_height = height
        self._recompute_inset()

    def set_overscan(self, top: int = 0, bottom: int = 0,
                     left: int = 0, right: int = 0) -> None:
        """Update safe-area inset (px)."""
        self._overscan = {
            "top": max(0, int(top)),
            "bottom": max(0, int(bottom)),
            "left": max(0, int(left)),
            "right": max(0, int(right)),
        }
        self._recompute_inset()

    def get_overscan(self) -> dict[str, int]:
        return dict(self._overscan)

    def _recompute_inset(self) -> None:
        """Re-derive inset dimensions and notify every layer of the new size."""
        ov = self._overscan
        w = max(1, self.fb_width - ov["left"] - ov["right"])
        h = max(1, self.fb_height - ov["top"] - ov["bottom"])
        if (w, h) != (self.width, self.height):
            self.width = w
            self.height = h
            for l in self._layers:
                l.resize(w, h)

    # ── Per-frame ───────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        for l in self._layers:
            if l.visible:
                l.update(dt)

    def render(self) -> None:
        """Clear the full framebuffer black, then render every visible layer.

        Most layers render into the safe-area inset viewport; layers that
        set ``full_framebuffer=True`` (currently just the calibration
        overlay) get the full framebuffer instead so their content can
        reach the absolute screen edge.
        """
        ctx = self.ctx
        full_vp = (0, 0, self.fb_width, self.fb_height)
        ctx.viewport = full_vp
        ctx.clear(0.0, 0.0, 0.0, 1.0)

        # Inset viewport — GL origin is bottom-left, so the y-offset is the
        # "bottom" inset (NOT "top").
        ov = self._overscan
        inset_vp = (ov["left"], ov["bottom"], self.width, self.height)

        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

        current_vp = None
        for l in self._layers:
            if not l.visible or l.opacity <= 0.0:
                continue
            target_vp = full_vp if l.full_framebuffer else inset_vp
            if target_vp != current_vp:
                ctx.viewport = target_vp
                current_vp = target_vp
            l.render()

    def teardown(self) -> None:
        for l in self._layers:
            l.teardown()
        self._layers.clear()
