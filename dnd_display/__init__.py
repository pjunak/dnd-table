"""
dnd_display – native Wayland display app for the DnD table.

Launched by cage (via kiosk.sh) as the single fullscreen Wayland client.
Subscribes to Flask's SSE stream for state, runs a GStreamer pipeline for
video, and composites layers (video / map / grid / tokens / splash / VFX)
via moderngl.
"""

__version__ = "0.1.0"
