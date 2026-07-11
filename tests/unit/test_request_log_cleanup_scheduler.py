from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.modules.request_logs.cleanup_scheduler as cleanup_scheduler

pytestmark = pytest.mark.unit


def test_builder_uses_retention_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(request_log_cleanup_interval_seconds=42, request_log_retention_days=30),
    )
    scheduler = cleanup_scheduler.build_request_log_cleanup_scheduler()
    assert scheduler.interval_seconds == 42
    assert scheduler.retention_days == 30


@pytest.mark.asyncio
async def test_cleanup_once_uses_retention_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = AsyncMock()
    repository.purge_before = AsyncMock(return_value=3)
    leader = AsyncMock()
    leader.try_acquire = AsyncMock(return_value=True)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_args):
            return None

    scheduler = cleanup_scheduler.RequestLogCleanupScheduler(interval_seconds=60, retention_days=30)
    with (
        patch.object(cleanup_scheduler, "_get_leader_election", return_value=leader),
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "RequestLogsRepository", return_value=repository),
    ):
        assert await scheduler._cleanup_once() == 3

    repository.purge_before.assert_awaited_once()
    await_args = repository.purge_before.await_args
    assert await_args is not None
    cutoff = await_args.args[0]
    assert 29.99 < (cleanup_scheduler.utcnow() - cutoff).total_seconds() / 86400 < 30.01


@pytest.mark.asyncio
async def test_disabled_cleanup_does_not_acquire_leader() -> None:
    scheduler = cleanup_scheduler.RequestLogCleanupScheduler(interval_seconds=60, retention_days=None)
    with patch.object(cleanup_scheduler, "_get_leader_election") as leader:
        assert await scheduler._cleanup_once() == 0
    leader.assert_not_called()


def test_cleanup_readiness_fails_closed_for_single_instance_error_or_staleness() -> None:
    original = cleanup_scheduler.RequestLogCleanupState(
        last_attempt_at=cleanup_scheduler._STATE.last_attempt_at,
        last_success_at=cleanup_scheduler._STATE.last_success_at,
        last_deleted_count=cleanup_scheduler._STATE.last_deleted_count,
        last_error=cleanup_scheduler._STATE.last_error,
    )
    try:
        cleanup_scheduler._STATE.last_success_at = None
        cleanup_scheduler._STATE.last_error = None
        assert (
            cleanup_scheduler.request_log_cleanup_is_ready(interval_seconds=60, leader_election_enabled=False) is False
        )

        cleanup_scheduler._STATE.last_success_at = cleanup_scheduler.utcnow() - timedelta(seconds=121)
        assert (
            cleanup_scheduler.request_log_cleanup_is_ready(interval_seconds=60, leader_election_enabled=False) is False
        )

        cleanup_scheduler._STATE.last_success_at = cleanup_scheduler.utcnow()
        cleanup_scheduler._STATE.last_error = "DatabaseError"
        assert (
            cleanup_scheduler.request_log_cleanup_is_ready(interval_seconds=60, leader_election_enabled=False) is False
        )

        cleanup_scheduler._STATE.last_error = None
        assert (
            cleanup_scheduler.request_log_cleanup_is_ready(interval_seconds=60, leader_election_enabled=False) is True
        )
    finally:
        cleanup_scheduler._STATE.last_attempt_at = original.last_attempt_at
        cleanup_scheduler._STATE.last_success_at = original.last_success_at
        cleanup_scheduler._STATE.last_deleted_count = original.last_deleted_count
        cleanup_scheduler._STATE.last_error = original.last_error
