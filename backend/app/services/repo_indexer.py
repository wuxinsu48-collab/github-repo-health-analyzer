from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from app.models import EvidenceItem


IGNORED_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpg",
    ".jpeg",
    ".lockb",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".webp",
    ".zip",
}

MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "pipfile",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "composer.json",
    "gemfile",
}

DOC_NAMES = {"readme.md", "readme", "license", "license.md", "contributing.md", "security.md", "changelog.md"}
CONFIG_NAMES = {
    "dockerfile",
    "docker-compose.yml",
    ".editorconfig",
    ".eslintrc",
    ".prettierrc",
    "tsconfig.json",
    "vite.config.ts",
    "ruff.toml",
    "mypy.ini",
    "pytest.ini",
}
SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java", ".kt", ".cs", ".php", ".rb"}
TEST_MARKERS = ("test", "tests", "__tests__", "spec")
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
)


class RepoIndex(BaseModel):
    root: str
    tree: list[str]
    file_count: int
    directory_count: int
    total_bytes: int
    extension_counts: dict[str, int] = Field(default_factory=dict)
    source_files: list[str] = Field(default_factory=list)
    test_files: list[str] = Field(default_factory=list)
    documentation_files: list[str] = Field(default_factory=list)
    manifest_files: list[str] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    security_files: list[str] = Field(default_factory=list)
    snippets: list[EvidenceItem] = Field(default_factory=list)
    security_findings: list[EvidenceItem] = Field(default_factory=list)
    large_files: list[str] = Field(default_factory=list)
    has_tests: bool = False
    has_ci: bool = False
    has_docs: bool = False
    has_license: bool = False
    has_security_policy: bool = False


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_ignored_dir(path: Path) -> bool:
    return path.name.lower() in IGNORED_DIRS


def _is_probably_binary(path: Path, sample: bytes) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS or b"\0" in sample


def _is_text_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return False
    if _is_probably_binary(path, sample):
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _read_text(path: Path, limit: int = 2400) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _is_test_path(relative_path: str) -> bool:
    lower = relative_path.lower()
    parts = set(lower.split("/"))
    return bool(parts & set(TEST_MARKERS)) or lower.endswith((".test.ts", ".spec.ts", ".test.js", ".spec.js", "_test.py"))


def _is_ci_path(relative_path: str) -> bool:
    lower = relative_path.lower()
    return lower.startswith(".github/workflows/") or lower.startswith(".gitlab-ci") or lower in {"azure-pipelines.yml", "circle.yml"}


def _is_doc_path(relative_path: str) -> bool:
    lower = relative_path.lower()
    return lower.split("/")[-1] in DOC_NAMES or lower.startswith("docs/")


def _is_security_file(relative_path: str) -> bool:
    lower = relative_path.lower()
    return lower.endswith("security.md") or lower.endswith(".github/dependabot.yml") or lower.endswith("dependabot.yml")


def _is_config_file(relative_path: str) -> bool:
    lower = relative_path.lower()
    name = lower.split("/")[-1]
    return name in CONFIG_NAMES or lower.startswith(".github/") or lower.endswith((".yml", ".yaml", ".toml", ".ini"))


def _has_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _snippet_label(relative_path: str) -> str:
    lower = relative_path.lower()
    if lower.startswith(".github/workflows/"):
        return "CI 配置片段"
    if _is_test_path(relative_path):
        return "测试文件片段"
    if _is_doc_path(relative_path):
        return "文档片段"
    if lower.split("/")[-1] in MANIFEST_NAMES:
        return "工程配置片段"
    return "源码片段"


def _collect_snippets(root: Path, files: Iterable[Path], limit: int = 24) -> list[EvidenceItem]:
    snippets: list[EvidenceItem] = []
    priority: list[Path] = []
    remaining: list[Path] = []
    for path in files:
        rel = _relative(path, root)
        if _is_doc_path(rel) or rel.lower().split("/")[-1] in MANIFEST_NAMES or _is_ci_path(rel) or _is_test_path(rel):
            priority.append(path)
        elif path.suffix.lower() in SOURCE_EXTENSIONS:
            remaining.append(path)

    for path in [*priority, *remaining]:
        if len(snippets) >= limit:
            break
        if not _is_text_file(path):
            continue
        rel = _relative(path, root)
        text = _read_text(path)
        if _has_secret(text):
            text = "发现疑似密钥、Token 或密码模式，片段已脱敏。"
        snippets.append(EvidenceItem(label=_snippet_label(rel), path=rel, excerpt=text.strip()[:1200]))
    return snippets


def index_repository(root: Path) -> RepoIndex:
    root = root.resolve()
    tree: list[str] = []
    directories: set[str] = set()
    extension_counts: Counter[str] = Counter()
    source_files: list[str] = []
    test_files: list[str] = []
    documentation_files: list[str] = []
    manifest_files: list[str] = []
    ci_files: list[str] = []
    config_files: list[str] = []
    security_files: list[str] = []
    security_findings: list[EvidenceItem] = []
    large_files: list[str] = []
    scanned_files: list[Path] = []
    total_bytes = 0

    for path in root.rglob("*"):
        if any(_is_ignored_dir(parent) for parent in path.relative_to(root).parents if parent != Path(".")):
            continue
        if path.is_dir():
            if _is_ignored_dir(path):
                continue
            directories.add(_relative(path, root))
            continue
        if not path.is_file():
            continue

        rel = _relative(path, root)
        if any(part.lower() in IGNORED_DIRS for part in rel.split("/")):
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        tree.append(rel)
        scanned_files.append(path)

        suffix = path.suffix.lower() or "<none>"
        extension_counts[suffix] += 1
        lower = rel.lower()
        name = lower.split("/")[-1]

        if size > 500_000:
            large_files.append(rel)
        if path.suffix.lower() in SOURCE_EXTENSIONS:
            source_files.append(rel)
        if _is_test_path(rel):
            test_files.append(rel)
        if _is_doc_path(rel):
            documentation_files.append(rel)
        if name in MANIFEST_NAMES:
            manifest_files.append(rel)
        if _is_ci_path(rel):
            ci_files.append(rel)
        if _is_config_file(rel):
            config_files.append(rel)
        if _is_security_file(rel):
            security_files.append(rel)

        if size <= 300_000 and _is_text_file(path):
            text = _read_text(path, limit=6000)
            if _has_secret(text):
                security_findings.append(
                    EvidenceItem(label="疑似敏感信息", path=rel, excerpt="发现疑似密钥、Token 或密码模式，已脱敏。")
                )

    tree.sort()
    return RepoIndex(
        root=str(root),
        tree=tree[:2500],
        file_count=len(tree),
        directory_count=len(directories),
        total_bytes=total_bytes,
        extension_counts=dict(extension_counts),
        source_files=sorted(source_files)[:500],
        test_files=sorted(test_files)[:300],
        documentation_files=sorted(documentation_files)[:120],
        manifest_files=sorted(manifest_files)[:80],
        ci_files=sorted(ci_files)[:80],
        config_files=sorted(config_files)[:160],
        security_files=sorted(security_files)[:80],
        snippets=_collect_snippets(root, scanned_files),
        security_findings=security_findings[:20],
        large_files=sorted(large_files)[:80],
        has_tests=bool(test_files),
        has_ci=bool(ci_files),
        has_docs=bool(documentation_files),
        has_license=any(path.lower().split("/")[-1] in {"license", "license.md"} for path in documentation_files),
        has_security_policy=any(path.lower().endswith("security.md") for path in security_files),
    )
