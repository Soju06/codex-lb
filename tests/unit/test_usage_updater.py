from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key
from app.modules.usage.updater import UsageUpdater

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_refresh_cache():
    """Clear the module-level freshness cache between tests."""
    usage_updater_module._clear_usage_refresh_state()
    yield
    usage_updater_module._clear_usage_refresh_state()


@pytest.mark.asyncio
async def test_clear_usage_refresh_state_clears_singleflight_cache() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory():
        started.set()
        await release.wait()
        return usage_updater_module.AccountRefreshResult(usage_written=False)

    first = asyncio.create_task(usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.run("acc_singleflight_clear", factory))
    await started.wait()
    usage_updater_module._clear_usage_refresh_state()
    assert usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT._inflight == {}
    release.set()
    await first


@pytest.mark.asyncio
async def test_usage_refresh_singleflight_cancel_all_cancels_inflight_task() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def factory():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.run("acc_cancel", factory))
    await started.wait()

    await usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT._inflight == {}


@pytest.mark.asyncio
async def test_usage_refresh_scheduler_stop_cancels_inflight_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)
    run_loop_task = asyncio.create_task(asyncio.sleep(3600))
    scheduler._task = run_loop_task
    cancel_all = asyncio.Event()

    async def _cancel_all() -> None:
        cancel_all.set()

    monkeypatch.setattr(
        refresh_scheduler_module.usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT,
        "cancel_all",
        _cancel_all,
    )

    await scheduler.stop()

    assert cancel_all.is_set()
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_usage_refresh_scheduler_stop_cancels_inflight_singleflight_without_scheduler_task() -> None:
    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def factory():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.run("acc_stop_no_task", factory))
    await started.wait()

    await scheduler.stop()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT._inflight == {}


@dataclass(frozen=True, slots=True)
class UsageEntry:
    account_id: str
    used_percent: float
    input_tokens: int | None
    output_tokens: int | None
    recorded_at: datetime | None
    window: str | None
    reset_at: int | None
    window_minutes: int | None
    credits_has: bool | None
    credits_unlimited: bool | None
    credits_balance: float | None


class StubUsageRepository:
    def __init__(self, *, return_rows: bool = False) -> None:
        self.entries: list[UsageEntry] = []
        self._return_rows = return_rows
        self._next_id = 1

    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        for entry in reversed(self.entries):
            normalized_window = entry.window or "primary"
            expected_window = window or "primary"
            if entry.account_id == account_id and normalized_window == expected_window:
                return UsageHistory(
                    id=self._next_id,
                    account_id=entry.account_id,
                    used_percent=entry.used_percent,
                    input_tokens=entry.input_tokens,
                    output_tokens=entry.output_tokens,
                    recorded_at=entry.recorded_at or datetime.now(tz=timezone.utc),
                    window=entry.window,
                    reset_at=entry.reset_at,
                    window_minutes=entry.window_minutes,
                    credits_has=entry.credits_has,
                    credits_unlimited=entry.credits_unlimited,
                    credits_balance=entry.credits_balance,
                )
        return None

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory | None:
        self.entries.append(
            UsageEntry(
                account_id=account_id,
                used_percent=used_percent,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                recorded_at=recorded_at,
                window=window,
                reset_at=reset_at,
                window_minutes=window_minutes,
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
        )
        if not self._return_rows:
            return None
        entry = UsageHistory(
            id=self._next_id,
            account_id=account_id,
            used_percent=used_percent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            recorded_at=recorded_at or datetime.now(tz=timezone.utc),
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
        )
        self._next_id += 1
        return entry


@dataclass(frozen=True, slots=True)
class AdditionalUsageEntry:
    account_id: str
    limit_name: str
    metered_feature: str
    window: str
    used_percent: float
    reset_at: int | None
    window_minutes: int | None
    quota_key: str | None = None


class StubAdditionalUsageRepository:
    def __init__(self) -> None:
        self.entries: list[AdditionalUsageEntry] = []
        self.deleted_account_ids: list[str] = []
        self.deleted_account_limit_pairs: list[tuple[str, str]] = []
        self.deleted_account_limit_windows: list[tuple[str, str, str]] = []
        self._written_accounts: set[str] = set()

    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        recorded_at: datetime | None = None,
        quota_key: str | None = None,
    ) -> None:
        self._written_accounts.add(account_id)
        self.entries.append(
            AdditionalUsageEntry(
                account_id=account_id,
                quota_key=quota_key
                or canonicalize_additional_quota_key(
                    limit_name=limit_name,
                    metered_feature=metered_feature,
                ),
                limit_name=limit_name,
                metered_feature=metered_feature,
                window=window,
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=window_minutes,
            )
        )

    async def delete_for_account(self, account_id: str) -> None:
        self.deleted_account_ids.append(account_id)

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None:
        self.deleted_account_limit_pairs.append((account_id, limit_name))

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None:
        self.deleted_account_limit_pairs.append((account_id, quota_key))

    async def delete_for_account_limit_window(self, account_id: str, limit_name: str, window: str) -> None:
        self.deleted_account_limit_windows.append((account_id, limit_name, window))

    async def delete_for_account_quota_key_window(self, account_id: str, quota_key: str, window: str) -> None:
        self.deleted_account_limit_windows.append((account_id, quota_key, window))

    async def latest_recorded_at_for_account(self, account_id: str):
        from app.core.utils.time import utcnow

        return utcnow() if account_id in self._written_accounts else None

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        if account_ids is None:
            return sorted({entry.limit_name for entry in self.entries})
        return sorted({entry.limit_name for entry in self.entries if entry.account_id in account_ids})

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        if account_ids is None:
            return sorted(
                {
                    entry.quota_key
                    or canonicalize_additional_quota_key(
                        limit_name=entry.limit_name,
                        metered_feature=entry.metered_feature,
                    )
                    or entry.limit_name
                    for entry in self.entries
                }
            )
        return sorted(
            {
                entry.quota_key
                or canonicalize_additional_quota_key(
                    limit_name=entry.limit_name,
                    metered_feature=entry.metered_feature,
                )
                or entry.limit_name
                for entry in self.entries
                if entry.account_id in account_ids
            }
        )


