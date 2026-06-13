from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.models import (
    CommunityReference,
    CoreScoreResult,
    DeepAnalysisReport,
    EvidenceItem,
    ScoreDimension,
    SuitabilityScores,
)
from app.services.repo_indexer import RepoIndex


DIMENSION_MAX = {
    "architecture": 20,
    "engineering": 20,
    "testing": 15,
    "documentation": 15,
    "security": 15,
    "maintainability": 15,
}


class AnalysisState(TypedDict, total=False):
    index: RepoIndex
    github_evidence: dict[str, Any]
    structure_summary: dict[str, Any]
    dimensions: dict[str, ScoreDimension]
    suitability: SuitabilityScores
    community_reference: CommunityReference
    report: DeepAnalysisReport
    trace: list[str]


def _clamp(value: float, maximum: int) -> int:
    return max(0, min(maximum, round(value)))


def _first_evidence(index: RepoIndex, label: str, paths: list[str], fallback: str = "") -> EvidenceItem:
    path = paths[0] if paths else None
    excerpt = fallback
    if path:
        for snippet in index.snippets:
            if snippet.path == path:
                excerpt = snippet.excerpt[:500]
                break
    return EvidenceItem(label=label, path=path, excerpt=excerpt)


def _dimension(score: int, max_score: int, reason: str, evidence: list[EvidenceItem]) -> ScoreDimension:
    return ScoreDimension(score=_clamp(score, max_score), max_score=max_score, reason=reason, evidence=evidence)


def _summarize_structure(state: AnalysisState) -> AnalysisState:
    index = state["index"]
    top_dirs = sorted({path.split("/")[0] for path in index.tree if "/" in path})[:20]
    state["structure_summary"] = {
        "top_dirs": top_dirs,
        "source_count": len(index.source_files),
        "test_count": len(index.test_files),
        "doc_count": len(index.documentation_files),
        "manifest_count": len(index.manifest_files),
        "config_count": len(index.config_files),
    }
    state["trace"] = [*state.get("trace", []), "读取目录树、配置文件、文档、测试和源码片段"]
    return state


def _score_architecture(index: RepoIndex) -> ScoreDimension:
    top_source_dirs = {path.split("/")[0] for path in index.source_files if "/" in path}
    score = 0
    score += 6 if top_source_dirs & {"src", "app", "lib", "server", "backend", "frontend"} else 2 if index.source_files else 0
    score += 4 if index.manifest_files else 0
    score += 4 if len(top_source_dirs) >= 2 else 2 if top_source_dirs else 0
    score += 3 if index.config_files else 0
    score += 3 if index.documentation_files else 0
    reason = "源码、配置和文档形成了可识别的项目结构。" if score >= 12 else "项目结构信号较少，架构边界不够清晰。"
    return _dimension(
        score,
        DIMENSION_MAX["architecture"],
        reason,
        [
            EvidenceItem(label="主要目录", excerpt=", ".join(sorted(top_source_dirs)) or "未识别到常见源码目录"),
            _first_evidence(index, "工程配置", index.manifest_files),
        ],
    )


def _score_engineering(index: RepoIndex) -> ScoreDimension:
    score = 0
    score += 5 if index.manifest_files else 0
    score += 5 if index.has_ci else 0
    score += 3 if any(path.lower().endswith(("lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock")) for path in index.tree) else 0
    score += 3 if any("docker" in path.lower() for path in index.config_files) else 0
    score += 2 if any(path.lower().endswith(("tsconfig.json", "pytest.ini", "ruff.toml", ".eslintrc", ".prettierrc")) for path in index.config_files) else 0
    score += 2 if index.source_files else 0
    reason = "依赖、源码和自动化配置较完整。" if score >= 13 else "工程化文件不够完整，自动化和依赖治理信号不足。"
    return _dimension(
        score,
        DIMENSION_MAX["engineering"],
        reason,
        [
            _first_evidence(index, "依赖或构建文件", index.manifest_files),
            _first_evidence(index, "CI 文件", index.ci_files, "未发现 CI 配置"),
        ],
    )


def _score_testing(index: RepoIndex) -> ScoreDimension:
    score = 0
    score += 8 if index.has_tests else 0
    score += 3 if any("pytest" in snippet.excerpt.lower() or "vitest" in snippet.excerpt.lower() or "jest" in snippet.excerpt.lower() for snippet in index.snippets) else 0
    score += 3 if index.has_ci and index.has_tests else 0
    ratio = len(index.test_files) / max(len(index.source_files), 1)
    score += 1 if ratio >= 0.15 else 0
    reason = "能看到测试入口和自动化测试信号。" if score >= 9 else "测试文件或测试运行链路不足。"
    return _dimension(
        score,
        DIMENSION_MAX["testing"],
        reason,
        [_first_evidence(index, "测试文件", index.test_files, "未发现测试文件")],
    )


