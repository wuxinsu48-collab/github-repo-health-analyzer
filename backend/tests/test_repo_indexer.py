from pathlib import Path

from app.services.repo_indexer import index_repository


def test_index_repository_collects_project_signals_and_ignores_vendor(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "node_modules").mkdir()
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_main.py").write_text("def test_main():\n    assert True\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nRun tests with pytest.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("fastapi\npytest\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (repo / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")

    index = index_repository(repo)

    assert "src/main.py" in index.tree
    assert "node_modules/ignored.js" not in index.tree
    assert index.has_tests is True
    assert index.has_ci is True
    assert "README.md" in index.documentation_files
    assert "requirements.txt" in index.manifest_files


def test_index_repository_flags_secret_like_content_without_storing_full_secret(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("SECRET_TOKEN = 'placeholder-secret-value'\n", encoding="utf-8")

    index = index_repository(repo)

    assert index.security_findings
    assert "placeholder-secret-value" not in str(index.model_dump())
