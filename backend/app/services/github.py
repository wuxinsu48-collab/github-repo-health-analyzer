from __future__ import annotations

import base64
import asyncio
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import ConnectionTestResponse

GitHubProgress = Callable[[dict[str, Any]], None]


class GitHubApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def parse_github_repo_url(url: str) -> tuple[str, str]:
    value = url.strip()
    if not value:
        raise ValueError("请输入 GitHub 仓库 URL")

    markdown_match = re.match(r"^\[[^\]]+\]\(([^)]+)\)$", value)
    if markdown_match:
        value = markdown_match.group(1).strip()

    ssh_match = re.match(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$", value)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    shorthand_match = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", value)
    if shorthand_match:
        return shorthand_match.group(1), shorthand_match.group(2)

    if value.startswith("github.com/"):
        value = "https://" + value

    parsed = urlparse(value)
    if parsed.netloc.lower() != "github.com":
        raise ValueError("只支持 github.com 的公开仓库 URL")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub URL 中缺少 owner/repo")

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("GitHub URL 中缺少 owner/repo")
    return owner, repo


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        progress: GitHubProgress | None = None,
    ):
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-analysis-mvp",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout if timeout is not None else httpx.Timeout(12.0, connect=5.0)
        self.transport = transport
        self.progress = progress

    def _emit(self, event_id: str, label: str, status: str, detail: str, target: str | None = None) -> None:
        if not self.progress:
            return
        self.progress(
            {
                "id": event_id,
                "label": label,
                "status": status,
                "detail": detail,
                "kind": "tool",
                "target": target,
            }
        )

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            transport=self.transport,
        )

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> Any:
        if client is None:
            async with self._make_client() as local_client:
                response = await local_client.get(path, params=params)
        else:
            response = await client.get(path, params=params)
        if response.status_code == 404:
            raise GitHubApiError(404, "仓库不存在或无法访问")
        if response.status_code == 401:
            raise GitHubApiError(401, "GitHub Token 无效")
        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                raise GitHubApiError(403, "GitHub API 速率限制已用尽，请填写 Token 后重试")
            raise GitHubApiError(403, "GitHub API 拒绝访问")
        if response.status_code >= 400:
            raise GitHubApiError(response.status_code, f"GitHub API 返回 HTTP {response.status_code}")
        return response.json()

    async def test_connection(self) -> ConnectionTestResponse:
        try:
            data = await self._get("/rate_limit")
        except GitHubApiError as exc:
            return ConnectionTestResponse(ok=False, message=exc.message, details={"status_code": exc.status_code})
        except httpx.ConnectError:
            return ConnectionTestResponse(ok=False, message="无法连接 GitHub API")
        except httpx.TimeoutException:
            return ConnectionTestResponse(ok=False, message="GitHub API 连接超时")
        except httpx.HTTPError as exc:
            return ConnectionTestResponse(ok=False, message=f"GitHub 请求失败: {exc.__class__.__name__}")

        core = (data.get("resources") or {}).get("core") or {}
        return ConnectionTestResponse(
            ok=True,
            message="GitHub 连接测试通过",
            details={
                "limit": core.get("limit"),
                "remaining": core.get("remaining"),
                "reset": core.get("reset"),
                "authenticated": "Authorization" in self.headers,
            },
        )

    async def fetch_repo_evidence(self, owner: str, repo: str) -> dict[str, Any]:
        async with self._make_client() as client:
            repo_data = await self._required_get(
                event_id="github_repo",
                label="repo",
                target=f"{owner}/{repo}",
                path=f"/repos/{owner}/{repo}",
                client=client,
            )
            default_branch = repo_data.get("default_branch") or "main"

            (
                languages,
                community_profile,
                commits,
                releases,
                tree,
                readme_excerpt,
                security_file,
            ) = await asyncio.gather(
                self._safe_get(
                    f"/repos/{owner}/{repo}/languages",
                    default={},
                    client=client,
                    event_id="github_languages",
                    label="languages",
                    target="languages",
                ),
                self._safe_get(
                    f"/repos/{owner}/{repo}/community/profile",
                    default={},
                    client=client,
                    event_id="github_community",
                    label="community",
                    target="community/profile",
                ),
                self._safe_get(
                    f"/repos/{owner}/{repo}/commits",
                    params={"per_page": 30},
                    default=[],
                    client=client,
                    event_id="github_commits",
                    label="commits",
                    target="commits?per_page=30",
                ),
                self._safe_get(
                    f"/repos/{owner}/{repo}/releases",
                    params={"per_page": 5},
                    default=[],
                    client=client,
                    event_id="github_releases",
                    label="releases",
                    target="releases?per_page=5",
                ),
                self._fetch_tree(owner, repo, default_branch, client=client),
                self._fetch_readme_excerpt(owner, repo, client=client),
                self._has_security_file(owner, repo, client=client),
            )

        community = self._normalize_community_profile(community_profile)
        community["security"] = community.get("security") or security_file

        return {
            "repo": self._normalize_repo(repo_data),
            "languages": languages,
            "community": community,
            "commits": self._normalize_commits(commits),
            "releases": self._normalize_releases(releases),
            "tree": tree,
            "readme_excerpt": readme_excerpt,
            "config_summary": summarize_tree(tree),
        }

    async def _safe_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        default: Any = None,
        *,
        client: httpx.AsyncClient | None = None,
        event_id: str | None = None,
        label: str | None = None,
        target: str | None = None,
    ) -> Any:
        if event_id and label:
            self._emit(event_id, label, "running", f"读取 {target or label}", target)
        try:
            data = await self._get(path, params=params, client=client)
        except GitHubApiError as exc:
            if event_id and label:
                self._emit(event_id, label, "skipped", f"{exc.message}，使用默认值继续", target)
            return default
        except httpx.TimeoutException:
            if event_id and label:
                self._emit(event_id, label, "skipped", "GitHub 请求超时，使用默认值继续", target)
            return default
        except httpx.HTTPError as exc:
            if event_id and label:
                self._emit(event_id, label, "skipped", f"GitHub 请求失败：{exc.__class__.__name__}，使用默认值继续", target)
            return default
        if event_id and label:
            self._emit(event_id, label, "completed", "读取完成", target)
        return data

    async def _required_get(
        self,
        *,
        event_id: str,
        label: str,
        target: str,
        path: str,
        client: httpx.AsyncClient,
    ) -> Any:
        self._emit(event_id, label, "running", f"读取 {target}", target)
        try:
            data = await self._get(path, client=client)
        except GitHubApiError as exc:
            self._emit(event_id, label, "failed", exc.message, target)
            raise
        except httpx.TimeoutException as exc:
            self._emit(event_id, label, "failed", "GitHub 请求超时", target)
            raise GitHubApiError(504, "GitHub API 连接超时") from exc
        except httpx.HTTPError as exc:
            self._emit(event_id, label, "failed", f"GitHub 请求失败：{exc.__class__.__name__}", target)
            raise GitHubApiError(502, f"GitHub 请求失败: {exc.__class__.__name__}") from exc
        self._emit(event_id, label, "completed", "仓库基础信息读取完成", target)
        return data

    async def _fetch_tree(self, owner: str, repo: str, ref: str, *, client: httpx.AsyncClient | None = None) -> list[str]:
        data = await self._safe_get(
            f"/repos/{owner}/{repo}/git/trees/{ref}",
            default={"tree": []},
            client=client,
            event_id="github_tree",
            label="root tree",
            target=ref,
        )
        paths = [item.get("path") for item in data.get("tree", []) if item.get("path")]
        return paths[:200]

    async def _fetch_readme_excerpt(self, owner: str, repo: str, *, client: httpx.AsyncClient | None = None) -> str:
        data = await self._safe_get(
            f"/repos/{owner}/{repo}/readme",
            default=None,
            client=client,
            event_id="github_readme",
            label="README",
            target="README",
        )
        if not data or not data.get("content"):
            return ""
        try:
            raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""
        return raw[:4000]

    async def _has_security_file(self, owner: str, repo: str, *, client: httpx.AsyncClient | None = None) -> bool:
        self._emit("github_security", "security file", "running", "检查 SECURITY.md", "SECURITY.md")
        candidates = ["SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md"]
        for path in candidates:
            data = await self._safe_get(
                f"/repos/{owner}/{repo}/contents/{path}",
                default=None,
                client=client,
            )
            if data:
                self._emit("github_security", "security file", "completed", f"发现 {path}", path)
                return True
        self._emit("github_security", "security file", "completed", "未发现 SECURITY.md", "SECURITY.md")
        return False

    @staticmethod
    def _normalize_repo(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "html_url": data.get("html_url"),
            "description": data.get("description") or "",
            "stars": data.get("stargazers_count") or 0,
            "forks": data.get("forks_count") or 0,
            "watchers": data.get("watchers_count") or 0,
            "subscribers": data.get("subscribers_count") or 0,
            "open_issues": data.get("open_issues_count") or 0,
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "pushed_at": data.get("pushed_at"),
            "archived": data.get("archived") or False,
            "disabled": data.get("disabled") or False,
            "fork": data.get("fork") or False,
            "license": data.get("license"),
            "default_branch": data.get("default_branch"),
            "size": data.get("size") or 0,
            "topics": data.get("topics") or [],
            "homepage": data.get("homepage") or "",
        }

    @staticmethod
    def _normalize_community_profile(data: dict[str, Any]) -> dict[str, bool]:
        files = data.get("files") or {}
        return {
            "readme": bool(files.get("readme")),
            "license": bool(files.get("license")),
            "contributing": bool(files.get("contributing")),
            "code_of_conduct": bool(files.get("code_of_conduct")),
            "security": bool(files.get("security")),
            "issue_template": bool(files.get("issue_template")),
            "pull_request_template": bool(files.get("pull_request_template")),
        }

    @staticmethod
    def _normalize_commits(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "sha": item.get("sha"),
                "date": ((item.get("commit") or {}).get("committer") or {}).get("date"),
                "author": (((item.get("commit") or {}).get("author") or {}).get("name")),
            }
            for item in data[:30]
        ]

    @staticmethod
    def _normalize_releases(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "tag_name": item.get("tag_name"),
                "name": item.get("name"),
                "published_at": item.get("published_at"),
            }
            for item in data[:5]
        ]


def summarize_tree(tree: list[str]) -> dict[str, Any]:
    lower_paths = [path.lower() for path in tree]
    manifest_names = (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "cargo.toml",
        "pom.xml",
    )
    return {
        "has_ci": any(path.startswith(".github/workflows/") for path in lower_paths),
        "has_tests": any(
            path.startswith("tests/")
            or path.startswith("test/")
            or "/tests/" in path
            or "/test/" in path
            or path.endswith(".test.ts")
            or path.endswith("_test.py")
            for path in lower_paths
        ),
        "has_docker": any(path.endswith("dockerfile") or path.endswith("docker-compose.yml") for path in lower_paths),
        "manifests": [path for path in tree if path.lower().endswith(manifest_names)],
    }
