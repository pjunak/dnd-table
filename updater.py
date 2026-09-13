"""
DnD Table – Self-update from GitHub.

Checks successful GitHub CI runs, exports the exact selected commit, and
deploys it to /opt/dnd-table. The source clone and local edits stay untouched.

The install directory (/opt/dnd-table) is a plain copy — not a git repo.
The git repo lives wherever the project was originally cloned (typically
/home/dnd/dnd-table or similar).  On first install via install.sh the
repo path is embedded; on subsequent updates we resolve it at runtime.
"""

import logging
import os
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path

from tested_updates import fetch_successful_commits, valid_sha

log = logging.getLogger(__name__)

INSTALL_DIR = "/opt/dnd-table"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE = "origin"
BRANCH = "main"
REPO_URL = "https://github.com/pjunak/dnd-table"

_UPDATE_LOCK = threading.Lock()

# Must match install.sh — otherwise `rsync --delete` will wipe the venv,
# user PNGs, and on-disk settings every time the updater runs.
_RSYNC_EXCLUDES = (
    ".git", "__pycache__", ".vscode", ".gitignore",
    ".venv", "*.png", "settings.json", ".installed-revision",
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


def _tested_target():
    if not _ensure_repo():
        raise ValueError("No git repository found")
    fetch = _git("fetch", REMOTE, BRANCH, timeout=60)
    if fetch.returncode:
        raise ValueError("Failed to fetch the update repository")
    for sha in fetch_successful_commits():
        if _git("merge-base", "--is-ancestor", sha, f"{REMOTE}/{BRANCH}").returncode == 0:
            return sha
    raise ValueError("No tested commit belongs to the current main branch")


def _installed_revision():
    try:
        sha = Path(INSTALL_DIR, ".installed-revision").read_text().strip()
        return sha if valid_sha(sha) else ""
    except OSError:
        return ""


def check_for_update():
    """Offer the newest successful main commit, never the moving branch tip."""
    if not _UPDATE_LOCK.acquire(blocking=False):
        return {"available": False, "error": "An update is already running"}
    try:
        target = _tested_target()
        current = _installed_revision()
        # A delayed CI rerun must not downgrade a newer installation.
        newer_installed = bool(current and _git(
            "merge-base", "--is-ancestor", target, current).returncode == 0)
        commits = []
        history = _git("log", f"{current}..{target}" if current else target,
                       "-n", "20", "--pretty=format:%h|%s|%cr", "--no-merges")
        if history.returncode == 0:
            for line in history.stdout.splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    commits.append(dict(zip(("hash", "subject", "date"), parts)))
        return {"available": not newer_installed, "current": current[:7] or "unknown",
                "latest": target[:7], "sha": target, "commits": commits}
    except Exception as error:
        log.warning("Update check failed: %s", error)
        return {"available": False, "error": str(error)}
    finally:
        _UPDATE_LOCK.release()


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


def _deploy_commit(sha):
    # Export tracked files from the chosen object. Never pull/reset the operator's
    # clone, and never deploy unrelated untracked files from that clone.
    with tempfile.TemporaryDirectory(prefix="dnd-table-update-") as scratch:
        archive = Path(scratch, "source.tar")
        source = Path(scratch, "source")
        source.mkdir()
        export = _git("archive", "--format=tar", "-o", str(archive), sha, timeout=60)
        if export.returncode:
            raise ValueError("Could not export the tested commit")
        with tarfile.open(archive) as package:
            package.extractall(source, filter="data")
        command = ["sudo", "rsync", "-a", "--delete"]
        for excluded in _RSYNC_EXCLUDES:
            command += ["--exclude", excluded]
        command += [str(source) + "/", INSTALL_DIR + "/"]
        subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
    subprocess.run(["sudo", "chown", "-R", "dndtable:dndtable", INSTALL_DIR],
                   capture_output=True, timeout=15, check=True)
    subprocess.run(["sudo", "chmod", "+x", f"{INSTALL_DIR}/kiosk.sh"],
                   capture_output=True, timeout=5, check=True)
    ok, error = _ensure_venv()
    if not ok:
        raise ValueError(error)
    subprocess.run(["sudo", "cp", f"{INSTALL_DIR}/dnd-table.service",
                    "/etc/systemd/system/dnd-table.service"],
                   capture_output=True, timeout=5, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"],
                   capture_output=True, timeout=10, check=True)
    marker = Path(INSTALL_DIR, ".installed-revision")
    pending = marker.with_suffix(".tmp")
    pending.write_text(sha + "\n", encoding="ascii")
    pending.replace(marker)


def apply_update(expected_sha):
    """Recheck CI and install the exact commit the owner selected in the panel."""
    if not valid_sha(expected_sha):
        return {"ok": False, "error": "Check for updates and select a tested commit first"}
    if not _UPDATE_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "An update is already running"}
    try:
        target = _tested_target()
        if target != expected_sha:
            raise ValueError("The available tested commit changed; check for updates again")
        current = _installed_revision()
        if current and _git("merge-base", "--is-ancestor", target, current).returncode == 0:
            raise ValueError("This commit is already installed or older than your installation")
        _deploy_commit(target)
        log.info("Installed tested commit %s", target)
        return {"ok": True, "sha": target}
    except Exception as error:
        log.exception("Update failed")
        return {"ok": False, "error": str(error)}
    finally:
        _UPDATE_LOCK.release()
