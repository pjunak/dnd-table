"""The update boundary is the successful commit selected by the owner."""
from types import SimpleNamespace
import io
import subprocess
import tarfile

import updater
from tested_updates import successful_commits

OLD = "a" * 40
NEW = "b" * 40


def run(sha=NEW, number=2, **changes):
    value = dict(head_sha=sha, run_number=number, status="completed", conclusion="success",
                 event="push", head_branch="main", path=".github/workflows/ci.yml",
                 head_repository={"full_name": "pjunak/dnd-table"})
    value.update(changes)
    return value


def test_successful_main_push_only_and_original_run_order():
    payload = {"workflow_runs": [run(OLD, 1), run(NEW, 2), run("c" * 40, 4, event="pull_request"),
        run("d" * 40, 5, conclusion="failure"), run("e" * 40, 6, status="in_progress"),
        run("f" * 40, 7, head_repository={"full_name": "other/fork"}),
        run("g" * 40, 8), run("1" * 40, 9, path=".github/workflows/other.yml")]}
    assert successful_commits(payload) == [NEW, OLD]


def test_skip_successful_commit_removed_from_main(monkeypatch):
    monkeypatch.setattr(updater, "_ensure_repo", lambda: True)
    monkeypatch.setattr(updater, "fetch_successful_commits", lambda: [NEW, OLD])
    monkeypatch.setattr(updater, "_git", lambda *args, **kw: SimpleNamespace(
        returncode=1 if args[:3] == ("merge-base", "--is-ancestor", NEW) else 0))
    assert updater._tested_target() == OLD


def test_no_github_verification_means_no_install(monkeypatch):
    monkeypatch.setattr(updater, "_tested_target", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(updater, "_deploy_commit", lambda sha: (_ for _ in ()).throw(AssertionError()))
    assert updater.apply_update(NEW) == {"ok": False, "error": "offline"}


def test_selected_commit_cannot_change_between_check_and_apply(monkeypatch):
    monkeypatch.setattr(updater, "_tested_target", lambda: NEW)
    monkeypatch.setattr(updater, "_deploy_commit", lambda sha: (_ for _ in ()).throw(AssertionError()))
    assert "changed" in updater.apply_update(OLD)["error"]
    assert not updater.apply_update("main")["ok"]
    assert not updater.apply_update(None)["ok"]


def test_update_installs_exact_sha_and_prevents_downgrade(monkeypatch):
    deployed = []
    monkeypatch.setattr(updater, "_tested_target", lambda: NEW)
    monkeypatch.setattr(updater, "_installed_revision", lambda: OLD)
    monkeypatch.setattr(updater, "_git", lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.setattr(updater, "_deploy_commit", deployed.append)
    assert updater.apply_update(NEW) == {"ok": True, "sha": NEW}
    assert deployed == [NEW]
    monkeypatch.setattr(updater, "_git", lambda *a, **k: SimpleNamespace(returncode=0))
    assert not updater.apply_update(NEW)["ok"]
    assert deployed == [NEW]


def test_installed_marker_not_source_checkout_controls_current(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_tested_target", lambda: NEW)
    monkeypatch.setattr(updater, "_git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert updater.check_for_update()["current"] == "unknown"
    (tmp_path / ".installed-revision").write_text(OLD)
    result = updater.check_for_update()
    assert result["available"] and result["sha"] == NEW and result["current"] == OLD[:7]


def test_export_is_exact_and_venv_failure_does_not_claim_success(tmp_path, monkeypatch):
    calls = []
    def git(*args, **kwargs):
        calls.append(args)
        assert args[0] == "archive" and args[-1] == NEW
        with tarfile.open(args[3], "w") as archive:
            data = b"tested source"
            entry = tarfile.TarInfo("tracked.py")
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        return SimpleNamespace(returncode=0)
    def command(args, **kwargs):
        calls.append(args)
        if "rsync" in args:
            from pathlib import Path
            assert Path(args[-2], "tracked.py").read_bytes() == b"tested source"
            for protected in [".venv", "settings.json", ".installed-revision"]:
                assert protected in args
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(updater, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "_git", git)
    monkeypatch.setattr(updater.subprocess, "run", command)
    monkeypatch.setattr(updater, "_ensure_venv", lambda: (False, "requirements failed"))
    import pytest
    with pytest.raises(ValueError, match="requirements failed"):
        updater._deploy_commit(NEW)
    assert not (tmp_path / ".installed-revision").exists()
    assert not any("pull" in call or "reset" in call or "music-output.service" in call for call in calls)
    monkeypatch.setattr(updater, "_ensure_venv", lambda: (True, ""))
    updater._deploy_commit(NEW)
    assert (tmp_path / ".installed-revision").read_text().strip() == NEW
