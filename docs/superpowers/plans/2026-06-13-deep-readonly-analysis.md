# Deep Read-Only Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old rule-score-plus-AI-adjustment model with a read-only cloned-repository analysis that produces a 100-point evidence-based core score and separate suitability scores.

**Architecture:** The backend clones the public GitHub repository into an ignored local workspace, indexes files without executing project code, builds a compact evidence bundle, runs a LangGraph-orchestrated staged analyzer, optionally asks AI to review that evidence, and stores the complete report in SQLite. The frontend reads the new report shape and visualizes core dimensions, evidence, AI reasoning, and suitability references.

**Tech Stack:** FastAPI, HTTPX, Pydantic, SQLite, Git CLI, LangGraph, Vue 3, TypeScript, ECharts.

---

## File Structure

- Create `backend/app/services/repo_workspace.py`: clone public repositories into `backend/workspaces/`, enforce GitHub URL safety, clean stale workspaces, and never execute cloned code.
- Create `backend/app/services/repo_indexer.py`: walk the cloned repository, ignore generated/vendor/binary/large files, collect tree, manifests, docs, CI, tests, source files, security signals, and small text snippets.
- Create `backend/app/services/deep_analysis.py`: define scoring dimensions, LangGraph nodes, deterministic scoring, suitability scoring, and AI evidence merge.
- Modify `backend/app/services/ai.py`: add deep-analysis JSON prompt and parser while keeping connection-test behavior.
- Modify `backend/app/models.py`: replace old scoring models with core dimensions, evidence items, suitability, community reference, and deep AI assessment models.
- Modify `backend/app/main.py`: make `/api/analyze` call GitHub metadata fetch, local clone/index, deep analysis, optional AI review, and report insertion.
- Modify `backend/app/db.py`: support new payload fields while keeping old report rows readable.
- Modify `.gitignore`: ignore `backend/workspaces/`.
- Modify backend tests: add RED tests for repo indexing, scoring dimensions, AI parser, and analyze API behavior.
- Modify `frontend/src/types.ts`, `frontend/src/App.vue`, and `frontend/src/style.css`: display core 100 score, six dimensions, suitability reference, analysis evidence, AI report, and updated scoring guide.

## Tasks

### Task 1: Backend Model Contract

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_deep_analysis.py`

- [ ] **Step 1: Write the failing test**

```python
from app.models import CoreScoreResult


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_deep_analysis.py::test_core_score_model_requires_six_weighted_dimensions -q`
Expected: FAIL because `CoreScoreResult` does not exist.

- [ ] **Step 3: Implement models**

Add Pydantic models for `EvidenceItem`, `ScoreDimension`, `CoreScoreResult`, `SuitabilityScores`, `CommunityReference`, `DeepAiAssessment`, and update report models to expose `core_score`, `rule_score`, and `ai_score` compat fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_deep_analysis.py::test_core_score_model_requires_six_weighted_dimensions -q`
Expected: PASS.

### Task 2: Read-Only Repository Workspace And Index

**Files:**
- Create: `backend/app/services/repo_workspace.py`
- Create: `backend/app/services/repo_indexer.py`
- Modify: `.gitignore`
- Test: `backend/tests/test_repo_indexer.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_repo_indexer.py::test_index_repository_collects_project_signals_and_ignores_vendor -q`
Expected: FAIL because `repo_indexer` does not exist.

- [ ] **Step 3: Implement clone and index services**

Implement `clone_repository(owner, repo, repo_url)` with `git clone --depth 1 --filter=blob:none`, timeout, workspace root under `backend/workspaces/`, and sanitized paths. Implement `index_repository(path)` with ignore directories, text/binary detection, size limits, snippet extraction, file metrics, security patterns, and source/test/doc/config classification.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_repo_indexer.py::test_index_repository_collects_project_signals_and_ignores_vendor -q`
Expected: PASS.

### Task 3: Deep Scoring Graph

**Files:**
- Create: `backend/app/services/deep_analysis.py`
- Test: `backend/tests/test_deep_analysis.py`

- [ ] **Step 1: Write failing scoring test**

```python
from pathlib import Path

