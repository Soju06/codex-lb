from __future__ import annotations

import pytest

from app.core.resilience import retry_budget as retry_budget_module
from app.core.resilience.retry_budget import RetryBudget

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_retry_budget_allows_retries_up_to_limit():
    budget = RetryBudget(max_retries_per_window=3, window_seconds=60)

    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is True
    assert budget.remaining() == 0


@pytest.mark.asyncio
async def test_retry_budget_rejects_after_limit():
    budget = RetryBudget(max_retries_per_window=2, window_seconds=60)

    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is False


@pytest.mark.asyncio
async def test_retry_budget_allows_new_retry_after_window_expires(monkeypatch: pytest.MonkeyPatch):
    values = [100.0, 101.0, 107.0, 107.0]
    state = {"index": 0}

    def fake_monotonic() -> float:
        index = state["index"]
        if index < len(values):
            state["index"] = index + 1
            return values[index]
        return values[-1]

    monkeypatch.setattr(retry_budget_module.time, "monotonic", fake_monotonic)

    budget = RetryBudget(max_retries_per_window=2, window_seconds=5)

    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is True
    assert await budget.try_acquire() is True
    assert budget.remaining() == 1
