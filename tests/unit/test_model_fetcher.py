from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.clients.model_fetcher import ModelFetchError, fetch_models_for_plan
from app.core.clients.native_egress import NativeEgressUnavailable
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class _TimeoutResponse:
    status = 200

    async def __aenter__(self) -> "_TimeoutResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> object:
        raise asyncio.TimeoutError


class _Session:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, *args: object, **kwargs: object) -> _TimeoutResponse:
        self.calls.append({"args": args, **kwargs})
        return _TimeoutResponse()


class _VersionCache:
    async def get_version(self) -> str:
        return "0.128.0"


class _CodexResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {
            "models": [
                {
                    "slug": "gpt-5.2",
                    "display_name": "GPT-5.2",
                    "description": "model",
                    "base_instructions": "",
                    "context_window": 128000,
                    "priority": 1,
                    "model_messages": {
                        "instructions_template": "You are a coding assistant.",
                        "instructions_variables": {"personality_default": ""},
                        "approvals": None,
                    },
                }
            ]
        }


class _CodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return _CodexResponse()


class _NativeModelResponse:
    status = 200

    async def __aenter__(self) -> "_NativeModelResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> object:
        del content_type
        return _CodexResponse().json()


class _NativeModelClient:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[object] = []

    async def request(self, request: object) -> object:
        self.calls.append(request)
        if self.unavailable:
            raise NativeEgressUnavailable("helper disappeared")
        return _NativeModelResponse()


async def test_fetch_models_for_plan_maps_read_timeout_to_model_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example"),
    )
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_codex_version_cache",
        lambda: _VersionCache(),
    )

    @contextlib.asynccontextmanager
    async def lease_session():
        yield _Session()

    monkeypatch.setattr("app.core.clients.model_fetcher.lease_http_session", lease_session)

    with pytest.raises(ModelFetchError) as exc_info:
        await fetch_models_for_plan("access-token", "account-id", allow_direct_egress=True)

    assert exc_info.value.status_code == 504
    assert exc_info.value.message == "Upstream models API timed out"
    assert exc_info.value.transport_error is True


async def test_fetch_models_for_plan_uses_resolved_codex_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example/backend-api"),
    )
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_codex_version_cache",
        lambda: _VersionCache(),
    )
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = _CodexClient()

    models = await fetch_models_for_plan("access-token", "account-id", route=route, codex_client=cast(Any, client))

    assert [model.slug for model in models] == ["gpt-5.2"]
    assert client.calls[0]["route"] is route
    assert client.calls[0]["method"] == "GET"
    assert str(client.calls[0]["url"]).endswith("/codex/models?client_version=0.128.0")
    headers = cast(dict[str, str], client.calls[0]["headers"])
    assert headers == {
        "Authorization": "Bearer access-token",
        "chatgpt-account-id": "account-id",
        "Accept": "*/*",
        "originator": "codex_cli_rs",
        "User-Agent": "codex_cli_rs/0.128.0 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10",
    }
    assert list(headers) == [
        "Authorization",
        "chatgpt-account-id",
        "Accept",
        "originator",
        "User-Agent",
    ]
    assert "aiohttp" not in headers["User-Agent"].lower()
    assert client.calls[0]["skip_auto_headers"] == {"Accept-Encoding"}
    assert models[0].raw["model_messages"] == {
        "instructions_template": "You are a coding assistant.",
        "instructions_variables": {"personality_default": ""},
        "approvals": None,
    }


async def test_fetch_models_for_plan_direct_uses_codex_control_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example/backend-api"),
    )
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_codex_version_cache",
        lambda: _VersionCache(),
    )
    session = _Session()

    @contextlib.asynccontextmanager
    async def lease_session():
        yield session

    monkeypatch.setattr("app.core.clients.model_fetcher.lease_http_session", lease_session)

    with pytest.raises(ModelFetchError):
        # The timeout response is intentional; headers are captured before its
        # JSON decoder raises so this test stays independent of model fixtures.
        await fetch_models_for_plan("access-token", "account-id", allow_direct_egress=True)

    headers = cast(dict[str, str], session.calls[0]["headers"])
    assert headers["Accept"] == "*/*"
    assert headers["originator"] == "codex_cli_rs"
    assert "version" not in {name.lower() for name in headers}
    assert headers["User-Agent"].startswith("codex_cli_rs/0.128.0 ")
    assert "aiohttp" not in headers["User-Agent"].lower()
    assert session.calls[0]["skip_auto_headers"] == {"Accept-Encoding"}


async def test_fetch_models_for_plan_direct_prefers_native_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example/backend-api"),
    )
    monkeypatch.setattr("app.core.clients.model_fetcher.get_codex_version_cache", lambda: _VersionCache())

    @contextlib.asynccontextmanager
    async def unexpected_python_session():
        raise AssertionError("aiohttp must not run after native selection")
        yield

    monkeypatch.setattr("app.core.clients.model_fetcher.lease_http_session", unexpected_python_session)
    client = _NativeModelClient()

    models = await fetch_models_for_plan(
        "access-token",
        "account-id",
        allow_direct_egress=True,
        native_egress_client=cast(Any, client),
    )

    assert [model.slug for model in models] == ["gpt-5.2"]
    assert len(client.calls) == 1
    request = client.calls[0]
    assert getattr(request, "method") == "GET"
    assert getattr(request, "headers")["originator"] == "codex_cli_rs"


async def test_fetch_models_for_plan_falls_back_only_when_native_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example/backend-api"),
    )
    monkeypatch.setattr("app.core.clients.model_fetcher.get_codex_version_cache", lambda: _VersionCache())
    session = _Session()

    @contextlib.asynccontextmanager
    async def lease_session():
        yield session

    monkeypatch.setattr("app.core.clients.model_fetcher.lease_http_session", lease_session)

    with pytest.raises(ModelFetchError):
        await fetch_models_for_plan(
            "access-token",
            "account-id",
            allow_direct_egress=True,
            native_egress_client=cast(Any, _NativeModelClient(unavailable=True)),
        )

    assert len(session.calls) == 1
