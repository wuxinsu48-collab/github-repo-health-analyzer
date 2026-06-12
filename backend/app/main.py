from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_report, init_db, insert_report, list_reports
from app.models import AnalyzeRequest, AiConfig, ConnectionTestResponse, GitHubTestRequest, RecentReport, ReportResponse
from app.services.ai import call_ai_assessment, test_ai_connection
from app.services.github import GitHubApiError, GitHubClient, parse_github_repo_url
from app.services.scoring import calculate_rule_score, compose_final_score


@asynccontextmanager
async def lifespan(_: FastAPI):
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


@app.post("/api/analyze", response_model=ReportResponse)
async def analyze(request: AnalyzeRequest) -> dict:
    try:
        owner, repo = parse_github_repo_url(request.repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = GitHubClient(token=request.github_token)
    try:
        evidence = await client.fetch_repo_evidence(owner, repo)
    except GitHubApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    rule_score = calculate_rule_score(evidence)
    evidence_for_ai = {
        **evidence,
        "rule_score": rule_score.rule_score,
        "dimension_scores": rule_score.dimension_scores,
        "risk_flags": rule_score.risk_flags,
    }

    ai_assessment = None
    ai_error = None
    if request.ai:
        try:
            ai_assessment = await call_ai_assessment(request.ai, evidence_for_ai, rule_score=rule_score.rule_score)
        except ValueError as exc:
            ai_error = str(exc)

    final_score = compose_final_score(
        rule_score=rule_score.rule_score,
        ai_score=ai_assessment.ai_score if ai_assessment else None,
    )

    payload = {
        "evidence": evidence,
        "rule_score": rule_score.model_dump(),
        "ai_assessment": ai_assessment.model_dump() if ai_assessment else None,
        "ai_error": ai_error,
        "final_score": final_score,
    }
    return insert_report(payload)


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
