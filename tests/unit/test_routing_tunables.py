"""Dashboard-managed routing weights and overload isolation (C2-2 routing/overload).

The five knobs resolve as code default < environment < dashboard, are frozen
once per request into ``RoutingTunables`` and reach the balancer through the
selection entry points; paths without a request snapshot reuse the balancer's
most recent one instead of reading settings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy import load_balancer as load_balancer_module
from app.modules.proxy._load_balancer.overload_backoff import (
    OVERLOAD_ISOLATION_TRIP_LEVEL,
    OVERLOAD_TRIP_COUNT,
    OverloadIsolationPolicy,
    record_upstream_overload,
)
from app.modules.proxy._load_balancer.tunables import RoutingTunables, resolve_routing_tunables
from app.modules.proxy._load_balancer.types import RuntimeState
from app.modules.proxy.load_balancer import LoadBalancer, _state_from_account, effective_routing_tunables
from tests.simulation.virtual_time import VirtualClock

_ENV = SimpleNamespace(
    proxy_overload_isolation_seconds=1800,
    proxy_account_error_rate_weighting_enabled=True,
    proxy_account_inflight_penalty_pct=2.5,
    proxy_account_lease_token_weight=1.0,
    proxy_account_lease_ttl_seconds=900.0,
)


def test_code_defaults_match_the_settings_fields() -> None:
    fields = Settings.model_fields
    assert RoutingTunables() == RoutingTunables(
        overload_isolation_seconds=float(fields["proxy_overload_isolation_seconds"].default),
        error_rate_weighting_enabled=fields["proxy_account_error_rate_weighting_enabled"].default,
        inflight_penalty_pct=fields["proxy_account_inflight_penalty_pct"].default,
        lease_token_weight=fields["proxy_account_lease_token_weight"].default,
        lease_ttl_seconds=fields["proxy_account_lease_ttl_seconds"].default,
    )


def test_null_columns_inherit_the_environment() -> None:
    environment = SimpleNamespace(
        proxy_overload_isolation_seconds=600,
        proxy_account_error_rate_weighting_enabled=False,
        proxy_account_inflight_penalty_pct=4.0,
        proxy_account_lease_token_weight=0.5,
        proxy_account_lease_ttl_seconds=300.0,
    )
    row = SimpleNamespace(
        proxy_overload_isolation_seconds=None,
        proxy_account_error_rate_weighting_enabled=None,
        proxy_account_inflight_penalty_pct=None,
        proxy_account_lease_token_weight=None,
        proxy_account_lease_ttl_seconds=None,
    )
    assert resolve_routing_tunables(row, startup_settings=environment) == RoutingTunables(
        overload_isolation_seconds=600.0,
        error_rate_weighting_enabled=False,
        inflight_penalty_pct=4.0,
        lease_token_weight=0.5,
        lease_ttl_seconds=300.0,
    )


def test_missing_snapshot_and_missing_environment_fields_fall_back_to_the_code_defaults() -> None:
    assert resolve_routing_tunables(None, startup_settings=SimpleNamespace()) == RoutingTunables()
    assert resolve_routing_tunables(None, startup_settings=_ENV) == RoutingTunables()


def test_dashboard_values_win_over_the_environment_including_zero_and_false() -> None:
    row = SimpleNamespace(
        proxy_overload_isolation_seconds=0,
        proxy_account_error_rate_weighting_enabled=False,
        proxy_account_inflight_penalty_pct=0.0,
        proxy_account_lease_token_weight=3.0,
        proxy_account_lease_ttl_seconds=120.0,
    )
    resolved = resolve_routing_tunables(row, startup_settings=_ENV)
    assert resolved == RoutingTunables(
        overload_isolation_seconds=0.0,
        error_rate_weighting_enabled=False,
        inflight_penalty_pct=0.0,
        lease_token_weight=3.0,
        lease_ttl_seconds=120.0,
    )
    assert not OverloadIsolationPolicy.from_tunables(resolved).enabled


def test_effective_routing_tunables_reads_the_balancer_module_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        load_balancer_module,
        "get_settings",
        lambda: SimpleNamespace(proxy_account_lease_ttl_seconds=45.0, proxy_overload_isolation_seconds=7),
    )
    assert effective_routing_tunables() == RoutingTunables(lease_ttl_seconds=45.0, overload_isolation_seconds=7.0)
    assert effective_routing_tunables(SimpleNamespace(proxy_account_lease_ttl_seconds=10.0)).lease_ttl_seconds == 10.0


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _usage(account_id: str, used_percent: float, now: float) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        window="primary",
        used_percent=used_percent,
        reset_at=int(now) + 3600,
        window_minutes=300,
        recorded_at=datetime.fromtimestamp(now, tz=timezone.utc),
    )


def test_state_pressure_uses_the_snapshot_penalty_not_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: _ENV)
    account = _account("acc-pressure")
    runtime = RuntimeState(inflight_response_creates=2, inflight_streams=2)

    now = 2_000_000_000.0
    usage = _usage(account.id, 20.0, now)
    environment_state = _state_from_account(
        account=account,
        primary_entry=usage,
        secondary_entry=None,
        runtime=runtime,
        now=now,
    )
    dashboard_state = _state_from_account(
        account=account,
        primary_entry=usage,
        secondary_entry=None,
        runtime=runtime,
        now=now,
        routing_tunables=RoutingTunables(inflight_penalty_pct=10.0),
    )
    # Four in-flight requests on a 20 % baseline: 2.5 % each from the
    # environment, 10 % each from the dashboard snapshot.
    assert environment_state.used_percent == pytest.approx(30.0)
    assert dashboard_state.used_percent == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_overload_isolation_uses_the_balancers_most_recent_request_snapshot(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: _ENV)
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(cast(Any, None), clock=clock)
    account = _account("acc-sustained")
    # Before any request, the environment applies.
    assert balancer.current_routing_tunables().overload_isolation_seconds == 1800.0

    # A request path hands the balancer the dashboard snapshot (here through the
    # lease entry point; select_account does the same); the error funnel that
    # runs later, without a snapshot of its own, must honour it.
    await balancer.acquire_account_lease(
        account.id, kind="response_create", routing_tunables=RoutingTunables(overload_isolation_seconds=240.0)
    )
    runtime = balancer._runtime[account.id]
    runtime.overload_backoff_level = OVERLOAD_ISOLATION_TRIP_LEVEL - 1
    runtime.overload_last_trip_at = clock.time()
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy._load_balancer.overload_backoff"):
        for _ in range(OVERLOAD_TRIP_COUNT):
            await record_upstream_overload(balancer, account)

    assert "isolation_seconds=240" in caplog.text
    assert runtime.overload_isolated_until == pytest.approx(clock.time() + 240.0)
