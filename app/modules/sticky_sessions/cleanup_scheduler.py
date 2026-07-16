from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol, TypeVar, cast

from app.core import startup as startup_module
from app.core.config.settings import Settings, get_settings
from app.core.utils.time import utcnow
from app.db.models import DashboardSettings
from app.db.session import SessionLocal, get_background_session
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, missing_durable_bridge_tables
from app.modules.proxy.ring_membership import RING_MEMBER_RETENTION_SECONDS, RingMembershipService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.settings.repository import SettingsRepository

logger = logging.getLogger(__name__)

# Cleanup poll cadence (fixed; issue #1340 / PRINCIPLES.md P2). The scheduler
# keeps ``interval_seconds`` as a constructor field so tests can exercise the
# loop with a short interval.
_CLEANUP_INTERVAL_SECONDS = 300


_T = TypeVar("_T")


class _LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


def _abandoned_bridge_retention_seconds(
    dashboard_settings: DashboardSettings,
    app_settings: Settings,
) -> float:
    """Retention for abandoned durable bridge rows.

    An idle local bridge session stays reusable until its effective idle TTL —
    up to the prompt-cache reuse TTL for prompt-cache sessions — which can
    exceed the prompt-cache affinity max age. Purging the ACTIVE durable row
    earlier would strip a still-reusable session of its durable ownership and
    continuity aliases, so retention must cover the longest reuse window.
    """

    return max(
        float(dashboard_settings.openai_cache_affinity_max_age_seconds),
        float(dashboard_settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds),
        float(app_settings.http_responses_session_bridge_idle_ttl_seconds),
        float(app_settings.http_responses_session_bridge_codex_idle_ttl_seconds),
    )


@dataclass(slots=True)
class StickySessionCleanupScheduler:
    interval_seconds: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
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

    async def _cleanup_once(self) -> None:
        await _get_leader_election().run_if_leader(self._cleanup_as_leader)

    async def _cleanup_as_leader(self) -> None:
        async with self._lock:
            try:
                async with get_background_session() as session:
                    settings_repo = SettingsRepository(session)
                    bridge_repo = DurableBridgeRepository(session)
                    sticky_repo = StickySessionsRepository(session)
                    settings = await settings_repo.get_or_create()

                    cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
                    deleted_count = await sticky_repo.purge_prompt_cache_before(cutoff)
                    if deleted_count > 0:
                        logger.info("Purged stale prompt-cache sticky sessions deleted_count=%s", deleted_count)
                    if startup_module._bridge_durable_schema_ready or not await missing_durable_bridge_tables(session):
                        bridge_deleted_count = await bridge_repo.purge_closed_before(cutoff)
                        if bridge_deleted_count > 0:
                            logger.info("Purged closed HTTP bridge sessions deleted_count=%s", bridge_deleted_count)
                        abandoned_cutoff = utcnow() - timedelta(
                            seconds=_abandoned_bridge_retention_seconds(settings, get_settings())
                        )
                        abandoned_deleted_count = await bridge_repo.purge_abandoned_before(abandoned_cutoff)
                        if abandoned_deleted_count > 0:
                            logger.info(
                                "Purged abandoned HTTP bridge sessions deleted_count=%s", abandoned_deleted_count
                            )
                ring_cutoff = utcnow() - timedelta(seconds=RING_MEMBER_RETENTION_SECONDS)
                ring_deleted_count = await RingMembershipService(SessionLocal).purge_stale_before(ring_cutoff)
                if ring_deleted_count > 0:
                    logger.info("Purged stale bridge ring members deleted_count=%s", ring_deleted_count)
            except Exception:
                logger.exception("Sticky session cleanup loop failed")


def build_sticky_session_cleanup_scheduler() -> StickySessionCleanupScheduler:
    settings = get_settings()
    return StickySessionCleanupScheduler(
        interval_seconds=_CLEANUP_INTERVAL_SECONDS,
        enabled=settings.sticky_session_cleanup_enabled,
    )
