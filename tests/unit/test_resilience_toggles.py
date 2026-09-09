"""Hot-path resolution of the dashboard-managed resilience toggles (C2-3)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.resilience import toggles as toggles_module
from app.core.resilience.circuit_breaker import get_circuit_breaker_for_account
from app.core.resilience.toggles import (
    ResilienceToggles,
    bind_resilience_toggles,
    current_resilience_toggles,
    resolve_resilience_toggles,
    set_resilience_toggles,
)

pytestmark = pytest.mark.unit

_ENV = SimpleNamespace(soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=False)


def test_null_columns_inherit_the_environment_layer() -> None:
    snapshot = SimpleNamespace(
        soft_drain_enabled=None, deterministic_failover_enabled=None, circuit_breaker_enabled=None
    )
    env = SimpleNamespace(soft_drain_enabled=False, deterministic_failover_enabled=True, circuit_breaker_enabled=True)

    assert resolve_resilience_toggles(snapshot, startup_settings=env) == ResilienceToggles(
        soft_drain_enabled=False, deterministic_failover_enabled=True, circuit_breaker_enabled=True
    )


def test_missing_attributes_and_missing_snapshot_fall_back_to_code_defaults() -> None:
    # A snapshot double without the columns reads as NULL; an env double without
    # the fields reads as the code default (True / True / False).
    assert resolve_resilience_toggles(SimpleNamespace(), startup_settings=SimpleNamespace()) == ResilienceToggles(
        soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=False
    )
    assert resolve_resilience_toggles(None, startup_settings=SimpleNamespace()) == ResilienceToggles(
        soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=False
    )


def test_dashboard_values_win_over_the_environment() -> None:
    snapshot = SimpleNamespace(
        soft_drain_enabled=False, deterministic_failover_enabled=False, circuit_breaker_enabled=True
    )
    env = SimpleNamespace(soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=False)

    assert resolve_resilience_toggles(snapshot, startup_settings=env) == ResilienceToggles(
        soft_drain_enabled=False, deterministic_failover_enabled=False, circuit_breaker_enabled=True
    )


def test_resolver_defaults_to_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toggles_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=True))

    assert resolve_resilience_toggles(None).circuit_breaker_enabled is True


@pytest.mark.asyncio
async def test_bound_toggles_are_task_local_and_unbound_tasks_use_the_environment() -> None:
    snapshot = SimpleNamespace(
        soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=True
    )

    async def request_task() -> tuple[bool, bool]:
        bound = bind_resilience_toggles(snapshot, startup_settings=_ENV)
        return bound.circuit_breaker_enabled, current_resilience_toggles(startup_settings=_ENV).circuit_breaker_enabled

    async def unrelated_task() -> bool:
        return current_resilience_toggles(startup_settings=_ENV).circuit_breaker_enabled

    assert await asyncio.create_task(request_task()) == (True, True)
    # Bindings made inside one task never leak into a sibling task.
    assert await asyncio.create_task(unrelated_task()) is False


def test_breaker_registry_is_constructed_regardless_of_the_toggle() -> None:
    # Construction is unconditional; the dashboard toggle gates use per request,
    # so re-enabling the breaker never needs a restart.
    breaker = get_circuit_breaker_for_account("acc-c2-3")
    assert get_circuit_breaker_for_account("acc-c2-3") is breaker


@pytest.mark.asyncio
async def test_generator_resumed_by_another_task_rebinds_resolved_toggles() -> None:
    """ContextVars follow tasks, not generators (codex review, streaming startup probe)."""
    snapshot = SimpleNamespace(
        soft_drain_enabled=True, deterministic_failover_enabled=True, circuit_breaker_enabled=True
    )

    async def request_stream():
        resolved = bind_resilience_toggles(snapshot, startup_settings=_ENV)
        yield "keepalive"  # first item, driven by the startup-probe task
        # Upstream attempt, driven by whichever task resumed the generator.
        set_resilience_toggles(resolved)
        yield current_resilience_toggles(startup_settings=_ENV).circuit_breaker_enabled

    stream = request_stream()
    assert await asyncio.create_task(stream.__anext__()) == "keepalive"
    # Without the rebind the resuming task would see the environment layer (off).
    assert await asyncio.create_task(stream.__anext__()) is True
    await stream.aclose()
