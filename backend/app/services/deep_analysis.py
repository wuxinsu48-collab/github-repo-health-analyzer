from __future__ import annotations

import json
import fnmatch
import re
from pathlib import Path
from collections.abc import Callable
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
from app.services.repo_tools import find_files_safe, grep_safe, list_dir_safe, read_file_safe


ProgressEventCallback = Callable[[dict[str, Any]], None]


DIMENSION_MAX = {
    "architecture": 20,
    "engineering": 20,
    "testing": 15,
    "documentation": 15,
    "security": 15,
    "maintainability": 15,
}

TODO_PATTERN = r"TODO|FIXME|HACK"
TEST_PATTERN = r"@Test\b|describe\(|it\(|test\(|expect\(|pytest|unittest|assert\b"
SECURITY_PATTERN = r"password|secret|api_key|apikey|token|private_key|eval\(|exec\(|os\.system|shell=True"
DOC_KEYWORDS = ("install", "run", "usage", "example", "env", "deploy", "quickstart", "configuration")
TEST_GLOBS = ["tests/**", "test/**", "src/test/**", "**/*test*.*", "**/*spec*.*"]
CODE_REVIEW_GLOBS = [
    "src/main/java/**",
    "src/*.py",
    "src/*.ts",
    "src/*.tsx",
    "src/*.js",
    "src/*.jsx",
    "src/*.java",
    "src/**/*.py",
    "src/**/*.ts",
    "src/**/*.tsx",
    "src/**/*.js",
    "src/**/*.jsx",
    "app/**",
    "lib/**",
    "server/**",
    "backend/**",
    "frontend/src/**",
    "*.py",
    "*.js",
    "*.ts",
    "*.java",
]
SECURITY_GLOBS = [*CODE_REVIEW_GLOBS, "*.sql", "*.properties", "*.yml", "*.yaml", "*.xml", "*.env", "*.env.example"]
ENTRY_CANDIDATES = (
    "src/main.tsx",
    "src/main.ts",
    "src/App.tsx",
    "src/App.vue",
    "main.py",
    "app.py",
    "server.js",
    "index.js",
)


class AnalysisState(TypedDict, total=False):
    index: RepoIndex
    github_evidence: dict[str, Any]
    structure_summary: dict[str, Any]
    exploration_plan: list[dict[str, Any]]
    exploration_notes: list[dict[str, Any]]
    evidence_pool: list[dict[str, Any]]
    dimensions: dict[str, ScoreDimension]
    suitability: SuitabilityScores
    community_reference: CommunityReference
    report: DeepAnalysisReport
    trace: list[str]
    progress_callback: ProgressEventCallback | None


def _clamp(value: float, maximum: int) -> int:
    return max(0, min(maximum, round(value)))


