from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.core.balancer import (
    ERROR_BACKOFF_THRESHOLD,
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
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _codex_backend_identity,
    _codex_session_selection_key,
)
from app.modules.proxy.cap_partitioning import CapPartition
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState, effective_account_concurrency_caps
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickyOwnerLookup
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


@pytest.mark.asyncio
async def test_opportunistic_selection_preserves_usage_limit_exhaustion_error() -> None:
    account = _make_account("acc-opportunistic-usage-exhausted")
    account.status = AccountStatus.QUOTA_EXCEEDED
    reset_at = int(time.time() + 300)
    account.reset_at = reset_at
    usage_repo = _StubUsageRepository(
        {account.id: _usage_row_with_percent(1, account.id, used_percent=100.0, reset_at=reset_at)},
        {},
    )
    balancer = LoadBalancer(lambda: _repo_factory(_StubAccountsRepository([account]), usage_repo))

    result = await balancer.select_account(
        routing_strategy="usage_weighted",
        traffic_class=load_balancer_module.TRAFFIC_CLASS_OPPORTUNISTIC,
    )

    assert result.account is None
    assert result.error_code == "usage_limit_reached"
    assert result.resets_at == reset_at


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
        # Keys reported as purge tombstones (see
        # purge_stale_hard_codex_session_mappings): get_account_id_and_abandonment
        # reports these as ownerless, same as a missing row, but flags them as
        # abandoned so run_sticky_selection_path can bypass the
        # ambiguous-owner check for them.
        self.abandoned_keys: set[str] = set()
        self.scoped_abandoned_account_ids_by_key: dict[str, str] = {}
        # Refresh-skip deadlines reported alongside the owner lookup, keyed by
        # sticky key (see StickyOwnerLookup.refresh_skip_deadline).
        self.refresh_skip_deadlines_by_key: dict[str, datetime] = {}
        self.deleted: list[tuple[str, StickySessionKind | None]] = []
        self.upserts: list[tuple[str, str, StickySessionKind | None]] = []
        self.insert_if_absent_calls: list[tuple[str, str, StickySessionKind]] = []
        self.seeded_upserts: list[tuple[str, str, StickySessionKind, str, StickySessionKind]] = []

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        lookup = await self.get_account_id_and_abandonment(*args, **kwargs)
        return lookup.account_id

    async def release_read_snapshot(self) -> None:
        # The shared owner-lookup session releases its read snapshot between
        # ownership sources; the stub has no transaction to end.
        return None

    async def get_account_id_and_abandonment(self, *args: Any, **kwargs: Any) -> StickyOwnerLookup:
        key = cast(str, args[0])
        scoped_abandoned_account_id = self.scoped_abandoned_account_ids_by_key.get(key)
        if scoped_abandoned_account_id is not None:
            return StickyOwnerLookup(
                account_id=None,
                continuity_abandoned=True,
                abandoned_account_id=scoped_abandoned_account_id,
            )
        if key in self.abandoned_keys:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=True)
        if self.account_ids_by_key is not None:
            return StickyOwnerLookup(
                account_id=self.account_ids_by_key.get(key),
                continuity_abandoned=False,
                refresh_skip_deadline=self.refresh_skip_deadlines_by_key.get(key),
            )
        del kwargs
        return StickyOwnerLookup(
            account_id=self.account_id,
            continuity_abandoned=False,
            refresh_skip_deadline=self.refresh_skip_deadlines_by_key.get(key),
        )

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        sticky_key = cast(str, args[0])
        account_id = cast(str, args[1])
        self.account_id = account_id
        if self.account_ids_by_key is not None:
            self.account_ids_by_key[sticky_key] = account_id
        self.upserts.append((sticky_key, account_id, kwargs.get("kind")))
        return None

    async def insert_if_absent(
        self,
        key: str,
        account_id: str,
        kind: StickySessionKind,
    ) -> str:
        self.insert_if_absent_calls.append((key, account_id, kind))
        if self.account_ids_by_key is None:
            self.account_ids_by_key = {}
        return self.account_ids_by_key.setdefault(key, account_id)

    async def upsert_with_seed_if_absent(
        self,
        key: str,
        account_id: str,
        *,
        kind: StickySessionKind,
        seed_key: str,
        seed_kind: StickySessionKind,
    ) -> None:
        self.seeded_upserts.append((key, account_id, kind, seed_key, seed_kind))
        if self.account_ids_by_key is None:
            self.account_ids_by_key = {}
        self.account_ids_by_key.setdefault(seed_key, account_id)
        self.account_ids_by_key[key] = account_id
        self.account_id = account_id
        self.upserts.append((key, account_id, kind))

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        sticky_key = cast(str, args[0])
        self.deleted.append((sticky_key, kwargs.get("kind")))
        if self.account_ids_by_key is not None:
            self.account_ids_by_key.pop(sticky_key, None)
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
        current_account_id = (
            self.account_ids_by_key.get(key) if self.account_ids_by_key is not None else self.account_id
        )
        if current_account_id != expected_account_id:
            return False
        if restore_account_id is None:
            self.deleted.append((key, kind))
            if self.account_ids_by_key is not None:
                self.account_ids_by_key.pop(key, None)
            self.account_id = None
            return True
        self.upserts.append((key, restore_account_id, kind))
        if self.account_ids_by_key is not None:
            self.account_ids_by_key[key] = restore_account_id
        self.account_id = restore_account_id
        return True


class _ConcurrentUnboundStickySessionsRepository(_StubStickySessionsRepository):
    def __init__(self, expected_lookups: int) -> None:
        super().__init__()
        self._expected_lookups = expected_lookups
        self._lookup_count = 0
        self._all_lookups_started = asyncio.Event()

    async def get_account_id_and_abandonment(self, *args: Any, **kwargs: Any) -> StickyOwnerLookup:
        del args, kwargs
        self._lookup_count += 1
        if self._lookup_count >= self._expected_lookups:
            self._all_lookups_started.set()
        await self._all_lookups_started.wait()
        return StickyOwnerLookup(account_id=None, continuity_abandoned=False)


