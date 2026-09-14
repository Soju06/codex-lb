from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

import app.core.config.spool_retention as spool_retention_module
import app.modules.sticky_sessions.cleanup_scheduler as cleanup_scheduler
from app.core.utils.time import utcnow
from app.db.models import DashboardSettings
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers_module
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import (
    DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    DurableBridgeOperationPurgeBatchResult,
    DurableBridgeRepository,
)

leader_election_module = importlib.import_module("app.core.scheduling.leader_election")

pytestmark = pytest.mark.unit


class _FakeLeader:
    """Leader stub that always runs the guarded body, bypassing the DB lease."""

    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


def _spool_retention_settings_repo(retention_seconds: float = 604800.0) -> AsyncMock:
    """``SettingsRepository`` double whose row carries only the retention window."""
    repo = AsyncMock()
    repo.get_or_create = AsyncMock(return_value=_spool_retention_row(retention_seconds))
    return repo


def _spool_retention_row(retention_seconds: float = 604800.0) -> DashboardSettings:
    """Minimal ``dashboard_settings`` snapshot for the operation retention pass.

    ``_run_operation_retention`` resolves its window from the row the pass
    already loaded (R2 spool retention), so the unit tests hand it one.
    """
    return cast(
        DashboardSettings,
        SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=retention_seconds,
        ),
    )


def _purge_batch(
    selected: int,
    deleted: int | None = None,
) -> DurableBridgeOperationPurgeBatchResult:
    return DurableBridgeOperationPurgeBatchResult(
        selected_operations=selected,
        deleted_operations=selected if deleted is None else deleted,
    )


def test_build_sticky_session_cleanup_scheduler_is_always_enabled(monkeypatch) -> None:
    monkeypatch.setattr(cleanup_scheduler, "_CLEANUP_INTERVAL_SECONDS", 42)

    scheduler = cleanup_scheduler.build_sticky_session_cleanup_scheduler()

    assert scheduler.interval_seconds == 42
    assert scheduler.enabled is True


@pytest.mark.asyncio
async def test_operation_retention_cleanup_drains_small_backlog(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(side_effect=[_purge_batch(3), _purge_batch(1)])
    cutoff = utcnow()
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)
    monkeypatch.setattr(cleanup_scheduler, "time", SimpleNamespace(monotonic=lambda: 10.0))

    result = await cleanup_scheduler._purge_operation_spool_with_budget(
        bridge_repo,
        cutoff=cutoff,
    )

    assert result == cleanup_scheduler.OperationRetentionCleanupResult(
        deleted_operations=4,
        batches=2,
        backlog_likely=False,
        outcome="completed",
        duration_seconds=0.0,
    )
    assert bridge_repo.purge_operation_spool_batch.await_count == 2
    bridge_repo.purge_operation_spool_batch.assert_awaited_with(
        cutoff=cutoff,
        batch_size=3,
    )


@pytest.mark.asyncio
async def test_operation_retention_cleanup_stops_at_batch_budget(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(
        side_effect=[_purge_batch(3), _purge_batch(3), AssertionError("extra batch")]
    )
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_MAX_BATCHES", 2)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_TIME_BUDGET_SECONDS", 60.0)
    monkeypatch.setattr(cleanup_scheduler, "time", SimpleNamespace(monotonic=lambda: 10.0))

    result = await cleanup_scheduler._purge_operation_spool_with_budget(
        bridge_repo,
        cutoff=utcnow(),
    )

    assert result.deleted_operations == 6
    assert result.batches == 2
    assert result.backlog_likely is True
    assert result.outcome == "batch_budget_exhausted"
    assert bridge_repo.purge_operation_spool_batch.await_count == 2


@pytest.mark.asyncio
async def test_operation_retention_cleanup_stops_at_time_budget(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(side_effect=[_purge_batch(3), AssertionError("extra batch")])
    monotonic_values = iter((10.0, 16.0, 16.0))
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_MAX_BATCHES", 10)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_TIME_BUDGET_SECONDS", 5.0)
    monkeypatch.setattr(
        cleanup_scheduler,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_values)),
    )

    result = await cleanup_scheduler._purge_operation_spool_with_budget(
        bridge_repo,
        cutoff=utcnow(),
    )

    assert result.deleted_operations == 3
    assert result.batches == 1
    assert result.duration_seconds == 6.0
    assert result.backlog_likely is True
    assert result.outcome == "time_budget_exhausted"
    assert bridge_repo.purge_operation_spool_batch.await_count == 1


