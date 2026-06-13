from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from app.db import delete_report, get_report, init_db, insert_report, list_reports
from app.models import (
    AnalysisJobResponse,
    AnalyzeRequest,
    AiConfig,
    ConnectionTestResponse,
    GitHubTestRequest,
    RecentReport,
    ReportResponse,
)
from app.services.analysis_jobs import (
    add_step_event,
    complete_job,
    create_analysis_job,
    fail_job,
    get_analysis_job,
    skip_step,
    update_step,
)
from app.services.ai import call_deep_ai_assessment, test_ai_connection
from app.services.deep_analysis import analyze_repository_index
from app.services.github import GitHubApiError, GitHubClient, parse_github_repo_url
from app.services.repo_indexer import index_repository
from app.services.repo_workspace import RepositoryCloneError, cleanup_repository, cleanup_stale_workspaces, clone_repository


ProgressCallback = Callable[[str, str, str, dict | None], None]


@asynccontextmanager
async def lifespan(_: FastAPI):
    cleanup_stale_workspaces()
    init_db()
    yield


app = FastAPI(title="GitHub Repository Health Analyzer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/github/test", response_model=ConnectionTestResponse)
async def github_test(request: GitHubTestRequest) -> ConnectionTestResponse:
    client = GitHubClient(token=request.token)
    return await client.test_connection()


@app.post("/api/ai/test", response_model=ConnectionTestResponse)
async def ai_test(request: AiConfig) -> ConnectionTestResponse:
    return await test_ai_connection(request)


def _progress(
    progress: ProgressCallback | None,
    step_id: str,
    status: str,
    detail: str = "",
    metadata: dict | None = None,
) -> None:
    if progress:
        progress(step_id, status, detail, metadata)


