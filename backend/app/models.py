from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GitHubTestRequest(BaseModel):
    token: str | None = None


class AiConfig(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    github_token: str | None = None
    ai: AiConfig | None = None


class AnalysisJobStep(BaseModel):
    id: str
    label: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    detail: str = ""


class AnalysisJobResponse(BaseModel):
    job_id: str
    status: Literal["running", "completed", "failed"]
    steps: list[AnalysisJobStep]
    report_id: int | None = None
    report: dict[str, Any] | None = None
    error: str | None = None


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RuleScoreResult(BaseModel):
    rule_score: int = Field(ge=0, le=100)
    dimension_scores: dict[str, int]
    risk_flags: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    label: str
    path: str | None = None
    excerpt: str = ""


class ScoreDimension(BaseModel):
    score: int = Field(ge=0)
    max_score: int = Field(gt=0)
    reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CoreScoreResult(BaseModel):
    score: int = Field(ge=0, le=100)
    dimensions: dict[str, ScoreDimension]
    summary: str
    risk_flags: list[str] = Field(default_factory=list)


class SuitabilityScores(BaseModel):
    learning: int = Field(ge=0, le=100)
    secondary_development: int = Field(ge=0, le=100)
    production: int = Field(ge=0, le=100)
    notes: dict[str, str] = Field(default_factory=dict)


class CommunityReference(BaseModel):
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    watchers: int = 0
    pushed_at: str | None = None
    archived: bool = False
    disabled: bool = False
    default_branch: str | None = None
    license_name: str | None = None
    topics: list[str] = Field(default_factory=list)
    recent_commits: int = 0
    releases: int = 0


class DeepAiAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"] = "medium"
    summary: str
    dimension_reviews: dict[str, str] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class DeepAnalysisReport(BaseModel):
    core_score: CoreScoreResult
    suitability: SuitabilityScores
    community_reference: CommunityReference
    local_index: dict[str, Any]
    analysis_trace: list[str] = Field(default_factory=list)
    summary: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class AiAssessment(BaseModel):
    ai_score: int = Field(ge=0, le=100)
    score_adjustment: int = Field(default=0, ge=-10, le=10)
    confidence: Literal["low", "medium", "high"] = "medium"
    summary: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    dimension_comments: dict[str, str] = Field(default_factory=dict)
    score_rationale: str = ""


class RecentReport(BaseModel):
    id: int
    repo_full_name: str
    repo_url: str
    created_at: str
    final_score: int
    rule_score: int
    ai_score: int | None = None


class ReportResponse(BaseModel):
    id: int
    repo_full_name: str
    repo_url: str
    created_at: str
    final_score: int
    rule_score: int
    ai_score: int | None = None
    payload: dict[str, Any]
