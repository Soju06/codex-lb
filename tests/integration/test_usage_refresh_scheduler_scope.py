from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Collection
from typing import cast

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _account(
    account_id: str,
    *,
    status: AccountStatus,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        reset_at=reset_at,
        blocked_at=blocked_at,
        limit_warmup_enabled=True,
    )


@pytest.mark.asyncio
async def test_scheduler_repository_path_scopes_selected_account_history_and_followups(
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db_setup
    now = utcnow()
    selected = _account(
        "acc_a",
        status=AccountStatus.RATE_LIMITED,
        reset_at=int(time.time()) + 3600,
        blocked_at=int(time.time()),
    )
    unrelated = _account("acc_b", status=AccountStatus.ACTIVE)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(selected)
        await accounts_repo.upsert(unrelated)
        for account, used_percent in ((selected, 10.0), (unrelated, 90.0)):
            await usage_repo.add_entry(
                account.id,
                used_percent,
                window="primary",
                recorded_at=now,
                reset_at=int(time.time()) + 3600,
                window_minutes=300,
            )
            await usage_repo.add_entry(
                account.id,
                used_percent,
                window="secondary",
                recorded_at=now,
                reset_at=int(time.time()) + 7200,
                window_minutes=10_080,
            )
            await usage_repo.add_entry(
                account.id,
                used_percent,
                window="monthly",
                recorded_at=now,
                reset_at=int(time.time()) + 10_800,
                window_minutes=43_200,
            )

    query_scopes: list[tuple[str | None, tuple[str, ...] | None]] = []
    updater_calls: list[tuple[list[str], set[str]]] = []
    warmup_calls: list[dict[str, object]] = []
    original_latest_by_account = UsageRepository.latest_by_account

    async def _tracked_latest_by_account(
        self: UsageRepository,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        query_scopes.append((window, tuple(account_ids) if account_ids is not None else None))
        return await original_latest_by_account(self, window, account_ids=account_ids)

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            return await fn()

    class _Updater:
        async def refresh_accounts(
            self,
            accounts: list[Account],
            latest_usage: dict[str, UsageHistory],
        ) -> bool:
            updater_calls.append(([account.id for account in accounts], set(latest_usage)))
            return True

    class _WarmupService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run_after_usage_refresh(self, **kwargs: object) -> None:
            warmup_calls.append(kwargs)

    monkeypatch.setattr(UsageRepository, "latest_by_account", _tracked_latest_by_account)
    monkeypatch.setattr(refresh_scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(refresh_scheduler_module, "build_background_usage_updater", lambda: _Updater())
    monkeypatch.setattr(refresh_scheduler_module, "LimitWarmupService", _WarmupService)

    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)

    assert await scheduler._refresh_once() == 30.0
    assert updater_calls == [([selected.id], {selected.id})]
    assert query_scopes == [
        ("primary", (selected.id,)),
        ("secondary", (selected.id,)),
        ("primary", (selected.id,)),
        ("secondary", (selected.id,)),
        ("primary", (selected.id,)),
        ("secondary", (selected.id,)),
        ("monthly", (selected.id,)),
    ]
    assert len(warmup_calls) == 1
    assert [account.id for account in cast("list[Account]", warmup_calls[0]["accounts"])] == [selected.id]
    assert {account.id for account in cast("list[Account]", warmup_calls[0]["stagger_accounts"])} == {
        selected.id,
        unrelated.id,
    }
    for snapshot_name in ("before_primary", "before_secondary", "after_primary", "after_secondary"):
        assert set(cast("dict[str, UsageHistory]", warmup_calls[0][snapshot_name])) <= {selected.id}

    async with SessionLocal() as session:
        persisted_selected = await AccountsRepository(session).get_by_id(selected.id)
        persisted_unrelated = await AccountsRepository(session).get_by_id(unrelated.id)
    assert persisted_selected is not None
    assert persisted_selected.status == AccountStatus.RATE_LIMITED
    assert persisted_unrelated is not None
    assert persisted_unrelated.status == AccountStatus.ACTIVE
