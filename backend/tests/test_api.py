import asyncio
import time

from fastapi.testclient import TestClient

from app.main import app
from app.db import insert_report, list_reports
from app.models import AnalyzeRequest
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
    assert "dimensions" in payload
    assert "exploration_notes" in payload
    assert "evidence_pool" in payload
    assert "final_report" in payload
    assert "suitability" in payload
    assert "community_reference" in payload
    assert "analysis_trace" in payload
    assert payload["final_report"]["core_score"]["score"] == payload["core_score"]["score"]
    assert data["final_score"] == payload["core_score"]["score"]


def test_analyze_endpoint_stores_agent_deep_score_when_ai_config_is_present(monkeypatch, tmp_path):
    from app.models import AgentCriticReview, AgentDeepScore, AgentDimensionScore, AgentObservation

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

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

    async def fake_run_agent_deep_scoring(**kwargs):
        kwargs["progress"](
            {
                "id": "project_classifier",
                "label": "project_classifier",
                "status": "completed",
                "detail": "classified",
                "kind": "node",
            }
        )
        return AgentDeepScore(
            score=81,
            confidence="high",
            project_profile={"project_type": "library", "primary_language": "Python"},
            rubric={"dimensions": {"testing": 15}, "rationale": "test rubric"},
            exploration_steps=[
                AgentObservation(
                    step=1,
                    thought="read docs",
                    action="read_file",
                    target="README.md",
                    dimension="documentation",
                    result_summary="read README",
                    evidence=[],
                )
            ],
            evidence_pool=[],
            curated_evidence={"documentation": ["README.md"]},
            dimensions={
                "testing": AgentDimensionScore(
                    dimension="testing",
                    score=12,
                    max_score=15,
                    confidence="high",
                    reasoning="Tests are present.",
                    evidence_refs=[],
                    strengths=[],
                    risks=[],
                    recommendations=[],
                )
            },
            critic_review=AgentCriticReview(
                verdict="ok",
                concerns=[],
                evidence_gaps=[],
                score_adjustments={},
            ),
            calibrated_dimensions={"testing": 12},
            final_report={
                "summary": "Agent report",
                "strengths": ["Local evidence"],
                "risks": ["None"],
                "recommendations": ["Keep improving"],
            },
            trace=["project_classifier"],
        )

    progress_events = []

    def progress(step_id, status, detail="", metadata=None):
        progress_events.append((step_id, status, detail, metadata))

    monkeypatch.setattr("app.main.GitHubClient.fetch_repo_evidence", fake_fetch_repo_evidence)
    monkeypatch.setattr("app.main.clone_repository", lambda owner, name, repo_url: repo)
    monkeypatch.setattr("app.main.cleanup_repository", lambda path: None)
    monkeypatch.setattr("app.main.run_agent_deep_scoring", fake_run_agent_deep_scoring)

    from app.main import run_analysis_pipeline

    report = asyncio.run(
        run_analysis_pipeline(
            AnalyzeRequest(
                repo_url="https://github.com/acme/demo",
                ai={"base_url": "https://api.example.test", "api_key": "placeholder", "model": "fake-model"},
            ),
            progress,
        )
    )

    payload = report["payload"]
    assert payload["agent_deep_score"]["score"] == 81
    assert payload["deep_ai_assessment"]["score"] == 81
    assert payload["deep_ai_assessment"]["dimension_reviews"]["testing"] == "Tests are present."
    assert payload["ai_error"] is None
    assert report["ai_score"] == 81
    assert any(
        step_id == "ai_review" and metadata and metadata["event"]["id"] == "project_classifier"
        for step_id, _, _, metadata in progress_events
    )