class _ConcurrentBoundStickySessionsRepository(_StubStickySessionsRepository):
    def __init__(self, *, account_id: str, expected_lookups: int) -> None:
        super().__init__()
        self.account_id = account_id
        self._initial_account_id = account_id
        self._expected_lookups = expected_lookups
        self._lookup_count = 0
        self._all_lookups_started = asyncio.Event()

    async def get_account_id_and_abandonment(self, *args: Any, **kwargs: Any) -> StickyOwnerLookup:
        del args, kwargs
        self._lookup_count += 1
        if self._lookup_count >= self._expected_lookups:
            self._all_lookups_started.set()
        await self._all_lookups_started.wait()
        return StickyOwnerLookup(account_id=self._initial_account_id, continuity_abandoned=False)


class _FailingUpsertStickySessionsRepository(_StubStickySessionsRepository):
    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("sticky persistence unavailable")


class _RetiringStaleOwnerStickySessionsRepository(_StubStickySessionsRepository):
    def __init__(self, *, raw_key: str, owner_account_id: str) -> None:
        super().__init__()
        self.account_ids_by_key = {raw_key: owner_account_id}
        self.tombstones: list[tuple[str, str]] = []

    async def abandon_legacy_session_header_owner_if_unavailable(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str,
    ) -> bool:
        assert kind == StickySessionKind.CODEX_SESSION
        assert self.account_ids_by_key is not None
        if self.account_ids_by_key.get(key) != expected_account_id:
            return False
        # The account objects supplied to selection intentionally remain stale
        # and ACTIVE after this authoritative repository decision.
        self.scoped_abandoned_account_ids_by_key[key] = expected_account_id
        self.tombstones.append((key, expected_account_id))
        return True

    async def upsert(self, *args: Any, **kwargs: Any) -> None:
        sticky_key = cast(str, args[0])
        account_id = cast(str, args[1])
        assert self.account_ids_by_key is not None
        self.account_ids_by_key[sticky_key] = account_id
        self.upserts.append((sticky_key, account_id, kwargs.get("kind")))


