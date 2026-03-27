"""
DnD Table – Filesystem helpers (browsing, USB detection, folder management).
"""

import os
from pathlib import Path

from config import MEDIA_DIRS, UPLOAD_DIR, PROTECTED_FOLDERS
from media import get_file_type


# ─── USB / source detection ──────────────────────────────────────

def detect_usb_drives():
    usb_paths = []
    user = os.environ.get("USER", "dnd")
    for base in [Path("/media") / user, Path("/run/media") / user]:
        if base.exists():
            for p in base.iterdir():
                if p.is_dir() and p.name != "dnd_media":
                    usb_paths.append(p)
    return usb_paths


def get_source_roots():
    """Map source IDs to filesystem paths."""
    roots = {}
    if MEDIA_DIRS["sdcard"].exists():
        roots["sdcard"] = MEDIA_DIRS["sdcard"]
    for usb in detect_usb_drives():
        roots["usb:" + usb.name] = usb
    return roots


# ─── Directory browsing ─────────────────────────────────────────

def browse_directory(source, rel_path):
    """Browse a specific directory level.  Returns folders, files, breadcrumb."""
    roots = get_source_roots()
    if source not in roots:
        return None

    root = roots[source]
    if ".." in rel_path or rel_path.startswith("/"):
        return None

    target = root / rel_path if rel_path else root
    target = target.resolve()

    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None

    if not target.exists() or not target.is_dir():
        return None

    # Breadcrumb
    source_label = "SD Card" if source == "sdcard" else source.replace("usb:", "USB: ")
    breadcrumb = [{"name": source_label, "path": ""}]
    if rel_path:
        parts = Path(rel_path).parts
        for i, part in enumerate(parts):
            breadcrumb.append({"name": part, "path": "/".join(parts[: i + 1])})

    folders = []
    files = []
    try:
        for item in sorted(target.iterdir()):
            if item.name.startswith(".") or item.name == "lost+found":
                continue
            if item.is_dir():
                is_protected = rel_path == "" and item.name in PROTECTED_FOLDERS
                folders.append({
                    "name": item.name,
                    "protected": is_protected,
                    "path": str(item),
                    "source": "sdcard" if source == "sdcard" else "usb",
                })
            elif item.is_file() and get_file_type(item.name):
                size = item.stat().st_size
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "type": get_file_type(item.name),
                    "source": "sdcard" if source == "sdcard" else "usb",
                    "size": f"{size / 1_048_576:.1f} MB" if size > 1_048_576 else f"{size / 1024:.0f} KB",
                })
    except (PermissionError, OSError):
        pass

    return {
        "breadcrumb": breadcrumb,
        "folders": folders,
        "files": files,
        "current_path": rel_path,
        "source": source,
    }


# ─── Folder helpers ──────────────────────────────────────────────

def ensure_default_folders():
    """Create default top-level folders on SD card if they don't exist."""
    if UPLOAD_DIR.exists():
        for folder in PROTECTED_FOLDERS:
            (UPLOAD_DIR / folder).mkdir(exist_ok=True)
