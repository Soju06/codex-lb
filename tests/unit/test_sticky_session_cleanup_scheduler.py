from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

import app.modules.sticky_sessions.cleanup_scheduler as cleanup_scheduler
from app.core.config.settings import Settings
from app.db.models import DashboardSettings

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
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=600,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=600,
    )

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_idle_ttl_seconds=120.0,
            http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=5)
    sticky_repo.purge_before = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.purge_closed_before = AsyncMock(return_value=2)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=1)
    ring_service = AsyncMock()
    ring_service.purge_stale_before = AsyncMock(return_value=0)

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
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "RingMembershipService", return_value=ring_service),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    sticky_repo.purge_before.assert_not_called()
    bridge_repo.purge_closed_before.assert_called_once()
    bridge_repo.purge_abandoned_before.assert_called_once()
    ring_service.purge_stale_before.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_once_skips_bridge_purge_when_schema_is_not_ready(monkeypatch) -> None:
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=600,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=600,
    )

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_idle_ttl_seconds=120.0,
            http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.purge_closed_before = AsyncMock(return_value=0)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    ring_service = AsyncMock()
    ring_service.purge_stale_before = AsyncMock(return_value=0)

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
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "RingMembershipService", return_value=ring_service),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", False),
        patch.object(
            cleanup_scheduler,
            "missing_durable_bridge_tables",
            AsyncMock(return_value=("http_bridge_sessions",)),
        ),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    bridge_repo.purge_closed_before.assert_not_called()
    bridge_repo.purge_abandoned_before.assert_not_called()
    ring_service.purge_stale_before.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_once_purges_bridge_when_schema_exists_after_startup_flag_reset(monkeypatch) -> None:
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=600,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=600,
    )

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_idle_ttl_seconds=120.0,
            http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.purge_closed_before = AsyncMock(return_value=1)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    ring_service = AsyncMock()
    ring_service.purge_stale_before = AsyncMock(return_value=2)

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
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "RingMembershipService", return_value=ring_service),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", False),
        patch.object(cleanup_scheduler, "missing_durable_bridge_tables", AsyncMock(return_value=())),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    bridge_repo.purge_closed_before.assert_called_once()
    bridge_repo.purge_abandoned_before.assert_called_once()
    ring_service.purge_stale_before.assert_called_once()


def test_abandoned_bridge_retention_covers_prompt_cache_reuse_window() -> None:
    """Abandoned-row retention must be at least the longest bridge reuse TTL."""
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
    )
    app_settings = SimpleNamespace(
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
    )

    retention = cleanup_scheduler._abandoned_bridge_retention_seconds(
        cast(DashboardSettings, dashboard_settings),
        cast(Settings, app_settings),
    )

    assert retention == 3600.0

    app_settings.http_responses_session_bridge_codex_idle_ttl_seconds = 7200.0
    retention = cleanup_scheduler._abandoned_bridge_retention_seconds(
        cast(DashboardSettings, dashboard_settings),
        cast(Settings, app_settings),
    )
    assert retention == 7200.0


@pytest.mark.asyncio
async def test_cleanup_once_gates_abandoned_purge_on_prompt_cache_reuse_ttl(monkeypatch) -> None:
    """An in-reuse-window prompt-cache session must not have its ACTIVE durable
    row purged: the abandoned cutoff must honor the prompt-cache idle TTL even
    when the affinity max age is shorter."""
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
    )

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_idle_ttl_seconds=120.0,
            http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.purge_closed_before = AsyncMock(return_value=0)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    ring_service = AsyncMock()
    ring_service.purge_stale_before = AsyncMock(return_value=0)

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
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "RingMembershipService", return_value=ring_service),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    closed_cutoff = bridge_repo.purge_closed_before.call_args.args[0]
    abandoned_cutoff = bridge_repo.purge_abandoned_before.call_args.args[0]
    # Closed rows use the 1800s affinity cutoff; abandoned ACTIVE/DRAINING rows
    # must be retained for the full 3600s prompt-cache reuse window.
    gap_seconds = (closed_cutoff - abandoned_cutoff).total_seconds()
    assert abs(gap_seconds - 1800.0) < 5.0
