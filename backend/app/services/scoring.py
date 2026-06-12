from __future__ import annotations

from datetime import datetime, timezone
from math import log10
from typing import Any

from app.models import RuleScoreResult


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, round(value)))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(value: str | None, now: datetime | None = None) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (now - parsed).days)


def _score_popularity(repo: dict[str, Any]) -> int:
    stars = int(repo.get("stars") or 0)
    forks = int(repo.get("forks") or 0)
    subscribers = int(repo.get("subscribers") or 0)
    star_score = min(70, log10(stars + 1) * 18)
    fork_score = min(20, log10(forks + 1) * 8)
    subscriber_score = min(10, log10(subscribers + 1) * 4)
    return _clamp(star_score + fork_score + subscriber_score)


def _score_activity(repo: dict[str, Any], commits: list[dict[str, Any]], releases: list[dict[str, Any]]) -> int:
    pushed_days = _days_since(repo.get("pushed_at"))
    if pushed_days is None:
        recency = 5
    elif pushed_days <= 30:
        recency = 80
    elif pushed_days <= 180:
        recency = 65
    elif pushed_days <= 365:
        recency = 45
    elif pushed_days <= 1095:
        recency = 22
    else:
        recency = 5

    commit_score = min(10, len(commits) * 2)
    release_score = 10 if releases else 0
    return _clamp(recency + commit_score + release_score)


def _score_community(repo: dict[str, Any], community: dict[str, Any]) -> int:
    readme = bool(community.get("readme"))
    license_present = bool(community.get("license") or repo.get("license"))
    contributing = bool(community.get("contributing"))
    code_of_conduct = bool(community.get("code_of_conduct"))
    security = bool(community.get("security"))
    return _clamp(
        (20 if readme else 0)
        + (20 if license_present else 0)
        + (20 if contributing else 0)
        + (20 if code_of_conduct else 0)
        + (20 if security else 0)
    )


def _score_engineering(tree: list[str], releases: list[dict[str, Any]]) -> int:
    lower_paths = [path.lower() for path in tree]
    has_ci = any(path.startswith(".github/workflows/") or "/.github/workflows/" in path for path in lower_paths)
    has_tests = any(
        path.startswith("test/")
        or path.startswith("tests/")
        or "/test/" in path
        or "/tests/" in path
        or path.endswith(".test.ts")
        or path.endswith(".spec.ts")
        or path.endswith("_test.py")
        for path in lower_paths
    )
    has_manifest = any(
        path.endswith(name)
        for path in lower_paths
        for name in (
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "go.mod",
            "cargo.toml",
            "pom.xml",
        )
    )
    has_docker = any(path.endswith("dockerfile") or path.endswith("docker-compose.yml") for path in lower_paths)
    has_source = any(path.startswith(prefix) for path in lower_paths for prefix in ("src/", "app/", "lib/"))

    return _clamp(
        (25 if has_ci else 0)
        + (25 if has_tests else 0)
        + (20 if has_manifest else 0)
        + (10 if has_docker else 0)
        + (10 if has_source else 0)
        + (10 if releases else 0)
    )


def _score_risk(repo: dict[str, Any], community: dict[str, Any]) -> tuple[int, list[str]]:
    score = 100
    flags: list[str] = []

    if repo.get("archived"):
        score -= 40
        flags.append("仓库已归档")
    if repo.get("disabled"):
        score -= 30
        flags.append("仓库已禁用")
    if not (community.get("license") or repo.get("license")):
        score -= 15
        flags.append("缺少许可证")
    if not community.get("readme"):
        score -= 15
        flags.append("缺少 README")

    pushed_days = _days_since(repo.get("pushed_at"))
    if pushed_days is None or pushed_days > 365:
        score -= 25
        flags.append("长期未更新")

    stars = int(repo.get("stars") or 0)
    open_issues = int(repo.get("open_issues") or 0)
    if open_issues > 200 or (stars > 0 and open_issues / max(stars, 1) > 0.2):
        score -= 10
        flags.append("Issue 积压偏高")

    if repo.get("fork"):
        score -= 5
        flags.append("这是 Fork 仓库")

    return _clamp(score), flags


def calculate_rule_score(evidence: dict[str, Any]) -> RuleScoreResult:
    repo = evidence.get("repo") or {}
    community = evidence.get("community") or {}
    tree = evidence.get("tree") or []
    commits = evidence.get("commits") or []
    releases = evidence.get("releases") or []

    popularity = _score_popularity(repo)
    activity = _score_activity(repo, commits, releases)
    community_score = _score_community(repo, community)
    engineering = _score_engineering(tree, releases)
    risk, risk_flags = _score_risk(repo, community)

    rule_score = _clamp(
        popularity * 0.20
        + activity * 0.25
        + community_score * 0.20
        + engineering * 0.20
        + risk * 0.15
    )

    return RuleScoreResult(
        rule_score=rule_score,
        dimension_scores={
            "popularity": popularity,
            "activity": activity,
            "community": community_score,
            "engineering": engineering,
            "risk": risk,
        },
        risk_flags=risk_flags,
    )


def compose_final_score(rule_score: int, ai_score: int | None = None) -> int:
    if ai_score is None:
        return _clamp(rule_score)
    return _clamp(rule_score * 0.7 + ai_score * 0.3)