class _LosingRetirementRaceStickySessionsRepository(_RetiringStaleOwnerStickySessionsRepository):
    async def abandon_legacy_session_header_owner_if_unavailable(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str,
    ) -> bool:
        assert kind == StickySessionKind.CODEX_SESSION
        assert self.account_ids_by_key is not None
        assert self.account_ids_by_key.get(key) == expected_account_id
        # Another selector wins the source-scoped retirement CAS. The losing
        # selector's authoritative reread must carry this retained owner into
        # its stale-snapshot exclusion set.
        self.scoped_abandoned_account_ids_by_key[key] = expected_account_id
        self.tombstones.append((key, expected_account_id))
        return False


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
async def test_record_error_backoff_enters_floor_without_adding_full_threshold() -> None:
    account = _make_account("acc-error-backoff")
    accounts_repo = _StubAccountsRepository([account])
    usage_repo = _StubUsageRepository(primary={}, secondary={})
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo))

    await balancer.record_error_backoff(account)

    runtime = balancer._runtime[account.id]
    assert runtime.error_count == ERROR_BACKOFF_THRESHOLD
    assert runtime.last_error_at is not None

    await balancer.record_error_backoff(account)

    assert runtime.error_count == ERROR_BACKOFF_THRESHOLD + 1


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
async def test_stream_cap_takes_precedence_over_remaining_quota_exhausted_account() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    capped = _make_account("acc-stream-cap-mixed-capped")
    exhausted = _make_account("acc-stream-cap-mixed-exhausted")
    exhausted.status = AccountStatus.QUOTA_EXCEEDED
    exhausted.reset_at = now_epoch + 3600
    accounts_repo = _StubAccountsRepository([capped, exhausted])
    usage_repo = _StubUsageRepository(
        primary={
            capped.id: _usage_row_with_percent(
                203,
                capped.id,
                used_percent=50.0,
                reset_at=now_epoch + 300,
            ),
            exhausted.id: _usage_row_with_percent(
                204,
                exhausted.id,
                used_percent=100.0,
                reset_at=now_epoch + 3600,
            ),
        },
        secondary={},
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

    selected = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is None
    assert selected.error_code == "account_stream_cap"
    assert selected.error_message is not None
    assert "Account stream capacity is exhausted" in selected.error_message
    assert selected.resets_at is None

    for lease in leases:
        await balancer.release_account_lease(lease)


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
async def test_provisional_recovery_probe_does_not_publish_process_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-seed-healthy")
    probing = _make_account("acc-probe-seed-probing")
    accounts_repo = _StubAccountsRepository([healthy, probing])
    usage_repo = _StubUsageRepository(
        primary={
            healthy.id: _usage_row_with_percent(
                211,
                healthy.id,
                used_percent=30.0,
                reset_at=now_epoch + 300,
            ),
            probing.id: _usage_row_with_percent(
                212,
                probing.id,
                used_percent=10.0,
                reset_at=now_epoch + 300,
            ),
        },
        secondary={},
    )
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_ids_by_key = {}
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[probing.id] = RuntimeState(
        health_tier=HEALTH_TIER_PROBING,
        last_selected_at=0.0,
        version=37,
        health_version=13,
    )
    monkeypatch.setattr(balancer, "_commit_due_probe_reservation_locked", lambda *args, **kwargs: False)

    selected = await balancer.select_account(
        sticky_key="thread-after-probe-loss",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key="process-after-probe-loss",
        sticky_seed_key="process-seed-after-probe-loss",
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == healthy.id
    assert selected.lease is not None
    assert sticky_repo.account_ids_by_key == {
        "process-seed-after-probe-loss": healthy.id,
        "thread-after-probe-loss": healthy.id,
    }
    assert sticky_repo.seeded_upserts == [
        (
            "thread-after-probe-loss",
            healthy.id,
            StickySessionKind.PROMPT_CACHE,
            "process-seed-after-probe-loss",
            StickySessionKind.CODEX_SESSION,
        )
    ]

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
async def test_new_codex_thread_is_seeded_from_process_preference_without_rewriting_process_row() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-process-seed")
    assert alternate is not None
    process_session = "process-seed"
    process_key = _codex_session_selection_key(process_session)
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-new"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {process_key: owner.id}

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.account_ids_by_key == {
        process_key: owner.id,
        thread_key: owner.id,
    }
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == [(thread_key, owner.id, StickySessionKind.PROMPT_CACHE)]


@pytest.mark.asyncio
async def test_first_codex_thread_initializes_process_preference_once_for_later_siblings() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-first-process")
    assert alternate is not None
    process_session = "process-first-thread"
    process_key = _codex_session_selection_key(process_session)
    first_thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-first"}
    ).thread_selection_key
    sibling_thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-sibling"}
    ).thread_selection_key
    assert first_thread_key is not None
    assert sibling_thread_key is not None
    sticky_repo.account_ids_by_key = {}

    first = await balancer.select_account(
        sticky_key=first_thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )
    assert first.account is not None
    first_account_id = first.account.id

    sibling = await balancer.select_account(
        sticky_key=sibling_thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert sibling.account is not None
    assert sibling.account.id == first_account_id
    assert sticky_repo.account_ids_by_key[process_key] == first_account_id
    assert sticky_repo.insert_if_absent_calls == []
    assert sticky_repo.seeded_upserts == [
        (
            first_thread_key,
            first_account_id,
            StickySessionKind.PROMPT_CACHE,
            process_key,
            StickySessionKind.CODEX_SESSION,
        )
    ]
    assert sticky_repo.upserts == [
        (first_thread_key, first_account_id, StickySessionKind.PROMPT_CACHE),
        (sibling_thread_key, first_account_id, StickySessionKind.PROMPT_CACHE),
    ]


@pytest.mark.asyncio
async def test_fresh_same_owner_retention_skips_refresh_write_when_seed_exists() -> None:
    """A hot same-owner retention whose lookup observed the row inside the
    refresh-skip window issues no sticky write at all."""
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-skip-refresh")
    assert alternate is not None
    process_session = "process-skip-refresh"
    process_key = _codex_session_selection_key(process_session)
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-skip"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {process_key: owner.id, thread_key: owner.id}
    sticky_repo.refresh_skip_deadlines_by_key[thread_key] = datetime.now(tz=timezone.utc).replace(
        tzinfo=None
    ) + timedelta(seconds=10)

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.seeded_upserts == []
    assert sticky_repo.deleted == []


@pytest.mark.asyncio
async def test_fresh_thread_row_with_missing_seed_still_writes_and_initializes_seed() -> None:
    """Seed initialization piggybacks on the thread retention write; a fresh
    thread row must not suppress it while the process seed is absent."""
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-skip-seedless")
    assert alternate is not None
    process_session = "process-skip-seedless"
    process_key = _codex_session_selection_key(process_session)
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-seedless"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {thread_key: owner.id}
    sticky_repo.refresh_skip_deadlines_by_key[thread_key] = datetime.now(tz=timezone.utc).replace(
        tzinfo=None
    ) + timedelta(seconds=10)

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.account_ids_by_key[process_key] == owner.id
    assert sticky_repo.seeded_upserts == [
        (
            thread_key,
            owner.id,
            StickySessionKind.PROMPT_CACHE,
            process_key,
            StickySessionKind.CODEX_SESSION,
        )
    ]


@pytest.mark.asyncio
async def test_expired_refresh_skip_deadline_still_writes_through() -> None:
    """A deadline that lapsed between lookup and persist must not suppress the
    refresh: the skip window is revalidated at write time."""
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-skip-expired")
    assert alternate is not None
    process_session = "process-skip-expired"
    process_key = _codex_session_selection_key(process_session)
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-expired"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {process_key: owner.id, thread_key: owner.id}
    sticky_repo.refresh_skip_deadlines_by_key[thread_key] = datetime.now(tz=timezone.utc).replace(
        tzinfo=None
    ) - timedelta(seconds=1)

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.upserts == [(thread_key, owner.id, StickySessionKind.PROMPT_CACHE)]


@pytest.mark.asyncio
async def test_required_file_owner_does_not_rewrite_existing_thread_row() -> None:
    balancer, thread_owner, file_owner, sticky_repo = _make_cap_spillover_balancer("file-pin-thread")
    assert file_owner is not None
    process_session = "file-pin-process"
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "file-pin-thread"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {thread_key: thread_owner.id}
    preferred = _AffinityPolicy.preferred_owner_sticky_inputs(
        thread_key,
        StickySessionKind.PROMPT_CACHE,
        False,
        300,
        "thread_header",
        process_session,
    )

    selected = await balancer.select_account(
        sticky_key=preferred[0],
        sticky_kind=preferred[1],
        reallocate_sticky=preferred[2],
        sticky_max_age_seconds=preferred[3],
        sticky_source=preferred[4],
        legacy_sticky_key=preferred[5],
        required_account_id=file_owner.id,
        required_account_is_ownership_constraint=True,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == file_owner.id
    assert sticky_repo.account_ids_by_key == {thread_key: thread_owner.id}
    assert sticky_repo.upserts == []
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_required_file_owner_seeds_process_preference_for_later_sibling() -> None:
    balancer, thread_owner, file_owner, sticky_repo = _make_cap_spillover_balancer("file-pin-seed")
    assert file_owner is not None
    process_session = "file-pin-seed-process"
    process_key = _codex_session_selection_key(process_session)
    first_thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "file-pin-first"}
    ).thread_selection_key
    sibling_thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "file-pin-sibling"}
    ).thread_selection_key
    assert first_thread_key is not None
    assert sibling_thread_key is not None
    sticky_repo.account_ids_by_key = {}
    preferred = _AffinityPolicy.preferred_owner_sticky_inputs(
        first_thread_key,
        StickySessionKind.PROMPT_CACHE,
        False,
        300,
        "thread_header",
        process_session,
    )

    first = await balancer.select_account(
        sticky_key=preferred[0],
        sticky_kind=preferred[1],
        reallocate_sticky=preferred[2],
        sticky_max_age_seconds=preferred[3],
        sticky_source=preferred[4],
        legacy_sticky_key=preferred[5],
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        required_account_id=file_owner.id,
        required_account_is_ownership_constraint=True,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert first.account is not None
    assert first.account.id == file_owner.id
    assert first_thread_key not in (sticky_repo.account_ids_by_key or {})
    assert sticky_repo.account_ids_by_key == {process_key: file_owner.id}

    sibling = await balancer.select_account(
        sticky_key=sibling_thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_seed_key=process_key,
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert sibling.account is not None
    assert sibling.account.id == file_owner.id
    assert sticky_repo.account_ids_by_key[process_key] == file_owner.id
    await balancer.release_account_lease(first.lease)
    await balancer.release_account_lease(sibling.lease)


@pytest.mark.asyncio
async def test_legacy_raw_process_owner_wins_over_thread_locality() -> None:
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-legacy-owner")
    assert alternate is not None
    process_session = "legacy-process-owner"
    thread_key = _codex_backend_identity(
        {"session-id": process_session, "thread-id": "thread-existing"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {
        process_session: owner.id,
        thread_key: alternate.id,
    }

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        legacy_sticky_key=process_session,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.account_ids_by_key == {
        process_session: owner.id,
        thread_key: alternate.id,
    }
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []


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
async def test_goal_restart_does_not_repin_retired_owner_from_stale_selection_snapshot() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    stale_owner = _make_account("goal-restart-stale-snapshot-owner")
    replacement = _make_account("goal-restart-stale-snapshot-replacement")
    raw_session = "goal-restart-stale-snapshot"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo = _RetiringStaleOwnerStickySessionsRepository(
        raw_key=raw_session,
        owner_account_id=stale_owner.id,
    )
    # Keep both account objects ACTIVE to model inputs loaded before the
    # repository's guarded retirement observes the owner's unavailable row.
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([stale_owner, replacement]),
            _StubUsageRepository(
                {
                    stale_owner.id: _usage_row(301, stale_owner.id, window="primary", reset_at=now_epoch + 300),
                    replacement.id: _usage_row(302, replacement.id, window="primary", reset_at=now_epoch + 300),
                },
                {},
            ),
            sticky_repo,
        )
    )

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        abandon_unavailable_legacy_owner=True,
        routing_strategy="single_account",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == replacement.id
    assert sticky_repo.tombstones == [(raw_session, stale_owner.id)]
    assert sticky_repo.account_ids_by_key == {
        raw_session: stale_owner.id,
        selection_key: replacement.id,
    }
    assert all(account_id != stale_owner.id for _, account_id, _ in sticky_repo.upserts)
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_goal_restart_with_thread_header_retires_unavailable_legacy_owner() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    stale_owner = _make_account("goal-restart-thread-header-owner")
    replacement = _make_account("goal-restart-thread-header-replacement")
    raw_session = "goal-restart-thread-header-session"
    thread_key = _codex_backend_identity(
        {"session-id": raw_session, "thread-id": "goal-restart-thread"}
    ).thread_selection_key
    assert thread_key is not None
    sticky_repo = _RetiringStaleOwnerStickySessionsRepository(
        raw_key=raw_session,
        owner_account_id=stale_owner.id,
    )
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([stale_owner, replacement]),
            _StubUsageRepository(
                {
                    stale_owner.id: _usage_row(311, stale_owner.id, window="primary", reset_at=now_epoch + 300),
                    replacement.id: _usage_row(312, replacement.id, window="primary", reset_at=now_epoch + 300),
                },
                {},
            ),
            sticky_repo,
        )
    )

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        sticky_max_age_seconds=300,
        legacy_sticky_key=raw_session,
        abandon_unavailable_legacy_owner=True,
        routing_strategy="single_account",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == replacement.id
    assert sticky_repo.tombstones == [(raw_session, stale_owner.id)]
    assert sticky_repo.account_ids_by_key == {
        raw_session: stale_owner.id,
        thread_key: replacement.id,
    }
    assert all(account_id != stale_owner.id for _, account_id, _ in sticky_repo.upserts)
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_goal_restart_cas_loser_does_not_repin_concurrently_retired_owner() -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    stale_owner = _make_account("goal-restart-cas-loser-owner")
    replacement = _make_account("goal-restart-cas-loser-replacement")
    raw_session = "goal-restart-cas-loser"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo = _LosingRetirementRaceStickySessionsRepository(
        raw_key=raw_session,
        owner_account_id=stale_owner.id,
    )
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([stale_owner, replacement]),
            _StubUsageRepository(
                {
                    stale_owner.id: _usage_row(305, stale_owner.id, window="primary", reset_at=now_epoch + 300),
                    replacement.id: _usage_row(306, replacement.id, window="primary", reset_at=now_epoch + 300),
                },
                {},
            ),
            sticky_repo,
        )
    )

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        abandon_unavailable_legacy_owner=True,
        routing_strategy="single_account",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == replacement.id
    assert sticky_repo.tombstones == [(raw_session, stale_owner.id)]
    assert sticky_repo.account_ids_by_key == {
        raw_session: stale_owner.id,
        selection_key: replacement.id,
    }
    assert all(account_id != stale_owner.id for _, account_id, _ in sticky_repo.upserts)
    await balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_goal_restart_mutation_authority_precedes_model_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    owner = _make_account("goal-restart-model-ineligible-owner")
    replacement = _make_account("goal-restart-model-eligible-replacement")
    raw_session = "goal-restart-model-authority"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo = _RetiringStaleOwnerStickySessionsRepository(
        raw_key=raw_session,
        owner_account_id=owner.id,
    )
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([owner, replacement]),
            _StubUsageRepository(
                {
                    owner.id: _usage_row(303, owner.id, window="primary", reset_at=now_epoch + 300),
                    replacement.id: _usage_row(304, replacement.id, window="primary", reset_at=now_epoch + 300),
                },
                {},
            ),
            sticky_repo,
        )
    )

    monkeypatch.setattr(load_balancer_module, "_mapped_model_has_registry_entry", lambda _model: True)
    monkeypatch.setattr(
        load_balancer_module,
        "_filter_accounts_for_model",
        lambda accounts, _model, **_kwargs: [account for account in accounts if account.id == replacement.id],
    )
    monkeypatch.setattr(
        load_balancer_module,
        "_filter_accounts_for_model_with_catalog_evidence",
        lambda accounts, _model, **_kwargs: load_balancer_module._ModelAccountFilterResult(
            accounts=[account for account in accounts if account.id == replacement.id],
            general_model_account_ids=frozenset({replacement.id}),
        ),
    )

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        abandon_unavailable_legacy_owner=True,
        model="gpt-model-authority",
        lease_kind="stream",
    )

    assert selected.account is not None
    assert selected.account.id == replacement.id
    assert sticky_repo.tombstones == [(raw_session, owner.id)]
    await balancer.release_account_lease(selected.lease)


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
async def test_scoped_restart_marker_does_not_prove_ambiguous_conversation_owner() -> None:
    balancer, retired_owner, replacement, sticky_repo = _make_cap_spillover_balancer("conversation-scoped-restart")
    assert replacement is not None
    raw_session = "conversation-scoped-restart-session"
    selection_key = _codex_session_selection_key(raw_session)
    sticky_repo.account_ids_by_key = {
        raw_session: retired_owner.id,
        selection_key: replacement.id,
    }
    sticky_repo.scoped_abandoned_account_ids_by_key[raw_session] = retired_owner.id

    selected = await balancer.select_account(
        sticky_key=selection_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is None
    assert selected.error_code == "conversation_owner_unavailable"


@pytest.mark.asyncio
async def test_tombstoned_hard_owner_lets_conversation_continuity_reselect() -> None:
    """A purge-tombstoned mapping (see purge_stale_hard_codex_session_mappings)
    must not permanently strand a `conversation`-continuity request just
    because the pool has more than one account. Unlike a key that was never
    seen, we know this key's owner was durably unavailable and continuity was
    deliberately abandoned, so a fresh account may be selected and a new hard
    mapping re-established — otherwise the request could never recover even
    after the original owner comes back, since nothing on this path would
    ever re-create the very row needed to stop failing closed."""
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("conversation-tombstoned")
    assert alternate is not None
    turn_state_key = "conversation-tombstoned-turn-state"
    sticky_repo.abandoned_keys = {turn_state_key}

    selected = await balancer.select_account(
        sticky_key=turn_state_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="turn_state",
        require_unambiguous_account=True,
        lease_kind="response_create",
    )

    assert selected.account is not None
    assert selected.account.id in {owner.id, alternate.id}
    assert selected.error_code is None
    assert sticky_repo.upserts
    assert sticky_repo.upserts[-1][0] == turn_state_key
    assert sticky_repo.upserts[-1][1] == selected.account.id


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


# ---------------------------------------------------------------------------
# Congestion-aware per-API-key stream fair share
# ---------------------------------------------------------------------------

# Small pool for exact fair-share arithmetic: 2 accounts x 2 stream slots = 4.
_FAIR_SHARE_CAPS = load_balancer_module.AccountConcurrencyCaps(
    response_create_limit=4,
    stream_limit=2,
)


def _make_fair_share_pool(
    prefix: str,
    *,
    account_count: int = 2,
    sticky_repo: _StubStickySessionsRepository | None = None,
) -> tuple[LoadBalancer, list[Account], _StubStickySessionsRepository]:
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    accounts = [_make_account(f"{prefix}-{index}") for index in range(account_count)]
    primary = {
        account.id: _usage_row(400 + index, account.id, window="primary", reset_at=now_epoch + 300)
        for index, account in enumerate(accounts)
    }
    secondary = {
        account.id: _usage_row(500 + index, account.id, window="secondary", reset_at=now_epoch + 3600)
        for index, account in enumerate(accounts)
    }
    sticky_repo = sticky_repo or _StubStickySessionsRepository()
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository(accounts),
            _StubUsageRepository(primary, secondary),
            sticky_repo,
        )
    )
    return balancer, accounts, sticky_repo


