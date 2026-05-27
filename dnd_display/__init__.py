"""
dnd_display — native Wayland display app for the DnD table.

Launched by cage (via ``kiosk.sh``) as the single fullscreen Wayland
client.  Subscribes to Flask's SSE stream on ``/display/stream`` for
state, runs a GStreamer pipeline for video / images, and composites
layers (video / grid / splash / calibration / debug) through a moderngl
context.

Public entry point: ``python -m dnd_display`` → ``dnd_display.app.main``.
Layers are pluggable through ``Compositor.add()`` and are kept entirely
GL-free in their data path so new ones (tokens, VFX, fog) can land
without touching the existing stack.
"""

__version__ = "0.2.0"