def _make_account(account_id: str, chatgpt_account_id: str, email: str = "a@example.com") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_force_refresh_usage_recovers_rate_limited_account_when_primary_quota_returns() -> None:
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(StubUsageRepository(), accounts_repo)
    account = _make_account("acc_force_probe_recovered", "workspace_force_probe_recovered")
    account.status = AccountStatus.RATE_LIMITED
    account.deactivation_reason = None
    account.reset_at = 12345
    account.blocked_at = None
    accounts_repo.accounts_by_id[account.id] = account

    await updater._recover_quota_status_from_usage(
        account,
        primary=usage_updater_module.UsageWindow(used_percent=0.0),
        secondary=usage_updater_module.UsageWindow(used_percent=80.0),
    )

    assert accounts_repo.status_updates == [
        {
            "account_id": account.id,
            "status": AccountStatus.ACTIVE,
            "deactivation_reason": None,
            "reset_at": None,
            "blocked_at": None,
        },
    ]
    assert account.status == AccountStatus.ACTIVE
    assert account.reset_at is None


@pytest.mark.asyncio
async def test_force_refresh_bypasses_fresh_usage_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo)
    account = _make_account("acc_force_probe", "workspace_force_probe")
    usage_updater_module._last_successful_refresh[account.id] = datetime.now(tz=timezone.utc)

    refresh_account = AsyncMock(
        return_value=usage_updater_module.AccountRefreshResult(usage_written=True),
    )
    monkeypatch.setattr(updater, "_refresh_account", refresh_account)
    sync_account = AsyncMock()
    monkeypatch.setattr(updater, "_sync_account_from_repo", sync_account)

    refreshed = await updater.force_refresh(account)

    assert refreshed is True
    refresh_account.assert_awaited_once_with(
        account,
        usage_account_id=account.chatgpt_account_id,
    )
    sync_account.assert_awaited_once_with(account)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_force_refresh_does_not_join_stale_refresh_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo)
    account = _make_account("acc_force_probe_singleflight", "workspace_force_probe_singleflight")
    stale_started = asyncio.Event()
    release_stale = asyncio.Event()

    async def stale_refresh(
        refresh_account: Account,
        *,
        usage_account_id: str | None,
        interval_seconds: int,
    ) -> usage_updater_module.AccountRefreshResult:
        assert refresh_account is account
        assert usage_account_id == account.chatgpt_account_id
        assert interval_seconds > 0
        stale_started.set()
        await release_stale.wait()
        return usage_updater_module.AccountRefreshResult(usage_written=False)

    force_refresh_account = AsyncMock(
        return_value=usage_updater_module.AccountRefreshResult(usage_written=True),
    )
    sync_account = AsyncMock()
    monkeypatch.setattr(updater, "_refresh_account_if_stale", stale_refresh)
    monkeypatch.setattr(updater, "_refresh_account", force_refresh_account)
    monkeypatch.setattr(updater, "_sync_account_from_repo", sync_account)

    stale_task = asyncio.create_task(updater.refresh_accounts([account], latest_usage={}))
    await stale_started.wait()

    force_task = asyncio.create_task(updater.force_refresh(account))
    await asyncio.sleep(0)

    force_refresh_account.assert_not_awaited()
    release_stale.set()
    assert await stale_task is False
    refreshed = await force_task

    assert refreshed is True
    force_refresh_account.assert_awaited_once_with(
        account,
        usage_account_id=account.chatgpt_account_id,
    )
    assert sync_account.await_count == 2
    sync_account.assert_awaited_with(account)

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_force_refresh_preserves_cancellation_while_waiting_on_stale_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo)
    account = _make_account("acc_force_probe_cancel", "workspace_force_probe_cancel")
    stale_started = asyncio.Event()
    release_stale = asyncio.Event()

    async def stale_refresh(
        refresh_account: Account,
        *,
        usage_account_id: str | None,
        interval_seconds: int,
    ) -> usage_updater_module.AccountRefreshResult:
        del refresh_account, usage_account_id, interval_seconds
        stale_started.set()
        await release_stale.wait()
        return usage_updater_module.AccountRefreshResult(usage_written=False)

    force_refresh_account = AsyncMock(
        return_value=usage_updater_module.AccountRefreshResult(usage_written=True),
    )
    monkeypatch.setattr(updater, "_refresh_account_if_stale", stale_refresh)
    monkeypatch.setattr(updater, "_refresh_account", force_refresh_account)
    monkeypatch.setattr(updater, "_sync_account_from_repo", AsyncMock())

    stale_task = asyncio.create_task(updater.refresh_accounts([account], latest_usage={}))
    await stale_started.wait()
    force_task = asyncio.create_task(updater.force_refresh(account))
    await asyncio.sleep(0)

    force_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await force_task

    release_stale.set()
    assert await stale_task is False
    force_refresh_account.assert_not_awaited()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_force_refresh_bypasses_auth_failure_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS", "300")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo)
    account = _make_account("acc_force_probe_cooldown", "workspace_force_probe_cooldown")
    refresh_account = AsyncMock(
        return_value=usage_updater_module.AccountRefreshResult(usage_written=True),
    )
    sync_account = AsyncMock()
    monkeypatch.setattr(updater, "_refresh_account", refresh_account)
    monkeypatch.setattr(updater, "_sync_account_from_repo", sync_account)

    usage_updater_module._mark_usage_refresh_auth_cooldown(account.id, 403)

    refreshed = await updater.force_refresh(account)

    assert refreshed is True
    refresh_account.assert_awaited_once_with(
        account,
        usage_account_id=account.chatgpt_account_id,
    )
    sync_account.assert_awaited_once_with(account)
    assert usage_updater_module._is_usage_refresh_in_cooldown(account.id) is False
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_force_refresh_respects_usage_refresh_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "false")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    updater = UsageUpdater(StubUsageRepository())
    account = _make_account("acc_force_probe_disabled", "workspace_force_probe_disabled")
    refresh_account = AsyncMock()
    monkeypatch.setattr(updater, "_refresh_account", refresh_account)

    refreshed = await updater.force_refresh(account)

    assert refreshed is False
    refresh_account.assert_not_awaited()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_usage_updater_includes_chatgpt_account_id_even_when_shared(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    calls: list[dict[str, Any]] = []

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        calls.append({"access_token": access_token, "account_id": account_id})
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 20.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None)

    shared = "workspace_shared"
    acc_a = _make_account("acc_a", shared, email="a@example.com")
    acc_b = _make_account("acc_b", shared, email="b@example.com")
    acc_c = _make_account("acc_c", "workspace_unique", email="c@example.com")

    await updater.refresh_accounts([acc_a, acc_b, acc_c], latest_usage={})

    assert [call["account_id"] for call in calls] == [shared, shared, "workspace_unique"]


