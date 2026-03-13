from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key
from app.modules.usage.updater import UsageUpdater, _last_successful_refresh

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_refresh_cache():
    """Clear the module-level freshness cache between tests."""
    _last_successful_refresh.clear()
    yield
    _last_successful_refresh.clear()


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
        self.token_updates: list[dict[str, Any]] = []
        self.accounts_by_id: dict[str, Account] = {}

    async def get_by_id(self, account_id: str) -> Account | None:
        return self.accounts_by_id.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        account = self.accounts_by_id.get(account_id)
        if account is not None:
            account.status = status
            account.deactivation_reason = deactivation_reason
            account.reset_at = reset_at
        self.status_updates.append(
            {
                "account_id": account_id,
                "status": status,
                "deactivation_reason": deactivation_reason,
            }
        )
        return True

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
            if isinstance(plan_type, str):
                account.plan_type = plan_type
            if isinstance(email, str):
                account.email = email
            if isinstance(chatgpt_account_id, str):
                account.chatgpt_account_id = chatgpt_account_id
        self.token_updates.append({"account_id": account_id, **kwargs})
        return True


@pytest.mark.asyncio
async def test_usage_updater_does_not_deactivate_on_account_invalid_4xx(monkeypatch) -> None:
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

    assert len(accounts_repo.status_updates) == 0


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
async def test_usage_updater_syncs_plan_type_from_usage_payload(monkeypatch) -> None:
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

    assert acc.plan_type == "plus"
    assert len(accounts_repo.token_updates) == 1
    token_update = accounts_repo.token_updates[0]
    assert token_update["account_id"] == "acc_plan_sync"
    assert token_update["plan_type"] == "plus"
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


@pytest.mark.asyncio
async def test_usage_updater_deactivates_account_when_401_refresh_fails_permanently(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_USAGE_REFRESH_ENABLED", "true")
    from app.core.clients.usage import UsageFetchError
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def stub_fetch_usage_401(**_: Any) -> UsagePayload:
        raise UsageFetchError(401, "Provided authentication token is expired. Please try signing in again.")

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", stub_fetch_usage_401)

    usage_repo = StubUsageRepository(return_rows=True)
    accounts_repo = StubAccountsRepository()
    updater = UsageUpdater(usage_repo, accounts_repo=accounts_repo)
    assert updater._auth_manager is not None

    async def stub_ensure_fresh(account: Account, *, force: bool = False) -> Account:
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = "Refresh token was reused - re-login required"
        raise RefreshError(
            code="refresh_token_reused",
            message="Your refresh token has already been used to generate a new access token. Please try signing in again.",
            is_permanent=True,
        )

    monkeypatch.setattr(updater._auth_manager, "ensure_fresh", stub_ensure_fresh)

    acc = _make_account("acc_401_perm", "workspace_401_perm", email="auth-perm@example.com")
    refreshed = await updater.refresh_accounts([acc], latest_usage={})

    assert refreshed is False
    assert acc.status == AccountStatus.DEACTIVATED
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
