from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import LimitWindow
from app.modules.api_keys.repository import ApiKeysRepository

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
