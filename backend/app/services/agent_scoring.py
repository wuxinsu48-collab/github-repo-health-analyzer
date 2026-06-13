from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict

import httpx
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from app.models import (
    AgentCriticReview,
    AgentDeepScore,
    AgentDimensionScore,
    AgentObservation,
    AgentToolCall,
    AiConfig,
)
from app.services.ai import build_chat_completions_url
from app.services.repo_indexer import RepoIndex
from app.services.repo_tools import find_files_safe, grep_safe, list_dir_safe, read_file_safe


ProgressEventCallback = Callable[[dict[str, Any]], None]


DEFAULT_RUBRIC = {
    "functionality": 15,
    "architecture_quality": 20,
    "engineering": 15,
    "testing": 15,
    "security": 15,
    "documentation": 10,
    "maintenance": 10,
}

ENTRY_CANDIDATES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "README.md",
    "readme.md",
    ".github/workflows",
    "src/main.tsx",
    "src/main.ts",
    "src/App.tsx",
    "src/App.vue",
    "main.py",
    "app.py",
    "server.js",
    "index.js",
)

DEFAULT_SECURITY_PATTERN = (
    r"password|secret|api_key|apikey|token|private_key|eval\(|exec\(|os\.system|"
    r"subprocess|shell=True"
)
DEFAULT_TEST_PATTERN = r"describe\(|it\(|test\(|expect\(|pytest|unittest|assert\b|@Test\b"
DEFAULT_MAINTAINABILITY_PATTERN = r"TODO|FIXME|HACK"


class AgentLlm(Protocol):
    async def complete_json(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        pass


class AgentState(TypedDict, total=False):
    config: AiConfig
    repo_root: Path
    index: RepoIndex
    github_evidence: dict[str, Any]
    core_report: Any
    max_exploration_steps: int
    llm: AgentLlm
    progress_callback: ProgressEventCallback | None
    repo_map: dict[str, Any]
    rule_scout_map: dict[str, Any]
    project_profile: dict[str, Any]
    rubric: dict[str, Any]
    exploration_steps: list[AgentObservation]
    evidence_pool: list[dict[str, Any]]
    curated_evidence: dict[str, Any]
    dimensions: dict[str, AgentDimensionScore]
    critic_review: AgentCriticReview
    calibrated_dimensions: dict[str, int]
    calibration_rationale: str
    aggregate_score: int
    confidence: str
    final_report: dict[str, Any]
    result: AgentDeepScore
    trace: list[str]


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        last_fence = text.rfind("```")
        if first_newline != -1 and last_fence > first_newline:
            text = text[first_newline:last_fence].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI response did not contain a JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI response was not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AI JSON response must be an object")
    return parsed


class HttpxAgentLlm:
    def __init__(self, config: AiConfig, timeout: float = 90, request_attempts: int = 2) -> None:
        self.config = config
        self.timeout = timeout
        self.request_attempts = max(1, request_attempts)

    def _completion_body(self, messages: list[dict[str, str]], max_tokens: int, temperature: float = 0.15) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.config.model.startswith("deepseek-"):
            body["thinking"] = {"type": "disabled"}
        return body

    async def _post_completion(self, body: dict[str, Any]) -> str:
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        response: httpx.Response | Any | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.request_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        build_chat_completions_url(self.config.base_url),
                        headers=headers,
                        json=body,
                    )
            except httpx.InvalidURL as exc:
                raise ValueError("AI base_url format is invalid") from exc
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self.request_attempts:
                    continue
                raise ValueError("AI service timed out during Agent deep scoring") from exc
            except httpx.ConnectError as exc:
                last_error = exc
                if attempt < self.request_attempts:
                    continue
                raise ValueError("Cannot connect to AI base_url") from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.request_attempts:
                    continue
                raise ValueError(f"AI request failed: {exc.__class__.__name__}") from exc

            if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                if attempt < self.request_attempts:
                    continue
            break

        if response is None:
            raise ValueError(f"AI request failed: {last_error.__class__.__name__ if last_error else 'UnknownError'}")

        if response.status_code == 401:
            raise ValueError("AI API Key is invalid or unauthorized")
        if response.status_code == 403:
            raise ValueError("AI API Key has insufficient permission or account is unavailable")
        if response.status_code == 400:
            raise ValueError(f"AI model or request parameters are invalid: {response.text[:300]}")
        if response.status_code == 404:
            raise ValueError("AI base_url endpoint or model was not found")
        if response.status_code >= 400:
            raise ValueError(f"AI service returned HTTP {response.status_code}: {response.text[:300]}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("AI response is not compatible with OpenAI Chat Completions") from exc
        return str(content)

    def _initial_messages(self, stage: str, payload: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are a strict read-only code review agent. You may only judge from "
                    "provided repository index, safe tool observations, and evidence snippets. "
                    "Return strict JSON only. User-facing text should be Chinese."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"stage": stage, **payload}, ensure_ascii=False),
            },
        ]

    def _repair_messages(
        self,
        stage: str,
        payload: dict[str, Any],
        invalid_output: str,
        parse_error: str,
    ) -> list[dict[str, str]]:
        schema_hint = {
            key: payload[key]
            for key in ("output_schema", "default_rubric", "rules")
            if key in payload
        }
        repair_request = {
            "stage": stage,
            "task": "Repair the previous assistant output into one syntactically valid JSON object.",
            "requirements": [
                "Return JSON only.",
                "Do not use markdown fences.",
                "Do not add commentary.",
                "Preserve the intended keys and values as much as possible.",
            ],
            "parse_error": parse_error,
            "schema_hint": schema_hint,
            "invalid_output": invalid_output[:6000],
        }
        return [
            {
                "role": "system",
                "content": "You repair malformed JSON. Return exactly one valid JSON object and nothing else.",
            },
            {"role": "user", "content": json.dumps(repair_request, ensure_ascii=False)},
        ]

    async def complete_json(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._completion_body(
            messages=self._initial_messages(stage, payload),
            max_tokens=_max_tokens_for_stage(stage),
        )
        content = await self._post_completion(body)
        try:
            return _extract_json(content)
        except ValueError as parse_exc:
            repair_body = self._completion_body(
                messages=self._repair_messages(stage, payload, content, str(parse_exc)),
                max_tokens=_max_tokens_for_stage(stage),
                temperature=0,
            )
            repair_content = await self._post_completion(repair_body)
            try:
                return _extract_json(repair_content)
            except ValueError as repair_exc:
                raise ValueError(f"AI Agent stage {stage} returned invalid JSON after repair: {repair_exc}") from repair_exc


def _max_tokens_for_stage(stage: str) -> int:
    if stage == "final_report":
        return 2200
    if stage.startswith("dimension_judge"):
        return 1600
    if stage in {"critic_review", "score_calibrator", "evidence_curator"}:
        return 1800
    return 1000


def _emit(
    state: AgentState,
    event_id: str,
    label: str,
    status: str,
    detail: str = "",
    kind: str = "node",
    target: str | None = None,
    dimension: str | None = None,
) -> None:
    callback = state.get("progress_callback")
    if not callback:
        return
    callback(
        {
            "id": event_id,
            "label": label,
            "status": status,
            "detail": detail,
            "kind": kind,
            "target": target,
            "dimension": dimension,
        }
    )


def _append_trace(state: AgentState, item: str) -> None:
    state["trace"] = [*state.get("trace", []), item]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value)[:48].strip("_")
    return cleaned or "target"