@pytest.mark.asyncio
async def test_operation_retention_full_selection_with_short_delete_keeps_backlog(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(side_effect=[_purge_batch(3, 1)])
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_MAX_BATCHES", 1)
    monkeypatch.setattr(cleanup_scheduler, "time", SimpleNamespace(monotonic=lambda: 10.0))

    result = await cleanup_scheduler._purge_operation_spool_with_budget(
        bridge_repo,
        cutoff=utcnow(),
    )

    assert result.deleted_operations == 1
    assert result.batches == 1
    assert result.backlog_likely is True
    assert result.outcome == "batch_budget_exhausted"


@pytest.mark.asyncio
async def test_operation_retention_cleanup_failure_preserves_committed_progress(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(
        side_effect=[_purge_batch(3), RuntimeError("db unavailable operation_id=secret")]
    )
    monotonic_values = iter((10.0, 10.0, 12.0))
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_MAX_BATCHES", 3)
    monkeypatch.setattr(cleanup_scheduler, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values)))

    with pytest.raises(cleanup_scheduler.OperationRetentionCleanupError) as captured:
        await cleanup_scheduler._purge_operation_spool_with_budget(
            bridge_repo,
            cutoff=utcnow(),
        )

    assert captured.value.result.deleted_operations == 3
    assert captured.value.result.batches == 1
    assert captured.value.result.backlog_likely is True
    assert captured.value.result.outcome == "failed"
    assert captured.value.result.duration_seconds == 2.0
    assert captured.value.error_type == "RuntimeError"
    assert captured.value.__cause__ is None


def test_cleanup_delay_retries_immediately_while_backlog_is_likely() -> None:
    assert cleanup_scheduler._next_cleanup_delay_seconds(300, backlog_likely=True, retry_immediately=True) == 0.0
    assert cleanup_scheduler._next_cleanup_delay_seconds(3, backlog_likely=True, retry_immediately=True) == 0.0
    assert cleanup_scheduler._next_cleanup_delay_seconds(300, backlog_likely=True, retry_immediately=False) == 5.0
    assert cleanup_scheduler._next_cleanup_delay_seconds(3, backlog_likely=True, retry_immediately=False) == 3.0
    assert cleanup_scheduler._next_cleanup_delay_seconds(300, backlog_likely=False, retry_immediately=False) == 300.0


def test_startup_and_scheduler_share_the_bounded_spool_purge_size() -> None:
    assert cleanup_scheduler._OPERATION_RETENTION_BATCH_SIZE == DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
    assert (
        inspect.signature(DurableBridgeRepository.purge_operation_spool_batch).parameters["batch_size"].default
        == DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
    )
    assert (
        inspect.signature(DurableBridgeRepository.purge_operation_spool).parameters["batch_size"].default
        == DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
    )
    assert (
        inspect.signature(DurableBridgeSessionCoordinator.purge_operation_spool_batch).parameters["batch_size"].default
        == DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
    )
    assert (
        inspect.signature(DurableBridgeSessionCoordinator.purge_operation_spool).parameters["batch_size"].default
        == DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
    )


def test_leader_skip_preserves_existing_backlog_signal() -> None:
    assert cleanup_scheduler._merge_backlog_signal(True, None) is True
    assert cleanup_scheduler._merge_backlog_signal(True, False) is False
    assert cleanup_scheduler._merge_backlog_signal(False, True) is True


@pytest.mark.asyncio
async def test_full_cleanup_leader_skip_preserves_existing_backlog_signal(monkeypatch) -> None:
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=True)
    leader = SimpleNamespace(run_if_leader=AsyncMock(return_value=None))
    monkeypatch.setattr(cleanup_scheduler, "_get_leader_election", lambda: leader)

    attempted = await scheduler._cleanup_once()

    assert attempted is None
    assert cleanup_scheduler._merge_backlog_signal(True, attempted) is True


