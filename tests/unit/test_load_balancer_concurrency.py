from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

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
)
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
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
        self.deleted: list[tuple[str, StickySessionKind | None]] = []
        self.upserts: list[tuple[str, str, StickySessionKind | None]] = []

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
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
async def test_hard_sticky_fallback_excludes_saturated_due_probe() -> None:
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

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.error_code is None
    assert selected.lease is not None
    assert sticky_repo.account_id == healthy.id

    for lease in [*saturated_leases, selected.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_unavailable_hard_sticky_owner_with_saturated_fallback_returns_cap_reason() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    unavailable_owner = _make_account("acc-capped-only-fallback-owner")
    unavailable_owner.status = AccountStatus.RATE_LIMITED
    unavailable_owner.reset_at = now_epoch + 3600
    probing = _make_account("acc-capped-only-fallback-probing")
    accounts_repo = _StubAccountsRepository([unavailable_owner, probing])
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
    assert selected.error_code == "account_stream_cap"
    assert selected.error_message is not None
    assert "Account stream capacity is exhausted" in selected.error_message
    assert sticky_repo.account_id == unavailable_owner.id

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
            sticky_kind=StickySessionKind.CODEX_SESSION,
            routing_strategy="usage_weighted",
        ),
        balancer.select_account(
            sticky_key="concurrent-fallback-b",
            sticky_kind=StickySessionKind.CODEX_SESSION,
            routing_strategy="usage_weighted",
        ),
    )

    selected_ids = {selection.account.id for selection in (first, second) if selection.account is not None}
    assert selected_ids == {healthy.id, probing.id}
    assert [account_id for _, account_id, _ in sticky_repo.upserts].count(probing.id) == 1


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
async def test_codex_session_sticky_reallocates_under_budget_pressure() -> None:
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
    assert result.account.id == account_b.id
    assert sticky_repo.deleted == [("hard-session", StickySessionKind.CODEX_SESSION)]
    assert sticky_repo.account_id == account_b.id
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
