from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState
from app.modules.proxy.repo_bundle import ProxyRepositories

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


class StubAccountsRepository:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts
        self.status_updates: list[dict[str, Any]] = []

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        self.status_updates.append(
            {
                "account_id": account_id,
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
        )
        return True


class StubUsageRepository:
    def __init__(
        self,
        *,
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


class StubStickySessionsRepository:
    async def get_account_id(self, key: str) -> str | None:
        return None

    async def upsert(self, key: str, account_id: str) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None


@pytest.mark.asyncio
async def test_select_account_skips_latest_primary_requery_when_not_refreshed(monkeypatch) -> None:
    async def stub_refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: dict[str, UsageHistory],
    ) -> bool:
        return False

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.UsageUpdater.refresh_accounts",
        stub_refresh_accounts,
    )

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

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=accounts_repo,  # type: ignore[arg-type]
            usage=usage_repo,  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=sticky_repo,  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
        )

    balancer = LoadBalancer(repo_factory)
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == account.id
    assert usage_repo.primary_calls == 1
    assert usage_repo.secondary_calls == 1


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

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=accounts_repo,  # type: ignore[arg-type]
            usage=usage_repo,  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=sticky_repo,  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
        )

    balancer = LoadBalancer(repo_factory)
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
async def test_round_robin_serializes_concurrent_selection(monkeypatch) -> None:
    async def stub_refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: dict[str, UsageHistory],
    ) -> bool:
        return False

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.UsageUpdater.refresh_accounts",
        stub_refresh_accounts,
    )

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

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=accounts_repo,  # type: ignore[arg-type]
            usage=usage_repo,  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=sticky_repo,  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
        )

    original_sync = LoadBalancer._sync_state

    async def slow_sync(
        self: LoadBalancer,
        accounts_repo: AccountsRepository,
        account: Account,
        state,
    ) -> None:
        await asyncio.sleep(0.05)
        await original_sync(self, accounts_repo, account, state)

    monkeypatch.setattr(LoadBalancer, "_sync_state", slow_sync)

    balancer = LoadBalancer(repo_factory)
    start = asyncio.Event()

    async def pick_account() -> str:
        await start.wait()
        selection = await balancer.select_account(routing_strategy="round_robin")
        assert selection.account is not None
        return selection.account.id

    first = asyncio.create_task(pick_account())
    second = asyncio.create_task(pick_account())
    start.set()
    selected_ids = await asyncio.gather(first, second)

    assert len(set(selected_ids)) == 2


@pytest.mark.asyncio
async def test_select_account_does_not_clobber_concurrent_error_state(monkeypatch) -> None:
    async def stub_refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: dict[str, UsageHistory],
    ) -> bool:
        return False

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.UsageUpdater.refresh_accounts",
        stub_refresh_accounts,
    )

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

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=accounts_repo,  # type: ignore[arg-type]
            usage=usage_repo,  # type: ignore[arg-type]
            request_logs=object(),  # type: ignore[arg-type]
            sticky_sessions=sticky_repo,  # type: ignore[arg-type]
            api_keys=object(),  # type: ignore[arg-type]
        )

    original_sync = LoadBalancer._sync_state
    release_select_sync = asyncio.Event()
    select_sync_blocked = asyncio.Event()
    blocked_once = False

    async def controlled_sync(
        self: LoadBalancer,
        accounts_repo: AccountsRepository,
        account: Account,
        state: Any,
    ) -> None:
        nonlocal blocked_once
        if not blocked_once and state.error_count == 0:
            blocked_once = True
            select_sync_blocked.set()
            await release_select_sync.wait()
        await original_sync(self, accounts_repo, account, state)

    monkeypatch.setattr(LoadBalancer, "_sync_state", controlled_sync)

    balancer = LoadBalancer(repo_factory)
    select_task = asyncio.create_task(balancer.select_account())
    await select_sync_blocked.wait()

    record_error_task = asyncio.create_task(balancer.record_error(account))
    await asyncio.sleep(0.01)
    assert not record_error_task.done()

    release_select_sync.set()
    await select_task
    await record_error_task

    runtime = balancer._runtime[account.id]
    assert runtime.error_count == 1
    assert runtime.last_error_at is not None
