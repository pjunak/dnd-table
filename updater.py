"""
DnD Table – Self-update from GitHub.

Checks the remote repository for new commits, pulls changes into the
local git clone, rsyncs to /opt/dnd-table, and restarts the service.

The install directory (/opt/dnd-table) is a plain copy — not a git repo.
The git repo lives wherever the project was originally cloned (typically
/home/dnd/dnd-table or similar).  On first install via install.sh the
repo path is embedded; on subsequent updates we resolve it at runtime.
"""

import logging
import os
import subprocess

log = logging.getLogger(__name__)

INSTALL_DIR = "/opt/dnd-table"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE = "origin"
BRANCH = "main"
REPO_URL = "https://github.com/pjunak/dnd-table"

# Headless music-output client (pjunak/music) — lives outside the repo at
# /opt/music-output and is refreshed from upstream on each update.
_MUSIC_OUTPUT_PY = "/opt/music-output/music_output.py"
_MUSIC_OUTPUT_URL = (
    "https://raw.githubusercontent.com/pjunak/music/main/"
    "clients/headless/music_output.py"
)

# Must match install.sh — otherwise `rsync --delete` will wipe the venv,
# user PNGs, and on-disk settings every time the updater runs.
_RSYNC_EXCLUDES = (
    ".git", "__pycache__", ".vscode", ".gitignore",
    ".venv", "*.png", "settings.json",
)


def _git(*args, timeout=15):
    """Run a git command inside the repo directory."""
    env = os.environ.copy()
    cmd = ["git", "-C", REPO_DIR] + list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env,
    )
    return result


def _ensure_repo():
    """Make sure we have a git repo to work with.

    If running from the install dir (not a git repo), clone fresh.
    """
    test = _git("rev-parse", "--git-dir")
    if test.returncode == 0:
        return True

    # Running from /opt/dnd-table which isn't a git repo — clone one
    clone_dir = "/home/dndtable/dnd-table"
    if os.path.isdir(os.path.join(clone_dir, ".git")):
        global REPO_DIR
        REPO_DIR = clone_dir
        return True

    # No repo found anywhere — clone it
    log.info("No git repo found, cloning from %s", REPO_URL)
    try:
        result = subprocess.run(
            ["git", "clone", REPO_URL, clone_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            REPO_DIR = clone_dir
            return True
        log.error("git clone failed: %s", result.stderr.strip())
    except Exception as e:
        log.error("git clone error: %s", e)
    return False


def check_for_update():
    """Check if a newer version is available on the remote.

    Returns dict with:
      available (bool) — True if remote has new commits
      current  (str)   — short hash of current HEAD
      latest   (str)   — short hash of remote HEAD
      commits  (list)  — list of {hash, subject, date} for new commits
      error    (str)   — error message if something went wrong
    """
    if not _ensure_repo():
        return {"available": False, "error": "No git repository found"}

    # Fetch latest from remote
    fetch = _git("fetch", REMOTE, BRANCH, timeout=30)
    if fetch.returncode != 0:
        return {"available": False, "error": "Failed to reach GitHub: " + fetch.stderr.strip()}

    # Current local HEAD
    local = _git("rev-parse", "--short", "HEAD")
    local_hash = local.stdout.strip() if local.returncode == 0 else "unknown"

    local_full = _git("rev-parse", "HEAD")
    local_full_hash = local_full.stdout.strip() if local_full.returncode == 0 else ""

    # Remote HEAD
    remote = _git("rev-parse", "--short", f"{REMOTE}/{BRANCH}")
    remote_hash = remote.stdout.strip() if remote.returncode == 0 else "unknown"

    remote_full = _git("rev-parse", f"{REMOTE}/{BRANCH}")
    remote_full_hash = remote_full.stdout.strip() if remote_full.returncode == 0 else ""

    if local_full_hash == remote_full_hash:
        return {"available": False, "current": local_hash, "latest": remote_hash,
                "commits": []}

    # List new commits
    log_result = _git(
        "log", f"HEAD..{REMOTE}/{BRANCH}",
        "--pretty=format:%h|%s|%cr", "--no-merges",
    )
    commits = []
    if log_result.returncode == 0 and log_result.stdout.strip():
        for line in log_result.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0], "subject": parts[1], "date": parts[2]})

    return {
        "available": True,
        "current": local_hash,
        "latest": remote_hash,
        "commits": commits,
    }


