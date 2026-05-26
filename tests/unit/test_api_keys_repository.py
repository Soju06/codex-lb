from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import LimitWindow
from app.modules.api_keys.repository import ApiKeyAccountCost, ApiKeysRepository

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_reset_expired_limits_counts_successful_updates_without_rowcount() -> None:
    session = AsyncMock()
    repo = ApiKeysRepository(session)
    now = datetime(2026, 4, 23, 12, 0, 0)
    expired_limits = [
        SimpleNamespace(id=101, reset_at=now - timedelta(days=2), limit_window=LimitWindow.DAILY),
        SimpleNamespace(id=102, reset_at=now - timedelta(days=14), limit_window=LimitWindow.WEEKLY),
    ]

    executed_sql: list[str] = []

    async def _execute(statement):
        executed_sql.append(str(statement))
        call_index = len(executed_sql)
        if call_index == 1:
            return SimpleNamespace(all=lambda: expired_limits)
        if call_index == 2:
            return SimpleNamespace(rowcount=-1, scalar_one_or_none=lambda: 101)
        if call_index == 3:
            return SimpleNamespace(rowcount=-1, scalar_one_or_none=lambda: None)
        if call_index == 4:
            return SimpleNamespace(all=lambda: [])
        raise AssertionError(f"unexpected execute call {call_index}")

    session.execute.side_effect = _execute

    reset_count = await repo.reset_expired_limits(now=now)

    assert reset_count == 1
    assert len(executed_sql) == 4
    assert "RETURNING api_key_limits.id" in executed_sql[1]
    assert "RETURNING api_key_limits.id" in executed_sql[2]
    session.commit.assert_awaited_once()


class TestUsage7dByAccount:
    @pytest.mark.asyncio
    async def test_groups_known_accounts_sorted_by_cost_descending(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        since = datetime(2026, 5, 1, 0, 0, 0)
        until = datetime(2026, 5, 8, 0, 0, 0)

        rows = [
            SimpleNamespace(account_id="acc_1", email="alice@example.com", is_deleted=False, cost_usd=1.5),
            SimpleNamespace(account_id="acc_2", email="bob@example.com", is_deleted=False, cost_usd=3.2),
        ]

        session.execute.return_value = SimpleNamespace(all=lambda: rows)

        result = await repo.usage_7d_by_account("key_1", since, until)

        assert len(result) == 2
        assert result[0] == ApiKeyAccountCost(
            account_id="acc_2",
            email="bob@example.com",
            cost_usd=3.2,
            is_deleted=False,
        )
        assert result[1] == ApiKeyAccountCost(
            account_id="acc_1",
            email="alice@example.com",
            cost_usd=1.5,
            is_deleted=False,
        )

    @pytest.mark.asyncio
    async def test_deleted_account_bucket_is_sorted_by_cost_without_folding_unknown_account_usage(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        since = datetime(2026, 5, 1, 0, 0, 0)
        until = datetime(2026, 5, 8, 0, 0, 0)

        rows = [
            SimpleNamespace(account_id="acc_1", email="alice@example.com", is_deleted=False, cost_usd=1.0),
            SimpleNamespace(account_id=None, email=None, is_deleted=False, cost_usd=0.5),
            SimpleNamespace(account_id="acc_del", email=None, is_deleted=True, cost_usd=0.8),
        ]

        session.execute.return_value = SimpleNamespace(all=lambda: rows)

        result = await repo.usage_7d_by_account("key_1", since, until)

        assert len(result) == 3
        assert result[0].account_id == "acc_1"
        assert result[0].cost_usd == 1.0
        assert result[0].is_deleted is False
        assert result[1] == ApiKeyAccountCost(
            account_id=None,
            email=None,
            cost_usd=0.8,
            is_deleted=True,
        )
        assert result[2] == ApiKeyAccountCost(
            account_id=None,
            email=None,
            cost_usd=0.5,
            is_deleted=False,
        )

    @pytest.mark.asyncio
    async def test_zero_cost_rows_excluded(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        since = datetime(2026, 5, 1, 0, 0, 0)
        until = datetime(2026, 5, 8, 0, 0, 0)

        rows = [
            SimpleNamespace(account_id="acc_1", email="alice@example.com", is_deleted=False, cost_usd=0.0),
            SimpleNamespace(account_id="acc_2", email="bob@example.com", is_deleted=False, cost_usd=2.0),
        ]

        session.execute.return_value = SimpleNamespace(all=lambda: rows)

        result = await repo.usage_7d_by_account("key_1", since, until)

        assert len(result) == 1
        assert result[0].account_id == "acc_2"

    @pytest.mark.asyncio
    async def test_empty_result_when_no_logs(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        since = datetime(2026, 5, 1, 0, 0, 0)
        until = datetime(2026, 5, 8, 0, 0, 0)

        session.execute.return_value = SimpleNamespace(all=lambda: [])

        result = await repo.usage_7d_by_account("key_1", since, until)

        assert result == []


class TestAccountLookupQueries:
    @pytest.mark.asyncio
    async def test_list_all_accounts_only_loads_identity_and_plan_type(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        statements: list[str] = []

        async def _execute(statement):
            statements.append(str(statement))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        session.execute.side_effect = _execute

        result = await repo.list_all_accounts()

        assert result == []
        assert len(statements) == 1
        assert "access_token_encrypted" not in statements[0]
        assert "refresh_token_encrypted" not in statements[0]
        assert "id_token_encrypted" not in statements[0]
        assert "plan_type" in statements[0]

    @pytest.mark.asyncio
    async def test_list_accounts_by_ids_only_loads_identity_and_plan_type(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        statements: list[str] = []

        async def _execute(statement):
            statements.append(str(statement))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        session.execute.side_effect = _execute

        result = await repo.list_accounts_by_ids(["acc_1", "acc_2"])

        assert result == []
        assert len(statements) == 1
        assert "access_token_encrypted" not in statements[0]
        assert "refresh_token_encrypted" not in statements[0]
        assert "id_token_encrypted" not in statements[0]
        assert "plan_type" in statements[0]


class TestUsage7d:
    @pytest.mark.asyncio
    async def test_returns_totals_and_account_costs_from_single_execute(self) -> None:
        session = AsyncMock()
        repo = ApiKeysRepository(session)
        since = datetime(2026, 5, 1, 0, 0, 0)
        until = datetime(2026, 5, 8, 0, 0, 0)

        rows = [
            SimpleNamespace(
                total_requests=3,
                total_input_tokens=200,
                total_output_tokens=50,
                cached_input_tokens=25,
                total_cost_usd=1.8,
                account_id="acc_1",
                email="alice@example.com",
                is_deleted=False,
                cost_usd=1.0,
            ),
            SimpleNamespace(
                total_requests=3,
                total_input_tokens=200,
                total_output_tokens=50,
                cached_input_tokens=25,
                total_cost_usd=1.8,
                account_id="acc_del",
                email=None,
                is_deleted=True,
                cost_usd=0.8,
            ),
        ]

        session.execute.return_value = SimpleNamespace(all=lambda: rows)

        result = await repo.usage_7d("key_1", since, until)

        assert result.total_requests == 3
        assert result.total_tokens == 250
        assert result.cached_input_tokens == 25
        assert result.total_cost_usd == 1.8
        assert result.account_costs == [
            ApiKeyAccountCost(
                account_id="acc_1",
                email="alice@example.com",
                cost_usd=1.0,
                is_deleted=False,
            ),
            ApiKeyAccountCost(
                account_id=None,
                email=None,
                cost_usd=0.8,
                is_deleted=True,
            ),
        ]
        session.execute.assert_awaited_once()