from app.services.deep_analysis import analyze_repository_index
from app.services.repo_indexer import index_repository


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_deep_analysis.py::test_deep_analysis_scores_core_dimensions_from_local_evidence -q`
Expected: FAIL because `deep_analysis` does not exist.

- [ ] **Step 3: Implement LangGraph scoring nodes**

Create a `StateGraph` with nodes `summarize_structure`, `score_core`, `score_suitability`, and `finalize_report`. Use deterministic scoring for the six dimensions and evidence snippets. Keep GitHub metadata separate in `community_reference`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_deep_analysis.py::test_deep_analysis_scores_core_dimensions_from_local_evidence -q`
Expected: PASS.

### Task 4: AI Evidence Review

**Files:**
- Modify: `backend/app/services/ai.py`
- Test: `backend/tests/test_ai.py`

- [ ] **Step 1: Write failing parser test**

```python
from app.services.ai import parse_deep_ai_response


def test_parse_deep_ai_response_keeps_ai_score_independent_from_rule_formula():
    raw = """
    {
      "score": 68,
      "confidence": "high",
      "summary": "代码结构清楚，但测试和安全治理不足。",
      "dimension_reviews": {
        "architecture": "分层存在但边界一般",
        "engineering": "依赖文件存在",
        "testing": "测试较少",
        "documentation": "README 简略",
        "security": "缺少安全说明",
        "maintainability": "文件规模可控"
      },
      "strengths": ["入口文件清晰"],
      "risks": ["测试覆盖不足"],
      "recommendations": ["补充 CI 和安全说明"]
    }
    """

    result = parse_deep_ai_response(raw)

    assert result.score == 68
    assert "architecture" in result.dimension_reviews
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_ai.py::test_parse_deep_ai_response_keeps_ai_score_independent_from_rule_formula -q`
Expected: FAIL because `parse_deep_ai_response` does not exist.

- [ ] **Step 3: Implement AI prompt and parser**

Add `DeepAiAssessment`, `build_deep_assessment_payload`, `call_deep_ai_assessment`, and `parse_deep_ai_response`. Prompt AI to judge only the compact local evidence bundle and return independent score plus dimension reviews, not a correction to rule score.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_ai.py::test_parse_deep_ai_response_keeps_ai_score_independent_from_rule_formula -q`
Expected: PASS.

### Task 5: API Integration And Persistence

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/db.py`
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write failing analyze API test**

```python
def test_analyze_endpoint_returns_deep_core_score(monkeypatch, tmp_path):
    # Mock GitHub metadata and clone path, then assert /api/analyze stores a report
    # whose payload contains core_score, suitability, community_reference, and analysis_trace.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_api.py::test_analyze_endpoint_returns_deep_core_score -q`
Expected: FAIL because API still returns the old payload.

- [ ] **Step 3: Integrate services**

In `/api/analyze`, parse GitHub URL, fetch REST metadata, clone repo read-only, index local files, run `analyze_repository_index`, optionally merge AI review, compose final score from `core_score.score`, save payload, and return existing response shape with compatibility fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_api.py::test_analyze_endpoint_returns_deep_core_score -q`
Expected: PASS.

### Task 6: Frontend Visualization

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Update TypeScript contracts**

Represent `core_score`, `suitability`, `community_reference`, `analysis_trace`, `local_index`, and `deep_ai_assessment`.

- [ ] **Step 2: Update charts**

Show six core dimensions with max scores 20/20/15/15/15/15, show suitability as three reference bars, and keep language distribution from GitHub metadata.

- [ ] **Step 3: Update report panels**

Replace “规则分 + AI 修正” copy with “只读深度分析核心分”, show evidence snippets by dimension, analysis trace, and updated scoring guide.

- [ ] **Step 4: Verify frontend build**

Run: `npm run build`
Expected: PASS.

### Task 7: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Run frontend build**

Run: `npm run build`
Expected: build succeeds.

- [ ] **Step 3: Check for leaked secrets**

Run: `rg "sk-[A-Za-z0-9_-]{20,}|api_key\\s*[:=]" -n --glob "!frontend/node_modules/**" --glob "!backend/data/**"`
Expected: no real API keys are present.

- [ ] **Step 4: Summarize**

Report changed files, verification output, and note that the branch is not pushed.
