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
async def test_cleanup_once_purges_both_prompt_cache_and_stale_entries(monkeypatch) -> None:
    """_cleanup_once should purge prompt-cache entries by affinity TTL AND
    stale entries of all kinds by the stale threshold."""
    dashboard_settings = SimpleNamespace(openai_cache_affinity_max_age_seconds=600)
    app_settings = SimpleNamespace(sticky_session_stale_threshold_seconds=86400)

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=5)
    sticky_repo.purge_before = AsyncMock(return_value=3)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(cleanup_scheduler, "get_settings", lambda: app_settings)

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
    # One call per non-prompt-cache kind (STICKY_THREAD, CODEX_SESSION)
    assert sticky_repo.purge_before.call_count == 2

    # Verify the stale cutoff uses the app-level threshold (86400s)
    first_stale_cutoff = sticky_repo.purge_before.call_args_list[0][0][0]
    prompt_cutoff = sticky_repo.purge_prompt_cache_before.call_args[0][0]
    assert first_stale_cutoff < prompt_cutoff  # 86400s ago < 600s ago
