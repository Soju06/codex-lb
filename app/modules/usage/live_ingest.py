from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.core import usage as usage_core
from app.core.config.settings import get_settings
from app.core.usage.live_hub import register_live_usage_publisher
from app.core.usage.live_snapshots import LiveRateLimitSnapshot, LiveUsageWindow
from app.db.models import Account
from app.db.session import get_background_session
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)

_RESOLUTION_TTL_SECONDS = 300.0

# Write-coalescing tuning (fixed; issue #1340 / PRINCIPLES.md P2). The
# ingestor keeps both as constructor fields so tests can exercise queue
# overflow and coalescing with small values.
_QUEUE_SIZE = 512
_WRITE_MIN_INTERVAL_SECONDS = 5.0
_CACHE_INVALIDATION_MIN_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class _QueuedSnapshot:
    account_id: str | None
    chatgpt_account_id: str | None
    snapshot: LiveRateLimitSnapshot


def _fingerprint(snapshot: LiveRateLimitSnapshot) -> tuple[object, ...]:
    def window_key(window: LiveUsageWindow | None) -> tuple[object, ...] | None:
        if window is None:
            return None
        return (round(window.used_percent, 2), window.window_minutes, window.reset_at)

    return (
        window_key(snapshot.primary),
        window_key(snapshot.secondary),
        snapshot.credits_has,
        snapshot.credits_unlimited,
        snapshot.credits_balance,
    )