def test_operation_retention_cleanup_records_low_cardinality_metrics(monkeypatch) -> None:
    runs = MagicMock()
    deleted = MagicMock()
    duration = MagicMock()
    backlog = MagicMock()
    monkeypatch.setattr(cleanup_scheduler, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(cleanup_scheduler, "http_bridge_spool_cleanup_runs_total", runs)
    monkeypatch.setattr(cleanup_scheduler, "http_bridge_spool_cleanup_deleted_operations_total", deleted)
    monkeypatch.setattr(cleanup_scheduler, "http_bridge_spool_cleanup_duration_seconds", duration)
    monkeypatch.setattr(cleanup_scheduler, "http_bridge_spool_cleanup_backlog_likely", backlog)

    cleanup_scheduler._record_operation_retention_cleanup(
        cleanup_scheduler.OperationRetentionCleanupResult(
            deleted_operations=50,
            batches=1,
            backlog_likely=True,
            outcome="time_budget_exhausted",
            duration_seconds=5.25,
        )
    )

    runs.labels.assert_called_once_with(outcome="time_budget_exhausted")
    runs.labels.return_value.inc.assert_called_once_with()
    deleted.inc.assert_called_once_with(50)
    duration.observe.assert_called_once_with(5.25)
    backlog.set.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_operation_retention_cleanup_failure_is_observable(monkeypatch) -> None:
    recorded = MagicMock()
    bridge_repo = AsyncMock()

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=_spool_retention_settings_repo()),
        patch.object(cleanup_scheduler, "StickySessionsRepository"),
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
        patch.object(
            cleanup_scheduler,
            "_purge_operation_spool_with_budget",
            AsyncMock(
                side_effect=cleanup_scheduler.OperationRetentionCleanupError(
                    cleanup_scheduler.OperationRetentionCleanupResult(
                        deleted_operations=50,
                        batches=1,
                        backlog_likely=True,
                        outcome="failed",
                        duration_seconds=1.5,
                    ),
                    error_type="RuntimeError",
                )
            ),
        ),
        patch.object(cleanup_scheduler, "_record_operation_retention_cleanup", recorded),
    ):
        await scheduler._cleanup_once()

    recorded.assert_called_once()
    result = recorded.call_args.args[0]
    assert result.deleted_operations == 50
    assert result.batches == 1
    assert result.backlog_likely is True
    assert result.outcome == "failed"


