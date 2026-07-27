from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
)
from app.core.balancer.logic import (
    DRAIN_PRIMARY_THRESHOLD_PCT,
    DRAIN_SECONDARY_THRESHOLD_PCT,
    PROBE_QUIET_SECONDS,
    PROBE_SUCCESS_STREAK_REQUIRED,
    AccountState,
)
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.affinity import _codex_session_selection_key
from app.modules.proxy.cap_partitioning import CapPartition
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState, effective_account_concurrency_caps
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_dashboard_caps_from_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SettingsCache:
        async def get(self) -> object:
            return load_balancer_module.get_settings()

    monkeypatch.setattr(load_balancer_module, "get_settings_cache", lambda: _SettingsCache())


def _make_account(account_id: str) -> Account:
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


def test_effective_account_concurrency_caps_supports_partial_settings_double(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        load_balancer_module,
        "get_settings",
        lambda: SimpleNamespace(circuit_breaker_enabled=False),
    )

    assert effective_account_concurrency_caps() == load_balancer_module.AccountConcurrencyCaps(
        response_create_limit=4,
        stream_limit=8,
    )


@pytest.mark.asyncio
async def test_account_lease_uses_explicit_dashboard_cap_snapshot_not_startup_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup_settings = SimpleNamespace(
        proxy_account_lease_ttl_seconds=60.0,
        proxy_request_budget_seconds=10.0,
        http_responses_stream_request_budget_seconds=7200.0,
        http_responses_session_bridge_request_budget_seconds=7200.0,
        proxy_account_response_create_limit=1,
        proxy_account_stream_limit=1,
    )
    dashboard_settings = SimpleNamespace(
        proxy_account_response_create_limit=1,
        proxy_account_stream_limit=1,
    )

    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: startup_settings)
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([]), _StubUsageRepository({}, {})))

    first = await balancer.acquire_account_lease(
        "acc-dashboard-caps",
        kind="stream",
        concurrency_caps=effective_account_concurrency_caps(dashboard_settings),
    )
    dashboard_settings.proxy_account_stream_limit = 2
    second = await balancer.acquire_account_lease(
        "acc-dashboard-caps",
        kind="stream",
        concurrency_caps=effective_account_concurrency_caps(dashboard_settings),
    )
    third = await balancer.acquire_account_lease(
        "acc-dashboard-caps",
        kind="stream",
        concurrency_caps=effective_account_concurrency_caps(dashboard_settings),
    )

    assert first is not None
    assert second is not None
    assert third is None


class _StubAccountsRepository:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def get_by_id(self, account_id: str) -> Account | None:
        return next((account for account in self._accounts if account.id == account_id), None)

    async def update_status(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True

    async def update_status_if_current(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True


class _BlockingProbeAccountsRepository(_StubAccountsRepository):
    def __init__(self, accounts: list[Account]) -> None:
        super().__init__(accounts)
        self.probe_snapshot_started = asyncio.Event()
        self.release_probe_snapshot = asyncio.Event()

    async def get_by_id(self, account_id: str) -> Account | None:
        self.probe_snapshot_started.set()
        await self.release_probe_snapshot.wait()
        return await super().get_by_id(account_id)


class _StubUsageRepository:
    def __init__(
        self,
        primary: dict[str, UsageHistory],
        secondary: dict[str, UsageHistory],
        monthly: dict[str, UsageHistory] | None = None,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._monthly = monthly or {}

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        del account_ids
        if window == "secondary":
            return self._secondary
        if window == "monthly":
            return self._monthly
        return self._primary

    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        if window == "secondary":
            return self._secondary.get(account_id)
        if window == "monthly":
            return self._monthly.get(account_id)
        return self._primary.get(account_id)


class _StubStickySessionsRepository:
    def __init__(self) -> None:
        self.account_id: str | None = None
        self.account_ids_by_key: dict[str, str] | None = None
        self.deleted: list[tuple[str, StickySessionKind | None]] = []
        self.upserts: list[tuple[str, str, StickySessionKind | None]] = []

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        if self.account_ids_by_key is not None:
            return self.account_ids_by_key.get(cast(str, args[0]))
        del args, kwargs
        return self.account_id

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        sticky_key = cast(str, args[0])
        account_id = cast(str, args[1])
        self.account_id = account_id
        self.upserts.append((sticky_key, account_id, kwargs.get("kind")))
        return None

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        sticky_key = cast(str, args[0])
        self.deleted.append((sticky_key, kwargs.get("kind")))
        self.account_id = None
        return True

    async def restore_if_current(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str | None,
        restore_account_id: str | None,
    ) -> bool:
        if self.account_id != expected_account_id:
            return False
        if restore_account_id is None:
            self.deleted.append((key, kind))
            self.account_id = None
            return True
        self.upserts.append((key, restore_account_id, kind))
        self.account_id = restore_account_id
        return True


class _ConcurrentUnboundStickySessionsRepository(_StubStickySessionsRepository):
    def __init__(self, expected_lookups: int) -> None:
        super().__init__()
        self._expected_lookups = expected_lookups
        self._lookup_count = 0
        self._all_lookups_started = asyncio.Event()

    async def get_account_id(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._lookup_count += 1
        if self._lookup_count >= self._expected_lookups:
            self._all_lookups_started.set()
        await self._all_lookups_started.wait()
        return None


class _ConcurrentBoundStickySessionsRepository(_StubStickySessionsRepository):
    def __init__(self, *, account_id: str, expected_lookups: int) -> None:
        super().__init__()
        self.account_id = account_id
        self._initial_account_id = account_id
        self._expected_lookups = expected_lookups
        self._lookup_count = 0
        self._all_lookups_started = asyncio.Event()

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        self._lookup_count += 1
        if self._lookup_count >= self._expected_lookups:
            self._all_lookups_started.set()
        await self._all_lookups_started.wait()
        return self._initial_account_id


class _FailingUpsertStickySessionsRepository(_StubStickySessionsRepository):
    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("sticky persistence unavailable")


@asynccontextmanager
async def _repo_factory(
    accounts_repo: _StubAccountsRepository,
    usage_repo: _StubUsageRepository,
    sticky_repo: _StubStickySessionsRepository | None = None,
) -> AsyncIterator[ProxyRepositories]:
    sticky_repo = sticky_repo or _StubStickySessionsRepository()
    yield ProxyRepositories(
        accounts=cast(Any, accounts_repo),
        usage=cast(Any, usage_repo),
        request_logs=cast(RequestLogsRepository, object()),
        sticky_sessions=cast(Any, sticky_repo),
        api_keys=cast(ApiKeysRepository, object()),
        additional_usage=cast(AdditionalUsageRepository, object()),
    )


def _usage_row(entry_id: int, account_id: str, *, window: str, reset_at: int) -> UsageHistory:
    return UsageHistory(
        id=entry_id,
        account_id=account_id,
        recorded_at=datetime.now(tz=timezone.utc),
        window=window,
        used_percent=10.0,
        reset_at=reset_at,
        window_minutes=5 if window == "primary" else 60,
    )


def _usage_row_with_percent(
    entry_id: int,
    account_id: str,
    *,
    used_percent: float,
    reset_at: int,
) -> UsageHistory:
    row = _usage_row(entry_id, account_id, window="primary", reset_at=reset_at)
    row.used_percent = used_percent
    return row


class _FakeGaugeChild:
    def __init__(self, values: dict[tuple[str, str], float], account_id: str, kind: str) -> None:
        self._values = values
        self._account_id = account_id
        self._kind = kind

    def set(self, value: float) -> None:
        self._values[(self._account_id, self._kind)] = value


class _FakeAccountInflightGauge:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], float] = {}

    def labels(self, *, account_id: str, kind: str) -> _FakeGaugeChild:
        return _FakeGaugeChild(self.values, account_id, kind)


@pytest.mark.asyncio
async def test_select_account_100_concurrent_calls_avoid_serial_persist_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-concurrency-a")
    account_b = _make_account("acc-concurrency-b")

    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row(1, account_a.id, window="primary", reset_at=now_epoch + 300),
            account_b.id: _usage_row(2, account_b.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={
            account_a.id: _usage_row(3, account_a.id, window="secondary", reset_at=now_epoch + 3600),
            account_b.id: _usage_row(4, account_b.id, window="secondary", reset_at=now_epoch + 3600),
        },
    )

    original_persist = LoadBalancer._persist_selection_state

    async def slow_persist(self: LoadBalancer, *args: Any, **kwargs: Any) -> set[str]:
        await asyncio.sleep(0.01)
        return await original_persist(self, *args, **kwargs)

    monkeypatch.setattr(LoadBalancer, "_persist_selection_state", slow_persist)

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    start = time.perf_counter()
    results = await asyncio.gather(*(balancer.select_account() for _ in range(100)))
    elapsed = time.perf_counter() - start

    # The injected persist delay is 10ms per state, and each selection persists
    # two states. A fully serialized implementation would therefore take about
    # 2.0s for 100 selections. Allow extra scheduler slack for shared CI
    # runners, but still require a comfortably sub-serialized runtime.
    assert elapsed < 1.25, f"Expected <1.25s for 100 concurrent selections, got {elapsed:.3f}s"
    assert all(result.account is not None for result in results)


@pytest.mark.asyncio
async def test_record_error_updates_are_atomic_with_per_account_lock() -> None:
    account = _make_account("acc-error-atomic")
    accounts_repo = _StubAccountsRepository([account])
    usage_repo = _StubUsageRepository(primary={}, secondary={})
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    await asyncio.gather(*(balancer.record_error(account) for _ in range(50)))

    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 50
    assert runtime.last_error_at is not None


@pytest.mark.asyncio
async def test_successful_force_probes_promote_probing_account_to_healthy() -> None:
    account = _make_account("acc-force-probe-success")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        error_count=2,
        last_error_at=time.time() - 120.0,
    )

    for _ in range(PROBE_SUCCESS_STREAK_REQUIRED):
        await balancer.record_probe_result(
            account_id=account.id,
            http_status=200,
        )

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_HEALTHY
    assert runtime.probe_success_streak == 0
    assert runtime.error_count == 0
    assert runtime.last_error_at is None


@pytest.mark.asyncio
async def test_unsuccessful_force_probe_resets_probe_success_streak() -> None:
    account = _make_account("acc-force-probe-rejected")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=2,
        version=7,
    )

    await balancer.record_probe_result(
        account_id=account.id,
        http_status=400,
    )

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.probe_success_streak == 0
    assert runtime.version == 8
    assert runtime.error_count == 0


