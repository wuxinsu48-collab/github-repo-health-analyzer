from __future__ import annotations

import httpx
import pytest
from types import SimpleNamespace

from app.models import AiConfig
from app.services.repo_indexer import index_repository


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.text = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self.content}}]}


class FakeAgentLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.decisions = [
            {
                "thought": "Inspect dependency scripts first.",
                "action": "read_file",
                "target": "package.json",
                "dimension": "engineering",
                "reason": "Package scripts show engineering maturity.",
                "max_lines": 120,
            },
            {
                "thought": "Read onboarding documentation.",
                "action": "read_file",
                "target": "README.md",
                "dimension": "documentation",
                "reason": "README quality informs documentation score.",
                "max_lines": 120,
            },
            {
                "thought": "Look for actual test assertions.",
                "action": "grep",
                "target": "test\\(|expect\\(",
                "dimension": "testing",
                "reason": "Test syntax indicates executable tests.",
                "include_globs": ["tests/**", "src/**"],
                "max_matches": 10,
            },
            {
                "thought": "Enough evidence for the first pass.",
                "action": "finish",
                "target": "",
                "dimension": "overall",
                "reason": "The main scoring dimensions have evidence.",
            },
        ]

    async def complete_json(self, stage: str, payload: dict) -> dict:
        self.calls.append((stage, payload))
        if stage == "project_classifier":
            return {
                "project_type": "web_app",
                "primary_language": "TypeScript",
                "frameworks": ["React", "Vite"],
                "package_managers": ["npm"],
                "app_purpose": "Demo frontend app",
                "confidence": "high",
            }
        if stage == "rubric_selector":
            return {
                "dimensions": {
                    "functionality": 15,
                    "architecture_quality": 20,
                    "engineering": 15,
                    "testing": 15,
                    "security": 15,
                    "documentation": 10,
                    "maintenance": 10,
                },
                "rationale": "Web app rubric with full engineering and quality coverage.",
            }
        if stage == "explorer_decide":
            return self.decisions.pop(0)
        if stage == "evidence_curator":
            evidence_files = [
                item.get("file")
                for item in payload.get("evidence_pool", [])
                if item.get("file")
            ]
            return {
                "curated_evidence": {
                    "engineering": evidence_files[:2],
                    "testing": evidence_files[-2:],
                    "documentation": ["README.md"],
                },
                "notes": ["Evidence was grouped by target dimension."],
            }
        if stage.startswith("dimension_judge:"):
            dimension = stage.split(":", 1)[1]
            scores = {
                "functionality": (12, 15),
                "architecture_quality": (16, 20),
                "engineering": (12, 15),
                "testing": (11, 15),
                "security": (12, 15),
                "documentation": (7, 10),
                "maintenance": (8, 10),
            }
            score, max_score = scores[dimension]
            return {
                "score": score,
                "max_score": max_score,
                "confidence": "medium",
                "reasoning": f"{dimension} has enough local evidence.",
                "evidence_refs": [item.get("id") for item in payload.get("evidence", [])[:2]],
                "strengths": [f"{dimension} strength"],
                "risks": [f"{dimension} risk"],
                "recommendations": [f"Improve {dimension}"],
            }
        if stage == "critic_review":
            return {
                "verdict": "Scores are evidence-backed.",
                "concerns": ["Testing evidence is present but limited."],
                "evidence_gaps": ["Runtime behavior was not executed."],
                "score_adjustments": {},
            }
        if stage == "score_calibrator":
            return {
                "calibrated_dimensions": {
                    key: value["score"]
                    for key, value in payload["dimensions"].items()
                },
                "rationale": "No calibration changes needed.",
            }
        if stage == "final_report":
            return {
                "summary": "The agent inspected scripts, docs, and tests before scoring.",
                "strengths": ["Clear scripts", "Readable README"],
                "risks": ["Limited automated tests"],
                "recommendations": ["Expand test coverage"],
            }
        raise AssertionError(f"unexpected stage {stage}")


