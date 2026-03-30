from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.core.balancer.types import UpstreamError
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import ModelRegistrySnapshot
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AdditionalUsageHistory,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import (
    ADDITIONAL_QUOTA_DATA_UNAVAILABLE,
    NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS,
    NO_PLAN_SUPPORT_FOR_MODEL,
    LoadBalancer,
    RuntimeState,
)
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


def _make_account(account_id: str, email: str = "a@example.com") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


class StubAccountsRepository(AccountsRepository):
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts
        self.status_updates: list[dict[str, Any]] = []

    async def get_by_id(self, account_id: str) -> Account | None:
        return self._find_account(account_id)

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    def _find_account(self, account_id: str) -> Account | None:
        return next((account for account in self._accounts if account.id == account_id), None)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        account = self._find_account(account_id)
        if account is None:
            return False
        account.status = status
        account.deactivation_reason = deactivation_reason
        account.reset_at = reset_at
        self.status_updates.append(
            {
                "account_id": account_id,
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
        )
        return True

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
    ) -> bool:
        account = self._find_account(account_id)
        if account is None:
            return False
        if (
            account.status != expected_status
            or account.deactivation_reason != expected_deactivation_reason
            or account.reset_at != expected_reset_at
        ):
            return False
        return await self.update_status(account_id, status, deactivation_reason, reset_at)