def _ensure_venv():
    """Recreate the install-dir venv if missing and refresh requirements.

    Mirrors step 6 of install.sh.  The Flask service runs as the
    ``dndtable`` user, which owns /opt/dnd-table, so no sudo is needed
    here to write into the install directory.

    Returns (ok, err) and never raises — failure is reported via the
    bool.  ``apply_update`` treats a False return as fatal, since the
    next service restart would fail anyway: both kiosk.sh and
    dnd-table.service expect /opt/dnd-table/.venv/bin/python.
    """
    venv_dir = os.path.join(INSTALL_DIR, ".venv")
    venv_python = os.path.join(venv_dir, "bin", "python")

    if not os.path.isfile(venv_python):
        log.info("Recreating venv at %s", venv_dir)
        try:
            r = subprocess.run(
                ["python3", "-m", "venv", venv_dir, "--system-site-packages"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                return False, "venv create failed: " + r.stderr.strip()
        except Exception as e:
            return False, f"venv create error: {e}"
        # Upgrade pip inside the fresh venv (best effort)
        subprocess.run(
            [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True, timeout=120,
        )

    req = os.path.join(INSTALL_DIR, "requirements.txt")
    if os.path.isfile(req):
        log.info("Installing requirements from %s", req)
        try:
            r = subprocess.run(
                [venv_python, "-m", "pip", "install", "-r", req],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode != 0:
                return False, "pip install failed: " + r.stderr.strip()
        except Exception as e:
            return False, f"pip install error: {e}"

    return True, ""


def _refresh_music_output():
    """Best-effort refresh of the headless music-output client.

    The client lives outside the repo (/opt/music-output, owned by the
    service user) and tracks upstream pjunak/music, so re-fetch it on
    update.  Non-fatal: the existing copy keeps working if the download
    fails, and the whole step is skipped on boxes without the output.
    """
    if not os.path.isdir(os.path.dirname(_MUSIC_OUTPUT_PY)):
        return
    try:
        # The dir is owned by the service user, so refreshing the client
        # itself needs no sudo; only the unit file (in /etc) needs root.
        subprocess.run(
            ["curl", "-fsSL", _MUSIC_OUTPUT_URL, "-o", _MUSIC_OUTPUT_PY],
            capture_output=True, timeout=30,
        )
        subprocess.run(
            ["sudo", "cp", f"{INSTALL_DIR}/system/music-output.service",
             "/etc/systemd/system/music-output.service"],
            capture_output=True, timeout=5,
        )
    except Exception as e:
        log.warning("music-output refresh failed: %s", e)


def apply_update():
    """Pull the latest code and deploy to the install directory.

    Returns dict with:
      ok    (bool) — True if update succeeded
      error (str)  — error message on failure
    """
    if not _ensure_repo():
        return {"ok": False, "error": "No git repository found"}

    # Pull latest
    pull = _git("pull", REMOTE, BRANCH, timeout=60)
    if pull.returncode != 0:
        # Try reset if local changes conflict
        _git("reset", "--hard", f"{REMOTE}/{BRANCH}")
        pull = _git("pull", REMOTE, BRANCH, timeout=60)
        if pull.returncode != 0:
            return {"ok": False, "error": "git pull failed: " + pull.stderr.strip()}

    # Build rsync command
    rsync_cmd = [
        "sudo", "rsync", "-a", "--delete",
    ]
    for exc in _RSYNC_EXCLUDES:
        rsync_cmd += ["--exclude", exc]
    rsync_cmd += [REPO_DIR.rstrip("/") + "/", INSTALL_DIR + "/"]

    try:
        result = subprocess.run(
            rsync_cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": "rsync failed: " + result.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": f"rsync error: {e}"}

    # Fix ownership
    subprocess.run(
        ["sudo", "chown", "-R", "dndtable:dndtable", INSTALL_DIR],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["sudo", "chmod", "+x", f"{INSTALL_DIR}/kiosk.sh"],
        capture_output=True, timeout=5,
    )

    # Ensure the venv exists and requirements are up to date.  Without
    # this the kiosk falls through to the chromium GPU-probe fallback
    # because /opt/dnd-table/.venv/bin/python is missing or stale.
    ok, err = _ensure_venv()
    if not ok:
        return {"ok": False, "error": err}

    # Refresh the headless music-output client (lives outside the repo).
    _refresh_music_output()

    # Reload service files in case they changed
    subprocess.run(
        ["sudo", "cp", f"{INSTALL_DIR}/dnd-table.service",
         "/etc/systemd/system/dnd-table.service"],
        capture_output=True, timeout=5,
    )
    subprocess.run(
        ["sudo", "systemctl", "daemon-reload"],
        capture_output=True, timeout=10,
    )
    # Pick up a refreshed music client (best-effort; the Flask service
    # itself is restarted by the /update/apply route).
    subprocess.run(
        ["sudo", "systemctl", "restart", "music-output.service"],
        capture_output=True, timeout=10,
    )

    log.info("Update applied from %s to %s", REPO_DIR, INSTALL_DIR)
    return {"ok": True}
