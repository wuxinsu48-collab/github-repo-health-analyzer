from pathlib import Path

from app.models import CoreScoreResult
from app.services.deep_analysis import analyze_repository_index
from app.services.repo_indexer import index_repository


def test_core_score_model_requires_six_weighted_dimensions():
    result = CoreScoreResult.model_validate(
        {
            "score": 72,
            "dimensions": {
                "architecture": {"score": 14, "max_score": 20, "reason": "分层清楚", "evidence": []},
                "engineering": {"score": 15, "max_score": 20, "reason": "工程文件完整", "evidence": []},
                "testing": {"score": 8, "max_score": 15, "reason": "有测试但覆盖未知", "evidence": []},
                "documentation": {"score": 11, "max_score": 15, "reason": "README 可用", "evidence": []},
                "security": {"score": 10, "max_score": 15, "reason": "未发现明显泄漏", "evidence": []},
                "maintainability": {"score": 14, "max_score": 15, "reason": "文件规模较合理", "evidence": []},
            },
            "summary": "项目整体可读。",
            "risk_flags": [],
        }
    )

    assert result.score == 72
    assert sum(item.max_score for item in result.dimensions.values()) == 100


def test_deep_analysis_scores_core_dimensions_from_local_evidence(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("def handler():\n    return 'ok'\n", encoding="utf-8")
    (repo / "tests" / "test_main.py").write_text("def test_handler():\n    assert True\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nArchitecture and usage.\n", encoding="utf-8")
    (repo / "LICENSE").write_text("MIT", encoding="utf-8")
    (repo / "requirements.txt").write_text("fastapi\npytest\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")

    index = index_repository(repo)
    report = analyze_repository_index(index=index, github_evidence={"repo": {"stars": 3, "forks": 1}})

    assert report.core_score.score > 60
    assert set(report.core_score.dimensions) == {
        "architecture",
        "engineering",
        "testing",
        "documentation",
        "security",
        "maintainability",
    }
    assert report.suitability.learning >= report.suitability.production
    assert report.community_reference.stars == 3
