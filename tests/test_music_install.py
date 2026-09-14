import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from music_install import (ARCHIVE, ENVIRONMENT, MANAGED, SERVICE, download_release,
                           install, rollback, unpack_package)

SHA = "a" * 40
BINARY = b"verified executable"
UNIT = b"[Service]\nUser=dndtable\nExecStart=/opt/music-output/music-output\n"


def package(revision=SHA, extra=None, symlink=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, body in {"music-output": BINARY, "REVISION": f"{revision}\n".encode(),
                           "README.md": b"instructions", SERVICE: UNIT, **(extra or {})}.items():
            item = tarfile.TarInfo(name)
            item.size = len(body)
            if symlink and name == "music-output":
                item.type = tarfile.SYMTYPE
                item.linkname = "/etc/passwd"
                item.size = 0
                archive.addfile(item)
            else:
                archive.addfile(item, io.BytesIO(body))
    data = output.getvalue()
    return data, f"{hashlib.sha256(data).hexdigest()}  {ARCHIVE}\n".encode()


def test_download_is_pinned_to_one_public_tested_release():
    data, checksum = package()
    tag = f"music-output-{SHA}"
    base = f"https://github.com/pjunak/music/releases/download/{tag}/"
    requested = []
    def reader(url, limit):
        requested.append(url)
        if url.endswith("/latest"):
            return json.dumps({"tag_name": tag, "draft": False, "prerelease": False,
                               "assets": [{"name": name, "browser_download_url": base + name}
                                          for name in (ARCHIVE, "SHA256SUMS")]}).encode()
        return {base + ARCHIVE: data, base + "SHA256SUMS": checksum}[url]
    assert download_release(None, reader) == (SHA, BINARY)
    assert requested[1:] == [base + ARCHIVE, base + "SHA256SUMS"]


def test_corrupt_or_wrong_revision_package_is_rejected():
    data, checksum = package()
    with pytest.raises(ValueError, match="checksum"):
        unpack_package(data + b"corrupt", checksum, SHA)
    with pytest.raises(ValueError, match="revision"):
        unpack_package(data, checksum, "b" * 40)


@pytest.mark.parametrize("extra,symlink", [({"../outside": b"escape"}, False), (None, True)])
def test_archive_paths_and_links_cannot_write_outside_package(extra, symlink):
    with pytest.raises(ValueError, match="Unexpected|unsupported"):
        unpack_package(*package(extra=extra, symlink=symlink), SHA)


def test_unpublished_or_foreign_downloads_are_rejected():
    def reader(url, limit):
        return json.dumps({"tag_name": f"music-output-{SHA}", "assets": []}).encode()
    with pytest.raises(ValueError, match="missing"):
        download_release(None, reader)
    with pytest.raises(ValueError, match="full 40"):
        download_release("main", reader)


class Service:
    def __init__(self, active=False, enabled=False):
        self.active, self.enabled = active, enabled
        self.fail_restart = False
        self.incompatible = False
        self.calls = []

    def __call__(self, *args, check=True):
        self.calls.append(args)
        if args[0] != "systemctl":
            if self.incompatible:
                raise RuntimeError("binary is incompatible")
            assert Path(args[0]).read_bytes() == BINARY
            return SimpleNamespace(returncode=0)
        action = args[1]
        if action == "restart" and self.fail_restart:
            self.active = False
            raise RuntimeError("new service failed")
        if action in ("start", "restart"):
            self.active = True
        if action == "stop":
            self.active = False
        if action == "enable":
            self.enabled = True
        if action == "disable":
            self.enabled = False
        code = int((action == "is-active" and not self.active) or
                   (action == "is-enabled" and not self.enabled))
        if check and code:
            raise RuntimeError("service is not running")
        return SimpleNamespace(returncode=code)


def put(root, name, body):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_first_install_then_explicit_rollback(tmp_path):
    service = Service()
    install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    assert (tmp_path / MANAGED[0]).read_bytes() == BINARY
    assert service.active and service.enabled
    assert (tmp_path / ENVIRONMENT).exists()
    rollback(tmp_path, service)
    assert not (tmp_path / MANAGED[0]).exists()
    assert not service.active and not service.enabled
    assert (tmp_path / ENVIRONMENT).exists()  # current settings are always retained


def test_update_and_rollback_preserve_settings_identity_and_legacy_files(tmp_path):
    originals = (b"old binary", b"old revision", b"legacy unit")
    for relative, data in zip(MANAGED, originals):
        put(tmp_path, relative, data)
    env = put(tmp_path, ENVIRONMENT, b"MUSIC_CLIENT_ID=original\nMUSIC_CONTROL_TOKEN=fixture-only\n")
    identity = put(tmp_path, "home/dndtable/.config/music-output/client-id", b"original-device")
    legacy = put(tmp_path, "opt/music-output/legacy-client", b"legacy executable")
    service = Service(active=True, enabled=True)
    install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    env.write_bytes(b"operator edited settings after installation")
    rollback(tmp_path, service)
    assert [ (tmp_path / item).read_bytes() for item in MANAGED ] == list(originals)
    assert env.read_bytes() == b"operator edited settings after installation"
    assert identity.read_bytes() == b"original-device"
    assert legacy.read_bytes() == b"legacy executable"
    assert service.active and service.enabled
    assert all("dnd-table.service" not in args for args in service.calls)


def test_startup_failure_restores_previous_player(tmp_path):
    for relative in MANAGED:
        put(tmp_path, relative, b"previous")
    env = put(tmp_path, ENVIRONMENT, b"preserved settings")
    service = Service(active=True, enabled=True)
    service.fail_restart = True
    with pytest.raises(RuntimeError, match="new service"):
        install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    assert all((tmp_path / relative).read_bytes() == b"previous" for relative in MANAGED)
    assert env.read_bytes() == b"preserved settings"
    assert service.active and service.enabled


def test_incompatible_binary_never_stops_running_service(tmp_path):
    old = put(tmp_path, MANAGED[0], b"old binary")
    service = Service(active=True)
    service.incompatible = True
    with pytest.raises(RuntimeError, match="incompatible"):
        install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    assert old.read_bytes() == b"old binary"
    assert all(args[0] != "systemctl" for args in service.calls)


def test_first_install_failure_removes_only_new_config_and_files(tmp_path):
    service = Service()
    service.fail_restart = True
    with pytest.raises(RuntimeError):
        install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    assert not (tmp_path / ENVIRONMENT).exists()
    assert all(not (tmp_path / relative).exists() for relative in MANAGED)
    assert not service.active and not service.enabled


def test_reinstalling_current_package_preserves_the_previous_rollback(tmp_path):
    service = Service()
    install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    before = (tmp_path / "var/backups/music-output/previous").read_bytes()
    service.calls.clear()
    install(tmp_path, SHA, BINARY, UNIT, service, lambda seconds: None)
    assert (tmp_path / "var/backups/music-output/previous").read_bytes() == before
    assert service.calls == []