async def _grab_stream(balancer: LoadBalancer, account_id: str, api_key_id: str | None) -> Any:
    """Deterministically pin a stream lease for a key onto one account."""
    async with balancer._runtime_lock:
        return balancer._acquire_account_lease_locked(
            account_id,
            kind="stream",
            estimated_tokens=0.0,
            api_key_id=api_key_id,
        )


async def _fair_share_select(
    balancer: LoadBalancer,
    api_key_id: str | None,
    *,
    threshold_pct: int = 50,
    lease_kind: Literal["stream", "response_create"] = "stream",
) -> Any:
    return await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind=lease_kind,
        concurrency_caps=_FAIR_SHARE_CAPS,
        api_key_id=api_key_id,
        api_key_stream_fair_share_threshold_pct=threshold_pct,
    )


@pytest.mark.asyncio
async def test_stream_lease_api_key_accounting_tracks_acquire_release_and_deletes_at_zero() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-accounting", account_count=1)
    account = accounts[0]

    first = await _fair_share_select(balancer, "k1", threshold_pct=0)
    second = await _fair_share_select(balancer, "k1", threshold_pct=0)

    assert first.lease is not None
    assert first.lease.api_key_id == "k1"
    assert second.lease is not None
    assert second.lease.api_key_id == "k1"
    runtime = balancer._runtime[account.id]
    assert runtime.stream_key_inflight == {"k1": 2}

    await balancer.release_account_lease(first.lease)
    assert runtime.stream_key_inflight == {"k1": 1}

    await balancer.release_account_lease(second.lease)
    assert not runtime.stream_key_inflight
    assert runtime.inflight_streams == 0


