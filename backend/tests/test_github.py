import pytest

from app.services.github import parse_github_repo_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/vuejs/core", ("vuejs", "core")),
        ("https://github.com/vuejs/core.git", ("vuejs", "core")),
        ("https://github.com/vuejs/core/issues/1", ("vuejs", "core")),
        ("git@github.com:psf/requests.git", ("psf", "requests")),
        ("github.com/fastapi/fastapi", ("fastapi", "fastapi")),
        ("zhanghuanhao/LibrarySystem", ("zhanghuanhao", "LibrarySystem")),
        ("[zhanghuanhao/LibrarySystem](https://github.com/zhanghuanhao/LibrarySystem)", ("zhanghuanhao", "LibrarySystem")),
    ],
)
def test_parse_github_repo_url_accepts_common_formats(url, expected):
    assert parse_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://gitlab.com/example/project",
        "https://github.com/only-owner",
        "not a url",
    ],
)
def test_parse_github_repo_url_rejects_invalid_values(url):
    with pytest.raises(ValueError):
        parse_github_repo_url(url)