def _clamp(value: int | float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, round(float(value))))


def _index_snapshot(index: RepoIndex, github_evidence: dict[str, Any]) -> dict[str, Any]:
    repo = github_evidence.get("repo") or {}
    return {
        "repo": {
            "full_name": repo.get("full_name"),
            "description": repo.get("description"),
            "default_branch": repo.get("default_branch"),
            "topics": repo.get("topics") or [],
        },
        "counts": {
            "files": index.file_count,
            "directories": index.directory_count,
            "bytes": index.total_bytes,
        },
        "extension_counts": index.extension_counts,
        "tree_sample": index.tree[:220],
        "source_files": index.source_files[:120],
        "test_files": index.test_files[:80],
        "documentation_files": index.documentation_files[:60],
        "manifest_files": index.manifest_files[:60],
        "ci_files": index.ci_files[:60],
        "config_files": index.config_files[:80],
        "security_files": index.security_files[:40],
        "large_files": index.large_files[:40],
        "has_tests": index.has_tests,
        "has_ci": index.has_ci,
        "has_docs": index.has_docs,
        "has_license": index.has_license,
        "has_security_policy": index.has_security_policy,
    }


def _compact_observations(observations: list[AgentObservation], limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "step": item.step,
            "action": item.action,
            "target": item.target,
            "dimension": item.dimension,
            "result_summary": item.result_summary,
        }
        for item in observations[-limit:]
    ]