class BrokenCalibratorLlm(FakeAgentLlm):
    async def complete_json(self, stage: str, payload: dict) -> dict:
        if stage == "critic_review":
            return {
                "verdict": "Calibration should be conservative.",
                "concerns": ["Testing is thin."],
                "evidence_gaps": [],
                "score_adjustments": {"testing": -2, "security": 1},
            }
        if stage == "score_calibrator":
            raise ValueError(
                "AI Agent stage score_calibrator returned invalid JSON after repair: "
                "AI response was not valid JSON: Expecting ',' delimiter"
            )
        return await super().complete_json(stage, payload)


class UnstableButRecoverableLlm(FakeAgentLlm):
    async def complete_json(self, stage: str, payload: dict) -> dict:
        self.calls.append((stage, payload))
        if stage in {"project_classifier", "rubric_selector", "evidence_curator", "critic_review", "final_report"}:
            raise ValueError(f"{stage} returned invalid JSON after repair")
        if stage == "explorer_decide":
            return {
                "thought": "Try the same target again.",
                "action": "read_file",
                "target": "package.json",
                "dimension": "engineering",
                "reason": "Repeated decision from unstable model.",
                "max_lines": 80,
            }
        if stage.startswith("dimension_judge:"):
            dimension = stage.split(":", 1)[1]
            if dimension == "testing":
                raise ValueError("dimension judge returned invalid JSON")
        return await super().complete_json(stage, payload)


class ScoutAwareLlm(FakeAgentLlm):
    def __init__(self) -> None:
        super().__init__()
        self.scout_payloads: list[dict] = []

    async def complete_json(self, stage: str, payload: dict) -> dict:
        self.calls.append((stage, payload))
        if stage == "explorer_decide":
            if payload["step"] == 1:
                return {
                    "thought": "Start from my own engineering read before using the scout map.",
                    "action": "read_file",
                    "target": "package.json",
                    "dimension": "engineering",
                    "reason": "First step should be independent.",
                    "max_lines": 80,
                }
            if payload["step"] == 2:
                self.scout_payloads.append(payload.get("rule_scout_map"))
                return {
                    "thought": "Use the rule scout map to inspect the testing gap directly.",
                    "action": "read_file",
                    "target": "tests/app.test.ts",
                    "dimension": "testing",
                    "reason": "The scout map points at a testing evidence gap.",
                    "max_lines": 80,
                }
            return {
                "thought": "Enough after the scout-guided check.",
                "action": "finish",
                "target": "",
                "dimension": "overall",
                "reason": "The scout-guided evidence was collected.",
            }
        return await super().complete_json(stage, payload)


def _collect_dict_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _collect_dict_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _collect_dict_keys(child)


