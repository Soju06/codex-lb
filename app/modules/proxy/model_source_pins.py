"""Durable, thread-keyed pins to a subscription-overflow model source (#2123 WP-C1).

Interfaces contract only (design v3 §3, §8.8, §8.9). Pins make "delivered ⇒
pinned" (I11) hold across replicas: a conversation that received content from
the overflow source keeps resolving to it for ``PIN_IDLE_TTL`` after its last
turn, then stays answerable as a tombstone for ``PIN_TOMBSTONE_GRACE`` so a late
follow-up gets a deterministic decline instead of a silent provider switch.
Every write is capped by the drain deadline so the table is empty when the
dashboard says the drain is over.

This module is the only request-path reader of the pin table and of the TTL
constants in ``app.modules.settings.subscription_overflow``; the inertness
ratchet (``tests/unit/test_subscription_overflow_inert.py``) pins that. Nothing
under ``app/modules/proxy`` imports it yet: the pin primitive is armed by WP-C2.
Bodies are filled in by the pins-retention package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.modules.settings.subscription_overflow import PIN_IDLE_TTL, PIN_KIND_THREAD

__all__ = [
    "PIN_CACHE_MAX_ENTRIES",
    "PIN_CACHE_TTL_SECONDS",
    "PIN_KIND_ANCHOR",
    "PIN_KIND_BOUNCE",
    "PIN_KIND_THREAD",
    "PIN_LOOKUP_DEADLINE_SECONDS",
    "PIN_TOUCH_INTERVAL_SECONDS",
    "PIN_WRITE_ACQUIRE_DEADLINE_SECONDS",
    "WS_BOUNCE_TTL_SECONDS",
    "ModelSourcePinRepository",
    "PinCache",
    "PinIntent",
    "PinLookupResult",
    "PinLookupState",
    "PinLookupTimeout",
    "PinRecord",
    "PinWrite",
    "PinWriteExecutor",
    "PinWriteOutcome",
    "anchor_pin_key",
    "bounce_pin_key",
    "drain_capped_expiry",
    "lookup_pin_bounded",
    "thread_pin_key",
]

PIN_KIND_ANCHOR = "anchor"
PIN_KIND_BOUNCE = "bounce"

# Bounded exposure (design §8.2): a pin lookup is one primary-key read; a pin
# write bounds *acquisition* of the writer section only -- an issued statement
# always runs to completion so the outcome is verifiable.
PIN_LOOKUP_DEADLINE_SECONDS = 2.0
PIN_WRITE_ACQUIRE_DEADLINE_SECONDS = 10.0
# A live pin's ``last_seen_at`` slides at most once an hour per replica.
PIN_TOUCH_INTERVAL_SECONDS = 3600.0
# Positive-only replica cache (no negative caching: an absent pin is always re-read).
PIN_CACHE_TTL_SECONDS = 60.0
PIN_CACHE_MAX_ENTRIES = 10_000
# WebSocket bounce rows expire and purge together after this long.
WS_BOUNCE_TTL_SECONDS = 60.0

_KEY_SEPARATOR = "\n"


def thread_pin_key(thread_selection_key: str) -> str:
    """``"thread\\n" + key`` for the ``thread_only`` form; a ``process-thread`` key raises ``ValueError``."""

    raise NotImplementedError


def anchor_pin_key(api_key_id: str | None, response_id: str) -> str:
    """``"anchor\\n{api_key_id or '-'}\\n{response_id}"``."""

    raise NotImplementedError


def bounce_pin_key(thread_selection_key: str) -> str:
    """``"bounce\\n" + key``; same key-form rule as ``thread_pin_key``."""

    raise NotImplementedError


def drain_capped_expiry(
    now: datetime,
    drain_until: datetime | None,
    *,
    ttl: timedelta = PIN_IDLE_TTL,
) -> tuple[datetime, datetime]:
    """``(expires_at, purge_at)`` for a write at ``now``.

    ``expires_at = min(now + ttl, drain_until - PIN_TOMBSTONE_GRACE - 1 d)`` and
    ``purge_at = expires_at + PIN_TOMBSTONE_GRACE`` so ``purge_at < drain_until``
    for every row written while draining. Both inputs and outputs are
    timezone-aware UTC (``drain_until`` from settings is naive UTC and must be
    normalised by the caller-facing helpers).
    """

    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PinRecord:
    pin_key: str
    kind: str
    source_id: str
    api_key_id: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    purge_at: datetime


@dataclass(frozen=True, slots=True)
class PinWrite:
    pin_key: str
    kind: str
    source_id: str
    api_key_id: str | None


PinLookupState = Literal["live", "expired", "bounce", "none"]


@dataclass(frozen=True, slots=True)
class PinLookupResult:
    """``live``: ``expires_at > now``; ``expired``: tombstone; ``bounce``: bounce kind; ``none``: absent or purged."""

    state: PinLookupState
    record: PinRecord | None


class PinLookupTimeout(Exception):
    """A bounded pin lookup exceeded ``PIN_LOOKUP_DEADLINE_SECONDS``."""


class ModelSourcePinRepository:
    """Dialect-neutral CRUD over ``model_source_pins``; never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, writes: Sequence[PinWrite], *, now: datetime, drain_until: datetime | None) -> None:
        """``INSERT ... ON CONFLICT (pin_key) DO UPDATE`` with drain-capped expiry; no commit."""

        raise NotImplementedError

    async def lookup(self, pin_key: str, *, now: datetime) -> PinLookupResult:
        """Rows with ``purge_at > now`` answer; ``expires_at <= now`` is ``expired``."""

        raise NotImplementedError

    async def reread(self, pin_key: str) -> PinRecord | None:
        """Primary-key re-read used to verify a write whose outcome is uncertain."""

        raise NotImplementedError

    async def touch(self, pin_key: str, *, now: datetime, drain_until: datetime | None) -> bool:
        """Slide ``last_seen_at``/``expires_at``/``purge_at`` (drain-capped); ``True`` when a row changed."""

        raise NotImplementedError

    async def delete(self, pin_key: str) -> bool:
        raise NotImplementedError

    async def prune_purged(self, *, batch_size: int = 10_000) -> int:
        """Delete rows with ``purge_at <= <database now>`` in ``pin_key``-keyed batches; returns the count."""

        raise NotImplementedError

    async def count_live_by_kind(self, *, now: datetime) -> dict[str, int]:
        raise NotImplementedError

    async def max_purge_at(self) -> datetime | None:
        """Latest ``purge_at`` on the table (retention drain-invariant alarm)."""

        raise NotImplementedError