class LiveUsageIngestor:
    """Fire-and-forget sink for per-turn rate-limit snapshots.

    Snapshots ride the serving path, so enqueueing must stay O(1) and never
    raise; a single consumer task owns its own background sessions and writes
    usage-history rows with the same shape the background poller produces.
    """

    def __init__(
        self,
        *,
        queue_size: int,
        write_min_interval_seconds: float,
    ) -> None:
        self._queue: asyncio.Queue[_QueuedSnapshot] = asyncio.Queue(maxsize=max(1, queue_size))
        self._write_min_interval_seconds = write_min_interval_seconds
        self._last_write: dict[str, tuple[tuple[object, ...], float]] = {}
        self._resolution_cache: dict[str, tuple[str | None, float]] = {}
        self._consumer: asyncio.Task[None] | None = None
        self._dropped = 0
        self._last_cache_invalidation = 0.0
        self._trailing_invalidation: asyncio.Task[None] | None = None

    def publish(
        self,
        snapshot: LiveRateLimitSnapshot,
        *,
        account_id: str | None = None,
        chatgpt_account_id: str | None = None,
    ) -> None:
        item = _QueuedSnapshot(account_id=account_id, chatgpt_account_id=chatgpt_account_id, snapshot=snapshot)
        if account_id is not None and self._should_skip(account_id, snapshot):
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning("Live usage ingest queue full; dropped_total=%d", self._dropped)
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def start(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._run(), name="live-usage-ingestor")

    async def stop(self) -> None:
        consumer = self._consumer
        self._consumer = None
        trailing = self._trailing_invalidation
        self._trailing_invalidation = None
        for task in (consumer, trailing):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _should_skip(self, account_id: str, snapshot: LiveRateLimitSnapshot) -> bool:
        last = self._last_write.get(account_id)
        if last is None:
            return False
        fingerprint, written_at = last
        if fingerprint != _fingerprint(snapshot):
            return False
        return time.monotonic() - written_at < self._write_min_interval_seconds

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._ingest(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Live usage ingest failed account_id=%s chatgpt_account_id=%s",
                    item.account_id,
                    item.chatgpt_account_id,
                    exc_info=True,
                )

    async def _ingest(self, item: _QueuedSnapshot) -> None:
        account_id = item.account_id
        if account_id is None:
            account_id = await self._resolve_account_id(item.chatgpt_account_id)
        if account_id is None:
            return
        if self._should_skip(account_id, item.snapshot):
            return

        snapshot = item.snapshot
        primary = snapshot.primary
        secondary = snapshot.secondary
        monthly: LiveUsageWindow | None = None
        # Mirror the poller's write-time normalization: a lone primary window
        # with the monthly duration is the monthly-only free-plan shape and
        # belongs in the monthly slot, not the primary one.
        if (
            primary is not None
            and secondary is None
            and primary.window_minutes == usage_core.DEFAULT_WINDOW_MINUTES_MONTHLY
        ):
            monthly, primary = primary, None
        async with get_background_session() as session:
            repo = UsageRepository(session)
            if primary is not None:
                await repo.add_entry(
                    account_id=account_id,
                    used_percent=float(primary.used_percent),
                    input_tokens=None,
                    output_tokens=None,
                    window="primary",
                    reset_at=primary.reset_at,
                    window_minutes=primary.window_minutes,
                    credits_has=snapshot.credits_has,
                    credits_unlimited=snapshot.credits_unlimited,
                    credits_balance=snapshot.credits_balance,
                )
            if secondary is not None:
                # Mirror the poller: credits normally ride the primary row.
                # A secondary-only snapshot (e.g. the short window is not
                # being reported) must still carry the fresh credit state.
                secondary_carries_credits = primary is None
                await repo.add_entry(
                    account_id=account_id,
                    used_percent=float(secondary.used_percent),
                    input_tokens=None,
                    output_tokens=None,
                    window="secondary",
                    reset_at=secondary.reset_at,
                    window_minutes=secondary.window_minutes,
                    credits_has=snapshot.credits_has if secondary_carries_credits else None,
                    credits_unlimited=snapshot.credits_unlimited if secondary_carries_credits else None,
                    credits_balance=snapshot.credits_balance if secondary_carries_credits else None,
                )
            if monthly is not None:
                await repo.add_entry(
                    account_id=account_id,
                    used_percent=float(monthly.used_percent),
                    input_tokens=None,
                    output_tokens=None,
                    window="monthly",
                    reset_at=monthly.reset_at,
                    window_minutes=monthly.window_minutes,
                    credits_has=snapshot.credits_has,
                    credits_unlimited=snapshot.credits_unlimited,
                    credits_balance=snapshot.credits_balance,
                )
        self._last_write[account_id] = (_fingerprint(snapshot), time.monotonic())
        await self._invalidate_caches_throttled()

    async def _invalidate_caches_throttled(self) -> None:
        # Invalidations are throttled, but every write must still be covered:
        # a write inside the throttle window schedules one trailing
        # invalidation at window expiry, so cached selection inputs and
        # downstream x-codex-* headers are stale for at most the throttle
        # interval rather than the header cache TTL.
        now = time.monotonic()
        remaining = _CACHE_INVALIDATION_MIN_INTERVAL_SECONDS - (now - self._last_cache_invalidation)
        if remaining <= 0:
            await self._invalidate_caches_now()
            return
        if self._trailing_invalidation is None or self._trailing_invalidation.done():
            self._trailing_invalidation = asyncio.create_task(self._trailing_invalidate(remaining))

    async def _trailing_invalidate(self, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)
        await self._invalidate_caches_now()

    async def _invalidate_caches_now(self) -> None:
        self._last_cache_invalidation = time.monotonic()
        get_account_selection_cache().invalidate()
        # Downstream x-codex-* headers are served from a TTL cache that only
        # the poller invalidates otherwise; drop it so clients see the live
        # values before the TTL expires.
        await get_rate_limit_headers_cache().invalidate()

    async def _resolve_account_id(self, chatgpt_account_id: str | None) -> str | None:
        if not chatgpt_account_id:
            return None
        cached = self._resolution_cache.get(chatgpt_account_id)
        now = time.monotonic()
        if cached is not None and now - cached[1] < _RESOLUTION_TTL_SECONDS:
            return cached[0]
        async with get_background_session() as session:
            rows = (
                (await session.execute(select(Account.id).where(Account.chatgpt_account_id == chatgpt_account_id)))
                .scalars()
                .all()
            )
        # Ambiguous identities (multiple workspace slots) are dropped rather
        # than guessed; the poller stays authoritative for them.
        resolved = rows[0] if len(rows) == 1 else None
        self._resolution_cache[chatgpt_account_id] = (resolved, now)
        return resolved


_ingestor: LiveUsageIngestor | None = None


def start_live_usage_ingestor() -> LiveUsageIngestor | None:
    global _ingestor
    settings = get_settings()
    if not getattr(settings, "live_usage_ingestion_enabled", True):
        register_live_usage_publisher(None)
        return None
    ingestor = LiveUsageIngestor(
        queue_size=_QUEUE_SIZE,
        write_min_interval_seconds=_WRITE_MIN_INTERVAL_SECONDS,
    )
    ingestor.start()
    register_live_usage_publisher(ingestor.publish)
    _ingestor = ingestor
    return ingestor


async def stop_live_usage_ingestor() -> None:
    global _ingestor
    ingestor = _ingestor
    _ingestor = None
    register_live_usage_publisher(None)
    if ingestor is not None:
        await ingestor.stop()
