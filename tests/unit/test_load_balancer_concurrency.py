from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories

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

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        if window == "secondary":
            return self._secondary
        return self._primary


class _StubStickySessionsRepository:
    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        del args, kwargs
        return None

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True


@asynccontextmanager
async def _repo_factory(
    accounts_repo: _StubAccountsRepository,
    usage_repo: _StubUsageRepository,
) -> AsyncIterator[ProxyRepositories]:
    yield ProxyRepositories(
        accounts=cast(Any, accounts_repo),
        usage=cast(Any, usage_repo),
        request_logs=object(),  # type: ignore[arg-type]
        sticky_sessions=cast(Any, _StubStickySessionsRepository()),
        api_keys=object(),  # type: ignore[arg-type]
        additional_usage=object(),  # type: ignore[arg-type]
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
