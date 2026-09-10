from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.modules.dashboard import repository as dashboard_repository_module
from app.modules.dashboard.repository import DashboardRepository


class _RecordingUsageRepo:
    def __init__(self, deltas: dict[str, float]) -> None:
        self.calls: list[tuple[tuple[tuple[str, str], ...], datetime, datetime]] = []
        self._deltas = deltas

    async def positive_used_percent_deltas_by_account(self, account_windows, *, since, until):
        self.calls.append((tuple(account_windows.items()), since, until))
        return dict(self._deltas) if account_windows else {}


def _repo(deltas: dict[str, float] | None = None) -> tuple[DashboardRepository, _RecordingUsageRepo]:
    repo = DashboardRepository(MagicMock())
    usage_repo = _RecordingUsageRepo(deltas or {"acc_a": 12.5})
    cast(Any, repo)._usage_repo = usage_repo
    return repo, usage_repo


_NOW = datetime(2026, 9, 9, 12, 0, 0)
_WINDOW = {"since": _NOW - timedelta(days=7), "until": _NOW}
_WEEK_SECONDS = 7 * 24 * 3600


def test_autouse_fixture_zeroes_ttl_and_clears_cache() -> None:
    assert dashboard_repository_module._TRAILING_DEMAND_TTL_SECONDS == 0.0
    assert dashboard_repository_module._trailing_demand_cache == {}


@pytest.mark.asyncio
async def test_zero_ttl_disables_memoization() -> None:
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)

    assert len(usage_repo.calls) == 2
    assert dashboard_repository_module._trailing_demand_cache == {}


@pytest.mark.asyncio
async def test_same_signature_within_ttl_queries_once(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    first = await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary", "acc_b": "primary"}, **_WINDOW)
    # Insertion order and the drifting window must not defeat the memo.
    drifted = {"since": _WINDOW["since"] + timedelta(seconds=15), "until": _WINDOW["until"] + timedelta(seconds=15)}
    second = await repo.positive_used_percent_deltas_by_account({"acc_b": "primary", "acc_a": "secondary"}, **drifted)

    assert len(usage_repo.calls) == 1
    assert first == second == {"acc_a": 12.5}


@pytest.mark.asyncio
async def test_miss_forwards_signature_and_window_bounds(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)

    assert usage_repo.calls == [((("acc_a", "secondary"),), _WINDOW["since"], _WINDOW["until"])]


@pytest.mark.asyncio
async def test_different_window_span_is_its_own_entry(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    await repo.positive_used_percent_deltas_by_account(
        {"acc_a": "secondary"}, since=_NOW - timedelta(hours=3), until=_NOW
    )

    assert len(usage_repo.calls) == 2
    assert set(dashboard_repository_module._trailing_demand_cache) == {
        (_WEEK_SECONDS, (("acc_a", "secondary"),)),
        (3 * 3600, (("acc_a", "secondary"),)),
    }


@pytest.mark.asyncio
async def test_empty_signature_is_memoized_as_empty(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    first = await repo.positive_used_percent_deltas_by_account({}, **_WINDOW)
    second = await repo.positive_used_percent_deltas_by_account({}, **_WINDOW)

    assert first == second == {}
    assert len(usage_repo.calls) == 1
    assert (_WEEK_SECONDS, ()) in dashboard_repository_module._trailing_demand_cache


@pytest.mark.asyncio
async def test_hit_returns_a_copy(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, _ = _repo()

    first = await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    first["acc_a"] = -1.0
    second = await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)

    assert second == {"acc_a": 12.5}


@pytest.mark.asyncio
async def test_different_signature_queries_independently(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    await repo.positive_used_percent_deltas_by_account({"acc_a": "primary"}, **_WINDOW)
    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary", "acc_b": "secondary"}, **_WINDOW)

    assert len(usage_repo.calls) == 3


@pytest.mark.asyncio
async def test_expired_entry_is_recomputed(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    key = (_WEEK_SECONDS, (("acc_a", "secondary"),))
    deltas, _expires_at = dashboard_repository_module._trailing_demand_cache[key]
    dashboard_repository_module._trailing_demand_cache[key] = (deltas, time.monotonic() - 1.0)

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)

    assert len(usage_repo.calls) == 2
    _deltas, refreshed_expires_at = dashboard_repository_module._trailing_demand_cache[key]
    assert refreshed_expires_at > time.monotonic()


@pytest.mark.asyncio
async def test_clear_forces_recompute(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()

    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)
    dashboard_repository_module._clear_trailing_demand_cache()
    await repo.positive_used_percent_deltas_by_account({"acc_a": "secondary"}, **_WINDOW)

    assert len(usage_repo.calls) == 2


@pytest.mark.asyncio
async def test_cache_evicts_oldest_entry_at_capacity(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    repo, usage_repo = _repo()
    capacity = dashboard_repository_module._TRAILING_DEMAND_MAX_ENTRIES

    for index in range(capacity + 1):
        await repo.positive_used_percent_deltas_by_account({f"acc_{index}": "secondary"}, **_WINDOW)
    await repo.positive_used_percent_deltas_by_account({"acc_0": "secondary"}, **_WINDOW)

    assert len(dashboard_repository_module._trailing_demand_cache) == capacity
    assert len(usage_repo.calls) == capacity + 2
