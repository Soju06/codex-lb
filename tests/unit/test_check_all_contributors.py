from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def _load_checker_module():
    """Load the standalone coverage checker without running its CLI."""
    script_path = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_all_contributors.py"
    spec = importlib.util.spec_from_file_location("check_all_contributors", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("repository_login", "opener_login", "commit_login"),
    [
        ("CurrentName", None, None),
        (None, "CurrentName", None),
        (None, None, "CurrentName"),
        ("CurrentName", "FormerName", None),
        (None, "FormerName", "CurrentName"),
        ("FormerName", "FormerName", "CurrentName"),
    ],
    ids=[
        "repository",
        "pr-opener",
        "pr-commit",
        "stale-opener-repository",
        "stale-opener-pr-commit",
        "stale-repository-and-opener",
    ],
)
def test_main_resolves_renamed_authors_from_existing_api_pages(
    tmp_path, monkeypatch, capsys, repository_login, opener_login, commit_login
):
    """One stable account stays one contributor across renamed API and event evidence."""
    checker = _load_checker_module()
    config_path = tmp_path / ".all-contributorsrc"
    config_path.write_text(
        json.dumps({"contributors": [{"login": "CurrentName"}, {"login": "Existing"}]}), encoding="utf-8"
    )
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "commits": 2,
                    "commits_url": "https://api.github.test/repos/example/codex-lb/pulls/1/commits",
                    "user": {"id": 123, "login": opener_login, "type": "User"},
                }
            }
        ),
        encoding="utf-8",
    )
    repository_url = "https://api.github.com/repos/example/codex-lb/contributors?per_page=100&anon=false"
    repository_next = repository_url + "&page=2"
    commits_url = "https://api.github.test/repos/example/codex-lb/pulls/1/commits?per_page=100"
    commits_next = commits_url + "&page=2"
    pages = {
        repository_url: (
            [
                {"id": 7, "login": "Existing", "type": "User"},
                {"id": 8, "login": "TypedBot", "type": "Bot"},
            ],
            f'<{repository_next}>; rel="next"',
        ),
        repository_next: ([{"id": 123, "login": repository_login, "type": "User"}], None),
        commits_url: (
            [
                {"author": {"id": 7, "login": "Existing", "type": "User"}},
                {"author": {"id": 9, "login": "automation[bot]", "type": "User"}},
            ],
            f'<{commits_next}>; rel="next"',
        ),
        commits_next: ([{"author": {"id": 123, "login": commit_login, "type": "User"}}], None),
    }
    requested_urls: list[str] = []

    def fake_request_json(url, token):
        """Serve only the expected contributor and PR commit pages."""
        requested_urls.append(url)
        assert token == "token"
        return pages[url]

    monkeypatch.setattr(checker, "_request_json", fake_request_json)
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="123+FormerName@users.noreply.github.com\n7+Existing@users.noreply.github.com\n"
        ),
    )
    monkeypatch.setattr(checker.sys, "argv", ["check_all_contributors.py", "--config", str(config_path)])
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/codex-lb")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    assert checker.main() == 0
    assert capsys.readouterr() == ("all-contributors covers 2 GitHub commit contributors\n", "")
    assert requested_urls == [repository_url, repository_next, commits_url, commits_next]


@pytest.mark.parametrize(
    ("user", "email"),
    [
        ({"id": 2, "login": "CurrentName", "type": "User"}, "1+FormerName@users.noreply.github.com"),
        ({"id": 1, "login": "CurrentName", "type": "User"}, "FormerName@users.noreply.github.com"),
        ({"id": True, "login": "CurrentName", "type": "User"}, "1+FormerName@users.noreply.github.com"),
        ({"id": 1.0, "login": "CurrentName", "type": "User"}, "1+FormerName@users.noreply.github.com"),
        ({"id": "1", "login": "CurrentName", "type": "User"}, "1+FormerName@users.noreply.github.com"),
        ({"id": None, "login": "CurrentName", "type": "User"}, "1+FormerName@users.noreply.github.com"),
        ({"id": 0, "login": "CurrentName", "type": "User"}, "0+FormerName@users.noreply.github.com"),
        ({"id": 1, "login": "CurrentName", "type": "Bot"}, "1+FormerName@users.noreply.github.com"),
        ({"id": 1, "login": "automation[bot]", "type": "User"}, "1+FormerName@users.noreply.github.com"),
    ],
    ids=[
        "unknown-id",
        "legacy-email",
        "bool-id",
        "float-id",
        "string-id",
        "null-id",
        "zero-id",
        "bot-type",
        "bot-login",
    ],
)
def test_main_keeps_unresolved_local_authors_in_coverage(tmp_path, monkeypatch, capsys, user, email):
    """Unproven identity matches must not hide an unrecorded local author."""
    checker = _load_checker_module()
    config_path = tmp_path / ".all-contributorsrc"
    config_path.write_text(json.dumps({"contributors": [{"login": "CurrentName"}]}), encoding="utf-8")
    monkeypatch.setattr(checker, "_request_json", lambda url, token: ([user], None))
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=email + "\n3+dependabot[bot]@users.noreply.github.com\n"
        ),
    )
    monkeypatch.setattr(checker.sys, "argv", ["check_all_contributors.py", "--config", str(config_path)])
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    assert checker.main() == 1
    assert capsys.readouterr() == (
        "",
        "Missing GitHub commit contributors in .all-contributorsrc:\n"
        "  - formername\n"
        "\nAdd the missing people with all-contributors before merging, or update this checker if GitHub "
        "contributors semantics change.\n",
    )


