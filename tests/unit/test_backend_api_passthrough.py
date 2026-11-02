from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.core.config.settings import Settings
from app.core.exceptions import ProxyAuthError
from app.modules.proxy import api as proxy_api


def _request(path: str, headers: dict[str, str], query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


class _UpstreamResponse:
    status = 200
    headers = {"content-type": "application/json"}

    async def read(self) -> bytes:
        return b'{"accounts": []}'

    async def __aenter__(self) -> "_UpstreamResponse":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _Session:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def get(self, url: str, **kwargs: Any) -> _UpstreamResponse:
        self._captured["url"] = url
        self._captured.update(kwargs)
        return _UpstreamResponse()


class _Lease:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    async def __aenter__(self) -> _Session:
        return _Session(self._captured)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_workspace_routing_discovery_is_relayed_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease(captured))
    monkeypatch.setattr(
        proxy_api,
        "get_settings",
        lambda: Settings(upstream_base_url="https://chatgpt.com/backend-api"),
    )

    response = await proxy_api.backend_api_passthrough(
        "wham/accounts/check",
        _request(
            "/backend-api/wham/accounts/check",
            {"Authorization": "Bearer personal-token", "chatgpt-account-id": "personal-account"},
            query="include=plan",
        ),
    )

    # The Codex CLI refuses to probe without this: its own discovery call must behave
    # as it does against chatgpt.com, with the caller's credential and no pool secrets.
    assert captured["url"] == "https://chatgpt.com/backend-api/wham/accounts/check"
    assert captured["headers"]["authorization"] == "Bearer personal-token"
    assert captured["headers"]["chatgpt-account-id"] == "personal-account"
    assert captured["params"] == [("include", "plan")]
    assert response.status_code == 200
    assert response.body == b'{"accounts": []}'


@pytest.mark.asyncio
async def test_passthrough_requires_a_caller_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease({}))

    with pytest.raises(ProxyAuthError):
        await proxy_api.backend_api_passthrough(
            "wham/accounts/check",
            _request("/backend-api/wham/accounts/check", {}),
        )
