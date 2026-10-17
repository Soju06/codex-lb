"""Dashboard-managed resilience toggles change runtime behaviour without a restart (C2-3)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

import app.core.clients.proxy as proxy_client
import app.modules.proxy.load_balancer as load_balancer_module
from app.core.balancer.logic import DRAIN_PRIMARY_THRESHOLD_PCT
from app.core.config.settings_cache import get_settings_cache
from app.core.resilience import circuit_breaker as circuit_breaker_module
from app.core.resilience.circuit_breaker import CircuitBreakerOpenError, CircuitState
from app.core.resilience.toggles import bind_resilience_toggles, resolve_resilience_toggles
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _server_error_response():
    yield SimpleNamespace(status=503)


async def _call_upstream(account_id: str) -> None:
    async with proxy_client._service_circuit_breaker_context(
        cast(Any, _server_error_response()),
        account_id=account_id,
    ):
        pass


@pytest.mark.asyncio
async def test_circuit_breaker_toggle_flipped_via_put_gates_breaker_use_without_restart(async_client, monkeypatch):
    account_id = "acc-resilience-toggle"
    monkeypatch.setattr(circuit_breaker_module, "_account_circuit_breakers", {})

    # Environment default is off. Turn the breaker on from the dashboard: the
    # request path takes a fresh snapshot, binds the toggles, and the client
    # now records failures and eventually opens the breaker.
    enabled = await async_client.put("/api/settings", json={"circuitBreakerEnabled": True})
    assert enabled.status_code == 200
    bind_resilience_toggles(await get_settings_cache().get())
    for _ in range(circuit_breaker_module._FAILURE_THRESHOLD):
        await _call_upstream(account_id)
    breaker = circuit_breaker_module._account_circuit_breakers[account_id]
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitBreakerOpenError):
        await _call_upstream(account_id)
    assert load_balancer_module._is_upstream_circuit_breaker_open(
        resolve_resilience_toggles(await get_settings_cache().get()).circuit_breaker_enabled
    )

    # Turn it off again from the dashboard. The (still open) breaker object
    # survives, but the next request no longer consults it, and selection no
    # longer reports the upstream as degraded.
    disabled = await async_client.put("/api/settings", json={"circuitBreakerEnabled": False})
    assert disabled.status_code == 200
    bind_resilience_toggles(await get_settings_cache().get())
    await _call_upstream(account_id)
    assert circuit_breaker_module._account_circuit_breakers[account_id] is breaker
    assert breaker.state is CircuitState.OPEN
    assert not load_balancer_module._is_upstream_circuit_breaker_open(
        resolve_resilience_toggles(await get_settings_cache().get()).circuit_breaker_enabled
    )


@pytest.mark.asyncio
async def test_soft_drain_toggle_flipped_via_put_controls_health_tier(async_client, monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    account = Account(
        id="acc-soft-drain",
        chatgpt_account_id="chatgpt-acc-soft-drain",
        email="drain@example.com",
        plan_type="plus",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
    )
    primary = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=datetime.fromtimestamp(now - 10, tz=UTC).replace(tzinfo=None),
        window="primary",
        used_percent=DRAIN_PRIMARY_THRESHOLD_PCT + 5.0,
        reset_at=int(now + 3600),
        window_minutes=300,
    )

    def health_tier(soft_drain_enabled: bool | None) -> int:
        return _state_from_account(
            account=account,
            primary_entry=primary,
            secondary_entry=None,
            runtime=RuntimeState(),
            now=now,
            soft_drain_enabled=soft_drain_enabled,
        ).health_tier

    # Environment default: soft drain on, usage above the fixed threshold drains the account.
    snapshot = await get_settings_cache().get()
    assert health_tier(resolve_resilience_toggles(snapshot).soft_drain_enabled) == 1

    disabled = await async_client.put("/api/settings", json={"softDrainEnabled": False})
    assert disabled.status_code == 200
    snapshot = await get_settings_cache().get()
    assert resolve_resilience_toggles(snapshot).soft_drain_enabled is False
    assert health_tier(resolve_resilience_toggles(snapshot).soft_drain_enabled) == 0
    # Callers that pass nothing keep inheriting the environment layer.
    assert health_tier(None) == 1