def _score_documentation(index: RepoIndex) -> ScoreDimension:
    readme = [path for path in index.documentation_files if path.lower().split("/")[-1].startswith("readme")]
    score = 0
    score += 6 if readme else 0
    score += 3 if any(path.lower().startswith("docs/") for path in index.documentation_files) else 0
    score += 3 if index.has_license else 0
    score += 2 if any(path.lower().endswith("contributing.md") for path in index.documentation_files) else 0
    score += 1 if index.has_security_policy else 0
    reason = "基础文档较完整，能支撑阅读和上手。" if score >= 10 else "文档覆盖偏薄，上手和治理信息不足。"
    return _dimension(
        score,
        DIMENSION_MAX["documentation"],
        reason,
        [
            _first_evidence(index, "README", readme, "未发现 README"),
            EvidenceItem(label="文档文件数量", excerpt=str(len(index.documentation_files))),
        ],
    )


def _score_security(index: RepoIndex) -> ScoreDimension:
    score = 15
    risk_reasons: list[str] = []
    if index.security_findings:
        score -= 6
        risk_reasons.append("发现疑似敏感信息模式")
    if not index.has_license:
        score -= 2
        risk_reasons.append("缺少许可证")
    if not index.has_security_policy:
        score -= 2
        risk_reasons.append("缺少 SECURITY.md")
    if any(path.lower().endswith((".env", ".pem", ".key")) for path in index.tree):
        score -= 3
        risk_reasons.append("存在敏感扩展名文件")
    reason = "未发现明显安全风险信号。" if not risk_reasons else "；".join(risk_reasons)
    evidence = index.security_findings[:3] or [EvidenceItem(label="安全扫描", excerpt="只读扫描未发现明显密钥模式")]
    return _dimension(score, DIMENSION_MAX["security"], reason, evidence)


def _score_maintainability(index: RepoIndex) -> ScoreDimension:
    source_count = len(index.source_files)
    avg_size = index.total_bytes / max(index.file_count, 1)
    score = 0
    score += 4 if 1 <= source_count <= 400 else 2 if source_count else 0
    score += 4 if avg_size <= 60_000 else 2 if avg_size <= 150_000 else 0
    score += 3 if len(index.large_files) <= 2 else 1
    score += 2 if index.has_tests else 0
    score += 2 if index.has_docs else 0
    reason = "文件规模和辅助材料较利于维护。" if score >= 10 else "维护性信号不足，文件规模、测试或文档需要补强。"
    return _dimension(
        score,
        DIMENSION_MAX["maintainability"],
        reason,
        [
            EvidenceItem(label="源码文件数", excerpt=str(source_count)),
            EvidenceItem(label="大文件数量", excerpt=str(len(index.large_files))),
        ],
    )


def _score_core(state: AnalysisState) -> AnalysisState:
    index = state["index"]
    dimensions = {
        "architecture": _score_architecture(index),
        "engineering": _score_engineering(index),
        "testing": _score_testing(index),
        "documentation": _score_documentation(index),
        "security": _score_security(index),
        "maintainability": _score_maintainability(index),
    }
    state["dimensions"] = dimensions
    state["trace"] = [*state.get("trace", []), "按六个核心维度计算 100 分，不把 Star/Fork 计入核心分"]
    return state


def _community_reference(github_evidence: dict[str, Any]) -> CommunityReference:
    repo = github_evidence.get("repo") or {}
    license_value = repo.get("license") or {}
    license_name = license_value.get("name") if isinstance(license_value, dict) else None
    return CommunityReference(
        stars=int(repo.get("stars") or 0),
        forks=int(repo.get("forks") or 0),
        open_issues=int(repo.get("open_issues") or 0),
        watchers=int(repo.get("watchers") or 0),
        pushed_at=repo.get("pushed_at"),
        archived=bool(repo.get("archived") or False),
        disabled=bool(repo.get("disabled") or False),
        default_branch=repo.get("default_branch"),
        license_name=license_name,
        topics=repo.get("topics") or [],
        recent_commits=len(github_evidence.get("commits") or []),
        releases=len(github_evidence.get("releases") or []),
    )