@pytest.mark.asyncio
async def test_stream_lease_api_key_stale_reclaim_decrements_per_key_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        proxy_account_lease_ttl_seconds=1.0,
        proxy_request_budget_seconds=1.0,
        http_responses_stream_request_budget_seconds=1.0,
        http_responses_session_bridge_request_budget_seconds=1.0,
        proxy_account_stream_limit=2,
        proxy_account_response_create_limit=2,
    )
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-stale", account_count=1)
    account = accounts[0]

    stale_lease = await _grab_stream(balancer, account.id, "k1")
    runtime = balancer._runtime[account.id]
    assert runtime.stream_key_inflight == {"k1": 1}
    # Stream TTL is the max request budget (1.0s) plus the 60s stale grace.
    object.__setattr__(stale_lease, "acquired_at", time.monotonic() - 120.0)

    replacement = await balancer.acquire_account_lease(account.id, kind="response_create")

    assert replacement is not None
    assert not runtime.stream_key_inflight
    assert runtime.inflight_streams == 0


@pytest.mark.asyncio
async def test_api_key_fair_share_default_threshold_keeps_saturation_outcomes_identical() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-default", account_count=1)
    account = accounts[0]

    keyed_leases = [(await _fair_share_select(balancer, "k1", threshold_pct=0)).lease for _ in range(2)]
    assert all(lease is not None for lease in keyed_leases)
    assert balancer._runtime[account.id].stream_key_inflight == {"k1": 2}

    # Even with per-key accounting active and the pool saturated by one key,
    # the default threshold of zero must reproduce the pre-feature outcome:
    # the plain per-account stream cap denial, never a fair-share denial.
    denied_keyed = await _fair_share_select(balancer, "k1", threshold_pct=0)
    denied_keyless = await _fair_share_select(balancer, None, threshold_pct=0)

    assert denied_keyed.account is None
    assert denied_keyless.account is None
    assert denied_keyed.error_code == denied_keyless.error_code == "account_stream_cap"
    assert denied_keyed.error_message == denied_keyless.error_message


