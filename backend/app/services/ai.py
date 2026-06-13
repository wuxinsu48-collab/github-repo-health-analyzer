from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.models import AiAssessment, AiConfig, ConnectionTestResponse, DeepAiAssessment


SYSTEM_PROMPT = """你是一个开源仓库体检评审员。你只能基于用户提供的 evidence bundle 做判断。
返回严格 JSON，不要输出 markdown。评分必须可解释，风险和建议用中文。"""

DEEP_SYSTEM_PROMPT = """你是一个严谨的开源仓库代码审阅 Agent。你只能基于用户提供的本地只读证据包评分，不能假设仓库之外的信息。
返回严格 JSON，不要输出 markdown。"""


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
        raise ValueError("AI 响应中没有找到 JSON 对象")

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI 响应不是合法 JSON: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("AI JSON 响应必须是对象")
    return parsed


def clamp_ai_score(ai_score: int, rule_score: int, rationale: str = "") -> int:
    if rationale.strip():
        return max(0, min(100, int(ai_score)))
    lower = max(0, rule_score - 10)
    upper = min(100, rule_score + 10)
    return max(lower, min(upper, int(ai_score)))


def clamp_score_adjustment(score_adjustment: int) -> int:
    return max(-10, min(10, int(score_adjustment)))


def parse_ai_response(raw: str, rule_score: int) -> AiAssessment:
    parsed = _extract_json(raw)
    required = {
        "confidence",
        "summary",
        "strengths",
        "risks",
        "recommendations",
        "dimension_comments",
    }
    missing = sorted(required - parsed.keys())
    if missing:
        raise ValueError(f"AI 响应缺少字段: {', '.join(missing)}")

    if "score_adjustment" in parsed:
        score_adjustment = clamp_score_adjustment(int(parsed["score_adjustment"]))
        parsed["score_adjustment"] = score_adjustment
        parsed["ai_score"] = max(0, min(100, int(rule_score) + score_adjustment))
    elif "ai_score" in parsed:
        parsed["ai_score"] = clamp_ai_score(
            ai_score=int(parsed["ai_score"]),
            rule_score=rule_score,
            rationale=str(parsed.get("score_rationale") or ""),
        )
        parsed["score_adjustment"] = clamp_score_adjustment(int(parsed["ai_score"]) - int(rule_score))
    else:
        raise ValueError("AI 响应缺少字段: score_adjustment")

    try:
        return AiAssessment.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"AI 响应字段格式不正确: {exc}") from exc


def build_chat_completions_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return urljoin(cleaned + "/", "chat/completions")


def build_ai_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    instruction = {
        "task": "请基于 evidence bundle 输出开源仓库健康体检 JSON。",
        "output_schema": {
            "score_adjustment": "-10 到 10 的整数。表示 AI 基于软性证据对 rule_score 的修正，不要直接复述 rule_score。",
            "confidence": "low | medium | high",
            "summary": "中文总结",
            "strengths": ["优势"],
            "risks": ["风险"],
            "recommendations": ["改进建议"],
            "dimension_comments": {
                "popularity": "中文评价",
                "activity": "中文评价",
                "community": "中文评价",
                "engineering": "中文评价",
                "risk": "中文评价",
            },
            "score_rationale": "解释 score_adjustment 的主要证据。若为 0，也要说明为什么不修正。",
        },
        "scoring_constraint": "不要输出 ai_score。后端会用 rule_score + score_adjustment 计算 AI 分。score_adjustment 为正表示软性证据优于规则分，为负表示软性风险高于规则分。",
        "evidence": evidence,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(instruction, ensure_ascii=False)},
    ]


def build_assessment_payload(model: str, evidence: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": build_ai_messages(evidence),
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("deepseek-"):
        payload["thinking"] = {"type": "disabled"}
    return payload


def parse_deep_ai_response(raw: str) -> DeepAiAssessment:
    parsed = _extract_json(raw)
    required = {
        "score",
        "confidence",
        "summary",
        "dimension_reviews",
        "strengths",
        "risks",
        "recommendations",
    }
    missing = sorted(required - parsed.keys())
    if missing:
        raise ValueError(f"AI 响应缺少字段: {', '.join(missing)}")

    parsed["score"] = max(0, min(100, int(parsed["score"])))
    try:
        return DeepAiAssessment.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"AI 响应字段格式不正确: {exc}") from exc


