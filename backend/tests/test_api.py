from fastapi.testclient import TestClient

from app.main import app
from app.services.github import GitHubApiError


def test_health_endpoint_returns_ok():
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analyze_endpoint_returns_deep_core_score(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_main.py").write_text("def test_main():\n    assert True\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    async def fake_fetch_repo_evidence(self, owner, name):
        return {
            "repo": {
                "full_name": f"{owner}/{name}",
                "html_url": f"https://github.com/{owner}/{name}",
                "description": "demo",
                "stars": 5,
                "forks": 2,
                "watchers": 5,
                "subscribers": 1,
                "open_issues": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
                "pushed_at": "2026-06-01T00:00:00Z",
                "archived": False,
                "disabled": False,
                "fork": False,
                "license": None,
                "default_branch": "main",
                "size": 10,
                "topics": [],
                "homepage": "",
            },
            "languages": {"Python": 100},
            "community": {"readme": True, "license": False, "security": False},
            "commits": [],
            "releases": [],
            "tree": [],
            "readme_excerpt": "",
            "config_summary": {},
        }

    def fake_clone_repository(owner, name, repo_url):
        return repo

    monkeypatch.setattr("app.main.GitHubClient.fetch_repo_evidence", fake_fetch_repo_evidence)
    monkeypatch.setattr("app.main.clone_repository", fake_clone_repository)
    monkeypatch.setattr("app.main.cleanup_repository", lambda path: None)

    client = TestClient(app)
    response = client.post("/api/analyze", json={"repo_url": "https://github.com/acme/demo"})

    assert response.status_code == 200
    data = response.json()
    payload = data["payload"]
    assert "core_score" in payload
    assert "suitability" in payload
    assert "community_reference" in payload
    assert "analysis_trace" in payload
    assert data["final_score"] == payload["core_score"]["score"]


def test_analyze_endpoint_retries_public_repo_without_invalid_optional_token(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    calls: list[str | None] = []

    class FakeGitHubClient:
        def __init__(self, token=None):
            self.token = token

        async def fetch_repo_evidence(self, owner, name):
            calls.append(self.token)
            if self.token:
                raise GitHubApiError(401, "GitHub Token 无效")
            return {
                "repo": {
                    "full_name": f"{owner}/{name}",
                    "html_url": f"https://github.com/{owner}/{name}",
                    "description": "demo",
                    "stars": 5,
                    "forks": 2,
                    "watchers": 5,
                    "subscribers": 1,
                    "open_issues": 0,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-06-01T00:00:00Z",
                    "pushed_at": "2026-06-01T00:00:00Z",
                    "archived": False,
                    "disabled": False,
                    "fork": False,
                    "license": None,
                    "default_branch": "main",
                    "size": 10,
                    "topics": [],
                    "homepage": "",
                },
                "languages": {"Python": 100},
                "community": {"readme": True, "license": False, "security": False},
                "commits": [],
                "releases": [],
                "tree": [],
                "readme_excerpt": "",
                "config_summary": {},
            }

    monkeypatch.setattr("app.main.GitHubClient", FakeGitHubClient)
    monkeypatch.setattr("app.main.clone_repository", lambda owner, name, repo_url: repo)
    monkeypatch.setattr("app.main.cleanup_repository", lambda path: None)

    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        json={"repo_url": "zhanghuanhao/LibrarySystem", "github_token": "invalid-placeholder"},
    )

    assert response.status_code == 200
    payload = response.json()["payload"]
    assert calls == ["invalid-placeholder", None]
    assert payload["github_warning"] == "GitHub Token 无效，已改用公开访问继续分析。"


def test_analyze_job_endpoint_returns_agent_steps(monkeypatch):
    created = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr("app.main.schedule_analysis_task", fake_create_task)

    client = TestClient(app)
    response = client.post("/api/analyze/jobs", json={"repo_url": "zhanghuanhao/LibrarySystem"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"]
    assert data["status"] == "running"
    assert [step["id"] for step in data["steps"]] == [
        "parse_repo",
        "github_metadata",
        "clone_repo",
        "index_repo",
        "langgraph_score",
        "ai_review",
        "save_report",
    ]
    assert created

    status_response = client.get(f"/api/analyze/jobs/{data['job_id']}")
    assert status_response.status_code == 200
    assert status_response.json()["job_id"] == data["job_id"]
