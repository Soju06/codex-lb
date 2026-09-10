from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial

from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.telemetry.consent import TelemetryConsentStore
from app.modules.telemetry.sender import TelemetrySender
from app.modules.telemetry.snapshot import TelemetrySnapshotBuilder

logger = logging.getLogger(__name__)

TELEMETRY_INTERVAL_SECONDS = 24 * 60 * 60
TELEMETRY_FIELDS_DOCUMENTATION = "https://soju06.github.io/codex-lb/telemetry/"


@dataclass(slots=True)
class TelemetryScheduler:
    sender: TelemetrySender = field(default_factory=TelemetrySender)
    interval_seconds: float = TELEMETRY_INTERVAL_SECONDS
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="anonymous-telemetry-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        first_tick = True
        while not self._stop.is_set():
            await self._tick(log_undecided_notice=first_tick)
            first_tick = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _tick(self, *, log_undecided_notice: bool = False) -> None:
        await _get_leader_election().run_if_leader(
            partial(self._tick_as_leader, log_undecided_notice=log_undecided_notice)
        )

    async def _tick_as_leader(self, *, log_undecided_notice: bool = False) -> None:
        async with self._lock:
            try:
                today_utc = utcnow().date()
                async with get_background_session() as session:
                    store = TelemetryConsentStore(session)
                    consent = await store.resolve()
                    if log_undecided_notice and consent.state == "undecided" and consent.source == "default":
                        logger.info(
                            "Anonymous telemetry is active; collected fields: %s; disable with "
                            "CODEX_LB_TELEMETRY_ENABLED=false",
                            TELEMETRY_FIELDS_DOCUMENTATION,
                        )
                    if not consent.active:
                        return
                    assert consent.state != "disabled"
                    identity = await store.get_or_create_identity()
                    builder = TelemetrySnapshotBuilder(session)
                    snapshot = await builder.build(identity.instance_id, consent=consent.state)
                    acknowledged = (await store._repository.get_or_create()).telemetry_day_acknowledged_date
                try:
                    await self.sender.send_snapshot(snapshot)
                except Exception as exc:
                    logger.debug("Anonymous telemetry snapshot transmission failed", exc_info=exc)
                async with get_background_session() as day_session:
                    to_send = await TelemetrySnapshotBuilder(day_session).completed_days(
                        identity.instance_id,
                        acknowledged=acknowledged.date() if acknowledged else None,
                        today_utc=today_utc,
                    )
                results: list[bool] = []
                for day in to_send:
                    try:
                        results.append(await self.sender.send_day(day))
                    except Exception as exc:
                        logger.debug("Anonymous telemetry day transmission failed", exc_info=exc)
                        results.append(False)
                previous_date = acknowledged.date() if acknowledged else None
                # The watermark is inclusive; today - 8 is the last date outside
                # the seven completed calendar days, even for empty histories.
                window_floor = today_utc - timedelta(days=8)
                watermark = max(previous_date, window_floor) if previous_date is not None else window_floor
                for day, succeeded in zip(reversed(to_send), reversed(results), strict=True):
                    if not succeeded:
                        break
                    watermark = day.utc_date
                if previous_date is None or watermark > previous_date:
                    async with get_background_session() as ack_session:
                        ack_store = TelemetryConsentStore(ack_session)
                        row = await ack_store._repository.get_or_create()
                        row.telemetry_day_acknowledged_date = datetime.combine(watermark, datetime.min.time())
                        await ack_store._repository.commit_refresh(row)
            except Exception as exc:
                logger.debug("Anonymous telemetry scheduler tick failed", exc_info=exc)


def build_telemetry_scheduler() -> TelemetryScheduler:
    return TelemetryScheduler()
