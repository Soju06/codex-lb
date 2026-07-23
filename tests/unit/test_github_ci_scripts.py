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


def test_detect_changed_areas_falls_back_to_full_suite_after_github_outage(monkeypatch) -> None:
    detect_changed_areas = _load_script_module("detect_changed_areas")

    def fail_request_json(url: str):
        del url
        raise detect_changed_areas.GitHubApiError("HTTP 503: <html>Unicorn!</html>")

    monkeypatch.setattr(detect_changed_areas, "request_json", fail_request_json)

    files = detect_changed_areas._pull_request_files({"pull_request": {"url": "https://api.github.test/prs/1"}})

    assert all(
        any(detect_changed_areas._matches(path, patterns) for path in files)
        for patterns in detect_changed_areas.FILTERS.values()
    )