def _emit(
    state: AnalysisState,
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


def _tool_event_id(item: dict[str, Any]) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", str(item.get("target") or ""))[:36].strip("_")
    return f"tool_{item['step']}_{item['action']}_{raw or 'target'}"


def _basename(path: str) -> str:
    return path.lower().split("/")[-1]


def _path_set(index: RepoIndex) -> set[str]:
    return {path.lower(): path for path in index.tree}.keys()


def _find_path(index: RepoIndex, target: str) -> str | None:
    target_lower = target.lower()
    for path in index.tree:
        if path.lower() == target_lower:
            return path
    return None


def _find_first_by_name(index: RepoIndex, names: set[str]) -> str | None:
    for path in index.tree:
        if _basename(path) in names:
            return path
    return None


def _find_first_matching(index: RepoIndex, *needles: str) -> str | None:
    lowered = [needle.lower() for needle in needles]
    for path in index.tree:
        path_lower = path.lower()
        if all(needle in path_lower for needle in lowered):
            return path
    return None


def _plan_item(step: int, action: str, target: str, reason: str, dimension: str, **extra: Any) -> dict[str, Any]:
    item = {
        "step": step,
        "action": action,
        "target": target,
        "reason": reason,
        "dimension": dimension,
    }
    item.update(extra)
    return item


def _append_plan(plan: list[dict[str, Any]], action: str, target: str, reason: str, dimension: str, **extra: Any) -> None:
    plan.append(_plan_item(len(plan) + 1, action, target, reason, dimension, **extra))


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
    _emit(state, "summarize_structure", "summarize_structure", "running", "读取 RepoIndex 汇总目录、源码、测试、文档和配置")
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
    _emit(state, "summarize_structure", "summarize_structure", "completed", f"索引中包含 {index.file_count} 个文件")
    return state


def _plan_exploration(state: AnalysisState) -> AnalysisState:
    _emit(state, "plan_exploration", "plan_exploration", "running", "根据索引结果生成本地按需探索计划")
    index = state["index"]
    plan: list[dict[str, Any]] = []

    _append_plan(plan, "list_dir", ".", "先观察仓库根目录，判断源码、配置和文档的组织方式", "architecture")

    package_json = _find_path(index, "package.json")
    if package_json:
        _append_plan(plan, "read_file", package_json, "读取 package.json，确认脚本、依赖和测试入口", "engineering")

    pom_xml = _find_path(index, "pom.xml")
    if pom_xml:
        _append_plan(plan, "read_file", pom_xml, "读取 pom.xml，确认 Maven 依赖、构建插件和测试依赖", "engineering")

    build_gradle = _find_first_by_name(index, {"build.gradle", "build.gradle.kts"})
    if build_gradle:
        _append_plan(plan, "read_file", build_gradle, "读取 Gradle 构建文件，确认依赖、任务和测试配置", "engineering")

    pyproject = _find_path(index, "pyproject.toml")
    if pyproject:
        _append_plan(plan, "read_file", pyproject, "读取 pyproject.toml，确认 Python 工程配置和测试工具", "engineering")

    requirements = _find_path(index, "requirements.txt")
    if requirements:
        _append_plan(plan, "read_file", requirements, "读取 requirements.txt，确认依赖和测试框架信号", "engineering")

    readme = _find_first_by_name(index, {"readme", "readme.md", "readme.rst"})
    if readme:
        _append_plan(plan, "read_file", readme, "读取 README，判断文档是否覆盖安装、运行、用法和部署", "documentation")

    for workflow in index.ci_files[:2]:
        _append_plan(plan, "read_file", workflow, "读取 CI workflow，确认是否有构建和测试自动化", "engineering")

    _append_plan(
        plan,
        "grep",
        TODO_PATTERN,
        "搜索项目源码中的 TODO/FIXME/HACK，作为可维护性风险证据",
        "maintainability",
        include_globs=CODE_REVIEW_GLOBS,
        exclude_globs=["**/static/**", "**/vendor/**", "**/*.min.js"],
    )
    _append_plan(
        plan,
        "grep",
        TEST_PATTERN,
        "搜索测试目录和测试命名文件中的断言/测试框架调用，补充测试质量证据",
        "testing",
        include_globs=TEST_GLOBS,
    )
    _append_plan(
        plan,
        "grep",
        SECURITY_PATTERN,
        "搜索源码/配置/SQL 中的敏感字段和危险执行调用，补充安全风险证据",
        "security",
        include_globs=SECURITY_GLOBS,
        exclude_globs=["**/static/**", "**/vendor/**", "**/*.min.js"],
    )
    _append_plan(plan, "find_files", "Dockerfile,docker-compose*,*.env.example,*.env.sample", "查找容器化和环境变量示例，补充工程完整度证据", "engineering")

    seen_entries: set[str] = set()
    for candidate in ENTRY_CANDIDATES:
        path = _find_path(index, candidate)
        if path and path not in seen_entries:
            seen_entries.add(path)
            _append_plan(plan, "read_file", path, "读取可能的入口文件，判断架构边界和入口复杂度", "architecture")

    layer_candidates = [
        (_find_first_matching(index, "/controller/", ".java"), "读取 controller 代表文件，判断请求入口和层次边界"),
        (_find_first_matching(index, "/service/", ".java"), "读取 service 代表文件，判断业务层抽象和复用方式"),
        (_find_first_matching(index, "/dao/", ".java"), "读取 dao 代表文件，判断数据访问层边界和 SQL/ORM 风险"),
        (_find_first_matching(index, "/bean/", ".java"), "读取 bean/model 代表文件，判断领域模型复杂度"),
        (_find_first_matching(index, ".sql"), "读取 SQL 初始化脚本，判断数据结构、安全和部署耦合"),
    ]
    for path, reason in layer_candidates:
        if path and path not in seen_entries:
            seen_entries.add(path)
            dimension = "security" if path.lower().endswith(".sql") else "architecture"
            _append_plan(plan, "read_file", path, reason, dimension)
    if not seen_entries:
        for path in index.source_files[:2]:
            _append_plan(plan, "read_file", path, "读取代表性源码文件，判断模块边界和代码复杂度", "architecture")

    state["exploration_plan"] = plan
    state["trace"] = [*state.get("trace", []), f"生成 {len(plan)} 个本地只读探索计划项"]
    _emit(state, "plan_exploration", "plan_exploration", "completed", f"生成 {len(plan)} 个探索计划项")
    return state


def _safe_snippet(text: str, limit: int = 900) -> str:
    return re.sub(r"\s+\n", "\n", text.strip())[:limit]


def _read_file_summary(target: str, result: dict[str, Any]) -> str:
    content = result.get("content", "")
    lower = content.lower()
    name = _basename(target)
    if name == "package.json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return f"读取 {result['line_end'] - result['line_start'] + 1} 行，package.json 不是标准 JSON 或片段不完整"
        scripts = parsed.get("scripts") if isinstance(parsed, dict) else {}
        script_names = sorted(scripts.keys()) if isinstance(scripts, dict) else []
        if script_names:
            has_test = "test" in script_names
            test_text = "发现 test 脚本" if has_test else "未发现 test 脚本"
            return f"发现 scripts: {', '.join(script_names[:8])}；{test_text}"
        return "未发现 scripts 字段"
    if name == "pom.xml":
        found = []
        for token, label in (
            ("spring", "Spring"),
            ("junit", "JUnit"),
            ("maven-surefire-plugin", "Surefire"),
            ("mysql", "MySQL"),
            ("mybatis", "MyBatis"),
            ("hibernate", "Hibernate"),
        ):
            if token in lower:
                found.append(label)
        return f"pom.xml 识别到：{', '.join(found) if found else '未识别到关键依赖/测试配置'}"
    if name in {"build.gradle", "build.gradle.kts"}:
        found = [token for token in ("spring", "junit", "testimplementation", "mysql", "mybatis") if token in lower]
        return f"Gradle 文件识别到：{', '.join(found) if found else '未识别到关键依赖/测试配置'}"
    if name.startswith("readme"):
        found = [keyword for keyword in DOC_KEYWORDS if keyword in lower]
        return f"README 覆盖关键词：{', '.join(found) if found else '未发现安装/运行/用法等关键词'}"
    if target.lower().startswith(".github/workflows/"):
        has_test = any(token in lower for token in ("test", "pytest", "vitest", "jest", "npm test"))
        return "CI workflow 包含测试执行信号" if has_test else "CI workflow 未明显包含测试执行信号"
    if name in {"requirements.txt", "pyproject.toml"}:
        has_test_tool = any(token in lower for token in ("pytest", "unittest", "tox", "coverage", "ruff", "mypy"))
        return "发现 Python 依赖/工具配置，并包含测试或质量工具信号" if has_test_tool else "发现 Python 依赖/工具配置，测试工具信号较少"
    if name.endswith(".sql"):
        tables = len(re.findall(r"(?i)\bcreate\s+table\b", content))
        password_columns = len(re.findall(r"(?i)\b(password|passwd|pwd)\b", content))
        return f"SQL 脚本包含 {tables} 个建表语句，密码字段线索 {password_columns} 处"
    total_lines = int(result.get("total_lines") or 0)
    return f"读取入口/源码片段，总行数约 {total_lines} 行"


def _grep_summary(matches: list[dict[str, Any]], dimension: str) -> str:
    if not matches:
        return "未发现匹配结果"
    files = sorted({match["file"] for match in matches})
    if dimension == "security":
        return f"发现 {len(matches)} 条安全相关匹配，涉及 {len(files)} 个文件"
    if dimension == "testing":
        return f"发现 {len(matches)} 条测试模式匹配，涉及 {len(files)} 个文件"
    if dimension == "maintainability":
        return f"发现 {len(matches)} 条 TODO/FIXME/HACK，涉及 {len(files)} 个文件"
    return f"发现 {len(matches)} 条匹配，涉及 {len(files)} 个文件"


def _evidence_from_read(item: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    content = _safe_snippet(result.get("content", ""))
    if not content:
        return []
    return [
        {
            "step": item["step"],
            "action": item["action"],
            "target": item["target"],
            "dimension": item["dimension"],
            "file": result["file"],
            "line_start": result["line_start"],
            "line_end": result["line_end"],
            "total_lines": result.get("total_lines"),
            "snippet": content,
            "reason": f"该片段支持{item['dimension']}维度判断",
        }
    ]


def _evidence_from_grep(item: dict[str, Any], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for match in matches[:8]:
        evidence.append(
            {
                "step": item["step"],
                "action": item["action"],
                "target": item["target"],
                "dimension": item["dimension"],
                "file": match["file"],
                "line_start": match["line"],
                "line_end": match["line"],
                "snippet": match["snippet"],
                "match": match.get("match", ""),
                "reason": f"grep 命中用于支持{item['dimension']}维度判断",
            }
        )
    return evidence


def _execute_exploration_item(repo_root: str, item: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    action = item["action"]
    target = item["target"]
    if action == "read_file":
        result = read_file_safe(repo_root, target, max_lines=200)
        return _read_file_summary(target, result), _evidence_from_read(item, result)
    if action == "grep":
        matches = grep_safe(repo_root, target, include_globs=item.get("include_globs"), max_matches=50, timeout=5)
        exclude_globs = item.get("exclude_globs") or []
        if exclude_globs:
            matches = [
                match
                for match in matches
                if not any(fnmatch.fnmatch(match["file"], pattern) for pattern in exclude_globs)
            ]
        return _grep_summary(matches, item["dimension"]), _evidence_from_grep(item, matches)
    if action == "list_dir":
        entries = list_dir_safe(repo_root, target)
        preview = ", ".join(entry["path"] for entry in entries[:12]) or "空目录或无可读条目"
        evidence = [
            {
                "step": item["step"],
                "action": item["action"],
                "target": target,
                "dimension": item["dimension"],
                "file": target,
                "line_start": None,
                "line_end": None,
                "snippet": preview,
                "reason": "目录结构用于判断架构清晰度",
            }
        ]
        return f"列出 {len(entries)} 个条目：{preview}", evidence
    if action == "find_files":
        patterns = [part.strip() for part in target.split(",") if part.strip()]
        found: list[str] = []
        for pattern in patterns:
            found.extend(find_files_safe(repo_root, pattern, max_results=20))
        found = sorted(dict.fromkeys(found))[:50]
        preview = ", ".join(found[:12]) or "未发现匹配文件"
        evidence = [
            {
                "step": item["step"],
                "action": item["action"],
                "target": target,
                "dimension": item["dimension"],
                "file": target,
                "line_start": None,
                "line_end": None,
                "snippet": preview,
                "reason": "文件存在性用于判断工程完整度",
            }
        ]
        return f"找到 {len(found)} 个匹配文件：{preview}", evidence
    return "未知动作，已跳过", []


def _explore_codebase(state: AnalysisState) -> AnalysisState:
    plan = state.get("exploration_plan", [])[:12]
    _emit(state, "explore_codebase", "explore_codebase", "running", f"开始执行 {len(plan)} 步只读探索")
    index = state["index"]
    notes: list[dict[str, Any]] = []
    evidence_pool: list[dict[str, Any]] = []

    for item in plan:
        tool_id = _tool_event_id(item)
        _emit(
            state,
            tool_id,
            item["action"],
            "running",
            item["reason"],
            kind="tool",
            target=item["target"],
            dimension=item["dimension"],
        )
        try:
            result_summary, evidence = _execute_exploration_item(index.root, item)
        except Exception as exc:
            result_summary = f"探索失败：{exc}"
            evidence = []
            _emit(
                state,
                tool_id,
                item["action"],
                "failed",
                result_summary,
                kind="tool",
                target=item["target"],
                dimension=item["dimension"],
            )
        else:
            _emit(
                state,
                tool_id,
                item["action"],
                "completed",
                result_summary,
                kind="tool",
                target=item["target"],
                dimension=item["dimension"],
            )
        note = {
            "step": item["step"],
            "thought": item["reason"],
            "action": item["action"],
            "target": item["target"],
            "result_summary": result_summary,
            "dimension": item["dimension"],
            "evidence": evidence,
        }
        notes.append(note)
        evidence_pool.extend(evidence)

    state["exploration_notes"] = notes
    state["evidence_pool"] = evidence_pool
    state["trace"] = [*state.get("trace", []), f"执行 {len(notes)} 步本地按需探索，收集 {len(evidence_pool)} 条证据"]
    _emit(state, "explore_codebase", "explore_codebase", "completed", f"完成 {len(notes)} 步探索，收集 {len(evidence_pool)} 条证据")
    return state


def _pool_for(state: AnalysisState, dimension: str) -> list[dict[str, Any]]:
    return [item for item in state.get("evidence_pool", []) if item.get("dimension") == dimension]


def _combined_text(items: list[dict[str, Any]]) -> str:
    chunks = []
    for item in items:
        chunks.append(str(item.get("snippet") or ""))
        chunks.append(str(item.get("match") or ""))
    return "\n".join(chunks).lower()


def _pool_files(state: AnalysisState, dimension: str) -> list[str]:
    return [str(item.get("file") or "") for item in _pool_for(state, dimension)]


def _note_text(state: AnalysisState, dimension: str | None = None) -> str:
    notes = state.get("exploration_notes", [])
    if dimension:
        notes = [note for note in notes if note.get("dimension") == dimension]
    return "\n".join(str(note.get("result_summary") or "") for note in notes).lower()


def _evidence_items_from_pool(
    state: AnalysisState,
    dimension: str,
    fallback: list[EvidenceItem] | None = None,
    limit: int = 4,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for evidence in _pool_for(state, dimension)[:limit]:
        items.append(
            EvidenceItem(
                label=f"{evidence.get('action')} evidence",
                path=evidence.get("file") or None,
                excerpt=str(evidence.get("snippet") or "")[:500],
                line_start=evidence.get("line_start"),
                line_end=evidence.get("line_end"),
                reason=evidence.get("reason"),
            )
        )
    if fallback:
        items.extend(fallback)
    return items[:limit]


def _has_package_test_script(state: AnalysisState) -> bool:
    text = "\n".join(
        item.get("snippet", "")
        for item in state.get("evidence_pool", [])
        if item.get("file") == "package.json"
    ).lower()
    return '"test"' in text or "'test'" in text or "test:" in text or "test 脚本" in _note_text(state, "engineering")


def _has_ci_test_signal(state: AnalysisState) -> bool:
    text = _combined_text([item for item in state.get("evidence_pool", []) if ".github/workflows/" in str(item.get("file"))])
    return any(token in text for token in ("npm test", "pytest", "vitest", "jest", "test"))


def _score_architecture(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    architecture_files = _pool_files(state, "architecture")
    architecture_text = _combined_text(_pool_for(state, "architecture"))
    top_source_dirs = {path.split("/")[0] for path in architecture_files if "/" in path}
    has_root_structure = any(path == "." for path in architecture_files)
    has_entry = any(
        _basename(path) in {"main.tsx", "main.ts", "app.tsx", "app.vue", "main.py", "app.py", "server.js", "index.js"}
        for path in architecture_files
    )
    has_controller = any("/controller/" in path.lower() for path in architecture_files)
    has_service = any("/service/" in path.lower() for path in architecture_files)
    has_dao = any("/dao/" in path.lower() or "/repository/" in path.lower() for path in architecture_files)
    has_model = any("/bean/" in path.lower() or "/model/" in path.lower() or "/entity/" in path.lower() for path in architecture_files)
    has_layered_flow = sum([has_controller, has_service, has_dao, has_model])
    giant_read_file = any(int(item.get("total_lines") or 0) > 800 for item in _pool_for(state, "architecture"))

    score = 0
    score += 3 if has_root_structure else 0
    score += 4 if has_entry else 0
    score += 3 if has_controller else 0
    score += 3 if has_service else 0
    score += 3 if has_dao else 0
    score += 2 if has_model else 0
    score += 2 if "src/main/java" in architecture_text or top_source_dirs & {"src", "app", "lib", "server", "backend", "frontend"} else 0
    score += 2 if has_layered_flow >= 3 else 0
    score -= 2 if giant_read_file else 0

    reason = (
        "Agent 读取到入口/分层源码证据，能看到较清晰的架构边界。"
        if score >= 13
        else "Agent 探索到的入口、分层或模块边界证据仍不充分。"
    )
    evidence = _evidence_items_from_pool(
        state,
        "architecture",
        [
            EvidenceItem(label="已读源码目录", excerpt=", ".join(sorted(top_source_dirs)) or "未读到常见源码目录"),
        ],
    )
    return _dimension(score, DIMENSION_MAX["architecture"], reason, evidence)


def _score_engineering(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    engineering_text = _combined_text(_pool_for(state, "engineering"))
    engineering_notes = _note_text(state, "engineering")
    engineering_files = _pool_files(state, "engineering")
    has_manifest_evidence = any(_basename(path) in {"package.json", "pom.xml", "build.gradle", "build.gradle.kts", "pyproject.toml", "requirements.txt"} for path in engineering_files)
    has_maven_or_gradle = any(_basename(path) in {"pom.xml", "build.gradle", "build.gradle.kts"} for path in engineering_files)
    has_build_or_dev_script = any(token in engineering_text for token in ('"build"', '"dev"', '"start"', "'build'", "'dev'", "'start'", "maven", "gradle", "spring"))
    has_test_script = _has_package_test_script(state)
    has_test_tooling = has_test_script or any(token in engineering_text for token in ("junit", "surefire", "pytest", "vitest", "jest"))
    has_env_example = ".env.example" in engineering_text or ".env.sample" in engineering_text
    has_container = "dockerfile" in engineering_text or "docker-compose" in engineering_text
    has_ci_evidence = "ci workflow 包含" in engineering_notes or _has_ci_test_signal(state)

    score = 0
    score += 6 if has_manifest_evidence else 0
    score += 3 if has_maven_or_gradle else 0
    score += 3 if has_build_or_dev_script else 0
    score += 3 if has_test_tooling else 0
    score += 3 if has_ci_evidence else 0
    score += 2 if has_container else 0
    score += 2 if has_env_example else 0
    score += 1 if any(path.lower().endswith(("package-lock.json", "pnpm-lock.yaml", "poetry.lock", "pom.xml")) for path in engineering_files) else 0

    reason = (
        "Agent 读取到依赖、构建、测试工具或自动化配置证据。"
        if score >= 14
        else "Agent 探索到的工程化证据不足，构建脚本、CI、容器化或环境示例需要补强。"
    )
    evidence = _evidence_items_from_pool(
        state,
        "engineering",
        [
            _first_evidence(index, "依赖或构建文件", index.manifest_files),
            _first_evidence(index, "CI 文件", index.ci_files, "未发现 CI 配置"),
        ],
    )
    return _dimension(score, DIMENSION_MAX["engineering"], reason, evidence)


def _score_testing(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    test_matches = [item for item in _pool_for(state, "testing") if item.get("action") == "grep"]
    ratio = len(index.test_files) / max(len(index.source_files), 1)

    score = 0
    score += 7 if index.has_tests else 0
    score += 4 if test_matches else 0
    score += 5 if _has_package_test_script(state) else 0
    score += 3 if index.has_ci and (index.has_tests or _has_ci_test_signal(state)) else 0
    score += 1 if ratio >= 0.15 else 0

    reason = (
        "能看到测试文件、测试模式或 CI 测试链路。"
        if score >= 9
        else "测试文件、测试脚本或自动化测试链路仍不充分。"
    )
    evidence = _evidence_items_from_pool(
        state,
        "testing",
        [_first_evidence(index, "测试文件", index.test_files, "未发现测试文件")],
    )
    return _dimension(score, DIMENSION_MAX["testing"], reason, evidence)


def _score_documentation(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    readme = [path for path in index.documentation_files if _basename(path).startswith("readme")]
    doc_text = _combined_text(_pool_for(state, "documentation"))
    keyword_hits = [keyword for keyword in DOC_KEYWORDS if keyword in doc_text]

    score = 0
    score += 6 if readme else 0
    score += 3 if len(keyword_hits) >= 3 else 2 if keyword_hits else 0
    score += 2 if any(path.lower().startswith("docs/") for path in index.documentation_files) else 0
    score += 2 if index.has_license else 0
    score += 1 if any(path.lower().endswith("contributing.md") for path in index.documentation_files) else 0
    score += 1 if index.has_security_policy else 0

    reason = (
        "README 或 docs 覆盖了上手、运行和使用信息。"
        if score >= 10
        else "文档覆盖偏薄，上手、运行、环境或部署信息不足。"
    )
    evidence = _evidence_items_from_pool(
        state,
        "documentation",
        [
            _first_evidence(index, "README", readme, "未发现 README"),
            EvidenceItem(label="文档文件数量", excerpt=str(len(index.documentation_files))),
        ],
    )
    return _dimension(score, DIMENSION_MAX["documentation"], reason, evidence)


def _score_security(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    security_items = _pool_for(state, "security")
    security_text = _combined_text(security_items)
    dangerous_matches = sum(1 for token in ("eval(", "exec(", "os.system", "shell=true") if token in security_text)
    secret_matches = len([item for item in security_items if any(token in str(item.get("match") or item.get("snippet") or "").lower() for token in ("password", "secret", "api_key", "apikey", "token", "private_key"))])

    score = 15
    risk_reasons: list[str] = []
    if index.security_findings:
        score -= 6
        risk_reasons.append("发现疑似敏感信息模式")
    if secret_matches:
        penalty = min(4, secret_matches)
        score -= penalty
        risk_reasons.append(f"grep 发现 {secret_matches} 条敏感字段线索")
    if dangerous_matches:
        score -= min(4, dangerous_matches * 2)
        risk_reasons.append("发现危险执行调用线索")
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
    fallback = index.security_findings[:3] or [EvidenceItem(label="安全扫描", excerpt="只读扫描未发现明显密钥模式")]
    evidence = _evidence_items_from_pool(state, "security", fallback)
    return _dimension(score, DIMENSION_MAX["security"], reason, evidence)


def _score_maintainability(index: RepoIndex, state: AnalysisState) -> ScoreDimension:
    source_count = len(index.source_files)
    todo_count = len([item for item in _pool_for(state, "maintainability") if item.get("action") == "grep"])
    architecture_files = _pool_files(state, "architecture")
    read_source_count = len([path for path in architecture_files if path and path != "."])
    read_line_counts = [
        int(item.get("total_lines") or 0)
        for item in _pool_for(state, "architecture")
        if item.get("action") == "read_file"
    ]
    has_giant_sample = any(lines > 500 for lines in read_line_counts)
    has_layer_evidence = sum(
        [
            any("/controller/" in path.lower() for path in architecture_files),
            any("/service/" in path.lower() for path in architecture_files),
            any("/dao/" in path.lower() or "/repository/" in path.lower() for path in architecture_files),
            any("/bean/" in path.lower() or "/model/" in path.lower() or "/entity/" in path.lower() for path in architecture_files),
        ]
    ) >= 3
    has_engineering_evidence = bool(_pool_for(state, "engineering"))
    has_doc_evidence = bool(_pool_for(state, "documentation"))
    has_test_evidence = bool(_pool_for(state, "testing") or index.test_files)

    score = 0
    score += 3 if read_source_count >= 3 else 1 if read_source_count else 0
    score += 3 if has_layer_evidence else 1 if read_source_count else 0
    score += 2 if has_engineering_evidence else 0
    score += 2 if has_doc_evidence else 0
    score += 2 if has_test_evidence else 0
    score += 2 if 1 <= source_count <= 400 else 1 if source_count else 0
    score += 1 if len(index.large_files) <= 2 else 0
    score -= 2 if has_giant_sample else 0
    score -= min(4, todo_count // 3 + (1 if todo_count else 0))

    reason = (
        "Agent 读取到分层源码、工程配置和辅助材料，维护性有一定支撑。"
        if score >= 10
        else "Agent 探索到的维护性证据不足，分层、测试、文档或 TODO 管理需要补强。"
    )
    evidence = _evidence_items_from_pool(
        state,
        "maintainability",
        [
            EvidenceItem(label="源码文件数量", excerpt=str(source_count)),
            EvidenceItem(label="大文件数量", excerpt=str(len(index.large_files))),
        ],
    )
    return _dimension(score, DIMENSION_MAX["maintainability"], reason, evidence)


def _score_core(state: AnalysisState) -> AnalysisState:
    _emit(state, "score_core", "score_core", "running", "结合确定性索引和探索证据计算六维核心分")
    index = state["index"]
    dimensions = {
        "architecture": _score_architecture(index, state),
        "engineering": _score_engineering(index, state),
        "testing": _score_testing(index, state),
        "documentation": _score_documentation(index, state),
        "security": _score_security(index, state),
        "maintainability": _score_maintainability(index, state),
    }
    state["dimensions"] = dimensions
    state["trace"] = [*state.get("trace", []), "按六个核心维度计算 100 分，以 LangGraph 本地探索证据为主，索引摘要只做兜底，不纳入 Star/Fork"]
    total = sum(dimension.score for dimension in dimensions.values())
    _emit(state, "score_core", "score_core", "completed", f"六维核心分合计 {total}")
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
    _emit(state, "score_suitability", "score_suitability", "running", "生成学习、二开、生产使用三个参考分")
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
    _emit(state, "score_suitability", "score_suitability", "completed", "参考适用性分生成完成")
    return state


def _finalize_report(state: AnalysisState) -> AnalysisState:
    _emit(state, "finalize_report", "finalize_report", "running", "汇总最终报告、风险和建议")
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
        summary="核心分以 LangGraph 本地只读探索证据为主计算，索引摘要只做兜底，不包含 Star、Fork 等社区热度。",
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
    exploration_notes = state.get("exploration_notes", [])
    evidence_pool = state.get("evidence_pool", [])
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
        exploration_notes=exploration_notes,
        evidence_pool=evidence_pool,
        agent_exploration=exploration_notes,
        analysis_trace=state.get("trace", []),
        summary=core_score.summary,
        strengths=strengths,
        risks=risks,
        recommendations=recommendations,
    )
    _emit(state, "finalize_report", "finalize_report", "completed", f"最终报告生成完成，核心分 {core_score.score}")
    return state


def _build_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("summarize_structure", _summarize_structure)
    graph.add_node("plan_exploration", _plan_exploration)
    graph.add_node("explore_codebase", _explore_codebase)
    graph.add_node("score_core", _score_core)
    graph.add_node("score_suitability", _score_suitability)
    graph.add_node("finalize_report", _finalize_report)
    graph.set_entry_point("summarize_structure")
    graph.add_edge("summarize_structure", "plan_exploration")
    graph.add_edge("plan_exploration", "explore_codebase")
    graph.add_edge("explore_codebase", "score_core")
    graph.add_edge("score_core", "score_suitability")
    graph.add_edge("score_suitability", "finalize_report")
    graph.add_edge("finalize_report", END)
    return graph.compile()


def analyze_repository_index(
    index: RepoIndex,
    github_evidence: dict[str, Any],
    progress: ProgressEventCallback | None = None,
) -> DeepAnalysisReport:
    graph = _build_graph()
    state = graph.invoke(
        {
            "index": index,
            "github_evidence": github_evidence,
            "trace": [],
            "progress_callback": progress,
        }
    )
    return state["report"]
