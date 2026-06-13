from __future__ import annotations

import copy
import uuid
from typing import Any

from app.models import AnalysisJobEvent, AnalysisJobResponse, AnalysisJobStep


JOB_STEP_DEFINITIONS = [
    ("parse_repo", "解析仓库地址"),
    ("github_metadata", "读取 GitHub 元数据"),
    ("clone_repo", "只读 clone 仓库"),
    ("index_repo", "扫描目录与代码证据"),
    ("langgraph_score", "基础规则评分"),
    ("ai_review", "AI Agent 深度评分"),
    ("save_report", "保存报告记录"),
]


_jobs: dict[str, AnalysisJobResponse] = {}


def _default_steps() -> list[AnalysisJobStep]:
    return [AnalysisJobStep(id=step_id, label=label) for step_id, label in JOB_STEP_DEFINITIONS]


def create_analysis_job() -> AnalysisJobResponse:
    job = AnalysisJobResponse(job_id=uuid.uuid4().hex, status="running", steps=_default_steps())
    _jobs[job.job_id] = job
    return copy.deepcopy(job)


def get_analysis_job(job_id: str) -> AnalysisJobResponse:
    if job_id not in _jobs:
        raise KeyError(job_id)
    return copy.deepcopy(_jobs[job_id])


def update_step(job_id: str, step_id: str, status: str, detail: str = "") -> None:
    job = _jobs[job_id]
    for step in job.steps:
        if step.id == step_id:
            step.status = status
            step.detail = detail
            break


def skip_step(job_id: str, step_id: str, detail: str = "") -> None:
    update_step(job_id, step_id, "skipped", detail)


def add_step_event(job_id: str, step_id: str, event: dict[str, Any] | AnalysisJobEvent) -> None:
    job = _jobs[job_id]
    event_model = event if isinstance(event, AnalysisJobEvent) else AnalysisJobEvent.model_validate(event)
    for step in job.steps:
        if step.id != step_id:
            continue
        for index, existing in enumerate(step.events):
            if existing.id == event_model.id:
                step.events[index] = event_model
                return
        step.events.append(event_model)
        return


def complete_job(job_id: str, report: dict[str, Any]) -> None:
    job = _jobs[job_id]
    job.status = "completed"
    job.report_id = int(report["id"])
    job.report = report


def fail_job(job_id: str, error: str) -> None:
    job = _jobs[job_id]
    job.status = "failed"
    job.error = error


def clear_jobs_for_tests() -> None:
    _jobs.clear()
