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
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit


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

    await balancer.release_account_lease(leases[0])
    recovered = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )

    assert recovered.account is not None
    assert recovered.account.id == account.id
    assert recovered.lease is not None


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