def _compact_evidence(evidence_pool: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    compacted = []
    for item in evidence_pool[-limit:]:
        compacted.append(
            {
                "id": item.get("id"),
                "file": item.get("file"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "dimension": item.get("dimension"),
                "action": item.get("action"),
                "snippet": str(item.get("snippet") or "")[:700],
                "reason": item.get("reason"),
            }
        )
    return compacted


FORBIDDEN_SCOUT_KEY_PARTS = ("score",)


def _get_field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return value


def _scrub_score_text(value: str, limit: int = 700) -> str:
    text = value[:limit]
    text = re.sub(r"\b\d+\s*/\s*\d+\b", "[redacted]", text)
    text = re.sub(r"\b\d+\s*(分|points?)\b", "[redacted]", text, flags=re.IGNORECASE)
    return text


def _strip_score_fields(value: Any) -> Any:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        stripped: dict[str, Any] = {}
        for key, child in plain.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in FORBIDDEN_SCOUT_KEY_PARTS):
                continue
            stripped[key_text] = _strip_score_fields(child)
        return stripped
    if isinstance(plain, list):
        return [_strip_score_fields(item) for item in plain[:80]]
    if isinstance(plain, str):
        return _scrub_score_text(plain)
    return plain


def _scout_evidence_item(value: Any) -> dict[str, Any]:
    plain = _to_plain(value)
    if not isinstance(plain, dict):
        return {"excerpt": _scrub_score_text(str(value))}
    allowed = {
        "label",
        "path",
        "file",
        "line_start",
        "line_end",
        "excerpt",
        "snippet",
        "reason",
        "dimension",
        "action",
        "target",
        "result_summary",
        "match",
    }
    return _strip_score_fields({key: plain.get(key) for key in allowed if plain.get(key) is not None})


def _looks_like_gap(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("未发现", "缺少", "不足", "limited", "missing", "gap", "thin", "no "))


def _build_rule_scout_map(core_report: Any | None) -> dict[str, Any]:
    if not core_report:
        return {}

    core_score = _get_field(core_report, "core_score", {})
    dimensions = _get_field(core_score, "dimensions", {}) or {}
    dimension_clues: list[dict[str, Any]] = []
    coverage_gaps: list[dict[str, str]] = []
    path_candidates: list[str] = []

    for dimension, raw_dimension in dimensions.items():
        evidence = [_scout_evidence_item(item) for item in (_get_field(raw_dimension, "evidence", []) or [])]
        evidence = [item for item in evidence if item]
        for item in evidence:
            path = item.get("path") or item.get("file")
            if path:
                path_candidates.append(str(path))
        reason = _scrub_score_text(str(_get_field(raw_dimension, "reason", "")))
        clue = {
            "dimension": str(dimension),
            "reason": reason,
            "evidence": evidence[:8],
        }
        dimension_clues.append(_strip_score_fields(clue))
        combined_text = " ".join([reason, *[str(item) for item in evidence]])
        if not evidence or _looks_like_gap(combined_text):
            coverage_gaps.append(
                {
                    "dimension": str(dimension),
                    "hint": "基础规则证据提示该维度需要 AI Agent 追加本地佐证。",
                }
            )

    exploration_clues = []
    for note in (_get_field(core_report, "exploration_notes", []) or [])[:16]:
        exploration_clues.append(_scout_evidence_item(note))
        target = _get_field(note, "target")
        if target:
            path_candidates.append(str(target))

    evidence_clues = []
    for evidence in (_get_field(core_report, "evidence_pool", []) or [])[:40]:
        item = _scout_evidence_item(evidence)
        evidence_clues.append(item)
        path = item.get("path") or item.get("file")
        if path:
            path_candidates.append(str(path))

    risk_flags = [
        _scrub_score_text(str(item))
        for item in (_get_field(core_score, "risk_flags", []) or [])[:12]
    ]
    recommendations = [
        _scrub_score_text(str(item))
        for item in (_get_field(core_report, "recommendations", []) or [])[:12]
    ]

    scout_map = {
        "purpose": "基础规则评分给 AI Agent 的侦查地图：只包含证据线索、缺口和候选路径，不包含任何分值。",
        "usage_rule": "第 1 步先自主探索；第 2 步起参考本地图补齐证据，不要把它当结论。",
        "dimension_clues": dimension_clues,
        "coverage_gaps": coverage_gaps,
        "exploration_clues": exploration_clues,
        "evidence_clues": evidence_clues,
        "risk_flags": risk_flags,
        "recommendations": recommendations,
        "path_candidates": sorted(dict.fromkeys(path_candidates))[:40],
    }
    return _strip_score_fields(scout_map)


def _normalize_rubric(raw: dict[str, Any]) -> dict[str, Any]:
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = DEFAULT_RUBRIC
    normalized: dict[str, int] = {}
    for key, value in dimensions.items():
        if key not in DEFAULT_RUBRIC:
            continue
        try:
            normalized[key] = _clamp(int(value), 1, 100)
        except (TypeError, ValueError):
            continue
    if set(normalized) != set(DEFAULT_RUBRIC) or sum(normalized.values()) != 100:
        normalized = dict(DEFAULT_RUBRIC)
    return {
        "dimensions": normalized,
        "rationale": str(raw.get("rationale") or "Default rubric selected for read-only AI Agent review."),
    }


def _fallback_project_profile(index: RepoIndex) -> dict[str, Any]:
    language_by_extension = {
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".vue": "Vue",
        ".py": "Python",
        ".java": "Java",
        ".go": "Go",
        ".rs": "Rust",
        ".kt": "Kotlin",
        ".cs": "C#",
        ".php": "PHP",
        ".rb": "Ruby",
    }
    source_extensions = {
        ext: count
        for ext, count in index.extension_counts.items()
        if ext in language_by_extension
    }
    primary_extension = max(source_extensions, key=source_extensions.get) if source_extensions else ""
    manifest_names = {path.lower().split("/")[-1] for path in index.manifest_files}
    package_managers = []
    if "package.json" in manifest_names:
        package_managers.append("npm")
    if "pyproject.toml" in manifest_names:
        package_managers.append("pyproject")
    if "requirements.txt" in manifest_names:
        package_managers.append("pip")
    if "go.mod" in manifest_names:
        package_managers.append("go modules")
    if "cargo.toml" in manifest_names:
        package_managers.append("cargo")
    if "pom.xml" in manifest_names:
        package_managers.append("maven")

    lower_tree = [path.lower() for path in index.tree]
    frameworks = []
    if any(path.endswith(".vue") for path in lower_tree):
        frameworks.append("Vue")
    if any(path.endswith(".tsx") for path in lower_tree):
        frameworks.append("React/TSX")
    if any(path.endswith("vite.config.ts") or path.endswith("vite.config.js") for path in lower_tree):
        frameworks.append("Vite")
    if any(path.endswith("fastapi") for path in lower_tree):
        frameworks.append("FastAPI")

    return {
        "project_type": "unknown",
        "primary_language": language_by_extension.get(primary_extension, "unknown"),
        "frameworks": frameworks,
        "package_managers": package_managers,
        "app_purpose": "Fallback classification from repository index; AI classifier was unavailable.",
        "confidence": "low",
    }


def _tool_signature(call: AgentToolCall) -> str:
    return f"{call.action}:{call.target}".lower()


def _fallback_tool_call(
    index: RepoIndex,
    step: int,
    seen_signatures: set[str] | None = None,
) -> AgentToolCall:
    seen_signatures = seen_signatures or set()
    readable = {path.lower(): path for path in index.tree}
    candidates: list[AgentToolCall] = [
        AgentToolCall(
            thought="Fallback root listing.",
            action="list_dir",
            target=".",
            dimension="architecture_quality",
            reason="List the root directory to orient the agent.",
        )
    ]
    for candidate in ENTRY_CANDIDATES:
        if candidate.lower() in readable:
            candidates.append(
                AgentToolCall(
                    thought=f"Fallback inspection of {candidate}.",
                    action="read_file",
                    target=readable[candidate.lower()],
                    dimension="engineering",
                    reason="The model did not provide a valid tool call, so inspect a high-value project file.",
                )
            )
        elif any(path.startswith(f"{candidate.lower().rstrip('/')}/") for path in readable):
            candidates.append(
                AgentToolCall(
                    thought=f"Fallback listing of {candidate}.",
                    action="list_dir",
                    target=candidate,
                    dimension="engineering",
                    reason="Inspect a high-value project directory when the model decision is unavailable.",
                )
            )
    candidates.extend(
        [
            AgentToolCall(
                thought="Fallback test grep.",
                action="grep",
                target=DEFAULT_TEST_PATTERN,
                dimension="testing",
                reason="Look for test syntax when no valid decision is available.",
                include_globs=["tests/**", "test/**", "src/**"],
                max_matches=20,
            ),
            AgentToolCall(
                thought="Fallback security grep.",
                action="grep",
                target=DEFAULT_SECURITY_PATTERN,
                dimension="security",
                reason="Look for common secret and dangerous execution patterns.",
                max_matches=20,
            ),
            AgentToolCall(
                thought="Fallback maintainability grep.",
                action="grep",
                target=DEFAULT_MAINTAINABILITY_PATTERN,
                dimension="maintenance",
                reason="Look for TODO/FIXME/HACK markers.",
                max_matches=20,
            ),
            AgentToolCall(
                thought="Fallback test file discovery.",
                action="find_files",
                target="*test*",
                dimension="testing",
                reason="Find likely test files if grep decisions are unavailable.",
                max_matches=20,
            ),
        ]
    )
    for candidate in candidates:
        if _tool_signature(candidate) not in seen_signatures:
            return candidate
    return AgentToolCall(
        thought="Fallback stop: all deterministic backup tool calls were already used.",
        action="finish",
        target="",
        dimension="overall",
        reason=f"Stopped after {step - 1} steps to avoid repeated tool calls.",
    )


def _fallback_dimension_score(
    state: AgentState,
    dimension: str,
    max_score: int,
    error: str,
) -> AgentDimensionScore:
    evidence = _evidence_for_dimension(state, dimension)
    if len(evidence) >= 5:
        ratio = 0.55
    elif evidence:
        ratio = 0.45
    else:
        ratio = 0.3
    score = _clamp(max_score * ratio, 0, max_score)
    refs = [str(item.get("id") or item.get("file")) for item in evidence[:3] if item.get("id") or item.get("file")]
    return AgentDimensionScore(
        dimension=dimension,
        score=score,
        max_score=max_score,
        confidence="low",
        reasoning=f"{dimension} judge fallback used because the AI response failed: {error}",
        evidence_refs=refs,
        strengths=["保守沿用已收集的本地证据。"] if refs else [],
        risks=["该维度 AI 评委失败，分数可信度较低。"],
        recommendations=["重新运行 AI Agent 深度评分，或增加该维度的可读证据后再评估。"],
    )


def _fallback_critic_review(state: AgentState, error: str) -> AgentCriticReview:
    low_confidence = [
        score.dimension
        for score in state.get("dimensions", {}).values()
        if score.confidence == "low"
    ]
    return AgentCriticReview(
        verdict=f"critic_review fallback used because the AI critic failed: {error}",
        concerns=["部分评分来自兜底逻辑，需要按低置信度解读。"],
        evidence_gaps=[f"{dimension} 缺少稳定 AI 复核。" for dimension in low_confidence[:4]],
        score_adjustments={},
    )


def _fallback_final_report(state: AgentState, error: str) -> dict[str, Any]:
    dimensions = list(state.get("dimensions", {}).values())
    ranked = sorted(
        dimensions,
        key=lambda score: score.score / score.max_score if score.max_score else 0,
    )
    weakest = [score.dimension for score in ranked[:3]]
    strongest = [score.dimension for score in ranked[-3:]]
    return {
        "summary": (
            "fallback 报告：AI Agent 已完成只读探索、维度评审和分数聚合，但最终报告生成失败，"
            f"因此使用结构化兜底摘要。当前总分 {state.get('aggregate_score', 0)}/100，"
            f"置信度 {state.get('confidence', 'low')}。失败原因：{error}"
        )
        ,
        "strengths": [f"{dimension} 相对表现较好。" for dimension in strongest],
        "risks": [f"{dimension} 证据或评分稳定性偏弱。" for dimension in weakest],
        "recommendations": [
            "重新运行 AI Agent 深度评分以获得完整自然语言报告。",
            "优先补充低分维度的测试、文档、安全和架构证据。",
        ],
        "calibration_rationale": state.get("calibration_rationale", ""),
    }


def _parse_tool_call(
    raw: dict[str, Any],
    index: RepoIndex,
    step: int,
    seen_signatures: set[str] | None = None,
) -> AgentToolCall:
    try:
        call = AgentToolCall.model_validate(raw)
    except ValidationError:
        return _fallback_tool_call(index, step, seen_signatures)
    if call.action != "finish" and not call.target:
        return _fallback_tool_call(index, step, seen_signatures)
    return call


def _evidence_id(step: int, action: str, ordinal: int, target: str) -> str:
    return f"ev_{step}_{action}_{ordinal}_{_slug(target)}"


def _summarize_files(files: list[str], limit: int = 4) -> str:
    if not files:
        return "no files"
    suffix = "" if len(files) <= limit else f" and {len(files) - limit} more"
    return ", ".join(files[:limit]) + suffix


def _execute_tool(repo_root: Path, call: AgentToolCall, step: int) -> AgentObservation:
    evidence: list[dict[str, Any]] = []
    try:
        if call.action == "read_file":
            result = read_file_safe(
                repo_root,
                call.target,
                start_line=call.start_line,
                max_lines=call.max_lines,
            )
            evidence.append(
                {
                    "id": _evidence_id(step, call.action, 1, result["file"]),
                    "step": step,
                    "action": call.action,
                    "dimension": call.dimension,
                    "file": result["file"],
                    "line_start": result["line_start"],
                    "line_end": result["line_end"],
                    "total_lines": result["total_lines"],
                    "snippet": str(result["content"])[:1800],
                    "reason": call.reason,
                }
            )
            summary = (
                f"Read {result['file']} lines {result['line_start']}-{result['line_end']}"
                + ("; truncated" if result.get("truncated") else "")
            )
        elif call.action == "grep":
            matches = grep_safe(
                repo_root,
                call.target,
                include_globs=call.include_globs,
                max_matches=call.max_matches,
                timeout=5,
            )
            for index, match in enumerate(matches, start=1):
                evidence.append(
                    {
                        "id": _evidence_id(step, call.action, index, f"{match['file']}:{match['line']}"),
                        "step": step,
                        "action": call.action,
                        "dimension": call.dimension,
                        "file": match["file"],
                        "line_start": match["line"],
                        "line_end": match["line"],
                        "snippet": match["snippet"],
                        "match": match["match"],
                        "reason": call.reason,
                    }
                )
            summary = f"Found {len(matches)} matches in {_summarize_files(sorted({m['file'] for m in matches}))}"
        elif call.action == "list_dir":
            entries = list_dir_safe(repo_root, call.target or ".")
            snippet = json.dumps(entries[:80], ensure_ascii=False)
            evidence.append(
                {
                    "id": _evidence_id(step, call.action, 1, call.target or "."),
                    "step": step,
                    "action": call.action,
                    "dimension": call.dimension,
                    "file": call.target or ".",
                    "line_start": None,
                    "line_end": None,
                    "snippet": snippet[:1800],
                    "reason": call.reason,
                }
            )
            summary = f"Listed {len(entries)} entries under {call.target or '.'}"
        elif call.action == "find_files":
            files = find_files_safe(repo_root, call.target, max_results=call.max_matches)
            for index, file in enumerate(files, start=1):
                evidence.append(
                    {
                        "id": _evidence_id(step, call.action, index, file),
                        "step": step,
                        "action": call.action,
                        "dimension": call.dimension,
                        "file": file,
                        "line_start": None,
                        "line_end": None,
                        "snippet": file,
                        "reason": call.reason,
                    }
                )
            summary = f"Found {len(files)} files: {_summarize_files(files)}"
        else:
            summary = "No tool executed."
    except (OSError, ValueError) as exc:
        summary = f"Tool failed: {exc}"

    return AgentObservation(
        step=step,
        thought=call.thought or call.reason,
        action=call.action,
        target=call.target,
        dimension=call.dimension,
        result_summary=summary,
        evidence=evidence,
    )


async def _repo_indexer(state: AgentState) -> AgentState:
    _emit(state, "repo_indexer", "repo_indexer", "running", "Building repository map from local index.")
    state["repo_map"] = _index_snapshot(state["index"], state.get("github_evidence") or {})
    _append_trace(state, "repo_indexer")
    _emit(
        state,
        "repo_indexer",
        "repo_indexer",
        "completed",
        f"Mapped {state['index'].file_count} files and {state['index'].directory_count} directories.",
    )
    return state


async def _project_classifier(state: AgentState) -> AgentState:
    _emit(state, "project_classifier", "project_classifier", "running", "Classifying project type and stack.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "project_classifier",
            {
                "task": "Classify the repository from the index. Return JSON with project_type, primary_language, frameworks, package_managers, app_purpose, confidence.",
                "repo_map": state["repo_map"],
            },
        )
    except ValueError as exc:
        fallback_used = True
        raw = _fallback_project_profile(state["index"])
    state["project_profile"] = {
        "project_type": str(raw.get("project_type") or "unknown"),
        "primary_language": str(raw.get("primary_language") or "unknown"),
        "frameworks": raw.get("frameworks") if isinstance(raw.get("frameworks"), list) else [],
        "package_managers": raw.get("package_managers") if isinstance(raw.get("package_managers"), list) else [],
        "app_purpose": str(raw.get("app_purpose") or ""),
        "confidence": raw.get("confidence") if raw.get("confidence") in {"low", "medium", "high"} else "medium",
    }
    _append_trace(state, "project_classifier")
    _emit(
        state,
        "project_classifier",
        "project_classifier",
        "completed",
        (
            "project_classifier fallback used. "
            if fallback_used
            else ""
        )
        + f"{state['project_profile']['project_type']} / {state['project_profile']['primary_language']}",
    )
    return state


async def _rubric_selector(state: AgentState) -> AgentState:
    _emit(state, "rubric_selector", "rubric_selector", "running", "Selecting weighted scoring rubric.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "rubric_selector",
            {
                "task": "Select a 100-point scoring rubric for this project. Keep exactly these dimension keys: functionality, architecture_quality, engineering, testing, security, documentation, maintenance.",
                "default_rubric": DEFAULT_RUBRIC,
                "project_profile": state["project_profile"],
                "repo_map": state["repo_map"],
            },
        )
    except ValueError as exc:
        fallback_used = True
        raw = {
            "dimensions": DEFAULT_RUBRIC,
            "rationale": f"rubric_selector fallback used because the AI response failed: {exc}",
        }
    state["rubric"] = _normalize_rubric(raw)
    _append_trace(state, "rubric_selector")
    detail = state["rubric"]["rationale"]
    if fallback_used and "fallback" not in detail.lower():
        detail = f"rubric_selector fallback used. {detail}"
    _emit(state, "rubric_selector", "rubric_selector", "completed", detail)
    return state


def _explorer_payload(state: AgentState, step: int) -> dict[str, Any]:
    payload = {
        "task": "Choose the next read-only repository tool call. Return action read_file, grep, list_dir, find_files, or finish.",
        "rules": {
            "allowed_actions": ["read_file", "grep", "list_dir", "find_files", "finish"],
            "do_not_execute_code": True,
            "prefer_specific_evidence": True,
            "use_security_pattern_when_needed": DEFAULT_SECURITY_PATTERN,
            "use_test_pattern_when_needed": DEFAULT_TEST_PATTERN,
            "use_maintainability_pattern_when_needed": DEFAULT_MAINTAINABILITY_PATTERN,
        },
        "step": step,
        "repo_map": state["repo_map"],
        "project_profile": state["project_profile"],
        "rubric": state["rubric"],
        "recent_observations": _compact_observations(state.get("exploration_steps", [])),
        "current_evidence": _compact_evidence(state.get("evidence_pool", []), limit=35),
    }
    if step > 1 and state.get("rule_scout_map"):
        payload["rule_scout_map"] = state["rule_scout_map"]
        payload["scout_map_rules"] = {
            "purpose": "Use this as a scout map for evidence gaps and candidate paths only.",
            "do_not_treat_as_conclusion": True,
            "do_not_assume_scores": True,
            "prefer_verifying_scout_clues_with_tools": True,
        }
    return payload


async def _evidence_explorer_loop(state: AgentState) -> AgentState:
    _emit(state, "evidence_explorer_loop", "evidence_explorer_loop", "running", "Starting AI-led tool exploration.")
    observations: list[AgentObservation] = []
    evidence_pool: list[dict[str, Any]] = []
    seen_tool_calls: set[str] = set()
    max_steps = max(1, min(int(state.get("max_exploration_steps") or 18), 30))
    for step in range(1, max_steps + 1):
        _emit(state, f"explorer_decide_{step}", "explorer_decide", "running", f"Choosing tool call #{step}.")
        fallback_detail = ""
        try:
            raw = await state["llm"].complete_json("explorer_decide", _explorer_payload(state, step))
        except ValueError as exc:
            raw = {}
            fallback_detail = f"explorer_decide fallback used because the AI response failed: {exc}. "
        call = _parse_tool_call(raw, state["index"], step, seen_tool_calls)
        if call.action != "finish" and _tool_signature(call) in seen_tool_calls:
            repeated = f"{call.action} {call.target}"
            call = _fallback_tool_call(state["index"], step, seen_tool_calls)
            fallback_detail += f"Duplicate tool call avoided: {repeated}. "
        _emit(
            state,
            f"explorer_decide_{step}",
            "explorer_decide",
            "completed",
            fallback_detail + (call.thought or call.reason),
            target=call.target or None,
            dimension=call.dimension,
        )

        if call.action == "finish":
            observation = AgentObservation(
                step=step,
                thought=call.thought or call.reason,
                action="finish",
                target="",
                dimension=call.dimension,
                result_summary=call.reason or "Explorer decided to stop.",
                evidence=[],
            )
            observations.append(observation)
            _emit(state, f"should_continue_{step}", "should_continue", "completed", "Explorer stopped.")
            break
        seen_tool_calls.add(_tool_signature(call))

        _emit(
            state,
            f"tool_router_{step}",
            "tool_router",
            "running",
            f"Routing {call.action} to safe repository tool.",
            target=call.target,
            dimension=call.dimension,
        )
        tool_event_id = f"tool_{step}_{call.action}_{_slug(call.target)}"
        _emit(
            state,
            tool_event_id,
            call.action,
            "running",
            call.reason,
            kind="tool",
            target=call.target,
            dimension=call.dimension,
        )
        observation = _execute_tool(state["repo_root"], call, step)
        status = "failed" if observation.result_summary.startswith("Tool failed:") else "completed"
        _emit(
            state,
            tool_event_id,
            call.action,
            status,
            observation.result_summary,
            kind="tool",
            target=call.target,
            dimension=call.dimension,
        )
        _emit(
            state,
            f"tool_router_{step}",
            "tool_router",
            "completed",
            observation.result_summary,
            target=call.target,
            dimension=call.dimension,
        )

        observations.append(observation)
        evidence_pool.extend(observation.evidence)
        state["exploration_steps"] = observations
        state["evidence_pool"] = evidence_pool
        _emit(
            state,
            f"observe_{step}",
            "observe",
            "completed",
            observation.result_summary,
            target=call.target,
            dimension=call.dimension,
        )
        _emit(
            state,
            f"should_continue_{step}",
            "should_continue",
            "completed",
            f"{len(evidence_pool)} evidence items collected.",
        )

    state["exploration_steps"] = observations
    state["evidence_pool"] = evidence_pool
    _append_trace(state, "evidence_explorer_loop")
    _emit(state, "evidence_explorer_loop", "evidence_explorer_loop", "completed", f"{len(observations)} steps, {len(evidence_pool)} evidence items.")
    return state


def _fallback_curated_evidence(evidence_pool: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {}
    for item in evidence_pool:
        dimension = str(item.get("dimension") or "overall")
        grouped.setdefault(dimension, []).append(str(item.get("id") or item.get("file") or "evidence"))
    return grouped


async def _evidence_curator(state: AgentState) -> AgentState:
    _emit(state, "evidence_curator", "evidence_curator", "running", "Curating and grouping evidence by dimension.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "evidence_curator",
            {
                "task": "Deduplicate and group evidence by scoring dimension. Return curated_evidence object and notes array.",
                "project_profile": state["project_profile"],
                "rubric": state["rubric"],
                "evidence_pool": _compact_evidence(state.get("evidence_pool", []), limit=100),
            },
        )
    except ValueError as exc:
        fallback_used = True
        raw = {
            "curated_evidence": _fallback_curated_evidence(state.get("evidence_pool", [])),
            "notes": [f"evidence_curator fallback used because the AI response failed: {exc}"],
        }
    curated = raw.get("curated_evidence")
    if not isinstance(curated, dict):
        fallback_used = True
        curated = _fallback_curated_evidence(state.get("evidence_pool", []))
    state["curated_evidence"] = {"groups": curated, "notes": raw.get("notes") if isinstance(raw.get("notes"), list) else []}
    _append_trace(state, "evidence_curator")
    detail = f"Curated {len(curated)} groups."
    if fallback_used:
        detail = f"evidence_curator fallback used. {detail}"
    _emit(state, "evidence_curator", "evidence_curator", "completed", detail)
    return state


def _evidence_for_dimension(state: AgentState, dimension: str) -> list[dict[str, Any]]:
    direct = [item for item in state.get("evidence_pool", []) if item.get("dimension") == dimension]
    related = []
    for item in state.get("evidence_pool", []):
        if item in direct:
            continue
        file = str(item.get("file") or "").lower()
        if dimension == "testing" and ("test" in file or "spec" in file):
            related.append(item)
        elif dimension == "documentation" and ("readme" in file or file.startswith("docs/")):
            related.append(item)
        elif dimension == "engineering" and file.split("/")[-1] in {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "cargo.toml"}:
            related.append(item)
        elif dimension == "security" and any(token in str(item.get("snippet") or "").lower() for token in ["secret", "token", "password", "shell=true", "eval("]):
            related.append(item)
    combined = [*direct, *related]
    return _compact_evidence(combined or state.get("evidence_pool", []), limit=35)


def _coerce_dimension_score(dimension: str, max_score: int, raw: dict[str, Any]) -> AgentDimensionScore:
    try:
        score = _clamp(int(raw.get("score", 0)), 0, max_score)
    except (TypeError, ValueError):
        score = 0
    confidence = raw.get("confidence") if raw.get("confidence") in {"low", "medium", "high"} else "medium"
    return AgentDimensionScore(
        dimension=dimension,
        score=score,
        max_score=max_score,
        confidence=confidence,
        reasoning=str(raw.get("reasoning") or "No reasoning returned by judge."),
        evidence_refs=[str(item) for item in raw.get("evidence_refs", []) if item],
        strengths=[str(item) for item in raw.get("strengths", []) if item],
        risks=[str(item) for item in raw.get("risks", []) if item],
        recommendations=[str(item) for item in raw.get("recommendations", []) if item],
    )


async def _dimension_judges(state: AgentState) -> AgentState:
    _emit(state, "dimension_judges", "dimension_judges", "running", "Running dimension-specific AI judges.")
    dimensions: dict[str, AgentDimensionScore] = {}
    rubric_dimensions = state["rubric"]["dimensions"]
    for dimension, max_score in rubric_dimensions.items():
        event_id = f"dimension_judge_{dimension}"
        _emit(state, event_id, f"{dimension}_judge", "running", f"Scoring {dimension}.", dimension=dimension)
        fallback_used = False
        try:
            raw = await state["llm"].complete_json(
                f"dimension_judge:{dimension}",
                {
                    "task": "Score this single dimension from local evidence only.",
                    "dimension": dimension,
                    "max_score": max_score,
                    "project_profile": state["project_profile"],
                    "rubric": state["rubric"],
                    "evidence": _evidence_for_dimension(state, dimension),
                    "output_schema": {
                        "score": f"0..{max_score}",
                        "max_score": max_score,
                        "confidence": "low|medium|high",
                        "reasoning": "Chinese evidence-based reasoning",
                        "evidence_refs": ["evidence ids"],
                        "strengths": ["Chinese list"],
                        "risks": ["Chinese list"],
                        "recommendations": ["Chinese list"],
                    },
                },
            )
            score = _coerce_dimension_score(dimension, int(max_score), raw)
        except ValueError as exc:
            fallback_used = True
            score = _fallback_dimension_score(state, dimension, int(max_score), str(exc))
        dimensions[dimension] = score
        detail = f"{score.score}/{score.max_score}: {score.reasoning}"
        if fallback_used and "fallback" not in detail.lower():
            detail = f"{dimension}_judge fallback used. {detail}"
        _emit(state, event_id, f"{dimension}_judge", "completed", detail, dimension=dimension)
    state["dimensions"] = dimensions
    _append_trace(state, "dimension_judges")
    _emit(state, "dimension_judges", "dimension_judges", "completed", f"{len(dimensions)} judges completed.")
    return state


async def _critic_review(state: AgentState) -> AgentState:
    _emit(state, "critic_review", "critic_review", "running", "Reviewing score consistency and evidence gaps.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "critic_review",
            {
                "task": "Critique the dimension scores for unsupported claims, over-scoring, contradictions, and evidence gaps.",
                "project_profile": state["project_profile"],
                "dimensions": {key: value.model_dump() for key, value in state["dimensions"].items()},
                "evidence_pool": _compact_evidence(state.get("evidence_pool", []), limit=80),
            },
        )
        try:
            state["critic_review"] = AgentCriticReview.model_validate(raw)
        except ValidationError:
            fallback_used = True
            state["critic_review"] = AgentCriticReview(
                verdict=str(raw.get("verdict") or "critic_review fallback used because response was incomplete."),
                concerns=[str(item) for item in raw.get("concerns", []) if item],
                evidence_gaps=[str(item) for item in raw.get("evidence_gaps", []) if item],
                score_adjustments={},
            )
    except ValueError as exc:
        fallback_used = True
        state["critic_review"] = _fallback_critic_review(state, str(exc))
    _append_trace(state, "critic_review")
    detail = state["critic_review"].verdict
    if fallback_used and "fallback" not in detail.lower():
        detail = f"critic_review fallback used. {detail}"
    _emit(state, "critic_review", "critic_review", "completed", detail)
    return state


def _deterministic_calibration(state: AgentState) -> dict[str, int]:
    adjustments = state["critic_review"].score_adjustments or {}
    calibrated: dict[str, int] = {}
    for dimension, score in state["dimensions"].items():
        try:
            adjustment = int(adjustments.get(dimension, 0))
        except (TypeError, ValueError):
            adjustment = 0
        calibrated[dimension] = _clamp(score.score + adjustment, 0, score.max_score)
    return calibrated


async def _score_calibrator(state: AgentState) -> AgentState:
    _emit(state, "score_calibrator", "score_calibrator", "running", "Calibrating dimension scores after critic review.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "score_calibrator",
            {
                "task": "Apply critic review to produce final calibrated dimension scores. Do not exceed max_score.",
                "dimensions": {key: value.model_dump() for key, value in state["dimensions"].items()},
                "critic_review": state["critic_review"].model_dump(),
                "output_schema": {
                    "calibrated_dimensions": {
                        key: f"integer 0..{value.max_score}"
                        for key, value in state["dimensions"].items()
                    },
                    "rationale": "Chinese explanation of score changes",
                },
            },
        )
    except ValueError as exc:
        fallback_used = True
        raw = {
            "calibrated_dimensions": _deterministic_calibration(state),
            "rationale": f"score_calibrator fallback used because AI returned invalid JSON: {exc}",
        }
    calibrated_raw = raw.get("calibrated_dimensions")
    if not isinstance(calibrated_raw, dict):
        fallback_used = True
        calibrated_raw = _deterministic_calibration(state)
    calibrated: dict[str, int] = {}
    for dimension, score in state["dimensions"].items():
        try:
            value = int(calibrated_raw.get(dimension, score.score))
        except (TypeError, ValueError):
            value = score.score
        calibrated[dimension] = _clamp(value, 0, score.max_score)
    state["calibrated_dimensions"] = calibrated
    state["calibration_rationale"] = str(raw.get("rationale") or "")
    _append_trace(state, "score_calibrator")
    detail = state["calibration_rationale"] or "Calibration completed."
    if fallback_used and "fallback" not in detail.lower():
        detail = f"score_calibrator fallback used. {detail}"
        state["calibration_rationale"] = detail
    _emit(state, "score_calibrator", "score_calibrator", "completed", detail)
    return state


async def _aggregate_score(state: AgentState) -> AgentState:
    _emit(state, "aggregate_score", "aggregate_score", "running", "Aggregating calibrated scores.")
    total = _clamp(sum(state["calibrated_dimensions"].values()), 0, 100)
    confidence_counts = Counter(score.confidence for score in state["dimensions"].values())
    if confidence_counts["low"] >= 2 or len(state.get("evidence_pool", [])) < 3:
        confidence = "low"
    elif confidence_counts["high"] >= 4:
        confidence = "high"
    else:
        confidence = "medium"
    state["aggregate_score"] = total
    state["confidence"] = confidence
    _append_trace(state, "aggregate_score")
    _emit(state, "aggregate_score", "aggregate_score", "completed", f"AI Agent score {total}/100.")
    return state


async def _final_report(state: AgentState) -> AgentState:
    _emit(state, "final_report", "final_report", "running", "Generating final AI Agent report.")
    fallback_used = False
    try:
        raw = await state["llm"].complete_json(
            "final_report",
            {
                "task": "Write the final Chinese report from the agent evidence chain, scores, critic review, and calibration.",
                "score": state["aggregate_score"],
                "confidence": state["confidence"],
                "project_profile": state["project_profile"],
                "rubric": state["rubric"],
                "dimensions": {key: value.model_dump() for key, value in state["dimensions"].items()},
                "calibrated_dimensions": state["calibrated_dimensions"],
                "critic_review": state["critic_review"].model_dump(),
                "exploration_steps": [item.model_dump() for item in state.get("exploration_steps", [])],
                "evidence_pool": _compact_evidence(state.get("evidence_pool", []), limit=80),
                "output_schema": {
                    "summary": "Chinese paragraph",
                    "strengths": ["Chinese list"],
                    "risks": ["Chinese list"],
                    "recommendations": ["Chinese list"],
                },
            },
        )
        final_report = {
            "summary": str(raw.get("summary") or "AI Agent review completed."),
            "strengths": [str(item) for item in raw.get("strengths", []) if item],
            "risks": [str(item) for item in raw.get("risks", []) if item],
            "recommendations": [str(item) for item in raw.get("recommendations", []) if item],
            "calibration_rationale": state.get("calibration_rationale", ""),
        }
    except ValueError as exc:
        fallback_used = True
        final_report = _fallback_final_report(state, str(exc))
    state["final_report"] = final_report
    state["result"] = AgentDeepScore(
        score=state["aggregate_score"],
        confidence=state["confidence"],
        project_profile=state["project_profile"],
        rubric=state["rubric"],
        exploration_steps=state.get("exploration_steps", []),
        evidence_pool=state.get("evidence_pool", []),
        curated_evidence=state.get("curated_evidence", {}),
        dimensions=state["dimensions"],
        critic_review=state["critic_review"],
        calibrated_dimensions=state["calibrated_dimensions"],
        final_report=final_report,
        trace=[*state.get("trace", []), "final_report"],
    )
    detail = f"Final AI Agent report generated: {state['aggregate_score']}/100."
    if fallback_used:
        detail = f"final_report fallback used. {detail}"
    _emit(state, "final_report", "final_report", "completed", detail)
    return state


def _build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("repo_indexer", _repo_indexer)
    graph.add_node("project_classifier", _project_classifier)
    graph.add_node("rubric_selector", _rubric_selector)
    graph.add_node("evidence_explorer_loop", _evidence_explorer_loop)
    graph.add_node("evidence_curator", _evidence_curator)
    graph.add_node("dimension_judges", _dimension_judges)
    graph.add_node("critic_review", _critic_review)
    graph.add_node("score_calibrator", _score_calibrator)
    graph.add_node("aggregate_score", _aggregate_score)
    graph.add_node("final_report", _final_report)
    graph.set_entry_point("repo_indexer")
    graph.add_edge("repo_indexer", "project_classifier")
    graph.add_edge("project_classifier", "rubric_selector")
    graph.add_edge("rubric_selector", "evidence_explorer_loop")
    graph.add_edge("evidence_explorer_loop", "evidence_curator")
    graph.add_edge("evidence_curator", "dimension_judges")
    graph.add_edge("dimension_judges", "critic_review")
    graph.add_edge("critic_review", "score_calibrator")
    graph.add_edge("score_calibrator", "aggregate_score")
    graph.add_edge("aggregate_score", "final_report")
    graph.add_edge("final_report", END)
    return graph.compile()


async def run_agent_deep_scoring(
    *,
    config: AiConfig,
    repo_root: str | Path,
    index: RepoIndex,
    github_evidence: dict[str, Any],
    core_report: Any | None = None,
    progress: ProgressEventCallback | None = None,
    llm: AgentLlm | None = None,
    max_exploration_steps: int = 18,
) -> AgentDeepScore:
    graph = _build_agent_graph()
    state = await graph.ainvoke(
        {
            "config": config,
            "repo_root": Path(repo_root),
            "index": index,
            "github_evidence": github_evidence,
            "core_report": core_report,
            "rule_scout_map": _build_rule_scout_map(core_report),
            "max_exploration_steps": max_exploration_steps,
            "llm": llm or HttpxAgentLlm(config),
            "progress_callback": progress,
            "trace": [],
            "exploration_steps": [],
            "evidence_pool": [],
        }
    )
    return state["result"]
