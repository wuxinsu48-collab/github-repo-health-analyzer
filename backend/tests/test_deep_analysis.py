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
    assert 0 <= report.suitability.learning <= 100
    assert 0 <= report.suitability.secondary_development <= 100
    assert 0 <= report.suitability.production <= 100
    assert report.community_reference.stars == 3


def test_deep_analysis_explores_repo_and_returns_evidence_pool(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "package.json").write_text(
        '{"scripts":{"build":"vite build","test":"vitest run"},"dependencies":{"vue":"latest"}}',
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Demo\n\nInstall with npm. Run tests. Usage example.\n", encoding="utf-8")
    (repo / "src" / "main.tsx").write_text("export function main() { return 'ok' }\n", encoding="utf-8")
    (repo / "src" / "App.tsx").write_text("// TODO split this component\nexport const App = () => null\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n- run: npm test\n", encoding="utf-8")

    index = index_repository(repo)
    report = analyze_repository_index(index=index, github_evidence={"repo": {"stars": 0, "forks": 0}})

    assert report.exploration_notes
    assert report.evidence_pool
    assert report.agent_exploration == report.exploration_notes
    assert any(note["action"] == "read_file" and note["target"] == "package.json" for note in report.exploration_notes)
    assert any(note["action"] == "grep" and "TODO" in note["target"] for note in report.exploration_notes)
    assert any(item["file"] == "package.json" for item in report.evidence_pool)
    assert any(item["dimension"] == "maintainability" for item in report.evidence_pool)
    assert report.core_score.dimensions["testing"].score >= 8


def test_deep_analysis_emits_node_and_tool_progress_events(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nUsage example.\n", encoding="utf-8")
    (repo / "src" / "main.ts").write_text("export const main = () => true\n", encoding="utf-8")
    index = index_repository(repo)
    events: list[dict] = []

    analyze_repository_index(index=index, github_evidence={"repo": {}}, progress=events.append)

    assert any(event["id"] == "plan_exploration" and event["status"] == "completed" for event in events)
    assert any(event["id"] == "explore_codebase" and event["status"] == "running" for event in events)
    assert any(event["kind"] == "tool" and event["label"] == "read_file" and event["target"] == "package.json" for event in events)
    assert any(event["kind"] == "tool" and event["label"] == "grep" for event in events)


def test_java_maven_analysis_is_driven_by_agent_exploration_not_shallow_index(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src" / "main" / "java" / "com" / "library" / "controller").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "com" / "library" / "service").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "com" / "library" / "dao").mkdir(parents=True)
    (repo / "src" / "main" / "java" / "com" / "library" / "bean").mkdir(parents=True)
    (repo / "src" / "main" / "webapp" / "static" / "js").mkdir(parents=True)
    (repo / "pom.xml").write_text(
        """
<project>
  <dependencies>
    <dependency><groupId>org.springframework</groupId><artifactId>spring-webmvc</artifactId></dependency>
    <dependency><groupId>mysql</groupId><artifactId>mysql-connector-java</artifactId></dependency>
  </dependencies>
</project>
""".strip(),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Library\n\nRun with Tomcat.\n", encoding="utf-8")
    (repo / "library.sql").write_text("CREATE TABLE admin(id int, password varchar(32));\n", encoding="utf-8")
    (repo / "src" / "main" / "java" / "com" / "library" / "controller" / "BookController.java").write_text(
        "package com.library.controller;\nclass BookController { BookService service; }\n",
        encoding="utf-8",
    )
    (repo / "src" / "main" / "java" / "com" / "library" / "service" / "BookService.java").write_text(
        "package com.library.service;\nclass BookService { BookDao dao; }\n",
        encoding="utf-8",
    )
    (repo / "src" / "main" / "java" / "com" / "library" / "dao" / "BookDao.java").write_text(
        "package com.library.dao;\nclass BookDao { }\n",
        encoding="utf-8",
    )
    (repo / "src" / "main" / "java" / "com" / "library" / "bean" / "Book.java").write_text(
        "package com.library.bean;\nclass Book { String name; }\n",
        encoding="utf-8",
    )
    (repo / "src" / "main" / "webapp" / "static" / "js" / "jquery.js").write_text(
        "/* test test test is a browser feature probe, not a project test */\n",
        encoding="utf-8",
    )

    report = analyze_repository_index(index_repository(repo), {"repo": {}})
    explored_targets = {note["target"] for note in report.exploration_notes if note["action"] == "read_file"}
    testing_evidence_files = {
        evidence.path
        for evidence in report.core_score.dimensions["testing"].evidence
        if evidence.path
    }

    assert "pom.xml" in explored_targets
    assert any("controller/BookController.java" in target for target in explored_targets)
    assert any("service/BookService.java" in target for target in explored_targets)
    assert any("dao/BookDao.java" in target for target in explored_targets)
    assert report.core_score.dimensions["testing"].score <= 2
    assert not any("static/js" in path for path in testing_evidence_files)