class PinCache:
    """Positive-only, TTL ``PIN_CACHE_TTL_SECONDS``, LRU-bounded at ``PIN_CACHE_MAX_ENTRIES``."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PIN_CACHE_TTL_SECONDS,
        max_entries: int = PIN_CACHE_MAX_ENTRIES,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries

    def get(self, pin_key: str, *, now: float) -> PinRecord | None:
        raise NotImplementedError

    def put(self, record: PinRecord, *, now: float) -> None:
        raise NotImplementedError

    def invalidate(self, pin_key: str) -> None:
        raise NotImplementedError


async def lookup_pin_bounded(
    pin_key: str,
    *,
    cache: PinCache | None,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
) -> PinLookupResult:
    """Cache hit or one primary-key read bounded by ``PIN_LOOKUP_DEADLINE_SECONDS``; raises ``PinLookupTimeout``."""

    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PinIntent:
    """Pins to commit before the first content frame reaches the client (I11)."""

    writes: tuple[PinWrite, ...]
    thread_key: str | None


PinWriteOutcome = Literal["written", "not_written", "unknown"]


class PinWriteExecutor:
    """Verified-durable pin write.

    ``PIN_WRITE_ACQUIRE_DEADLINE_SECONDS`` bounds acquisition of the writer
    section only (``sqlite_writer_section()`` / PostgreSQL checkout) ->
    ``not_written`` with no statement issued. Once issued, statement + COMMIT
    run under ``_await_cleanup_deferring_cancellation`` and any post-issuance
    exception is resolved by a bounded primary-key re-read -> ``written`` |
    ``not_written`` | ``unknown``.
    """

    async def commit(
        self,
        intent: PinIntent,
        *,
        drain_until: datetime | None,
        scheduler: Scheduler = REAL_SCHEDULER,
        clock: Clock = REAL_CLOCK,
    ) -> PinWriteOutcome:
        raise NotImplementedError

    async def delete_durably(self, pin_key: str, *, scheduler: Scheduler = REAL_SCHEDULER) -> PinWriteOutcome:
        """Neutral release of a pin (WP-C2 caller); same verification discipline as ``commit``."""

        raise NotImplementedError
