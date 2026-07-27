from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TypeVar, cast

import pytest

from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._service.rate_limit import _RateLimitMixin
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit

_NOW_EPOCH = 1_700_000_000
_T = TypeVar("_T")


class _SharedSessionGuard:
    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls: list[str] = []

    async def run(self, label: str, result: _T) -> _T:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.in_flight > 1:
            self.in_flight -= 1
            raise AssertionError(f"overlapping shared-session operation: {label}")
        self.calls.append(label)
        try:
            await asyncio.sleep(0)
            return result
        finally:
            self.in_flight -= 1


class _GuardedAccountsRepository:
    def __init__(self, guard: _SharedSessionGuard, accounts: list[Account]) -> None:
        self._guard = guard
        self._accounts = accounts

    async def list_accounts(self) -> list[Account]:
        return await self._guard.run("accounts", list(self._accounts))


class _GuardedUsageRepository:
    def __init__(
        self,
        guard: _SharedSessionGuard,
        rows: dict[str | None, dict[str, UsageHistory]],
    ) -> None:
        self._guard = guard
        self._rows = rows

    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        label = window or "credits"
        return await self._guard.run(f"usage:{label}", dict(self._rows[window]))


class _GuardedAdditionalUsageRepository:
    def __init__(self, guard: _SharedSessionGuard) -> None:
        self._guard = guard

    async def list_limit_names(self, *, account_ids: list[str] | None = None) -> list[str]:
        del account_ids
        return await self._guard.run("additional:list_limit_names", [])


class _TestRateLimitService(_RateLimitMixin):
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self.refresh_calls = 0

    async def _refresh_usage(self, repos: ProxyRepositories, accounts: list[Account]) -> None:
        del repos, accounts
        self.refresh_calls += 1


def _account(account_id: str, *, plan_type: str) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
    )


def _usage(
    entry_id: int,
    *,
    account_id: str,
    window: str,
    used_percent: float,
    reset_after: int,
    window_minutes: int,
    credits_balance: float | None = None,
) -> UsageHistory:
    return UsageHistory(
        id=entry_id,
        account_id=account_id,
        recorded_at=datetime(2025, 1, 1),
        window=window,
        used_percent=used_percent,
        reset_at=_NOW_EPOCH + reset_after,
        window_minutes=window_minutes,
        credits_has=credits_balance is not None,
        credits_unlimited=False if credits_balance is not None else None,
        credits_balance=credits_balance,
    )


def _service_and_guard() -> tuple[_TestRateLimitService, _SharedSessionGuard]:
    guard = _SharedSessionGuard()
    plus_account = _account("plus", plan_type="plus")
    free_account = _account("free", plan_type="free")
    primary = _usage(
        1,
        account_id=plus_account.id,
        window="primary",
        used_percent=20.0,
        reset_after=300,
        window_minutes=300,
    )
    secondary = _usage(
        2,
        account_id=plus_account.id,
        window="secondary",
        used_percent=40.0,
        reset_after=604_800,
        window_minutes=10_080,
    )
    monthly = _usage(
        3,
        account_id=free_account.id,
        window="monthly",
        used_percent=60.0,
        reset_after=2_592_000,
        window_minutes=43_200,
        credits_balance=8.75,
    )
    rows = {
        "primary": {plus_account.id: primary},
        "secondary": {plus_account.id: secondary},
        "monthly": {free_account.id: monthly},
        None: {plus_account.id: primary, free_account.id: monthly},
    }

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[ProxyRepositories]:
        yield ProxyRepositories(
            accounts=cast(
                AccountsRepository,
                _GuardedAccountsRepository(guard, [plus_account, free_account]),
            ),
            usage=cast(UsageRepository, _GuardedUsageRepository(guard, rows)),
            request_logs=cast(RequestLogsRepository, object()),
            sticky_sessions=cast(StickySessionsRepository, object()),
            api_keys=cast(ApiKeysRepository, object()),
            additional_usage=cast(
                AdditionalUsageRepository,
                _GuardedAdditionalUsageRepository(guard),
            ),
            quota_planner=cast(QuotaPlannerRepository, object()),
        )

    return _TestRateLimitService(repo_factory), guard


@pytest.mark.asyncio
async def test_rate_limit_headers_serialize_usage_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.proxy._service.rate_limit.time.time", lambda: _NOW_EPOCH)
    service, guard = _service_and_guard()

    headers = await service._compute_rate_limit_headers()

    assert guard.max_in_flight == 1
    assert guard.calls == [
        "accounts",
        "usage:primary",
        "usage:secondary",
        "usage:monthly",
        "usage:credits",
    ]
    assert headers == {
        "x-codex-primary-used-percent": "20.0",
        "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": str(_NOW_EPOCH + 300),
        "x-codex-secondary-used-percent": "40.0",
        "x-codex-secondary-window-minutes": "10080",
        "x-codex-secondary-reset-at": str(_NOW_EPOCH + 604_800),
        "x-codex-monthly-used-percent": "60.0",
        "x-codex-monthly-window-minutes": "43200",
        "x-codex-monthly-reset-at": str(_NOW_EPOCH + 2_592_000),
        "x-codex-credits-has-credits": "true",
        "x-codex-credits-unlimited": "false",
        "x-codex-credits-balance": "8.75",
    }


@pytest.mark.asyncio
async def test_rate_limit_payload_serializes_usage_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.proxy._service.rate_limit.time.time", lambda: _NOW_EPOCH)
    service, guard = _service_and_guard()

    payload = await service.get_rate_limit_payload()

    assert guard.max_in_flight == 1
    assert guard.calls == [
        "accounts",
        "usage:primary",
        "usage:secondary",
        "usage:monthly",
        "additional:list_limit_names",
        "usage:credits",
    ]
    assert service.refresh_calls == 1
    assert payload.plan_type == "plus"
    assert payload.rate_limit is not None
    assert payload.rate_limit.allowed is True
    assert payload.rate_limit.limit_reached is False
    assert payload.rate_limit.primary_window is not None
    assert payload.rate_limit.primary_window.used_percent == 20
    assert payload.rate_limit.primary_window.reset_at == _NOW_EPOCH + 300
    assert payload.rate_limit.secondary_window is not None
    assert payload.rate_limit.secondary_window.used_percent == 40
    assert payload.rate_limit.secondary_window.reset_at == _NOW_EPOCH + 604_800
    assert payload.rate_limit.monthly_window is not None
    assert payload.rate_limit.monthly_window.used_percent == 60
    assert payload.rate_limit.monthly_window.reset_at == _NOW_EPOCH + 2_592_000
    assert payload.credits is not None
    assert payload.credits.has_credits is True
    assert payload.credits.unlimited is False
    assert payload.credits.balance == "8.75"
    assert payload.additional_rate_limits == []