@pytest.mark.asyncio
async def test_api_key_fair_share_denies_over_share_key_and_admits_light_key_under_congestion() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-congested")
    account_a, account_b = accounts
    # C = 2 accounts * 2 slots = 4; heavy holds 2, light holds 1 -> T = 3.
    # threshold 50: 3 * 100 >= 4 * 50 -> congested; share = max(2, 4 // 2) = 2.
    await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_a.id, "light")

    denied = await _fair_share_select(balancer, "heavy")

    assert denied.account is None
    assert denied.lease is None
    assert denied.error_code == "api_key_stream_fair_share"
    assert denied.error_message is not None
    assert "fair share" in denied.error_message
    # A denial must not perturb the per-key accounting it read.
    assert balancer._runtime[account_a.id].stream_key_inflight == {"heavy": 1, "light": 1}
    assert balancer._runtime[account_b.id].stream_key_inflight == {"heavy": 1}

    admitted = await _fair_share_select(balancer, "light")

    assert admitted.account is not None
    assert admitted.lease is not None
    assert admitted.lease.api_key_id == "light"


@pytest.mark.asyncio
async def test_api_key_fair_share_readmits_heavy_key_after_release_below_share() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-readmit")
    account_a, account_b = accounts
    heavy_lease = await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_a.id, "light")

    denied = await _fair_share_select(balancer, "heavy")
    assert denied.error_code == "api_key_stream_fair_share"

    await balancer.release_account_lease(heavy_lease)
    # T = 2 keeps the pool congested (2 * 100 >= 4 * 50), but heavy now holds
    # 1 < share 2, so the freed capacity flows back to it.
    readmitted = await _fair_share_select(balancer, "heavy")

    assert readmitted.account is not None
    assert readmitted.lease is not None
    assert readmitted.lease.api_key_id == "heavy"


@pytest.mark.asyncio
async def test_api_key_fair_share_keyless_request_bypasses_gate_under_congestion() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-keyless-bypass")
    account_a, account_b = accounts
    await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    # T = 2, C = 4, threshold 50 -> congested.

    keyless = await _fair_share_select(balancer, None)

    assert keyless.account is not None
    assert keyless.lease is not None
    assert keyless.lease.api_key_id is None
    # Keyless streams never join the per-key map.
    runtime = balancer._runtime[keyless.account.id]
    assert runtime.stream_key_inflight == {"heavy": 1}


@pytest.mark.asyncio
async def test_api_key_fair_share_counts_keyless_streams_toward_pool_inflight() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-keyless-counted")
    account_a, account_b = accounts
    await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_a.id, "light")
    # T = 3, C = 4, threshold 80: 300 < 320 -> not congested, heavy admits.

    uncongested = await _fair_share_select(balancer, "heavy", threshold_pct=80)
    assert uncongested.account is not None
    await balancer.release_account_lease(uncongested.lease)

    # One keyless stream tips the pool over: T = 4, 400 >= 320 -> congested,
    # and heavy (2 >= share 2) is now denied.
    await _grab_stream(balancer, account_b.id, None)
    congested = await _fair_share_select(balancer, "heavy", threshold_pct=80)

    assert congested.account is None
    assert congested.error_code == "api_key_stream_fair_share"


@pytest.mark.asyncio
async def test_api_key_fair_share_response_create_leases_never_touch_stream_key_inflight() -> None:
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-response-create")
    account_a, account_b = accounts
    await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_a.id, "light")
    # The stream pool is congested, but non-stream leases bypass the gate
    # entirely -- even for the over-share key.
    selection = await _fair_share_select(balancer, "heavy", lease_kind="response_create")

    assert selection.account is not None
    assert selection.lease is not None
    assert selection.lease.kind == "response_create"
    assert balancer._runtime[account_a.id].stream_key_inflight == {"heavy": 1, "light": 1}
    assert balancer._runtime[account_b.id].stream_key_inflight == {"heavy": 1}
    assert balancer._runtime[selection.account.id].inflight_response_creates == 1

    await balancer.release_account_lease(selection.lease)
    assert balancer._runtime[account_a.id].stream_key_inflight == {"heavy": 1, "light": 1}
    assert balancer._runtime[account_b.id].stream_key_inflight == {"heavy": 1}


@pytest.mark.asyncio
async def test_direct_acquire_counts_api_key_and_enforces_fair_share_under_congestion() -> None:
    """``acquire_account_lease`` joins per-key accounting and the fair-share gate.

    Regression for the warm HTTP bridge session reacquire path (review P2):
    direct account-pinned acquires previously ignored ``api_key_id``, so keyed
    turns on reused bridge sessions took uncounted stream capacity and were
    never congestion-gated. The pinned account is the key's whole usable pool
    here, so fair share is measured against that single account.
    """
    direct_caps = load_balancer_module.AccountConcurrencyCaps(response_create_limit=4, stream_limit=4)
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-direct", account_count=1)
    account = accounts[0]
    # C = 1 account * 4 slots = 4; heavy holds 2, other holds 1 -> T = 3.
    # threshold 50: 300 >= 200 -> congested; share = max(2, 4 // 2) = 2.
    await _grab_stream(balancer, account.id, "heavy")
    await _grab_stream(balancer, account.id, "heavy")
    await _grab_stream(balancer, account.id, "other")
    runtime = balancer._runtime[account.id]

    with pytest.raises(load_balancer_module.ApiKeyFairShareDenialError) as exc_info:
        await balancer.acquire_account_lease(
            account.id,
            kind="stream",
            concurrency_caps=direct_caps,
            api_key_id="heavy",
            api_key_stream_fair_share_threshold_pct=50,
        )

    assert exc_info.value.decision.congested is True
    assert "fair share" in str(exc_info.value)
    # The denial neither installed a lease nor perturbed the accounting.
    assert runtime.inflight_streams == 3
    assert runtime.stream_key_inflight == {"heavy": 2, "other": 1}

    # A key under the minimum guarantee admits despite the congestion and is
    # counted into the per-key map; release returns its count symmetrically.
    light = await balancer.acquire_account_lease(
        account.id,
        kind="stream",
        concurrency_caps=direct_caps,
        api_key_id="light",
        api_key_stream_fair_share_threshold_pct=50,
    )
    assert light is not None
    assert light.api_key_id == "light"
    assert runtime.stream_key_inflight == {"heavy": 2, "other": 1, "light": 1}
    await balancer.release_account_lease(light)
    assert runtime.stream_key_inflight == {"heavy": 2, "other": 1}

    # A zero threshold disables the gate (pre-feature outcome) but keyed
    # direct acquires still join the accounting.
    ungated = await balancer.acquire_account_lease(
        account.id,
        kind="stream",
        concurrency_caps=direct_caps,
        api_key_id="heavy",
        api_key_stream_fair_share_threshold_pct=0,
    )
    assert ungated is not None
    assert runtime.stream_key_inflight == {"heavy": 3, "other": 1}


