"""Install an owner-selected, tested Music output release independently of the table.

GitHub downloads are public. This installer never reads a personal access token,
changes the table service, or writes the output's stable identity. Configuration
and identity stay with the existing dndtable account. Rollback restores the previous
binary and unit, including a legacy unit while its old files remain on disk.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

REPOSITORY = "pjunak/music"
ARCHIVE = "music-output-linux-x86_64.tar.gz"
SERVICE = "music-output.service"
MANAGED = ("opt/music-output/music-output", "opt/music-output/REVISION",
           "etc/systemd/system/music-output.service")
ENVIRONMENT = "etc/music-output.env"
HISTORY = "var/backups/music-output"
TAG = re.compile(r"music-output-([a-f0-9]{40})\Z")


def fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dnd-table-music-installer",
                                                  "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Download was redirected outside HTTPS")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Download exceeded the package size limit")
    return body


def unpack_package(archive: bytes, checksums: bytes, revision: str) -> bytes:
    expected = f"{hashlib.sha256(archive).hexdigest()}  {ARCHIVE}\n".encode()
    if checksums != expected:
        raise ValueError("Music download checksum did not match; installation unchanged")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        members = package.getmembers()
        names = {member.name for member in members}
        if len(members) != 4 or names != {"music-output", "REVISION", "README.md", SERVICE}:
            raise ValueError("Unexpected files in Music package")
        if any(not member.isfile() or member.size > 128 * 1024 * 1024 for member in members):
            raise ValueError("Music package contains an unsupported file")
        if package.extractfile("REVISION").read() != f"{revision}\n".encode():
            raise ValueError("Music package source revision did not match")
        return package.extractfile("music-output").read()


def download_release(tag: str | None, reader=fetch) -> tuple[str, bytes]:
    if tag and not TAG.fullmatch(tag):
        raise ValueError("Use latest or music-output- followed by a full 40-character commit ID")
    suffix = f"tags/{tag}" if tag else "latest"
    release = json.loads(reader(f"https://api.github.com/repos/{REPOSITORY}/releases/{suffix}", 1024 * 1024))
    match = TAG.fullmatch(release.get("tag_name", ""))
    if not match or release.get("draft") or release.get("prerelease") or (tag and release["tag_name"] != tag):
        raise ValueError("GitHub did not return a published Music output release")
    tag = release["tag_name"]
    base = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    for name in (ARCHIVE, "SHA256SUMS"):
        if assets.get(name, {}).get("browser_download_url") != base + name:
            raise ValueError("Music release download is missing or points outside its source release")
    archive = reader(base + ARCHIVE, 128 * 1024 * 1024)
    checksums = reader(base + "SHA256SUMS", 1024)
    return match[1], unpack_package(archive, checksums, match[1])


def command(*args: str, check: bool = True):
    # No environment or service journal is included in errors: either may contain secrets.
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args[:2])} failed (exit {result.returncode})")
    return result


def replace_file(path: Path, content: bytes, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def snapshot(root: Path, run=command) -> Path:
    history = root / HISTORY
    history.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=history))
    state = {"active": run("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0,
             "enabled": run("systemctl", "is-enabled", "--quiet", SERVICE, check=False).returncode == 0,
             "files": {}}
    for index, relative in enumerate(MANAGED):
        path = root / relative
        if path.is_symlink():
            raise ValueError(f"Managed path is a symlink: {relative}")
        state["files"][relative] = path.exists()
        if path.exists():
            shutil.copy2(path, backup / str(index))
    replace_file(backup / "state.json", json.dumps(state).encode(), 0o600)
    # Recorded before service changes, so an interrupted install can also be rolled back.
    replace_file(history / "previous", backup.name.encode(), 0o600)
    return backup


def restore(root: Path, backup: Path, run=command):
    state = json.loads((backup / "state.json").read_text())
    if set(state.get("files", {})) != set(MANAGED):
        raise ValueError("Rollback manifest is invalid")
    run("systemctl", "stop", SERVICE, check=False)
    for index, relative in enumerate(MANAGED):
        path = root / relative
        if state["files"][relative]:
            source = backup / str(index)
            replace_file(path, source.read_bytes(), source.stat().st_mode & 0o777)
        else:
            path.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable" if state["enabled"] else "disable", SERVICE,
        check=state["files"][MANAGED[2]])
    if state["active"]:
        run("systemctl", "start", SERVICE)
        run("systemctl", "is-active", "--quiet", SERVICE)


def rollback(root: Path, run=command):
    history = root / HISTORY
    name = (history / "previous").read_text()
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-z0-9_]+", name):
        raise ValueError("Rollback reference is invalid")
    restore(root, history / name, run)
    print("MUSIC_OUTPUT_ROLLED_BACK: previous player and service restored; current settings retained")


def install(root: Path, revision: str, binary: bytes, unit: bytes, run=command, pause=time.sleep):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Invalid source revision")
    current = root / MANAGED[1]
    if (all((root / name).is_file() for name in MANAGED)
            and current.read_text().strip() == revision
            and (root / MANAGED[0]).read_bytes() == binary
            and (root / MANAGED[2]).read_bytes() == unit):
        print(f"MUSIC_OUTPUT_CURRENT: {revision}; no changes needed")
        return
    # Test runtime compatibility before stopping an existing player.
    with tempfile.TemporaryDirectory(prefix="music-output-check-") as directory:
        candidate = Path(directory) / "music-output"
        candidate.write_bytes(binary)
        candidate.chmod(0o755)
        run(str(candidate), "--version")
    backup = snapshot(root, run)
    print(f"Music output {revision}; backup: {backup}", flush=True)
    created_environment = False
    try:
        # Keep all configured server/name/token/state-dir values byte-for-byte.
        environment = root / ENVIRONMENT
        if not environment.exists():
            replace_file(environment, b"MUSIC_SERVER_URL=https://music.junak.eu\nMUSIC_OUTPUT_NAME=DnD Table\nMUSIC_CONTROL_PORT=8731\n", 0o600)
            created_environment = True
        state = json.loads((backup / "state.json").read_text())
        if state["active"]:
            run("systemctl", "stop", SERVICE)
        replace_file(root / MANAGED[0], binary, 0o755)
        replace_file(root / MANAGED[1], f"{revision}\n".encode(), 0o644)
        replace_file(root / MANAGED[2], unit, 0o644)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", SERVICE)
        run("systemctl", "restart", SERVICE)
        pause(3)
        run("systemctl", "is-active", "--quiet", SERVICE)
    except BaseException:
        if created_environment:
            (root / ENVIRONMENT).unlink(missing_ok=True)
        restore(root, backup, run)
        raise
    print(f"MUSIC_OUTPUT_INSTALLED: {revision}\nRollback: bash install-music.sh --rollback\nService is running. Speaker playback still needs a hardware check.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", nargs="?", default="latest", help="latest or music-output-<full commit ID>")
    parser.add_argument("--rollback", action="store_true", help="restore the previous player and service, retaining current settings")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() != 0:
        parser.error("Run through bash install-music.sh on the Linux x86-64 table (sudo required)")
    import fcntl
    import pwd
    with open("/run/lock/music-output-install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            rollback(Path("/"))
            return
        account = pwd.getpwnam("dndtable")
        if account.pw_uid == 0 or not re.fullmatch(r"/[A-Za-z0-9_./-]+", account.pw_dir):
            raise ValueError("The dndtable account requires a normal home directory and a non-root UID")
        if not shutil.which("mpv"):
            raise ValueError("Install the audio dependency first: sudo apt install mpv ca-certificates")
        revision, binary = download_release(None if args.release == "latest" else args.release)
        unit = (Path(__file__).parent / "system/music-output.service").read_text()
        unit = unit.replace("@HOME@", account.pw_dir).replace("@UID@", str(account.pw_uid))
        install(Path("/"), revision, binary, unit.encode())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Music installation failed: {error}", file=sys.stderr)
        sys.exit(1)