@pytest.mark.asyncio
async def test_operation_retention_cutoff_follows_the_dashboard_window(monkeypatch) -> None:
    """R2 spool retention: the dashboard column wins over the deprecated env alias."""
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    monkeypatch.setattr(
        spool_retention_module,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    await scheduler._run_operation_retention(bridge_repo, _spool_retention_row(7200.0))

    cutoff = bridge_repo.purge_operation_spool_batch.call_args.kwargs["cutoff"]
    assert abs((utcnow() - timedelta(seconds=7200.0) - cutoff).total_seconds()) < 5.0


@pytest.mark.asyncio
async def test_operation_retention_failure_log_omits_exception_detail(monkeypatch, caplog) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(
        side_effect=RuntimeError("operation_id=secret SQL DELETE FROM transcript")
    )
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with caplog.at_level("INFO", logger=cleanup_scheduler.__name__):
        backlog_likely = await scheduler._run_operation_retention(bridge_repo, _spool_retention_row())

    assert backlog_likely is True
    assert scheduler._operation_retention_attempt_failed is True
    assert cleanup_scheduler._next_cleanup_delay_seconds(300, backlog_likely=True, retry_immediately=False) == 5.0
    assert "deleted_operations=0 batches=0" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "operation_id=secret" not in caplog.text
    assert "DELETE FROM" not in caplog.text


@pytest.mark.asyncio
async def test_operation_retention_noop_logs_aggregate_without_prometheus(monkeypatch, caplog) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    monkeypatch.setattr(cleanup_scheduler, "PROMETHEUS_AVAILABLE", False)
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with caplog.at_level("INFO", logger=cleanup_scheduler.__name__):
        backlog_likely = await scheduler._run_operation_retention(bridge_repo, _spool_retention_row())

    assert backlog_likely is False
    assert "deleted_operations=0 batches=1 outcome=completed" in caplog.text
    assert "backlog_likely=False" in caplog.text
    assert "error_type=none" in caplog.text


@pytest.mark.asyncio
async def test_operation_retention_noop_avoids_duplicate_log_with_prometheus(monkeypatch, caplog) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            metrics_enabled=True,
        ),
    )
    monkeypatch.setattr(cleanup_scheduler, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(cleanup_scheduler, "_record_operation_retention_cleanup", Mock())
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with caplog.at_level("INFO", logger=cleanup_scheduler.__name__):
        backlog_likely = await scheduler._run_operation_retention(bridge_repo, _spool_retention_row())

    assert backlog_likely is False
    assert "HTTP bridge operation transcript retention" not in caplog.text


@pytest.mark.asyncio
async def test_operation_retention_noop_logs_aggregate_when_metrics_disabled(monkeypatch, caplog) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            metrics_enabled=False,
        ),
    )
    monkeypatch.setattr(cleanup_scheduler, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(cleanup_scheduler, "_record_operation_retention_cleanup", Mock())
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with caplog.at_level("INFO", logger=cleanup_scheduler.__name__):
        backlog_likely = await scheduler._run_operation_retention(bridge_repo, _spool_retention_row())

    assert backlog_likely is False
    assert "deleted_operations=0 batches=1 outcome=completed" in caplog.text


@pytest.mark.asyncio
async def test_full_cleanup_cancellation_records_partial_result_and_preserves_backlog(monkeypatch) -> None:
    partial_result = cleanup_scheduler.OperationRetentionCleanupResult(
        deleted_operations=3,
        batches=1,
        backlog_likely=True,
        outcome="failed",
        duration_seconds=1.0,
    )
    recorded = Mock()
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    class LeaseLossLeader:
        cancellation_seen = False

        async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object | None:
            try:
                return await fn()
            except asyncio.CancelledError:
                self.cancellation_seen = True
                return None

    leader = LeaseLossLeader()

    async def cancelled_retention(self) -> bool | None:
        return await self._run_operation_retention(AsyncMock(), _spool_retention_row())

    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            metrics_enabled=True,
        ),
    )
    monkeypatch.setattr(
        cleanup_scheduler,
        "_purge_operation_spool_with_budget",
        AsyncMock(side_effect=cleanup_scheduler.OperationRetentionCleanupCancelledError(partial_result)),
    )
    monkeypatch.setattr(cleanup_scheduler, "_record_operation_retention_cleanup", recorded)
    monkeypatch.setattr(cleanup_scheduler, "_get_leader_election", lambda: leader)
    monkeypatch.setattr(
        cleanup_scheduler.StickySessionCleanupScheduler,
        "_cleanup_as_leader",
        cancelled_retention,
    )

    backlog_likely = await scheduler._cleanup_once()

    assert leader.cancellation_seen is True
    assert backlog_likely is True
    assert scheduler._operation_retention_attempt_failed is True
    recorded.assert_called_once_with(partial_result)


