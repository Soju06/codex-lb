from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import LoadBalancer
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
