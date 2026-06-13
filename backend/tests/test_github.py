import pytest
import httpx

from app.services.github import GitHubClient, parse_github_repo_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/vuejs/core", ("vuejs", "core")),
        ("https://github.com/vuejs/core.git", ("vuejs", "core")),
        ("https://github.com/vuejs/core/issues/1", ("vuejs", "core")),
        ("git@github.com:psf/requests.git", ("psf", "requests")),
        ("github.com/fastapi/fastapi", ("fastapi", "fastapi")),
        ("zhanghuanhao/LibrarySystem", ("zhanghuanhao", "LibrarySystem")),
        ("[zhanghuanhao/LibrarySystem](https://github.com/zhanghuanhao/LibrarySystem)", ("zhanghuanhao", "LibrarySystem")),
    ],
)
def test_parse_github_repo_url_accepts_common_formats(url, expected):
    assert parse_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://gitlab.com/example/project",
        "https://github.com/only-owner",
        "not a url",
    ],
)
def test_parse_github_repo_url_rejects_invalid_values(url):
    with pytest.raises(ValueError):
        parse_github_repo_url(url)


@pytest.mark.asyncio
async def test_fetch_repo_evidence_continues_when_optional_request_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/demo",
                    "html_url": "https://github.com/acme/demo",
                    "default_branch": "main",
                },
            )
        if path == "/repos/acme/demo/languages":
            raise httpx.ReadTimeout("slow languages endpoint", request=request)
        if path == "/repos/acme/demo/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "README.md"}]})
        if path == "/repos/acme/demo/readme":
            return httpx.Response(200, json={})
        if "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[] if path.endswith(("/commits", "/releases")) else {"files": {}})

    client = GitHubClient(transport=httpx.MockTransport(handler))

    evidence = await client.fetch_repo_evidence("acme", "demo")

    assert evidence["repo"]["full_name"] == "acme/demo"
    assert evidence["languages"] == {}
    assert evidence["tree"] == ["README.md"]


@pytest.mark.asyncio
async def test_fetch_repo_evidence_emits_granular_progress_events():
    events: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/demo",
                    "html_url": "https://github.com/acme/demo",
                    "default_branch": "main",
                },
            )
        if path == "/repos/acme/demo/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "src/main.ts"}]})
        if path == "/repos/acme/demo/readme":
            return httpx.Response(200, json={})
        if "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[] if path.endswith(("/commits", "/releases")) else {"files": {}})

    client = GitHubClient(transport=httpx.MockTransport(handler), progress=events.append)

    await client.fetch_repo_evidence("acme", "demo")

    event_ids = {event["id"] for event in events}
    assert {"github_repo", "github_languages", "github_tree", "github_readme"}.issubset(event_ids)
    assert all(event["kind"] == "tool" for event in events)
    assert any(event["status"] == "running" for event in events)
    assert any(event["status"] == "completed" for event in events)


@pytest.mark.asyncio
async def test_fetch_repo_evidence_uses_shallow_tree_request_to_avoid_large_downloads():
    tree_queries: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/demo",
                    "html_url": "https://github.com/acme/demo",
                    "default_branch": "main",
                },
            )
        if path == "/repos/acme/demo/git/trees/main":
            tree_queries.append(request.url.query)
            return httpx.Response(200, json={"tree": [{"path": "src"}, {"path": "README.md"}]})
        if path == "/repos/acme/demo/readme":
            return httpx.Response(200, json={})
        if "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[] if path.endswith(("/commits", "/releases")) else {"files": {}})

    client = GitHubClient(transport=httpx.MockTransport(handler))

    evidence = await client.fetch_repo_evidence("acme", "demo")

    assert tree_queries == [b""]
    assert evidence["tree"] == ["src", "README.md"]
