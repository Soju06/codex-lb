from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, cast

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.request_logs.repository import RequestLogsRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RequestLogCleanupState:
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_deleted_count: int = 0
    last_error: str | None = None
    last_leader_error: str | None = None
    currently_responsible: bool = False


_STATE = RequestLogCleanupState()


def request_log_cleanup_health() -> str:
    if _STATE.last_leader_error is not None:
        return f"leader_error:{_STATE.last_leader_error}"
    if _STATE.last_error is not None:
        return f"error:{_STATE.last_error}"
    if _STATE.last_success_at is None:
        return "pending"
    return f"ok:{_STATE.last_deleted_count}"


def request_log_cleanup_is_ready(*, interval_seconds: int, leader_election_enabled: bool) -> bool:
    if _STATE.last_leader_error is not None:
        return False
    if leader_election_enabled and not _STATE.currently_responsible:
        return True
    if _STATE.last_error is not None:
        return False
    if _STATE.last_success_at is None:
        return False
    return (utcnow() - _STATE.last_success_at).total_seconds() <= interval_seconds * 2


class _LeaderElectionLike(Protocol):
    last_acquire_error: str | None

    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class RequestLogCleanupScheduler:
    interval_seconds: int
    retention_days: int | None
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self.retention_days is None or (self._task and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._cleanup_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _cleanup_once(self) -> int:
        if self.retention_days is None:
            return 0
        leader_election = _get_leader_election()
        _STATE.currently_responsible = await leader_election.try_acquire()
        _STATE.last_leader_error = getattr(leader_election, "last_acquire_error", None)
        if _STATE.last_leader_error is not None:
            _STATE.last_attempt_at = utcnow()
            return 0
        if not _STATE.currently_responsible:
            return 0
        async with self._lock:
            _STATE.last_attempt_at = utcnow()
            try:
                cutoff = utcnow() - timedelta(days=self.retention_days)
                async with get_background_session() as session:
                    deleted = await RequestLogsRepository(session).purge_before(cutoff)
                if deleted:
                    logger.info("Purged expired request logs deleted_count=%s", deleted)
                _STATE.last_success_at = utcnow()
                _STATE.last_deleted_count = deleted
                _STATE.last_error = None
                _STATE.last_leader_error = None
                return deleted
            except Exception as exc:
                _STATE.last_error = type(exc).__name__
                logger.exception("Request log cleanup loop failed")
                return 0


def build_request_log_cleanup_scheduler() -> RequestLogCleanupScheduler:
    settings = get_settings()
    return RequestLogCleanupScheduler(
        interval_seconds=settings.request_log_cleanup_interval_seconds,
        retention_days=settings.request_log_retention_days,
    )