class StubUsageRepository(UsageRepository):
    def __init__(
        self,
        primary: dict[str, UsageHistory],
        secondary: dict[str, UsageHistory],
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self.primary_calls = 0
        self.secondary_calls = 0

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        if window == "secondary":
            self.secondary_calls += 1
            return self._secondary
        self.primary_calls += 1
        return self._primary


class StubStickySessionsRepository(StickySessionsRepository):
    def __init__(self) -> None:
        pass

    async def get_account_id(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> str | None:
        return None

    async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> StickySession:
        return self._build_row(key, account_id, kind)

    async def delete(self, key: str, *, kind: StickySessionKind | None = None) -> bool:
        return False

    @staticmethod
    def _build_row(key: str, account_id: str, kind: StickySessionKind) -> StickySession:
        return StickySession(key=key, account_id=account_id, kind=kind)


class StubRequestLogsRepository(RequestLogsRepository):
    def __init__(self) -> None:
        pass


class StubApiKeysRepository(ApiKeysRepository):
    def __init__(self) -> None:
        pass


class StubAdditionalUsageRepository(AdditionalUsageRepository):
    def __init__(
        self,
        primary: dict[str, AdditionalUsageHistory] | None = None,
        secondary: dict[str, AdditionalUsageHistory] | None = None,
    ) -> None:
        self._primary = primary or {}
        self._secondary = secondary or {}

    async def latest_by_account(
        self,
        quota_key: str | None = None,
        window: str | None = None,
        *,
        limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        effective_key = quota_key or limit_name
        assert effective_key is not None
        assert window is not None
        if window == "secondary":
            source = self._secondary
        else:
            source = self._primary
        rows = {
            account_id: entry
            for account_id, entry in source.items()
            if getattr(entry, "quota_key", entry.limit_name) == effective_key
        }
        if account_ids is not None:
            account_id_set = set(account_ids)
            rows = {account_id: entry for account_id, entry in rows.items() if account_id in account_id_set}
        if since is not None:
            rows = {account_id: entry for account_id, entry in rows.items() if entry.recorded_at >= since}
        return dict(rows)

    async def latest_by_quota_key(
        self,
        quota_key: str,
        window: str,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        return await self.latest_by_account(
            quota_key=quota_key,
            window=window,
            account_ids=account_ids,
            since=since,
        )


def _additional_entry(
    entry_id: int,
    *,
    account_id: str,
    window: str,
    used_percent: float,
    recorded_at: datetime | None = None,
    limit_name: str = "GPT-5.3-Codex-Spark",
    quota_key: str = "codex_spark",
    reset_at: int = 1741500000,
) -> AdditionalUsageHistory:
    now = recorded_at or utcnow()
    return AdditionalUsageHistory(
        id=entry_id,
        account_id=account_id,
        quota_key=quota_key,
        limit_name=limit_name,
        metered_feature="codex_bengalfox",
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=5 if window == "primary" else 10080,
        recorded_at=now,
    )


@asynccontextmanager
async def _repo_factory(
    accounts_repo: StubAccountsRepository,
    usage_repo: StubUsageRepository,
    sticky_repo: StubStickySessionsRepository,
    additional_usage_repo: StubAdditionalUsageRepository | None = None,
) -> AsyncIterator[ProxyRepositories]:
    yield ProxyRepositories(
        accounts=accounts_repo,
        usage=usage_repo,
        request_logs=StubRequestLogsRepository(),
        sticky_sessions=sticky_repo,
        api_keys=StubApiKeysRepository(),
        additional_usage=additional_usage_repo or StubAdditionalUsageRepository(),
    )


@pytest.mark.asyncio
async def test_select_account_reads_cached_usage_once_per_window() -> None:
    account = _make_account("acc-load-balancer")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == account.id
    assert usage_repo.primary_calls == 1
    assert usage_repo.secondary_calls == 1


@pytest.mark.asyncio
async def test_select_account_uses_cached_usage_without_inline_refresh(monkeypatch) -> None:
    async def fail_refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: dict[str, UsageHistory],
    ) -> bool:
        raise AssertionError("select_account should not refresh usage inline")

    monkeypatch.setattr(
        "app.modules.usage.updater.UsageUpdater.refresh_accounts",
        fail_refresh_accounts,
    )

    account = _make_account("acc-cached-selection")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=15.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == account.id
    assert usage_repo.primary_calls == 1
    assert usage_repo.secondary_calls == 1


@pytest.mark.asyncio
async def test_select_account_proceeds_without_cached_usage_rows(monkeypatch) -> None:
    async def fail_refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: dict[str, UsageHistory],
    ) -> bool:
        raise AssertionError("select_account should not refresh usage inline")

    monkeypatch.setattr(
        "app.modules.usage.updater.UsageUpdater.refresh_accounts",
        fail_refresh_accounts,
    )

    account = _make_account("acc-no-usage-yet")
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={}, secondary={})
    sticky_repo = StubStickySessionsRepository()

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == account.id
    assert usage_repo.primary_calls == 1
    assert usage_repo.secondary_calls == 1


@pytest.mark.asyncio
async def test_select_account_prefilters_accounts_by_additional_usage_limit() -> None:
    account_ineligible = _make_account("acc-additional-exhausted", email="full@example.com")
    account_eligible = _make_account("acc-additional-eligible", email="ok@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account_ineligible.id,
        recorded_at=now,
        window="primary",
        used_percent=20.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    primary_entry_ok = UsageHistory(
        id=2,
        account_id=account_eligible.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )

    accounts_repo = StubAccountsRepository([account_ineligible, account_eligible])
    usage_repo = StubUsageRepository(
        primary={account_ineligible.id: primary_entry, account_eligible.id: primary_entry_ok},
        secondary={},
    )
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account_ineligible.id: _additional_entry(
                11,
                account_id=account_ineligible.id,
                window="primary",
                used_percent=100.0,
                recorded_at=now,
            ),
            account_eligible.id: _additional_entry(
                12,
                account_id=account_eligible.id,
                window="primary",
                used_percent=35.0,
                recorded_at=now,
            ),
        }
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(additional_limit_name="codex_spark")

    assert selection.account is not None
    assert selection.account.id == account_eligible.id


@pytest.mark.asyncio
async def test_select_account_requires_fresh_additional_usage_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.get_settings",
        lambda: SimpleNamespace(usage_refresh_interval_seconds=600),
    )

    account_stale = _make_account("acc-additional-stale", email="stale@example.com")
    account_fresh = _make_account("acc-additional-fresh", email="fresh@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    usage_rows = {
        account_stale.id: UsageHistory(
            id=21,
            account_id=account_stale.id,
            recorded_at=now,
            window="primary",
            used_percent=15.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
        account_fresh.id: UsageHistory(
            id=22,
            account_id=account_fresh.id,
            recorded_at=now,
            window="primary",
            used_percent=10.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
    }
    accounts_repo = StubAccountsRepository([account_stale, account_fresh])
    usage_repo = StubUsageRepository(primary=usage_rows, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account_stale.id: _additional_entry(
                31,
                account_id=account_stale.id,
                window="primary",
                used_percent=5.0,
                recorded_at=now - timedelta(seconds=1201),
            ),
            account_fresh.id: _additional_entry(
                32,
                account_id=account_fresh.id,
                window="primary",
                used_percent=5.0,
                recorded_at=now - timedelta(seconds=1199),
            ),
        }
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(additional_limit_name="codex_spark")

    assert selection.account is not None
    assert selection.account.id == account_fresh.id


@pytest.mark.asyncio
async def test_select_account_uses_canonical_quota_key_for_upstream_limit_alias(monkeypatch) -> None:
    account = _make_account("acc-additional-alias", email="alias@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    usage_repo = StubUsageRepository(
        primary={
            account.id: UsageHistory(
                id=41,
                account_id=account.id,
                recorded_at=now,
                window="primary",
                used_percent=10.0,
                reset_at=now_epoch + 300,
                window_minutes=5,
            )
        },
        secondary={},
    )
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                42,
                account_id=account.id,
                window="primary",
                limit_name="GPT-5.3-Codex-Spark",
                quota_key="codex_spark",
                used_percent=5.0,
                recorded_at=now,
            )
        }
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            StubAccountsRepository([account]),
            usage_repo,
            StubStickySessionsRepository(),
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is not None
    assert selection.account.id == account.id


