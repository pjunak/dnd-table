"""
DnD Table – Display output helpers (xrandr-based TV adjustments).

Applies HDMI output settings to fight common TV picture distortion:
  - Color range: Full RGB (0-255) vs Limited (16-235)
  - Underscan: Lets the TV know the signal should not be overscanned
  - Sharpness / dot-by-dot: Disables GPU scaling so pixels map 1:1
"""

import logging
import os
import subprocess

from config import DISPLAY

log = logging.getLogger(__name__)


def _xrandr(*args):
    """Run an xrandr command, returning True on success."""
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    cmd = ["xrandr", "--display", DISPLAY] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, env=env,
        )
        if result.returncode != 0:
            log.warning("xrandr failed: %s", result.stderr.strip())
            return False
        return True
    except Exception as e:
        log.warning("xrandr error: %s", e)
        return False


def _detect_output():
    """Detect the connected HDMI/DP output name."""
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    try:
        result = subprocess.run(
            ["xrandr", "--display", DISPLAY, "--query"],
            capture_output=True, text=True, timeout=5, env=env,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "connected":
                return parts[0]
    except Exception as e:
        log.warning("Could not detect output: %s", e)
    return "HDMI-1"  # sensible default for RPi


def apply_color_range(mode):
    """Set Broadcast RGB to 'Full' or 'Limited 16:235'.

    This controls whether the HDMI output uses full PC range (0-255)
    or the limited TV range (16-235). TVs that expect limited range
    will crush blacks and clip whites when receiving full-range signal.
    """
    output = _detect_output()
    if mode == "limited":
        value = "Limited 16:235"
    else:
        value = "Full"
    ok = _xrandr("--output", output, "--set", "Broadcast RGB", value)
    if ok:
        log.info("Set Broadcast RGB = %s on %s", value, output)
    return ok


def apply_underscan(enabled):
    """Toggle underscan property on the output.

    When enabled, tells the TV/monitor via HDMI metadata that the
    signal is already underscanned, so the TV should not apply its
    own overscan cropping.
    """
    output = _detect_output()
    value = "on" if enabled else "off"
    # Try the common property names used by different GPU drivers
    for prop in ("underscan", "underscan support"):
        if _xrandr("--output", output, "--set", prop, value):
            log.info("Set %s = %s on %s", prop, value, output)
            if enabled:
                # Some drivers also need underscan hborder/vborder = 0
                _xrandr("--output", output, "--set", "underscan hborder", "0")
                _xrandr("--output", output, "--set", "underscan vborder", "0")
            return True
    log.info("Underscan property not available on %s (not all drivers support it)", output)
    return False


def apply_sharpness(enabled):
    """Set scaling mode to None (dot-by-dot) or Full (GPU-scaled).

    Dot-by-dot / 'None' scaling tells the GPU not to scale the output,
    which avoids blurry interpolation on TVs. This is sometimes called
    'Just Scan' or '1:1 pixel mapping' on TVs.
    """
    output = _detect_output()
    value = "None" if enabled else "Full"
    for prop in ("scaling mode", "scaler"):
        if _xrandr("--output", output, "--set", prop, value):
            log.info("Set %s = %s on %s", prop, value, output)
            return True
    log.info("Scaling mode property not available on %s", output)
    return False


def apply_all_tv_settings(color_range, underscan, sharpness):
    """Apply all TV-related xrandr settings at once."""
    apply_color_range(color_range)
    apply_underscan(underscan)
    apply_sharpness(sharpness)
