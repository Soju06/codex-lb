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
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.affinity import _codex_session_selection_key
from app.modules.proxy.cap_partitioning import CapPartition
from app.modules.proxy.load_balancer import LoadBalancer, effective_account_concurrency_caps
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

    async def update_status(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True

    async def update_status_if_current(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True


class _StubUsageRepository:
    def __init__(self, primary: dict[str, UsageHistory], secondary: dict[str, UsageHistory]) -> None:
        self._primary = primary
        self._secondary = secondary

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        del account_ids
        if window == "secondary":
            return self._secondary
        return self._primary


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
