from __future__ import annotations

import fnmatch
import re
import time
from pathlib import Path
from typing import Any

from app.services.repo_indexer import BINARY_EXTENSIONS, IGNORED_DIRS


MAX_READ_LINES = 200
MAX_TEXT_FILE_BYTES = 500_000
TEXT_EXTENSIONS = {
    ".css",
    ".csv",
    ".env",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".properties",
    ".py",
    ".sql",
    ".ts",
    ".tsx",
    ".toml",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)((?:api[_-]?key|apikey|secret|token|password|private_key)\s*[:=]\s*['\"]?)([^'\"\s,}]+)"),
)


def _repo_root(repo_root: str | Path) -> Path:
    root = Path(repo_root).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    return root


def _safe_path(repo_root: str | Path, relative_path: str | Path = ".") -> Path:
    root = _repo_root(repo_root)
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes repository root") from exc
    return target


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _has_ignored_part(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part.lower() in IGNORED_DIRS for part in parts)


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return False
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return path.suffix.lower() in TEXT_EXTENSIONS
    return True


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _redact_sensitive(text: str) -> str:
    redacted = text
    redacted = SENSITIVE_PATTERNS[0].sub("[redacted-api-key]", redacted)
    redacted = SENSITIVE_PATTERNS[1].sub("[redacted-github-token]", redacted)
    redacted = SENSITIVE_PATTERNS[2].sub(lambda match: f"{match.group(1)}[redacted]", redacted)
    return redacted


def _matches_any_glob(relative_path: str, include_globs: list[str] | None) -> bool:
    if not include_globs:
        return True
    normalized = relative_path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, glob) or fnmatch.fnmatch(Path(normalized).name, glob) for glob in include_globs)


def list_dir_safe(repo_root: str | Path, relative_path: str = ".") -> list[dict[str, Any]]:
    root = _repo_root(repo_root)
    directory = _safe_path(root, relative_path)
    if _has_ignored_part(directory, root):
        return []
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("path is not a directory")

    entries: list[dict[str, Any]] = []
    for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if _has_ignored_part(child, root):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": _relative(child, root),
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else 0,
            }
        )
    return entries


def read_file_safe(
    repo_root: str | Path,
    relative_path: str,
    start_line: int = 1,
    max_lines: int = MAX_READ_LINES,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    file_path = _safe_path(root, relative_path)
    if _has_ignored_part(file_path, root):
        raise ValueError("path is ignored")
    if not file_path.exists():
        raise ValueError("file does not exist")
    if not file_path.is_file():
        raise ValueError("path is not a file")
    if not _is_text_file(file_path):
        raise ValueError("file is not a supported text file")

    start = max(1, int(start_line))
    limit = max(1, min(int(max_lines), MAX_READ_LINES))
    lines = _read_text(file_path).splitlines()
    selected = lines[start - 1 : start - 1 + limit]
    line_end = start + len(selected) - 1 if selected else start - 1
    return {
        "file": _relative(file_path, root),
        "line_start": start,
        "line_end": line_end,
        "total_lines": len(lines),
        "content": _redact_sensitive("\n".join(selected)),
        "truncated": start - 1 + limit < len(lines),
    }


def grep_safe(
    repo_root: str | Path,
    pattern: str,
    include_globs: list[str] | None = None,
    max_matches: int = 50,
    timeout: float = 5,
) -> list[dict[str, Any]]:
    root = _repo_root(repo_root)
    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid grep pattern: {exc}") from exc

    matches: list[dict[str, Any]] = []
    for file_path in sorted(root.rglob("*")):
        if time.monotonic() > deadline:
            break
        if len(matches) >= max_matches:
            break
        if not file_path.is_file() or _has_ignored_part(file_path, root):
            continue
        rel = _relative(file_path, root)
        if not _matches_any_glob(rel, include_globs):
            continue
        if not _is_text_file(file_path):
            continue
        try:
            lines = _read_text(file_path).splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if time.monotonic() > deadline or len(matches) >= max_matches:
                break
            match = regex.search(line)
            if not match:
                continue
            matches.append(
                {
                    "file": rel,
                    "line": line_number,
                    "snippet": _redact_sensitive(line.strip())[:500],
                    "match": _redact_sensitive(match.group(0))[:160],
                }
            )
    return matches


def find_files_safe(repo_root: str | Path, pattern: str, max_results: int = 50) -> list[str]:
    root = _repo_root(repo_root)
    query = pattern.lower()
    has_glob = any(char in pattern for char in "*?[]")
    results: list[str] = []
    for file_path in sorted(root.rglob("*")):
        if len(results) >= max_results:
            break
        if not file_path.is_file() or _has_ignored_part(file_path, root):
            continue
        rel = _relative(file_path, root)
        candidate = rel.lower()
        name = file_path.name.lower()
        matched = fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(name, pattern) if has_glob else query in candidate
        if matched:
            results.append(rel)
    return results