@pytest.mark.asyncio
async def test_api_key_fair_share_sticky_path_denies_with_stable_code_and_preserves_mapping() -> None:
    sticky_repo = _StubStickySessionsRepository()
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-sticky", sticky_repo=sticky_repo)
    account_a, account_b = accounts
    sticky_repo.account_id = account_a.id
    await _grab_stream(balancer, account_a.id, "heavy")
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_a.id, "light")

    denied = await balancer.select_account(
        sticky_key="fair-share-sticky-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_FAIR_SHARE_CAPS,
        api_key_id="heavy",
        api_key_stream_fair_share_threshold_pct=50,
    )

    assert denied.account is None
    assert denied.lease is None
    assert denied.error_code == "api_key_stream_fair_share"
    assert denied.error_message is not None
    assert "fair share" in denied.error_message
    assert sticky_repo.account_id == account_a.id
    assert sticky_repo.deleted == []
    assert sticky_repo.upserts == []


@pytest.mark.asyncio
async def test_api_key_fair_share_sticky_commit_recheck_denies_when_share_fills_during_sticky_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balancer, accounts, sticky_repo = _make_fair_share_pool("acc-fair-share-recheck")
    account_a, account_b = accounts
    sticky_repo.account_id = account_a.id
    # heavy holds 1 (one below its share of 2); light keeps the pool congested:
    # T = 2, C = 4, threshold 50 -> 200 >= 200, share = max(2, 4 // 2) = 2.
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_b.id, "light")

    original_select_with_stickiness = balancer._select_with_stickiness

    async def racing_select_with_stickiness(*args: Any, **kwargs: Any) -> Any:
        # A concurrent selection for the same key wins the race inside the
        # window between the filter-phase gate and the commit lock section.
        async with balancer._runtime_lock:
            balancer._acquire_account_lease_locked(
                account_a.id,
                kind="stream",
                estimated_tokens=0.0,
                api_key_id="heavy",
            )
        return await original_select_with_stickiness(*args, **kwargs)

    monkeypatch.setattr(balancer, "_select_with_stickiness", racing_select_with_stickiness)

    denied = await balancer.select_account(
        sticky_key="fair-share-recheck-session",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=600,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_FAIR_SHARE_CAPS,
        api_key_id="heavy",
        api_key_stream_fair_share_threshold_pct=50,
    )

    assert denied.account is None
    assert denied.lease is None
    assert denied.error_code == "api_key_stream_fair_share"
    heavy_total = sum((runtime.stream_key_inflight or {}).get("heavy", 0) for runtime in balancer._runtime.values())
    assert heavy_total == 2  # setup lease + racing winner; the loser added nothing


@pytest.mark.asyncio
async def test_api_key_fair_share_concurrent_sticky_selections_cannot_overshoot_share() -> None:
    sticky_repo = _ConcurrentBoundStickySessionsRepository(
        account_id="acc-fair-share-race-0",
        expected_lookups=2,
    )
    balancer, accounts, _ = _make_fair_share_pool("acc-fair-share-race", sticky_repo=sticky_repo)
    account_a, account_b = accounts
    assert account_a.id == "acc-fair-share-race-0"
    # heavy holds 1 (one below its share of 2) and light keeps the pool
    # congested: T = 2, C = 4, threshold 50 -> 200 >= 200.
    await _grab_stream(balancer, account_b.id, "heavy")
    await _grab_stream(balancer, account_b.id, "light")

    results = await asyncio.gather(
        *(
            balancer.select_account(
                sticky_key="fair-share-race-session",
                sticky_kind=StickySessionKind.CODEX_SESSION,
                routing_strategy="usage_weighted",
                lease_kind="stream",
                concurrency_caps=_FAIR_SHARE_CAPS,
                api_key_id="heavy",
                api_key_stream_fair_share_threshold_pct=50,
            )
            for _ in range(2)
        )
    )

    admitted = [result for result in results if result.account is not None]
    denied = [result for result in results if result.account is None]
    assert len(admitted) == 1
    assert admitted[0].account is not None
    assert admitted[0].account.id == account_a.id
    assert admitted[0].lease is not None
    assert len(denied) == 1
    assert denied[0].error_code == "api_key_stream_fair_share"
    # The commit re-check kept heavy at exactly its share across both paths.
    heavy_total = sum((runtime.stream_key_inflight or {}).get("heavy", 0) for runtime in balancer._runtime.values())
    assert heavy_total == 2


