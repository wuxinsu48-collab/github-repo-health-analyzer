from pathlib import Path

import pytest

from app.services.repo_tools import find_files_safe, grep_safe, list_dir_safe, read_file_safe


def test_read_file_safe_stays_inside_repo_and_limits_lines(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (repo / "README.md").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = read_file_safe(repo, "README.md", start_line=2, max_lines=2)

    assert result["file"] == "README.md"
    assert result["line_start"] == 2
    assert result["line_end"] == 3
    assert result["content"] == "two\nthree"

    with pytest.raises(ValueError):
        read_file_safe(repo, "../outside.txt")


def test_list_find_and_grep_ignore_generated_directories(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "node_modules").mkdir()
    (repo / "src" / "app.py").write_text("def main():\n    token = 'demo'\n", encoding="utf-8")
    (repo / "node_modules" / "ignored.py").write_text("token = 'ignored'\n", encoding="utf-8")

    entries = list_dir_safe(repo)
    assert {entry["name"] for entry in entries} == {"src"}

    files = find_files_safe(repo, "*.py")
    assert files == ["src/app.py"]

    matches = grep_safe(repo, "token")
    assert [match["file"] for match in matches] == ["src/app.py"]
    assert matches[0]["line"] == 2
