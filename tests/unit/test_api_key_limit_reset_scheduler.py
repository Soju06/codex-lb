from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.modules.api_keys.reset_scheduler as reset_scheduler

pytestmark = pytest.mark.unit


def test_build_api_key_limit_reset_scheduler_uses_fixed_hourly_interval() -> None:
    scheduler = reset_scheduler.build_api_key_limit_reset_scheduler()

    assert scheduler.interval_seconds == 3600
    assert scheduler.enabled is True


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
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))

    monkeypatch.setattr(reset_scheduler, "_get_leader_election", lambda: leader)

    with (
        patch.object(reset_scheduler, "get_background_session", FakeSession),
        patch.object(reset_scheduler, "ApiKeysRepository", return_value=repo),
    ):
        await scheduler._reset_once()

    leader.try_acquire.assert_awaited_once()
    repo.reset_expired_limits.assert_awaited_once()
    repo.release_stale_usage_reservations.assert_awaited_once()
