from __future__ import annotations

import importlib.util
import io
import urllib.error
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_script_module(name: str) -> ModuleType:
    path = Path(".github/scripts") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Response:
    headers = {"Link": '<https://api.github.test/next>; rel="next"'}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'[{"login":"octocat"}]'


def test_github_api_request_json_sleeps_and_retries_transient_html(monkeypatch) -> None:
    github_api = _load_script_module("github_api")
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        del timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                Message(),
                io.BytesIO(b"<html><title>Unicorn!</title></html>"),
            )
        return _Response()

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_urlopen)

    payload, link = github_api.request_json(
        "https://api.github.test/repos/example/project/contributors",
        token=None,
        attempts=2,
        sleep=sleeps.append,
    )

    assert calls == [
        "https://api.github.test/repos/example/project/contributors",
        "https://api.github.test/repos/example/project/contributors",
    ]
    assert sleeps == [2.0]
    assert payload == [{"login": "octocat"}]
    assert link == '<https://api.github.test/next>; rel="next"'


def test_github_api_request_json_sleeps_and_retries_rate_limit_403(monkeypatch) -> None:
    github_api = _load_script_module("github_api")
    calls: list[str] = []
    sleeps: list[float] = []
    headers = Message()
    headers["x-ratelimit-remaining"] = "0"

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        del timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                headers,
                io.BytesIO(b'{"message":"API rate limit exceeded"}'),
            )
        return _Response()

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_urlopen)

    payload, _link = github_api.request_json(
        "https://api.github.test/repos/example/project/pulls/1/files",
        token=None,
        attempts=2,
        sleep=sleeps.append,
    )

    assert calls == [
        "https://api.github.test/repos/example/project/pulls/1/files",
        "https://api.github.test/repos/example/project/pulls/1/files",
    ]
    assert sleeps == [2.0]
    assert payload == [{"login": "octocat"}]


def test_github_api_request_json_fails_closed_on_permission_403(monkeypatch) -> None:
    github_api = _load_script_module("github_api")
    headers = Message()
    headers["x-ratelimit-remaining"] = "42"

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        del timeout
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            headers,
            io.BytesIO(b'{"message":"Resource not accessible by integration"}'),
        )

    monkeypatch.setattr(github_api.urllib.request, "urlopen", fake_urlopen)

    try:
        github_api.request_json(
            "https://api.github.test/repos/example/project/pulls/1/files",
            token=None,
            attempts=2,
            sleep=lambda _delay: None,
        )
    except github_api.GitHubApiError as exc:
        assert exc.transient is False
        assert "Resource not accessible" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected permission 403 to fail closed")


def test_detect_changed_areas_falls_back_to_full_suite_after_github_outage(monkeypatch) -> None:
    detect_changed_areas = _load_script_module("detect_changed_areas")

    def fail_request_json(url: str):
        del url
        raise detect_changed_areas.GitHubApiError("HTTP 503: <html>Unicorn!</html>", transient=True)

    monkeypatch.setattr(detect_changed_areas, "request_json", fail_request_json)

    files = detect_changed_areas._pull_request_files({"pull_request": {"url": "https://api.github.test/prs/1"}})

    assert all(
        any(detect_changed_areas._matches(path, patterns) for path in files)
        for patterns in detect_changed_areas.FILTERS.values()
    )


def test_detect_changed_areas_includes_previous_filename_for_renames(monkeypatch) -> None:
    detect_changed_areas = _load_script_module("detect_changed_areas")

    def fake_request_json(url: str):
        del url
        return [
            {
                "filename": "docs/usage.md",
                "previous_filename": "app/legacy_usage.py",
                "status": "renamed",
            }
        ], None

    monkeypatch.setattr(detect_changed_areas, "request_json", fake_request_json)

    files = detect_changed_areas._pull_request_files({"pull_request": {"url": "https://api.github.test/prs/1"}})

    assert files == ["docs/usage.md", "app/legacy_usage.py"]
    assert any(detect_changed_areas._matches(path, detect_changed_areas.FILTERS["backend"]) for path in files)


def test_detect_changed_areas_fails_closed_on_nontransient_github_error(monkeypatch) -> None:
    detect_changed_areas = _load_script_module("detect_changed_areas")

    def fail_request_json(url: str):
        del url
        raise detect_changed_areas.GitHubApiError("HTTP 401: bad credentials", transient=False)

    monkeypatch.setattr(detect_changed_areas, "request_json", fail_request_json)

    try:
        detect_changed_areas._pull_request_files({"pull_request": {"url": "https://api.github.test/prs/1"}})
    except SystemExit as exc:
        assert "HTTP 401" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected non-transient GitHub API failure to fail closed")


def test_fetch_pr_labels_fails_closed_after_github_error(monkeypatch) -> None:
    fetch_pr_labels = _load_script_module("fetch_pr_labels")

    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setenv("PR_NUMBER", "123")

    def fail_request_json(url: str, *, token: str | None = None):
        del url, token
        raise fetch_pr_labels.GitHubApiError("HTTP 503: <html>Unicorn!</html>", transient=True)

    monkeypatch.setattr(fetch_pr_labels, "request_json", fail_request_json)

    try:
        fetch_pr_labels.main()
    except SystemExit as exc:
        assert "GitHub PR labels request failed after retries" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected label lookup failure to fail closed")


def test_check_all_contributors_fails_closed_on_nontransient_api_error(monkeypatch) -> None:
    check_all_contributors = _load_script_module("check_all_contributors")

    def fail_request_json(url: str, *, token: str | None = None):
        del url, token
        raise check_all_contributors.GitHubApiError("HTTP 403: forbidden", transient=False)

    monkeypatch.setattr(check_all_contributors, "request_json", fail_request_json)

    try:
        check_all_contributors.fetch_contributor_logins("example/project", token=None)
    except SystemExit as exc:
        assert "HTTP 403" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected non-transient contributors lookup failure to fail closed")


def test_check_all_contributors_fails_closed_after_transient_api_retries(monkeypatch) -> None:
    check_all_contributors = _load_script_module("check_all_contributors")

    def fail_request_json(url: str, *, token: str | None = None):
        del url, token
        raise check_all_contributors.GitHubApiError("HTTP 503: <html>Unicorn!</html>", transient=True)

    monkeypatch.setattr(check_all_contributors, "request_json", fail_request_json)

    try:
        check_all_contributors.fetch_contributor_logins("example/project", token=None)
    except SystemExit as exc:
        assert "cannot validate all-contributors coverage from partial evidence" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected transient contributors lookup exhaustion to fail closed")
