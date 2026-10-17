"""Own upstream metadata refresh and bounded historical cost repair."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field

from app.core.clients.codex_version import get_codex_version_cache
from app.core.config.settings import get_settings
from app.core.metadata_store import write_metadata
from app.core.scheduling.leader_election_handle import get_leader_election
from app.core.usage.pricing_catalog import (
    BUNDLE_PATH,
    decode_snapshot,
    encode_snapshot,
    fetch_catalogs,
    get_active_prices,
    install_prices,
    merge_catalogs,
    snapshot_updated_at,
)
from app.db.session import get_background_session
from app.modules.request_logs.cost_backfill import backfill_missing_costs

logger = logging.getLogger(__name__)
_REFRESH_SECONDS = 3600
_FAILURE_RETRY_SECONDS = 300


@dataclass
class MetadataRefreshScheduler:
    interval_seconds: float = 5.0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _refresh_at: float = 0.0
    _backfill_at: float = 0.0
    _cursor: int = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        path = get_settings().data_dir / "pricing-cache.json"
        try:
            cached = json.loads(await asyncio.to_thread(path.read_text))
            bundled = json.loads(BUNDLE_PATH.read_text())
            prices = decode_snapshot(cached)
            if snapshot_updated_at(cached) < snapshot_updated_at(bundled):
                prices = merge_catalogs(decode_snapshot(bundled), prices)
            install_prices(prices)
        except (OSError, ValueError):
            get_active_prices()
        await get_codex_version_cache().restore(get_settings().data_dir / "codex-version-cache.json")
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _refresh(self) -> None:
        await get_codex_version_cache().get_version()
        try:
            prices = await fetch_catalogs()
            previous = get_active_prices()
            install_prices(prices)
            if get_active_prices() != previous:
                self._cursor = 0
                self._backfill_at = 0.0
            await asyncio.to_thread(
                write_metadata, get_settings().data_dir / "pricing-cache.json", encode_snapshot(get_active_prices())
            )
            logger.info("Refreshed OpenAI pricing metadata: models=%d", len(prices))
            self._refresh_at = time.monotonic() + _REFRESH_SECONDS
        except (OSError, ValueError):
            self._refresh_at = time.monotonic() + _FAILURE_RETRY_SECONDS
            logger.warning("Metadata refresh failed; retaining last-good prices", exc_info=True)

    async def _backfill(self) -> bool:
        async with get_background_session() as session:
            batch = await backfill_missing_costs(session, after_id=self._cursor)
        self._cursor = batch.last_id
        if batch.scanned < 200:
            self._cursor = 0
            self._backfill_at = time.monotonic() + _REFRESH_SECONDS
        if batch.updated:
            logger.info("Backfilled missing request costs: updated=%d last_id=%d", batch.updated, batch.last_id)
        return True

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() >= self._refresh_at:
                try:
                    await self._refresh()
                except Exception:
                    self._refresh_at = time.monotonic() + _FAILURE_RETRY_SECONDS
                    logger.exception("Upstream metadata refresh failed")
            if time.monotonic() >= self._backfill_at:
                try:
                    await get_leader_election().run_if_leader(self._backfill)
                except Exception:
                    self._backfill_at = time.monotonic() + _FAILURE_RETRY_SECONDS
                    logger.exception("Missing-cost backfill failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass


def build_metadata_refresh_scheduler() -> MetadataRefreshScheduler:
    return MetadataRefreshScheduler()