async def run_analysis_pipeline(request: AnalyzeRequest, progress: ProgressCallback | None = None) -> dict:
    _progress(progress, "parse_repo", "running", "解析用户输入的 GitHub 仓库地址")
    try:
        owner, repo = parse_github_repo_url(request.repo_url)
    except ValueError as exc:
        _progress(progress, "parse_repo", "failed", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _progress(progress, "parse_repo", "completed", f"{owner}/{repo}")

    _progress(progress, "github_metadata", "running", "读取 Star、Fork、语言分布和社区文件")
    github_warning = None

    def github_progress(event: dict) -> None:
        detail = event.get("detail") or event.get("label") or "GitHub 元数据事件"
        _progress(progress, "github_metadata", "running", str(detail), {"event": event})

    client = GitHubClient(token=request.github_token, progress=github_progress)
    try:
        evidence = await client.fetch_repo_evidence(owner, repo)
        _progress(progress, "github_metadata", "completed", "GitHub 元数据读取完成")
    except GitHubApiError as exc:
        if request.github_token and exc.status_code in {401, 403}:
            try:
                evidence = await GitHubClient(token=None, progress=github_progress).fetch_repo_evidence(owner, repo)
                github_warning = f"{exc.message}，已改用公开访问继续分析。"
                _progress(progress, "github_metadata", "completed", github_warning)
            except GitHubApiError as fallback_exc:
                _progress(progress, "github_metadata", "failed", fallback_exc.message)
                raise HTTPException(status_code=fallback_exc.status_code, detail=fallback_exc.message) from fallback_exc
        else:
            _progress(progress, "github_metadata", "failed", exc.message)
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    local_repo = None
    deep_report = None
    try:
        _progress(progress, "clone_repo", "running", "clone 到本地临时工作区，只读分析")
        local_repo = await asyncio.to_thread(clone_repository, owner, repo, evidence.get("repo", {}).get("html_url"))
        _progress(progress, "clone_repo", "completed", "clone 完成")

        _progress(progress, "index_repo", "running", "扫描目录、配置、文档、测试和源码片段")
        local_index = await asyncio.to_thread(index_repository, local_repo)
        _progress(progress, "index_repo", "completed", f"扫描 {local_index.file_count} 个文件")

        _progress(progress, "langgraph_score", "running", "LangGraph 生成探索计划，使用只读工具收集证据并计算核心 100 分")

        def langgraph_progress(event: dict) -> None:
            detail = event.get("detail") or event.get("label") or "LangGraph 内部事件"
            _progress(progress, "langgraph_score", "running", str(detail), {"event": event})

        deep_report = await asyncio.to_thread(
            analyze_repository_index,
            index=local_index,
            github_evidence=evidence,
            progress=langgraph_progress,
        )
        _progress(progress, "langgraph_score", "completed", f"核心分 {deep_report.core_score.score}")
    except RepositoryCloneError as exc:
        active_step = "clone_repo" if local_repo is None else "index_repo"
        _progress(progress, active_step, "failed", str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if local_repo is not None:
            cleanup_repository(local_repo)

    if deep_report is None:
        raise HTTPException(status_code=500, detail="深度分析报告生成失败")

    core_score = deep_report.core_score.score
    dimension_percentages = {
        key: round(value.score / value.max_score * 100)
        for key, value in deep_report.core_score.dimensions.items()
    }

    deep_ai_assessment = None
    ai_error = None
    if request.ai:
        _progress(progress, "ai_review", "running", "AI 基于本地证据包做独立评分")
        try:
            ai_evidence = {
                "repo": {
                    "full_name": evidence.get("repo", {}).get("full_name"),
                    "description": evidence.get("repo", {}).get("description"),
                    "default_branch": evidence.get("repo", {}).get("default_branch"),
                },
                "local_index": deep_report.local_index,
                "exploration_notes": deep_report.exploration_notes,
                "evidence_pool": deep_report.evidence_pool[:80],
                "community_reference": deep_report.community_reference.model_dump(),
                "analysis_trace": deep_report.analysis_trace,
                "deterministic_findings": {
                    "dimension_reasons": {
                        key: value.reason
                        for key, value in deep_report.core_score.dimensions.items()
                    },
                    "risk_flags": deep_report.core_score.risk_flags,
                    "strengths": deep_report.strengths,
                    "risks": deep_report.risks,
                    "recommendations": deep_report.recommendations,
                },
            }
            deep_ai_assessment = await call_deep_ai_assessment(request.ai, ai_evidence)
            _progress(progress, "ai_review", "completed", f"AI 独立分 {deep_ai_assessment.score}")
        except ValueError as exc:
            ai_error = str(exc)
            _progress(progress, "ai_review", "failed", ai_error)
    else:
        _progress(progress, "ai_review", "skipped", "未填写完整 AI 配置，跳过 AI 独立审阅")

    final_score = core_score

    _progress(progress, "save_report", "running", "写入 SQLite 报告记录")
    payload = {
        "evidence": evidence,
        "local_index": deep_report.local_index,
        "core_score": deep_report.core_score.model_dump(),
        "dimensions": {
            key: value.model_dump()
            for key, value in deep_report.core_score.dimensions.items()
        },
        "suitability": deep_report.suitability.model_dump(),
        "community_reference": deep_report.community_reference.model_dump(),
        "exploration_notes": deep_report.exploration_notes,
        "evidence_pool": deep_report.evidence_pool,
        "agent_exploration": deep_report.agent_exploration,
        "analysis_trace": deep_report.analysis_trace,
        "summary": deep_report.summary,
        "strengths": deep_report.strengths,
        "risks": deep_report.risks,
        "recommendations": deep_report.recommendations,
        "github_warning": github_warning,
        "deep_ai_assessment": deep_ai_assessment.model_dump() if deep_ai_assessment else None,
        "rule_score": {
            "rule_score": core_score,
            "dimension_scores": dimension_percentages,
            "risk_flags": deep_report.core_score.risk_flags,
        },
        "ai_assessment": None,
        "ai_error": ai_error,
        "final_report": deep_report.model_dump(),
        "final_score": final_score,
    }
    report = insert_report(payload)
    _progress(progress, "save_report", "completed", f"报告 #{report['id']} 已保存")
    return report


@app.post("/api/analyze", response_model=ReportResponse)
async def analyze(request: AnalyzeRequest) -> dict:
    return await run_analysis_pipeline(request)


async def run_analysis_job(job_id: str, request: AnalyzeRequest) -> None:
    loop = asyncio.get_running_loop()

    def progress(step_id: str, status: str, detail: str = "", metadata: dict | None = None) -> None:
        def apply_progress() -> None:
            if status == "skipped":
                skip_step(job_id, step_id, detail)
            else:
                update_step(job_id, step_id, status, detail)
            if metadata and metadata.get("event"):
                add_step_event(job_id, step_id, metadata["event"])

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop.call_soon_threadsafe(apply_progress)
        else:
            apply_progress()

    try:
        report = await run_analysis_pipeline(request, progress)
    except HTTPException as exc:
        fail_job(job_id, str(exc.detail))
    except Exception as exc:
        fail_job(job_id, f"{exc.__class__.__name__}: {exc}")
    else:
        complete_job(job_id, report)


def schedule_analysis_task(coro):
    return asyncio.create_task(coro)


@app.post("/api/analyze/jobs", response_model=AnalysisJobResponse)
async def analyze_job(request: AnalyzeRequest) -> AnalysisJobResponse:
    job = create_analysis_job()
    schedule_analysis_task(run_analysis_job(job.job_id, request))
    return get_analysis_job(job.job_id)


@app.get("/api/analyze/jobs/{job_id}", response_model=AnalysisJobResponse)
def analyze_job_status(job_id: str) -> AnalysisJobResponse:
    try:
        return get_analysis_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="分析任务不存在") from exc


@app.get("/api/reports", response_model=list[RecentReport])
def reports() -> list[dict]:
    records = list_reports(limit=20)
    return [
        {
            "id": record["id"],
            "repo_full_name": record["repo_full_name"],
            "repo_url": record["repo_url"],
            "created_at": record["created_at"],
            "final_score": record["final_score"],
            "rule_score": record["rule_score"],
            "ai_score": record["ai_score"],
        }
        for record in records
    ]


@app.get("/api/reports/{report_id}", response_model=ReportResponse)
def report_detail(report_id: int) -> dict:
    try:
        return get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="报告不存在") from exc


@app.delete("/api/reports/{report_id}", response_class=Response)
def report_delete(report_id: int) -> Response:
    if not delete_report(report_id):
        raise HTTPException(status_code=404, detail="报告不存在")
    return Response(status_code=204)
