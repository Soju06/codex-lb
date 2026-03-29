from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Iterator, cast
from unittest.mock import AsyncMock, patch

import pytest

from app.core.openai.model_registry import ModelRegistry, ReasoningLevel, UpstreamModel
from app.core.resilience.circuit_breaker import CircuitState
from app.core.resilience.degradation import get_status, is_degraded, set_degraded, set_normal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_degradation_state() -> Iterator[None]:
    set_normal()
    yield
    set_normal()


def _model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="balanced"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset(),
        raw={},
    )


class _StubAccountsRepository:
    def __init__(self, accounts: list[object]) -> None:
        self._accounts = accounts

    async def list_accounts(self) -> list[object]:
        return list(self._accounts)

    async def update_status_if_current(self, *args, **kwargs) -> bool:
        return True

    async def update_status(self, *args, **kwargs) -> bool:
        return True


class _StubUsageRepository:
    async def latest_by_account(self, window: str | None = None) -> dict[str, object]:
        return {}


class _StubStickyRepository:
    async def get_account_id(self, *args, **kwargs) -> str | None:
        return None

    async def upsert(self, *args, **kwargs):
        return None

    async def delete(self, *args, **kwargs) -> bool:
        return False


class _StubAdditionalUsageRepository:
    async def latest_by_account(self, *args, **kwargs) -> dict[str, object]:
        return {}

    async def latest_by_quota_key(self, *args, **kwargs) -> dict[str, object]:
        return {}


@asynccontextmanager
async def _repo_factory(accounts: list[object]):
    yield ProxyRepositories(
        accounts=cast(AccountsRepository, _StubAccountsRepository(accounts)),
        usage=cast(UsageRepository, _StubUsageRepository()),
        request_logs=cast(RequestLogsRepository, SimpleNamespace()),
        sticky_sessions=cast(StickySessionsRepository, _StubStickyRepository()),
        api_keys=cast(ApiKeysRepository, SimpleNamespace()),
        additional_usage=cast(AdditionalUsageRepository, _StubAdditionalUsageRepository()),
    )


def test_set_degraded_sets_status() -> None:
    set_degraded("all upstream accounts are unavailable")

    assert is_degraded() is True
    assert get_status() == {
        "level": "degraded",
        "reason": "all upstream accounts are unavailable",
    }


def test_set_normal_clears_degraded_state() -> None:
    set_degraded("temporary outage")

    set_normal()

    assert is_degraded() is False
    assert get_status() == {"level": "normal", "reason": None}


@pytest.mark.asyncio
async def test_health_ready_reports_degraded_status() -> None:
    from app.modules.health.api import health_ready

    set_degraded("all upstream accounts are unavailable")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("app.core.draining._draining", False), patch("app.modules.health.api.get_session") as mock_get_session:

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        response = await health_ready()

    assert response.status == "degraded"
    assert response.checks == {
        "database": "ok",
        "upstream": "degraded",
        "upstream_reason": "all upstream accounts are unavailable",
    }


@pytest.mark.asyncio
async def test_health_ready_reports_degraded_when_circuit_breaker_open() -> None:
    from fastapi import HTTPException

    from app.modules.health.api import health_ready

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("app.core.draining._draining", False), patch("app.modules.health.api.get_session") as mock_get_session:
        with patch("app.modules.health.api.get_settings", return_value=SimpleNamespace(circuit_breaker_enabled=True)):
            with patch(
                "app.modules.health.api.get_circuit_breaker",
                return_value=SimpleNamespace(state=CircuitState.OPEN),
            ):

                async def mock_get_session_context():
                    yield mock_session

                mock_get_session.return_value = mock_get_session_context()

                with pytest.raises(HTTPException) as exc_info:
                    await health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Circuit breaker open — upstream unavailable"


@pytest.mark.asyncio
async def test_model_registry_keeps_cached_models_when_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"plus": [_model("cached-model")]})

    def _raise_runtime_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.core.openai.model_registry.ModelRegistrySnapshot", _raise_runtime_error)

    with pytest.raises(RuntimeError, match="boom"):
        await registry.update({"pro": [_model("new-model")]})

    assert set(registry.get_models_with_fallback()) == {"cached-model"}


@pytest.mark.asyncio
async def test_load_balancer_returns_degraded_message_when_no_accounts_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )

    balancer = LoadBalancer(lambda: _repo_factory([]))
    selection = await balancer.select_account()

    assert selection.account is None
    assert selection.error_message == (
        "No available accounts. Service is operating in degraded mode: all upstream accounts are unavailable"
    )
    assert is_degraded() is True
