import pytest
import httpx

from app.models import AiConfig
from app.services.ai import (
    build_assessment_payload,
    call_deep_ai_assessment,
    clamp_ai_score,
    parse_ai_response,
    parse_deep_ai_response,
    test_ai_connection as run_ai_connection_test,
)


def test_parse_ai_response_extracts_json_from_markdown_block():
    raw = """
```json
{
  "ai_score": 86,
  "confidence": "medium",
  "summary": "整体健康。",
  "strengths": ["活跃"],
  "risks": ["缺少 CONTRIBUTING"],
  "recommendations": ["补充贡献指南"],
  "dimension_comments": {
    "popularity": "热度高",
    "activity": "近期有维护",
    "community": "社区文件较完整",
    "engineering": "有测试",
    "risk": "风险可控"
  }
}
```
"""

    result = parse_ai_response(raw, rule_score=82)

    assert result.ai_score == 86
    assert result.confidence == "medium"
    assert result.strengths == ["活跃"]
    assert result.score_adjustment == 4


def test_parse_ai_response_uses_score_adjustment_instead_of_echoed_rule_score():
    raw = """
{
  "score_adjustment": -7,
  "confidence": "high",
  "summary": "仓库热度不错，但长期未维护，工程实践不足。",
  "strengths": ["历史关注度较高"],
  "risks": ["长期未更新", "缺少 CI"],
  "recommendations": ["补充维护状态说明", "增加自动化测试"],
  "dimension_comments": {
    "popularity": "热度较好",
    "activity": "维护活跃度偏低",
    "community": "社区文件不足",
    "engineering": "工程化较弱",
    "risk": "存在维护风险"
  },
  "score_rationale": "长期未维护和缺少工程化信号需要下调。"
}
"""

    result = parse_ai_response(raw, rule_score=63)

    assert result.score_adjustment == -7
    assert result.ai_score == 56


def test_parse_ai_response_rejects_missing_required_fields():
    with pytest.raises(ValueError):
        parse_ai_response('{"ai_score": 80}', rule_score=82)


def test_clamp_ai_score_limits_large_drift_without_rationale():
    assert clamp_ai_score(ai_score=40, rule_score=80, rationale="") == 70
    assert clamp_ai_score(ai_score=99, rule_score=80, rationale="") == 90


def test_clamp_ai_score_allows_large_drift_with_rationale():
    assert clamp_ai_score(ai_score=55, rule_score=80, rationale="缺少关键安全文件且长期无人维护") == 55


def test_build_assessment_payload_requests_json_and_disables_thinking():
    payload = build_assessment_payload(
        model="deepseek-v4-flash",
        evidence={"repo": {"full_name": "owner/repo"}, "rule_score": 43},
    )

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["model"] == "deepseek-v4-flash"
    user_message = payload["messages"][1]["content"]
    assert "score_adjustment" in user_message
    assert '"ai_score":' not in user_message


def test_parse_deep_ai_response_keeps_ai_score_independent_from_rule_formula():
    raw = """
{
  "score": 68,
  "confidence": "high",
  "summary": "代码结构清楚，但测试和安全治理不足。",
  "dimension_reviews": {
    "architecture": "分层存在但边界一般",
    "engineering": "依赖文件存在",
    "testing": "测试较少",
    "documentation": "README 简略",
    "security": "缺少安全说明",
    "maintainability": "文件规模可控"
  },
  "strengths": ["入口文件清晰"],
  "risks": ["测试覆盖不足"],
  "recommendations": ["补充 CI 和安全说明"]
}
"""

    result = parse_deep_ai_response(raw)

    assert result.score == 68
    assert "architecture" in result.dimension_reviews


@pytest.mark.asyncio
async def test_call_deep_ai_assessment_reports_timeout_as_value_error(monkeypatch):
    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", TimeoutClient)

    with pytest.raises(ValueError, match="超时"):
        await call_deep_ai_assessment(
            AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash"),
            evidence={"repo": {"full_name": "owner/repo"}},
        )


@pytest.mark.asyncio
async def test_ai_connection_disables_deepseek_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        text = "{}"

    class CapturingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return Response()

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", CapturingClient)

    response = await run_ai_connection_test(
        AiConfig(base_url="https://api.example.test", api_key="placeholder", model="deepseek-v4-flash")
    )

    assert response.ok is True
    assert captured["json"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_ai_connection_reports_api_key_field_for_unauthorized(monkeypatch):
    class Response:
        status_code = 401
        text = '{"error":"unauthorized"}'

    class UnauthorizedClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", UnauthorizedClient)

    response = await run_ai_connection_test(
        AiConfig(base_url="https://api.example.test", api_key="bad-key", model="deepseek-v4-flash")
    )

    assert response.ok is False
    assert response.details["field"] == "api_key"


@pytest.mark.asyncio
async def test_ai_connection_reports_base_url_field_for_connection_error(monkeypatch):
    class ConnectionErrorClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("no route")

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", ConnectionErrorClient)

    response = await run_ai_connection_test(
        AiConfig(base_url="https://bad-host.example", api_key="placeholder", model="deepseek-v4-flash")
    )

    assert response.ok is False
    assert response.details["field"] == "base_url"


@pytest.mark.asyncio
async def test_ai_connection_reports_model_field_for_bad_request(monkeypatch):
    class Response:
        status_code = 400
        text = '{"error":{"message":"model not found"}}'

    class BadRequestClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("app.services.ai.httpx.AsyncClient", BadRequestClient)

    response = await run_ai_connection_test(
        AiConfig(base_url="https://api.example.test", api_key="placeholder", model="missing-model")
    )

    assert response.ok is False
    assert response.details["field"] == "model"
