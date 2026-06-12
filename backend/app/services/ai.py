from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.models import AiAssessment, AiConfig, ConnectionTestResponse


SYSTEM_PROMPT = """你是一个开源仓库体检评审员。你只能基于用户提供的 evidence bundle 做判断。
返回严格 JSON，不要输出 markdown。评分必须可解释，风险和建议用中文。"""


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


def parse_ai_response(raw: str, rule_score: int) -> AiAssessment:
    parsed = _extract_json(raw)
    required = {
        "ai_score",
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

    parsed["ai_score"] = clamp_ai_score(
        ai_score=int(parsed["ai_score"]),
        rule_score=rule_score,
        rationale=str(parsed.get("score_rationale") or ""),
    )
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
            "ai_score": "0-100 integer",
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
            "score_rationale": "只有当 ai_score 超出 rule_score 正负 10 分时才必须填写",
        },
        "scoring_constraint": "默认 ai_score 应在 rule_score 正负 10 分内，除非 evidence 中有强证据并填写 score_rationale。",
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


async def test_ai_connection(config: AiConfig) -> ConnectionTestResponse:
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(build_chat_completions_url(config.base_url), headers=headers, json=payload)
    except httpx.InvalidURL:
        return ConnectionTestResponse(ok=False, message="base_url 格式不正确")
    except httpx.ConnectError:
        return ConnectionTestResponse(ok=False, message="无法连接 AI base_url")
    except httpx.TimeoutException:
        return ConnectionTestResponse(ok=False, message="AI 服务连接超时")
    except httpx.HTTPError as exc:
        return ConnectionTestResponse(ok=False, message=f"AI 请求失败: {exc.__class__.__name__}")

    if response.status_code == 200:
        return ConnectionTestResponse(ok=True, message="AI 连接测试通过", details={"model": config.model})
    if response.status_code == 401:
        return ConnectionTestResponse(ok=False, message="AI API Key 无效或权限不足")
    if response.status_code == 404:
        return ConnectionTestResponse(ok=False, message="base_url 或模型接口不存在")
    return ConnectionTestResponse(
        ok=False,
        message=f"AI 服务返回 HTTP {response.status_code}",
        details={"body": response.text[:300]},
    )
