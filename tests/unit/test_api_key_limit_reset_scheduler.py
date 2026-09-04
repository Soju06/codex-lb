from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import app.modules.api_keys.reset_scheduler as reset_scheduler

pytestmark = pytest.mark.unit


def test_build_api_key_limit_reset_scheduler_uses_fixed_hourly_interval() -> None:
    scheduler = reset_scheduler.build_api_key_limit_reset_scheduler()

    assert scheduler.interval_seconds == 3600
    assert scheduler.enabled is True


@pytest.mark.parametrize(
    ("now", "expected_seconds"),
    [
        (datetime(2026, 9, 4, 23, 49, 30), 30.0),
        (datetime(2026, 9, 4, 23, 50, 0), 0.0),
        (datetime(2026, 9, 4, 23, 50, 1), 86_399.0),
        (datetime(2026, 9, 4, 8, 15, 0), 56_100.0),
    ],
)
def test_seconds_until_daily_limit_alignment_uses_2350_utc(
    now: datetime,
    expected_seconds: float,
) -> None:
    assert reset_scheduler.seconds_until_daily_limit_alignment(now) == expected_seconds


@pytest.mark.asyncio
async def test_reset_once_resets_expired_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.reset_expired_limits = AsyncMock(return_value=3)
    repo.release_stale_usage_reservations = AsyncMock(return_value=2)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    scheduler = reset_scheduler.ApiKeyLimitResetScheduler(interval_seconds=3600, enabled=True)
    gate_calls = 0

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            nonlocal gate_calls
            gate_calls += 1
            return await fn()

    monkeypatch.setattr(reset_scheduler, "_get_leader_election", lambda: _Leader())

    with (
        patch.object(reset_scheduler, "get_background_session", FakeSession),
        patch.object(reset_scheduler, "ApiKeysRepository", return_value=repo),
    ):
        await scheduler._reset_once()

    assert gate_calls == 1
    repo.reset_expired_limits.assert_awaited_once()
    repo.release_stale_usage_reservations.assert_awaited_once()
    release_await_args = repo.release_stale_usage_reservations.await_args
    assert release_await_args is not None
    release_kwargs = release_await_args.kwargs
    # The hard age ceiling must accompany the heartbeat cutoff so an orphaned
    # heartbeat cannot exempt its reservation forever (issue #1594).
    assert (
        release_kwargs["cutoff"] - release_kwargs["max_age_cutoff"]
        == reset_scheduler._MAX_USAGE_RESERVATION_AGE - reset_scheduler._STALE_USAGE_RESERVATION_AGE
    )


@pytest.mark.asyncio
async def test_align_daily_limits_once_is_leader_gated_and_targets_next_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 9, 4, 23, 50, 0)
    repo = AsyncMock()
    repo.align_daily_limit_resets = AsyncMock(return_value=4)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    scheduler = reset_scheduler.ApiKeyLimitResetScheduler(interval_seconds=3600, enabled=True)
    gate_calls = 0

    class _Leader:
        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
            nonlocal gate_calls
            gate_calls += 1
            return await fn()

    monkeypatch.setattr(reset_scheduler, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(reset_scheduler, "utcnow", lambda: fixed_now)

    with (
        patch.object(reset_scheduler, "get_background_session", FakeSession),
        patch.object(reset_scheduler, "ApiKeysRepository", return_value=repo),
    ):
        await scheduler._align_daily_limits_once()

    assert gate_calls == 1
    repo.align_daily_limit_resets.assert_awaited_once_with(reset_at=datetime(2026, 9, 5, 0, 0, 0))
