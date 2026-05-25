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
    """Renders an ordered stack of Layers."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int):
        self.ctx = ctx
        self.width = width
        self.height = height
        self._layers: list[Layer] = []

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

    def resize(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        for l in self._layers:
            l.resize(width, height)

    def update(self, dt: float) -> None:
        for l in self._layers:
            if l.visible:
                l.update(dt)

    def render(self) -> None:
        # Default blend mode for transparent layers (grid, splash, VFX).
        # Individual layers can override inside their render() if needed.
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        for l in self._layers:
            if l.visible and l.opacity > 0.0:
                l.render()

    def teardown(self) -> None:
        for l in self._layers:
            l.teardown()
        self._layers.clear()
