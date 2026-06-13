from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import ConnectionTestResponse


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
    def __init__(self, token: str | None = None):
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-analysis-mvp",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=30) as client:
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
        repo_data = await self._get(f"/repos/{owner}/{repo}")
        default_branch = repo_data.get("default_branch") or "main"

        languages = await self._safe_get(f"/repos/{owner}/{repo}/languages", default={})
        community_profile = await self._safe_get(f"/repos/{owner}/{repo}/community/profile", default={})
        commits = await self._safe_get(f"/repos/{owner}/{repo}/commits", params={"per_page": 30}, default=[])
        releases = await self._safe_get(f"/repos/{owner}/{repo}/releases", params={"per_page": 5}, default=[])
        tree = await self._fetch_tree(owner, repo, default_branch)
        readme_excerpt = await self._fetch_readme_excerpt(owner, repo)
        security_file = await self._has_security_file(owner, repo)

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
    ) -> Any:
        try:
            return await self._get(path, params=params)
        except GitHubApiError:
            return default

    async def _fetch_tree(self, owner: str, repo: str, ref: str) -> list[str]:
        data = await self._safe_get(
            f"/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            default={"tree": []},
        )
        paths = [item.get("path") for item in data.get("tree", []) if item.get("path")]
        return paths[:200]

    async def _fetch_readme_excerpt(self, owner: str, repo: str) -> str:
        data = await self._safe_get(f"/repos/{owner}/{repo}/readme", default=None)
        if not data or not data.get("content"):
            return ""
        try:
            raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""
        return raw[:4000]

    async def _has_security_file(self, owner: str, repo: str) -> bool:
        candidates = ["SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md"]
        for path in candidates:
            data = await self._safe_get(f"/repos/{owner}/{repo}/contents/{path}", default=None)
            if data:
                return True
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
