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

A later pass switches the appsink caps to ``video/x-raw(memory:GLMemory)``
and shares our EGL context via ``gst.gl.app_context`` — zero-copy from the
VA-API surface, but a lot more EGL plumbing. The Layer API doesn't change.

Threading: the GLib main loop runs in a background thread. The appsink
``new-sample`` signal fires on the streaming thread; we stash the sample
under a lock and the GL thread pulls it once per frame in
``pull_latest_rgba()``.

Pipelines are assembled element-by-element via ``Gst.ElementFactory.make``
rather than ``Gst.parse_launch`` so that file paths (or any other string
that flows in from the control panel) can't inject extra pipeline
elements via embedded quotes / exclamation marks.
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


_RGBA_CAPS = Gst.Caps.from_string("video/x-raw,format=RGBA")


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
        """Play a video / still-image file from disk, looping on EOS.

        ``path`` is passed through ``Gst.ElementFactory.set_property`` so
        no parsing or shell expansion occurs — embedded quotes and other
        special characters are inert.
        """
        pipeline = self._build_file_pipeline(path)
        if pipeline is None:
            return
        self._adopt(pipeline, source_desc=f"file:{path}")

    def play_test_pattern(self, pattern: str = "smpte") -> None:
        """Play a videotestsrc pattern.  Used during bring-up to verify
        the GStreamer→texture path end-to-end without needing a file."""
        pipeline = self._build_testsrc_pipeline(pattern)
        if pipeline is None:
            return
        self._adopt(pipeline, source_desc=f"testsrc:{pattern}")

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

    # ── Pipeline construction ────────────────────────────────────

    def _build_file_pipeline(self, path: str) -> Optional[Gst.Pipeline]:
        """filesrc → decodebin → videoconvert → videoscale → appsink."""
        pipeline = Gst.Pipeline.new("dnd-file-pipeline")

        src = self._make("filesrc")
        if src is None:
            return None
        src.set_property("location", path)

        decode = self._make("decodebin")
        convert = self._make("videoconvert")
        scale = self._make("videoscale")
        sink = self._configure_appsink()
        if not all((decode, convert, scale, sink)):
            return None

        for el in (src, decode, convert, scale, sink):
            pipeline.add(el)

        if not src.link(decode):
            log.error("filesrc → decodebin link failed")
            return None
        if not convert.link(scale) or not scale.link(sink):
            log.error("converter chain link failed")
            return None

        # decodebin exposes pads only after it sniffs the container, so
        # the video pad → convert.sink link is deferred.
        def _on_pad_added(_decode, pad):
            convert_sink = convert.get_static_pad("sink")
            if convert_sink is None or convert_sink.is_linked():
                return
            caps = pad.get_current_caps() or pad.query_caps(None)
            structure = caps.get_structure(0) if caps and caps.get_size() else None
            name = structure.get_name() if structure else ""
            if not name.startswith("video/") and not name.startswith("image/"):
                # Audio pad on a video file (or vice versa) — ignore.
                return
            ret = pad.link(convert_sink)
            if ret != Gst.PadLinkReturn.OK:
                log.warning("decodebin pad link returned %s", ret)

        decode.connect("pad-added", _on_pad_added)
        return pipeline

    def _build_testsrc_pipeline(self, pattern: str) -> Optional[Gst.Pipeline]:
        pipeline = Gst.Pipeline.new("dnd-testsrc-pipeline")
        src = self._make("videotestsrc")
        # `pattern` is a GStreamer enum name — passed via set_property so
        # an unknown value is rejected by GStreamer rather than injected.
        if src is None:
            return None
        try:
            src.set_property("pattern", pattern)
        except (TypeError, ValueError):
            log.warning("Unknown test pattern %r, defaulting to smpte", pattern)
            src.set_property("pattern", "smpte")

        caps_filter = self._make("capsfilter")
        if caps_filter is None:
            return None
        caps_filter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw,framerate=30/1,width=1280,height=720"),
        )

        convert = self._make("videoconvert")
        sink = self._configure_appsink()
        if not all((convert, sink)):
            return None

        for el in (src, caps_filter, convert, sink):
            pipeline.add(el)
        if not src.link(caps_filter) or not caps_filter.link(convert) or not convert.link(sink):
            log.error("testsrc chain link failed")
            return None
        return pipeline

    def _configure_appsink(self) -> Optional["Gst.Element"]:
        sink = self._make("appsink", name="sink")
        if sink is None:
            return None
        sink.set_property("emit-signals", True)
        sink.set_property("sync", True)
        sink.set_property("max-buffers", 2)
        sink.set_property("drop", True)
        sink.set_property("caps", _RGBA_CAPS)
        return sink

    @staticmethod
    def _make(factory: str, name: str = "") -> Optional["Gst.Element"]:
        el = Gst.ElementFactory.make(factory, name or None)
        if el is None:
            log.error("Failed to create GStreamer element: %s", factory)
        return el

    # ── Pipeline adoption (called by play_*) ─────────────────────

    def _adopt(self, pipeline: Gst.Pipeline, source_desc: str) -> None:
        self.stop()

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
        log.error("GStreamer error in %s: %s (%s)",
                  self._source_desc, err.message, dbg)

    def _on_bus_eos(self, _bus, _msg):
        # Loop the source on EOS — DnD maps and ambient videos should loop.
        # Seek-to-start is more reliable than READY→PLAYING for some
        # decoders; flush ensures we don't show a stale frame mid-restart.
        if self._pipeline is None:
            return
        log.debug("EOS on %s, looping", self._source_desc)
        self._pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            0,
        )
