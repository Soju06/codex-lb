from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.proxy.api as proxy_api_module
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def _make_account(account_id: str, plan_type: str = "plus") -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"b",
        id_token_encrypted=b"c",
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_build_account_pool_usage_limits_latest_queries_to_assigned_accounts(monkeypatch) -> None:
    assigned_account_ids = ["acc-a", "acc-b"]
    latest_calls: list[tuple[str, list[str] | None]] = []
    session = AsyncSession.__new__(AsyncSession)

    class FakeApiKeysRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def list_accounts_by_ids(self, account_ids: list[str]) -> list[Account]:
            assert account_ids == assigned_account_ids
            return [_make_account(account_id) for account_id in account_ids]

        async def list_all_accounts(self) -> list[Account]:
            raise AssertionError("scoped account pool usage should not load all accounts")

    class FakeUsageRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def latest_by_account(
            self,
            window: str,
            *,
            account_ids: list[str] | None = None,
        ) -> dict[str, object]:
            latest_calls.append((window, account_ids))
            return {}

    monkeypatch.setattr("app.modules.api_keys.repository.ApiKeysRepository", FakeApiKeysRepository)
    monkeypatch.setattr(proxy_api_module, "UsageRepository", FakeUsageRepository)

    result = await proxy_api_module._build_account_pool_usage(
        session=session,
        assigned_account_ids=assigned_account_ids,
        account_assignment_scope_enabled=True,
    )

    assert latest_calls == [
        ("primary", assigned_account_ids),
        ("secondary", assigned_account_ids),
    ]
    assert result is not None
    # No live primary sample -> the pooled primary percent reads absent.
    assert result.primary is None
    assert result.secondary == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_build_account_pool_usage_returns_object_when_pool_has_no_capacity(monkeypatch) -> None:
    session = AsyncSession.__new__(AsyncSession)

    class FakeApiKeysRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def list_accounts_by_ids(self, _account_ids: list[str]) -> list[Account]:
            raise AssertionError("unscoped account pool usage should not load assigned accounts")

        async def list_all_accounts(self) -> list[Account]:
            return []

    class FakeUsageRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def latest_by_account(
            self,
            window: str,
            *,
            account_ids: list[str] | None = None,
        ) -> dict[str, object]:
            assert account_ids is None
            return {}

    monkeypatch.setattr("app.modules.api_keys.repository.ApiKeysRepository", FakeApiKeysRepository)
    monkeypatch.setattr(proxy_api_module, "UsageRepository", FakeUsageRepository)

    result = await proxy_api_module._build_account_pool_usage(
        session=session,
        assigned_account_ids=[],
        account_assignment_scope_enabled=False,
    )

    assert result is not None
    assert result.primary is None
    assert result.secondary is None
