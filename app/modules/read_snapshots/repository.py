"""Durable analytical read values, bounded interest and fenced publication.

This module does no aggregation. Request paths may read or register interest;
only an independently leased producer can claim/publish a generation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import JSON, delete, func, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scheduling.leader_election import LeaderElection
from app.core.types import JsonValue
from app.core.utils.time import utcnow
from app.db.models import ReadSnapshot, SchedulerLeader
from app.modules.accounts.usage_rollup import _insert_fn

MAX_ENTRIES = 64
# Reserve room for every admitted timezone's three presets even when custom
# filters arrive before the first scheduler pass. The registry uses one row.
KIND_CAPACITY = {"dashboard": 1, "report-zone": 7, "report-preset": 24, "report-range": 31}
MAX_PARAMETERS_BYTES = 16 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
REGISTRY_KEY = "0" * 64
SCHEMA_VERSION = 2
DASHBOARD_INTERVAL_SECONDS = 300
REPORT_INTERVAL_SECONDS = 300
REFRESH_TIMEOUT_SECONDS = 90


class SnapshotCapacityError(ValueError):
    pass


class SnapshotParametersTooLargeError(ValueError):
    pass


def encode_parameters(kind: str, parameters: dict[str, JsonValue]) -> tuple[str, str]:
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > MAX_PARAMETERS_BYTES:
        raise SnapshotParametersTooLargeError("Snapshot filter parameters are too large")
    version = 0 if kind == "report-zone" else SCHEMA_VERSION
    digest = hashlib.sha256(f"{version}:{kind}:{encoded}".encode()).hexdigest()
    return digest, encoded


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


async def _database_now(session: AsyncSession) -> datetime:
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        value = (await session.execute(select(func.strftime("%Y-%m-%d %H:%M:%f", "now")))).scalar_one()
        return datetime.fromisoformat(str(value))
    clock = func.clock_timestamp()
    return _utc_naive((await session.execute(select(clock))).scalar_one())


@dataclass(frozen=True)
class SnapshotValue:
    payload: dict[str, JsonValue] | None
    generated_at: datetime | None
    stale: bool
    refreshing: bool
    last_error: str | None
    generation: int
    interval_seconds: int

    def metadata(self) -> dict[str, JsonValue]:
        return {
            "generatedAt": None if self.generated_at is None else self.generated_at.isoformat() + "Z",
            "stale": self.stale,
            "refreshing": self.refreshing,
            "lastError": self.last_error,
            "generation": self.generation,
            "refreshIntervalSeconds": self.interval_seconds,
        }


class SnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _insert_missing(self, values: dict[str, object]) -> None:
        stmt = _insert_fn(self.session)(ReadSnapshot).values(**values)
        stmt = stmt.on_conflict_do_nothing(index_elements=[ReadSnapshot.snapshot_key])
        await self.session.execute(stmt)

    async def register(
        self,
        kind: str,
        parameters: dict[str, JsonValue],
        *,
        pinned: bool = False,
        touch: bool = True,
    ) -> str:
        if kind not in KIND_CAPACITY:
            raise ValueError("Unknown analytical snapshot kind")
        key, encoded = encode_parameters(kind, parameters)
        now = utcnow()
        row = await self.session.get(ReadSnapshot, key)
        if row is not None:
            changed = False
            if pinned and not row.pinned:
                row.pinned = True
                changed = True
            if touch and row.requested_at < now - timedelta(seconds=60):
                row.requested_at = now
                changed = True
            if changed:
                await self.session.commit()
            return key
        # Discard the initial read view before acquiring the registry writer
        # reservation (including when a reader has already opened a transaction).
        await self.session.rollback()
        # Registry serialization bounds rows across replicas, not only in one
        # process. It never holds a lock while a report is being calculated.
        await self._insert_missing(
            {
                "snapshot_key": REGISTRY_KEY,
                "kind": "registry",
                "schema_version": 0,
                "parameters_json": "{}",
                "refresh_at": now,
                "requested_at": now,
                "pinned": True,
            }
        )
        await self.session.execute(
            update(ReadSnapshot)
            .where(ReadSnapshot.snapshot_key == REGISTRY_KEY)
            .values(requested_at=ReadSnapshot.requested_at)
        )
        row = await self.session.get(ReadSnapshot, key, with_for_update=True, populate_existing=True)
        if row is None:
            registered = (
                await self.session.execute(
                    select(ReadSnapshot.snapshot_key, ReadSnapshot.kind, ReadSnapshot.schema_version).with_for_update()
                )
            ).all()
            version = 0 if kind == "report-zone" else SCHEMA_VERSION
            # A rolling contract upgrade retains the prior replica's exact-key
            # last-good data. Per-kind admission is versioned, while the global
            # row bound still includes every version and the shared registry.
            kind_full = (
                sum(item.kind == kind and item.schema_version == version for item in registered) >= KIND_CAPACITY[kind]
            )
            if len(registered) >= MAX_ENTRIES or kind_full:
                idle = (
                    ReadSnapshot.pinned.is_(False),
                    or_(
                        ReadSnapshot.refresh_started_at.is_(None),
                        ReadSnapshot.refresh_started_at < now - timedelta(seconds=REFRESH_TIMEOUT_SECONDS),
                    ),
                )
                victim: str | None = None
                if not kind_full and kind in ("dashboard", "report-preset"):
                    # Canonical warming must not wait a day behind a full
                    # previous-contract filter cache. Keep every old pinned
                    # last-good and every active producer, without widening
                    # the registry or letting ordinary filters preempt peers.
                    victim = (
                        await self.session.execute(
                            select(ReadSnapshot.snapshot_key)
                            .where(
                                *idle,
                                ReadSnapshot.schema_version > 0,
                                ReadSnapshot.schema_version < SCHEMA_VERSION,
                            )
                            .order_by(ReadSnapshot.requested_at)
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                victims = select(ReadSnapshot.snapshot_key).where(
                    *idle,
                    ReadSnapshot.requested_at < now - timedelta(days=1),
                )
                if kind_full:
                    victims = victims.where(ReadSnapshot.kind == kind, ReadSnapshot.schema_version == version)
                if victim is None:
                    victim = (
                        await self.session.execute(victims.order_by(ReadSnapshot.requested_at).limit(1))
                    ).scalar_one_or_none()
                if victim is None:
                    await self.session.rollback()
                    raise SnapshotCapacityError("Background snapshot queue is full")
                await self.session.execute(delete(ReadSnapshot).where(ReadSnapshot.snapshot_key == victim))
            await self._insert_missing(
                {
                    "snapshot_key": key,
                    "kind": kind,
                    "parameters_json": encoded,
                    "schema_version": 0 if kind == "report-zone" else SCHEMA_VERSION,
                    "refresh_at": now,
                    "requested_at": now,
                    "pinned": pinned,
                }
            )
        await self.session.commit()
        return key

    async def read(self, key: str, *, interval_seconds: int) -> SnapshotValue | None:
        row = await self.session.get(ReadSnapshot, key, populate_existing=True)
        if row is None:
            return None
        now = utcnow()
        if row.requested_at < now - timedelta(seconds=60):
            row.requested_at = now
            await self.session.commit()
        generated = row.generated_at
        payload = None if row.payload_json is None else cast(dict[str, JsonValue], json.loads(row.payload_json))
        return SnapshotValue(
            payload=payload,
            generated_at=generated,
            stale=generated is None or now - generated >= timedelta(seconds=interval_seconds),
            refreshing=row.refresh_started_at is not None
            and now - row.refresh_started_at < timedelta(seconds=REFRESH_TIMEOUT_SECONDS),
            last_error=row.last_error,
            generation=row.published_generation,
            interval_seconds=interval_seconds,
        )

    async def due(self, kinds: tuple[str, ...], *, limit: int = 8) -> list[tuple[str, str, dict[str, JsonValue]]]:
        now = utcnow()
        rows = (
            await self.session.execute(
                select(ReadSnapshot.snapshot_key, ReadSnapshot.kind, ReadSnapshot.parameters_json)
                .where(
                    ReadSnapshot.kind.in_(kinds),
                    ReadSnapshot.schema_version == SCHEMA_VERSION,
                    ReadSnapshot.refresh_at <= now,
                    or_(ReadSnapshot.kind != "report-range", ReadSnapshot.requested_at >= now - timedelta(days=1)),
                )
                .order_by(ReadSnapshot.pinned.desc(), ReadSnapshot.refresh_at)
                .limit(limit)
            )
        ).all()
        return [(row.snapshot_key, row.kind, json.loads(row.parameters_json)) for row in rows]

    async def known_timezones(self) -> list[str]:
        rows = (
            (
                await self.session.execute(
                    select(ReadSnapshot.parameters_json)
                    .where(ReadSnapshot.kind == "report-zone")
                    .order_by(ReadSnapshot.requested_at.desc())
                    .limit(8)
                )
            )
            .scalars()
            .all()
        )
        return list(dict.fromkeys(["UTC", *(str(json.loads(value)["timezone"]) for value in rows)]))[:8]

    async def ready_for_traffic(self) -> bool:
        """Two bounded metadata reads, no payload transfer or interest writes."""
        from app.modules.read_snapshots.keys import REPORT_PRESETS, local_today

        required: dict[str, str | None] = {encode_parameters("dashboard", {})[0]: None}
        for zone in await self.known_timezones():
            for days in REPORT_PRESETS:
                required[encode_parameters("report-preset", {"days": days, "timezone": zone})[0]] = zone
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            report_end = sql_cast(ReadSnapshot.payload_json, JSON)["range"]["end"].as_string()
        else:
            report_end = func.json_extract(ReadSnapshot.payload_json, "$.range.end")
        rows = (
            await self.session.execute(
                select(ReadSnapshot.snapshot_key, ReadSnapshot.generated_at, report_end).where(
                    ReadSnapshot.snapshot_key.in_(required),
                    ReadSnapshot.schema_version == SCHEMA_VERSION,
                    ReadSnapshot.published_generation > 0,
                    ReadSnapshot.payload_json.is_not(None),
                )
            )
        ).all()
        if len(rows) != len(required):
            return False
        now = utcnow()
        for key, generated, end_date in rows:
            zone = required[key]
            interval = DASHBOARD_INTERVAL_SECONDS if zone is None else REPORT_INTERVAL_SECONDS
            if generated is None or now - generated >= timedelta(seconds=interval):
                return False
            if zone is not None and end_date != local_today(zone, now).isoformat():
                return False
        return True

    async def refresh_changed_report_date(self, days: int, zone: str, today: str) -> None:
        key, _ = encode_parameters("report-preset", {"days": days, "timezone": zone})
        row = await self.session.get(ReadSnapshot, key, populate_existing=True)
        if row is not None and row.payload_json is not None:
            payload = json.loads(row.payload_json)
            if payload.get("range", {}).get("end") != today:
                row.refresh_at = utcnow()
                await self.session.commit()

    async def _fence(self, leader: LeaderElection) -> bool:
        # Obtain the database write lock before sampling its clock. A lock
        # waiter cannot publish using a timestamp from before the wait. The
        # no-op write also acquires SQLite's cross-process writer reservation.
        await self.session.execute(
            update(SchedulerLeader)
            .where(SchedulerLeader.id == leader.lease_id)
            .values(expires_at=SchedulerLeader.expires_at)
        )
        row = await self.session.get(SchedulerLeader, leader.lease_id, with_for_update=True, populate_existing=True)
        if row is None or row.leader_id != leader.leader_id:
            return False
        return _utc_naive(row.expires_at) > await _database_now(self.session)

    async def claim(self, key: str, leader: LeaderElection) -> int | None:
        if not await self._fence(leader):
            await self.session.rollback()
            return None
        row = await self.session.get(ReadSnapshot, key, with_for_update=True, populate_existing=True)
        if row is None:
            await self.session.rollback()
            return None
        row.generation += 1
        row.refresh_started_at = utcnow()
        generation = row.generation
        await self.session.commit()
        return generation

    async def publish(
        self,
        key: str,
        generation: int,
        leader: LeaderElection,
        payload: dict[str, JsonValue],
        *,
        interval_seconds: int,
        duration_ms: float,
    ) -> bool:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("Completed snapshot exceeds its bounded payload size")
        if not await self._fence(leader):
            await self.session.rollback()
            return False
        row = await self.session.get(ReadSnapshot, key, with_for_update=True, populate_existing=True)
        if row is None or row.generation != generation:
            await self.session.rollback()
            return False
        now = utcnow()
        row.payload_json = encoded
        row.published_generation = generation
        row.generated_at = now
        row.refresh_at = now + timedelta(seconds=interval_seconds)
        row.refresh_started_at = None
        row.last_error = None
        row.duration_ms = duration_ms
        await self.session.commit()
        return True

    async def failed(self, key: str, generation: int, leader: LeaderElection, error: str) -> None:
        if not await self._fence(leader):
            await self.session.rollback()
            return
        row = await self.session.get(ReadSnapshot, key, with_for_update=True, populate_existing=True)
        if row is not None and row.generation == generation:
            row.refresh_started_at = None
            row.last_error = error[:128]
            row.refresh_at = utcnow() + timedelta(seconds=30)
        await self.session.commit()
