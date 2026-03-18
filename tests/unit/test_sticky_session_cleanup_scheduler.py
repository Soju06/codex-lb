from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.modules.sticky_sessions.cleanup_scheduler as cleanup_scheduler

pytestmark = pytest.mark.unit


def test_build_sticky_session_cleanup_scheduler_respects_enabled_setting(monkeypatch) -> None:
    settings = SimpleNamespace(sticky_session_cleanup_interval_seconds=42, sticky_session_cleanup_enabled=False)
    monkeypatch.setattr(cleanup_scheduler, "get_settings", lambda: settings)

    scheduler = cleanup_scheduler.build_sticky_session_cleanup_scheduler()

    assert scheduler.interval_seconds == 42
    assert scheduler.enabled is False


@pytest.mark.asyncio
async def test_cleanup_once_purges_prompt_cache_only(monkeypatch) -> None:
    """_cleanup_once should purge prompt-cache entries by affinity TTL.
    Durable kinds (STICKY_THREAD, CODEX_SESSION) must NOT be purged."""
    dashboard_settings = SimpleNamespace(openai_cache_affinity_max_age_seconds=600)

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=5)
    sticky_repo.purge_before = AsyncMock(return_value=0)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(
        interval_seconds=60,
        enabled=True,
    )

    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
        patch.object(cleanup_scheduler, "StickySessionsRepository", return_value=sticky_repo),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    sticky_repo.purge_before.assert_not_called()
