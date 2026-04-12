"""
DnD Table – Raspberry Pi boot config helpers.

Reads and writes overscan settings in /boot/firmware/config.txt (Debian 13+).
Provides platform detection to determine if we're running on a Raspberry Pi.
"""

import logging
import os
import subprocess

log = logging.getLogger(__name__)

# Debian 13 (Trixie) and newer use /boot/firmware/config.txt
_CONFIG_PATHS = ["/boot/firmware/config.txt", "/boot/config.txt"]

_OVERSCAN_KEYS = ("disable_overscan", "overscan_left", "overscan_right",
                  "overscan_top", "overscan_bottom")


def _find_config():
    """Return the first config.txt path that exists, or None."""
    for p in _CONFIG_PATHS:
        if os.path.isfile(p):
            return p
    return None


def detect_platform():
    """Detect whether we're running on a Raspberry Pi.

    Returns dict with 'is_rpi' (bool) and 'model' (str or None).
    """
    info = {"is_rpi": False, "model": None}

    # /proc/device-tree/model is the most reliable source on RPi
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip().rstrip("\x00")
        if "raspberry pi" in model.lower():
            info["is_rpi"] = True
            info["model"] = model
            return info
    except (FileNotFoundError, PermissionError):
        pass

    # Fallback: check /proc/cpuinfo for BCM chip
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read()
        if "BCM" in cpuinfo:
            info["is_rpi"] = True
            info["model"] = "Raspberry Pi (detected via cpuinfo)"
    except (FileNotFoundError, PermissionError):
        pass

    return info


def read_boot_config():
    """Read overscan settings from config.txt.

    Returns dict with keys: top, bottom, left, right (int pixels).
    """
    result = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    path = _find_config()
    if not path:
        return result

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "overscan_top":
                    result["top"] = int(val)
                elif key == "overscan_bottom":
                    result["bottom"] = int(val)
                elif key == "overscan_left":
                    result["left"] = int(val)
                elif key == "overscan_right":
                    result["right"] = int(val)
    except Exception as e:
        log.warning("Could not read %s: %s", path, e)

    return result


def write_overscan_config(top, bottom, left, right):
    """Write overscan values to config.txt.

    Creates a backup before writing.  Uses sudo to write since config.txt
    is owned by root.  Sets disable_overscan=0 when any value is non-zero,
    or disable_overscan=1 when all are zero.

    Returns True on success.
    """
    path = _find_config()
    if not path:
        log.error("No config.txt found at any known location")
        return False

    has_overscan = any(v != 0 for v in (top, bottom, left, right))
    new_values = {
        "disable_overscan": "0" if has_overscan else "1",
        "overscan_top": str(top),
        "overscan_bottom": str(bottom),
        "overscan_left": str(left),
        "overscan_right": str(right),
    }

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        log.error("Could not read %s: %s", path, e)
        return False

    # Backup
    try:
        subprocess.run(
            ["sudo", "cp", path, path + ".bak"],
            capture_output=True, timeout=5,
        )
    except Exception as e:
        log.warning("Could not create backup of %s: %s", path, e)

    # Update existing lines or track which keys we've set
    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in new_values:
            new_lines.append(key + "=" + new_values[key] + "\n")
            seen.add(key)
        else:
            new_lines.append(line)

    # Append any keys that weren't already in the file
    for key in _OVERSCAN_KEYS:
        if key not in seen and key in new_values:
            new_lines.append(key + "=" + new_values[key] + "\n")

    # Write via sudo tee
    try:
        content = "".join(new_lines)
        proc = subprocess.run(
            ["sudo", "tee", path],
            input=content, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            log.error("sudo tee failed: %s", proc.stderr.strip())
            return False
        log.info("Wrote overscan to %s: T=%d B=%d L=%d R=%d", path, top, bottom, left, right)
        return True
    except Exception as e:
        log.error("Could not write %s: %s", path, e)
        return False
