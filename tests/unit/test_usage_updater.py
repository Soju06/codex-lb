from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.usage.updater import UsageUpdater

pytestmark = pytest.mark.unit


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
    def __init__(self) -> None:
        self.entries: list[UsageEntry] = []

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
        return None


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


class StubAccountsRepository:
    def __init__(self) -> None:
        self.status_updates: list[dict[str, Any]] = []

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
            }
        )
        return True

    async def update_tokens(self, *args: Any, **kwargs: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_usage_updater_deactivates_on_4xx_error(monkeypatch) -> None:
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

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 1
    update = accounts_repo.status_updates[0]
    assert update["account_id"] == "acc_402"
    assert update["status"] == AccountStatus.DEACTIVATED
    assert "402" in update["deactivation_reason"]
    assert "Payment Required" in update["deactivation_reason"]


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

    await updater.refresh_accounts([acc], latest_usage={})

    assert len(accounts_repo.status_updates) == 0


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