@pytest.mark.asyncio
async def test_unsuccessful_force_probe_bumps_version_without_success_streak() -> None:
    account = _make_account("acc-force-probe-rejected-without-streak")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=0,
        version=11,
    )

    await balancer.record_probe_result(
        account_id=account.id,
        http_status=400,
    )

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.probe_success_streak == 0
    assert runtime.version == 12
    assert runtime.error_count == 0


@pytest.mark.asyncio
async def test_successful_force_probe_does_not_override_usage_drain() -> None:
    account = _make_account("acc-force-probe-usage-drained")
    now_epoch = int(time.time())
    usage_repo = _StubUsageRepository(
        {
            account.id: _usage_row_with_percent(
                80,
                account.id,
                used_percent=DRAIN_PRIMARY_THRESHOLD_PCT,
                reset_at=now_epoch + 300,
            )
        },
        {},
    )
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=2,
    )

    await balancer.record_probe_result(
        account_id=account.id,
        http_status=200,
    )

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_DRAINING
    assert runtime.probe_success_streak == 0
    assert runtime.drain_entered_at is not None


@pytest.mark.asyncio
async def test_successful_force_probe_counts_after_draining_quiet_period() -> None:
    account = _make_account("acc-force-probe-after-quiet")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_DRAINING,
        drain_entered_at=time.time() - PROBE_QUIET_SECONDS - 1.0,
        error_count=2,
        last_error_at=time.time() - PROBE_QUIET_SECONDS - 1.0,
    )

    await balancer.record_probe_result(
        account_id=account.id,
        http_status=204,
    )

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.probe_success_streak == 1
    assert runtime.error_count == 0


@pytest.mark.asyncio
async def test_successful_force_probe_counts_after_persisted_status_normalizes_active() -> None:
    account = _make_account("acc-force-probe-stale-rate-limit")
    now_epoch = int(time.time())
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = now_epoch - 30
    usage_repo = _StubUsageRepository(
        {
            account.id: _usage_row_with_percent(
                85,
                account.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            )
        },
        {},
    )
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=0,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.probe_success_streak == 1


@pytest.mark.asyncio
async def test_successful_force_probe_does_not_clear_errors_before_probe_eligibility() -> None:
    account = _make_account("acc-force-probe-ineligible-clear")
    accounts_repo = _StubAccountsRepository([account])
    usage_repo = _StubUsageRepository(primary={}, secondary={})
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_HEALTHY,
        error_count=2,
        last_error_at=time.time(),
        health_version=3,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 2
    assert runtime.last_error_at is not None
    assert runtime.probe_success_streak == 0
    assert runtime.health_version >= 3


@pytest.mark.asyncio
async def test_force_probe_uses_monthly_usage_for_free_account_health() -> None:
    account = _make_account("acc-force-probe-monthly")
    account.plan_type = "free"
    now_epoch = int(time.time())
    monthly = _usage_row(81, account.id, window="monthly", reset_at=now_epoch + 30 * 24 * 3600)
    monthly.used_percent = DRAIN_SECONDARY_THRESHOLD_PCT
    monthly.window_minutes = 30 * 24 * 60
    usage_repo = _StubUsageRepository({}, {}, {account.id: monthly})
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=2,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_DRAINING
    assert runtime.probe_success_streak == 0


@pytest.mark.asyncio
async def test_force_probe_ignores_zero_capacity_primary_for_free_account() -> None:
    account = _make_account("acc-force-probe-free-primary")
    account.plan_type = "free"
    now_epoch = int(time.time())
    primary = _usage_row_with_percent(
        83,
        account.id,
        used_percent=DRAIN_PRIMARY_THRESHOLD_PCT + 2.0,
        reset_at=now_epoch + 300,
    )
    monthly = _usage_row(84, account.id, window="monthly", reset_at=now_epoch + 30 * 24 * 3600)
    monthly.used_percent = 10.0
    monthly.window_minutes = 30 * 24 * 60
    usage_repo = _StubUsageRepository({account.id: primary}, {}, {account.id: monthly})
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=2,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_HEALTHY
    assert runtime.probe_success_streak == 0


@pytest.mark.asyncio
async def test_force_probe_remaps_weekly_only_primary_before_health_evaluation() -> None:
    account = _make_account("acc-force-probe-weekly-primary")
    now_epoch = int(time.time())
    weekly_primary = _usage_row_with_percent(
        82,
        account.id,
        used_percent=DRAIN_PRIMARY_THRESHOLD_PCT + 2.0,
        reset_at=now_epoch + 7 * 24 * 3600,
    )
    weekly_primary.window_minutes = 7 * 24 * 60
    usage_repo = _StubUsageRepository({account.id: weekly_primary}, {})
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=2,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_HEALTHY
    assert runtime.probe_success_streak == 0


@pytest.mark.asyncio
async def test_stale_reclaim_keeps_active_stream_lease_within_stream_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        proxy_account_lease_ttl_seconds=1.0,
        proxy_request_budget_seconds=10.0,
        http_responses_stream_request_budget_seconds=7200.0,
        http_responses_session_bridge_request_budget_seconds=7200.0,
        proxy_account_stream_limit=2,
        proxy_account_response_create_limit=2,
    )
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)
    account = _make_account("acc-stale-stream-budget")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))

    stream_lease = await balancer.acquire_account_lease(account.id, kind="stream")
    assert stream_lease is not None
    object.__setattr__(stream_lease, "acquired_at", time.monotonic() - 2.0)

    second_stream_lease = await balancer.acquire_account_lease(account.id, kind="stream")

    assert second_stream_lease is not None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 2, 0.0)


@pytest.mark.asyncio
async def test_stale_reclaim_still_recovers_old_response_create_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        proxy_account_lease_ttl_seconds=1.0,
        proxy_request_budget_seconds=10.0,
        http_responses_stream_request_budget_seconds=7200.0,
        http_responses_session_bridge_request_budget_seconds=7200.0,
        proxy_account_stream_limit=2,
        proxy_account_response_create_limit=2,
    )
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)
    account = _make_account("acc-stale-response-create")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))

    response_lease = await balancer.acquire_account_lease(account.id, kind="response_create")
    assert response_lease is not None
    object.__setattr__(response_lease, "acquired_at", time.monotonic() - 2.0)

    replacement_lease = await balancer.acquire_account_lease(account.id, kind="response_create")

    assert replacement_lease is not None
    assert await balancer.account_pressure_snapshot(account.id) == (1, 0, 0.0)


