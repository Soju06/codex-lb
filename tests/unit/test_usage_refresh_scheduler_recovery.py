from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.db.models import Account, AccountStatus, UsageHistory

pytestmark = pytest.mark.unit

_UNSET = object()


def _make_account(
    account_id: str,
    *,
    status: AccountStatus,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2025, 1, 1),
        status=status,
        reset_at=reset_at,
        blocked_at=blocked_at,
        deactivation_reason=None,
    )


def _make_usage(
    account_id: str,
    *,
    window: str,
    used_percent: float,
    reset_at: int,
    recorded_at: datetime,
    window_minutes: int,
) -> UsageHistory:
    return UsageHistory(
        id=1,
        account_id=account_id,
        recorded_at=recorded_at,
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


def _epoch_to_naive_utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


class StubAccountsRepository:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = {account.id: account for account in accounts}
        self.status_updates: list[dict[str, Any]] = []

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = _UNSET,
    ) -> bool:
        account = self._accounts.get(account_id)
        if account is None:
            return False
        if account.status != expected_status or account.deactivation_reason != expected_deactivation_reason:
            return False
        if account.reset_at != expected_reset_at:
            return False
        if expected_blocked_at is not _UNSET and account.blocked_at != expected_blocked_at:
            return False
        account.status = status
        account.deactivation_reason = deactivation_reason
        account.reset_at = reset_at
        if blocked_at is not _UNSET:
            account.blocked_at = cast("int | None", blocked_at)
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


class StubUsageRepository:
    def __init__(
        self,
        *,
        primary: dict[str, UsageHistory] | None = None,
        secondary: dict[str, UsageHistory] | None = None,
    ) -> None:
        self._primary = primary or {}
        self._secondary = secondary or {}

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        if window == "secondary":
            return self._secondary
        return self._primary


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_keeps_rate_limited_until_reset_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    blocked_at = int(now - 130)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_rate_limited",
        status=AccountStatus.RATE_LIMITED,
        reset_at=future_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=10.0,
                reset_at=future_reset,
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=300,
            )
        },
        secondary={
            account.id: _make_usage(
                account.id,
                window="secondary",
                used_percent=20.0,
                reset_at=int(now + 7200),
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=10080,
            )
        },
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert account.status == AccountStatus.RATE_LIMITED
    assert account.reset_at == future_reset
    assert account.blocked_at == blocked_at
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_restores_rate_limited_after_reset_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    past_reset = int(now - 300)
    blocked_at = int(now - 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_rate_limited_recovered",
        status=AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=10.0,
                reset_at=past_reset,
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=300,
            )
        }
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 1
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
async def test_reconcile_recoverable_account_statuses_keeps_rate_limited_without_persisted_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    blocked_at = int(now - 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_rate_limited_no_reset_recovered",
        status=AccountStatus.RATE_LIMITED,
        reset_at=None,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=10.0,
                reset_at=int(now + 300),
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=300,
            )
        }
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert account.status == AccountStatus.RATE_LIMITED
    assert account.reset_at is None
    assert account.blocked_at == blocked_at
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_restores_quota_exceeded_from_fresh_secondary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    blocked_at = int(now - 130)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_quota_exceeded",
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=future_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=5.0,
                reset_at=int(now + 300),
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=300,
            )
        },
        secondary={
            account.id: _make_usage(
                account.id,
                window="secondary",
                used_percent=10.0,
                reset_at=future_reset,
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=10080,
            )
        },
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 1
    assert account.status == AccountStatus.ACTIVE
    assert account.reset_at is None
    assert account.blocked_at is None


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_does_not_demote_quota_exceeded_to_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    primary_reset = int(now + 300)
    secondary_reset = int(now + 7200)
    blocked_at = int(now - 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_quota_exceeded_demoted",
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=secondary_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=100.0,
                reset_at=primary_reset,
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=300,
            )
        },
        secondary={
            account.id: _make_usage(
                account.id,
                window="secondary",
                used_percent=10.0,
                reset_at=secondary_reset,
                recorded_at=_epoch_to_naive_utc(now - 10),
                window_minutes=10080,
            )
        },
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert account.status == AccountStatus.QUOTA_EXCEEDED
    assert account.reset_at == secondary_reset
    assert account.blocked_at == blocked_at
    assert accounts_repo.status_updates == []


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_ignores_active_accounts() -> None:
    account = _make_account("acc_active", status=AccountStatus.ACTIVE)
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository()

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert accounts_repo.status_updates == []
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_keeps_rate_limited_when_primary_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    future_reset = int(now + 3600)
    blocked_at = int(now - 130)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_rate_limited_stale",
        status=AccountStatus.RATE_LIMITED,
        reset_at=future_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=10.0,
                reset_at=future_reset,
                recorded_at=_epoch_to_naive_utc(blocked_at - 30),
                window_minutes=300,
            )
        }
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert accounts_repo.status_updates == []
    assert account.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_reconcile_recoverable_account_statuses_keeps_rate_limited_when_reset_elapsed_but_primary_predates_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    past_reset = int(now - 300)
    blocked_at = int(now - 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr("app.modules.proxy.load_balancer.utcnow", lambda: _epoch_to_naive_utc(now))

    account = _make_account(
        "acc_rate_limited_stale_pre_block",
        status=AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
        blocked_at=blocked_at,
    )
    accounts_repo = StubAccountsRepository([account])
    usage_repo = StubUsageRepository(
        primary={
            account.id: _make_usage(
                account.id,
                window="primary",
                used_percent=10.0,
                reset_at=past_reset,
                recorded_at=_epoch_to_naive_utc(blocked_at - 30),
                window_minutes=300,
            )
        }
    )

    recovered = await refresh_scheduler_module.reconcile_recoverable_account_statuses(
        accounts_repo=accounts_repo,
        usage_repo=usage_repo,
        accounts=[account],
    )

    assert recovered == 0
    assert accounts_repo.status_updates == []
    assert account.status == AccountStatus.RATE_LIMITED
    assert account.reset_at == past_reset
    assert account.blocked_at == blocked_at
