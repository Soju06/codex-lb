from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.clients.model_fetcher import ModelFetchError, fetch_models_for_plan
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
    def get(self, *args: object, **kwargs: object) -> _TimeoutResponse:
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
                }
            ]
        }


class _CodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return _CodexResponse()


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
