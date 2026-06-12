from app.services.scoring import calculate_rule_score, compose_final_score


def test_calculate_rule_score_rewards_active_well_documented_repo():
    evidence = {
        "repo": {
            "stars": 12000,
            "forks": 1600,
            "watchers": 12000,
            "subscribers": 320,
            "open_issues": 80,
            "created_at": "2020-01-01T00:00:00Z",
            "pushed_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "archived": False,
            "disabled": False,
            "fork": False,
            "license": {"name": "MIT"},
            "default_branch": "main",
            "size": 12000,
        },
        "languages": {"TypeScript": 700000, "Python": 300000},
        "community": {
            "readme": True,
            "license": True,
            "contributing": True,
            "code_of_conduct": True,
            "security": True,
        },
        "tree": [
            "src/index.ts",
            "tests/index.test.ts",
            ".github/workflows/ci.yml",
            "package.json",
            "Dockerfile",
        ],
        "commits": [{"sha": "a"}, {"sha": "b"}],
        "releases": [{"tag_name": "v1.0.0"}],
    }

    result = calculate_rule_score(evidence)

    assert result.rule_score >= 80
    assert result.dimension_scores["community"] == 100
    assert result.dimension_scores["engineering"] >= 80
    assert result.risk_flags == []


def test_calculate_rule_score_penalizes_inactive_archived_repo():
    evidence = {
        "repo": {
            "stars": 10,
            "forks": 1,
            "watchers": 10,
            "subscribers": 0,
            "open_issues": 250,
            "created_at": "2018-01-01T00:00:00Z",
            "pushed_at": "2021-01-01T00:00:00Z",
            "updated_at": "2021-01-01T00:00:00Z",
            "archived": True,
            "disabled": False,
            "fork": False,
            "license": None,
            "default_branch": "master",
            "size": 100,
        },
        "languages": {},
        "community": {
            "readme": False,
            "license": False,
            "contributing": False,
            "code_of_conduct": False,
            "security": False,
        },
        "tree": [],
        "commits": [],
        "releases": [],
    }

    result = calculate_rule_score(evidence)

    assert result.rule_score < 45
    assert "仓库已归档" in result.risk_flags
    assert "长期未更新" in result.risk_flags


def test_compose_final_score_uses_70_percent_rule_score_and_30_percent_ai_score():
    assert compose_final_score(rule_score=80, ai_score=90) == 83

