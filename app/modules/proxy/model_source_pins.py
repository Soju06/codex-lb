"""Durable, thread-keyed pins to a subscription-overflow model source (#2123 WP-C1).

Design v3 §3, §8.5, §8.8, §8.9. Pins make "delivered ⇒ pinned" (I11) hold
across replicas: a conversation that received content from the overflow source
keeps resolving to it for ``PIN_IDLE_TTL`` after its last turn, then stays
answerable as a tombstone for ``PIN_TOMBSTONE_GRACE`` so a late follow-up gets a
deterministic decline instead of a silent provider switch. Every write is
capped by the drain deadline (``drain_capped_expiry``) so the table is empty
when the dashboard says the drain is over.

Three layers, each usable on its own:

* ``ModelSourcePinRepository`` -- dialect-neutral statements over
  ``model_source_pins`` (precedent ``file_pin_repository.py``). Timestamps are
  timezone-aware UTC on both dialects: SQLite stores the padded
  ``YYYY-MM-DD HH:MM:SS.ffffff`` form SQLAlchemy uses for ``DateTime`` columns
  (tz-less, so every value is normalised to UTC before binding and re-tagged as
  UTC when read), PostgreSQL stores ``timestamptz``. The repository never
  commits; the DB clock decides only the retention purge.
* ``PinCache`` + ``lookup_pin_bounded`` -- a positive-only replica cache in
  front of one primary-key read bounded by ``PIN_LOOKUP_DEADLINE_SECONDS``. An
  absent or non-live row is never cached (no negative caching: another replica
  may pin the thread a moment later).
* ``PinWriteExecutor`` -- the verified-durable write. The acquisition deadline
  bounds only the wait for the writer section / pool checkout; an issued
  statement and its COMMIT always run to completion with the caller's
  cancellation deferred, and a post-issuance failure is resolved by a bounded
  primary-key re-read so the caller learns ``written`` | ``not_written`` |
  ``unknown`` instead of guessing (design §8.5, CL-4). An intent that still
  owes its anchor is refused before anything is issued (decision 78), so a
  ``store``-enabled turn is never delivered with a chain that resolves nowhere.

This module is the only request-path reader of the pin table and of the TTL
constants in ``app.modules.settings.subscription_overflow``. Its request-path
callers are the overflow decision (``app.modules.proxy.overflow``: thread,
anchor and compact lookups, the handshake evidence read, the neutral release),
the dispatch owner (``source_dispatch.py``: the pre-content commit) and the
WebSocket parity helper (bounce rows); the retention job
(``app/core/retention/job.py``) purges tombstones, checks the drain invariant
and samples the live-pin gauge.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import DateTime, Integer, String, bindparam, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.utils.shared_future import _await_result_deferring_cancellation
from app.db.models import DashboardSettings
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.settings.subscription_overflow import PIN_IDLE_TTL, PIN_KIND_THREAD, PIN_TOMBSTONE_GRACE

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
    "classify_pin",
    "drain_capped_expiry",
    "drain_deadline_from_settings",
    "get_pin_cache",
    "lookup_pin_bounded",
    "lookup_pins_bounded",
    "thread_pin_key",
]

logger = logging.getLogger(__name__)

PIN_KIND_ANCHOR = "anchor"
PIN_KIND_BOUNCE = "bounce"
_PIN_KINDS = frozenset({PIN_KIND_THREAD, PIN_KIND_ANCHOR, PIN_KIND_BOUNCE})

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
# ``_CodexBackendIdentity.thread_selection_key`` (affinity.py) tags its scope
# inline: ``...:thread_header:thread_only:<digest>`` for a bare ``thread-id`` and
# ``...:thread_header:process_thread:<digest>`` once a process-session header is
# folded in. Pins use the former only (design §3, P6): the same conversation
# must resolve to the same row from every Codex process and API key.
_THREAD_ONLY_KEY_MARKER = ":thread_header:thread_only:"
_TABLE = "model_source_pins"
_PIN_DATETIME = DateTime(timezone=True)
_COLUMNS = "pin_key, kind, source_id, api_key_id, created_at, last_seen_at, expires_at, purge_at"
_COLUMN_TYPES = {
    "pin_key": String(),
    "kind": String(),
    "source_id": String(),
    "api_key_id": String(),
    "created_at": _PIN_DATETIME,
    "last_seen_at": _PIN_DATETIME,
    "expires_at": _PIN_DATETIME,
    "purge_at": _PIN_DATETIME,
}

# The retention purge is the one statement decided by the database clock, like
# ``file_account_pins``: PostgreSQL's ``statement_timestamp()`` and SQLite's
# padded ``strftime`` form (six fractional digits, matching the stored width so
# the lexicographic comparison is exact).
_POSTGRES_STATEMENT_NOW = "statement_timestamp()"
_SQLITE_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"

_UPSERT = text(
    f"""
    INSERT INTO {_TABLE} ({_COLUMNS})
    VALUES (:pin_key, :kind, :source_id, :api_key_id, :now, :now, :expires_at, :purge_at)
    ON CONFLICT (pin_key) DO UPDATE SET
        source_id = excluded.source_id,
        api_key_id = excluded.api_key_id,
        last_seen_at = excluded.last_seen_at,
        expires_at = excluded.expires_at,
        purge_at = excluded.purge_at
    """
).bindparams(
    bindparam("now", type_=_PIN_DATETIME),
    bindparam("expires_at", type_=_PIN_DATETIME),
    bindparam("purge_at", type_=_PIN_DATETIME),
)
_SELECT_BY_KEY = text(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE pin_key = :pin_key").columns(**_COLUMN_TYPES)
_SELECT_BY_KEYS = (
    text(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE pin_key IN :pin_keys")
    .bindparams(bindparam("pin_keys", expanding=True))
    .columns(**_COLUMN_TYPES)
)
# A touch slides live thread/anchor rows only: a tombstone stays a tombstone
# until a fresh delivery re-pins the thread through ``upsert``, and a bounce
# row keeps its 60 s lifetime (its expiry rule differs, so it is never slid).
_TOUCH = text(
    f"""
    UPDATE {_TABLE}
    SET last_seen_at = :now, expires_at = :expires_at, purge_at = :purge_at
    WHERE pin_key = :pin_key AND expires_at > :now AND kind <> '{PIN_KIND_BOUNCE}'
    """
).bindparams(
    bindparam("now", type_=_PIN_DATETIME),
    bindparam("expires_at", type_=_PIN_DATETIME),
    bindparam("purge_at", type_=_PIN_DATETIME),
)
_DELETE = text(f"DELETE FROM {_TABLE} WHERE pin_key = :pin_key")
_COUNT_LIVE_BY_KIND = text(
    f"SELECT kind, COUNT(*) AS live FROM {_TABLE} WHERE expires_at > :now GROUP BY kind"
).bindparams(bindparam("now", type_=_PIN_DATETIME))
_MAX_PURGE_AT = text(f"SELECT MAX(purge_at) AS purge_at FROM {_TABLE}").columns(purge_at=_PIN_DATETIME)
# ``pin_key``-keyed batches: SQLite has no ``DELETE ... LIMIT`` without a
# compile flag, and both dialects accept the keyed subquery form.
_POSTGRES_PRUNE = text(
    f"""
    DELETE FROM {_TABLE}
    WHERE pin_key IN (
        SELECT pin_key FROM {_TABLE}
        WHERE purge_at <= {_POSTGRES_STATEMENT_NOW}
        ORDER BY purge_at
        LIMIT :batch_size
    )
    """
).bindparams(bindparam("batch_size", type_=Integer))
_SQLITE_PRUNE = text(
    f"""
    DELETE FROM {_TABLE}
    WHERE pin_key IN (
        SELECT pin_key FROM {_TABLE}
        WHERE purge_at <= {_SQLITE_NOW}
        ORDER BY purge_at
        LIMIT :batch_size
    )
    """
).bindparams(bindparam("batch_size", type_=Integer))


def build_model_source_pin_prune(*, dialect_name: str) -> TextClause:
    if dialect_name == "postgresql":
        return _POSTGRES_PRUNE
    if dialect_name == "sqlite":
        return _SQLITE_PRUNE
    raise RuntimeError(f"Unsupported database dialect for model source pins: {dialect_name}")


def _as_utc(value: datetime) -> datetime:
    """Timezone-aware UTC. Naive values are taken as UTC (the settings row's convention)."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_thread_only_key(thread_selection_key: str) -> str:
    if _THREAD_ONLY_KEY_MARKER not in thread_selection_key:
        raise ValueError("model-source pins are keyed by the thread_only selection key, never the process-thread form")
    return thread_selection_key


def thread_pin_key(thread_selection_key: str) -> str:
    """``"thread\\n" + key`` for the ``thread_only`` form; a ``process-thread`` key raises ``ValueError``."""

    return PIN_KIND_THREAD + _KEY_SEPARATOR + _require_thread_only_key(thread_selection_key)


def anchor_pin_key(api_key_id: str | None, response_id: str) -> str:
    """``"anchor\\n{api_key_id or '-'}\\n{response_id}"``."""

    if not response_id:
        raise ValueError("anchor pins require the source response id")
    return PIN_KIND_ANCHOR + _KEY_SEPARATOR + (api_key_id or "-") + _KEY_SEPARATOR + response_id


def bounce_pin_key(thread_selection_key: str) -> str:
    """``"bounce\\n" + key``; same key-form rule as ``thread_pin_key``."""

    return PIN_KIND_BOUNCE + _KEY_SEPARATOR + _require_thread_only_key(thread_selection_key)


def drain_capped_expiry(
    now: datetime,
    drain_until: datetime | None,
    *,
    ttl: timedelta = PIN_IDLE_TTL,
) -> tuple[datetime, datetime]:
    """``(expires_at, purge_at)`` for a write at ``now``.

    ``expires_at = min(now + ttl, drain_until - PIN_TOMBSTONE_GRACE - 1 d)`` and
    ``purge_at = expires_at + PIN_TOMBSTONE_GRACE`` so ``purge_at < drain_until``
    for every row written while draining (design §8.8, CL-3). Inputs may be
    naive (taken as UTC, the settings row's convention) or aware; outputs are
    always timezone-aware UTC.
    """

    if ttl < timedelta(0):
        raise ValueError("pin TTL must not be negative")
    expires_at = _as_utc(now) + ttl
    if drain_until is not None:
        expires_at = min(expires_at, _as_utc(drain_until) - PIN_TOMBSTONE_GRACE - timedelta(days=1))
    return expires_at, expires_at + PIN_TOMBSTONE_GRACE


def _bounce_expiry(now: datetime, drain_until: datetime | None) -> tuple[datetime, datetime]:
    """Bounce rows expire and purge together after ``WS_BOUNCE_TTL_SECONDS``, under the same drain cap."""

    expires_at = _as_utc(now) + timedelta(seconds=WS_BOUNCE_TTL_SECONDS)
    if drain_until is not None:
        expires_at = min(expires_at, _as_utc(drain_until) - PIN_TOMBSTONE_GRACE - timedelta(days=1))
    return expires_at, expires_at


def _expiry_for(kind: str, now: datetime, drain_until: datetime | None) -> tuple[datetime, datetime]:
    if kind == PIN_KIND_BOUNCE:
        return _bounce_expiry(now, drain_until)
    return drain_capped_expiry(now, drain_until)


def drain_deadline_from_settings(settings: DashboardSettings) -> datetime | None:
    """The armed drain deadline as timezone-aware UTC, or ``None`` when no drain is armed.

    The dashboard row stores the deadline naive UTC; pin arithmetic and the
    pin columns are timezone-aware. Background callers (the retention job) go
    through this helper instead of naming the settings column themselves.
    """

    value = settings.subscription_overflow_drain_until
    return None if value is None else _as_utc(value)


@dataclass(frozen=True, slots=True)
class PinRecord:
    """One ``model_source_pins`` row; every timestamp is timezone-aware UTC."""

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


_NONE_RESULT = PinLookupResult("none", None)


def classify_pin(record: PinRecord | None, now: datetime) -> PinLookupResult:
    """Pure state rule shared by the repository, the cache path and the tests."""

    if record is None:
        return _NONE_RESULT
    now = _as_utc(now)
    if record.purge_at <= now:
        return _NONE_RESULT
    if record.kind == PIN_KIND_BOUNCE:
        return PinLookupResult("bounce", record)
    if record.expires_at > now:
        return PinLookupResult("live", record)
    return PinLookupResult("expired", record)


class PinLookupTimeout(Exception):
    """A bounded pin lookup exceeded ``PIN_LOOKUP_DEADLINE_SECONDS``."""


def _record_from_row(row: Row[Any]) -> PinRecord:
    pin_key, kind, source_id, api_key_id, created_at, last_seen_at, expires_at, purge_at = tuple(row)
    return PinRecord(
        pin_key=str(pin_key),
        kind=str(kind),
        source_id=str(source_id),
        api_key_id=None if api_key_id is None else str(api_key_id),
        created_at=_as_utc(created_at),
        last_seen_at=_as_utc(last_seen_at),
        expires_at=_as_utc(expires_at),
        purge_at=_as_utc(purge_at),
    )


class ModelSourcePinRepository:
    """Dialect-neutral CRUD over ``model_source_pins``; never commits.

    Callers own the transaction boundary and, on file-backed SQLite, the
    ``sqlite_writer_section()`` around any write.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, writes: Sequence[PinWrite], *, now: datetime, drain_until: datetime | None) -> None:
        """``INSERT ... ON CONFLICT (pin_key) DO UPDATE`` with drain-capped expiry; no commit.

        ``created_at`` is kept from the existing row; ``source_id``,
        ``api_key_id`` and the three sliding timestamps follow the new write.
        Thread and anchor rows get ``drain_capped_expiry``; bounce rows expire
        and purge together after ``WS_BOUNCE_TTL_SECONDS`` (also drain-capped).
        """

        if not writes:
            return
        now = _as_utc(now)
        params: list[dict[str, object]] = []
        for write in writes:
            if write.kind not in _PIN_KINDS:
                raise ValueError(f"unknown model-source pin kind: {write.kind!r}")
            if not write.pin_key.startswith(write.kind + _KEY_SEPARATOR):
                raise ValueError("model-source pin keys are namespaced by their kind")
            expires_at, purge_at = _expiry_for(write.kind, now, drain_until)
            params.append(
                {
                    "pin_key": write.pin_key,
                    "kind": write.kind,
                    "source_id": write.source_id,
                    "api_key_id": write.api_key_id,
                    "now": now,
                    "expires_at": expires_at,
                    "purge_at": purge_at,
                }
            )
        await self._session.execute(_UPSERT, params)

    async def lookup(self, pin_key: str, *, now: datetime) -> PinLookupResult:
        """Rows with ``purge_at > now`` answer; ``expires_at <= now`` is ``expired``."""

        return classify_pin(await self.reread(pin_key), now)

    async def reread(self, pin_key: str) -> PinRecord | None:
        """Primary-key re-read used to verify a write whose outcome is uncertain (never cached)."""

        row = (await self._session.execute(_SELECT_BY_KEY, {"pin_key": pin_key})).first()
        return None if row is None else _record_from_row(row)

    async def reread_many(self, pin_keys: Sequence[str]) -> dict[str, PinRecord]:
        unique_keys = tuple(dict.fromkeys(pin_keys))
        if not unique_keys:
            return {}
        rows = (await self._session.execute(_SELECT_BY_KEYS, {"pin_keys": unique_keys})).all()
        records = [_record_from_row(row) for row in rows]
        return {record.pin_key: record for record in records}

    async def touch(self, pin_key: str, *, now: datetime, drain_until: datetime | None) -> bool:
        """Slide ``last_seen_at``/``expires_at``/``purge_at`` (drain-capped); ``True`` when a row changed.

        Only a live thread or anchor row slides; a tombstone, a bounce row or
        an absent key returns ``False``.
        """

        now = _as_utc(now)
        expires_at, purge_at = drain_capped_expiry(now, drain_until)
        result = await self._session.execute(
            _TOUCH,
            {"pin_key": pin_key, "now": now, "expires_at": expires_at, "purge_at": purge_at},
        )
        return _rowcount(result) > 0

    async def delete(self, pin_key: str) -> bool:
        result = await self._session.execute(_DELETE, {"pin_key": pin_key})
        return _rowcount(result) > 0

    async def prune_purged(self, *, batch_size: int = 10_000) -> int:
        """Delete one ``pin_key``-keyed batch of rows with ``purge_at <= <database now>``; returns the count.

        Callers loop until a batch comes back short and commit per batch.
        """

        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        statement = build_model_source_pin_prune(dialect_name=self._dialect_name())
        result = await self._session.execute(statement, {"batch_size": batch_size})
        return _rowcount(result)

    async def count_live_by_kind(self, *, now: datetime) -> dict[str, int]:
        rows = (await self._session.execute(_COUNT_LIVE_BY_KIND, {"now": _as_utc(now)})).all()
        return {str(kind): int(count) for kind, count in rows}

    async def max_purge_at(self) -> datetime | None:
        """Latest ``purge_at`` on the table (retention drain-invariant alarm)."""

        value = (await self._session.execute(_MAX_PURGE_AT)).scalar_one_or_none()
        return None if value is None else _as_utc(value)

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", -1)
    return int(count) if isinstance(count, int) and count >= 0 else 0


class PinCache:
    """Positive-only, TTL ``PIN_CACHE_TTL_SECONDS``, LRU-bounded at ``PIN_CACHE_MAX_ENTRIES``.

    Only live records are stored (``lookup_pin_bounded`` decides); an entry is
    dropped after ``ttl_seconds`` or when a lookup finds it no longer live, so a
    stale entry can never turn a live pin on another replica into a decline.
    Absence is never cached.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = PIN_CACHE_TTL_SECONDS,
        max_entries: int = PIN_CACHE_MAX_ENTRIES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[PinRecord, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, pin_key: str, *, now: float) -> PinRecord | None:
        entry = self._entries.get(pin_key)
        if entry is None:
            return None
        record, stored_at = entry
        if now - stored_at >= self._ttl_seconds:
            del self._entries[pin_key]
            return None
        self._entries.move_to_end(pin_key)
        return record

    def put(self, record: PinRecord, *, now: float) -> None:
        self._entries[record.pin_key] = (record, now)
        self._entries.move_to_end(record.pin_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, pin_key: str) -> None:
        self._entries.pop(pin_key, None)


_PIN_CACHE: PinCache | None = None


def get_pin_cache() -> PinCache:
    """Process-wide positive-only pin cache (one per replica worker)."""

    global _PIN_CACHE
    if _PIN_CACHE is None:
        _PIN_CACHE = PinCache()
    return _PIN_CACHE


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
WriterSection = Callable[[], AbstractAsyncContextManager[None]]


async def _read_pin(pin_key: str, session_factory: SessionFactory) -> PinRecord | None:
    async with session_factory() as session:
        return await ModelSourcePinRepository(session).reread(pin_key)


async def _read_pins(pin_keys: Sequence[str], session_factory: SessionFactory) -> dict[str, PinRecord]:
    async with session_factory() as session:
        return await ModelSourcePinRepository(session).reread_many(pin_keys)


async def lookup_pin_bounded(
    pin_key: str,
    *,
    cache: PinCache | None,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
    session_factory: SessionFactory = get_background_session,
) -> PinLookupResult:
    """Cache hit or one primary-key read bounded by ``PIN_LOOKUP_DEADLINE_SECONDS``; raises ``PinLookupTimeout``.

    A cached record is served only while it still classifies as ``live`` at
    ``clock.now()``; anything else is dropped and re-read, so the cache can
    only ever short-circuit a positive answer.
    """

    now = clock.now()
    if cache is not None:
        cached = cache.get(pin_key, now=clock.monotonic())
        if cached is not None:
            result = classify_pin(cached, now)
            if result.state == "live":
                return result
            cache.invalidate(pin_key)
    try:
        record = await scheduler.wait_for(_read_pin(pin_key, session_factory), PIN_LOOKUP_DEADLINE_SECONDS)
    except TimeoutError as exc:
        raise PinLookupTimeout(f"model-source pin lookup exceeded {PIN_LOOKUP_DEADLINE_SECONDS:g}s") from exc
    result = classify_pin(record, now)
    if cache is not None and result.state == "live" and result.record is not None:
        cache.put(result.record, now=clock.monotonic())
    return result


async def lookup_pins_bounded(
    pin_keys: Sequence[str],
    *,
    cache: PinCache | None,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
    session_factory: SessionFactory = get_background_session,
) -> dict[str, PinLookupResult]:
    """``lookup_pin_bounded`` for several keys with one bounded read (the handshake's thread + bounce evidence).

    Same cache discipline per key: a cached record is served only while it
    still classifies as ``live``; every other key is read in one
    ``reread_many`` bounded by ``PIN_LOOKUP_DEADLINE_SECONDS``, and only live
    records are cached afterwards. Raises ``PinLookupTimeout``.
    """

    now = clock.now()
    results: dict[str, PinLookupResult] = {}
    missing: list[str] = []
    for pin_key in dict.fromkeys(pin_keys):
        cached = cache.get(pin_key, now=clock.monotonic()) if cache is not None else None
        if cached is not None:
            result = classify_pin(cached, now)
            if result.state == "live":
                results[pin_key] = result
                continue
            if cache is not None:
                cache.invalidate(pin_key)
        missing.append(pin_key)
    if not missing:
        return results
    try:
        records = await scheduler.wait_for(_read_pins(missing, session_factory), PIN_LOOKUP_DEADLINE_SECONDS)
    except TimeoutError as exc:
        raise PinLookupTimeout(f"model-source pin lookup exceeded {PIN_LOOKUP_DEADLINE_SECONDS:g}s") from exc
    for pin_key in missing:
        result = classify_pin(records.get(pin_key), now)
        results[pin_key] = result
        if cache is not None and result.state == "live" and result.record is not None:
            cache.put(result.record, now=clock.monotonic())
    return results


@dataclass(frozen=True, slots=True)
class PinIntent:
    """Pins to commit before the first content frame reaches the client (I11).

    ``writes`` are known at decision time (the thread pin of a fresh dispatch;
    empty for a pinned or anchored dispatch, which only touches). An anchored
    intent (``anchor=True``: the client did not send ``store: false``) gains the
    anchor row for the source-minted response id through ``resolve`` at the
    content trigger, so SDK ``previous_response_id`` follow-ups return to the
    same source (design §3, §7.2).

    The anchor is *owed*, not optional (decision 78): an anchored intent whose
    source minted no ``response.id`` by that trigger stays ``anchor_pending``
    and ``PinWriteExecutor.commit`` refuses it, because the client would
    otherwise receive a turn whose ``previous_response_id`` chain resolves
    nowhere. An intent that owes no anchor (``store: false``, i.e. every native
    Codex turn) still commits its thread pin alone.
    """

    writes: tuple[PinWrite, ...]
    thread_key: str | None
    source_id: str | None = None
    anchor_api_key_id: str | None = None
    anchor: bool = False
    # Set by ``resolve`` when the anchor row was appended; an anchored intent
    # that never resolved still *owes* its anchor (I11: delivered => pinned
    # *and* anchored). New field last: positional construction is unchanged.
    anchor_resolved: bool = False

    def __post_init__(self) -> None:
        if self.anchor and self.source_id is None:
            raise ValueError("an anchored pin intent needs the source id")
        if self.anchor_resolved and not self.anchor:
            raise ValueError("an unanchored pin intent cannot carry a resolved anchor")

    @property
    def anchor_pending(self) -> bool:
        """An anchor this intent owes (``store`` not ``false``) that no source ``response.id`` could build."""

        return self.anchor and not self.anchor_resolved

    def resolve(self, response_id: str | None) -> PinIntent:
        """The intent with the anchor row appended iff ``anchor`` is set and the source minted a response id.

        Idempotent: an already-resolved intent is returned unchanged, so a
        second ``resolve`` can never append a duplicate anchor write.
        """

        if not self.anchor or self.anchor_resolved or not response_id or self.source_id is None:
            return self
        anchor_write = PinWrite(
            anchor_pin_key(self.anchor_api_key_id, response_id),
            PIN_KIND_ANCHOR,
            self.source_id,
            self.anchor_api_key_id,
        )
        return replace(self, writes=(*self.writes, anchor_write), anchor_resolved=True)


PinWriteOutcome = Literal["written", "not_written", "unknown"]

_Operation = Callable[[ModelSourcePinRepository], Awaitable[object]]


def _write_landed(write: PinWrite, record: PinRecord | None, now: datetime) -> bool:
    """The re-read row reflects this write (or a later one to the same source)."""

    return record is not None and record.last_seen_at >= now and record.source_id == write.source_id


class PinWriteExecutor:
    """Verified-durable pin write.

    ``PIN_WRITE_ACQUIRE_DEADLINE_SECONDS`` bounds acquisition of the writer
    section only (``sqlite_writer_section()`` / PostgreSQL checkout); a
    deadline or any failure before issuance -> ``not_written`` with no
    statement issued. Once issued, statement + COMMIT
    run under ``_await_result_deferring_cancellation`` and any post-issuance
    exception is resolved by a bounded primary-key re-read -> ``written`` |
    ``not_written`` | ``unknown``. A caller cancellation that arrives while the
    statement is in flight is deferred until the outcome is known and logged,
    then re-raised (CancelledError is a ``BaseException``; it is never turned
    into an outcome). Every non-``written`` outcome is logged at WARN as
    ``model_source_pin_write outcome=...``.

    ``commit`` refuses an intent that still owes its anchor
    (``PinIntent.anchor_pending``) before issuing anything: ``not_written``
    with ``reason=anchor_unresolved``, so the thread pin is not written either
    and no content frame is delivered unanchored (decision 78). An intent with
    nothing to write because its evidence is already durable (a pinned or
    anchored continuation) stays vacuously ``written``.

    SQLite divergence (design §8.9): the acquisition deadline covers the
    in-process writer queue; an external writer holding the file makes the
    issued statement wait up to the driver's 30 s busy timeout, uncancellable
    in-thread -- hence run-to-completion, and a worst case of 10 + 30 s before
    the outcome is known. PostgreSQL: 10 s checkout + one plain row write.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = get_background_session,
        writer_section: WriterSection = sqlite_writer_section,
    ) -> None:
        self._session_factory = session_factory
        self._writer_section = writer_section

    async def commit(
        self,
        intent: PinIntent,
        *,
        drain_until: datetime | None,
        scheduler: Scheduler = REAL_SCHEDULER,
        clock: Clock = REAL_CLOCK,
    ) -> PinWriteOutcome:
        if intent.anchor_pending:
            # I11 (delivered => pinned *and* anchored): a ``store``-enabled
            # dispatch whose source minted no ``response.id`` by the content
            # trigger cannot be anchored, so the client would receive a turn
            # whose ``previous_response_id`` chain resolves nowhere -- and, for
            # a request without a thread key, a turn with no durable row at
            # all that this method used to report as ``written``. Fail closed
            # with nothing issued; the thread pin is not written either,
            # because nothing was delivered (design §6.3, §8.5, decision 78).
            return self._log_outcome("not_written", action="upsert", kinds=PIN_KIND_ANCHOR, reason="anchor_unresolved")
        writes = intent.writes
        if not writes:
            return "written"
        # Aware UTC like every other ``now`` boundary here: ``_write_landed``
        # compares it with the re-read row's aware ``last_seen_at``.
        now = _as_utc(clock.now())
        kinds = ",".join(sorted({write.kind for write in writes}))

        async def upsert(repository: ModelSourcePinRepository) -> None:
            await repository.upsert(writes, now=now, drain_until=drain_until)

        async def verify() -> PinWriteOutcome:
            records = await self._reread_bounded([write.pin_key for write in writes], scheduler=scheduler)
            if records is None:
                return "unknown"
            landed = [_write_landed(write, records.get(write.pin_key), now) for write in writes]
            if all(landed):
                return "written"
            if not any(landed):
                return "not_written"
            return "unknown"

        return await self._run(upsert, verify, scheduler=scheduler, action="upsert", kinds=kinds)

    async def delete_durably(
        self,
        pin_key: str,
        *,
        scheduler: Scheduler = REAL_SCHEDULER,
        cache: PinCache | None = None,
    ) -> PinWriteOutcome:
        """Neutral release of a pin (WP-C2 caller); same verification discipline as ``commit``.

        ``written`` means the row is verifiably gone. ``cache`` (when given) is
        invalidated before the statement is issued and again once the outcome
        is known, so a positive replica entry never outlives the release.
        """

        if cache is not None:
            cache.invalidate(pin_key)

        async def delete(repository: ModelSourcePinRepository) -> None:
            await repository.delete(pin_key)

        async def verify() -> PinWriteOutcome:
            records = await self._reread_bounded([pin_key], scheduler=scheduler)
            if records is None:
                return "unknown"
            return "not_written" if pin_key in records else "written"

        kind = pin_key.split(_KEY_SEPARATOR, 1)[0]
        try:
            return await self._run(delete, verify, scheduler=scheduler, action="delete", kinds=kind)
        finally:
            if cache is not None:
                cache.invalidate(pin_key)

    async def _run(
        self,
        operation: _Operation,
        verify: Callable[[], Awaitable[PinWriteOutcome]],
        *,
        scheduler: Scheduler,
        action: str,
        kinds: str,
    ) -> PinWriteOutcome:
        failure: Exception | None
        cancellation: asyncio.CancelledError | None
        async with AsyncExitStack() as stack:
            try:
                # Acquisition only: the writer section (file-backed SQLite) and
                # the session's connection checkout. A timeout or failure here
                # means no statement was issued, so ``not_written`` is exact
                # (cancellation still propagates: nothing needs verifying). The
                # section is entered and left by this task (anyio locks are
                # task-bound).
                session = await scheduler.wait_for(self._acquire(stack), PIN_WRITE_ACQUIRE_DEADLINE_SECONDS)
            except TimeoutError:
                return self._log_outcome("not_written", action=action, kinds=kinds, reason="acquire_timeout")
            except Exception as exc:
                logger.warning("model_source_pin_write %s acquisition failed (%s: %s)", action, type(exc).__name__, exc)
                return self._log_outcome("not_written", action=action, kinds=kinds, reason="acquire_failed")
            # Statement + COMMIT run to completion in an owned task; the
            # caller's cancellation is deferred and returned as a marker.
            failure, cancellation = await _await_result_deferring_cancellation(
                self._issue(session, operation), scheduler=scheduler
            )
        outcome: PinWriteOutcome
        reason: str | None = None
        if failure is None:
            outcome = "written"
        else:
            logger.warning(
                "model_source_pin_write %s statement failed (%s: %s); verifying by re-read",
                action,
                type(failure).__name__,
                failure,
            )
            outcome, verify_cancellation = await _await_result_deferring_cancellation(verify(), scheduler=scheduler)
            cancellation = cancellation or verify_cancellation
            reason = "reread_failed" if outcome == "unknown" else "statement_failed"
        self._log_outcome(outcome, action=action, kinds=kinds, reason=reason)
        if cancellation is not None:
            raise cancellation
        return outcome

    async def _acquire(self, stack: AsyncExitStack) -> AsyncSession:
        await stack.enter_async_context(self._writer_section())
        session = await stack.enter_async_context(self._session_factory())
        # Checkout now so pool waits count against the acquisition deadline
        # rather than against the statement phase.
        await session.connection()
        return session

    @staticmethod
    async def _issue(session: AsyncSession, operation: _Operation) -> Exception | None:
        try:
            await operation(ModelSourcePinRepository(session))
            await session.commit()
        except Exception as exc:
            return exc
        return None

    async def _reread_bounded(self, pin_keys: Sequence[str], *, scheduler: Scheduler) -> dict[str, PinRecord] | None:
        """Primary-key re-read on a fresh session, bounded by ``PIN_LOOKUP_DEADLINE_SECONDS``; ``None`` on failure."""

        async def read() -> dict[str, PinRecord]:
            async with self._session_factory() as session:
                return await ModelSourcePinRepository(session).reread_many(pin_keys)

        try:
            return await scheduler.wait_for(read(), PIN_LOOKUP_DEADLINE_SECONDS)
        except Exception as exc:
            logger.warning("model_source_pin_write verification re-read failed (%s: %s)", type(exc).__name__, exc)
            return None

    @staticmethod
    def _log_outcome(outcome: PinWriteOutcome, *, action: str, kinds: str, reason: str | None) -> PinWriteOutcome:
        if outcome == "written":
            logger.debug("model_source_pin_write outcome=written action=%s kinds=%s", action, kinds)
        else:
            logger.warning(
                "model_source_pin_write outcome=%s action=%s kinds=%s reason=%s",
                outcome,
                action,
                kinds,
                reason or "unspecified",
            )
        return outcome