@pytest.mark.asyncio
async def test_account_inflight_lease_metric_tracks_acquire_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _make_account("acc-inflight-metric")
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), _StubUsageRepository({}, {})))
    gauge = _FakeAccountInflightGauge()
    monkeypatch.setattr(load_balancer_module, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(load_balancer_module, "account_inflight_leases", gauge)

    stream_lease = await balancer.acquire_account_lease(account.id, kind="stream")
    assert stream_lease is not None
    assert gauge.values[(account.id, "response_create")] == 0
    assert gauge.values[(account.id, "stream")] == 1

    response_create_lease = await balancer.acquire_account_lease(account.id, kind="response_create")
    assert response_create_lease is not None
    assert gauge.values[(account.id, "response_create")] == 1
    assert gauge.values[(account.id, "stream")] == 1

    await balancer.release_account_lease(stream_lease)
    assert gauge.values[(account.id, "response_create")] == 1
    assert gauge.values[(account.id, "stream")] == 0

    await balancer.release_account_lease(response_create_lease)
    assert gauge.values[(account.id, "response_create")] == 0
    assert gauge.values[(account.id, "stream")] == 0


@pytest.mark.asyncio
async def test_account_stream_leases_spread_concurrent_burst_until_cap() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-lease-a")
    account_b = _make_account("acc-lease-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row(10, account_a.id, window="primary", reset_at=now_epoch + 300),
            account_b.id: _usage_row(11, account_b.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={
            account_a.id: _usage_row(12, account_a.id, window="secondary", reset_at=now_epoch + 3600),
            account_b.id: _usage_row(13, account_b.id, window="secondary", reset_at=now_epoch + 3600),
        },
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    results = await asyncio.gather(
        *(
            balancer.select_account(
                routing_strategy="usage_weighted",
                lease_kind="stream",
            )
            for _ in range(16)
        )
    )

    selected_ids = [result.account.id for result in results if result.account is not None]
    assert selected_ids.count(account_a.id) == 8
    assert selected_ids.count(account_b.id) == 8
    assert all(result.lease is not None for result in results)

    for result in results:
        await balancer.release_account_lease(result.lease)

    assert await balancer.account_pressure_snapshot(account_a.id) == (0, 0, 0.0)
    assert await balancer.account_pressure_snapshot(account_b.id) == (0, 0, 0.0)


@pytest.mark.asyncio
async def test_account_stream_cap_returns_stable_local_reason_until_released() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account = _make_account("acc-stream-cap")
    accounts_repo = _StubAccountsRepository([account])
    usage_repo = _StubUsageRepository(
        primary={account.id: _usage_row(20, account.id, window="primary", reset_at=now_epoch + 300)},
        secondary={account.id: _usage_row(21, account.id, window="secondary", reset_at=now_epoch + 3600)},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    leases = [
        (
            await balancer.select_account(
                routing_strategy="usage_weighted",
                lease_kind="stream",
            )
        ).lease
        for _ in range(8)
    ]
    capped = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert capped.account is None
    assert capped.error_code == "account_stream_cap"
    assert capped.error_message == (
        "Account stream capacity is exhausted; per-account limit is 8. "
        "Increase the dashboard stream limit or wait for active streams to finish."
    )
    assert "all upstream accounts are unavailable" not in capped.error_message

    await balancer.release_account_lease(leases[0])
    recovered = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert recovered.account is not None
    assert recovered.account.id == account.id
    assert recovered.lease is not None


@pytest.mark.asyncio
async def test_account_stream_recovery_reserve_keeps_last_slot_for_reattach() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account = _make_account("acc-stream-recovery-reserve")
    accounts_repo = _StubAccountsRepository([account])
    usage_repo = _StubUsageRepository(
        primary={account.id: _usage_row(22, account.id, window="primary", reset_at=now_epoch + 300)},
        secondary={account.id: _usage_row(23, account.id, window="secondary", reset_at=now_epoch + 3600)},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    leases = [
        (
            await balancer.select_account(
                routing_strategy="usage_weighted",
                lease_kind="stream",
                stream_reserve_slots=1,
            )
        ).lease
        for _ in range(7)
    ]
    ordinary = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
        stream_reserve_slots=1,
    )
    recovery = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
        stream_reserve_slots=0,
    )

    assert ordinary.account is None
    assert ordinary.error_code == "account_stream_cap"
    assert recovery.account is not None
    assert recovery.account.id == account.id
    assert recovery.lease is not None

    for lease in [*leases, recovery.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_account_stream_recovery_reserve_keeps_ordinary_slot_when_cap_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(proxy_account_response_create_limit=64, proxy_account_stream_limit=1)
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)
    account = _make_account("acc-stream-recovery-reserve-cap-one")
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([account]),
            _StubUsageRepository(primary={}, secondary={}),
        )
    )

    ordinary = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
        stream_reserve_slots=1,
    )

    assert ordinary.account is not None
    assert ordinary.account.id == account.id
    await balancer.release_account_lease(ordinary.lease)


