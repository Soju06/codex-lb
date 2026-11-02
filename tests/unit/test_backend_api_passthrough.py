from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.core.clients import backend_relay
from app.core.clients.backend_relay import (
    BackendRelayReservedPath,
    BackendRelayResponse,
    relay_backend_get,
)
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


def _stub_transport(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any], *, status: int = 200) -> None:
    monkeypatch.setattr(backend_relay, "lease_http_session", lambda: _Lease(captured, status))
    monkeypatch.setattr(
        backend_relay,
        "get_settings",
        lambda: Settings(upstream_base_url="https://chatgpt.com/backend-api"),
    )


@pytest.mark.asyncio
async def test_workspace_routing_discovery_is_relayed_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_transport(monkeypatch, captured)

    relayed = await relay_backend_get(
        "wham/accounts/check",
        headers=[
            ("authorization", "Bearer personal-token"),
            ("chatgpt-account-id", "personal-account"),
        ],
        query=[("include", "plan")],
    )

    # The Codex CLI refuses to probe without this: its own discovery call must behave
    # as it does against chatgpt.com, with the caller's credential and no pool secrets.
    assert captured["url"] == "https://chatgpt.com/backend-api/wham/accounts/check"
    assert captured["headers"]["authorization"] == "Bearer personal-token"
    assert captured["headers"]["chatgpt-account-id"] == "personal-account"
    assert captured["params"] == [("include", "plan")]
    assert relayed.status_code == 200
    assert relayed.body == b'{"accounts": []}'
    assert relayed.content_type == "application/json"


@pytest.mark.asyncio
async def test_relay_requires_a_caller_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_transport(monkeypatch, captured)

    for headers in (
        [],
        [("authorization", "Basic cG9vbDpzZWNyZXQ=")],
        [("authorization", "Bearer   ")],
    ):
        with pytest.raises(ProxyAuthError):
            await relay_backend_get("wham/accounts/check", headers=headers, query=[])
    assert captured == {}


@pytest.mark.asyncio
async def test_relay_forwards_only_the_callers_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_transport(monkeypatch, captured)

    await relay_backend_get(
        "wham/accounts/check",
        headers=[
            ("authorization", "Bearer personal-token"),
            ("chatgpt-account-id", "personal-account"),
            ("x-codex-turn-metadata", "meta"),
            # Neither of these belongs to the ChatGPT backend audience.
            ("cookie", "codex_lb_dashboard_session=secret"),
            ("proxy-authorization", "Basic cG9vbDpzZWNyZXQ="),
        ],
        query=[],
    )

    assert captured["headers"] == {
        "authorization": "Bearer personal-token",
        "chatgpt-account-id": "personal-account",
        "x-codex-turn-metadata": "meta",
    }
    assert captured["allow_redirects"] is False


@pytest.mark.asyncio
async def test_relay_does_not_follow_an_upstream_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_transport(monkeypatch, captured, status=302)

    with pytest.raises(ProxyUpstreamError):
        await relay_backend_get(
            "plugins/featured",
            headers=[("authorization", "Bearer personal-token")],
            query=[],
        )
    assert captured["allow_redirects"] is False


@pytest.mark.asyncio
async def test_relay_never_answers_for_the_pool_owned_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _stub_transport(monkeypatch, captured)

    # A GET that only the relay could answer for `/codex/...` must not borrow it: those
    # paths belong to handlers with their own method and auth semantics.
    for path in ("codex/responses", "codex/models/", "wham/usage/extra"):
        with pytest.raises(BackendRelayReservedPath):
            await relay_backend_get(
                path,
                headers=[("authorization", "Bearer personal-token")],
                query=[],
            )
    assert captured == {}


@pytest.mark.asyncio
async def test_route_returns_the_relayed_status_body_and_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stub_relay(path: str, **_: Any) -> BackendRelayResponse:
        assert path == "wham/accounts/check"
        return BackendRelayResponse(status_code=201, body=b'{"ok": true}', content_type="application/json")

    monkeypatch.setattr(proxy_api, "relay_backend_get", stub_relay)

    response = await proxy_api.backend_api_passthrough(
        "wham/accounts/check",
        _request("/backend-api/wham/accounts/check", {"Authorization": "Bearer personal-token"}),
    )

    assert isinstance(response, Response)
    assert response.status_code == 201
    assert response.body == b'{"ok": true}'
    assert response.media_type == "application/json"


@pytest.mark.asyncio
async def test_route_maps_the_pool_owned_namespace_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stub_relay(path: str, **_: Any) -> BackendRelayResponse:
        raise BackendRelayReservedPath(path)

    monkeypatch.setattr(proxy_api, "relay_backend_get", stub_relay)

    with pytest.raises(HTTPException) as raised:
        await proxy_api.backend_api_passthrough(
            "codex/models/",
            _request("/backend-api/codex/models/", {"Authorization": "Bearer personal-token"}),
        )
    assert raised.value.status_code == 404
