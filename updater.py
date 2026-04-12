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

_RSYNC_EXCLUDES = (
    ".git", "__pycache__", ".vscode", ".claude", ".gitignore",
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
    clone_dir = "/home/dnd/dnd-table"
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

    # Fix ownership and permissions
    subprocess.run(
        ["sudo", "chown", "-R", "dnd:dnd", INSTALL_DIR],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["sudo", "chmod", "+x", f"{INSTALL_DIR}/setup-display.sh"],
        capture_output=True, timeout=5,
    )

    # Reload service file in case it changed
    subprocess.run(
        ["sudo", "cp", f"{INSTALL_DIR}/dnd-table.service",
         "/etc/systemd/system/dnd-table.service"],
        capture_output=True, timeout=5,
    )
    subprocess.run(
        ["sudo", "systemctl", "daemon-reload"],
        capture_output=True, timeout=10,
    )

    log.info("Update applied from %s to %s", REPO_DIR, INSTALL_DIR)
    return {"ok": True}