@pytest.mark.asyncio
async def test_usage_refresh_recovers_quota_exceeded_account_when_usage_is_available(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_recovered", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.ACTIVE
    assert account.reset_at is None
    assert account.blocked_at is None
    assert accounts_repo.status_updates == [
        {
            "account_id": account.id,
            "status": AccountStatus.ACTIVE,
            "deactivation_reason": None,
            "reset_at": None,
            "blocked_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_usage_refresh_keeps_recent_quota_exceeded_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: 1735600060.0)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_recent_cooldown", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.QUOTA_EXCEEDED
    assert account.reset_at == 1735689600
    assert account.blocked_at == 1735600000
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_bypasses_freshness_after_quota_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS", "3600")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: 1735601000.0)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_fresh_after_cooldown", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account
    fresh_usage = UsageHistory(
        id=1,
        account_id=account.id,
        used_percent=100.0,
        input_tokens=None,
        output_tokens=None,
        recorded_at=datetime.fromtimestamp(1735600500),
        window="primary",
    )

    await updater.refresh_accounts([account], latest_usage={account.id: fresh_usage})

    assert account.status == AccountStatus.ACTIVE
    assert account.blocked_at is None
    assert len(usage_repo.entries) == 2


@pytest.mark.asyncio
async def test_usage_refresh_preserves_freshness_after_failed_quota_recovery_probe(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS", "3600")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        raise AssertionError("fresh post-cooldown quota probe should not refetch")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: 1735601000.0)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_fresh_probe", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account
    fresh_usage = UsageHistory(
        id=1,
        account_id=account.id,
        used_percent=100.0,
        input_tokens=None,
        output_tokens=None,
        recorded_at=datetime.fromtimestamp(1735600950),
        window="primary",
    )

    await updater.refresh_accounts([account], latest_usage={account.id: fresh_usage})

    assert usage_repo.entries == []
    assert account.status == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_usage_refresh_does_not_overwrite_newer_status_change(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_paused_during_refresh", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    async def pause_before_compare(*args: Any, **kwargs: Any) -> bool:
        account.status = AccountStatus.PAUSED
        return False

    monkeypatch.setattr(accounts_repo, "update_status_if_current", pause_before_compare)

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.PAUSED
    assert account.reset_at == 1735689600
    assert account.blocked_at == 1735600000
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_syncs_blocked_at_after_compare_failure(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_blocked_at_changed", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1735689600
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    async def change_blocked_at_before_compare(*args: Any, **kwargs: Any) -> bool:
        account.blocked_at = 1735601234
        return False

    monkeypatch.setattr(accounts_repo, "update_status_if_current", change_blocked_at_before_compare)

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.QUOTA_EXCEEDED
    assert account.reset_at == 1735689600
    assert account.blocked_at == 1735601234
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_does_not_recover_when_secondary_quota_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 5.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 300 * 60,
                    },
                    "secondary_window": {
                        "used_percent": None,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_unknown_secondary", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1736208000
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.QUOTA_EXCEEDED
    assert account.reset_at == 1736208000
    assert account.blocked_at == 1735600000
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_does_not_recover_when_secondary_quota_is_still_exhausted(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 5.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 300 * 60,
                    },
                    "secondary_window": {
                        "used_percent": 100.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_still_exhausted", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1736208000
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.QUOTA_EXCEEDED
    assert account.reset_at == 1736208000
    assert account.blocked_at == 1735600000
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_demotes_quota_exceeded_to_rate_limited_when_primary_is_exhausted(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 100.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 300 * 60,
                    },
                    "secondary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_quota_primary_still_limited", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = 1736208000
    account.blocked_at = 1735600000
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.RATE_LIMITED
    assert account.reset_at == 1735689600
    assert account.blocked_at is None
    assert accounts_repo.status_updates[-1] == {
        "account_id": account.id,
        "status": AccountStatus.RATE_LIMITED,
        "deactivation_reason": None,
        "reset_at": 1735689600,
        "blocked_at": None,
    }


@pytest.mark.asyncio
async def test_usage_refresh_recovers_quota_exceeded_free_weekly_account(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 604800,
                    },
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_free_weekly_recovered", "workspace_shared")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.plan_type = "free"
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert account.status == AccountStatus.ACTIVE
    assert usage_repo.entries[-1].window == "secondary"


@pytest.mark.asyncio
async def test_usage_refresh_skips_mismatched_workspace_payload(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "workspace_id": "ws_other",
                "plan_type": "free",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 100.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                },
                "additional_rate_limits": [],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    additional_repo = StubAdditionalUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo, additional_usage_repo=additional_repo)
    account = _make_account("acc_team_ws", "upstream_user", email="same@example.com")
    account.workspace_id = "ws_team"
    account.plan_type = "business"
    accounts_repo.accounts_by_id[account.id] = account

    result = await updater.refresh_accounts([account], latest_usage={})

    assert result is False
    assert usage_repo.entries == []
    assert additional_repo.deleted_account_ids == []
    assert accounts_repo.status_updates == []
    assert accounts_repo.token_updates == []
    assert account.status == AccountStatus.ACTIVE
    assert account.plan_type == "business"
    assert account.workspace_id == "ws_team"


@pytest.mark.asyncio
async def test_usage_refresh_skips_taken_workspace_slot_payload(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "workspace_id": "ws_taken",
                "workspace_label": "Taken Workspace",
                "seat_type": "business",
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 12.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 5 * 60 * 60,
                    },
                },
                "additional_rate_limits": [],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    additional_repo = StubAdditionalUsageRepository()
    accounts_repo = StubAccountsRepository()
    account = _make_account("acc_unknown_taken", "chatgpt_shared", email="shared@example.com")
    account.workspace_id = None
    account.workspace_label = None
    account.seat_type = None
    original_plan_type = account.plan_type
    accounts_repo.accounts_by_id[account.id] = account
    accounts_repo.taken_workspace_slots.add(("shared@example.com", "chatgpt_shared", "ws_taken"))
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo, additional_usage_repo=additional_repo)

    result = await updater.refresh_accounts([account], latest_usage={})

    assert result is False
    assert usage_repo.entries == []
    assert additional_repo.entries == []
    assert account.workspace_id is None
    assert account.workspace_label is None
    assert account.seat_type is None
    assert account.plan_type == original_plan_type
    assert accounts_repo.token_updates == []


@pytest.mark.asyncio
async def test_usage_refresh_skips_unknown_workspace_plan_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "plan_type": "free",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 100.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_unknown_team", "upstream_user", email="same@example.com")
    account.plan_type = "team"
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert usage_repo.entries == []
    assert accounts_repo.status_updates == []
    assert account.plan_type == "team"


@pytest.mark.asyncio
async def test_usage_refresh_skips_workspace_account_when_payload_omits_workspace_and_plan_conflicts(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        del access_token, account_id
        return UsagePayload.model_validate(
            {
                "plan_type": "free",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 100.0,
                        "reset_at": 1736208000,
                        "limit_window_seconds": 7 * 24 * 60 * 60,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    account = _make_account("acc_workspace_missing_payload", "upstream_user", email="same@example.com")
    account.workspace_id = "ws_team"
    account.plan_type = "business"
    accounts_repo.accounts_by_id[account.id] = account

    await updater.refresh_accounts([account], latest_usage={})

    assert usage_repo.entries == []
    assert accounts_repo.status_updates == []
    assert accounts_repo.token_updates == []
    assert account.workspace_id == "ws_team"
    assert account.plan_type == "business"


class StubAccountsRepository:
    def __init__(self) -> None:
        self.status_updates: list[dict[str, Any]] = []
        self.token_updates: list[dict[str, Any]] = []
        self.accounts_by_id: dict[str, Account] = {}
        self.taken_workspace_slots: set[tuple[str, str | None, str]] = set()

    async def get_by_id(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
    ) -> bool:
        account = self.accounts_by_id.get(account_id)
        if account is not None:
            account.status = status
            account.deactivation_reason = deactivation_reason
            account.reset_at = reset_at
            account.blocked_at = blocked_at
        self.status_updates.append(
            {
                "account_id": account_id,
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
                "blocked_at": blocked_at,
            }
        )
        return True

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None = None,
    ) -> bool:
        account = self.accounts_by_id.get(account_id)
        if (
            account is None
            or account.status != expected_status
            or account.deactivation_reason != expected_deactivation_reason
            or account.reset_at != expected_reset_at
            or account.blocked_at != expected_blocked_at
        ):
            return False
        return await self.update_status(account_id, status, deactivation_reason, reset_at, blocked_at)

    async def update_tokens(self, *args: Any, **kwargs: Any) -> bool:
        account_id = args[0] if args else kwargs.get("account_id")
        if not isinstance(account_id, str):
            return True
        account = self.accounts_by_id.get(account_id)
        if account is not None:
            account.access_token_encrypted = kwargs["access_token_encrypted"]
            account.refresh_token_encrypted = kwargs["refresh_token_encrypted"]
            account.id_token_encrypted = kwargs["id_token_encrypted"]
            account.last_refresh = kwargs["last_refresh"]
            plan_type = kwargs.get("plan_type")
            email = kwargs.get("email")
            chatgpt_account_id = kwargs.get("chatgpt_account_id")
            workspace_id = kwargs.get("workspace_id")
            workspace_label = kwargs.get("workspace_label")
            seat_type = kwargs.get("seat_type")
            if isinstance(plan_type, str):
                account.plan_type = plan_type
            if isinstance(email, str):
                account.email = email
            if isinstance(chatgpt_account_id, str):
                account.chatgpt_account_id = chatgpt_account_id
            if isinstance(workspace_id, str):
                account.workspace_id = workspace_id
            if isinstance(workspace_label, str):
                account.workspace_label = workspace_label
            if isinstance(seat_type, str):
                account.seat_type = seat_type
        self.token_updates.append({"account_id": account_id, **kwargs})
        return True

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        del account_id
        return (email, chatgpt_account_id, workspace_id) in self.taken_workspace_slots


@pytest.mark.asyncio
async def test_usage_updater_deactivates_on_account_invalid_4xx(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_402(**_: Any) -> UsagePayload:
        raise UsageFetchError(402, "Payment Required")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_402)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_402", "workspace_402", email="payment@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 1
    update = accounts_repo.status_updates[0]
    assert update["account_id"] == "acc_402"
    assert update["status"] == AccountStatus.DEACTIVATED
    assert "402" in update["deactivation_reason"]
    assert "Payment Required" in update["deactivation_reason"]


@pytest.mark.asyncio
async def test_usage_updater_does_not_deactivate_on_403(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_403(**_: Any) -> UsagePayload:
        raise UsageFetchError(403, "Forbidden")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_403)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_403", "workspace_403", email="forbidden@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 0


@pytest.mark.asyncio
async def test_usage_updater_does_not_deactivate_on_transient_4xx(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_429(**_: Any) -> UsagePayload:
        raise UsageFetchError(429, "Too Many Requests")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_429)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_429", "workspace_429", email="rate@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 0


@pytest.mark.asyncio
async def test_usage_updater_does_not_deactivate_on_401(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_401(**_: Any) -> UsagePayload:
        raise UsageFetchError(401, "Unauthorized")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_401)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_401", "workspace_401", email="auth@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 0


@pytest.mark.asyncio
async def test_usage_updater_deactivates_on_401_account_deactivated_code(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_401_deactivated(**_: Any) -> UsagePayload:
        raise UsageFetchError(
            401,
            "Your OpenAI account has been deactivated, please check your email for more information.",
            code="account_deactivated",
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_401_deactivated)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_401_deactivated", "workspace_401_deactivated", email="dead@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 1
    update = accounts_repo.status_updates[0]
    assert update["status"] == AccountStatus.DEACTIVATED
    assert "401" in update["deactivation_reason"]
    assert "deactivated" in update["deactivation_reason"].lower()


@pytest.mark.asyncio
async def test_usage_updater_deactivates_on_401_deactivated_message_without_code(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_401_deactivated_message(**_: Any) -> UsagePayload:
        raise UsageFetchError(
            401,
            "Your OpenAI account has been deactivated, please check your email for more information.",
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_401_deactivated_message)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_401_message", "workspace_401_message", email="message@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 1
    assert accounts_repo.status_updates[0]["status"] == AccountStatus.DEACTIVATED


@pytest.mark.asyncio
async def test_usage_updater_cools_down_repeated_403_failures(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS", "300")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    fetch_calls = 0

    async def stub_fetch_usage_403(**_: Any) -> UsagePayload:
        nonlocal fetch_calls
        fetch_calls += 1
        raise UsageFetchError(403, "Forbidden")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_403)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_403_cooldown", "workspace_403_cooldown", email="forbidden@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})
    await updater.refresh_accounts([acc], latest_usage={})

    assert fetch_calls == 1
    assert len(accounts_repo.status_updates) == 0


@pytest.mark.asyncio
async def test_usage_updater_subset_refresh_does_not_clear_other_account_cooldowns(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS", "300")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    fetch_calls = 0

    async def stub_fetch_usage(*, account_id: str | None, **_: Any) -> UsagePayload:
        nonlocal fetch_calls
        fetch_calls += 1
        if account_id == "workspace_cooled":
            raise UsageFetchError(403, "Forbidden")
        return UsagePayload.model_validate({})

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    cooled = _make_account("acc_cooldown_kept", "workspace_cooled", email="cooled@example.com")
    imported = _make_account("acc_imported", "workspace_imported", email="imported@example.com")
    accounts_repo.accounts_by_id[cooled.id] = cooled
    accounts_repo.accounts_by_id[imported.id] = imported

    await updater.refresh_accounts([cooled], latest_usage={})
    await updater.refresh_accounts([imported], latest_usage={})
    await updater.refresh_accounts([cooled], latest_usage={})

    assert fetch_calls == 2


def test_mark_usage_refresh_auth_cooldown_ignores_non_auth_status(monkeypatch) -> None:
    monkeypatch.setattr(
        usage_updater_module,
        "get_settings",
        lambda: type("Settings", (), {"usage_refresh_auth_failure_cooldown_seconds": 300.0})(),
    )

    usage_updater_module._mark_usage_refresh_auth_cooldown("acc_non_auth", 500)

    assert usage_updater_module._is_usage_refresh_in_cooldown("acc_non_auth") is False


@pytest.mark.asyncio
async def test_usage_updater_does_not_deactivate_on_5xx(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_500(**_: Any) -> UsagePayload:
        raise UsageFetchError(500, "Internal Server Error")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_500)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)

    acc = _make_account("acc_500", "workspace_500", email="server@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 0


@pytest.mark.asyncio
async def test_usage_updater_persists_primary_and_secondary_usage(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        assert access_token
        assert account_id == "workspace_123"
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 12.5,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 300,
                    },
                    "secondary_window": {
                        "used_percent": 55.0,
                        "reset_at": 1735693200,
                        "limit_window_seconds": 60,
                    },
                },
                "credits": {"has_credits": True, "unlimited": False, "balance": "42.5"},
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None)
    acc = _make_account("acc_test", "workspace_123", email="persist@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(usage_repo.entries) == 2
    by_window = {entry.window: entry for entry in usage_repo.entries}

    primary = by_window["primary"]
    assert primary.account_id == "acc_test"
    assert primary.used_percent == 12.5
    assert primary.reset_at == 1735689600
    assert primary.window_minutes == 5
    assert primary.credits_has is True
    assert primary.credits_unlimited is False
    assert primary.credits_balance == 42.5

    secondary = by_window["secondary"]
    assert secondary.account_id == "acc_test"
    assert secondary.used_percent == 55.0
    assert secondary.reset_at == 1735693200
    assert secondary.window_minutes == 1
    assert secondary.credits_has is None
    assert secondary.credits_unlimited is None
    assert secondary.credits_balance is None


@pytest.mark.asyncio
async def test_usage_updater_does_not_sync_conflicting_plan_without_workspace_identity(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate({"plan_type": "plus"})

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    acc = _make_account("acc_plan_sync", "workspace_plan_sync", email="plan@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc
    acc.plan_type = "free"

    await updater.refresh_accounts([acc], latest_usage={})

    assert acc.plan_type == "free"
    assert accounts_repo.token_updates == []
    assert usage_repo.entries == []


@pytest.mark.asyncio
async def test_usage_updater_computes_reset_at_from_reset_after_seconds(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr("app.modules.usage.updater._now_epoch", lambda: 1000)

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 1.0,
                        "reset_after_seconds": 120,
                        "limit_window_seconds": 60,
                    }
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None)
    acc = _make_account("acc_reset", "workspace_reset", email="reset@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(usage_repo.entries) == 1
    entry = usage_repo.entries[0]
    assert entry.window == "primary"
    assert entry.reset_at == 1120


@pytest.mark.asyncio
async def test_usage_updater_refresh_accounts_returns_false_when_rate_limit_missing(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate({})

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    updater = UsageUpdater(usage_repo, accounts_repo=None)
    acc = _make_account("acc_no_rate", "workspace_no_rate", email="no-rate@example.com")

    refreshed = await updater.refresh_accounts([acc], latest_usage={})

    assert refreshed is False
    assert len(usage_repo.entries) == 0


@pytest.mark.asyncio
async def test_usage_updater_refresh_accounts_returns_false_on_401_retry_failure(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_401(**_: Any) -> UsagePayload:
        raise UsageFetchError(401, "Unauthorized")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_401)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    assert updater._auth_manager is not None

    async def stub_ensure_fresh(account: Account, *, force: bool = False) -> Account:
        raise RefreshError(code="invalid_grant", message="refresh failed", is_permanent=False)

    monkeypatch.setattr(updater._auth_manager, "ensure_fresh", stub_ensure_fresh)

    acc = _make_account("acc_401_retry", "workspace_401_retry", email="auth-retry@example.com")
    accounts_repo.accounts_by_id[acc.id] = acc
    refreshed = await updater.refresh_accounts([acc], latest_usage={})

    assert refreshed is False
    assert len(usage_repo.entries) == 0


@pytest.mark.parametrize(
    ("primary_used", "secondary_used"),
    [
        (10.0, None),
        (None, 20.0),
    ],
)
@pytest.mark.asyncio
async def test_usage_updater_refresh_accounts_returns_true_when_any_window_written(
    monkeypatch,
    primary_used: float | None,
    secondary_used: float | None,
) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: Any) -> UsagePayload:
        assert access_token
        assert account_id == "workspace_written"
        rate_limit: dict[str, Any] = {}
        if primary_used is not None:
            rate_limit["primary_window"] = {
                "used_percent": primary_used,
                "reset_at": 1735689600,
                "limit_window_seconds": 60,
            }
        if secondary_used is not None:
            rate_limit["secondary_window"] = {
                "used_percent": secondary_used,
                "reset_at": 1735689600,
                "limit_window_seconds": 60,
            }
        return UsagePayload.model_validate({"rate_limit": rate_limit})

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    updater = UsageUpdater(usage_repo, accounts_repo=None)
    acc = _make_account("acc_written", "workspace_written", email="written@example.com")

    refreshed = await updater.refresh_accounts([acc], latest_usage={})

    assert refreshed is True
    assert len(usage_repo.entries) == 1


@pytest.mark.asyncio
async def test_usage_updater_refresh_accounts_returns_true_when_partial_write(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(*, account_id: str | None, **_: Any) -> UsagePayload:
        if account_id == "workspace_skip":
            return UsagePayload.model_validate({})
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    }
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    updater = UsageUpdater(usage_repo, accounts_repo=None)
    acc_skip = _make_account("acc_skip", "workspace_skip", email="skip@example.com")
    acc_write = _make_account("acc_write", "workspace_write", email="write@example.com")

    refreshed = await updater.refresh_accounts([acc_skip, acc_write], latest_usage={})

    assert refreshed is True
    assert len(usage_repo.entries) == 1


@pytest.mark.asyncio
async def test_usage_updater_singleflights_concurrent_refreshes(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    fetch_calls = 0
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def stub_fetch_usage(*, account_id: str | None, **_: Any) -> UsagePayload:
        nonlocal fetch_calls
        fetch_calls += 1
        assert account_id == "workspace_shared_refresh"
        fetch_started.set()
        await release_fetch.wait()
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    }
                }
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository(return_rows=True)
    updater_a = UsageUpdater(usage_repo, accounts_repo=None)
    updater_b = UsageUpdater(usage_repo, accounts_repo=None)
    acc_a = _make_account("acc_singleflight", "workspace_shared_refresh", email="a@example.com")
    acc_b = _make_account("acc_singleflight", "workspace_shared_refresh", email="b@example.com")

    first = asyncio.create_task(updater_a.refresh_accounts([acc_a], latest_usage={}))
    await fetch_started.wait()
    second = asyncio.create_task(updater_b.refresh_accounts([acc_b], latest_usage={}))
    await asyncio.sleep(0.01)

    assert not second.done()

    release_fetch.set()
    first_refreshed, second_refreshed = await asyncio.gather(first, second)

    assert fetch_calls == 1
    assert first_refreshed is True
    assert second_refreshed is True
    assert len(usage_repo.entries) == 1


# --- Additional rate limits tests ---


@pytest.mark.asyncio
async def test_additional_rate_limits_written_to_additional_repo(monkeypatch) -> None:
    """Additional rate limits from payload are persisted via additional_usage_repo."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.modules.usage.updater._now_epoch", lambda: 2000)

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 50.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 300,
                            },
                            "secondary_window": {
                                "used_percent": 75.0,
                                "reset_after_seconds": 120,
                                "limit_window_seconds": 3600,
                            },
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_add", "workspace_add", email="add@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    # Primary usage entry written
    assert len(usage_repo.entries) == 1

    # Two additional entries: primary + secondary window
    assert len(additional_repo.entries) == 2
    by_window = {e.window: e for e in additional_repo.entries}

    primary = by_window["primary"]
    assert primary.account_id == "acc_add"
    assert primary.limit_name == "o-pro"
    assert primary.metered_feature == "o_pro"
    assert primary.used_percent == 50.0
    assert primary.reset_at == 1735689600
    assert primary.window_minutes == 5

    secondary = by_window["secondary"]
    assert secondary.account_id == "acc_add"
    assert secondary.limit_name == "o-pro"
    assert secondary.metered_feature == "o_pro"
    assert secondary.used_percent == 75.0
    assert secondary.reset_at == 2120  # now_epoch(2000) + 120
    assert secondary.window_minutes == 60


@pytest.mark.asyncio
async def test_additional_rate_limits_normalize_known_alias_to_canonical_quota_key(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "GPT-5.3-Codex-Spark",
                        "metered_feature": "codex_bengalfox",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 12.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 300,
                            }
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)

    await updater.refresh_accounts([_make_account("acc_alias", "workspace_alias")], latest_usage={})

    assert len(additional_repo.entries) == 1
    entry = additional_repo.entries[0]
    assert entry.quota_key == "codex_spark"
    assert entry.limit_name == "GPT-5.3-Codex-Spark"
    assert entry.metered_feature == "codex_bengalfox"


@pytest.mark.asyncio
async def test_additional_rate_limits_merge_aliases_before_pruning_quota(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "GPT-5.3-Codex-Spark",
                        "metered_feature": "codex_bengalfox",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 12.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 300,
                            }
                        },
                    },
                    {
                        "limit_name": "codex_other",
                        "metered_feature": "codex_bengalfox",
                        "rate_limit": None,
                    },
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)

    await updater.refresh_accounts([_make_account("acc_alias_merge", "workspace_alias_merge")], latest_usage={})

    assert len(additional_repo.entries) == 1
    entry = additional_repo.entries[0]
    assert entry.quota_key == "codex_spark"
    assert entry.limit_name == "GPT-5.3-Codex-Spark"
    assert additional_repo.deleted_account_limit_pairs == []


@pytest.mark.asyncio
async def test_additional_rate_limits_merge_windows_across_aliases(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "GPT-5.3-Codex-Spark",
                        "metered_feature": "codex_bengalfox",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 12.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 300,
                            }
                        },
                    },
                    {
                        "limit_name": "codex_other",
                        "metered_feature": "codex_bengalfox",
                        "rate_limit": {
                            "secondary_window": {
                                "used_percent": 33.0,
                                "reset_at": 1735689700,
                                "limit_window_seconds": 1800,
                            }
                        },
                    },
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)

    await updater.refresh_accounts([_make_account("acc_alias_windows", "workspace_alias_windows")], latest_usage={})

    assert len(additional_repo.entries) == 2
    by_window = {entry.window: entry for entry in additional_repo.entries}
    assert by_window["primary"].quota_key == "codex_spark"
    assert by_window["secondary"].quota_key == "codex_spark"
    assert additional_repo.deleted_account_limit_pairs == []


@pytest.mark.asyncio
async def test_additional_rate_limits_null_writes_nothing(monkeypatch) -> None:
    """When additional_rate_limits is null, no additional entries are written."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_null", "workspace_null", email="null@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(additional_repo.entries) == 0


@pytest.mark.asyncio
async def test_additional_rate_limits_sync_even_when_main_rate_limit_missing(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 60,
                            }
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_add_only", "workspace_add_only", email="add-only@example.com")

    refreshed = await updater.refresh_accounts([acc], latest_usage={})

    # Additional-only accounts write additional data and mark themselves as fresh
    # to prevent tight re-polling (R6-F1).
    assert refreshed is True
    assert usage_repo.entries == []
    assert len(additional_repo.entries) == 1
    assert additional_repo.entries[0].limit_name == "o-pro"


@pytest.mark.asyncio
async def test_additional_only_account_not_repolled_within_interval(monkeypatch) -> None:
    """R6-F1: Additional-only accounts must not cause tight re-polling."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    call_count = 0

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        nonlocal call_count
        call_count += 1
        return UsagePayload.model_validate(
            {
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25.0,
                                "reset_at": 1735689600,
                                "limit_window_seconds": 60,
                            }
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_add_only2", "workspace_add_only2", email="add-only2@example.com")

    # First call fetches usage.
    await updater.refresh_accounts([acc], latest_usage={})
    assert call_count == 1

    # Second call immediately should be skipped due to freshness cache.
    await updater.refresh_accounts([acc], latest_usage={})
    assert call_count == 1


@pytest.mark.asyncio
async def test_additional_rate_limits_empty_list_writes_nothing(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_empty", "workspace_empty", email="empty@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(additional_repo.entries) == 0
    assert additional_repo.deleted_account_ids == ["acc_empty"]


@pytest.mark.asyncio
async def test_additional_rate_limits_none_does_not_prune_existing_rows(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_none_preserve", "workspace_none_preserve", email="preserve@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert additional_repo.deleted_account_ids == []


@pytest.mark.asyncio
async def test_additional_rate_limits_multiple_limits(monkeypatch) -> None:
    """Multiple additional limits produce one entry per limit per window."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("app.modules.usage.updater._now_epoch", lambda: 5000)

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 5.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 30.0,
                                "reset_at": 9999,
                                "limit_window_seconds": 600,
                            },
                        },
                    },
                    {
                        "limit_name": "deep-research",
                        "metered_feature": "deep_research",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 80.0,
                                "reset_at": 8888,
                                "limit_window_seconds": 120,
                            },
                            "secondary_window": {
                                "used_percent": 40.0,
                                "reset_at": 7777,
                                "limit_window_seconds": 1800,
                            },
                        },
                    },
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_multi", "workspace_multi", email="multi@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    # o-pro: 1 primary; deep-research: 1 primary + 1 secondary = 3 total
    assert len(additional_repo.entries) == 3
    names = [(e.limit_name, e.window) for e in additional_repo.entries]
    assert ("o-pro", "primary") in names
    assert ("deep-research", "primary") in names
    assert ("deep-research", "secondary") in names


@pytest.mark.asyncio
async def test_additional_rate_limits_secondary_none_only_primary(monkeypatch) -> None:
    """When secondary_window is None, only primary entry is written."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "sora",
                        "metered_feature": "sora_video",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 60.0,
                                "reset_at": 4444,
                                "limit_window_seconds": 180,
                            },
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_sec_none", "workspace_sec_none", email="sec-none@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(additional_repo.entries) == 1
    entry = additional_repo.entries[0]
    assert entry.window == "primary"
    assert entry.limit_name == "sora"
    assert entry.metered_feature == "sora_video"
    assert entry.used_percent == 60.0


@pytest.mark.asyncio
async def test_additional_rate_limits_prune_stale_limit_names(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25.0,
                                "reset_at": 5555,
                                "limit_window_seconds": 60,
                            },
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    additional_repo.entries.extend(
        [
            AdditionalUsageEntry(
                account_id="acc_prune",
                limit_name="o-pro",
                metered_feature="o_pro",
                window="primary",
                used_percent=10.0,
                reset_at=1111,
                window_minutes=1,
            ),
            AdditionalUsageEntry(
                account_id="acc_prune",
                limit_name="legacy-limit",
                metered_feature="legacy_feature",
                window="primary",
                used_percent=90.0,
                reset_at=2222,
                window_minutes=5,
            ),
        ]
    )
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_prune", "workspace_prune", email="prune@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert additional_repo.deleted_account_limit_pairs == [("acc_prune", "legacy_limit")]


@pytest.mark.asyncio
async def test_additional_rate_limits_prune_stale_secondary_window(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25.0,
                                "reset_at": 5555,
                                "limit_window_seconds": 60,
                            },
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    additional_repo.entries.extend(
        [
            AdditionalUsageEntry(
                account_id="acc_secondary_prune",
                limit_name="o-pro",
                metered_feature="o_pro",
                window="primary",
                used_percent=10.0,
                reset_at=1111,
                window_minutes=1,
            ),
            AdditionalUsageEntry(
                account_id="acc_secondary_prune",
                limit_name="o-pro",
                metered_feature="o_pro",
                window="secondary",
                used_percent=80.0,
                reset_at=2222,
                window_minutes=60,
            ),
        ]
    )
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_secondary_prune", "workspace_secondary_prune", email="secondary-prune@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    assert additional_repo.deleted_account_limit_pairs == []
    assert additional_repo.deleted_account_limit_windows == [("acc_secondary_prune", "o_pro", "secondary")]


@pytest.mark.asyncio
async def test_additional_rate_limits_no_credits_passed(monkeypatch) -> None:
    """Credits data is NOT passed to additional limit entries (no credits_* fields)."""
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                    },
                },
                "credits": {"has_credits": True, "unlimited": False, "balance": "100.0"},
                "additional_rate_limits": [
                    {
                        "limit_name": "o-pro",
                        "metered_feature": "o_pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25.0,
                                "reset_at": 5555,
                                "limit_window_seconds": 60,
                            },
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    additional_repo = StubAdditionalUsageRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=None, additional_usage_repo=additional_repo)
    acc = _make_account("acc_no_cred", "workspace_no_cred", email="no-cred@example.com")

    await updater.refresh_accounts([acc], latest_usage={})

    # Primary usage entry should have credits
    assert len(usage_repo.entries) == 1
    assert usage_repo.entries[0].credits_has is True

    # Additional entry should NOT have credits fields (not part of the protocol)
    assert len(additional_repo.entries) == 1
    entry = additional_repo.entries[0]
    assert not hasattr(entry, "credits_has")
    assert not hasattr(entry, "credits_unlimited")
    assert not hasattr(entry, "credits_balance")


def test_latest_usage_is_fresh_returns_false_when_reset_at_has_passed() -> None:
    now = datetime(2024, 6, 1, 12, 0, 30)
    reset_epoch = int(datetime(2024, 6, 1, 12, 0, 25, tzinfo=timezone.utc).timestamp())
    entry = UsageHistory(
        id=1,
        account_id="a",
        used_percent=100.0,
        recorded_at=datetime(2024, 6, 1, 12, 0, 20),
        window="primary",
        reset_at=reset_epoch,
        window_minutes=300,
    )

    assert usage_updater_module._latest_usage_is_fresh(entry, now=now, interval_seconds=60) is False


@pytest.mark.asyncio
async def test_refresh_accounts_forces_fetch_after_rate_limit_reset_despite_fresh_usage(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings
    from app.core.utils.time import utcnow

    get_settings.cache_clear()
    now_epoch = 1_700_000_000
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: now_epoch)

    fetch_calls = 0

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        nonlocal fetch_calls
        fetch_calls += 1
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 5.0,
                        "reset_at": now_epoch + 3600,
                        "limit_window_seconds": 3600,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    await usage_repo.add_entry(
        "acc_reset",
        100.0,
        recorded_at=utcnow(),
        window="primary",
        reset_at=now_epoch - 1,
        window_minutes=60,
    )
    latest = await usage_repo.latest_entry_for_account("acc_reset", window="primary")
    assert latest is not None

    acc = _make_account("acc_reset", "workspace_reset", email="reset@example.com")
    acc.status = AccountStatus.RATE_LIMITED
    acc.reset_at = now_epoch - 1

    updater = UsageUpdater(usage_repo, accounts_repo=None)
    await updater.refresh_accounts([acc], latest_usage={"acc_reset": latest})

    assert fetch_calls == 1
    assert usage_repo.entries[-1].used_percent == 5.0


@pytest.mark.asyncio
async def test_refresh_accounts_does_not_repeat_post_reset_rate_limit_probe(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS", "3600")
    from app.core.config.settings import get_settings
    from app.core.utils.time import utcnow

    get_settings.cache_clear()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: now_epoch)

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        raise AssertionError("post-reset rate-limit probe should not repeat while usage is fresh")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    latest = UsageHistory(
        id=1,
        account_id="acc_rate_limited_post_reset",
        used_percent=5.0,
        recorded_at=now,
        window="primary",
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )
    acc = _make_account("acc_rate_limited_post_reset", "workspace_rate_limited_post_reset")
    acc.status = AccountStatus.RATE_LIMITED
    acc.reset_at = now_epoch - 1

    updater = UsageUpdater(StubUsageRepository(), accounts_repo=None)
    await updater.refresh_accounts([acc], latest_usage={acc.id: latest})


@pytest.mark.asyncio
async def test_refresh_accounts_forces_fetch_after_quota_reset_despite_fresh_primary_usage(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings
    from app.core.utils.time import utcnow

    get_settings.cache_clear()
    now_epoch = 1_700_000_000
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: now_epoch)

    fetch_calls = 0

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        nonlocal fetch_calls
        fetch_calls += 1
        return UsagePayload.model_validate(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10.0,
                        "reset_at": now_epoch + 3600,
                        "limit_window_seconds": 3600,
                    },
                    "secondary_window": {
                        "used_percent": 15.0,
                        "reset_at": now_epoch + 7 * 24 * 3600,
                        "limit_window_seconds": 7 * 24 * 3600,
                    },
                },
            }
        )

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    usage_repo = StubUsageRepository()
    await usage_repo.add_entry(
        "acc_quota_reset",
        50.0,
        recorded_at=utcnow(),
        window="primary",
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )
    latest = await usage_repo.latest_entry_for_account("acc_quota_reset", window="primary")
    assert latest is not None

    acc = _make_account("acc_quota_reset", "workspace_quota_reset", email="quota-reset@example.com")
    acc.status = AccountStatus.QUOTA_EXCEEDED
    acc.reset_at = now_epoch - 1

    updater = UsageUpdater(usage_repo, accounts_repo=None)
    await updater.refresh_accounts([acc], latest_usage={"acc_quota_reset": latest})

    assert fetch_calls == 1
    assert usage_repo.entries[-2].used_percent == 10.0
    assert usage_repo.entries[-1].used_percent == 15.0


@pytest.mark.asyncio
async def test_refresh_accounts_does_not_repeat_post_reset_quota_probe(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS", "3600")
    from app.core.config.settings import get_settings
    from app.core.utils.time import utcnow

    get_settings.cache_clear()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr("app.modules.usage.updater.time.time", lambda: now_epoch)

    async def stub_fetch_usage(**_: Any) -> UsagePayload:
        raise AssertionError("post-reset quota probe should not repeat while usage is fresh")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage)

    latest = UsageHistory(
        id=1,
        account_id="acc_quota_post_reset",
        used_percent=10.0,
        recorded_at=now,
        window="primary",
        reset_at=now_epoch + 3600,
        window_minutes=60,
    )
    acc = _make_account("acc_quota_post_reset", "workspace_quota_post_reset")
    acc.status = AccountStatus.QUOTA_EXCEEDED
    acc.reset_at = now_epoch - 1

    updater = UsageUpdater(StubUsageRepository(), accounts_repo=None)
    await updater.refresh_accounts([acc], latest_usage={acc.id: latest})
