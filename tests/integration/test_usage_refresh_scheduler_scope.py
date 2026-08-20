from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime, timezone
from typing import cast

import pytest
from sqlalchemy.orm.exc import DetachedInstanceError

from app.core.crypto import TokenEncryptor
from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import LimitWarmupSendResult
from app.modules.settings.repository import SettingsRepository
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
async def test_background_accounts_repo_keeps_loaded_account_usable_after_context(db_setup) -> None:
    del db_setup
    account = _account("acc_detached", status=AccountStatus.RATE_LIMITED)
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)

    async with refresh_scheduler_module._background_accounts_repo() as accounts_repo:
        loaded = await accounts_repo.get_by_id_fresh(account.id)

    assert loaded is not None
    try:
        actual = (loaded.status, loaded.limit_warmup_enabled)
    except DetachedInstanceError:
        pytest.fail("background account expired when its read transaction closed")
    assert actual == (AccountStatus.RATE_LIMITED, True)


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
        ("monthly", (selected.id,)),
        ("primary", (selected.id,)),
        ("secondary", (selected.id,)),
        ("monthly", (selected.id,)),
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
    assert warmup_calls[0]["previous_plan_types"] == {selected.id: "plus"}
    for snapshot_name in ("before_primary", "before_secondary", "after_primary", "after_secondary"):
        assert set(cast("dict[str, UsageHistory]", warmup_calls[0][snapshot_name])) <= {selected.id}

    async with SessionLocal() as session:
        persisted_selected = await AccountsRepository(session).get_by_id(selected.id)
        persisted_unrelated = await AccountsRepository(session).get_by_id(unrelated.id)
    assert persisted_selected is not None
    assert persisted_selected.status == AccountStatus.RATE_LIMITED
    assert persisted_unrelated is not None
    assert persisted_unrelated.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_scheduler_recovers_rate_limited_free_before_monthly_reset_warmup(
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db_setup
    now = int(time.time())
    primary_reset_at = now + 8 * 24 * 60 * 60
    before_reset_at = primary_reset_at
    blocked_at = now - 5 * 24 * 60 * 60
    before_recorded_at = datetime.fromtimestamp(now - 120, timezone.utc).replace(tzinfo=None)
    after_recorded_epoch = now - 60
    after_recorded_at = datetime.fromtimestamp(after_recorded_epoch, timezone.utc).replace(tzinfo=None)
    after_reset_at = after_recorded_epoch + 43_200 * 60
    account = _account(
        "acc_free",
        status=AccountStatus.RATE_LIMITED,
        reset_at=primary_reset_at,
        blocked_at=blocked_at,
    )
    account.plan_type = "free"

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        await UsageRepository(session).add_entry(
            account.id,
            100.0,
            window="primary",
            recorded_at=datetime.fromtimestamp(blocked_at, timezone.utc).replace(tzinfo=None),
            reset_at=primary_reset_at,
            window_minutes=300,
        )
        await UsageRepository(session).add_entry(
            account.id,
            100.0,
            window="monthly",
            recorded_at=before_recorded_at,
            reset_at=before_reset_at,
            window_minutes=43_200,
        )
        await SettingsRepository(session).update(
            limit_warmup_enabled=True,
            limit_warmup_windows="secondary",
            limit_warmup_model="gpt-5.1-codex-mini",
        )

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            return await fn()

    class _Updater:
        async def refresh_accounts(
            self,
            accounts: list[Account],
            latest_usage: dict[str, UsageHistory],
        ) -> bool:
            assert [candidate.id for candidate in accounts] == [account.id]
            assert set(latest_usage) == {account.id}
            async with SessionLocal() as session:
                await UsageRepository(session).add_entry(
                    account.id,
                    0.0,
                    window="monthly",
                    recorded_at=after_recorded_at,
                    reset_at=after_reset_at,
                    window_minutes=43_200,
                )
            return True

    class _Sender:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def send(
            self,
            target: Account,
            *,
            model: str,
            prompt: str,
        ) -> LimitWarmupSendResult:
            async with SessionLocal() as session:
                persisted = await AccountsRepository(session).get_by_id(target.id)
            assert persisted is not None
            assert (persisted.status, persisted.reset_at, persisted.blocked_at) == (
                AccountStatus.ACTIVE,
                None,
                None,
            )
            self.calls.append((target.id, model))
            return LimitWarmupSendResult(
                request_id="warmup-monthly",
                success=True,
                latency_ms=12,
            )

    sender = _Sender()
    monkeypatch.setattr(refresh_scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(refresh_scheduler_module, "build_background_usage_updater", lambda: _Updater())
    monkeypatch.setattr(refresh_scheduler_module, "StreamingLimitWarmupSender", lambda *_args, **_kwargs: sender)

    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)

    assert await scheduler._refresh_once() == 60.0
    assert sender.calls == [(account.id, "gpt-5.1-codex-mini")]
    async with SessionLocal() as session:
        persisted_account = await AccountsRepository(session).get_by_id(account.id)
        attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
    assert persisted_account is not None
    assert (persisted_account.status, persisted_account.reset_at, persisted_account.blocked_at) == (
        AccountStatus.ACTIVE,
        None,
        None,
    )
    assert (attempt.window, attempt.reset_at, attempt.status) == ("monthly", after_reset_at, "succeeded")


@pytest.mark.asyncio
async def test_scheduler_warms_confirmed_paid_to_free_plan_transition(
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db_setup
    account = _account("acc_paid_to_free", status=AccountStatus.ACTIVE)
    prior_reset_at = int(time.time()) + 7 * 24 * 60 * 60
    monthly_reset_at = int(time.time()) + 30 * 24 * 60 * 60

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        await UsageRepository(session).add_entry(
            account.id,
            100.0,
            window="secondary",
            recorded_at=utcnow(),
            reset_at=prior_reset_at,
            window_minutes=10_080,
        )
        await SettingsRepository(session).update(
            limit_warmup_enabled=True,
            limit_warmup_windows="secondary",
            limit_warmup_model="gpt-5.1-codex-mini",
        )

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            return await fn()

    class _Updater:
        async def refresh_accounts(
            self,
            accounts: list[Account],
            latest_usage: dict[str, UsageHistory],
        ) -> bool:
            assert [candidate.id for candidate in accounts] == [account.id]
            assert accounts[0].plan_type == "plus"
            accounts[0].plan_type = "free"
            async with SessionLocal() as session:
                persisted = await AccountsRepository(session).get_by_id(account.id)
                assert persisted is not None
                persisted.plan_type = "free"
                await session.commit()
                await UsageRepository(session).add_entry(
                    account.id,
                    0.0,
                    window="monthly",
                    recorded_at=utcnow(),
                    reset_at=monthly_reset_at,
                    window_minutes=43_200,
                )
            return True

    class _Sender:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def send(
            self,
            target: Account,
            *,
            model: str,
            prompt: str,
        ) -> LimitWarmupSendResult:
            self.calls.append((target.id, model))
            return LimitWarmupSendResult(request_id="warmup-plan-transition", success=True, latency_ms=12)

    sender = _Sender()
    monkeypatch.setattr(refresh_scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(refresh_scheduler_module, "build_background_usage_updater", lambda: _Updater())
    monkeypatch.setattr(refresh_scheduler_module, "StreamingLimitWarmupSender", lambda *_args, **_kwargs: sender)

    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)

    assert await scheduler._refresh_once() == 60.0
    assert sender.calls == [(account.id, "gpt-5.1-codex-mini")]
    async with SessionLocal() as session:
        persisted_account = await AccountsRepository(session).get_by_id(account.id)
        attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
    assert persisted_account is not None
    assert persisted_account.plan_type == "free"
    assert (attempt.window, attempt.reset_at, attempt.status) == ("monthly", monthly_reset_at, "succeeded")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_attempt", [False, True], ids=["warmup-new", "warmup-deduped"])
@pytest.mark.parametrize(
    "current_pair_confirms_unanchored",
    [False, True],
    ids=["persisted-only", "unanchored-current"],
)
async def test_scheduler_restart_uses_anchored_persisted_evidence(
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
    existing_attempt: bool,
    current_pair_confirms_unanchored: bool,
) -> None:
    del db_setup
    now = int(time.time())
    blocked_at = now - 5 * 24 * 60 * 60
    legacy_reset_at = now + 8 * 24 * 60 * 60
    transition_recorded_at = now - 60 * 60
    transition_reset_at = transition_recorded_at + 43_200 * 60
    current_before_recorded_at = now - 120
    current_before_reset_at = now - 90
    latest_recorded_at = now - 60
    latest_reset_at = latest_recorded_at + 43_200 * 60
    expected_attempt_reset_at = latest_reset_at if current_pair_confirms_unanchored else transition_reset_at
    account = _account(
        "acc_free_restart",
        status=AccountStatus.RATE_LIMITED,
        reset_at=legacy_reset_at,
        blocked_at=blocked_at,
    )
    account.plan_type = "free"

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account.id,
            100.0,
            window="monthly",
            recorded_at=datetime.fromtimestamp(blocked_at, timezone.utc).replace(tzinfo=None),
            reset_at=legacy_reset_at,
            window_minutes=43_200,
        )
        await usage_repo.add_entry(
            account.id,
            100.0,
            window="monthly",
            recorded_at=datetime.fromtimestamp(blocked_at + 60, timezone.utc).replace(tzinfo=None),
            reset_at=legacy_reset_at,
            window_minutes=43_200,
        )
        sliding_sample_count = 240
        sliding_span = transition_recorded_at - blocked_at - 120
        for index in range(1, sliding_sample_count + 1):
            recorded_at = blocked_at + 60 + (sliding_span * index // sliding_sample_count)
            await usage_repo.add_entry(
                account.id,
                100.0,
                window="monthly",
                recorded_at=datetime.fromtimestamp(recorded_at, timezone.utc).replace(tzinfo=None),
                reset_at=legacy_reset_at + index * 60,
                window_minutes=43_200,
            )
        await usage_repo.add_entry(
            account.id,
            0.0,
            window="monthly",
            recorded_at=datetime.fromtimestamp(transition_recorded_at, timezone.utc).replace(tzinfo=None),
            reset_at=transition_reset_at,
            window_minutes=43_200,
        )
        if current_pair_confirms_unanchored:
            await usage_repo.add_entry(
                account.id,
                40.0,
                window="monthly",
                recorded_at=datetime.fromtimestamp(current_before_recorded_at, timezone.utc).replace(tzinfo=None),
                reset_at=current_before_reset_at,
                window_minutes=43_200,
            )
        await SettingsRepository(session).update(
            limit_warmup_enabled=True,
            limit_warmup_windows="secondary",
            limit_warmup_model="gpt-5.1-codex-mini",
        )
        if existing_attempt:
            attempt = await LimitWarmupRepository(session).try_create_attempt(
                account_id=account.id,
                window="monthly",
                reset_at=expected_attempt_reset_at,
                model="gpt-5.1-codex-mini",
                attempted_at=utcnow(),
                status="succeeded",
                reset_at_tolerance_seconds=5,
            )
            assert attempt is not None

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            return await fn()

    class _Updater:
        async def refresh_accounts(
            self,
            accounts: list[Account],
            latest_usage: dict[str, UsageHistory],
        ) -> bool:
            assert [candidate.id for candidate in accounts] == [account.id]
            async with SessionLocal() as session:
                await UsageRepository(session).add_entry(
                    account.id,
                    0.0,
                    window="monthly",
                    recorded_at=datetime.fromtimestamp(latest_recorded_at, timezone.utc).replace(tzinfo=None),
                    reset_at=latest_reset_at,
                    window_minutes=43_200,
                )
            return True

    class _Sender:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def send(self, target: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
            del model, prompt
            async with SessionLocal() as session:
                persisted = await AccountsRepository(session).get_by_id(target.id)
            assert persisted is not None
            assert persisted.status == AccountStatus.ACTIVE
            self.calls.append(target.id)
            return LimitWarmupSendResult(request_id="warmup-restart", success=True, latency_ms=12)

    sender = _Sender()
    monkeypatch.setattr(refresh_scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(refresh_scheduler_module, "build_background_usage_updater", lambda: _Updater())
    monkeypatch.setattr(refresh_scheduler_module, "StreamingLimitWarmupSender", lambda *_args, **_kwargs: sender)

    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)

    assert await scheduler._refresh_once() == 60.0
    assert sender.calls == ([] if existing_attempt else [account.id])
    async with SessionLocal() as session:
        persisted = await AccountsRepository(session).get_by_id(account.id)
        attempt = (await LimitWarmupRepository(session).latest_by_account([account.id]))[account.id]
    assert persisted is not None
    assert (persisted.status, persisted.reset_at, persisted.blocked_at) == (AccountStatus.ACTIVE, None, None)
    assert (attempt.window, attempt.reset_at, attempt.status) == (
        "monthly",
        expected_attempt_reset_at,
        "succeeded",
    )