@pytest.mark.asyncio
async def test_full_cleanup_lease_loss_during_session_teardown_preserves_confirmed_backlog(monkeypatch) -> None:
    teardown_started = asyncio.Event()
    never = asyncio.Event()
    leader = leader_election_module.LeaderElection(leader_id="node-a")
    lease_session = MagicMock()
    lease_session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    lease_session.execute = AsyncMock(
        side_effect=(
            SimpleNamespace(first=lambda: (5.0,)),
            SimpleNamespace(first=lambda: None),
        )
    )
    lease_session.commit = AsyncMock()

    @asynccontextmanager
    async def leader_session():
        yield lease_session

    class SessionThatBlocksOnExit:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            teardown_started.set()
            await never.wait()

    completed_full_batch = cleanup_scheduler.OperationRetentionCleanupResult(
        deleted_operations=50,
        batches=1,
        backlog_likely=True,
        outcome="batch_budget_exhausted",
        duration_seconds=0.01,
    )
    monkeypatch.setattr(
        leader_election_module,
        "get_settings",
        lambda: SimpleNamespace(leader_election_enabled=True, leader_election_ttl_seconds=5),
    )
    monkeypatch.setattr(leader_election_module, "get_background_session", leader_session)
    monkeypatch.setattr(cleanup_scheduler, "_get_leader_election", lambda: leader)
    monkeypatch.setattr(cleanup_scheduler, "get_background_session", SessionThatBlocksOnExit)
    monkeypatch.setattr(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True)
    # R2 spool retention: the retention pass resolves its window from the
    # dashboard row this pass loads.
    monkeypatch.setattr(cleanup_scheduler, "SettingsRepository", lambda _session: _spool_retention_settings_repo())
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            metrics_enabled=False,
        ),
    )
    monkeypatch.setattr(
        cleanup_scheduler,
        "_purge_operation_spool_with_budget",
        AsyncMock(return_value=completed_full_batch),
    )
    monkeypatch.setattr(cleanup_scheduler, "_record_operation_retention_cleanup", Mock())
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=300, enabled=False)

    attempted = await asyncio.wait_for(scheduler._cleanup_once(), timeout=3.0)
    backlog_likely = cleanup_scheduler._merge_backlog_signal(False, attempted)

    assert teardown_started.is_set()
    assert lease_session.execute.await_count == 2
    assert attempted is True
    assert backlog_likely is True
    assert (
        cleanup_scheduler._next_cleanup_delay_seconds(
            300.0,
            backlog_likely=backlog_likely,
            retry_immediately=attempted is True and not scheduler._operation_retention_attempt_failed,
        )
        == 0.0
    )


@pytest.mark.asyncio
async def test_operation_retention_cleanup_cancellation_keeps_partial_progress(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(side_effect=[_purge_batch(3), asyncio.CancelledError()])
    monkeypatch.setattr(cleanup_scheduler, "_OPERATION_RETENTION_BATCH_SIZE", 3)

    with pytest.raises(cleanup_scheduler.OperationRetentionCleanupCancelledError) as captured:
        await cleanup_scheduler._purge_operation_spool_with_budget(bridge_repo, cutoff=utcnow())

    assert captured.value.result.deleted_operations == 3
    assert captured.value.result.batches == 1
    assert captured.value.result.backlog_likely is True
    assert captured.value.result.outcome == "failed"


@pytest.mark.asyncio
async def test_operation_retention_partial_failure_keeps_backlog_retry(monkeypatch) -> None:
    partial_failure = cleanup_scheduler.OperationRetentionCleanupError(
        cleanup_scheduler.OperationRetentionCleanupResult(
            deleted_operations=3,
            batches=1,
            backlog_likely=True,
            outcome="failed",
            duration_seconds=1.0,
        ),
        error_type="RuntimeError",
    )
    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    monkeypatch.setattr(
        cleanup_scheduler,
        "_purge_operation_spool_with_budget",
        AsyncMock(side_effect=partial_failure),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    backlog_likely = await scheduler._run_operation_retention(AsyncMock(), _spool_retention_row())

    assert backlog_likely is True
    assert scheduler._operation_retention_attempt_failed is True


@pytest.mark.asyncio
async def test_operation_retention_catchup_does_not_run_other_maintenance(monkeypatch) -> None:
    bridge_repo = AsyncMock()
    run_retention = AsyncMock(return_value=False)
    dashboard_settings = _spool_retention_row()
    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(return_value=dashboard_settings)

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=True)
    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
        patch.object(cleanup_scheduler, "StickySessionsRepository") as sticky_repository,
        patch.object(cleanup_scheduler, "RingMembershipService") as ring_membership_service,
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
        patch.object(
            cleanup_scheduler.StickySessionCleanupScheduler,
            "_run_operation_retention",
            run_retention,
        ),
    ):
        backlog_likely = await scheduler._cleanup_operation_retention_as_leader()

    assert backlog_likely is False
    # R2 spool retention: the catch-up pass reads the dashboard row for its
    # retention window, and nothing else -- no sticky, bridge-session or ring
    # maintenance is accelerated.
    run_retention.assert_awaited_once_with(bridge_repo, dashboard_settings)
    sticky_repository.assert_not_called()
    ring_membership_service.assert_not_called()


