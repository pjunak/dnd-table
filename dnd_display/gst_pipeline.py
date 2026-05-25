"""
GStreamer pipeline driving the VideoLayer.

Pipeline shape (v0, CPU-side handoff):

    filesrc / videotestsrc
        ! decodebin            (auto-selects vaapidecode for H.264/HEVC/VP9)
        ! videoconvert
        ! videoscale
        ! appsink caps=video/x-raw,format=RGBA

Hardware decode still happens (the vaapi plugin gets selected by decodebin
when available); the final upload to our GL context costs one RGBA copy
per frame. Acceptable on Braswell @ 1080p.

A later pass switches the appsink caps to `video/x-raw(memory:GLMemory)`
and shares our EGL context via gst.gl.app_context — zero-copy from the
VA-API surface, but a lot more EGL plumbing. The Layer API doesn't change.

Threading: the GLib main loop runs in a background thread. The appsink
"new-sample" signal fires on the streaming thread; we stash the sample
under a lock and the GL thread pulls it once per frame in
``pull_latest_rgba()``.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # type: ignore[attr-defined]  # noqa: E402

log = logging.getLogger(__name__)

Gst.init(None)


_RGBA_CAPS = "video/x-raw,format=RGBA"


class VideoPipeline:
    """One playing pipeline at a time. Calling play() restarts."""

    def __init__(self) -> None:
        self._pipeline: Optional[Gst.Pipeline] = None
        self._sink = None
        self._sample_lock = threading.Lock()
        self._latest_sample: Optional[Gst.Sample] = None
        self._loop: Optional[GLib.MainLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._source_desc: str = ""

    # ── Lifecycle ────────────────────────────────────────────────

    def play_file(self, path: str) -> None:
        """Play a video/image file from disk, looping forever."""
        # filesrc + decodebin handles both video and still images
        # (decodebin returns image/* caps for stills; videoconvert handles it).
        desc = (
            f'filesrc location="{path}" ! decodebin name=dec ! '
            f'videoconvert ! videoscale ! '
            f'appsink name=sink emit-signals=true sync=true '
            f'max-buffers=2 drop=true caps="{_RGBA_CAPS}"'
        )
        self._launch(desc, source_desc=f"file:{path}")

    def play_test_pattern(self, pattern: str = "smpte") -> None:
        """Play a videotestsrc test pattern. No file needed; useful for verifying
        the GStreamer→texture path end-to-end."""
        desc = (
            f'videotestsrc pattern={pattern} ! '
            f'video/x-raw,framerate=30/1,width=1280,height=720 ! '
            f'videoconvert ! '
            f'appsink name=sink emit-signals=true sync=true '
            f'max-buffers=2 drop=true caps="{_RGBA_CAPS}"'
        )
        self._launch(desc, source_desc=f"testsrc:{pattern}")

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._sink = None
        with self._sample_lock:
            self._latest_sample = None

    # ── Frame access (called from GL thread) ─────────────────────

    def pull_latest_rgba(self) -> Optional[tuple[bytes, int, int]]:
        """Return (rgba_bytes, width, height) for the most recent frame, or None."""
        with self._sample_lock:
            sample = self._latest_sample
            self._latest_sample = None
        if sample is None:
            return None

        caps = sample.get_caps()
        if caps is None:
            return None
        struct_ = caps.get_structure(0)
        ok_w, w = struct_.get_int("width")
        ok_h, h = struct_.get_int("height")
        if not (ok_w and ok_h):
            return None

        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            data = bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)
        return data, w, h

    # ── Internals ────────────────────────────────────────────────

    def _launch(self, desc: str, source_desc: str) -> None:
        self.stop()
        try:
            pipeline = Gst.parse_launch(desc)
        except GLib.Error as e:
            log.error("Failed to build pipeline (%s): %s", source_desc, e)
            return
        assert isinstance(pipeline, Gst.Pipeline)

        sink = pipeline.get_by_name("sink")
        if sink is None:
            log.error("Pipeline %s missing appsink", source_desc)
            return
        sink.connect("new-sample", self._on_new_sample)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::eos", self._on_bus_eos)

        self._pipeline = pipeline
        self._sink = sink
        self._source_desc = source_desc

        self._ensure_loop()
        pipeline.set_state(Gst.State.PLAYING)
        log.info("GStreamer pipeline playing: %s", source_desc)

    def _ensure_loop(self) -> None:
        if self._loop_thread is not None:
            return
        self._loop = GLib.MainLoop()
        self._loop_thread = threading.Thread(
            target=self._loop.run, name="gst-glib-loop", daemon=True,
        )
        self._loop_thread.start()

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        with self._sample_lock:
            self._latest_sample = sample
        return Gst.FlowReturn.OK

    def _on_bus_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        log.error("GStreamer error in %s: %s (%s)", self._source_desc, err.message, dbg)

    def _on_bus_eos(self, _bus, _msg):
        # Loop the source on EOS — DnD maps and ambient videos should loop.
        log.debug("EOS on %s, restarting", self._source_desc)
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.READY)
            self._pipeline.set_state(Gst.State.PLAYING)