def build_deep_ai_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    instruction = {
        "task": "请基于本地只读代码证据包，给出独立 AI 评分和中文解释。",
        "scoring_standard": {
            "total": 100,
            "dimensions": {
                "architecture": "架构清晰度 20 分",
                "engineering": "工程完整度 20 分",
                "testing": "测试质量 15 分",
                "documentation": "文档质量 15 分",
                "security": "安全风险 15 分",
                "maintainability": "可维护性 15 分",
            },
            "important": "不要把 Star、Fork、社区热度计入核心分；它们只能作为背景参考。",
        },
        "output_schema": {
            "score": "0 到 100 的整数，AI 独立评分，不是规则分修正值",
            "confidence": "low | medium | high",
            "summary": "中文总结",
            "dimension_reviews": {
                "architecture": "中文评价",
                "engineering": "中文评价",
                "testing": "中文评价",
                "documentation": "中文评价",
                "security": "中文评价",
                "maintainability": "中文评价",
            },
            "strengths": ["优势"],
            "risks": ["风险"],
            "recommendations": ["改进建议"],
        },
        "evidence": evidence,
    }
    return [
        {"role": "system", "content": DEEP_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(instruction, ensure_ascii=False)},
    ]


def build_deep_assessment_payload(model: str, evidence: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": build_deep_ai_messages(evidence),
        "temperature": 0.15,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("deepseek-"):
        payload["thinking"] = {"type": "disabled"}
    return payload


async def call_ai_assessment(config: AiConfig, evidence: dict[str, Any], rule_score: int) -> AiAssessment:
    payload = build_assessment_payload(model=config.model, evidence=evidence)
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(build_chat_completions_url(config.base_url), headers=headers, json=payload)

    if response.status_code == 401:
        raise ValueError("AI API Key 无效或权限不足")
    if response.status_code == 404:
        raise ValueError("AI base_url 或模型接口不存在")
    if response.status_code >= 400:
        raise ValueError(f"AI 服务返回错误 HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("AI 响应格式不兼容 OpenAI Chat Completions") from exc

    return parse_ai_response(content, rule_score=rule_score)


async def call_deep_ai_assessment(config: AiConfig, evidence: dict[str, Any]) -> DeepAiAssessment:
    payload = build_deep_assessment_payload(model=config.model, evidence=evidence)
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(build_chat_completions_url(config.base_url), headers=headers, json=payload)
    except httpx.InvalidURL as exc:
        raise ValueError("AI base_url 格式不正确") from exc
    except httpx.TimeoutException as exc:
        raise ValueError("AI 服务连接超时，核心报告已生成，可稍后重试 AI 分析") from exc
    except httpx.ConnectError as exc:
        raise ValueError("无法连接 AI base_url，核心报告已生成") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"AI 请求失败: {exc.__class__.__name__}") from exc

    if response.status_code == 401:
        raise ValueError("AI API Key 无效或权限不足")
    if response.status_code == 404:
        raise ValueError("AI base_url 或模型接口不存在")
    if response.status_code >= 400:
        raise ValueError(f"AI 服务返回错误 HTTP {response.status_code}: {response.text[:300]}")

    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("AI 响应不是合法 JSON") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("AI 响应格式不兼容 OpenAI Chat Completions") from exc

    return parse_deep_ai_response(content)


async def test_ai_connection(config: AiConfig) -> ConnectionTestResponse:
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    if config.model.startswith("deepseek-"):
        payload["thinking"] = {"type": "disabled"}
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(build_chat_completions_url(config.base_url), headers=headers, json=payload)
    except httpx.InvalidURL:
        return ConnectionTestResponse(ok=False, message="base_url 格式不正确", details={"field": "base_url"})
    except httpx.ConnectError:
        return ConnectionTestResponse(ok=False, message="无法连接 AI base_url", details={"field": "base_url"})
    except httpx.TimeoutException:
        return ConnectionTestResponse(ok=False, message="AI 服务连接超时", details={"field": "base_url"})
    except httpx.HTTPError as exc:
        return ConnectionTestResponse(ok=False, message=f"AI 请求失败: {exc.__class__.__name__}")

    if response.status_code == 200:
        return ConnectionTestResponse(ok=True, message="AI 连接测试通过", details={"model": config.model})
    if response.status_code == 401:
        return ConnectionTestResponse(ok=False, message="AI API Key 无效或权限不足", details={"field": "api_key"})
    if response.status_code == 403:
        return ConnectionTestResponse(ok=False, message="AI API Key 权限不足或账户不可用", details={"field": "api_key"})
    if response.status_code == 400:
        return ConnectionTestResponse(
            ok=False,
            message="模型名称无效或请求参数不被该模型支持",
            details={"field": "model", "body": response.text[:300]},
        )
    if response.status_code == 404:
        return ConnectionTestResponse(
            ok=False,
            message="base_url 接口不存在，或模型名称不可用",
            details={"field": "base_url", "body": response.text[:300]},
        )
    return ConnectionTestResponse(
        ok=False,
        message=f"AI 服务返回 HTTP {response.status_code}",
        details={"field": "model", "body": response.text[:300]},
    )
