from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_checker_module():
    script_path = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_all_contributors.py"
    spec = importlib.util.spec_from_file_location("check_all_contributors", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_noreply_regex_accepts_current_and_legacy_github_formats():
    checker = _load_checker_module()

    current = checker.NOREPLY_RE.match("12345+octocat@users.noreply.github.com")
    legacy = checker.NOREPLY_RE.match("SHAREN@users.noreply.github.com")

    assert current is not None
    assert current.group(1) == "octocat"
    assert legacy is not None
    assert legacy.group(1) == "SHAREN"


def test_pull_request_commit_authors_include_normal_email_contributors(tmp_path, monkeypatch):
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

    assert checker.pull_request_commit_author_logins(str(event_path), "token") == {"normalauthor"}
    assert requested_urls == ["https://api.github.test/repos/example/codex-lb/pulls/1/commits?per_page=100"]


def test_pull_request_commit_authors_fail_when_github_endpoint_is_capped(tmp_path):
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
        checker.pull_request_commit_author_logins(str(event_path), "token")
    except SystemExit as exc:
        assert "more than 250 commits" in str(exc)
    else:
        raise AssertionError("expected capped PR commit list to fail closed")


def test_pull_request_commit_authors_fail_closed_when_retries_exhaust(tmp_path, monkeypatch):
    import pytest

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
        raise checker.GitHubApiError("503 after retries")

    monkeypatch.setattr(checker, "_request_json", failing_request_json)

    with pytest.raises(SystemExit, match="cannot validate all-contributors coverage"):
        checker.pull_request_commit_author_logins(str(event_path), "token")


def test_fetch_contributor_logins_fail_closed_when_retries_exhaust(monkeypatch):
    import pytest

    checker = _load_checker_module()

    def failing_request_json(url, token):
        raise checker.GitHubApiError("503 after retries")

    monkeypatch.setattr(checker, "_request_json", failing_request_json)

    with pytest.raises(SystemExit, match="cannot validate all-contributors coverage"):
        checker.fetch_contributor_logins("example/codex-lb", "token")
