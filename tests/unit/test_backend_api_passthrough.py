from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config.settings import Settings
from app.core.exceptions import ProxyAuthError, ProxyUpstreamError
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
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.headers = {"content-type": "application/json"}

    async def read(self) -> bytes:
        return b'{"accounts": []}'

    async def __aenter__(self) -> "_UpstreamResponse":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _Session:
    def __init__(self, captured: dict[str, Any], status: int = 200) -> None:
        self._captured = captured
        self._status = status

    def get(self, url: str, **kwargs: Any) -> _UpstreamResponse:
        self._captured["url"] = url
        self._captured.update(kwargs)
        return _UpstreamResponse(self._status)


class _Lease:
    def __init__(self, captured: dict[str, Any], status: int = 200) -> None:
        self._captured = captured
        self._status = status

    async def __aenter__(self) -> _Session:
        return _Session(self._captured, self._status)

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


@pytest.mark.asyncio
async def test_relay_forwards_only_the_callers_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease(captured))
    monkeypatch.setattr(
        proxy_api,
        "get_settings",
        lambda: Settings(upstream_base_url="https://chatgpt.com/backend-api"),
    )

    await proxy_api.backend_api_passthrough(
        "wham/accounts/check",
        _request(
            "/backend-api/wham/accounts/check",
            {
                "Authorization": "Bearer personal-token",
                "chatgpt-account-id": "personal-account",
                "x-codex-turn-metadata": "meta",
                # Neither of these belongs to the ChatGPT backend audience.
                "cookie": "codex_lb_dashboard_session=secret",
                "proxy-authorization": "Basic cG9vbDpzZWNyZXQ=",
            },
        ),
    )

    assert captured["headers"] == {
        "authorization": "Bearer personal-token",
        "chatgpt-account-id": "personal-account",
        "x-codex-turn-metadata": "meta",
    }
    assert captured["allow_redirects"] is False


@pytest.mark.asyncio
async def test_relay_requires_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease({}))

    for authorization in ("Basic cG9vbDpzZWNyZXQ=", "Bearer   ", ""):
        with pytest.raises(ProxyAuthError):
            await proxy_api.backend_api_passthrough(
                "wham/accounts/check",
                _request(
                    "/backend-api/wham/accounts/check",
                    {"Authorization": authorization} if authorization else {},
                ),
            )


@pytest.mark.asyncio
async def test_relay_does_not_follow_an_upstream_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease(captured, status=302))
    monkeypatch.setattr(
        proxy_api,
        "get_settings",
        lambda: Settings(upstream_base_url="https://chatgpt.com/backend-api"),
    )

    with pytest.raises(ProxyUpstreamError):
        await proxy_api.backend_api_passthrough(
            "plugins/featured",
            _request("/backend-api/plugins/featured", {"Authorization": "Bearer personal-token"}),
        )
    assert captured["allow_redirects"] is False


@pytest.mark.asyncio
async def test_relay_never_answers_for_the_pool_owned_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(proxy_api, "lease_http_session", lambda: _Lease(captured))

    # A GET that only the relay could answer for `/codex/...` must not borrow it: those
    # paths belong to handlers with their own method and auth semantics.
    for path in ("codex/responses", "codex/models/", "wham/usage/extra"):
        with pytest.raises(HTTPException):
            await proxy_api.backend_api_passthrough(
                path,
                _request(f"/backend-api/{path}", {"Authorization": "Bearer personal-token"}),
            )
    assert captured == {}
