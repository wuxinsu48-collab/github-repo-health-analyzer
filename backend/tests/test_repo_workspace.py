from pathlib import Path
import os
import stat

import pytest

from app.services import repo_workspace
from app.services.repo_workspace import RepositoryCloneError, cleanup_repository, cleanup_stale_workspaces


def test_cleanup_repository_removes_only_workspace_child(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspaces"
    child = workspace / "owner-repo-123"
    child.mkdir(parents=True)
    (child / "README.md").write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr(repo_workspace, "WORKSPACE_ROOT", workspace)

    cleanup_repository(child)

    assert not child.exists()


def test_cleanup_repository_rejects_paths_outside_workspace(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspaces"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.setattr(repo_workspace, "WORKSPACE_ROOT", workspace)

    with pytest.raises(RepositoryCloneError):
        cleanup_repository(outside)

    assert outside.exists()


def test_cleanup_repository_removes_readonly_files_on_windows(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspaces"
    child = workspace / "owner-repo-123"
    child.mkdir(parents=True)
    readonly = child / "pack.idx"
    readonly.write_text("pack", encoding="utf-8")
    os.chmod(readonly, stat.S_IREAD)
    monkeypatch.setattr(repo_workspace, "WORKSPACE_ROOT", workspace)

    cleanup_repository(child)

    assert not child.exists()


def test_cleanup_stale_workspaces_removes_workspace_directories(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspaces"
    stale_a = workspace / "owner-repo-aaa"
    stale_b = workspace / "owner-repo-bbb"
    stale_a.mkdir(parents=True)
    stale_b.mkdir()
    (stale_a / "README.md").write_text("# stale\n", encoding="utf-8")
    (workspace / "note.txt").write_text("kept", encoding="utf-8")
    monkeypatch.setattr(repo_workspace, "WORKSPACE_ROOT", workspace)

    removed = cleanup_stale_workspaces()

    assert removed == 2
    assert not stale_a.exists()
    assert not stale_b.exists()
    assert (workspace / "note.txt").exists()