@pytest.mark.asyncio
async def test_fresh_same_owner_retention_skips_refresh_write_on_probe_admission() -> None:
    """The recovery-probe admission path honors the refresh-skip deadline the
    same way the non-probe persist site does: a fresh same-owner retention of
    a due-probing pinned owner issues no sticky write."""
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    healthy = _make_account("acc-probe-skip-healthy")
    probing = _make_account("acc-probe-skip-probing")
    key = "probe-skip-session"

    def _build(sticky_repo: _StubStickySessionsRepository) -> LoadBalancer:
        accounts_repo = _StubAccountsRepository([healthy, probing])
        usage_repo = _StubUsageRepository(
            primary={
                healthy.id: _usage_row_with_percent(
                    150,
                    healthy.id,
                    used_percent=30.0,
                    reset_at=now_epoch + 300,
                ),
                probing.id: _usage_row_with_percent(
                    151,
                    probing.id,
                    used_percent=10.0,
                    reset_at=now_epoch + 300,
                ),
            },
            secondary={},
        )
        balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
        balancer._runtime[probing.id] = RuntimeState(
            health_tier=HEALTH_TIER_PROBING,
            last_selected_at=0.0,
            version=17,
        )
        return balancer

    # Control: without a freshness observation the probe admission persists
    # the retention write, proving this scenario exercises the probe branch.
    control_repo = _StubStickySessionsRepository()
    control_repo.account_ids_by_key = {key: probing.id}
    control_balancer = _build(control_repo)
    control = await control_balancer.select_account(
        sticky_key=key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert control.account is not None
    assert control.account.id == probing.id
    assert control_repo.upserts == [(key, probing.id, StickySessionKind.PROMPT_CACHE)]
    await control_balancer.release_account_lease(control.lease)

    skip_repo = _StubStickySessionsRepository()
    skip_repo.account_ids_by_key = {key: probing.id}
    skip_repo.refresh_skip_deadlines_by_key[key] = datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=10
    )
    skip_balancer = _build(skip_repo)
    selected = await skip_balancer.select_account(
        sticky_key=key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert selected.account is not None
    assert selected.account.id == probing.id
    assert skip_repo.upserts == []
    assert skip_repo.deleted == []
    # The probe reservation itself still committed: runtime advanced.
    probing_runtime = skip_balancer._runtime[probing.id]
    assert probing_runtime.version > 17
    assert probing_runtime.last_selected_at is not None
    assert probing_runtime.last_selected_at > 0.0
    await skip_balancer.release_account_lease(selected.lease)


@pytest.mark.asyncio
async def test_fresh_thread_only_retention_without_seed_key_skips_refresh_write() -> None:
    """Thread-only affinity (no process seed key at all) has nothing to
    initialize, so a fresh same-owner retention skips its refresh write."""
    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("thread-skip-no-seedkey")
    assert alternate is not None
    thread_key = _codex_backend_identity({"thread-id": "thread-only-skip"}).thread_selection_key
    assert thread_key is not None
    sticky_repo.account_ids_by_key = {thread_key: owner.id}
    sticky_repo.refresh_skip_deadlines_by_key[thread_key] = datetime.now(tz=timezone.utc).replace(
        tzinfo=None
    ) + timedelta(seconds=10)

    selected = await balancer.select_account(
        sticky_key=thread_key,
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        sticky_source="thread_header",
        sticky_max_age_seconds=300,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    assert sticky_repo.upserts == []
    assert sticky_repo.seeded_upserts == []
    assert sticky_repo.deleted == []


class _LookupCountingStickyRepo(_StubStickySessionsRepository):
    """Records owner-lookup and snapshot-release events, each stamped with the
    repository context that issued it, so tests can pin lookup count/order and
    the one-session/fresh-transaction-per-source contract."""

    def __init__(self) -> None:
        super().__init__()
        # Set by the test's repo factory each time a repo bundle opens.
        self.current_context_id: int | None = None
        self.owner_lookup_events: list[tuple[str, int | None, str | None]] = []

    async def get_account_id_and_abandonment(self, *args: Any, **kwargs: Any) -> StickyOwnerLookup:
        self.owner_lookup_events.append(("lookup", self.current_context_id, cast(str, args[0])))
        return await super().get_account_id_and_abandonment(*args, **kwargs)

    async def release_read_snapshot(self) -> None:
        self.owner_lookup_events.append(("release_snapshot", self.current_context_id, None))
        await super().release_read_snapshot()


@pytest.mark.asyncio
async def test_shared_owner_lookup_session_reads_each_owner_key_exactly_once() -> None:
    """Regression for the shared owner-lookup session.

    The legacy/seed/first-sticky owner reads moved into one repo bundle in
    ``select_account``; the sticky selection loop consumes the hoisted first
    read exactly once instead of re-reading. Each owner key must be looked up
    exactly once, in the legacy -> seed -> sticky order, all three reads must
    share one repository context (one session), each later ownership source
    must first release the shared read snapshot so it starts a fresh
    transaction, and the resolved hard owner must still win selection.
    """
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    owner = _make_account("acc-shared-owner-lookup")
    other = _make_account("acc-shared-owner-other")
    accounts_repo = _StubAccountsRepository([owner, other])
    usage_repo = _StubUsageRepository(
        primary={
            owner.id: _usage_row(70, owner.id, window="primary", reset_at=now_epoch + 300),
            other.id: _usage_row(71, other.id, window="primary", reset_at=now_epoch + 300),
        },
        secondary={},
    )
    sticky_repo = _LookupCountingStickyRepo()
    sticky_repo.account_ids_by_key = {"shared-lookup-sticky": owner.id}
    opened_context_count = 0

    @asynccontextmanager
    async def context_stamping_repo_factory() -> AsyncIterator[ProxyRepositories]:
        # Stamp every bundle open with a distinct identifier so the events
        # recorded by the sticky repo prove which context issued each read;
        # the old per-lookup-session flow would record three distinct ids.
        nonlocal opened_context_count
        opened_context_count += 1
        sticky_repo.current_context_id = opened_context_count
        async with _repo_factory(accounts_repo, usage_repo, sticky_repo) as repos:
            yield repos

    balancer = LoadBalancer(context_stamping_repo_factory)

    selected = await balancer.select_account(
        sticky_key="shared-lookup-sticky",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="turn_state",
        legacy_sticky_key="shared-lookup-legacy",
        sticky_seed_key="shared-lookup-seed",
        sticky_seed_kind=StickySessionKind.CODEX_SESSION,
        routing_strategy="usage_weighted",
    )

    assert selected.account is not None
    assert selected.account.id == owner.id
    # get_account_id (seed) delegates to get_account_id_and_abandonment in the
    # stub, so this also proves the seed lookup ran exactly once. The
    # release_snapshot events pin the fix semantics: one shared session, but a
    # fresh read transaction before each later ownership source so a
    # concurrently committed owner stays visible on SQLite/WAL.
    assert [(event, key) for event, _, key in sticky_repo.owner_lookup_events] == [
        ("lookup", "shared-lookup-legacy"),
        ("release_snapshot", None),
        ("lookup", "shared-lookup-seed"),
        ("release_snapshot", None),
        ("lookup", "shared-lookup-sticky"),
    ]
    lookup_context_ids = {context_id for _, context_id, _ in sticky_repo.owner_lookup_events}
    # One repository context served every ownership source; the old
    # session-per-lookup flow would have recorded three distinct ids here.
    assert len(lookup_context_ids) == 1
    assert None not in lookup_context_ids
