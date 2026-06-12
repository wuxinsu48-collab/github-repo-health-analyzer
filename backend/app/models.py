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


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RuleScoreResult(BaseModel):
    rule_score: int = Field(ge=0, le=100)
    dimension_scores: dict[str, int]
    risk_flags: list[str] = Field(default_factory=list)


class AiAssessment(BaseModel):
    ai_score: int = Field(ge=0, le=100)
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