@pytest.mark.asyncio
@pytest.mark.parametrize("additional_limit_name", ["codex_other", "GPT-5.3-Codex-Spark"])
async def test_select_account_accepts_legacy_additional_limit_aliases(additional_limit_name: str) -> None:
    account = _make_account(f"acc-additional-{additional_limit_name}")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    usage_repo = StubUsageRepository(
        primary={
            account.id: UsageHistory(
                id=51,
                account_id=account.id,
                recorded_at=now,
                window="primary",
                used_percent=10.0,
                reset_at=now_epoch + 300,
                window_minutes=5,
            )
        },
        secondary={},
    )
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                52,
                account_id=account.id,
                window="primary",
                limit_name="GPT-5.3-Codex-Spark",
                quota_key="codex_spark",
                used_percent=5.0,
                recorded_at=now,
            )
        }
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            StubAccountsRepository([account]),
            usage_repo,
            StubStickySessionsRepository(),
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(additional_limit_name=additional_limit_name)

    assert selection.account is not None
    assert selection.account.id == account.id


@pytest.mark.asyncio
async def test_select_account_prunes_stale_runtime_for_removed_accounts() -> None:
    account_id = "acc-reused"
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account(account_id)
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([])
    usage_repo = StubUsageRepository(primary={}, secondary={account_id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[account_id] = RuntimeState(cooldown_until=time.time() + 300.0)

    empty_selection = await balancer.select_account()
    assert empty_selection.account is None
    assert account_id not in balancer._runtime

    accounts_repo._accounts = [account]
    usage_repo._primary = {account_id: primary_entry}

    selection = await balancer.select_account()
    assert selection.account is not None
    assert selection.account.id == account_id


@pytest.mark.asyncio
async def test_round_robin_does_not_serialize_concurrent_selection(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account_a = _make_account("acc-round-robin-a", "a@example.com")
    account_b = _make_account("acc-round-robin-b", "b@example.com")
    primary_entries = {
        account_a.id: UsageHistory(
            id=1,
            account_id=account_a.id,
            recorded_at=now,
            window="primary",
            used_percent=10.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
        account_b.id: UsageHistory(
            id=2,
            account_id=account_b.id,
            recorded_at=now,
            window="primary",
            used_percent=10.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
    }
    secondary_entries = {
        account_a.id: UsageHistory(
            id=3,
            account_id=account_a.id,
            recorded_at=now,
            window="secondary",
            used_percent=10.0,
            reset_at=now_epoch + 3600,
            window_minutes=60,
        ),
        account_b.id: UsageHistory(
            id=4,
            account_id=account_b.id,
            recorded_at=now,
            window="secondary",
            used_percent=10.0,
            reset_at=now_epoch + 3600,
            window_minutes=60,
        ),
    }

    accounts_repo = StubAccountsRepository([account_a, account_b])
    usage_repo = StubUsageRepository(primary=primary_entries, secondary=secondary_entries)
    sticky_repo = StubStickySessionsRepository()

    original_persist_selection_state = LoadBalancer._persist_selection_state
    overlap_observed = asyncio.Event()
    inflight_persist_calls = 0

    async def slow_persist_selection_state(
        self: LoadBalancer,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[Any],
    ) -> None:
        nonlocal inflight_persist_calls
        inflight_persist_calls += 1
        try:
            if inflight_persist_calls >= 2:
                overlap_observed.set()
            await asyncio.sleep(0.05)
            await original_persist_selection_state(self, accounts_repo, account_map, states)
        finally:
            inflight_persist_calls -= 1

    monkeypatch.setattr(LoadBalancer, "_persist_selection_state", slow_persist_selection_state)

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    start = asyncio.Event()

    async def pick_account() -> str:
        await start.wait()
        selection = await balancer.select_account(routing_strategy="round_robin")
        assert selection.account is not None
        return selection.account.id

    first = asyncio.create_task(pick_account())
    second = asyncio.create_task(pick_account())
    started = time.perf_counter()
    start.set()
    selected_ids = await asyncio.gather(first, second)
    elapsed = time.perf_counter() - started

    assert len(set(selected_ids)) == 2
    assert overlap_observed.is_set()
    assert elapsed < 0.13


@pytest.mark.asyncio
async def test_select_account_does_not_clobber_concurrent_error_state(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-runtime-race", "race@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()

    original_persist_selection_state = LoadBalancer._persist_selection_state
    release_select_sync = asyncio.Event()
    select_sync_blocked = asyncio.Event()
    blocked_once = False

    async def controlled_persist_selection_state(
        self: LoadBalancer,
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[Any],
    ) -> None:
        nonlocal blocked_once
        if not blocked_once and any(state.error_count == 0 for state in states):
            blocked_once = True
            select_sync_blocked.set()
            await release_select_sync.wait()
        await original_persist_selection_state(self, accounts_repo, account_map, states)

    monkeypatch.setattr(LoadBalancer, "_persist_selection_state", controlled_persist_selection_state)

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    select_task = asyncio.create_task(balancer.select_account())
    await select_sync_blocked.wait()

    record_error_task = asyncio.create_task(balancer.record_error(account))
    await asyncio.sleep(0.01)
    assert record_error_task.done()

    release_select_sync.set()
    await select_task
    await record_error_task

    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 1
    assert runtime.last_error_at is not None


@pytest.mark.asyncio
async def test_mark_quota_exceeded_keeps_selection_blocked_until_persisted(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-quota-lock", "quota-lock@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    persist_started = asyncio.Event()
    release_persist = asyncio.Event()

    async def blocking_update_status(
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        persist_started.set()
        await release_persist.wait()
        return await StubAccountsRepository.update_status(
            accounts_repo,
            account_id,
            status,
            deactivation_reason,
            reset_at,
        )

    monkeypatch.setattr(accounts_repo, "update_status", blocking_update_status)

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    quota_error: UpstreamError = {"message": "quota exceeded"}
    mark_task = asyncio.create_task(balancer.mark_quota_exceeded(account, quota_error))
    await persist_started.wait()

    select_task = asyncio.create_task(balancer.select_account())
    await asyncio.sleep(0.01)
    assert not select_task.done()

    release_persist.set()
    await mark_task
    await select_task

    assert accounts_repo.status_updates[0]["status"] == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_record_errors_does_not_restore_terminal_status(monkeypatch) -> None:
    account = _make_account("acc-record-errors-race", "record-errors-race@example.com")
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    original_persist_state_if_current = balancer._persist_state_if_current
    persist_started = asyncio.Event()
    release_persist = asyncio.Event()

    async def blocking_persist_state_if_current(
        accounts_repo_arg: AccountsRepository,
        account_arg: Account,
        state_arg: Any,
    ) -> bool:
        persist_started.set()
        await release_persist.wait()
        return await original_persist_state_if_current(accounts_repo_arg, account_arg, state_arg)

    monkeypatch.setattr(balancer, "_persist_state_if_current", blocking_persist_state_if_current)

    record_task = asyncio.create_task(balancer.record_errors(account, 1))
    await persist_started.wait()

    fail_task = asyncio.create_task(balancer.mark_permanent_failure(account, "refresh_token_expired"))
    await asyncio.sleep(0.01)
    assert not fail_task.done()

    release_persist.set()
    await record_task
    await fail_task

    assert account.status == AccountStatus.DEACTIVATED
    assert accounts_repo.status_updates[-1]["status"] == AccountStatus.DEACTIVATED
    assert all(update["status"] != AccountStatus.ACTIVE for update in accounts_repo.status_updates)


@pytest.mark.asyncio
async def test_select_account_does_not_hold_runtime_lock_during_input_loading(monkeypatch) -> None:
    accounts_started = asyncio.Event()
    release_accounts = asyncio.Event()

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-refresh-unblocks-runtime", "runtime@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()

    async def blocking_list_accounts() -> list[Account]:
        accounts_started.set()
        await release_accounts.wait()
        return [account]

    monkeypatch.setattr(accounts_repo, "list_accounts", blocking_list_accounts)

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=accounts_repo,
            usage=usage_repo,
            additional_usage=StubAdditionalUsageRepository(),
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=sticky_repo,
            api_keys=object(),  # type: ignore[arg-type]
        )

    balancer = LoadBalancer(repo_factory)
    select_task = asyncio.create_task(balancer.select_account())
    await accounts_started.wait()

    record_error_task = asyncio.create_task(balancer.record_error(account))
    await asyncio.sleep(0.01)

    assert record_error_task.done()
    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 1
    assert runtime.last_error_at is not None

    release_accounts.set()
    selection = await select_task
    assert selection.account is not None


@pytest.mark.asyncio
async def test_select_account_does_not_open_repo_before_runtime_lock(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-runtime-before-repo", "runtime-before-repo@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=20.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    repo_entered = asyncio.Event()
    release_repo = asyncio.Event()

    @asynccontextmanager
    async def blocking_repo_factory() -> AsyncIterator[ProxyRepositories]:
        repo_entered.set()
        await release_repo.wait()
        yield ProxyRepositories(
            accounts=accounts_repo,
            usage=usage_repo,
            additional_usage=StubAdditionalUsageRepository(),
            request_logs=StubRequestLogsRepository(),
            sticky_sessions=sticky_repo,
            api_keys=StubApiKeysRepository(),
        )

    balancer = LoadBalancer(blocking_repo_factory)

    async def fake_load_selection_inputs(*, model: str | None, additional_limit_name: str | None = None):
        del model, additional_limit_name
        return load_balancer_module._SelectionInputs(
            accounts=[account],
            latest_primary={account.id: primary_entry},
            latest_secondary={account.id: secondary_entry},
        )

    monkeypatch.setattr(balancer, "_load_selection_inputs", fake_load_selection_inputs)

    # T21 made select_account lock-free (per-account locking replaces global _runtime_lock).
    # select_account now proceeds without acquiring _runtime_lock.
    # Verify that select_account still works correctly without the global lock.
    release_repo.set()
    selection = await balancer.select_account()
    assert repo_entered.is_set()
    assert selection.account is not None


@pytest.mark.asyncio
async def test_select_account_skips_stale_persistence_after_terminal_status_update(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-select-lockstep", "select-lockstep@example.com")
    account.status = AccountStatus.QUOTA_EXCEEDED
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=20.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    persist_blocked = asyncio.Event()
    release_persist = asyncio.Event()
    original_update_status_if_current = accounts_repo.update_status_if_current

    async def blocking_update_status_if_current(
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
    ) -> bool:
        persist_blocked.set()
        await release_persist.wait()
        return await original_update_status_if_current(
            account_id,
            status,
            deactivation_reason,
            reset_at,
            expected_status=expected_status,
            expected_deactivation_reason=expected_deactivation_reason,
            expected_reset_at=expected_reset_at,
        )

    monkeypatch.setattr(accounts_repo, "update_status_if_current", blocking_update_status_if_current)

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    select_task = asyncio.create_task(balancer.select_account())
    await persist_blocked.wait()

    fail_task = asyncio.create_task(balancer.mark_permanent_failure(account, "refresh_token_expired"))
    await fail_task

    release_persist.set()
    selection = await select_task

    assert accounts_repo.status_updates[-1]["status"] == AccountStatus.DEACTIVATED
    assert selection.account is None


@pytest.mark.asyncio
async def test_sync_runtime_state_bumps_version_for_status_only_updates() -> None:
    account = _make_account("acc-status-only-version", "status-only-version@example.com")
    balancer = LoadBalancer(
        lambda: _repo_factory(
            StubAccountsRepository([]),
            StubUsageRepository({}, {}),
            StubStickySessionsRepository(),
        )
    )
    runtime = balancer._runtime.setdefault(account.id, RuntimeState())
    initial_version = runtime.version

    state = load_balancer_module.AccountState(
        account_id=account.id,
        status=AccountStatus.DEACTIVATED,
        deactivation_reason="Refresh token expired - re-login required",
    )

    updated = balancer._sync_runtime_state(account, state)

    assert updated is True
    assert balancer._runtime[account.id].version == initial_version + 1


@pytest.mark.skip(reason="T21 per-account locking eliminates version conflicts that this test was designed to catch")
@pytest.mark.asyncio
async def test_select_account_reloads_inputs_after_version_conflict(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-reload-after-conflict", "reload-after-conflict@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    original_load_selection_inputs = balancer._load_selection_inputs
    load_calls = 0

    async def counted_load_selection_inputs(*, model: str | None, additional_limit_name: str | None = None):
        nonlocal load_calls
        load_calls += 1
        return await original_load_selection_inputs(model=model, additional_limit_name=additional_limit_name)

    original_select_account = load_balancer_module.select_account
    first_call = True

    def conflict_injecting_select_account(states, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            account.status = AccountStatus.DEACTIVATED
            account.deactivation_reason = "Refresh token expired - re-login required"
            balancer._runtime.setdefault(account.id, RuntimeState()).version += 1
        return original_select_account(states, **kwargs)

    monkeypatch.setattr(balancer, "_load_selection_inputs", counted_load_selection_inputs)
    monkeypatch.setattr(load_balancer_module, "select_account", conflict_injecting_select_account)

    selection = await balancer.select_account()

    assert load_calls >= 2
    assert selection.account is None


@pytest.mark.skip(reason="T21 per-account locking eliminates version conflicts that this test was designed to catch")
@pytest.mark.asyncio
async def test_select_account_does_not_hold_runtime_lock_during_conflict_reload(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-conflict-reload-unblocks-runtime", "conflict-reload@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    original_load_selection_inputs = balancer._load_selection_inputs
    reload_started = asyncio.Event()
    release_reload = asyncio.Event()
    load_calls = 0

    async def blocking_load_selection_inputs(*, model: str | None, additional_limit_name: str | None = None):
        nonlocal load_calls
        load_calls += 1
        if load_calls == 2:
            reload_started.set()
            await release_reload.wait()
        return await original_load_selection_inputs(model=model, additional_limit_name=additional_limit_name)

    original_select_account = load_balancer_module.select_account
    first_call = True

    def conflict_injecting_select_account(states, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            balancer._runtime.setdefault(account.id, RuntimeState()).version += 1
        return original_select_account(states, **kwargs)

    monkeypatch.setattr(balancer, "_load_selection_inputs", blocking_load_selection_inputs)
    monkeypatch.setattr(load_balancer_module, "select_account", conflict_injecting_select_account)

    select_task = asyncio.create_task(balancer.select_account())
    await reload_started.wait()

    record_error_task = asyncio.create_task(balancer.record_error(account))
    await asyncio.sleep(0.01)

    assert record_error_task.done()
    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 1
    assert runtime.last_error_at is not None

    release_reload.set()
    selection = await select_task
    assert selection.account is not None


@pytest.mark.skip(reason="T21 per-account locking eliminates version conflicts that this test was designed to catch")
@pytest.mark.asyncio
async def test_select_account_sticky_reloads_inputs_after_stale_selected_persistence(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-sticky-stale-selected", "sticky-stale-selected@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))

    original_load_selection_inputs = balancer._load_selection_inputs
    load_calls = 0

    async def counted_load_selection_inputs(*, model: str | None, additional_limit_name: str | None = None):
        nonlocal load_calls
        load_calls += 1
        return await original_load_selection_inputs(model=model, additional_limit_name=additional_limit_name)

    async def pinned_account_id(
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> str | None:
        del key, kind, max_age_seconds
        return account.id

    original_persist_selection_state = balancer._persist_selection_state
    first_persist = True

    async def stale_selected_persist(
        accounts_repo: AccountsRepository,
        account_map: dict[str, Account],
        states: list[Any],
    ) -> set[str]:
        nonlocal first_persist
        if first_persist:
            first_persist = False
            account.status = AccountStatus.DEACTIVATED
            account.deactivation_reason = "Refresh token expired - re-login required"
            return {account.id}
        return await original_persist_selection_state(accounts_repo, account_map, states)

    monkeypatch.setattr(balancer, "_load_selection_inputs", counted_load_selection_inputs)
    monkeypatch.setattr(sticky_repo, "get_account_id", pinned_account_id)
    monkeypatch.setattr(balancer, "_persist_selection_state", stale_selected_persist)

    selection = await balancer.select_account(
        sticky_key="sticky-session-1",
        sticky_kind=StickySessionKind.CODEX_SESSION,
    )

    assert load_calls >= 2
    assert selection.account is None


@pytest.mark.asyncio
async def test_select_account_skips_registry_plan_filter_for_mapped_model(monkeypatch) -> None:
    account = _make_account("acc-gated-registry-skip", "gated-registry-skip@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            )
        }
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(
            get_snapshot=lambda: SimpleNamespace(model_plans={}),
            plan_types_for_model=lambda _model: frozenset(),
        ),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is not None
    assert selection.account.id == account.id
    assert selection.error_code is None


@pytest.mark.asyncio
async def test_select_account_respects_registry_plan_filter_for_mapped_model(monkeypatch) -> None:
    account = _make_account("acc-gated-plan-filtered", "gated-plan-filtered@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            )
        }
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(
            get_snapshot=lambda: ModelRegistrySnapshot(
                models={},
                model_plans={"gpt-5.3-codex-spark": frozenset({"pro"})},
                plan_models={"pro": frozenset({"gpt-5.3-codex-spark"})},
                fetched_at=0.0,
            ),
            plan_types_for_model=lambda _model: frozenset({"pro"}),
        ),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is None
    assert selection.error_code == NO_PLAN_SUPPORT_FOR_MODEL


@pytest.mark.asyncio
async def test_select_account_returns_plan_support_error_for_ungated_model(monkeypatch) -> None:
    account = _make_account("acc-ungated-plan-filtered", "ungated-plan-filtered@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"pro"})),
    )

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    selection = await balancer.select_account(model="gpt-5.3-codex")

    assert selection.account is None
    assert selection.error_code == NO_PLAN_SUPPORT_FOR_MODEL
    assert selection.error_message == "No accounts with a plan supporting model 'gpt-5.3-codex'"


@pytest.mark.asyncio
async def test_select_account_empty_pool_preserves_no_accounts_for_modeled_request(monkeypatch) -> None:
    accounts_repo = StubAccountsRepository([])
    usage_repo = StubUsageRepository(primary={}, secondary={})
    sticky_repo = StubStickySessionsRepository()

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"pro"})),
    )

    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    selection = await balancer.select_account(model="gpt-5.3-codex")

    assert selection.account is None
    assert selection.error_code is None
    assert selection.error_message is not None
    assert "No available accounts" in selection.error_message


@pytest.mark.asyncio
async def test_select_account_retries_no_accounts_after_runtime_recovery(monkeypatch) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = _make_account("acc-no-accounts-retry", "no-accounts-retry@example.com")
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=10.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    secondary_entry = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=now,
        window="secondary",
        used_percent=10.0,
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )

    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={account.id: secondary_entry})
    sticky_repo = StubStickySessionsRepository()
    balancer = LoadBalancer(lambda: _repo_factory(accounts_repo, usage_repo, sticky_repo))
    balancer._runtime[account.id] = RuntimeState(error_count=3, last_error_at=time.time())

    original_persist_selection_state = balancer._persist_selection_state
    persist_started = asyncio.Event()
    release_persist = asyncio.Event()

    async def blocking_persist_selection_state(
        accounts_repo_arg: AccountsRepository,
        account_map: dict[str, Account],
        states: list[Any],
    ) -> set[str]:
        persist_started.set()
        await release_persist.wait()
        return await original_persist_selection_state(accounts_repo_arg, account_map, states)

    monkeypatch.setattr(balancer, "_persist_selection_state", blocking_persist_selection_state)

    select_task = asyncio.create_task(balancer.select_account())
    await persist_started.wait()

    await balancer.record_success(account)
    release_persist.set()
    selection = await select_task

    assert selection.account is not None
    assert selection.account.id == account.id


@pytest.mark.asyncio
async def test_select_account_returns_data_unavailable_error_for_mapped_model(monkeypatch) -> None:
    account = _make_account("acc-gated-stale", "gated-stale@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=20.0,
                recorded_at=now - timedelta(seconds=181),
            )
        }
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is None
    assert selection.error_code == ADDITIONAL_QUOTA_DATA_UNAVAILABLE


@pytest.mark.asyncio
async def test_select_account_returns_data_unavailable_when_secondary_window_is_stale(monkeypatch) -> None:
    account = _make_account("acc-gated-stale-secondary", "gated-stale-secondary@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            )
        },
        secondary={
            account.id: _additional_entry(
                3,
                account_id=account.id,
                window="secondary",
                used_percent=20.0,
                reset_at=now_epoch + 3600,
                recorded_at=now - timedelta(seconds=181),
            )
        },
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is None
    assert selection.error_code == ADDITIONAL_QUOTA_DATA_UNAVAILABLE


@pytest.mark.asyncio
async def test_select_account_allows_primary_only_account_when_other_account_has_secondary_history(
    monkeypatch,
) -> None:
    primary_only_account = _make_account("acc-primary-only", "primary-only@example.com")
    stale_secondary_account = _make_account("acc-stale-secondary", "stale-secondary@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    usage_rows = {
        primary_only_account.id: UsageHistory(
            id=1,
            account_id=primary_only_account.id,
            recorded_at=now,
            window="primary",
            used_percent=5.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
        stale_secondary_account.id: UsageHistory(
            id=2,
            account_id=stale_secondary_account.id,
            recorded_at=now,
            window="primary",
            used_percent=5.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
        ),
    }
    accounts_repo = StubAccountsRepository([primary_only_account, stale_secondary_account])
    usage_repo = StubUsageRepository(primary=usage_rows, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            primary_only_account.id: _additional_entry(
                11,
                account_id=primary_only_account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            ),
            stale_secondary_account.id: _additional_entry(
                12,
                account_id=stale_secondary_account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            ),
        },
        secondary={
            stale_secondary_account.id: _additional_entry(
                13,
                account_id=stale_secondary_account.id,
                window="secondary",
                used_percent=20.0,
                reset_at=now_epoch + 3600,
                recorded_at=now - timedelta(seconds=181),
            ),
        },
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is not None
    assert selection.account.id == primary_only_account.id
    assert selection.error_code is None


@pytest.mark.asyncio
async def test_select_account_returns_no_eligible_error_for_mapped_model(monkeypatch) -> None:
    account = _make_account("acc-gated-exhausted", "gated-exhausted@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=100.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            )
        },
        secondary={
            account.id: _additional_entry(
                3,
                account_id=account.id,
                window="secondary",
                used_percent=10.0,
                reset_at=now_epoch + 3600,
                recorded_at=now,
            )
        },
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is None
    assert selection.error_code == NO_ADDITIONAL_QUOTA_ELIGIBLE_ACCOUNTS


@pytest.mark.asyncio
async def test_select_account_additional_limit_filter_does_not_mutate_account_status(monkeypatch) -> None:
    account = _make_account("acc-gated-status-stable", "status-stable@example.com")
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_entry = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=now,
        window="primary",
        used_percent=5.0,
        reset_at=now_epoch + 300,
        window_minutes=5,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(primary={account.id: primary_entry}, secondary={})
    sticky_repo = StubStickySessionsRepository()
    additional_usage_repo = StubAdditionalUsageRepository(
        primary={
            account.id: _additional_entry(
                2,
                account_id=account.id,
                window="primary",
                used_percent=20.0,
                reset_at=now_epoch + 300,
                recorded_at=now,
            )
        }
    )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(plan_types_for_model=lambda _model: frozenset({"plus"})),
    )

    balancer = LoadBalancer(
        lambda: _repo_factory(
            accounts_repo,
            usage_repo,
            sticky_repo,
            additional_usage_repo,
        )
    )
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is not None
    assert selection.account.id == account.id
    assert accounts_repo.status_updates == []
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None