def test_analyze_endpoint_retries_public_repo_without_invalid_optional_token(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    calls: list[str | None] = []

    class FakeGitHubClient:
        def __init__(self, token=None, progress=None):
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


async def test_run_analysis_pipeline_forwards_github_progress_events(monkeypatch, tmp_path):
    from app.main import run_analysis_pipeline

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    class FakeGitHubClient:
        def __init__(self, token=None, progress=None):
            self.progress = progress

        async def fetch_repo_evidence(self, owner, name):
            if self.progress:
                self.progress(
                    {
                        "id": "github_repo",
                        "label": "repo",
                        "status": "completed",
                        "detail": "仓库基础信息读取完成",
                        "kind": "tool",
                        "target": f"{owner}/{name}",
                    }
                )
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

    progress_events = []

    def progress(step_id, status, detail="", metadata=None):
        progress_events.append((step_id, status, detail, metadata))

    monkeypatch.setattr("app.main.GitHubClient", FakeGitHubClient)
    monkeypatch.setattr("app.main.clone_repository", lambda owner, name, repo_url: repo)
    monkeypatch.setattr("app.main.cleanup_repository", lambda path: None)

    await run_analysis_pipeline(AnalyzeRequest(repo_url="https://github.com/acme/demo"), progress)

    assert any(
        step_id == "github_metadata" and metadata and metadata["event"]["id"] == "github_repo"
        for step_id, _, _, metadata in progress_events
    )


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


def test_analysis_job_step_can_include_langgraph_internal_events():
    from app.services.analysis_jobs import add_step_event, clear_jobs_for_tests, create_analysis_job, get_analysis_job

    clear_jobs_for_tests()
    job = create_analysis_job()

    add_step_event(
        job.job_id,
        "langgraph_score",
        {
            "id": "tool_1_read_file",
            "label": "read_file",
            "status": "running",
            "detail": "读取 package.json",
            "kind": "tool",
            "target": "package.json",
        },
    )
    add_step_event(
        job.job_id,
        "langgraph_score",
        {
            "id": "tool_1_read_file",
            "label": "read_file",
            "status": "completed",
            "detail": "发现 build/test 脚本",
            "kind": "tool",
            "target": "package.json",
        },
    )

    langgraph_step = next(step for step in get_analysis_job(job.job_id).steps if step.id == "langgraph_score")

    assert len(langgraph_step.events) == 1
    assert langgraph_step.events[0].status == "completed"
    assert langgraph_step.events[0].kind == "tool"
    assert langgraph_step.events[0].target == "package.json"


async def test_analysis_job_status_remains_pollable_during_blocking_clone(monkeypatch, tmp_path):
    from app.main import run_analysis_job
    from app.services.analysis_jobs import clear_jobs_for_tests, create_analysis_job, get_analysis_job

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    class FakeGitHubClient:
        def __init__(self, token=None, progress=None):
            self.progress = progress

        async def fetch_repo_evidence(self, owner, name):
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

    def slow_clone_repository(owner, name, repo_url):
        time.sleep(0.4)
        return repo

    clear_jobs_for_tests()
    job = create_analysis_job()
    monkeypatch.setattr("app.main.GitHubClient", FakeGitHubClient)
    monkeypatch.setattr("app.main.clone_repository", slow_clone_repository)
    monkeypatch.setattr("app.main.cleanup_repository", lambda path: None)

    start = time.perf_counter()
    task = asyncio.create_task(run_analysis_job(job.job_id, AnalyzeRequest(repo_url="https://github.com/acme/demo")))
    await asyncio.sleep(0.05)
    elapsed = time.perf_counter() - start

    clone_step = next(step for step in get_analysis_job(job.job_id).steps if step.id == "clone_repo")

    await task

    assert elapsed < 0.2
    assert clone_step.status == "running"


def test_delete_report_endpoint_removes_recent_report(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ANALYSIS_DB", str(tmp_path / "reports.sqlite3"))
    report = insert_report(
        {
            "evidence": {
                "repo": {
                    "full_name": "acme/demo",
                    "html_url": "https://github.com/acme/demo",
                }
            },
            "core_score": {"score": 72},
            "rule_score": {"rule_score": 72, "dimension_scores": {}, "risk_flags": []},
            "final_score": 72,
        }
    )

    client = TestClient(app)
    response = client.delete(f"/api/reports/{report['id']}")

    assert response.status_code == 204
    assert list_reports() == []
    assert client.get(f"/api/reports/{report['id']}").status_code == 404


def test_delete_report_endpoint_returns_404_for_missing_report(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ANALYSIS_DB", str(tmp_path / "reports.sqlite3"))
    client = TestClient(app)

    response = client.delete("/api/reports/999")

    assert response.status_code == 404