def test_noreply_regex_accepts_current_and_legacy_github_formats():
    """Extract numeric identity when present while retaining legacy logins."""
    checker = _load_checker_module()

    current = checker.NOREPLY_RE.match("12345+octocat@users.noreply.github.com")
    legacy = checker.NOREPLY_RE.match("SHAREN@users.noreply.github.com")

    assert current is not None
    assert current.groupdict() == {"id": "12345", "login": "octocat"}
    assert legacy is not None
    assert legacy.groupdict() == {"id": None, "login": "SHAREN"}


def test_pull_request_commit_authors_include_normal_email_contributors(tmp_path, monkeypatch):
    """GitHub author evidence covers non-noreply email addresses and excludes bots."""
    checker = _load_checker_module()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "commits_url": "https://api.github.test/repos/example/codex-lb/pulls/1/commits",
                    "user": {"login": "opener", "type": "User"},
                }
            }
        ),
        encoding="utf-8",
    )
    requested_urls: list[str] = []

    def fake_request_json(url, token):
        """Return a human with a normal email alongside an excluded bot."""
        requested_urls.append(url)
        assert token == "token"
        return (
            [
                {
                    "commit": {"author": {"email": "normal@example.com"}},
                    "author": {"login": "NormalAuthor", "type": "User"},
                },
                {
                    "commit": {"author": {"email": "bot@example.com"}},
                    "author": {"login": "dependabot[bot]", "type": "Bot"},
                },
            ],
            None,
        )

    monkeypatch.setattr(checker, "_request_json", fake_request_json)

    assert set(checker.pull_request_commit_authors(str(event_path), "token").values()) == {"normalauthor"}
    assert requested_urls == ["https://api.github.test/repos/example/codex-lb/pulls/1/commits?per_page=100"]


def test_pull_request_commit_authors_fail_when_github_endpoint_is_capped(tmp_path):
    """An API-capped commit list cannot produce a passing coverage check."""
    checker = _load_checker_module()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "commits": 251,
                    "commits_url": "https://api.github.test/repos/example/codex-lb/pulls/1/commits",
                }
            }
        ),
        encoding="utf-8",
    )

    try:
        checker.pull_request_commit_authors(str(event_path), "token")
    except SystemExit as exc:
        assert "more than 250 commits" in str(exc)
    else:
        raise AssertionError("expected capped PR commit list to fail closed")


@pytest.mark.parametrize("partial_response", [False, True], ids=["first-page", "later-page"])
def test_pull_request_commit_authors_fail_closed_when_retries_exhaust(tmp_path, monkeypatch, partial_response):
    """A failed PR author page invalidates coverage even after earlier pages succeeded."""
    checker = _load_checker_module()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "commits_url": "https://api.github.test/repos/example/codex-lb/pulls/1/commits",
                    "user": {"login": "opener", "type": "User"},
                }
            }
        ),
        encoding="utf-8",
    )

    def failing_request_json(url, token):
        """Fail either before or after the first PR author page."""
        if partial_response and "page=2" not in url:
            return (
                [{"author": {"id": 123, "login": "CurrentName", "type": "User"}}],
                f'<{url}&page=2>; rel="next"',
            )
        raise checker.GitHubApiError("503 after retries")

    monkeypatch.setattr(checker, "_request_json", failing_request_json)

    with pytest.raises(SystemExit, match="cannot validate all-contributors coverage"):
        checker.pull_request_commit_authors(str(event_path), "token")


@pytest.mark.parametrize("partial_response", [False, True], ids=["first-page", "later-page"])
def test_fetch_contributors_fail_closed_when_retries_exhaust(monkeypatch, partial_response):
    """Repository contributor coverage must not accept partially fetched evidence."""
    checker = _load_checker_module()

    def failing_request_json(url, token):
        """Fail either before or after the first contributor page."""
        if partial_response and "page=2" not in url:
            return [{"id": 123, "login": "CurrentName", "type": "User"}], f'<{url}&page=2>; rel="next"'
        raise checker.GitHubApiError("503 after retries")

    monkeypatch.setattr(checker, "_request_json", failing_request_json)

    with pytest.raises(SystemExit, match="cannot validate all-contributors coverage"):
        checker.fetch_contributors("example/codex-lb", "token")