@pytest.mark.asyncio
async def test_prebatch_retention_failure_records_aggregate_metrics(monkeypatch, caplog) -> None:
    recorded = MagicMock()

    class FailingSession:
        async def __aenter__(self):
            raise RuntimeError("operation_id=secret")

        async def __aexit__(self, *args):
            pass

    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)
    with (
        patch.object(cleanup_scheduler, "get_background_session", FailingSession),
        patch.object(cleanup_scheduler, "_record_operation_retention_cleanup", recorded),
        caplog.at_level("WARNING", logger=cleanup_scheduler.__name__),
    ):
        backlog_likely = await scheduler._cleanup_operation_retention_as_leader()

    assert backlog_likely is True
    assert scheduler._operation_retention_attempt_failed is True
    result = recorded.call_args.args[0]
    assert result.outcome == "failed"
    assert result.deleted_operations == 0
    assert result.batches == 0
    assert result.backlog_likely is True
    assert "error_type=RuntimeError" in caplog.text
    assert "operation_id=secret" not in caplog.text


@pytest.mark.asyncio
async def test_unrelated_cleanup_failure_preserves_existing_backlog_retry(monkeypatch) -> None:
    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    settings_repo = AsyncMock()
    settings_repo.get_or_create = AsyncMock(side_effect=RuntimeError("settings unavailable"))
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=True)
    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
    ):
        backlog_likely = await scheduler._cleanup_as_leader()

    assert backlog_likely is None
    assert cleanup_scheduler._merge_backlog_signal(True, backlog_likely) is True


@pytest.mark.asyncio
async def test_cleanup_once_purges_prompt_cache_only(monkeypatch) -> None:
    """_cleanup_once should purge prompt-cache entries by affinity TTL.
    STICKY_THREAD is never purged here, by any predicate: see
    TestNoStickyThreadKeyPrefixSweep. CODEX_SESSION is only ever purged
    via the separate, account-status-gated purge_stale_hard_codex_session_mappings
    call (see test_sticky_repository.py), never by this TTL-based path."""
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
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=5)
    sticky_repo.purge_before_for_key_prefix = AsyncMock(return_value=0)
    sticky_repo.purge_before = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.retire_stale_unavailable_bridge_owners = AsyncMock(return_value=0)
    bridge_repo.purge_closed_before = AsyncMock(return_value=2)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=1)
    bridge_repo.purge_retry_circuits_before = AsyncMock(return_value=3)
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
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
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    sticky_repo.purge_before.assert_not_called()
    bridge_repo.purge_closed_before.assert_called_once()
    bridge_repo.purge_abandoned_before.assert_called_once()
    bridge_repo.purge_retry_circuits_before.assert_called_once()
    bridge_repo.purge_operation_spool_batch.assert_called_once()
    ring_service.purge_stale_before.assert_called_once()
    sticky_repo.purge_stale_hard_codex_session_mappings.assert_called_once()
    passed_cutoff = sticky_repo.purge_stale_hard_codex_session_mappings.call_args.args[0]
    expected_cutoff = utcnow() - timedelta(seconds=cleanup_scheduler._STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS)
    assert abs((passed_cutoff - expected_cutoff).total_seconds()) < 5
    # The durable-bridge counterpart runs on the same sweep and the same grace
    # window; a bridged thread must not wait longer than a sticky one.
    bridge_repo.retire_stale_unavailable_bridge_owners.assert_called_once()
    retire_cutoff = bridge_repo.retire_stale_unavailable_bridge_owners.call_args.args[0]
    retire_now = bridge_repo.retire_stale_unavailable_bridge_owners.call_args.kwargs["now"]
    assert abs((retire_cutoff - expected_cutoff).total_seconds()) < 5
    assert (
        abs(
            (retire_now - retire_cutoff).total_seconds()
            - cleanup_scheduler._STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS
        )
        < 5
    )


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
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    sticky_repo.purge_before_for_key_prefix = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.retire_stale_unavailable_bridge_owners = AsyncMock(return_value=0)
    bridge_repo.purge_closed_before = AsyncMock(return_value=0)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    bridge_repo.purge_retry_circuits_before = AsyncMock(return_value=0)
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
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
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
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
    bridge_repo.purge_retry_circuits_before.assert_not_called()
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
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    sticky_repo.purge_before_for_key_prefix = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.retire_stale_unavailable_bridge_owners = AsyncMock(return_value=0)
    bridge_repo.purge_closed_before = AsyncMock(return_value=1)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    bridge_repo.purge_retry_circuits_before = AsyncMock(return_value=0)
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
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
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", False),
        patch.object(cleanup_scheduler, "missing_durable_bridge_tables", AsyncMock(return_value=())),
    ):
        await scheduler._cleanup_once()

    sticky_repo.purge_prompt_cache_before.assert_called_once()
    bridge_repo.purge_closed_before.assert_called_once()
    bridge_repo.purge_abandoned_before.assert_called_once()
    bridge_repo.purge_retry_circuits_before.assert_called_once()
    bridge_repo.purge_operation_spool_batch.assert_called_once()
    ring_service.purge_stale_before.assert_called_once()