@pytest.mark.asyncio
async def test_run_agent_deep_scoring_explores_repo_with_safe_tools(tmp_path):
    from app.services.agent_scoring import run_agent_deep_scoring

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "package.json").write_text(
        '{"scripts":{"build":"vite build","test":"vitest"},"dependencies":{"@vitejs/plugin-react":"latest"}}',
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Demo\n\nInstall with npm install and run npm test.\n", encoding="utf-8")
    (repo / "src" / "App.tsx").write_text("export function App(){ return <main>Hello</main> }\n", encoding="utf-8")
    (repo / "tests" / "app.test.ts").write_text("test('renders', () => { expect(true).toBe(true) })\n", encoding="utf-8")

    events: list[dict] = []
    fake_llm = FakeAgentLlm()

    result = await run_agent_deep_scoring(
        config=AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash"),
        repo_root=repo,
        index=index_repository(repo),
        github_evidence={"repo": {"full_name": "acme/demo", "description": "demo"}},
        llm=fake_llm,
        progress=events.append,
        max_exploration_steps=6,
    )

    assert result.score == 78
    assert result.project_profile["project_type"] == "web_app"
    assert result.rubric["dimensions"]["architecture_quality"] == 20
    assert [step.action for step in result.exploration_steps][:3] == ["read_file", "read_file", "grep"]
    assert any(item.get("file") == "package.json" for item in result.evidence_pool)
    assert any(item.get("file") == "tests/app.test.ts" for item in result.evidence_pool)
    assert set(result.dimensions) == {
        "functionality",
        "architecture_quality",
        "engineering",
        "testing",
        "security",
        "documentation",
        "maintenance",
    }
    assert result.critic_review.verdict == "Scores are evidence-backed."
    assert result.final_report["summary"].startswith("The agent inspected")
    assert any(stage == "explorer_decide" for stage, _ in fake_llm.calls)
    assert any(event["kind"] == "tool" and event["target"] == "package.json" for event in events)
    assert any(event["id"] == "dimension_judge_testing" for event in events)


@pytest.mark.asyncio
async def test_run_agent_deep_scoring_falls_back_when_calibrator_returns_invalid_json(tmp_path):
    from app.services.agent_scoring import run_agent_deep_scoring

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nRun tests with npm test.\n", encoding="utf-8")
    (repo / "src" / "App.tsx").write_text("export function App(){ return null }\n", encoding="utf-8")
    (repo / "tests" / "app.test.ts").write_text("test('works', () => { expect(true).toBe(true) })\n", encoding="utf-8")

    events: list[dict] = []

    result = await run_agent_deep_scoring(
        config=AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash"),
        repo_root=repo,
        index=index_repository(repo),
        github_evidence={"repo": {"full_name": "acme/demo", "description": "demo"}},
        llm=BrokenCalibratorLlm(),
        progress=events.append,
        max_exploration_steps=6,
    )

    assert result.calibrated_dimensions["testing"] == 9
    assert result.calibrated_dimensions["security"] == 13
    assert result.score == 77
    assert "fallback" in result.final_report["calibration_rationale"].lower()
    assert any(
        event["id"] == "score_calibrator" and event["status"] == "completed" and "fallback" in event["detail"].lower()
        for event in events
    )


@pytest.mark.asyncio
async def test_run_agent_deep_scoring_uses_node_fallbacks_and_avoids_repeat_tools(tmp_path):
    from app.services.agent_scoring import run_agent_deep_scoring

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nInstall and run tests.\n", encoding="utf-8")
    (repo / "src" / "App.tsx").write_text("export function App(){ return null }\n", encoding="utf-8")
    (repo / "tests" / "app.test.ts").write_text("test('works', () => { expect(true).toBe(true) })\n", encoding="utf-8")

    events: list[dict] = []
    result = await run_agent_deep_scoring(
        config=AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash"),
        repo_root=repo,
        index=index_repository(repo),
        github_evidence={"repo": {"full_name": "acme/demo", "description": "demo"}},
        llm=UnstableButRecoverableLlm(),
        progress=events.append,
        max_exploration_steps=4,
    )

    non_finish = [step for step in result.exploration_steps if step.action != "finish"]
    signatures = [(step.action, step.target) for step in non_finish]
    assert len(signatures) == len(set(signatures))
    assert result.project_profile["project_type"] == "unknown"
    assert result.rubric["dimensions"]["engineering"] == 15
    assert result.dimensions["testing"].confidence == "low"
    assert "fallback" in result.final_report["summary"].lower()
    assert result.score >= 0
    assert any(event["id"] == "project_classifier" and "fallback" in event["detail"].lower() for event in events)
    assert any("duplicate" in event["detail"].lower() for event in events)


@pytest.mark.asyncio
async def test_httpx_agent_llm_repairs_invalid_json_once(monkeypatch):
    from app.services.agent_scoring import HttpxAgentLlm

    calls: list[dict] = []

    class RepairingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(json)
            if len(calls) == 1:
                return FakeResponse('{"project_type":"web_app" "primary_language":"TypeScript"}')
            return FakeResponse('{"project_type":"web_app","primary_language":"TypeScript"}')

    monkeypatch.setattr("app.services.agent_scoring.httpx.AsyncClient", RepairingClient)

    llm = HttpxAgentLlm(
        AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash")
    )

    result = await llm.complete_json("project_classifier", {"task": "Return JSON."})

    assert result == {"project_type": "web_app", "primary_language": "TypeScript"}
    assert len(calls) == 2
    assert "repair" in calls[1]["messages"][1]["content"].lower()


@pytest.mark.asyncio
async def test_agent_uses_rule_scout_map_after_first_independent_step_without_scores(tmp_path):
    from app.services.agent_scoring import run_agent_deep_scoring

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n\nRun with npm install.\n", encoding="utf-8")
    (repo / "tests" / "app.test.ts").write_text("test('works', () => { expect(true).toBe(true) })\n", encoding="utf-8")

    core_report = SimpleNamespace(
        core_score=SimpleNamespace(
            score=99,
            dimensions={
                "testing": SimpleNamespace(
                    score=1,
                    max_score=15,
                    reason="Testing evidence gap needs a direct check.",
                    evidence=[
                        SimpleNamespace(
                            label="Testing clue",
                            path="tests/app.test.ts",
                            excerpt="test('works', () => { expect(true).toBe(true) })",
                            line_start=1,
                            line_end=1,
                            reason="Rule evidence points at a test file.",
                        )
                    ],
                )
            },
            risk_flags=["Testing evidence is thin."],
        ),
        exploration_notes=[
            {
                "step": 2,
                "action": "grep",
                "target": "test\\(|expect\\(",
                "dimension": "testing",
                "result_summary": "Found testing syntax in tests/app.test.ts",
            }
        ],
        evidence_pool=[
            {
                "step": 2,
                "action": "grep",
                "target": "test\\(|expect\\(",
                "dimension": "testing",
                "file": "tests/app.test.ts",
                "line_start": 1,
                "line_end": 1,
                "snippet": "test('works', () => { expect(true).toBe(true) })",
                "reason": "Rule evidence for testing gap.",
            }
        ],
        recommendations=["Add CI test execution."],
    )

    fake_llm = ScoutAwareLlm()
    result = await run_agent_deep_scoring(
        config=AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash"),
        repo_root=repo,
        index=index_repository(repo),
        github_evidence={"repo": {"full_name": "acme/demo", "description": "demo"}},
        core_report=core_report,
        llm=fake_llm,
        max_exploration_steps=4,
    )

    explorer_payloads = [payload for stage, payload in fake_llm.calls if stage == "explorer_decide"]
    assert "rule_scout_map" not in explorer_payloads[0]
    assert "rule_scout_map" in explorer_payloads[1]
    scout_map = explorer_payloads[1]["rule_scout_map"]
    assert scout_map == fake_llm.scout_payloads[0]
    assert "tests/app.test.ts" in str(scout_map)
    forbidden_keys = {"score", "max_score", "core_score", "final_score", "rule_score"}
    assert not (set(_collect_dict_keys(scout_map)) & forbidden_keys)
    assert "99" not in str(scout_map)
    assert any(step.target == "tests/app.test.ts" for step in result.exploration_steps)


@pytest.mark.asyncio
async def test_httpx_agent_llm_retries_transient_timeout(monkeypatch):
    from app.services.agent_scoring import HttpxAgentLlm

    calls: list[dict] = []

    class TransientClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(json)
            if len(calls) == 1:
                raise httpx.TimeoutException("temporary timeout")
            return FakeResponse('{"ok":true}')

    monkeypatch.setattr("app.services.agent_scoring.httpx.AsyncClient", TransientClient)

    llm = HttpxAgentLlm(
        AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash")
    )

    assert await llm.complete_json("project_classifier", {"task": "Return JSON."}) == {"ok": True}
    assert len(calls) == 2