@pytest.mark.asyncio
async def test_account_response_create_cap_prefers_unsaturated_account() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-response-create-cap-a")
    account_b = _make_account("acc-response-create-cap-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row(30, account_a.id, window="primary", reset_at=now_epoch + 300),
            account_b.id: _usage_row(31, account_b.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={
            account_a.id: _usage_row(32, account_a.id, window="secondary", reset_at=now_epoch + 3600),
            account_b.id: _usage_row(33, account_b.id, window="secondary", reset_at=now_epoch + 3600),
        },
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    saturated_leases = [await balancer.acquire_account_lease(account_a.id, kind="response_create") for _ in range(4)]
    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="response_create",
    )

    assert selected.account is not None
    assert selected.account.id == account_b.id
    assert selected.lease is not None

    for lease in [*saturated_leases, selected.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_unbound_codex_session_sticky_filters_saturated_accounts() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-hard-sticky-unbound-capped-a")
    account_b = _make_account("acc-hard-sticky-unbound-capped-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row(34, account_a.id, window="primary", reset_at=now_epoch + 300),
            account_b.id: _usage_row(35, account_b.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={
            account_a.id: _usage_row(36, account_a.id, window="secondary", reset_at=now_epoch + 3600),
            account_b.id: _usage_row(37, account_b.id, window="secondary", reset_at=now_epoch + 3600),
        },
    )
    sticky_repo = _StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [await balancer.acquire_account_lease(account_a.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key="new-hard-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == account_b.id
    assert selected.error_code is None
    assert selected.lease is not None
    assert sticky_repo.account_id == account_b.id

    for lease in [*saturated_leases, selected.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_existing_codex_session_owner_is_not_displaced_by_due_probing_account() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-sticky-healthy-owner")
    probing = _make_account("acc-sticky-due-probe")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                90,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                91,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = healthy.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=load_balancer_module.HEALTH_TIER_PROBING,
        last_selected_at=0.0,
    )

    selected = await balancer.select_account(
        sticky_key="existing-healthy-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert sticky_repo.account_id == healthy.id
    assert sticky_repo.deleted == []
    assert balancer._runtime[probing.id].last_selected_at == 0.0


@pytest.mark.asyncio
async def test_released_sticky_probe_reservation_does_not_invalidate_force_probe() -> None:
    healthy = _make_account("acc-force-probe-sticky-owner")
    probing = _make_account("acc-force-probe-reservation-release")
    accounts_repo = _BlockingProbeAccountsRepository([healthy, probing])
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = healthy.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, _StubUsageRepository({}, {}), sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        probe_success_streak=0,
        version=11,
    )

    force_probe = asyncio.create_task(balancer.record_probe_result(account_id=probing.id, http_status=200))
    await accounts_repo.probe_snapshot_started.wait()

    selected = await balancer.select_account(
        sticky_key="force-probe-sticky-owner",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert balancer._runtime[probing.id].last_selected_at == 0.0
    assert balancer._runtime[probing.id].version == 11

    accounts_repo.release_probe_snapshot.set()
    await force_probe

    runtime = balancer._runtime[probing.id]
    assert runtime.probe_success_streak == 1
    assert runtime.version == 12


@pytest.mark.asyncio
async def test_probing_recovery_selection_updates_timestamp_and_restores_healthy_preference() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-recovery-healthy")
    probing = _make_account("acc-recovery-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                92,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                93,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
    )

    recovery = await balancer.select_account(routing_strategy="usage_weighted")
    normal = await balancer.select_account(routing_strategy="usage_weighted")

    assert recovery.account is not None
    assert recovery.account.id == probing.id
    assert balancer._runtime[probing.id].last_selected_at is not None
    assert normal.account is not None
    assert normal.account.id == healthy.id


@pytest.mark.asyncio
async def test_recent_probing_account_remains_selectable_when_pool_has_no_healthy_fallback() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    probing = _make_account("acc-recent-probing-only")
    accounts_repo = _StubAccountsRepository([probing])
    usage_repo = _StubUsageRepository(
        {
            probing.id: _usage_row_with_percent(
                94,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    recent_selection = time.time()
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=recent_selection,
        version=5,
    )

    selected = await balancer.select_account(routing_strategy="usage_weighted")

    assert selected.account is not None
    assert selected.account.id == probing.id
    last_selected_at = balancer._runtime[probing.id].last_selected_at
    assert last_selected_at is not None
    assert last_selected_at > recent_selection


@pytest.mark.asyncio
async def test_sticky_recent_probing_account_remains_selectable_when_pool_has_no_healthy_fallback() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    probing = _make_account("acc-sticky-recent-probing-only")
    sticky_repo = _StubStickySessionsRepository()
    accounts_repo = _StubAccountsRepository([probing])
    usage_repo = _StubUsageRepository(
        {
            probing.id: _usage_row_with_percent(
                95,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    recent_selection = time.time()
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=recent_selection,
        version=5,
    )

    selected = await balancer.select_account(
        sticky_key="recent-probing-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == probing.id
    assert sticky_repo.account_id == probing.id
    assert sticky_repo.deleted == []
    last_selected_at = balancer._runtime[probing.id].last_selected_at
    assert last_selected_at is not None
    assert last_selected_at > recent_selection


def test_probe_reservation_rejects_stale_last_selected_snapshot() -> None:
    healthy = _make_account("acc-stale-probe-healthy")
    probing = _make_account("acc-stale-probe-snapshot")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([healthy, probing]), _StubUsageRepository({}, {}))
    )
    current_last_selected_at = time.time()
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=current_last_selected_at,
        version=5,
    )
    stale_state = AccountState(
        account_id=probing.id,
        status=AccountStatus.ACTIVE,
        used_percent=10.0,
        reset_at=current_last_selected_at + 300,
        last_selected_at=0.0,
        health_tier=HEALTH_TIER_PROBING,
    )

    reservation = balancer._reserve_due_probe_locked(
        [
            AccountState(
                account_id=healthy.id,
                status=AccountStatus.ACTIVE,
                used_percent=30.0,
                reset_at=current_last_selected_at + 300,
                health_tier=HEALTH_TIER_HEALTHY,
            ),
            stale_state,
        ],
        prefer_earlier_reset=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
        relative_availability_power=2.0,
        relative_availability_top_k=5,
        traffic_class=load_balancer_module.TRAFFIC_CLASS_FOREGROUND,
        routing_costs_by_account_id=None,
    )

    assert reservation is None
    assert balancer._runtime[probing.id].last_selected_at == current_last_selected_at
    assert balancer._runtime[probing.id].version == 5


@pytest.mark.parametrize("routing_strategy", ["sequential_drain", "reset_drain", "single_account"])
def test_bypass_routing_strategies_do_not_require_probe_reservations(routing_strategy: str) -> None:
    states = [
        AccountState("healthy", AccountStatus.ACTIVE, health_tier=HEALTH_TIER_HEALTHY),
        AccountState("probing", AccountStatus.ACTIVE, health_tier=HEALTH_TIER_PROBING),
    ]

    assert not load_balancer_module._probing_result_requires_recovery_reservation(
        states,
        states[1],
        routing_strategy=routing_strategy,
        traffic_class=load_balancer_module.TRAFFIC_CLASS_FOREGROUND,
    )


def test_blocked_healthy_tier_peer_does_not_suppress_recovery_probe() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    blocked_healthy = AccountState(
        "blocked-healthy-tier",
        AccountStatus.RATE_LIMITED,
        used_percent=100.0,
        reset_at=now_epoch + 300,
        health_tier=HEALTH_TIER_HEALTHY,
    )
    due_probe = AccountState(
        "due-probe",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        reset_at=now_epoch + 300,
        last_selected_at=0.0,
        health_tier=HEALTH_TIER_PROBING,
    )
    states = [blocked_healthy, due_probe]

    assert (
        load_balancer_module._filter_recovery_probe_candidates(
            states,
            traffic_class=load_balancer_module.TRAFFIC_CLASS_FOREGROUND,
        )
        == states
    )
    assert not load_balancer_module._probing_result_requires_recovery_reservation(
        states,
        due_probe,
        routing_strategy="usage_weighted",
        traffic_class=load_balancer_module.TRAFFIC_CLASS_FOREGROUND,
    )


@pytest.mark.asyncio
async def test_unbound_probe_reservation_rolls_back_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-unbound-persist-fail-healthy")
    probing = _make_account("acc-unbound-persist-fail-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                140,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                141,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=17,
    )

    async def fail_persist(*args: Any, **kwargs: Any) -> set[str]:
        del args, kwargs
        raise RuntimeError("account state persistence unavailable")

    monkeypatch.setattr(balancer, "_persist_selection_state", fail_persist)

    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await balancer.select_account(routing_strategy="usage_weighted")

    assert balancer._runtime[probing.id].last_selected_at == 0.0
    assert balancer._runtime[probing.id].version == 17


@pytest.mark.asyncio
async def test_unbound_probe_lease_preserves_reservation_until_commit() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-unbound-lease-healthy")
    probing = _make_account("acc-unbound-lease-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                142,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                143,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=17,
        health_version=5,
    )

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == probing.id
    assert selected.lease is not None
    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 1
    assert probing_runtime.last_selected_at is not None
    assert probing_runtime.last_selected_at > 0.0
    assert probing_runtime.version == 18
    assert probing_runtime.health_version == 6

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_unbound_probe_reservation_survives_status_recovery_before_commit() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-unbound-status-recovery-healthy")
    probing = _make_account("acc-unbound-status-recovery-probing")
    probing.status = AccountStatus.RATE_LIMITED
    probing.reset_at = now_epoch - 1
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                145,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                146,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=17,
        health_version=5,
    )

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == probing.id
    assert selected.account.status == AccountStatus.ACTIVE
    assert selected.lease is not None
    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 1
    assert probing_runtime.last_selected_at is not None
    assert probing_runtime.last_selected_at > 0.0

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_unbound_probe_reservation_rolls_back_when_commit_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-unbound-cancel-healthy")
    probing = _make_account("acc-unbound-cancel-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                147,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                148,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=23,
        health_version=9,
    )

    def cancel_commit(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(balancer, "_commit_due_probe_reservation_locked", cancel_commit)

    with pytest.raises(asyncio.CancelledError):
        await balancer.select_account(
            routing_strategy="usage_weighted",
            lease_kind="stream",
        )

    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 0
    assert probing_runtime.last_selected_at == 0.0
    assert probing_runtime.health_version == 9


@pytest.mark.asyncio
async def test_unbound_recovery_probe_selects_when_no_healthy_peer() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    probing = _make_account("acc-unbound-probe-only")
    accounts_repo = _StubAccountsRepository([probing])
    usage_repo = _StubUsageRepository(
        primary={
            probing.id: _usage_row_with_percent(
                144,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=21,
        health_version=7,
    )

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == probing.id
    assert selected.lease is not None
    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 1
    assert probing_runtime.version == 23
    assert probing_runtime.health_version == 7

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_unbound_recovery_probe_falls_back_after_repeated_reservation_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-loss-healthy")
    probing = _make_account("acc-probe-loss-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                147,
                healthy.id,
                used_percent=80.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                148,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=31,
        health_version=7,
    )
    monkeypatch.setattr(balancer, "_reserve_due_probe_locked", lambda *args, **kwargs: None)

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.error_message is None
    assert selected.lease is not None

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_unbound_recovery_probe_falls_back_after_repeated_commit_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-commit-loss-healthy")
    probing = _make_account("acc-probe-commit-loss-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                151,
                healthy.id,
                used_percent=80.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                152,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=31,
        health_version=7,
    )
    monkeypatch.setattr(balancer, "_commit_due_probe_reservation_locked", lambda *args, **kwargs: False)

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.error_message is None
    assert selected.lease is not None
    assert balancer._runtime[probing.id].last_selected_at == 0.0

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_unbound_recovery_probe_selects_with_only_draining_peer() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    probing = _make_account("acc-unbound-probe-draining-probe")
    draining = _make_account("acc-unbound-probe-draining-peer")
    accounts_repo = _StubAccountsRepository([probing, draining])
    usage_repo = _StubUsageRepository(
        primary={
            probing.id: _usage_row_with_percent(
                149,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
            draining.id: _usage_row_with_percent(
                150,
                draining.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=time.time(),
        version=21,
        health_version=7,
    )
    balancer._runtime[draining.id] = RuntimeState(
        health_tier=HEALTH_TIER_DRAINING,
        last_selected_at=0.0,
        version=3,
        health_version=1,
    )

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == probing.id
    assert selected.lease is not None

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_concurrent_unbound_stickies_reserve_one_due_probe() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-concurrent-recovery-healthy")
    probing = _make_account("acc-concurrent-recovery-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                94,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                95,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _ConcurrentUnboundStickySessionsRepository(expected_lookups=2)
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
    )

    first, second = await asyncio.gather(
        balancer.select_account(
            sticky_key="concurrent-unbound-a",
            sticky_kind=StickySessionKind.CODEX_SESSION,
            routing_strategy="usage_weighted",
        ),
        balancer.select_account(
            sticky_key="concurrent-unbound-b",
            sticky_kind=StickySessionKind.CODEX_SESSION,
            routing_strategy="usage_weighted",
        ),
    )

    selected_ids = {selection.account.id for selection in (first, second) if selection.account is not None}
    assert selected_ids == {healthy.id, probing.id}
    assert [account_id for _, account_id, _ in sticky_repo.upserts].count(probing.id) == 1


@pytest.mark.asyncio
async def test_hard_sticky_owner_does_not_fallback_to_available_account() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-capped-fallback-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    probing = _make_account("acc-capped-fallback-probing")
    healthy = _make_account("acc-capped-fallback-healthy")
    accounts_repo = _StubAccountsRepository([unavailable_owner, probing, healthy])
    usage_repo = _StubUsageRepository(
        primary={
            probing.id: _usage_row_with_percent(
                96,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
            healthy.id: _usage_row_with_percent(
                97,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [await balancer.acquire_account_lease(probing.id, kind="stream") for _ in range(8)]
    balancer._runtime[probing.id].health_tier = HEALTH_TIER_PROBING
    balancer._runtime[probing.id].last_selected_at = 0.0

    selected = await balancer.select_account(
        sticky_key="capped-fallback-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.deleted == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_hard_sticky_owner_failure_takes_precedence_over_fallback_cap() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-capped-only-fallback-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    probing = _make_account("acc-capped-only-fallback-probing")
    unavailable_fallback = _make_account("acc-capped-only-fallback-rate-limited")
    unavailable_fallback.status = AccountStatus.RATE_LIMITED
    unavailable_fallback.reset_at = now_epoch + 3600
    accounts_repo = _StubAccountsRepository([unavailable_owner, probing, unavailable_fallback])
    usage_repo = _StubUsageRepository(
        primary={
            probing.id: _usage_row_with_percent(
                100,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [await balancer.acquire_account_lease(probing.id, kind="stream") for _ in range(8)]
    balancer._runtime[probing.id].health_tier = HEALTH_TIER_PROBING
    balancer._runtime[probing.id].last_selected_at = 0.0

    selected = await balancer.select_account(
        sticky_key="capped-only-fallback-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.deleted == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_concurrent_sticky_fallbacks_reserve_one_due_probe() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-concurrent-fallback-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    healthy = _make_account("acc-concurrent-fallback-healthy")
    probing = _make_account("acc-concurrent-fallback-probing")
    accounts_repo = _StubAccountsRepository([unavailable_owner, healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                98,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                99,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _ConcurrentBoundStickySessionsRepository(
        account_id=unavailable_owner.id,
        expected_lookups=2,
    )
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
    )

    first, second = await asyncio.gather(
        balancer.select_account(
            sticky_key="concurrent-fallback-a",
            sticky_kind=StickySessionKind.STICKY_THREAD,
            routing_strategy="usage_weighted",
        ),
        balancer.select_account(
            sticky_key="concurrent-fallback-b",
            sticky_kind=StickySessionKind.STICKY_THREAD,
            routing_strategy="usage_weighted",
        ),
    )

    selected_ids = {selection.account.id for selection in (first, second) if selection.account is not None}
    assert selected_ids == {healthy.id, probing.id}
    assert [account_id for _, account_id, _ in sticky_repo.upserts].count(probing.id) == 1


@pytest.mark.asyncio
async def test_sticky_probe_reservation_rolls_back_when_final_lease_check_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-cap-race-healthy")
    probing = _make_account("acc-probe-cap-race-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                101,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                102,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=17,
    )
    caps = load_balancer_module.AccountConcurrencyCaps(response_create_limit=1, stream_limit=1)
    original_sticky_selection = balancer._select_with_stickiness
    sticky_selection_ready = asyncio.Event()
    release_sticky_selection = asyncio.Event()

    async def blocking_sticky_selection(*args: Any, **kwargs: Any) -> Any:
        outcome = await original_sticky_selection(*args, **kwargs)
        sticky_selection_ready.set()
        await release_sticky_selection.wait()
        return outcome

    monkeypatch.setattr(balancer, "_select_with_stickiness", blocking_sticky_selection)

    selection_task = asyncio.create_task(
        balancer.select_account(
            sticky_key="probe-cap-race",
            sticky_kind=StickySessionKind.PROMPT_CACHE,
            routing_strategy="usage_weighted",
            lease_kind="stream",
            concurrency_caps=caps,
        )
    )
    await sticky_selection_ready.wait()
    # Model a cap counter that changes after the state snapshot without
    # replacing this request's provisional last_selected_at token.
    async with balancer._runtime_lock:
        balancer._runtime[probing.id].inflight_streams = 1
    release_sticky_selection.set()

    selected = await selection_task

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.lease is not None
    assert balancer._runtime[probing.id].last_selected_at == 0.0
    assert balancer._runtime[probing.id].version == 17

    async with balancer._runtime_lock:
        balancer._runtime[probing.id].inflight_streams = 0
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_sticky_probe_reservation_retries_when_health_changes_during_sticky_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-cas-healthy")
    probing = _make_account("acc-probe-cas-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                103,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                104,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=23,
    )
    original_sticky_selection = balancer._select_with_stickiness
    sticky_selection_ready = asyncio.Event()
    release_sticky_selection = asyncio.Event()

    async def blocking_sticky_selection(*args: Any, **kwargs: Any) -> Any:
        outcome = await original_sticky_selection(*args, **kwargs)
        sticky_selection_ready.set()
        await release_sticky_selection.wait()
        return outcome

    monkeypatch.setattr(balancer, "_select_with_stickiness", blocking_sticky_selection)

    selection_task = asyncio.create_task(
        balancer.select_account(
            sticky_key="probe-health-cas",
            sticky_kind=StickySessionKind.PROMPT_CACHE,
            sticky_max_age_seconds=600,
            routing_strategy="usage_weighted",
        )
    )
    await sticky_selection_ready.wait()
    async with balancer._runtime_lock:
        runtime = balancer._runtime[probing.id]
        # Model a newer health observation while the reservation owner is doing
        # sticky I/O. Its health version is the CAS boundary; the stale PROBING
        # state must neither be returned nor published as affinity.
        runtime.health_tier = HEALTH_TIER_DRAINING
        runtime.version += 1
        runtime.health_version += 1
    release_sticky_selection.set()

    selected = await selection_task

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert sticky_repo.account_id == healthy.id
    assert all(account_id != probing.id for _, account_id, _ in sticky_repo.upserts)
    assert balancer._runtime[probing.id].last_selected_at == 0.0
    assert balancer._runtime[probing.id].version == 24


@pytest.mark.asyncio
async def test_sticky_probe_reservation_restores_affinity_after_repeated_commit_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-probe-sticky-commit-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    healthy = _make_account("acc-probe-sticky-commit-healthy")
    probing = _make_account("acc-probe-sticky-commit-probing")
    accounts_repo = _StubAccountsRepository([unavailable_owner, healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                153,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                154,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=23,
        health_version=9,
    )
    monkeypatch.setattr(balancer, "_commit_due_probe_reservation_locked", lambda *args, **kwargs: False)

    selected = await balancer.select_account(
        sticky_key="probe-sticky-commit-loss",
        sticky_kind=StickySessionKind.STICKY_THREAD,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.lease is not None
    assert sticky_repo.account_id == healthy.id
    assert balancer._runtime[probing.id].last_selected_at == 0.0
    assert all(account_id != probing.id for _, account_id, _ in sticky_repo.upserts[-1:])

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_sticky_probe_reservation_restore_does_not_clobber_newer_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-probe-sticky-newer-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    healthy = _make_account("acc-probe-sticky-newer-healthy")
    probing = _make_account("acc-probe-sticky-newer-probing")
    newer_owner = _make_account("acc-probe-sticky-newer-current")
    accounts_repo = _StubAccountsRepository([unavailable_owner, healthy, probing, newer_owner])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                171,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                172,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=23,
        health_version=9,
    )

    def lose_commit_after_new_owner(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        sticky_repo.account_id = newer_owner.id
        return False

    monkeypatch.setattr(balancer, "_commit_due_probe_reservation_locked", lose_commit_after_new_owner)

    selected = await balancer.select_account(
        sticky_key="probe-sticky-newer-owner",
        sticky_kind=StickySessionKind.STICKY_THREAD,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == newer_owner.id
    assert selected.lease is not None
    assert sticky_repo.account_id == newer_owner.id
    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 0
    assert probing_runtime.last_selected_at == 0.0

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_sticky_probe_reservation_rechecks_health_after_state_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-post-persist-healthy")
    probing = _make_account("acc-probe-post-persist-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                107,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                108,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=31,
    )
    original_persist = balancer._persist_selection_state
    persist_started = asyncio.Event()
    release_persist = asyncio.Event()
    block_first_persist = True

    async def blocking_persist(*args: Any, **kwargs: Any) -> set[str]:
        nonlocal block_first_persist
        if block_first_persist:
            block_first_persist = False
            persist_started.set()
            await release_persist.wait()
        return await original_persist(*args, **kwargs)

    monkeypatch.setattr(balancer, "_persist_selection_state", blocking_persist)
    selection_task = asyncio.create_task(
        balancer.select_account(
            sticky_key="probe-post-persist-cas",
            sticky_kind=StickySessionKind.PROMPT_CACHE,
            sticky_max_age_seconds=600,
            routing_strategy="usage_weighted",
            lease_kind="stream",
        )
    )
    await persist_started.wait()
    async with balancer._runtime_lock:
        runtime = balancer._runtime[probing.id]
        assert runtime.inflight_streams == 1
        runtime.health_tier = HEALTH_TIER_DRAINING
        runtime.version += 1
        runtime.health_version += 1
    release_persist.set()

    selected = await selection_task

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.lease is not None
    assert sticky_repo.account_id == healthy.id
    assert sticky_repo.upserts[-1] == (
        "probe-post-persist-cas",
        healthy.id,
        StickySessionKind.PROMPT_CACHE,
    )
    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 0
    assert probing_runtime.last_selected_at == 0.0
    assert probing_runtime.version == 33

    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_sticky_probe_reservation_does_not_leak_affinity_when_persistence_fails() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-probe-affinity-fail-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    healthy = _make_account("acc-probe-affinity-fail-healthy")
    probing = _make_account("acc-probe-affinity-fail-probing")
    accounts_repo = _StubAccountsRepository([unavailable_owner, healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                145,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                146,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _FailingUpsertStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=41,
        health_version=11,
    )

    with pytest.raises(RuntimeError, match="sticky persistence unavailable"):
        await balancer.select_account(
            sticky_key="probe-affinity-fail",
            sticky_kind=StickySessionKind.STICKY_THREAD,
            routing_strategy="usage_weighted",
            lease_kind="stream",
        )

    probing_runtime = balancer._runtime[probing.id]
    assert probing_runtime.inflight_streams == 0
    assert probing_runtime.last_selected_at == 0.0
    assert probing_runtime.health_version == 11
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []


@pytest.mark.asyncio
async def test_opportunistic_hard_sticky_owner_fails_closed_before_fallback_caps() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-opportunistic-cap-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    fallback_a = _make_account("acc-opportunistic-cap-a")
    fallback_b = _make_account("acc-opportunistic-cap-b")
    accounts_repo = _StubAccountsRepository([unavailable_owner, fallback_a, fallback_b])
    usage_repo = _StubUsageRepository(
        primary={
            fallback_a.id: _usage_row_with_percent(
                105,
                fallback_a.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
            fallback_b.id: _usage_row_with_percent(
                106,
                fallback_b.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [
        *[await balancer.acquire_account_lease(fallback_a.id, kind="stream") for _ in range(8)],
        *[await balancer.acquire_account_lease(fallback_b.id, kind="stream") for _ in range(8)],
    ]

    selected = await balancer.select_account(
        sticky_key="opportunistic-cap-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        traffic_class="opportunistic",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.deleted == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_hard_sticky_owner_does_not_select_under_cap_backoff_fallback() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-cap-backoff-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    saturated_fallback = _make_account("acc-cap-backoff-saturated")
    backoff_fallback = _make_account("acc-cap-backoff-cooling")
    accounts_repo = _StubAccountsRepository([unavailable_owner, saturated_fallback, backoff_fallback])
    usage_repo = _StubUsageRepository(
        primary={
            saturated_fallback.id: _usage_row_with_percent(
                109,
                saturated_fallback.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
            backoff_fallback.id: _usage_row_with_percent(
                110,
                backoff_fallback.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[backoff_fallback.id] = RuntimeState(
        error_count=3,
        last_error_at=time.time(),
    )
    saturated_leases = [await balancer.acquire_account_lease(saturated_fallback.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key="cap-backoff-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert balancer._runtime[backoff_fallback.id].inflight_streams == 0
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.deleted == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_hard_sticky_owner_failure_discards_budget_reallocation_delete() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-cap-delete-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    unavailable_owner.reset_at = now_epoch + 3600
    saturated_fallback = _make_account("acc-cap-delete-saturated")
    accounts_repo = _StubAccountsRepository([unavailable_owner, saturated_fallback])
    usage_repo = _StubUsageRepository(
        primary={
            unavailable_owner.id: _usage_row_with_percent(
                111,
                unavailable_owner.id,
                used_percent=100.0,
                reset_at=now_epoch + 3600,
            ),
            saturated_fallback.id: _usage_row_with_percent(
                112,
                saturated_fallback.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = unavailable_owner.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [await balancer.acquire_account_lease(saturated_fallback.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key="cap-delete-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == unavailable_owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.deleted == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_bound_codex_session_sticky_fails_closed_when_pinned_account_is_saturated() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-hard-sticky-bound-capped-a")
    account_b = _make_account("acc-hard-sticky-bound-capped-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row(38, account_a.id, window="primary", reset_at=now_epoch + 300),
            account_b.id: _usage_row(39, account_b.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={
            account_a.id: _usage_row(42, account_a.id, window="secondary", reset_at=now_epoch + 3600),
            account_b.id: _usage_row(43, account_b.id, window="secondary", reset_at=now_epoch + 3600),
        },
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = account_a.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    saturated_leases = [await balancer.acquire_account_lease(account_a.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key="existing-hard-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert selected.error_message is not None
    assert "Account stream capacity is exhausted" in selected.error_message
    assert sticky_repo.account_id == account_a.id

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
def _make_cap_spillover_balancer(
    prefix: str,
    *,
    include_alternate: bool = True,
) -> tuple[LoadBalancer, Account, Account | None, _StubStickySessionsRepository]:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    owner = _make_account(f"{prefix}-owner")
    alternate = _make_account(f"{prefix}-alternate") if include_alternate else None
    accounts = [owner, *([alternate] if alternate is not None else [])]
    usage_rows = {
        account.id: _usage_row(index + 100, account.id, window="primary", reset_at=now_epoch + 300)
        for index, account in enumerate(accounts)
    }
    secondary_rows = {
        account.id: _usage_row(index + 200, account.id, window="secondary", reset_at=now_epoch + 3600)
        for index, account in enumerate(accounts)
    }
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = owner.id
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository(accounts),
            _StubUsageRepository(usage_rows, secondary_rows),
            sticky_repo,
        )
    )
    return balancer, owner, alternate, sticky_repo


@pytest.mark.asyncio
@pytest.mark.parametrize(("lease_kind", "cap"), [("stream", 8), ("response_create", 4)])
async def test_bare_codex_session_spills_without_rebinding_when_owner_reaches_account_cap(
    lease_kind: Literal["stream", "response_create"],
    cap: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=load_balancer_module.__name__)
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer(f"cap-spill-{lease_kind}")
    assert alternate is not None
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind=lease_kind) for _ in range(cap)]
    raw_session = "bare-session-must-not-appear-in-log"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        routing_strategy="usage_weighted",
        lease_kind=lease_kind,
    )

    assert selected.account is not None
    assert selected.account.id == alternate.id
    assert selected.lease is not None
    assert sticky_repo.account_id == owner.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []
    assert "internal_soft_affinity_spillover" in caplog.text
    assert raw_session not in caplog.text

    for lease in [*saturated_leases, selected.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_kind", ["stream", "response_create"])
async def test_bare_codex_session_keeps_unsaturated_owner(
    lease_kind: Literal["stream", "response_create"],
) -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer(f"cap-sticky-{lease_kind}")
    raw_session = "bare-session-sticky"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        routing_strategy="usage_weighted",
        lease_kind=lease_kind,
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.account_id == owner.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_bare_codex_stream_avoids_owner_at_response_create_cap() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("cap-second-stage")
    assert alternate is not None
    create_leases = [await balancer.acquire_account_lease(owner.id, kind="response_create") for _ in range(4)]
    raw_session = "bare-session-second-stage"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == alternate.id
    assert sticky_repo.account_id == owner.id

    for lease in [*create_leases, selected.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lease_kind", "cap", "error_code"),
    [
        ("stream", 8, "account_stream_cap"),
        ("response_create", 4, "account_response_create_cap"),
    ],
)
async def test_bare_codex_session_preserves_mapping_when_no_alternate_is_below_cap(
    lease_kind: Literal["stream", "response_create"],
    cap: int,
    error_code: str,
) -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer(
        f"cap-no-alternate-{lease_kind}",
        include_alternate=False,
    )
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind=lease_kind) for _ in range(cap)]
    raw_session = "bare-session-no-alternate"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        routing_strategy="usage_weighted",
        lease_kind=lease_kind,
    )

    assert selected.account is None
    assert selected.error_code == error_code
    assert sticky_repo.account_id == owner.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_raw_codex_session_key_cannot_activate_cap_spillover() -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer("cap-raw-key")
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key="legacy-or-owner-bearing-key",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        spill_bare_session_on_account_cap=True,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert sticky_repo.account_id == owner.id

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_turn_state_that_looks_namespaced_remains_hard() -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer("cap-crafted-turn-state")
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key("crafted-turn-state"),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="turn_state",
        spill_bare_session_on_account_cap=True,
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert sticky_repo.account_id == owner.id

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_legacy_raw_session_mapping_remains_hard_during_upgrade() -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer("cap-legacy-session")
    raw_session = "legacy-bare-session"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo.account_ids_by_key = {raw_session: owner.id}
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert sticky_repo.account_ids_by_key == {raw_session: owner.id}
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_legacy_raw_session_mapping_wins_when_namespaced_row_also_exists() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("cap-legacy-coexist")
    assert alternate is not None
    raw_session = "legacy-coexisting-session"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo.account_ids_by_key = {
        selection_key: alternate.id,
        raw_session: owner.id,
    }
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind="stream") for _ in range(8)]

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=True,
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert sticky_repo.account_ids_by_key == {
        selection_key: alternate.id,
        raw_session: owner.id,
    }
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_legacy_raw_owner_conflict_blocks_resolved_preferred_owner() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("legacy-preferred-conflict")
    assert alternate is not None
    raw_session = "legacy-preferred-session"
    sticky_repo.account_ids_by_key = {raw_session: owner.id}

    selected = await balancer.select_account(
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        required_account_id=alternate.id,
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "continuity_owner_conflict"
    assert sticky_repo.account_ids_by_key == {raw_session: owner.id}
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []


@pytest.mark.asyncio
async def test_bare_session_mapping_does_not_prove_ambiguous_conversation_owner() -> None:
    balancer, owner, _, sticky_repo = _make_cap_spillover_balancer("conversation-ambiguous")
    raw_session = "conversation-session"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_conversation_owner_stays_ambiguous_when_one_account_is_capped() -> None:
    balancer, owner, _, _ = _make_cap_spillover_balancer("conversation-capped-candidate")
    saturated_leases = [await balancer.acquire_account_lease(owner.id, kind="response_create") for _ in range(4)]

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"

    for lease in saturated_leases:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_conversation_owner_stays_ambiguous_when_one_account_is_excluded() -> None:
    balancer, owner, _, _ = _make_cap_spillover_balancer("conversation-excluded-candidate")

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        exclude_account_ids={owner.id},
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_preferred_file_owner_does_not_narrow_conversation_ambiguity_pool() -> None:
    balancer, _owner, alternate, _ = _make_cap_spillover_balancer("conversation-file-owner")
    assert alternate is not None

    selected = await balancer.select_account(
        required_account_id=alternate.id,
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_unavailable_account_still_counts_toward_conversation_ambiguity() -> None:
    balancer, owner, _alternate, _ = _make_cap_spillover_balancer("conversation-paused-owner")
    owner.status = AccountStatus.PAUSED

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_conversation_owner_ambiguity_uses_prequota_candidate_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balancer, owner, alternate, _ = _make_cap_spillover_balancer("conversation-prequota-candidates")
    assert alternate is not None
    monkeypatch.setattr(
        balancer,
        "_load_selection_inputs",
        AsyncMock(
            return_value=load_balancer_module.SelectionInputs(
                accounts=[owner],
                continuity_owner_candidates=[owner, alternate],
                latest_primary={},
                latest_secondary={},
                latest_monthly={},
            )
        ),
    )

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_additional_quota_error_cannot_hide_ambiguous_conversation_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balancer, owner, alternate, _ = _make_cap_spillover_balancer("conversation-empty-quota-pool")
    assert alternate is not None
    monkeypatch.setattr(
        balancer,
        "_load_selection_inputs",
        AsyncMock(
            return_value=load_balancer_module.SelectionInputs(
                accounts=[],
                continuity_owner_candidates=[owner, alternate],
                latest_primary={},
                latest_secondary={},
                latest_monthly={},
                error_message="No accounts have the requested additional quota",
                error_code="additional_quota_unavailable",
            )
        ),
    )

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        additional_limit_name="codex_other_models",
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_security_scope_filters_ownership_candidates_even_when_routing_pool_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balancer, authorized, unauthorized, _ = _make_cap_spillover_balancer("conversation-empty-security-pool")
    assert unauthorized is not None
    authorized.security_work_authorized = True
    unauthorized.security_work_authorized = False
    monkeypatch.setattr(
        balancer,
        "_load_selection_inputs",
        AsyncMock(
            return_value=load_balancer_module.SelectionInputs(
                accounts=[],
                continuity_owner_candidates=[authorized, unauthorized],
                latest_primary={},
                latest_secondary={},
                latest_monthly={},
                error_message="No accounts have the requested additional quota",
                error_code="additional_quota_unavailable",
            )
        ),
    )

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        require_security_work_authorized=True,
        lease_kind="response_create",
    )

    # Security authorization is part of the ownership scope. Once it leaves
    # one possible owner, the original routing error—not false ambiguity—wins.
    assert selected.account is None
    assert selected.error_code == "additional_quota_unavailable"


@pytest.mark.asyncio
async def test_unresolved_conversation_allows_only_eligible_account() -> None:
    balancer, owner, _, _ = _make_cap_spillover_balancer(
        "conversation-single-account",
        include_alternate=False,
    )

    selected = await balancer.select_account(
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_mode", ["excluded", "api_key_scope"])
async def test_hard_codex_session_owner_outside_selection_pool_fails_closed(scope_mode: str) -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer(f"hard-owner-{scope_mode}")
    assert alternate is not None
    if scope_mode == "excluded":
        selected = await balancer.select_account(
            sticky_key="hard-owner-selection",
            sticky_kind=StickySessionKind.CODEX_SESSION,
            lease_kind="stream",
            exclude_account_ids={owner.id},
        )
    else:
        selected = await balancer.select_account(
            sticky_key="hard-owner-selection",
            sticky_kind=StickySessionKind.CODEX_SESSION,
            lease_kind="stream",
            account_ids={alternate.id},
        )

    assert selected.account is None
    assert selected.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == owner.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []


@pytest.mark.asyncio
async def test_hard_codex_session_sticky_does_not_reallocate_under_budget_pressure() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-hard-sticky-a")
    account_b = _make_account("acc-hard-sticky-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row_with_percent(
                40,
                account_a.id,
                used_percent=99.0,
                reset_at=now_epoch + 300,
            ),
            account_b.id: _usage_row_with_percent(
                41,
                account_b.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = account_a.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    result = await balancer.select_account(
        sticky_key="hard-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert result.account is not None
    assert result.account.id == account_a.id
    assert sticky_repo.deleted == []
    assert sticky_repo.account_id == account_a.id
    await balancer.release_account_lease(result.lease)


@pytest.mark.asyncio
async def test_force_probe_success_does_not_clear_newer_runtime_error() -> None:
    account = _make_account("acc-force-probe-stale-success")
    accounts_repo = _BlockingProbeAccountsRepository([account])
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, _StubUsageRepository({}, {})))
    prior_error_at = time.time() - 120.0
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        error_count=2,
        last_error_at=prior_error_at,
        probe_success_streak=2,
    )

    probe_task = asyncio.create_task(
        balancer.record_probe_result(
            account_id=account.id,
            http_status=200,
        )
    )
    await accounts_repo.probe_snapshot_started.wait()
    await balancer.record_error(account)
    accounts_repo.release_probe_snapshot.set()
    await probe_task

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.error_count == 3
    assert runtime.last_error_at is not None
    assert runtime.last_error_at > prior_error_at
    assert runtime.probe_success_streak == 0


@pytest.mark.asyncio
async def test_force_probe_success_survives_lease_only_version_bumps() -> None:
    account = _make_account("acc-force-probe-lease-version")
    accounts_repo = _BlockingProbeAccountsRepository([account])
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        probe_success_streak=0,
        version=40,
        health_version=7,
    )

    probe_task = asyncio.create_task(
        balancer.record_probe_result(
            account_id=account.id,
            http_status=200,
        )
    )
    await accounts_repo.probe_snapshot_started.wait()
    lease = await balancer.acquire_account_lease(account.id, kind="stream")
    await balancer.release_account_lease(lease)
    accounts_repo.release_probe_snapshot.set()
    await probe_task

    runtime = balancer._runtime[account.id]
    assert runtime.probe_success_streak == 1
    assert runtime.version == 43
    assert runtime.health_version == 8


@pytest.mark.asyncio
async def test_force_probe_success_clears_stale_errors_before_tier_check() -> None:
    account = _make_account("acc-force-probe-stale-errors")
    accounts_repo = _StubAccountsRepository([account])
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, _StubUsageRepository({}, {})))
    prior_error_at = time.time() - 120.0
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        error_count=2,
        last_error_at=prior_error_at,
        probe_success_streak=0,
        version=40,
        health_version=7,
    )

    await balancer.record_probe_result(account_id=account.id, http_status=200)

    runtime = balancer._runtime[account.id]
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.error_count == 0
    assert runtime.last_error_at is None
    assert runtime.probe_success_streak == 1


@pytest.mark.asyncio
async def test_force_probe_success_loses_to_committed_probe_admission() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-force-probe-routing-healthy")
    account = _make_account("acc-force-probe-routing-admission")
    accounts_repo = _BlockingProbeAccountsRepository([account])
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, _StubUsageRepository({}, {})))
    balancer._runtime[account.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        probe_success_streak=0,
        version=50,
        health_version=12,
    )

    probe_task = asyncio.create_task(
        balancer.record_probe_result(
            account_id=account.id,
            http_status=200,
        )
    )
    await accounts_repo.probe_snapshot_started.wait()

    reservation = balancer._reserve_due_probe_locked(
        [
            AccountState(
                account_id=healthy.id,
                status=AccountStatus.ACTIVE,
                used_percent=30.0,
                reset_at=now_epoch + 300,
                health_tier=HEALTH_TIER_HEALTHY,
            ),
            AccountState(
                account_id=account.id,
                status=AccountStatus.ACTIVE,
                used_percent=10.0,
                reset_at=now_epoch + 300,
                last_selected_at=0.0,
                health_tier=HEALTH_TIER_PROBING,
            ),
        ],
        prefer_earlier_reset=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
        relative_availability_power=2.0,
        relative_availability_top_k=5,
        traffic_class=load_balancer_module.TRAFFIC_CLASS_FOREGROUND,
        routing_costs_by_account_id=None,
    )

    assert reservation is not None
    assert balancer._commit_due_probe_reservation_locked(reservation)
    assert balancer._runtime[account.id].health_version == 13

    accounts_repo.release_probe_snapshot.set()
    await probe_task

    runtime = balancer._runtime[account.id]
    assert runtime.probe_success_streak == 0
    assert runtime.health_tier == HEALTH_TIER_PROBING
    assert runtime.health_version == 13


@pytest.mark.asyncio
async def test_unusable_hard_codex_session_does_not_delete_mapping_under_budget_pressure() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    account_a = _make_account("acc-hard-unusable-a")
    account_a.status = AccountStatus.QUOTA_EXCEEDED
    account_b = _make_account("acc-hard-unusable-b")
    accounts_repo = _StubAccountsRepository([account_a, account_b])
    usage_repo = _StubUsageRepository(
        primary={
            account_a.id: _usage_row_with_percent(
                44,
                account_a.id,
                used_percent=100.0,
                reset_at=now_epoch + 300,
            ),
            account_b.id: _usage_row_with_percent(
                45,
                account_b.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_id = account_a.id
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    result = await balancer.select_account(
        sticky_key="hard-unusable-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert result.account is None
    assert result.error_code == "hard_affinity_saturated"
    assert sticky_repo.account_id == account_a.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []


def test_effective_account_concurrency_caps_partitions_across_replicas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        load_balancer_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=8,
            proxy_account_caps_scope="partitioned",
        ),
    )
    monkeypatch.setattr(
        load_balancer_module,
        "get_cap_partition",
        lambda: CapPartition(replica_count=2, rank=0),
    )

    assert effective_account_concurrency_caps() == load_balancer_module.AccountConcurrencyCaps(
        response_create_limit=2,
        stream_limit=4,
        configured_response_create_limit=4,
        configured_stream_limit=8,
        replica_count=2,
    )


def test_effective_account_concurrency_caps_replica_scope_restores_full_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        load_balancer_module,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_account_response_create_limit=4,
            proxy_account_stream_limit=8,
            proxy_account_caps_scope="replica",
        ),
    )
    monkeypatch.setattr(
        load_balancer_module,
        "get_cap_partition",
        lambda: CapPartition(replica_count=2, rank=0),
    )

    assert effective_account_concurrency_caps() == load_balancer_module.AccountConcurrencyCaps(
        response_create_limit=4,
        stream_limit=8,
    )


def test_account_cap_error_message_states_replica_share() -> None:
    caps = load_balancer_module.AccountConcurrencyCaps(
        response_create_limit=2,
        stream_limit=4,
        configured_response_create_limit=4,
        configured_stream_limit=8,
        replica_count=2,
    )

    stream_message = load_balancer_module._account_cap_error_message("stream", caps)
    assert "this replica's share is 4" in stream_message
    assert "per-account limit 8" in stream_message
    assert "across 2 replicas" in stream_message

    create_message = load_balancer_module._account_cap_error_message("response_create", caps)
    assert "this replica's share is 2" in create_message
    assert "per-account limit 4" in create_message
    assert "across 2 replicas" in create_message


@pytest.mark.asyncio
async def test_partitioned_caps_bound_aggregate_streams_across_two_replicas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two replicas over one account pool admit at most the configured cluster cap.

    Before cap partitioning each replica enforced the full configured stream cap
    against its own in-process counters, so two replicas admitted 16 streams for
    a cluster-wide cap of 8.
    """
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    admitted: dict[str, int] = {}
    last_error: dict[str, tuple[str | None, str | None]] = {}

    for rank, replica in enumerate(["replica-a", "replica-b"]):
        account = _make_account("acc-cluster-cap")
        accounts_repo = _StubAccountsRepository([account])
        usage_repo = _StubUsageRepository(
            primary={account.id: _usage_row(50, account.id, window="primary", reset_at=now_epoch + 300)},
            secondary={account.id: _usage_row(51, account.id, window="secondary", reset_at=now_epoch + 3600)},
        )
        balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))
        monkeypatch.setattr(
            load_balancer_module,
            "get_cap_partition",
            lambda rank=rank: CapPartition(replica_count=2, rank=rank),
        )
        admitted[replica] = 0
        for _ in range(16):
            result = await balancer.select_account(
                routing_strategy="usage_weighted",
                lease_kind="stream",
            )
            if result.account is None:
                last_error[replica] = (result.error_code, result.error_message)
                break
            admitted[replica] += 1

    assert admitted == {"replica-a": 4, "replica-b": 4}
    assert sum(admitted.values()) == 8
    for error_code, error_message in last_error.values():
        assert error_code == "account_stream_cap"
        assert error_message is not None
        assert "this replica's share is 4" in error_message
        assert "across 2 replicas" in error_message