def test_abandoned_bridge_retention_covers_prompt_cache_reuse_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Abandoned-row retention must be at least the longest bridge reuse TTL."""
    dashboard_settings = SimpleNamespace(
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
    )

    retention = cleanup_scheduler._abandoned_bridge_retention_seconds(cast(DashboardSettings, dashboard_settings))

    assert retention == 3600.0

    monkeypatch.setattr(http_bridge_helpers_module, "HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS", 7200.0)
    retention = cleanup_scheduler._abandoned_bridge_retention_seconds(cast(DashboardSettings, dashboard_settings))
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
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
        ),
    )

    sticky_repo = AsyncMock()
    sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    sticky_repo.purge_before_for_key_prefix = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.retire_stale_unavailable_bridge_owners = AsyncMock(return_value=0)
    bridge_repo.purge_closed_before = AsyncMock(return_value=0)
    bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
    bridge_repo.purge_retry_circuits_before = AsyncMock(return_value=0)
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
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
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    closed_cutoff = bridge_repo.purge_closed_before.call_args.args[0]
    abandoned_cutoff = bridge_repo.purge_abandoned_before.call_args.args[0]
    # Closed rows use the 1800s affinity cutoff; abandoned ACTIVE/DRAINING rows
    # must be retained for the full 3600s prompt-cache reuse window.
    gap_seconds = (closed_cutoff - abandoned_cutoff).total_seconds()
    assert abs(gap_seconds - 1800.0) < 5.0


@pytest.mark.asyncio
async def test_cleanup_once_retains_operation_purge_when_sticky_cleanup_disabled(monkeypatch) -> None:
    settings_repo = _spool_retention_settings_repo()
    sticky_repo = AsyncMock()
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
        patch.object(cleanup_scheduler, "StickySessionsRepository", return_value=sticky_repo),
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    # R2 spool retention: the dashboard row is read for the retention window
    # even with sticky-mapping cleanup disabled; no sticky or bridge-session
    # maintenance runs.
    settings_repo.get_or_create.assert_awaited_once()
    sticky_repo.purge_prompt_cache_before.assert_not_awaited()
    bridge_repo.purge_closed_before.assert_not_awaited()
    bridge_repo.purge_operation_spool_batch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky_cleanup_enabled", [True, False])
async def test_cleanup_once_sweeps_expired_rate_limit_attempts(monkeypatch, sticky_cleanup_enabled: bool) -> None:
    """``rate_limit_attempts`` has no other way out: ``clear_for_key`` only runs on success.

    The key space includes a caller-supplied username, so the sweep is what
    bounds the table — and like operation retention it must not depend on the
    sticky-mapping toggle.
    """

    settings_repo = _spool_retention_settings_repo()
    sticky_repo = AsyncMock()
    sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=0)
    sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    sweeper = AsyncMock()

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            openai_cache_affinity_max_age_seconds=600,
        ),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=sticky_cleanup_enabled)

    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
        patch.object(cleanup_scheduler, "StickySessionsRepository", return_value=sticky_repo),
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "RingMembershipService", return_value=AsyncMock()),
        patch.object(cleanup_scheduler, "get_rate_limit_attempt_sweeper", lambda: sweeper),
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    sweeper.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_rate_limit_sweep_does_not_cost_the_rest_of_the_pass(monkeypatch) -> None:
    settings_repo = _spool_retention_settings_repo()
    bridge_repo = AsyncMock()
    bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
    sweeper = AsyncMock()
    sweeper.cleanup = AsyncMock(side_effect=RuntimeError("table is gone"))

    class FakeSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        cleanup_scheduler,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_spool_retention_seconds=604800.0),
    )
    scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=False)

    with (
        patch.object(cleanup_scheduler, "get_background_session", FakeSession),
        patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
        patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
        patch.object(cleanup_scheduler, "get_rate_limit_attempt_sweeper", lambda: sweeper),
        patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
        patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
    ):
        await scheduler._cleanup_once()

    bridge_repo.purge_operation_spool_batch.assert_awaited_once()


class TestNoStickyThreadKeyPrefixSweep:
    """A proxy-derived prompt-cache key is never a `sticky_thread` row.

    The derivation only runs with `openai_cache_affinity` enabled, and that is
    the branch that classifies the mapping as `prompt_cache`; the
    `sticky_thread` branch is reachable only with cache affinity off, where the
    derivation supplies no sticky key. So a key-prefix sweep of `sticky_thread`
    can never match a derived key of either shape, while it *can* match a
    client-supplied key and delete it from the kind that has no TTL by design.
    """

    @pytest.mark.asyncio
    async def test_cleanup_pass_never_purges_sticky_thread_by_key_prefix(self, monkeypatch) -> None:
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
                http_responses_session_bridge_operation_spool_retention_seconds=604800.0,
            ),
        )

        sticky_repo = AsyncMock()
        sticky_repo.purge_stale_hard_codex_session_mappings = AsyncMock(return_value=0)
        sticky_repo.purge_prompt_cache_before = AsyncMock(return_value=5)
        sticky_repo.purge_before_for_key_prefix = AsyncMock(return_value=7)
        sticky_repo.purge_before = AsyncMock(return_value=0)
        bridge_repo = AsyncMock()
        bridge_repo.purge_closed_before = AsyncMock(return_value=0)
        bridge_repo.purge_abandoned_before = AsyncMock(return_value=0)
        bridge_repo.purge_retry_circuits_before = AsyncMock(return_value=0)
        bridge_repo.purge_operation_spool_batch = AsyncMock(return_value=_purge_batch(0))
        ring_service = AsyncMock()
        ring_service.purge_stale_before = AsyncMock(return_value=0)

        class FakeSession:
            async def __aenter__(self):
                return AsyncMock()

            async def __aexit__(self, *args):
                pass

        scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=60, enabled=True)
        with (
            patch.object(cleanup_scheduler, "get_background_session", FakeSession),
            patch.object(cleanup_scheduler, "SettingsRepository", return_value=settings_repo),
            patch.object(cleanup_scheduler, "StickySessionsRepository", return_value=sticky_repo),
            patch.object(cleanup_scheduler, "DurableBridgeRepository", return_value=bridge_repo),
            patch.object(cleanup_scheduler, "RingMembershipService", return_value=ring_service),
            patch.object(cleanup_scheduler, "_get_leader_election", lambda: _FakeLeader()),
            patch.object(cleanup_scheduler.startup_module, "_bridge_durable_schema_ready", True),
        ):
            await scheduler._cleanup_once()

        sticky_repo.purge_prompt_cache_before.assert_awaited_once()
        sticky_repo.purge_before_for_key_prefix.assert_not_awaited()

    def test_scheduler_exposes_no_sticky_thread_prefix_sweep(self) -> None:
        scheduler = cleanup_scheduler.StickySessionCleanupScheduler(interval_seconds=300, enabled=True)
        assert not hasattr(scheduler, "_sweep_legacy_derived_sticky_threads")
        assert not hasattr(scheduler, "_purge_expired_anchored_sticky_threads")
