from __future__ import annotations

import re
import shutil
import subprocess
import uuid
import os
import stat
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "workspaces"
_SAFE_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


class RepositoryCloneError(Exception):
    pass


def _validate_repo_part(value: str, label: str) -> None:
    if not value or not _SAFE_REPO_PART.fullmatch(value):
        raise RepositoryCloneError(f"{label} 包含不安全字符，无法 clone")


def clone_repository(owner: str, repo: str, repo_url: str | None = None) -> Path:
    _validate_repo_part(owner, "owner")
    _validate_repo_part(repo, "repo")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    target = WORKSPACE_ROOT / f"{owner}-{repo}-{uuid.uuid4().hex[:10]}"
    clone_url = f"https://github.com/{owner}/{repo}.git"
    command = ["git", "clone", "--depth", "1", "--single-branch", clone_url, str(target)]

    try:
        result = subprocess.run(
            command,
            cwd=WORKSPACE_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositoryCloneError("本机没有找到 git 命令，无法进行深度分析") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryCloneError("clone 仓库超时，请稍后重试或换一个更小的仓库") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RepositoryCloneError(f"clone 仓库失败: {detail[:300]}")

    return target


def cleanup_repository(path: Path) -> None:
    workspace = WORKSPACE_ROOT.resolve()
    target = path.resolve()
    if target == workspace or workspace not in target.parents:
        raise RepositoryCloneError("拒绝清理工作区之外的路径")
    if target.exists():
        def reset_permissions_and_retry(function, item_path, exc):
            try:
                os.chmod(item_path, stat.S_IWRITE)
                function(item_path)
            except OSError:
                raise exc

        shutil.rmtree(target, onexc=reset_permissions_and_retry)
