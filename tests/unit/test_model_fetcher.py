from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.clients.model_fetcher import ModelFetchError, fetch_models_for_plan

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


async def test_fetch_models_for_plan_maps_read_timeout_to_model_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_settings",
        lambda: SimpleNamespace(upstream_base_url="https://upstream.example"),
    )
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_codex_version_cache",
        lambda: _VersionCache(),
    )
    monkeypatch.setattr(
        "app.core.clients.model_fetcher.get_http_client",
        lambda: SimpleNamespace(session=_Session()),
    )

    with pytest.raises(ModelFetchError) as exc_info:
        await fetch_models_for_plan("access-token", "account-id")

    assert exc_info.value.status_code == 504
    assert exc_info.value.message == "Upstream models API timed out"
