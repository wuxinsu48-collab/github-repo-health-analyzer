import pytest

from app.services.ai import build_assessment_payload, parse_ai_response, clamp_ai_score


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
