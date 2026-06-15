from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients.rate_limit_reset_credits import (
    RateLimitResetCreditPayload,
    RateLimitResetCreditsFetchError,
    RateLimitResetCreditsPayload,
)
from app.core.usage import refresh_scheduler as refresh_scheduler_module
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountRateLimitResetCreditRecord
from app.modules.accounts.reset_credit_updater import ResetCreditUpdater

pytestmark = pytest.mark.unit


def _account(account_id: str, *, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access-token",
        refresh_token_encrypted=b"refresh-token",
        id_token_encrypted=b"id-token",
        last_refresh=datetime(2025, 1, 1),
        status=status,
    )


class StubAccountsRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.inserted: list[AccountRateLimitResetCreditRecord] = []

    async def expire_rate_limit_reset_credits(
        self,
        *,
        now: datetime | None = None,
        account_id: str | None = None,
    ) -> int:
        self.calls.append(("expire", cast(str, account_id), now))
        return 0

    async def insert_rate_limit_reset_credits_if_missing(
        self,
        credits: list[AccountRateLimitResetCreditRecord],
    ) -> int:
        self.calls.append(("insert", credits[0].account_id if credits else "", list(credits)))
        self.inserted.extend(credits)
        return len(credits)


@pytest.mark.asyncio
async def test_reset_credit_updater_expires_before_fetch_and_inserts_new_rows() -> None:
    now = datetime(2026, 6, 15, 12, 0, 0)
    repo = StubAccountsRepository()
    events: list[tuple[str, str]] = []

    async def fetcher(*, access_token: str, account_id: str | None, **_: object) -> RateLimitResetCreditsPayload:
        events.append(("fetch", cast(str, account_id)))
        assert access_token == "decrypted-token"
        return RateLimitResetCreditsPayload(
            credits=[
                RateLimitResetCreditPayload(
                    id="RateLimitResetCredit_one",
                    status="available",
                    granted_at=datetime(2026, 6, 12, 1, 29, 41),
                    expires_at=datetime(2026, 7, 12, 1, 29, 41),
                    redeemed_at=None,
                )
            ],
            available_count=1,
        )

    updater = ResetCreditUpdater(
        repo,
        fetcher=fetcher,
        now=lambda: now,
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    inserted = await updater.refresh_accounts([_account("acc_one")])

    assert inserted == 1
    assert [call[0] for call in repo.calls] == ["expire", "insert"]
    assert events == [("fetch", "workspace-acc_one")]
    assert repo.inserted == [
        AccountRateLimitResetCreditRecord(
            account_id="acc_one",
            credit_id="RateLimitResetCredit_one",
            status="available",
            granted_at=datetime(2026, 6, 12, 1, 29, 41),
            expires_at=datetime(2026, 7, 12, 1, 29, 41),
            redeemed_at=None,
        )
    ]


@pytest.mark.asyncio
async def test_reset_credit_updater_isolates_per_account_failures() -> None:
    repo = StubAccountsRepository()

    async def fetcher(*, account_id: str | None, **_: object) -> RateLimitResetCreditsPayload:
        if account_id == "workspace-acc_fail":
            raise RateLimitResetCreditsFetchError(503, "busy")
        return RateLimitResetCreditsPayload(
            credits=[
                RateLimitResetCreditPayload(
                    id="RateLimitResetCredit_two",
                    status="available",
                    granted_at=datetime(2026, 6, 12, 1, 29, 41),
                    expires_at=datetime(2026, 7, 12, 1, 29, 41),
                    redeemed_at=None,
                )
            ],
            available_count=1,
        )

    updater = ResetCreditUpdater(
        repo,
        fetcher=fetcher,
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    inserted = await updater.refresh_accounts([_account("acc_fail"), _account("acc_ok")])

    assert inserted == 1
    assert [credit.account_id for credit in repo.inserted] == ["acc_ok"]
    assert [call[:2] for call in repo.calls] == [
        ("expire", "acc_fail"),
        ("expire", "acc_ok"),
        ("insert", "acc_ok"),
    ]


@pytest.mark.asyncio
async def test_reset_credit_updater_empty_accounts_returns_zero_without_repo_calls() -> None:
    repo = StubAccountsRepository()

    updater = ResetCreditUpdater(
        repo,
        fetcher=AsyncMock(),
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    inserted = await updater.refresh_accounts([])

    assert inserted == 0
    assert repo.calls == []


@pytest.mark.asyncio
async def test_reset_credit_updater_empty_credits_payload_inserts_zero() -> None:
    repo = StubAccountsRepository()

    async def fetcher(**_: object) -> RateLimitResetCreditsPayload:
        return RateLimitResetCreditsPayload(credits=[], available_count=0)

    updater = ResetCreditUpdater(
        repo,
        fetcher=fetcher,
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    inserted = await updater.refresh_accounts([_account("acc_one")])

    assert inserted == 0
    assert repo.inserted == []
    assert [call[0] for call in repo.calls] == ["expire", "insert"]


@pytest.mark.asyncio
async def test_reset_credit_updater_all_accounts_fail_returns_zero() -> None:
    repo = StubAccountsRepository()

    async def fetcher(*, account_id: str | None, **_: object) -> RateLimitResetCreditsPayload:
        raise RateLimitResetCreditsFetchError(503, "busy")

    updater = ResetCreditUpdater(
        repo,
        fetcher=fetcher,
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    inserted = await updater.refresh_accounts([_account("acc_one"), _account("acc_two")])

    assert inserted == 0
    assert repo.inserted == []
    assert [call[:2] for call in repo.calls] == [
        ("expire", "acc_one"),
        ("expire", "acc_two"),
    ]


@pytest.mark.asyncio
async def test_reset_credit_updater_skips_reauth_and_deactivated_accounts() -> None:
    repo = StubAccountsRepository()
    fetched: list[str] = []

    async def fetcher(*, account_id: str | None, **_: object) -> RateLimitResetCreditsPayload:
        fetched.append(cast(str, account_id))
        return RateLimitResetCreditsPayload(credits=[], available_count=0)

    updater = ResetCreditUpdater(
        repo,
        fetcher=fetcher,
        decrypt_token=lambda _: "decrypted-token",
        route_resolver=AsyncMock(return_value=None),
    )

    accounts = [
        _account("acc_reauth", status=AccountStatus.REAUTH_REQUIRED),
        _account("acc_deactivated", status=AccountStatus.DEACTIVATED),
        _account("acc_active"),
    ]

    inserted = await updater.refresh_accounts(accounts)

    assert inserted == 0
    assert fetched == ["workspace-acc_active"]
    assert [call[:2] for call in repo.calls] == [
        ("expire", "acc_active"),
        ("insert", ""),
    ]


class FakeSession:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class StubUsageRepository:
    async def latest_by_account(self, window: str | None = None) -> dict[str, UsageHistory]:
        return {}


@pytest.mark.asyncio
async def test_usage_refresh_scheduler_runs_reset_credit_updater() -> None:
    scheduler = refresh_scheduler_module.UsageRefreshScheduler(interval_seconds=60, enabled=True)
    accounts = [_account("acc_one"), _account("acc_two")]
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    usage_updater = SimpleNamespace(refresh_accounts=AsyncMock(return_value=False))
    reset_updater = SimpleNamespace(refresh_accounts=AsyncMock(return_value=1))

    monkeypatches = pytest.MonkeyPatch()
    monkeypatches.setattr(refresh_scheduler_module, "_get_leader_election", lambda: leader)
    monkeypatches.setattr(refresh_scheduler_module, "get_background_session", FakeSession)
    monkeypatches.setattr(refresh_scheduler_module, "UsageRepository", lambda session: StubUsageRepository())
    monkeypatches.setattr(
        refresh_scheduler_module,
        "AccountsRepository",
        lambda session: SimpleNamespace(list_accounts=AsyncMock(return_value=accounts)),
    )
    monkeypatches.setattr(refresh_scheduler_module, "AdditionalUsageRepository", lambda session: object())
    monkeypatches.setattr(refresh_scheduler_module, "SettingsRepository", lambda session: object())
    monkeypatches.setattr(refresh_scheduler_module, "LimitWarmupRepository", lambda session: object())
    monkeypatches.setattr(refresh_scheduler_module, "RequestLogsRepository", lambda session: object())
    monkeypatches.setattr(refresh_scheduler_module, "UsageUpdater", lambda *args: usage_updater)
    monkeypatches.setattr(refresh_scheduler_module, "ResetCreditUpdater", lambda repo: reset_updater)
    monkeypatches.setattr(
        refresh_scheduler_module,
        "get_rate_limit_headers_cache",
        lambda: SimpleNamespace(invalidate=AsyncMock()),
    )
    monkeypatches.setattr(
        refresh_scheduler_module,
        "get_account_selection_cache",
        lambda: SimpleNamespace(invalidate=lambda: None),
    )

    try:
        await scheduler._refresh_once()
    finally:
        monkeypatches.undo()

    leader.try_acquire.assert_awaited_once()
    reset_updater.refresh_accounts.assert_awaited_once_with(accounts)
    usage_updater.refresh_accounts.assert_awaited_once_with(accounts, {})
