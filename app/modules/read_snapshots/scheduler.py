"""One owned, leased background producer; requests never enter its collectors."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from time import monotonic

from app.core.scheduling.leader_election import LeaderElection
from app.core.types import JsonValue
from app.db.session import get_background_session
from app.modules.read_snapshots.collectors import dashboard_snapshot, report_snapshot
from app.modules.read_snapshots.keys import REPORT_PRESETS, local_today, resolve_parameters
from app.modules.read_snapshots.repository import (
    DASHBOARD_INTERVAL_SECONDS,
    REFRESH_TIMEOUT_SECONDS,
    REPORT_INTERVAL_SECONDS,
    SCHEMA_VERSION,
    SnapshotCapacityError,
    SnapshotRepository,
)

logger = logging.getLogger(__name__)
TICK_SECONDS = 5
MAX_JOBS_PER_PASS = 4


class ReadSnapshotScheduler:
    def __init__(self, *, tick_seconds: float = TICK_SECONDS) -> None:
        self.leader = LeaderElection(lease_id=100 + SCHEMA_VERSION)
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._dates: dict[str, str] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="analytical-read-snapshots")

    async def stop(self) -> bool:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        return await self.leader.release()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.leader.run_if_leader(self._serve)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Analytical snapshot refresh pass failed; last-good data retained")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def _serve(self) -> None:
        # Two bounded lanes, never two tasks sharing a session. A slow report
        # cannot postpone the next dashboard generation. The leased parent
        # owns and joins both lanes on shutdown, lease loss or partial failure.
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._lane(("dashboard",)), name="dashboard-snapshots")
            tasks.create_task(self._lane(("report-preset", "report-range")), name="report-snapshots")

    async def _lane(self, kinds: tuple[str, ...]) -> None:
        while not self._stop.is_set():
            await self.refresh_due(kinds)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass

    async def _seed(self) -> list[str]:
        async with get_background_session() as session:
            repository = SnapshotRepository(session)
            await repository.register("dashboard", {}, pinned=True, touch=False)
            zones = await repository.known_timezones()
            for zone in zones:
                today = local_today(zone).isoformat()
                for days in REPORT_PRESETS:
                    try:
                        # Re-register an evicted key too; a process-local seeded
                        # flag must not leave a durable known preset missing.
                        await repository.register(
                            "report-preset",
                            {"days": days, "timezone": zone},
                            pinned=zone == "UTC",
                            touch=False,
                        )
                    except SnapshotCapacityError:
                        logger.warning("Snapshot registry full for timezone=%s days=%s", zone, days)
                if self._dates.get(zone) != today:
                    # Also handles a process restarting across midnight without
                    # waiting for yesterday's scheduled expiry.
                    for days in REPORT_PRESETS:
                        await repository.refresh_changed_report_date(days, zone, today)
                    self._dates[zone] = today
            return zones

    async def refresh_due(self, kinds: tuple[str, ...] = ("dashboard", "report-preset", "report-range")) -> None:
        if kinds == ("dashboard",):
            async with get_background_session() as session:
                await SnapshotRepository(session).register("dashboard", {}, pinned=True, touch=False)
            zones: list[str] = []
        else:
            zones = await self._seed()
        async with get_background_session() as session:
            jobs = await SnapshotRepository(session).due(kinds, limit=64)
        jobs = [job for job in jobs if job[1] != "report-preset" or job[2].get("timezone") in zones]
        # Keep the repository's due-age ordering between noncanonical presets
        # and custom views. Strict kind priority would starve filters forever.
        jobs.sort(key=lambda job: job[1] != "dashboard")
        for key, kind, parameters in jobs[:MAX_JOBS_PER_PASS]:
            if self._stop.is_set():
                return
            await self.refresh_one(key, kind, parameters)

    async def refresh_one(self, key: str, kind: str, parameters: dict[str, JsonValue]) -> None:
        async with get_background_session() as session:
            generation = await SnapshotRepository(session).claim(key, self.leader)
        if generation is None:
            return
        started = monotonic()
        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                async with get_background_session() as session:
                    dialect = session.get_bind().dialect.name
                    if kind != "dashboard" and dialect == "postgresql":
                        # This is a fresh, owned analytical session. Keep all
                        # report statements on one view; the per-connection
                        # override is restored when it returns to the pool.
                        # Inference/accounting sessions retain their defaults.
                        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                    payload = (
                        await dashboard_snapshot(session)
                        if kind == "dashboard"
                        else await report_snapshot(session, parameters)
                    )
            if kind == "report-preset":
                expected = resolve_parameters(parameters)
                expected_range = {name: expected[name] for name in ("start", "end", "timezone")}
                if payload.get("range") != expected_range:
                    raise ValueError("report_date_changed_during_refresh")
            interval = DASHBOARD_INTERVAL_SECONDS if kind == "dashboard" else REPORT_INTERVAL_SECONDS
            async with get_background_session() as session:
                published = await SnapshotRepository(session).publish(
                    key,
                    generation,
                    self.leader,
                    payload,
                    interval_seconds=interval,
                    duration_ms=(monotonic() - started) * 1000,
                )
            logger.info(
                "Analytical snapshot kind=%s published=%s duration_ms=%.1f",
                kind,
                published,
                (monotonic() - started) * 1000,
            )
        except asyncio.CancelledError:
            # The caller owns this finalization; it is not a detached task.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    async with get_background_session() as session:
                        await SnapshotRepository(session).failed(key, generation, self.leader, "cancelled")
            raise
        except Exception as exc:
            async with get_background_session() as session:
                await SnapshotRepository(session).failed(key, generation, self.leader, type(exc).__name__)
            logger.exception("Analytical snapshot kind=%s failed; previous payload preserved", kind)


def build_read_snapshot_scheduler() -> ReadSnapshotScheduler:
    return ReadSnapshotScheduler()