def _score_suitability(state: AnalysisState) -> AnalysisState:
    dims = state["dimensions"]
    architecture = dims["architecture"].score / dims["architecture"].max_score
    engineering = dims["engineering"].score / dims["engineering"].max_score
    testing = dims["testing"].score / dims["testing"].max_score
    docs = dims["documentation"].score / dims["documentation"].max_score
    security = dims["security"].score / dims["security"].max_score
    maintainability = dims["maintainability"].score / dims["maintainability"].max_score

    state["suitability"] = SuitabilityScores(
        learning=_clamp((docs * 0.45 + architecture * 0.25 + maintainability * 0.20 + engineering * 0.10) * 100, 100),
        secondary_development=_clamp(
            (architecture * 0.30 + maintainability * 0.30 + engineering * 0.20 + testing * 0.20) * 100,
            100,
        ),
        production=_clamp((engineering * 0.30 + testing * 0.25 + security * 0.25 + docs * 0.20) * 100, 100),
        notes={
            "learning": "主要看 README/文档、结构清晰度和项目规模。",
            "secondary_development": "主要看架构、可维护性、工程配置和测试基础。",
            "production": "主要看工程完整度、测试质量、安全风险和文档治理。",
        },
    )
    state["community_reference"] = _community_reference(state.get("github_evidence") or {})
    state["trace"] = [*state.get("trace", []), "生成学习、二开、生产使用三个参考适用性分"]
    return state


def _finalize_report(state: AnalysisState) -> AnalysisState:
    dimensions = state["dimensions"]
    total = sum(dimension.score for dimension in dimensions.values())
    risk_flags = []
    if dimensions["security"].score < 10:
        risk_flags.append("安全治理或敏感信息风险需要关注")
    if dimensions["testing"].score < 7:
        risk_flags.append("测试质量偏弱")
    if dimensions["documentation"].score < 7:
        risk_flags.append("文档不足")

    core_score = CoreScoreResult(
        score=_clamp(total, 100),
        dimensions=dimensions,
        summary="核心分基于本地只读代码证据计算，不包含 Star、Fork 等社区热度。",
        risk_flags=risk_flags,
    )
    strengths = [
        dimension.reason
        for dimension in dimensions.values()
        if dimension.score / dimension.max_score >= 0.7
    ][:4]
    risks = risk_flags or [
        dimension.reason
        for dimension in dimensions.values()
        if dimension.score / dimension.max_score < 0.45
    ][:4]
    recommendations = []
    if dimensions["testing"].score < 10:
        recommendations.append("补充自动化测试，并在 CI 中运行。")
    if dimensions["documentation"].score < 10:
        recommendations.append("完善 README、使用说明和贡献/安全治理文档。")
    if dimensions["engineering"].score < 12:
        recommendations.append("补齐依赖管理、构建配置、CI 或容器化文件。")
    if dimensions["security"].score < 12:
        recommendations.append("补充 SECURITY.md，排查疑似密钥和敏感配置。")

    index = state["index"]
    state["report"] = DeepAnalysisReport(
        core_score=core_score,
        suitability=state["suitability"],
        community_reference=state["community_reference"],
        local_index={
            "tree": index.tree[:300],
            "file_count": index.file_count,
            "directory_count": index.directory_count,
            "total_bytes": index.total_bytes,
            "extension_counts": index.extension_counts,
            "source_files": index.source_files[:80],
            "test_files": index.test_files[:80],
            "documentation_files": index.documentation_files[:80],
            "manifest_files": index.manifest_files[:80],
            "ci_files": index.ci_files[:80],
            "config_files": index.config_files[:80],
            "security_files": index.security_files[:80],
            "snippets": [item.model_dump() for item in index.snippets[:24]],
            "security_findings": [item.model_dump() for item in index.security_findings],
        },
        analysis_trace=state.get("trace", []),
        summary=core_score.summary,
        strengths=strengths,
        risks=risks,
        recommendations=recommendations,
    )
    return state


def _build_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("summarize_structure", _summarize_structure)
    graph.add_node("score_core", _score_core)
    graph.add_node("score_suitability", _score_suitability)
    graph.add_node("finalize_report", _finalize_report)
    graph.set_entry_point("summarize_structure")
    graph.add_edge("summarize_structure", "score_core")
    graph.add_edge("score_core", "score_suitability")
    graph.add_edge("score_suitability", "finalize_report")
    graph.add_edge("finalize_report", END)
    return graph.compile()


def analyze_repository_index(index: RepoIndex, github_evidence: dict[str, Any]) -> DeepAnalysisReport:
    graph = _build_graph()
    state = graph.invoke({"index": index, "github_evidence": github_evidence, "trace": []})
    return state["report"]
