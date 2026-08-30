from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import Row, and_, case, delete, exists, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
    HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
    HttpBridgeOperationEvent,
    HttpBridgeOperationEventChunk,
    HttpBridgeOperationRecord,
    HttpBridgeRecoveryAttemptRecord,
    HttpBridgeRecoveryAttemptState,
    HttpBridgeRetryCircuit,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
)
from app.db.session import sqlite_writer_section
from app.modules.proxy.continuity import (
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS,
    is_http_bridge_account_neutral_replay,
)
from app.modules.proxy.durable_bridge_transcript_codec import (
    DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS,
    DurableBridgeTranscriptDecodeError,
    decode_durable_bridge_transcript_chunk,
    encode_durable_bridge_transcript_chunk,
)

_ANONYMOUS_API_KEY_SCOPE = "__anonymous__"
REQUIRED_DURABLE_BRIDGE_TABLES = (
    "http_bridge_sessions",
    "http_bridge_session_aliases",
    "http_bridge_retry_circuits",
    "http_bridge_recovery_attempts",
    "http_bridge_operations",
    "http_bridge_operation_events",
    "http_bridge_operation_event_chunks",
)
DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS = 3600.0
DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE = 50
_PURGE_CLOSED_BATCH_SIZE = 500
# Claim retry budget: insert races and epoch-CAS losses re-read and retry;
# each round has a winner, so a small budget converges under any realistic
# same-row claim contention.
_CLAIM_CAS_ATTEMPTS = 5
_SESSION_ID_LOOKUP_CHUNK_SIZE = 500


class DurableBridgeAliasRegistration(StrEnum):
    REGISTERED = "registered"
    OWNER_FENCED = "owner_fenced"
    ALIAS_PROTECTED = "alias_protected"


@dataclass(frozen=True, slots=True)
class DurableBridgeAliasRegistrationReceipt:
    """Rollback data for a continuity alias published before dispatch."""

    status: DurableBridgeAliasRegistration
    session_id: str
    api_key_scope: str
    alias_kind: str
    alias_value: str
    instance_id: str
    owner_epoch: int
    previous_alias_session_id: str | None
    previous_alias_owner_epoch: int | None
    previous_alias_account_id: str | None
    previous_latest_turn_state: str | None


def durable_bridge_api_key_scope(api_key_id: str | None) -> str:
    if api_key_id is None:
        return _ANONYMOUS_API_KEY_SCOPE
    stripped = api_key_id.strip()
    return stripped or _ANONYMOUS_API_KEY_SCOPE


def durable_bridge_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def durable_bridge_operation_fingerprint(*, api_key_scope: str, request_text: str) -> str:
    """Hash the logical turn together with its authorization namespace."""
    return durable_bridge_hash(f"{api_key_scope}:{request_text}")


def durable_bridge_operation_id(session_id: str, request_fingerprint: str) -> str:
    """Derive a stable, non-secret operation key for a continuity-bound turn."""
    return f"op_{durable_bridge_hash(f'{session_id}:{request_fingerprint}')[:64]}"


def _encode_pending_tool_calls(response_id: str, value: Mapping[str, str] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        {"response_id": response_id, "calls": dict(sorted(value.items()))},
        separators=(",", ":"),
    )


def _decode_pending_tool_calls(response_id: str | None, value: str | None) -> dict[str, str] | None:
    if response_id is None or value is None:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("response_id") != response_id:
        return None
    calls = payload.get("calls")
    if not isinstance(calls, dict):
        return None
    result: dict[str, str] = {}
    for call_id, call_type in calls.items():
        if not isinstance(call_id, str) or not call_id.strip():
            return None
        if not isinstance(call_type, str) or not call_type.strip():
            return None
        result[call_id] = call_type
    return result


@dataclass(frozen=True, slots=True)
class DurableBridgeSessionSnapshot:
    id: str
    session_key_kind: str
    session_key_value: str
    session_key_hash: str
    api_key_scope: str
    owner_instance_id: str | None
    owner_epoch: int
    lease_expires_at: datetime | None
    state: HttpBridgeSessionState
    account_id: str | None
    model: str | None
    service_tier: str | None
    latest_turn_state: str | None
    latest_response_id: str | None
    latest_input_item_count: int | None
    latest_input_full_fingerprint: str | None
    last_seen_at: datetime
    closed_at: datetime | None
    latest_pending_tool_calls: dict[str, str] | None = None
    owner_process_epoch: str | None = None


@dataclass(frozen=True, slots=True)
class DurableBridgeRetryCircuitSnapshot:
    session_key_kind: str
    session_key_hash: str
    api_key_scope: str
    consecutive_failures: int
    cooldown_until_epoch: float
    last_detail: str | None
    updated_at_epoch: float
    admission_generation: int


@dataclass(frozen=True, slots=True)
class DurableBridgeRecoveryAttemptSnapshot:
    session_id: str
    request_fingerprint: str
    request_id: str
    account_id: str | None
    model: str | None
    replay_safe: bool
    state: HttpBridgeRecoveryAttemptState
    response_id: str | None


@dataclass(frozen=True, slots=True)
class DurableBridgeOperationSnapshot:
    operation_id: str
    session_id: str
    request_fingerprint: str
    account_id: str | None
    model: str | None
    parent_response_id: str | None
    state: str
    response_id: str | None
    recovery_dispatch_count: int = 0
    request_text: str | None = None
    event_spool_complete: bool = True
    created: bool = False
    rebound: bool = False
    rebound_from_session_id: str | None = None
    rebound_from_account_id: str | None = None
    rebound_from_model: str | None = None
    rebound_from_parent_response_id: str | None = None


@dataclass(frozen=True, slots=True)
class DurableBridgeTranscriptTurn:
    operation: DurableBridgeOperationSnapshot
    events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DurableBridgeOperationEventInput:
    operation_id: str
    session_id: str
    instance_id: str
    owner_epoch: int
    event_text: str


@dataclass(frozen=True, slots=True)
class DurableBridgeOperationPurgeBatchResult:
    selected_operations: int
    deleted_operations: int


class DurableBridgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _commit_writer_section(self) -> None:
        async with sqlite_writer_section():
            await self._session.commit()

    async def _delete_operation_spool_material(self, operation_ids: Sequence[str]) -> None:
        if not operation_ids:
            return
        await self._session.execute(
            delete(HttpBridgeOperationEvent).where(HttpBridgeOperationEvent.operation_id.in_(operation_ids))
        )
        await self._session.execute(
            delete(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id.in_(operation_ids))
        )

    async def _operation_has_spool_material(self, operation_id: str) -> bool:
        legacy_event = await self._session.scalar(
            select(HttpBridgeOperationEvent.event_id)
            .where(HttpBridgeOperationEvent.operation_id == operation_id)
            .limit(1)
        )
        if legacy_event is not None:
            return True
        chunk_sequence = await self._session.scalar(
            select(HttpBridgeOperationEventChunk.first_sequence_number)
            .where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            .limit(1)
        )
        return chunk_sequence is not None

    async def _operation_has_legacy_events(self, operation_id: str) -> bool:
        event_id = await self._session.scalar(
            select(HttpBridgeOperationEvent.event_id)
            .where(HttpBridgeOperationEvent.operation_id == operation_id)
            .limit(1)
        )
        return event_id is not None

    async def _lock_operation_for_chunk_append(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        expected_recovery_dispatch_count: int | None = None,
    ) -> tuple[HttpBridgeOperationRecord, bool] | None:
        owner_exists = await self._session.scalar(
            select(HttpBridgeSessionRecord.id)
            .where(
                HttpBridgeSessionRecord.id == session_id,
                HttpBridgeSessionRecord.owner_instance_id == instance_id,
                HttpBridgeSessionRecord.owner_epoch == owner_epoch,
            )
            .with_for_update()
        )
        operation_statement = select(HttpBridgeOperationRecord).where(
            HttpBridgeOperationRecord.operation_id == operation_id,
            HttpBridgeOperationRecord.session_id == session_id,
        )
        if expected_recovery_dispatch_count is not None:
            operation_statement = operation_statement.where(
                HttpBridgeOperationRecord.recovery_dispatch_count == expected_recovery_dispatch_count
            )
        operation = await self._session.scalar(operation_statement.with_for_update())
        if owner_exists is None or operation is None:
            await self._session.rollback()
            return None
        if operation.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2:
            if await self._operation_has_legacy_events(operation_id):
                return operation, False
            return operation, True
        if operation.spool_format != HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1:
            return operation, False
        if int(operation.event_bytes or 0) != 0 or await self._operation_has_spool_material(operation_id):
            return operation, False
        return operation, True

    async def _next_operation_chunk_sequence(self, operation_id: str) -> int:
        latest = (
            await self._session.execute(
                select(
                    HttpBridgeOperationEventChunk.first_sequence_number,
                    HttpBridgeOperationEventChunk.event_count,
                )
                .where(HttpBridgeOperationEventChunk.operation_id == operation_id)
                .order_by(HttpBridgeOperationEventChunk.first_sequence_number.desc())
                .limit(1)
            )
        ).one_or_none()
        if latest is None:
            return 1
        first_sequence_number, event_count = latest
        return int(first_sequence_number) + int(event_count)

    async def _chunk_append_owner_exists(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> bool:
        return (
            await self._session.scalar(
                select(HttpBridgeSessionRecord.id).where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
            )
        ) is not None

    async def _chunk_append_preflight_allows_encoding(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_bytes: int,
        event_count: int,
        max_bytes: int,
    ) -> bool:
        if not await self._chunk_append_owner_exists(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
        ):
            return False
        current_event_bytes = await self._session.scalar(
            select(HttpBridgeOperationRecord.event_bytes).where(
                HttpBridgeOperationRecord.operation_id == operation_id,
                HttpBridgeOperationRecord.session_id == session_id,
            )
        )
        if current_event_bytes is None:
            return False
        first_sequence_number = await self._next_operation_chunk_sequence(operation_id)
        return (
            int(current_event_bytes) + event_bytes <= max_bytes
            and first_sequence_number - 1 + event_count <= DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS
        )

    async def get_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = select(HttpBridgeSessionRecord).where(
            HttpBridgeSessionRecord.session_key_kind == session_key_kind,
            HttpBridgeSessionRecord.session_key_hash == durable_bridge_hash(session_key_value),
            HttpBridgeSessionRecord.api_key_scope == api_key_scope,
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def get_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
    ) -> DurableBridgeRetryCircuitSnapshot | None:
        result = await self._session.execute(
            select(HttpBridgeRetryCircuit).where(
                HttpBridgeRetryCircuit.session_key_kind == session_key_kind,
                HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(session_key_value),
                HttpBridgeRetryCircuit.api_key_scope == api_key_scope,
            )
        )
        return _to_retry_circuit_snapshot(result.scalar_one_or_none())

    async def upsert_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        consecutive_failures: int,
        cooldown_until_epoch: float,
        last_detail: str | None,
        updated_at_epoch: float,
        base_updated_at_epoch: float = 0.0,
        failure_threshold: int = 1,
        conflict_cooldown_until_epoch: float | None = None,
        base_backoff_seconds: float = 60.0,
        max_backoff_seconds: float = 600.0,
        clean_close_max_backoff_seconds: float = 30.0,
    ) -> None:
        values = {
            "session_key_kind": session_key_kind,
            "session_key_hash": durable_bridge_hash(session_key_value),
            "api_key_scope": api_key_scope,
            "consecutive_failures": consecutive_failures,
            "cooldown_until_epoch": cooldown_until_epoch,
            "last_detail": last_detail,
            "updated_at_epoch": updated_at_epoch,
        }
        threshold = max(1, failure_threshold)
        cooldown_floor = (
            max(0.0, conflict_cooldown_until_epoch)
            if conflict_cooldown_until_epoch is not None
            else max(0.0, cooldown_until_epoch)
        )
        # A reset starts a new failure lineage. Never carry the incoming
        # cooldown into that fresh lineage, even when the threshold is one.
        reset_failure_cooldown = 0.0
        base_backoff = max(0.001, base_backoff_seconds)
        max_backoff = max(base_backoff, max_backoff_seconds)
        clean_close_max_backoff = max(0.001, clean_close_max_backoff_seconds)

        def cooldown_for_failure_count(failure_count: Any, last_detail: Any) -> Any:
            regular_cooldown = case(
                (failure_count < threshold, 0.0),
                (failure_count == threshold, base_backoff),
                (failure_count == threshold + 1, min(max_backoff, base_backoff * 2.0)),
                (failure_count == threshold + 2, min(max_backoff, base_backoff * 4.0)),
                else_=max_backoff,
            )
            clean_cooldown = case(
                (failure_count < threshold, 0.0),
                else_=clean_close_max_backoff,
            )
            return case((last_detail == "clean_close", clean_cooldown), else_=regular_cooldown)

        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_statement = pg_insert(HttpBridgeRetryCircuit).values(**values)
            excluded = insert_statement.excluded
            reset_lineage = and_(
                HttpBridgeRetryCircuit.consecutive_failures == 0,
                HttpBridgeRetryCircuit.cooldown_until_epoch <= 0,
                HttpBridgeRetryCircuit.last_detail.is_(None),
                HttpBridgeRetryCircuit.updated_at_epoch > base_updated_at_epoch,
            )
            # ``updated_at_epoch`` is an observation timestamp, not a
            # concurrency version. Treat an unchanged loaded row as a CAS
            # match, even when a replica's wall clock lags it. The failure
            # count guard still rejects an older snapshot that was loaded from
            # the same row after a newer failure had already been merged.
            failure_from_loaded_row = and_(
                HttpBridgeRetryCircuit.updated_at_epoch == base_updated_at_epoch,
                excluded.consecutive_failures >= HttpBridgeRetryCircuit.consecutive_failures,
            )
            failure_is_newer_than_base = or_(
                excluded.updated_at_epoch > base_updated_at_epoch,
                failure_from_loaded_row,
            )
            conflict_failures = case(
                (reset_lineage, 1),
                (
                    failure_is_newer_than_base,
                    func.greatest(
                        HttpBridgeRetryCircuit.consecutive_failures + 1,
                        excluded.consecutive_failures,
                    ),
                ),
                else_=HttpBridgeRetryCircuit.consecutive_failures,
            )
            merged_updated_at = func.greatest(
                HttpBridgeRetryCircuit.updated_at_epoch,
                excluded.updated_at_epoch,
            )
            merged_cooldown = case(
                (reset_lineage, reset_failure_cooldown),
                (
                    conflict_failures >= threshold,
                    func.greatest(
                        cooldown_floor,
                        merged_updated_at + cooldown_for_failure_count(conflict_failures, excluded.last_detail),
                    ),
                ),
                else_=0.0,
            )
            statement = insert_statement.on_conflict_do_update(
                index_elements=[
                    HttpBridgeRetryCircuit.session_key_kind,
                    HttpBridgeRetryCircuit.session_key_hash,
                    HttpBridgeRetryCircuit.api_key_scope,
                ],
                set_={
                    "consecutive_failures": conflict_failures,
                    "cooldown_until_epoch": case(
                        (reset_lineage, reset_failure_cooldown),
                        else_=func.greatest(
                            HttpBridgeRetryCircuit.cooldown_until_epoch,
                            excluded.cooldown_until_epoch,
                            merged_cooldown,
                        ),
                    ),
                    "last_detail": case(
                        (reset_lineage, excluded.last_detail),
                        (
                            excluded.updated_at_epoch >= HttpBridgeRetryCircuit.updated_at_epoch,
                            excluded.last_detail,
                        ),
                        else_=HttpBridgeRetryCircuit.last_detail,
                    ),
                    "updated_at_epoch": case(
                        (reset_lineage, excluded.updated_at_epoch),
                        else_=func.greatest(
                            HttpBridgeRetryCircuit.updated_at_epoch,
                            excluded.updated_at_epoch,
                        ),
                    ),
                },
            )
        elif dialect == "sqlite":
            insert_statement = sqlite_insert(HttpBridgeRetryCircuit).values(**values)
            excluded = insert_statement.excluded
            reset_lineage = and_(
                HttpBridgeRetryCircuit.consecutive_failures == 0,
                HttpBridgeRetryCircuit.cooldown_until_epoch <= 0,
                HttpBridgeRetryCircuit.last_detail.is_(None),
                HttpBridgeRetryCircuit.updated_at_epoch > base_updated_at_epoch,
            )
            failure_from_loaded_row = and_(
                HttpBridgeRetryCircuit.updated_at_epoch == base_updated_at_epoch,
                excluded.consecutive_failures >= HttpBridgeRetryCircuit.consecutive_failures,
            )
            failure_is_newer_than_base = or_(
                excluded.updated_at_epoch > base_updated_at_epoch,
                failure_from_loaded_row,
            )
            conflict_failures = case(
                (reset_lineage, 1),
                (
                    failure_is_newer_than_base,
                    func.max(
                        HttpBridgeRetryCircuit.consecutive_failures + 1,
                        excluded.consecutive_failures,
                    ),
                ),
                else_=HttpBridgeRetryCircuit.consecutive_failures,
            )
            merged_updated_at = func.max(
                HttpBridgeRetryCircuit.updated_at_epoch,
                excluded.updated_at_epoch,
            )
            merged_cooldown = case(
                (reset_lineage, reset_failure_cooldown),
                (
                    conflict_failures >= threshold,
                    func.max(
                        cooldown_floor,
                        merged_updated_at + cooldown_for_failure_count(conflict_failures, excluded.last_detail),
                    ),
                ),
                else_=0.0,
            )
            statement = insert_statement.on_conflict_do_update(
                index_elements=[
                    HttpBridgeRetryCircuit.session_key_kind,
                    HttpBridgeRetryCircuit.session_key_hash,
                    HttpBridgeRetryCircuit.api_key_scope,
                ],
                set_={
                    "consecutive_failures": conflict_failures,
                    "cooldown_until_epoch": case(
                        (reset_lineage, reset_failure_cooldown),
                        else_=func.max(
                            HttpBridgeRetryCircuit.cooldown_until_epoch,
                            excluded.cooldown_until_epoch,
                            merged_cooldown,
                        ),
                    ),
                    "last_detail": case(
                        (reset_lineage, excluded.last_detail),
                        (
                            excluded.updated_at_epoch >= HttpBridgeRetryCircuit.updated_at_epoch,
                            excluded.last_detail,
                        ),
                        else_=HttpBridgeRetryCircuit.last_detail,
                    ),
                    "updated_at_epoch": case(
                        (reset_lineage, excluded.updated_at_epoch),
                        else_=func.max(
                            HttpBridgeRetryCircuit.updated_at_epoch,
                            excluded.updated_at_epoch,
                        ),
                    ),
                },
            )
        else:
            raise RuntimeError(f"DurableBridgeRepository retry circuit upsert unsupported for dialect={dialect!r}")
        async with sqlite_writer_section():
            await self._session.execute(statement)
            await self._session.commit()

    async def claim_retry_circuit_generation(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        expected_updated_at_epoch: float | None,
        expected_admission_generation: int,
        expected_consecutive_failures: int,
        expected_cooldown_until_epoch: float,
    ) -> DurableBridgeRetryCircuitSnapshot | None:
        """Linearize replay admission against a retry-circuit generation.

        ``admission_generation`` is independent from the failure observation
        timestamp. A failure committed first makes this claim fail; a delayed
        failure committed afterward still sees its original ``updated_at``
        baseline and is merged after the already-admitted dispatch.
        """
        values = {
            "session_key_kind": session_key_kind,
            "session_key_hash": durable_bridge_hash(session_key_value),
            "api_key_scope": api_key_scope,
            "consecutive_failures": 0,
            "cooldown_until_epoch": 0.0,
            "last_detail": None,
            "updated_at_epoch": time.time(),
            "admission_generation": 1,
        }
        dialect = self._session.get_bind().dialect.name
        async with sqlite_writer_section():
            if expected_updated_at_epoch is None:
                if expected_admission_generation != 0:
                    return None
                if dialect == "postgresql":
                    statement = pg_insert(HttpBridgeRetryCircuit).values(**values).on_conflict_do_nothing()
                elif dialect == "sqlite":
                    statement = sqlite_insert(HttpBridgeRetryCircuit).values(**values).on_conflict_do_nothing()
                else:
                    raise RuntimeError(
                        f"DurableBridgeRepository retry circuit claim unsupported for dialect={dialect!r}"
                    )
            else:
                statement = (
                    update(HttpBridgeRetryCircuit)
                    .where(
                        HttpBridgeRetryCircuit.session_key_kind == session_key_kind,
                        HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(session_key_value),
                        HttpBridgeRetryCircuit.api_key_scope == api_key_scope,
                        HttpBridgeRetryCircuit.admission_generation == expected_admission_generation,
                        HttpBridgeRetryCircuit.updated_at_epoch == expected_updated_at_epoch,
                        HttpBridgeRetryCircuit.consecutive_failures == expected_consecutive_failures,
                        HttpBridgeRetryCircuit.cooldown_until_epoch == expected_cooldown_until_epoch,
                    )
                    .values(admission_generation=expected_admission_generation + 1)
                )
            result = await self._session.execute(statement)
            if getattr(result, "rowcount", 0) != 1:
                await self._session.rollback()
                return None
            await self._session.commit()
        return await self.get_retry_circuit(
            session_key_kind=session_key_kind,
            session_key_value=session_key_value,
            api_key_scope=api_key_scope,
        )

    async def delete_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        expected_updated_at_epoch: float | None = None,
    ) -> None:
        conditions = [
            HttpBridgeRetryCircuit.session_key_kind == session_key_kind,
            HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(session_key_value),
            HttpBridgeRetryCircuit.api_key_scope == api_key_scope,
        ]
        if expected_updated_at_epoch is not None:
            conditions.append(HttpBridgeRetryCircuit.updated_at_epoch == expected_updated_at_epoch)
        async with sqlite_writer_section():
            await self._session.execute(
                update(HttpBridgeRetryCircuit)
                .where(*conditions)
                .values(
                    consecutive_failures=0,
                    cooldown_until_epoch=0.0,
                    last_detail=None,
                    updated_at_epoch=time.time(),
                )
            )
            await self._session.commit()

    async def purge_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        expected_updated_at_epoch: float | None = None,
    ) -> None:
        conditions = [
            HttpBridgeRetryCircuit.session_key_kind == session_key_kind,
            HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(session_key_value),
            HttpBridgeRetryCircuit.api_key_scope == api_key_scope,
        ]
        if expected_updated_at_epoch is not None:
            conditions.append(HttpBridgeRetryCircuit.updated_at_epoch == expected_updated_at_epoch)
        async with sqlite_writer_section():
            await self._session.execute(delete(HttpBridgeRetryCircuit).where(*conditions))
            await self._session.commit()

    async def get_session_by_id(self, session_id: str) -> DurableBridgeSessionSnapshot | None:
        row = await self._session.get(HttpBridgeSessionRecord, session_id)
        return _to_snapshot(row)

    async def resolve_alias(
        self,
        *,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .join(HttpBridgeSessionAlias, HttpBridgeSessionAlias.session_id == HttpBridgeSessionRecord.id)
            .where(
                HttpBridgeSessionAlias.alias_kind == alias_kind,
                HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(alias_value),
                HttpBridgeSessionAlias.api_key_scope == api_key_scope,
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def find_session_by_latest_turn_state(
        self,
        *,
        turn_state: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .where(
                HttpBridgeSessionRecord.latest_turn_state == turn_state,
                HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
            )
            .order_by(
                case((HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE, 0), else_=1),
                HttpBridgeSessionRecord.last_seen_at.desc(),
                HttpBridgeSessionRecord.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def find_session_by_latest_response_id(
        self,
        *,
        response_id: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .where(
                HttpBridgeSessionRecord.latest_response_id == response_id,
                HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
            )
            .order_by(
                case((HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE, 0), else_=1),
                HttpBridgeSessionRecord.last_seen_at.desc(),
                HttpBridgeSessionRecord.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def claim_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        instance_id: str,
        lease_ttl_seconds: float,
        account_id: str | None,
        model: str | None,
        service_tier: str | None,
        latest_turn_state: str | None,
        latest_response_id: str | None,
        allow_takeover: bool,
        owner_process_epoch: str,
        force_owner_epoch_advance: bool = False,
    ) -> DurableBridgeSessionSnapshot:
        session_key_hash = durable_bridge_hash(session_key_value)
        # ``allow_takeover`` was decided by the caller against a pre-claim
        # lookup. Once another claimant has demonstrably written this row under
        # us (lost CAS, or lost insert race), that decision is stale: the row
        # we re-read may now carry the winner's live lease, and reusing the
        # permission would let the loser steal it. Revalidate from the fresh
        # read instead — a live foreign owner then fails closed exactly like a
        # non-takeover claim, which surfaces as the correct cross-replica
        # "retry to reach the correct replica" response.
        contended = False
        # Bounded retry budget shared by the insert race (IntegrityError) and
        # the epoch CAS: every round has a winner, so a loser converges after
        # at most one fresh read per concurrent claimant.
        for attempt in range(_CLAIM_CAS_ATTEMPTS):
            now = utcnow()
            lease_expires_at = now + timedelta(seconds=max(1.0, lease_ttl_seconds))
            row = await self._session.execute(
                select(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.session_key_kind == session_key_kind,
                    HttpBridgeSessionRecord.session_key_hash == session_key_hash,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                )
                .with_for_update()
            )
            existing = row.scalar_one_or_none()
            if existing is None:
                record = HttpBridgeSessionRecord(
                    session_key_kind=session_key_kind,
                    session_key_value=session_key_value,
                    session_key_hash=session_key_hash,
                    api_key_scope=api_key_scope,
                    owner_instance_id=instance_id,
                    owner_process_epoch=owner_process_epoch,
                    owner_epoch=1,
                    lease_expires_at=lease_expires_at,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=account_id,
                    model=model,
                    service_tier=service_tier,
                    latest_turn_state=latest_turn_state,
                    latest_response_id=latest_response_id,
                    last_seen_at=now,
                    closed_at=None,
                )
                self._session.add(record)
                try:
                    await self._commit_writer_section()
                except IntegrityError:
                    await self._session.rollback()
                    if attempt < _CLAIM_CAS_ATTEMPTS - 1:
                        contended = True
                        continue
                    raise
                # Same reason the CAS path builds its own snapshot: another
                # same-instance claimant can advance this brand-new row before
                # a refresh runs, and returning that epoch would hand two
                # claimants the same fence.
                inserted_id = record.id
                return DurableBridgeSessionSnapshot(
                    id=inserted_id,
                    session_key_kind=session_key_kind,
                    session_key_value=session_key_value,
                    session_key_hash=session_key_hash,
                    api_key_scope=api_key_scope,
                    owner_instance_id=instance_id,
                    owner_process_epoch=owner_process_epoch,
                    owner_epoch=1,
                    lease_expires_at=lease_expires_at,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=account_id,
                    model=model,
                    service_tier=service_tier,
                    latest_turn_state=latest_turn_state,
                    latest_response_id=latest_response_id,
                    latest_input_item_count=None,
                    latest_input_full_fingerprint=None,
                    latest_pending_tool_calls=None,
                    last_seen_at=now,
                    closed_at=None,
                )

            state_closed = existing.state == HttpBridgeSessionState.CLOSED
            owner_absent = existing.owner_instance_id is None
            account_changed = existing.account_id != account_id
            owner_changed = existing.owner_instance_id != instance_id
            if owner_changed:
                lease_expired = existing.lease_expires_at is None or to_utc_naive(existing.lease_expires_at) <= now
                live_owned_draining = (
                    existing.state == HttpBridgeSessionState.DRAINING and not lease_expired and not owner_absent
                )
                takeover_permitted = allow_takeover and not contended
                if live_owned_draining or (
                    not takeover_permitted and not lease_expired and not owner_absent and not state_closed
                ):
                    return _to_snapshot_required(existing)
            # Every claim advances the owner epoch, including a same-owner
            # reclaim: claims come only from a successor in-memory session (a
            # reused session renews instead of claiming), so a live same-owner
            # row means the predecessor local session is retiring concurrently
            # and its outstanding fenced release/renewals must no-op rather
            # than race this claim into a closed, ownerless row (issue #1695).
            next_epoch = existing.owner_epoch + 1

            # Write through an explicit UPDATE that sets every ownership field
            # unconditionally. Mutating ORM attributes lets SQLAlchemy omit
            # fields whose values match this transaction's (possibly stale)
            # read, so a release committing between the SELECT and this write
            # survived the claim and the refresh below returned a closed,
            # ownerless row to a claimant that believed it had succeeded
            # (issue #1695; SQLite's with_for_update is a no-op).
            values: dict[str, object] = {
                "owner_instance_id": instance_id,
                "owner_process_epoch": owner_process_epoch,
                "owner_epoch": next_epoch,
                "lease_expires_at": lease_expires_at,
                "state": HttpBridgeSessionState.ACTIVE,
                "account_id": account_id,
                "model": model,
                "service_tier": service_tier,
                "last_seen_at": now,
                "closed_at": None,
            }
            if account_changed:
                values["latest_turn_state"] = latest_turn_state
                values["latest_response_id"] = latest_response_id
                values["latest_input_item_count"] = None
                values["latest_input_full_fingerprint"] = None
                values["latest_pending_tool_calls_json"] = None
            else:
                if latest_turn_state is not None:
                    values["latest_turn_state"] = latest_turn_state
                if latest_response_id is not None:
                    values["latest_response_id"] = latest_response_id
                    values["latest_input_item_count"] = None
                    values["latest_input_full_fingerprint"] = None
                    values["latest_pending_tool_calls_json"] = None
            async with sqlite_writer_section():
                # Compare-and-set on the epoch read above: SQLite's
                # with_for_update is a no-op, so two successor claims can both
                # read epoch N; without the guard both would write N+1 and both
                # believe they own the row with colliding fences. The loser's
                # update matches zero rows and retries against fresh state.
                result = await self._session.execute(
                    update(HttpBridgeSessionRecord)
                    .where(
                        HttpBridgeSessionRecord.id == existing.id,
                        HttpBridgeSessionRecord.owner_epoch == existing.owner_epoch,
                    )
                    .values(**values)
                )
                if not bool(getattr(result, "rowcount", 0)):
                    await self._session.rollback()
                    if attempt < _CLAIM_CAS_ATTEMPTS - 1:
                        contended = True
                        continue
                    raise RuntimeError("Failed to claim durable bridge session after retry")
                if account_changed:
                    await self._clear_aliases_for_session(existing.id)
                await self._session.commit()
            # Build the snapshot from the values THIS CAS wrote rather than a
            # post-commit refresh: another successor can commit its own CAS
            # between this commit and a refresh, and returning that later epoch
            # would hand this claimant a fence that collides with the winner's.
            written_turn_state = values.get("latest_turn_state", existing.latest_turn_state)
            written_response_id = values.get("latest_response_id", existing.latest_response_id)
            written_pending_json = values.get("latest_pending_tool_calls_json", existing.latest_pending_tool_calls_json)
            return DurableBridgeSessionSnapshot(
                id=existing.id,
                session_key_kind=existing.session_key_kind,
                session_key_value=existing.session_key_value,
                session_key_hash=existing.session_key_hash,
                api_key_scope=existing.api_key_scope,
                owner_instance_id=instance_id,
                owner_process_epoch=owner_process_epoch,
                owner_epoch=next_epoch,
                lease_expires_at=lease_expires_at,
                state=HttpBridgeSessionState.ACTIVE,
                account_id=account_id,
                model=model,
                service_tier=service_tier,
                latest_turn_state=cast("str | None", written_turn_state),
                latest_response_id=cast("str | None", written_response_id),
                latest_input_item_count=cast(
                    "int | None", values.get("latest_input_item_count", existing.latest_input_item_count)
                ),
                latest_input_full_fingerprint=cast(
                    "str | None",
                    values.get("latest_input_full_fingerprint", existing.latest_input_full_fingerprint),
                ),
                latest_pending_tool_calls=_decode_pending_tool_calls(
                    cast("str | None", written_response_id),
                    cast("str | None", written_pending_json),
                ),
                last_seen_at=now,
                closed_at=None,
            )
        raise RuntimeError("Failed to claim durable bridge session after retry")

    async def renew_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        latest_input_item_count: int | None = None,
        latest_input_full_fingerprint: str | None = None,
        latest_pending_tool_calls: Mapping[str, str] | None = None,
        state: HttpBridgeSessionState | None = None,
    ) -> DurableBridgeSessionSnapshot | None:
        """Renew the lease with a single fenced UPDATE.

        Fenced-out callers mutate nothing and receive the current owner snapshot.
        """

        now = utcnow()
        values: dict[str, object] = {
            "lease_expires_at": now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
            "last_seen_at": now,
        }
        if latest_turn_state is not None:
            values["latest_turn_state"] = latest_turn_state
        if latest_response_id is not None:
            values["latest_response_id"] = latest_response_id
            values["latest_pending_tool_calls_json"] = _encode_pending_tool_calls(
                latest_response_id,
                latest_pending_tool_calls,
            )
            if latest_input_item_count is None or latest_input_full_fingerprint is None:
                values["latest_input_item_count"] = None
                values["latest_input_full_fingerprint"] = None
        if latest_input_item_count is not None and latest_input_full_fingerprint is not None:
            values["latest_input_item_count"] = latest_input_item_count
            values["latest_input_full_fingerprint"] = latest_input_full_fingerprint
        if state is not None:
            values["state"] = state
        return await self._execute_fenced_session_update(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            values=values,
        )

    async def rebind_session_account(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        account_id: str,
        clear_continuity: bool = False,
    ) -> bool:
        """Persist a replacement account only while this worker owns the lease."""

        async with sqlite_writer_section():
            values: dict[str, object] = {"account_id": account_id}
            if clear_continuity:
                values.update(
                    latest_turn_state=None,
                    latest_response_id=None,
                    latest_input_item_count=None,
                    latest_input_full_fingerprint=None,
                    latest_pending_tool_calls_json=None,
                )
            result = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(**values)
            )
            if clear_continuity and bool(getattr(result, "rowcount", 0)):
                await self._clear_aliases_for_session(session_id)
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def release_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeSessionSnapshot | None:
        """Release the lease with a single fenced UPDATE.

        Fenced-out callers mutate nothing and receive the current owner snapshot.
        """

        now = utcnow()
        values: dict[str, object] = {
            "owner_instance_id": None,
            "lease_expires_at": now,
            "last_seen_at": now,
            "state": HttpBridgeSessionState.DRAINING if draining else HttpBridgeSessionState.CLOSED,
            "closed_at": None if draining else now,
        }
        return await self._execute_fenced_session_update(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            values=values,
        )

    async def clear_latest_response_anchor(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> DurableBridgeSessionSnapshot | None:
        """Invalidate a stuck eventless anchor with a single fenced UPDATE.

        Clears only the response-id anchor and the state bound to it
        (input fingerprint/count, pending tool-call manifest). Leaves
        ``latest_turn_state`` and aliases untouched so the durable session
        remains reattachable without the stale anchor. Fenced-out callers
        mutate nothing and receive the current owner snapshot.
        """

        values: dict[str, object] = {
            "latest_response_id": None,
            "latest_input_item_count": None,
            "latest_input_full_fingerprint": None,
            "latest_pending_tool_calls_json": None,
        }
        return await self._execute_fenced_session_update(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            values=values,
        )

    async def clear_latest_response_anchor_if_matches(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        instance_id: str,
        owner_epoch: int,
        response_id: str,
    ) -> DurableBridgeSessionSnapshot | None:
        """Clear one response anchor and its matching alias under the owner fence.

        The response-id predicate makes a concurrent newer completion win over
        a stale denial. Only the denied response alias is removed; turn-state
        and other response aliases remain routable.
        """

        values: dict[str, object] = {
            "latest_response_id": None,
            "latest_input_item_count": None,
            "latest_input_full_fingerprint": None,
            "latest_pending_tool_calls_json": None,
        }
        async with sqlite_writer_section():
            cleared = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                    HttpBridgeSessionRecord.latest_response_id == response_id,
                )
                .values(**values)
                .returning(HttpBridgeSessionRecord.id)
            )
            if cleared.scalar_one_or_none() is None:
                await self._session.rollback()
                return None
            await self._session.execute(
                delete(HttpBridgeSessionAlias).where(
                    HttpBridgeSessionAlias.session_id == session_id,
                    HttpBridgeSessionAlias.alias_kind == "previous_response_id",
                    HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(response_id),
                    HttpBridgeSessionAlias.alias_value == response_id,
                    HttpBridgeSessionAlias.api_key_scope == api_key_scope,
                )
            )
            row = await self._session.get(
                HttpBridgeSessionRecord,
                session_id,
                populate_existing=True,
            )
            await self._session.commit()
        return _to_snapshot(row)

    async def record_recovery_attempt(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
        request_id: str,
        account_id: str | None,
        model: str | None,
        replay_safe: bool,
    ) -> DurableBridgeRecoveryAttemptSnapshot | None:
        """Record a safe request before dispatch so an ambiguous outcome is recoverable."""
        async with sqlite_writer_section():
            # Lock the owner row through the journal write so a takeover
            # cannot advance the epoch after this check but before dispatch.
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return None
            attempt = await self._session.scalar(
                select(HttpBridgeRecoveryAttemptRecord)
                .where(HttpBridgeRecoveryAttemptRecord.session_id == session_id)
                .where(HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint)
                .with_for_update()
            )
            if attempt is None:
                attempt = HttpBridgeRecoveryAttemptRecord(
                    session_id=session_id,
                    request_fingerprint=request_fingerprint,
                    request_id=request_id,
                    account_id=account_id,
                    model=model,
                    replay_safe=replay_safe,
                    state=HttpBridgeRecoveryAttemptState.UNKNOWN,
                )
                self._session.add(attempt)
            elif attempt.state == HttpBridgeRecoveryAttemptState.REPLAYED:
                snapshot = _to_recovery_attempt_snapshot(attempt)
                await self._session.rollback()
                return snapshot
            elif attempt.request_id != request_id:
                # A different request already owns the UNKNOWN checkpoint.
                # Do not overwrite it while that request may still be between
                # admission and dispatch; the caller must fail closed rather
                # than sharing a journal generation.
                snapshot = _to_recovery_attempt_snapshot(attempt)
                await self._session.rollback()
                return snapshot
            else:
                attempt.request_id = request_id
                attempt.account_id = account_id
                attempt.model = model
                attempt.replay_safe = replay_safe
                attempt.state = HttpBridgeRecoveryAttemptState.UNKNOWN
                attempt.response_id = None
            try:
                await self._session.commit()
            except IntegrityError:
                # A concurrent owner may have inserted the same fingerprint
                # after our initial SELECT (the absent-row case cannot be
                # locked by SQLite). Re-read the winner and use its durable
                # state instead of surfacing a transient uniqueness failure.
                await self._session.rollback()
                owner_exists = await self._session.scalar(
                    select(HttpBridgeSessionRecord.id)
                    .where(
                        HttpBridgeSessionRecord.id == session_id,
                        HttpBridgeSessionRecord.owner_instance_id == instance_id,
                        HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                    )
                    .with_for_update()
                )
                if owner_exists is None:
                    await self._session.rollback()
                    return None
                attempt = await self._session.scalar(
                    select(HttpBridgeRecoveryAttemptRecord)
                    .where(HttpBridgeRecoveryAttemptRecord.session_id == session_id)
                    .where(HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint)
                )
                if attempt is None:
                    raise
                if attempt.state == HttpBridgeRecoveryAttemptState.REPLAYED:
                    snapshot = _to_recovery_attempt_snapshot(attempt)
                    await self._session.rollback()
                    return snapshot
                if attempt.request_id != request_id:
                    snapshot = _to_recovery_attempt_snapshot(attempt)
                    await self._session.rollback()
                    return snapshot
                attempt.request_id = request_id
                attempt.account_id = account_id
                attempt.model = model
                attempt.replay_safe = replay_safe
                attempt.state = HttpBridgeRecoveryAttemptState.UNKNOWN
                attempt.response_id = None
                await self._session.commit()
            await self._session.refresh(attempt)
            return _to_recovery_attempt_snapshot(attempt)

    async def lookup_recovery_attempt(
        self,
        *,
        session_id: str,
        request_fingerprint: str,
    ) -> DurableBridgeRecoveryAttemptSnapshot | None:
        attempt = await self._session.scalar(
            select(HttpBridgeRecoveryAttemptRecord)
            .where(HttpBridgeRecoveryAttemptRecord.session_id == session_id)
            .where(HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint)
            .where(HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.UNKNOWN)
            .where(HttpBridgeRecoveryAttemptRecord.replay_safe.is_(True))
        )
        return _to_recovery_attempt_snapshot(attempt) if attempt is not None else None

    async def mark_recovery_attempt_replayed(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
        response_id: str | None = None,
    ) -> bool:
        async with sqlite_writer_section():
            # Keep the owner fence and journal transition in one transaction.
            # PostgreSQL's row lock prevents a concurrent takeover from
            # advancing the epoch between the check and the state update;
            # sqlite_writer_section provides the equivalent writer
            # serialization for SQLite.
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            values: dict[str, object] = {"state": HttpBridgeRecoveryAttemptState.REPLAYED}
            if response_id is not None:
                values["response_id"] = response_id
            # A claim authorizes one replay and must only transition UNKNOWN
            # rows. Settlement (which supplies response_id) remains idempotent
            # for a REPLAYED row after the replay completes.
            claimable_states = (
                (HttpBridgeRecoveryAttemptState.UNKNOWN,)
                if response_id is None
                else (HttpBridgeRecoveryAttemptState.UNKNOWN, HttpBridgeRecoveryAttemptState.REPLAYED)
            )
            result = await self._session.execute(
                update(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.state.in_(claimable_states),
                )
                .values(**values)
            )
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def rollback_recovery_attempt_replayed(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
    ) -> bool:
        """Return a pre-dispatch replay claim to UNKNOWN under the owner fence."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            result = await self._session.execute(
                update(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.REPLAYED,
                    HttpBridgeRecoveryAttemptRecord.response_id.is_(None),
                )
                .values(state=HttpBridgeRecoveryAttemptState.UNKNOWN)
            )
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def rollback_recovery_attempt_before_dispatch(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
    ) -> bool:
        """Delete an UNKNOWN checkpoint proven not to have reached upstream."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            result = await self._session.execute(
                delete(HttpBridgeRecoveryAttemptRecord).where(
                    HttpBridgeRecoveryAttemptRecord.session_id == session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.UNKNOWN,
                )
            )
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def record_operation(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
        account_id: str | None,
        model: str | None,
        parent_response_id: str | None,
        api_key_scope: str | None = None,
        request_text: str | None = None,
        recovery_attempt_session_id: str | None = None,
        recovery_attempt_owner_epoch: int | None = None,
        recovery_attempt_fingerprint: str | None = None,
        recovery_attempt_consumed: bool = False,
    ) -> DurableBridgeOperationSnapshot | None:
        """Create a fenced operation identity, or return the existing one."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return None
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(HttpBridgeOperationRecord.operation_id == operation_id)
                .with_for_update()
            )
            if operation is None:
                fingerprint_statement = select(HttpBridgeOperationRecord).where(
                    HttpBridgeOperationRecord.request_fingerprint == request_fingerprint
                )
                if api_key_scope is not None:
                    fingerprint_statement = fingerprint_statement.join(
                        HttpBridgeSessionRecord,
                        HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
                    ).where(HttpBridgeSessionRecord.api_key_scope == api_key_scope)
                operation = await self._session.scalar(fingerprint_statement.with_for_update())
            if operation is not None:
                rebound_from_session_id = operation.session_id if operation.state == "failed" else None
                rebound_from_account_id = operation.account_id if operation.state == "failed" else None
                rebound_from_model = operation.model if operation.state == "failed" else None
                rebound_from_parent_response_id = operation.parent_response_id if operation.state == "failed" else None
                if recovery_attempt_consumed:
                    # A REPLAYED recovery checkpoint is immutable. Return the
                    # existing row for safe transcript replay or fail-closed
                    # handling; never rebind a failed row and clear its spool.
                    snapshot = _to_operation_snapshot(operation)
                    await self._session.rollback()
                    return snapshot
                rebound = False
                handoff_allowed = True
                if operation.session_id != session_id and operation.state not in {"completed", "incomplete"}:
                    # A global fingerprint can outlive the durable session
                    # that first recorded it. Do not steal an operation from
                    # a still-live owner: its stream may still be dispatching
                    # the turn, and rebinding would fence its writes while a
                    # second owner sends a duplicate upstream request.
                    previous_session = await self._session.scalar(
                        select(HttpBridgeSessionRecord)
                        .where(HttpBridgeSessionRecord.id == operation.session_id)
                        .with_for_update()
                    )
                    now = utcnow()
                    recovery_handoff_allowed = False
                    if (
                        previous_session is not None
                        and recovery_attempt_session_id == operation.session_id
                        and recovery_attempt_owner_epoch is not None
                        and recovery_attempt_fingerprint is not None
                        and previous_session.owner_instance_id == instance_id
                        and previous_session.owner_epoch == recovery_attempt_owner_epoch
                    ):
                        # A fresh account-neutral replay has already fenced
                        # the one-shot journal on the origin session. That
                        # journal owner must remain fenced until settlement,
                        # but the operation itself must move to the
                        # replacement owner so its transcript and outcome
                        # writes are accepted there. This is the only
                        # cross-session handoff allowed while the origin
                        # lease is still active.
                        recovery_attempt = await self._session.scalar(
                            select(HttpBridgeRecoveryAttemptRecord)
                            .where(
                                HttpBridgeRecoveryAttemptRecord.session_id == recovery_attempt_session_id,
                                HttpBridgeRecoveryAttemptRecord.request_fingerprint == recovery_attempt_fingerprint,
                                HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.REPLAYED,
                                HttpBridgeRecoveryAttemptRecord.response_id.is_(None),
                            )
                            .with_for_update()
                        )
                        recovery_handoff_allowed = recovery_attempt is not None
                    handoff_allowed = (
                        recovery_handoff_allowed
                        or previous_session is None
                        or not (
                            previous_session.owner_instance_id is not None
                            and previous_session.lease_expires_at is not None
                            # PostgreSQL returns timestamptz values with an
                            # attached UTC offset, while ``utcnow`` is a
                            # naive UTC value used by the durable layer.
                            # Normalize before comparing so cross-session
                            # recovery remains database-backend agnostic.
                            and to_utc_naive(previous_session.lease_expires_at) > now
                        )
                    )
                    if handoff_allowed:
                        # Transfer only nonterminal operations to the currently
                        # fenced owner before the caller resets the attempt
                        # spool; completed transcripts remain attached to
                        # their original session for replay.
                        operation.session_id = session_id
                        operation.account_id = account_id
                        operation.model = model
                        operation.parent_response_id = parent_response_id
                        if request_text is not None and operation.request_text is None:
                            operation.request_text = request_text
                        operation.updated_at = now
                if operation.state == "failed" and handoff_allowed:
                    # An explicit upstream failure is retryable. Rebind the
                    # durable operation to the current owner while preserving
                    # its global identity; concurrent reconnects will see the
                    # submitted state and remain fenced.
                    operation.session_id = session_id
                    operation.account_id = account_id
                    operation.model = model
                    operation.parent_response_id = parent_response_id
                    if request_text is not None and operation.request_text is None:
                        operation.request_text = request_text
                    operation.state = "submitted"
                    operation.response_id = None
                    # A failed attempt is a new replay attempt.  Remove the
                    # previous attempt's SSE spool atomically so a later
                    # successful retry cannot replay a stale response.failed
                    # event before its fresh response.created sequence.
                    await self._delete_operation_spool_material((operation.operation_id,))
                    operation.event_bytes = 0
                    operation.event_spool_complete = False
                    operation.spool_format = HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
                    operation.updated_at = utcnow()
                    rebound = True
                if request_text is not None and operation.request_text is None:
                    operation.request_text = request_text
                    operation.updated_at = utcnow()
                snapshot = _to_operation_snapshot(
                    operation,
                    rebound=rebound,
                    rebound_from_session_id=rebound_from_session_id,
                    rebound_from_account_id=rebound_from_account_id,
                    rebound_from_model=rebound_from_model,
                    rebound_from_parent_response_id=rebound_from_parent_response_id,
                )
                await self._session.commit()
                return snapshot
            operation = HttpBridgeOperationRecord(
                operation_id=operation_id,
                session_id=session_id,
                request_fingerprint=request_fingerprint,
                account_id=account_id,
                model=model,
                parent_response_id=parent_response_id,
                request_text=request_text,
                state="submitted",
                # A transcript is replayable only after the event batcher has
                # drained and finalized it.  Set this explicitly rather than
                # relying on a backend-specific schema default (notably the
                # pre-existing SQLite default on migrated databases).
                event_spool_complete=False,
                spool_format=HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
            )
            self._session.add(operation)
            try:
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                operation = await self._session.scalar(
                    select(HttpBridgeOperationRecord).where(HttpBridgeOperationRecord.operation_id == operation_id)
                )
                if operation is None:
                    # A reconnect may derive a different session-scoped
                    # operation ID for the same anchored request. The global
                    # fingerprint fence makes that race resolve to the
                    # already-recorded operation instead of dispatching a
                    # duplicate.
                    fingerprint_statement = select(HttpBridgeOperationRecord).where(
                        HttpBridgeOperationRecord.request_fingerprint == request_fingerprint
                    )
                    if api_key_scope is not None:
                        fingerprint_statement = fingerprint_statement.join(
                            HttpBridgeSessionRecord,
                            HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
                        ).where(HttpBridgeSessionRecord.api_key_scope == api_key_scope)
                    operation = await self._session.scalar(fingerprint_statement)
                if operation is None:
                    raise
                return _to_operation_snapshot(operation)
            await self._session.refresh(operation)
            return _to_operation_snapshot(operation, created=True)

    async def get_operation(self, *, operation_id: str) -> DurableBridgeOperationSnapshot | None:
        operation = await self._session.scalar(
            select(HttpBridgeOperationRecord).where(HttpBridgeOperationRecord.operation_id == operation_id)
        )
        return _to_operation_snapshot(operation) if operation is not None else None

    async def reset_operation_event_spool(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> bool:
        """Start a fresh transcript for a server-owned ambiguous retry."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.state.not_in(("completed", "incomplete")),
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            await self._delete_operation_spool_material((operation_id,))
            operation.event_bytes = 0
            operation.event_spool_complete = False
            operation.spool_format = HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
            operation.updated_at = utcnow()
            await self._session.commit()
        return True

    async def claim_unknown_operation_for_recovery(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        max_recovery_dispatches: int | None = None,
    ) -> bool:
        """Atomically claim an UNKNOWN operation for one recovery attempt.

        Recovery admission can be reached by multiple reconnects at once. A
        reset followed by a later state transition leaves a window where each
        reconnect can observe UNKNOWN and submit the same operation. Keep the
        owner fence, state transition, and transcript reset in one serialized
        write so exactly one caller can move UNKNOWN back to SUBMITTED.
        """
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.state == "unknown",
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            if max_recovery_dispatches is not None and operation.recovery_dispatch_count >= max_recovery_dispatches:
                await self._session.rollback()
                return False
            await self._delete_operation_spool_material((operation_id,))
            operation.state = "submitted"
            operation.response_id = None
            operation.recovery_dispatch_count += 1
            operation.event_bytes = 0
            operation.event_spool_complete = False
            operation.spool_format = HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
            operation.updated_at = utcnow()
            await self._session.commit()
        return True

    async def mark_operation_unknown(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        restore_recovery_dispatch_claim: bool = False,
    ) -> bool:
        """Fence an ambiguously dispatched SUBMITTED operation as UNKNOWN.

        The operation event reader can race the send-failure cleanup. Lock the
        row before changing it and leave an already acknowledged or terminal
        operation untouched; those states carry stronger evidence than the
        transport exception and must never be downgraded to UNKNOWN.
        """
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            if operation.state == "submitted":
                operation.state = "unknown"
                if restore_recovery_dispatch_claim and operation.recovery_dispatch_count > 0:
                    operation.recovery_dispatch_count -= 1
                operation.updated_at = utcnow()
            elif (
                restore_recovery_dispatch_claim
                and operation.state == "unknown"
                and operation.recovery_dispatch_count > 0
            ):
                # A concurrent cleanup may have fenced the row first. The
                # caller still owns a proven pre-dispatch recovery claim, so
                # refund exactly that claim while retaining UNKNOWN.
                operation.recovery_dispatch_count -= 1
                operation.updated_at = utcnow()
            await self._session.commit()
        return True

    async def rollback_operation_before_dispatch(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        restore_rebound: bool = False,
        rebound_from_session_id: str | None = None,
        rebound_from_account_id: str | None = None,
        rebound_from_model: str | None = None,
        rebound_from_parent_response_id: str | None = None,
    ) -> bool:
        """Undo an operation transition that never reached upstream."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.state == "submitted",
                    HttpBridgeOperationRecord.response_id.is_(None),
                    HttpBridgeOperationRecord.event_bytes == 0,
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            if await self._operation_has_spool_material(operation_id):
                await self._session.rollback()
                return False
            if restore_rebound:
                operation.state = "failed"
                operation.event_spool_complete = False
                if rebound_from_session_id is not None:
                    operation.session_id = rebound_from_session_id
                    operation.account_id = rebound_from_account_id
                    operation.model = rebound_from_model
                    operation.parent_response_id = rebound_from_parent_response_id
                operation.updated_at = utcnow()
            else:
                await self._session.delete(operation)
            await self._session.commit()
        return True

    async def get_operation_by_fingerprint(
        self,
        *,
        request_fingerprint: str,
        api_key_scope: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        statement = select(HttpBridgeOperationRecord).where(
            HttpBridgeOperationRecord.request_fingerprint == request_fingerprint
        )
        if api_key_scope is not None:
            statement = statement.join(
                HttpBridgeSessionRecord,
                HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
            ).where(HttpBridgeSessionRecord.api_key_scope == api_key_scope)
        operation = await self._session.scalar(statement)
        return _to_operation_snapshot(operation) if operation is not None else None

    async def get_operation_events(self, *, operation_id: str, max_bytes: int = 8 * 1024 * 1024) -> list[str]:
        operation = (
            await self._session.execute(
                select(
                    HttpBridgeOperationRecord.spool_format,
                    HttpBridgeOperationRecord.event_bytes,
                ).where(HttpBridgeOperationRecord.operation_id == operation_id)
            )
        ).one_or_none()
        if operation is None:
            return []
        spool_format = str(operation.spool_format)
        if type(operation.event_bytes) is not int:
            return []
        expected_event_bytes = operation.event_bytes
        if expected_event_bytes < 0 or expected_event_bytes > max_bytes:
            return []
        if spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1:
            result = await self._session.execute(
                select(HttpBridgeOperationEvent.event_text)
                .where(HttpBridgeOperationEvent.operation_id == operation_id)
                .order_by(HttpBridgeOperationEvent.sequence_number.asc())
            )
            events = [str(value) for value in result.scalars().all()]
            if sum(len(event.encode("utf-8")) for event in events) != expected_event_bytes:
                return []
            return events
        if spool_format != HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2:
            return []

        chunks = (
            await self._session.execute(
                select(HttpBridgeOperationEventChunk)
                .where(HttpBridgeOperationEventChunk.operation_id == operation_id)
                .order_by(HttpBridgeOperationEventChunk.first_sequence_number.asc())
            )
        ).scalars()
        expected_sequence = 1
        total_event_count = 0
        remaining_bytes = expected_event_bytes
        events: list[str] = []
        for chunk in chunks:
            if type(chunk.event_count) is not int or not isinstance(chunk.payload, bytes):
                return []
            if chunk.first_sequence_number != expected_sequence:
                return []
            total_event_count += chunk.event_count
            if total_event_count > DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS:
                return []
            try:
                decoded = decode_durable_bridge_transcript_chunk(
                    codec=chunk.codec,
                    payload=bytes(chunk.payload),
                    event_count=chunk.event_count,
                    uncompressed_bytes=chunk.uncompressed_bytes,
                    payload_sha256=chunk.payload_sha256,
                    max_uncompressed_bytes=remaining_bytes + 4 * chunk.event_count,
                )
            except DurableBridgeTranscriptDecodeError:
                return []
            decoded_bytes = sum(len(event.encode("utf-8")) for event in decoded)
            if decoded_bytes > remaining_bytes:
                return []
            events.extend(decoded)
            remaining_bytes -= decoded_bytes
            expected_sequence += chunk.event_count
        return events if remaining_bytes == 0 else []

    async def get_operation_by_response_id(self, *, response_id: str) -> DurableBridgeOperationSnapshot | None:
        operation = await self._session.scalar(
            select(HttpBridgeOperationRecord).where(
                HttpBridgeOperationRecord.response_id == response_id,
                HttpBridgeOperationRecord.state.in_(("completed", "incomplete")),
            )
        )
        return _to_operation_snapshot(operation) if operation is not None else None

    async def get_replayable_transcript(
        self,
        *,
        response_id: str,
        max_turns: int = 128,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> list[DurableBridgeTranscriptTurn] | None:
        """Return a complete parent-response chain, newest turn last.

        Missing request bodies, truncated event spools, or a broken parent
        chain make the transcript ineligible for reconstruction.
        """
        turns: list[DurableBridgeTranscriptTurn] = []
        visited: set[str] = set()
        total_bytes = 0
        current_response_id: str | None = response_id
        while current_response_id is not None:
            if current_response_id in visited or len(turns) >= max_turns:
                return None
            visited.add(current_response_id)
            operation = await self.get_operation_by_response_id(response_id=current_response_id)
            if operation is None or operation.request_text is None or not operation.event_spool_complete:
                return None
            remaining_event_bytes = max_bytes - total_bytes - len(operation.request_text.encode("utf-8"))
            if remaining_event_bytes < 0:
                return None
            events = await self.get_operation_events(
                operation_id=operation.operation_id,
                max_bytes=remaining_event_bytes,
            )
            if not events or not any(
                "response.completed" in event or "response.incomplete" in event for event in events
            ):
                return None
            turn_bytes = len(operation.request_text.encode("utf-8")) + sum(
                len(event.encode("utf-8")) for event in events
            )
            total_bytes += turn_bytes
            if total_bytes > max_bytes:
                return None
            turns.append(DurableBridgeTranscriptTurn(operation=operation, events=tuple(events)))
            current_response_id = operation.parent_response_id
        turns.reverse()
        return turns

    async def purge_operation_spool_batch(
        self,
        *,
        cutoff: datetime,
        batch_size: int = DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    ) -> DurableBridgeOperationPurgeBatchResult:
        """Delete eligible transcript material past retention.

        Nonterminal rows are purgeable only after their owning session is
        ownerless or its lease has expired. Recheck that predicate in the
        delete transaction so an in-flight operation cannot lose its
        duplicate-suppression ledger between selection and deletion.
        """
        terminal_states = ("completed", "incomplete", "failed")
        # UNKNOWN is an ambiguous, still-live operation while its owner lease
        # is active. Treat it like the other nonterminal states so retention
        # cannot delete the duplicate-suppression fence during a long-running
        # server-indefinite recovery attempt.
        nonterminal_states = ("submitted", "acknowledged", "unknown")
        stale_owner = or_(
            HttpBridgeSessionRecord.owner_instance_id.is_(None),
            HttpBridgeSessionRecord.lease_expires_at.is_(None),
            HttpBridgeSessionRecord.lease_expires_at < utcnow(),
        )
        stale_nonterminal = and_(
            HttpBridgeOperationRecord.state.in_(nonterminal_states),
            exists(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
                    stale_owner,
                )
                .correlate(HttpBridgeOperationRecord)
            ),
        )
        purgeable = or_(HttpBridgeOperationRecord.state.in_(terminal_states), stale_nonterminal)
        async with sqlite_writer_section():
            selected = await self._session.execute(
                select(HttpBridgeOperationRecord)
                .join(
                    HttpBridgeSessionRecord,
                    HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
                )
                .where(HttpBridgeOperationRecord.updated_at < cutoff, purgeable)
                .order_by(HttpBridgeOperationRecord.updated_at.asc())
                .limit(batch_size)
                .with_for_update()
            )
            # The joined FOR UPDATE locks both the operation and owning
            # session on PostgreSQL, serializing retention deletion with
            # claim_session() on the same continuity row.
            operation_ids = [str(operation.operation_id) for operation in selected.scalars().all()]
            if not operation_ids:
                await self._session.commit()
                return DurableBridgeOperationPurgeBatchResult(
                    selected_operations=0,
                    deleted_operations=0,
                )
            deleted = await self._session.execute(
                delete(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id.in_(operation_ids),
                    HttpBridgeOperationRecord.updated_at < cutoff,
                    purgeable,
                )
                .returning(HttpBridgeOperationRecord.operation_id)
            )
            deleted_ids = [str(value) for value in deleted.scalars().all()]
            if deleted_ids:
                await self._delete_operation_spool_material(deleted_ids)
            await self._session.commit()
        return DurableBridgeOperationPurgeBatchResult(
            selected_operations=len(operation_ids),
            deleted_operations=len(deleted_ids),
        )

    async def purge_operation_spool(
        self,
        *,
        cutoff: datetime,
        batch_size: int = DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    ) -> int:
        """Delete one eligible transcript batch and return actual deletes."""
        result = await self.purge_operation_spool_batch(cutoff=cutoff, batch_size=batch_size)
        return result.deleted_operations

    async def append_operation_event_chunk(
        self,
        *,
        events: Sequence[DurableBridgeOperationEventInput],
        max_bytes: int,
    ) -> bool:
        """Append one compressed v2 chunk under the durable owner fence."""
        if not events:
            return True
        first = events[0]
        if any(
            event.operation_id != first.operation_id
            or event.session_id != first.session_id
            or event.instance_id != first.instance_id
            or event.owner_epoch != first.owner_epoch
            for event in events
        ):
            return False
        event_texts = [event.event_text for event in events]
        event_bytes = sum(len(event_text.encode("utf-8")) for event_text in event_texts)
        if event_bytes > max_bytes:
            return False
        if not await self._chunk_append_preflight_allows_encoding(
            operation_id=first.operation_id,
            session_id=first.session_id,
            instance_id=first.instance_id,
            owner_epoch=first.owner_epoch,
            event_bytes=event_bytes,
            event_count=len(event_texts),
            max_bytes=max_bytes,
        ):
            return False
        try:
            encoded = await asyncio.to_thread(encode_durable_bridge_transcript_chunk, event_texts)
        except ValueError:
            return False
        async with sqlite_writer_section():
            locked_operation = await self._lock_operation_for_chunk_append(
                operation_id=first.operation_id,
                session_id=first.session_id,
                instance_id=first.instance_id,
                owner_epoch=first.owner_epoch,
            )
            if locked_operation is None:
                return False
            operation, append_allowed = locked_operation
            if not append_allowed:
                await self._session.rollback()
                return False
            if int(operation.event_bytes or 0) + event_bytes > max_bytes:
                operation.event_spool_complete = False
                await self._session.commit()
                return False
            first_sequence_number = await self._next_operation_chunk_sequence(first.operation_id)
            if first_sequence_number - 1 + len(event_texts) > DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS:
                operation.event_spool_complete = False
                await self._session.commit()
                return False
            self._session.add(
                HttpBridgeOperationEventChunk(
                    operation_id=first.operation_id,
                    first_sequence_number=first_sequence_number,
                    event_count=encoded.event_count,
                    codec=encoded.codec,
                    uncompressed_bytes=encoded.uncompressed_bytes,
                    payload=encoded.payload,
                    payload_sha256=encoded.payload_sha256,
                )
            )
            operation.spool_format = HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2
            operation.event_bytes = int(operation.event_bytes or 0) + event_bytes
            await self._session.commit()
        return True

    async def append_terminal_operation_chunk(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
        state: str,
        expected_recovery_dispatch_count: int = 0,
        response_id: str | None = None,
    ) -> bool:
        """Append a terminal v2 chunk and expose its outcome atomically."""
        event_bytes = len(event_text.encode("utf-8"))
        preflight_allows_encoding = await self._chunk_append_preflight_allows_encoding(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            event_bytes=event_bytes,
            event_count=1,
            max_bytes=max_bytes,
        )
        try:
            encoded = (
                await asyncio.to_thread(encode_durable_bridge_transcript_chunk, (event_text,))
                if preflight_allows_encoding
                else None
            )
        except ValueError:
            encoded = None
        async with sqlite_writer_section():
            locked_operation = await self._lock_operation_for_chunk_append(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
            )
            if locked_operation is None:
                return False
            operation, append_allowed = locked_operation
            if not append_allowed:
                operation.event_spool_complete = False
                operation.state = state
                if response_id is not None:
                    operation.response_id = response_id
                operation.updated_at = utcnow()
                await self._session.commit()
                return False
            if int(operation.event_bytes or 0) + event_bytes > max_bytes or encoded is None:
                operation.event_spool_complete = False
                operation.state = state
                if response_id is not None:
                    operation.response_id = response_id
                operation.updated_at = utcnow()
                await self._session.commit()
                return False
            first_sequence_number = await self._next_operation_chunk_sequence(operation_id)
            if first_sequence_number > DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS:
                operation.event_spool_complete = False
                operation.state = state
                if response_id is not None:
                    operation.response_id = response_id
                operation.updated_at = utcnow()
                await self._session.commit()
                return False
            self._session.add(
                HttpBridgeOperationEventChunk(
                    operation_id=operation_id,
                    first_sequence_number=first_sequence_number,
                    event_count=encoded.event_count,
                    codec=encoded.codec,
                    uncompressed_bytes=encoded.uncompressed_bytes,
                    payload=encoded.payload,
                    payload_sha256=encoded.payload_sha256,
                )
            )
            operation.spool_format = HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2
            operation.event_bytes = int(operation.event_bytes or 0) + event_bytes
            operation.state = state
            if response_id is not None:
                operation.response_id = response_id
            operation.event_spool_complete = True
            operation.updated_at = utcnow()
            await self._session.commit()
        return True

    async def append_operation_event(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
    ) -> bool:
        """Append one replayable SSE block under the durable owner fence."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            event_size = len(event_text.encode("utf-8"))
            if event_size > max_bytes or int(operation.event_bytes or 0) + event_size > max_bytes:
                operation.event_spool_complete = False
                await self._session.commit()
                return False
            next_sequence = await self._session.scalar(
                select(func.coalesce(func.max(HttpBridgeOperationEvent.sequence_number), 0) + 1).where(
                    HttpBridgeOperationEvent.operation_id == operation_id,
                )
            )
            sequence = int(next_sequence or 1)
            self._session.add(
                HttpBridgeOperationEvent(
                    operation_id=operation_id,
                    sequence_number=sequence,
                    # Include occurrence position so identical downstream
                    # blocks remain distinct in replay transcripts.
                    event_fingerprint=durable_bridge_hash(f"{sequence}:{event_text}"),
                    event_text=event_text,
                )
            )
            operation.event_bytes = int(operation.event_bytes or 0) + event_size
            await self._session.commit()
        return True

    async def append_terminal_operation_event(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        event_text: str,
        max_bytes: int,
        state: str,
        expected_recovery_dispatch_count: int = 0,
        response_id: str | None = None,
    ) -> bool:
        """Append a terminal event and expose its operation state atomically."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.recovery_dispatch_count == expected_recovery_dispatch_count,
                    HttpBridgeOperationRecord.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            event_size = len(event_text.encode("utf-8"))
            persisted = event_size <= max_bytes and int(operation.event_bytes or 0) + event_size <= max_bytes
            if persisted:
                next_sequence = await self._session.scalar(
                    select(func.coalesce(func.max(HttpBridgeOperationEvent.sequence_number), 0) + 1).where(
                        HttpBridgeOperationEvent.operation_id == operation_id,
                    )
                )
                sequence = int(next_sequence or 1)
                self._session.add(
                    HttpBridgeOperationEvent(
                        operation_id=operation_id,
                        sequence_number=sequence,
                        event_fingerprint=durable_bridge_hash(f"{sequence}:{event_text}"),
                        event_text=event_text,
                    )
                )
                operation.event_bytes = int(operation.event_bytes or 0) + event_size
            else:
                operation.event_spool_complete = False
                # The terminal outcome is still authoritative even when the
                # transcript block cannot fit in the bounded spool. Expose
                # the failed state so an identical retry does not remain
                # fenced as an in-flight operation until retention expires.
                operation.state = state
                if response_id is not None:
                    operation.response_id = response_id
                operation.updated_at = utcnow()
                await self._session.commit()
                return False
            operation.state = state
            if response_id is not None:
                operation.response_id = response_id
            operation.event_spool_complete = True
            operation.updated_at = utcnow()
            await self._session.commit()
        return persisted

    async def append_operation_events(
        self,
        *,
        events: Sequence[DurableBridgeOperationEventInput],
        max_bytes: int,
    ) -> bool:
        """Append a batch of SSE blocks with one fenced transaction."""
        if not events:
            return True
        first = events[0]
        if any(
            event.operation_id != first.operation_id
            or event.session_id != first.session_id
            or event.instance_id != first.instance_id
            or event.owner_epoch != first.owner_epoch
            for event in events
        ):
            return False
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == first.session_id,
                    HttpBridgeSessionRecord.owner_instance_id == first.instance_id,
                    HttpBridgeSessionRecord.owner_epoch == first.owner_epoch,
                )
                .with_for_update()
            )
            operation = await self._session.scalar(
                select(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == first.operation_id,
                    HttpBridgeOperationRecord.session_id == first.session_id,
                    HttpBridgeOperationRecord.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
                )
                .with_for_update()
            )
            if owner_exists is None or operation is None:
                await self._session.rollback()
                return False
            next_sequence = await self._session.scalar(
                select(func.coalesce(func.max(HttpBridgeOperationEvent.sequence_number), 0) + 1).where(
                    HttpBridgeOperationEvent.operation_id == first.operation_id,
                )
            )
            sequence = int(next_sequence or 1)
            pending: list[tuple[str, int, str, int]] = []
            total_bytes = int(operation.event_bytes or 0)
            for event in events:
                event_size = len(event.event_text.encode("utf-8"))
                if total_bytes + event_size > max_bytes:
                    operation.event_spool_complete = False
                    await self._session.commit()
                    return False
                total_bytes += event_size
                pending.append(
                    (
                        event.event_text,
                        sequence,
                        durable_bridge_hash(f"{sequence}:{event.event_text}"),
                        event_size,
                    )
                )
                sequence += 1
            if pending:
                for event_text, sequence_number, fingerprint, event_size in pending:
                    self._session.add(
                        HttpBridgeOperationEvent(
                            operation_id=first.operation_id,
                            sequence_number=sequence_number,
                            event_fingerprint=fingerprint,
                            event_text=event_text,
                        )
                    )
                operation.event_bytes = total_bytes
            await self._session.commit()
        return True

    async def finalize_operation_event_spool(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> bool:
        """Mark a terminal operation replay-complete after its queue drained."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            result = await self._session.execute(
                update(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.state.in_(("completed", "incomplete")),
                    HttpBridgeOperationRecord.event_spool_complete.is_(False),
                )
                .values(event_spool_complete=True, updated_at=utcnow())
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def get_latest_completed_operation(
        self,
        *,
        session_id: str,
        parent_response_id: str,
        request_fingerprint: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        predicates = [
            HttpBridgeOperationRecord.session_id == session_id,
            HttpBridgeOperationRecord.parent_response_id == parent_response_id,
            HttpBridgeOperationRecord.state == "completed",
            HttpBridgeOperationRecord.response_id.is_not(None),
        ]
        if request_fingerprint is not None:
            predicates.append(HttpBridgeOperationRecord.request_fingerprint == request_fingerprint)
        operation = await self._session.scalar(
            select(HttpBridgeOperationRecord)
            .where(*predicates)
            .order_by(HttpBridgeOperationRecord.updated_at.desc())
            .limit(1)
        )
        return _to_operation_snapshot(operation) if operation is not None else None

    async def get_latest_completed_operation_any_session(
        self,
        *,
        parent_response_id: str,
        api_key_scope: str | None = None,
        request_fingerprint: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        statement = select(HttpBridgeOperationRecord)
        if api_key_scope is not None:
            statement = statement.join(
                HttpBridgeSessionRecord,
                HttpBridgeSessionRecord.id == HttpBridgeOperationRecord.session_id,
            ).where(HttpBridgeSessionRecord.api_key_scope == api_key_scope)
        operation = await self._session.scalar(
            statement.where(
                HttpBridgeOperationRecord.parent_response_id == parent_response_id,
                HttpBridgeOperationRecord.state == "completed",
                HttpBridgeOperationRecord.response_id.is_not(None),
                *(
                    [HttpBridgeOperationRecord.request_fingerprint == request_fingerprint]
                    if request_fingerprint is not None
                    else []
                ),
            )
            .order_by(HttpBridgeOperationRecord.updated_at.desc())
            .limit(1)
        )
        return _to_operation_snapshot(operation) if operation is not None else None

    async def settle_terminal_append_failure(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        state: str,
        expected_response_id: str | None,
        expected_recovery_dispatch_count: int = 0,
        alternate_expected_response_id: str | None = None,
        response_id: str | None = None,
    ) -> bool:
        """Settle only the terminal attempt whose append outcome was ambiguous."""
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            acknowledged_response_matches = (
                HttpBridgeOperationRecord.response_id == expected_response_id
                if expected_response_id is not None
                else HttpBridgeOperationRecord.response_id.is_(None)
            )
            if alternate_expected_response_id is not None:
                acknowledged_response_matches = or_(
                    acknowledged_response_matches,
                    HttpBridgeOperationRecord.response_id == alternate_expected_response_id,
                )
            terminal_response_matches = (
                HttpBridgeOperationRecord.response_id == response_id
                if response_id is not None
                else HttpBridgeOperationRecord.response_id.is_(None)
            )
            values: dict[str, object] = {
                "state": state,
                "event_spool_complete": False,
                "updated_at": utcnow(),
            }
            if response_id is not None:
                values["response_id"] = response_id
            result = await self._session.execute(
                update(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                    HttpBridgeOperationRecord.recovery_dispatch_count == expected_recovery_dispatch_count,
                    or_(
                        and_(HttpBridgeOperationRecord.state == "acknowledged", acknowledged_response_matches),
                        and_(
                            HttpBridgeOperationRecord.state == state,
                            or_(acknowledged_response_matches, terminal_response_matches),
                        ),
                    ),
                )
                .values(**values)
            )
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def update_operation(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        state: str,
        response_id: str | None = None,
    ) -> bool:
        async with sqlite_writer_section():
            owner_exists = await self._session.scalar(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .with_for_update()
            )
            if owner_exists is None:
                await self._session.rollback()
                return False
            values: dict[str, object] = {"state": state, "updated_at": utcnow()}
            if response_id is not None:
                values["response_id"] = response_id
            result = await self._session.execute(
                update(HttpBridgeOperationRecord)
                .where(
                    HttpBridgeOperationRecord.operation_id == operation_id,
                    HttpBridgeOperationRecord.session_id == session_id,
                )
                .values(**values)
            )
            await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def _execute_fenced_session_update(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        values: dict[str, object],
    ) -> DurableBridgeSessionSnapshot | None:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(**values)
                .returning(*_SNAPSHOT_COLUMNS)
            )
            updated_row = result.one_or_none()
            await self._session.commit()
        if updated_row is not None:
            return _returned_row_to_snapshot(updated_row)
        current = await self._session.get(HttpBridgeSessionRecord, session_id, populate_existing=True)
        return _to_snapshot(current)

    async def get_sessions_by_ids(
        self,
        session_ids: Sequence[str],
        *,
        chunk_size: int = _SESSION_ID_LOOKUP_CHUNK_SIZE,
    ) -> list[DurableBridgeSessionSnapshot]:
        unique_ids = list(dict.fromkeys(session_ids))
        if not unique_ids:
            return []
        snapshots: list[DurableBridgeSessionSnapshot] = []
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start : start + chunk_size]
            result = await self._session.execute(
                select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.id.in_(chunk))
            )
            snapshots.extend(_to_snapshot_required(row) for row in result.scalars().all())
        return snapshots

    async def mark_owner_draining(self, *, instance_id: str) -> int:
        result = await self._session.execute(
            select(HttpBridgeSessionRecord).where(
                HttpBridgeSessionRecord.owner_instance_id == instance_id,
                HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE,
            )
        )
        rows = list(result.scalars().all())
        now = utcnow()
        for row in rows:
            row.state = HttpBridgeSessionState.DRAINING
            row.last_seen_at = now
        await self._commit_writer_section()
        return len(rows)

    async def purge_owned_sessions_on_startup(
        self,
        *,
        instance_id: str,
        owner_process_epoch: str | None = None,
        ownerless_cutoff: datetime | None = None,
        batch_size: int = _PURGE_CLOSED_BATCH_SIZE,
    ) -> int:
        """Remove durable bridge rows left by the previous process instance.

        Ownerless ACTIVE/DRAINING rows are preserved by default: a graceful
        drain release intentionally clears ownership while keeping continuity
        aliases reusable until the full bridge idle-retention window.  Callers
        that already computed that retention cutoff may pass ``ownerless_cutoff``
        to piggyback that abandoned-row cleanup onto startup.
        """

        deleted_count = 0
        while True:
            now = utcnow()
            if owner_process_epoch is None:
                owned_restart_filter = HttpBridgeSessionRecord.owner_instance_id == instance_id
            else:
                owned_restart_filter = and_(
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    or_(
                        HttpBridgeSessionRecord.owner_process_epoch.is_(None),
                        HttpBridgeSessionRecord.owner_process_epoch != owner_process_epoch,
                    ),
                )
            purge_predicates = [owned_restart_filter]
            if ownerless_cutoff is not None:
                purge_predicates.append(
                    and_(
                        HttpBridgeSessionRecord.owner_instance_id.is_(None),
                        HttpBridgeSessionRecord.state.in_(
                            (HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING),
                        ),
                        or_(
                            HttpBridgeSessionRecord.lease_expires_at.is_(None),
                            HttpBridgeSessionRecord.lease_expires_at < now,
                        ),
                        HttpBridgeSessionRecord.last_seen_at < ownerless_cutoff,
                    )
                )
            startup_purge_filter = or_(*purge_predicates)
            result = await self._session.execute(
                select(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                    HttpBridgeSessionRecord.owner_instance_id,
                    HttpBridgeSessionRecord.owner_process_epoch,
                    HttpBridgeSessionRecord.last_seen_at,
                )
                .where(startup_purge_filter)
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            candidates = list(result.all())
            session_ids = [candidate.id for candidate in candidates]
            if not session_ids:
                return deleted_count
            # Operation rows are the durable recovery ledger. Never cascade
            # delete a session that still owns a retained operation, including
            # completed replayable transcripts; detach it so the next instance
            # can inspect and take over without losing continuity history.
            operation_session_ids = set(
                await self._session.scalars(
                    select(HttpBridgeOperationRecord.session_id).where(
                        HttpBridgeOperationRecord.session_id.in_(session_ids),
                    )
                )
            )
            retained_recovery_ids = {
                candidate.id
                for candidate in candidates
                if candidate.id in operation_session_ids
                or (
                    candidate.owner_instance_id == instance_id
                    and getattr(candidate, "owner_process_epoch", None) == owner_process_epoch
                    and (
                        ownerless_cutoff is None
                        or to_utc_naive(candidate.last_seen_at) >= to_utc_naive(ownerless_cutoff)
                    )
                    and is_http_bridge_account_neutral_replay(
                        kind=candidate.session_key_kind,
                        key=candidate.session_key_value,
                    )
                )
            }
            async with sqlite_writer_section():
                ownerless_operation_ids = {
                    candidate.id
                    for candidate in candidates
                    if candidate.id in retained_recovery_ids
                    and candidate.id in operation_session_ids
                    and candidate.owner_instance_id is None
                }
                if ownerless_operation_ids:
                    # The ownerless-cutoff predicate is part of the same
                    # startup query. Refresh retained rows so the bounded
                    # loop cannot select them forever while their operation
                    # transcript is awaiting normal retention cleanup.
                    await self._session.execute(
                        update(HttpBridgeSessionRecord)
                        .where(
                            HttpBridgeSessionRecord.id.in_(ownerless_operation_ids),
                            HttpBridgeSessionRecord.owner_instance_id.is_(None),
                        )
                        .values(last_seen_at=now, lease_expires_at=now)
                    )
                if retained_recovery_ids:
                    # A process can die after recording a submitted
                    # operation but before upstream acknowledges it. Once
                    # startup has fenced and detached that owner's session,
                    # classify those rows as UNKNOWN so the replacement can
                    # enter the normal proof-gated recovery path.
                    operation_retained_session_ids = retained_recovery_ids & operation_session_ids
                    if operation_retained_session_ids:
                        eligible_operation_sessions = set(
                            await self._session.scalars(
                                select(HttpBridgeSessionRecord.id)
                                .where(
                                    HttpBridgeSessionRecord.id.in_(operation_retained_session_ids),
                                    startup_purge_filter,
                                )
                                .with_for_update()
                            )
                        )
                        await self._session.execute(
                            update(HttpBridgeOperationRecord)
                            .where(
                                HttpBridgeOperationRecord.session_id.in_(eligible_operation_sessions),
                                HttpBridgeOperationRecord.state == "submitted",
                            )
                            .values(state="unknown", updated_at=now)
                        )
                    await self._session.execute(
                        update(HttpBridgeSessionRecord)
                        .where(
                            HttpBridgeSessionRecord.id.in_(retained_recovery_ids),
                            # Detach the rows selected as belonging to the
                            # previous process.  With an explicit new epoch,
                            # matching the new epoch here would leave old
                            # retained rows selected forever on every loop.
                            startup_purge_filter,
                        )
                        .values(
                            owner_instance_id=None,
                            lease_expires_at=now,
                            state=HttpBridgeSessionState.DRAINING,
                            closed_at=None,
                        )
                    )
                deletable_ids = [session_id for session_id in session_ids if session_id not in retained_recovery_ids]
                if deletable_ids:
                    if owner_process_epoch is None:
                        deleted = await self._session.execute(
                            delete(HttpBridgeSessionRecord)
                            .where(HttpBridgeSessionRecord.id.in_(deletable_ids))
                            .where(startup_purge_filter)
                            .returning(HttpBridgeSessionRecord.id)
                        )
                        deleted_ids = list(deleted.scalars().all())
                    else:
                        previous_process_ids = [
                            candidate.id for candidate in candidates if candidate.owner_instance_id == instance_id
                        ]
                        ownerless_ids = [
                            candidate.id
                            for candidate in candidates
                            if candidate.owner_instance_id is None and candidate.id not in retained_recovery_ids
                        ]
                        retired_ids: list[str] = []
                        if previous_process_ids:
                            retired = await self._session.execute(
                                update(HttpBridgeSessionRecord)
                                .where(HttpBridgeSessionRecord.id.in_(previous_process_ids))
                                .where(
                                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                                    or_(
                                        HttpBridgeSessionRecord.owner_process_epoch.is_(None),
                                        HttpBridgeSessionRecord.owner_process_epoch != owner_process_epoch,
                                    ),
                                )
                                .values(
                                    owner_instance_id=None,
                                    lease_expires_at=None,
                                    state=HttpBridgeSessionState.CLOSED,
                                    closed_at=now,
                                    last_seen_at=now,
                                    latest_turn_state=None,
                                    latest_response_id=None,
                                    latest_input_item_count=None,
                                    latest_input_full_fingerprint=None,
                                    latest_pending_tool_calls_json=None,
                                )
                                .returning(HttpBridgeSessionRecord.id)
                            )
                            retired_ids = list(retired.scalars().all())
                        deleted_ownerless_ids: list[str] = []
                        if ownerless_ids:
                            deleted_ownerless = await self._session.execute(
                                delete(HttpBridgeSessionRecord)
                                .where(HttpBridgeSessionRecord.id.in_(ownerless_ids))
                                .where(
                                    HttpBridgeSessionRecord.owner_instance_id.is_(None),
                                    HttpBridgeSessionRecord.state.in_(
                                        (HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING),
                                    ),
                                    or_(
                                        HttpBridgeSessionRecord.lease_expires_at.is_(None),
                                        HttpBridgeSessionRecord.lease_expires_at < now,
                                    ),
                                    HttpBridgeSessionRecord.last_seen_at < ownerless_cutoff
                                    if ownerless_cutoff is not None
                                    else true(),
                                )
                                .returning(HttpBridgeSessionRecord.id)
                            )
                            deleted_ownerless_ids = list(deleted_ownerless.scalars().all())
                        deleted_ids = retired_ids + deleted_ownerless_ids
                else:
                    deleted_ids = []
                if deleted_ids:
                    await self._session.execute(
                        delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id.in_(deleted_ids))
                    )
                await self._session.commit()
            deleted_count += len(deleted_ids)

    async def purge_closed_before(self, cutoff: datetime, *, batch_size: int = _PURGE_CLOSED_BATCH_SIZE) -> int:
        deleted_count = 0
        while True:
            result = await self._session.execute(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED,
                    HttpBridgeSessionRecord.last_seen_at < cutoff,
                    ~exists(
                        select(HttpBridgeOperationRecord.operation_id).where(
                            HttpBridgeOperationRecord.session_id == HttpBridgeSessionRecord.id,
                        )
                    ),
                )
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            session_ids = list(result.scalars().all())
            if not session_ids:
                return deleted_count
            async with sqlite_writer_section():
                await self._session.execute(
                    delete(HttpBridgeSessionAlias).where(
                        HttpBridgeSessionAlias.session_id.in_(
                            select(HttpBridgeSessionRecord.id).where(
                                HttpBridgeSessionRecord.id.in_(session_ids),
                                HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED,
                                HttpBridgeSessionRecord.last_seen_at < cutoff,
                                ~exists(
                                    select(HttpBridgeOperationRecord.operation_id).where(
                                        HttpBridgeOperationRecord.session_id == HttpBridgeSessionRecord.id,
                                    )
                                ),
                            )
                        )
                    )
                )
                deleted = await self._session.execute(
                    delete(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id.in_(session_ids))
                    .where(HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED)
                    .where(HttpBridgeSessionRecord.last_seen_at < cutoff)
                    .where(
                        ~exists(
                            select(HttpBridgeOperationRecord.operation_id).where(
                                HttpBridgeOperationRecord.session_id == HttpBridgeSessionRecord.id,
                            )
                        )
                    )
                    .returning(HttpBridgeSessionRecord.id)
                )
                await self._session.commit()
            deleted_count += len(deleted.scalars().all())

    async def purge_abandoned_before(self, cutoff: datetime, *, batch_size: int = _PURGE_CLOSED_BATCH_SIZE) -> int:
        """Purge ACTIVE/DRAINING rows whose lease expired and whose activity predates the cutoff."""

        deleted_count = 0
        while True:
            now = utcnow()
            abandoned_filter = (
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
                or_(
                    HttpBridgeSessionRecord.lease_expires_at.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at < now,
                ),
                HttpBridgeSessionRecord.last_seen_at < cutoff,
                ~exists(
                    select(HttpBridgeOperationRecord.operation_id).where(
                        HttpBridgeOperationRecord.session_id == HttpBridgeSessionRecord.id,
                    )
                ),
            )
            result = await self._session.execute(
                select(HttpBridgeSessionRecord.id)
                .where(*abandoned_filter)
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            session_ids = list(result.scalars().all())
            if not session_ids:
                return deleted_count
            async with sqlite_writer_section():
                await self._session.execute(
                    delete(HttpBridgeSessionAlias).where(
                        HttpBridgeSessionAlias.session_id.in_(
                            select(HttpBridgeSessionRecord.id).where(
                                HttpBridgeSessionRecord.id.in_(session_ids),
                                *abandoned_filter,
                            )
                        )
                    )
                )
                deleted = await self._session.execute(
                    delete(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id.in_(session_ids))
                    .where(*abandoned_filter)
                    .returning(HttpBridgeSessionRecord.id)
                )
                await self._session.commit()
            deleted_count += len(deleted.scalars().all())

    async def purge_retry_circuits_before(
        self,
        cutoff_epoch: float,
        *,
        batch_size: int = _PURGE_CLOSED_BATCH_SIZE,
    ) -> int:
        deleted_count = 0
        while True:
            result = await self._session.execute(
                select(
                    HttpBridgeRetryCircuit.session_key_kind,
                    HttpBridgeRetryCircuit.session_key_hash,
                    HttpBridgeRetryCircuit.api_key_scope,
                )
                .where(HttpBridgeRetryCircuit.updated_at_epoch < cutoff_epoch)
                .limit(batch_size)
            )
            keys = [tuple(row) for row in result.fetchall()]
            if not keys:
                return deleted_count
            batch_deleted_count = 0
            async with sqlite_writer_section():
                for session_key_kind, session_key_hash, api_key_scope in keys:
                    deleted = await self._session.execute(
                        delete(HttpBridgeRetryCircuit)
                        .where(HttpBridgeRetryCircuit.session_key_kind == session_key_kind)
                        .where(HttpBridgeRetryCircuit.session_key_hash == session_key_hash)
                        .where(HttpBridgeRetryCircuit.api_key_scope == api_key_scope)
                        .where(HttpBridgeRetryCircuit.updated_at_epoch < cutoff_epoch)
                        .returning(HttpBridgeRetryCircuit.session_key_hash)
                    )
                    batch_deleted_count += len(deleted.scalars().all())
                await self._session.commit()
            if batch_deleted_count == 0:
                return deleted_count
            deleted_count += batch_deleted_count

    async def upsert_alias(
        self,
        *,
        session_id: str,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
    ) -> None:
        async with sqlite_writer_section():
            await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=alias_value,
                api_key_scope=api_key_scope,
            )
            await self._session.commit()

    async def register_owned_alias(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        instance_id: str,
        owner_epoch: int,
        alias_kind: str,
        alias_value: str,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        latest_input_item_count: int | None = None,
        latest_input_full_fingerprint: str | None = None,
        latest_pending_tool_calls: Mapping[str, str] | None = None,
    ) -> DurableBridgeAliasRegistration:
        """Register continuity only while the caller still owns the durable row."""

        async with sqlite_writer_section():
            now = utcnow()
            session_values: dict[str, object] = {
                "lease_expires_at": now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
                "last_seen_at": now,
            }
            if latest_turn_state is not None:
                session_values["latest_turn_state"] = latest_turn_state
            if latest_response_id is not None:
                session_values["latest_response_id"] = latest_response_id
                session_values["latest_input_item_count"] = latest_input_item_count
                session_values["latest_input_full_fingerprint"] = latest_input_full_fingerprint
                session_values["latest_pending_tool_calls_json"] = _encode_pending_tool_calls(
                    latest_response_id,
                    latest_pending_tool_calls,
                )
            elif latest_input_item_count is not None and latest_input_full_fingerprint is not None:
                session_values["latest_input_item_count"] = latest_input_item_count
                session_values["latest_input_full_fingerprint"] = latest_input_full_fingerprint

            fenced_update = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(**session_values)
                .returning(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                )
            )
            target = fenced_update.one_or_none()
            if target is None:
                return DurableBridgeAliasRegistration.OWNER_FENCED

            registered = await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=alias_value,
                api_key_scope=api_key_scope,
                target_account_neutral_replay=is_http_bridge_account_neutral_replay(
                    kind=target.session_key_kind,
                    key=target.session_key_value,
                ),
            )
            if not registered:
                await self._session.rollback()
                return DurableBridgeAliasRegistration.ALIAS_PROTECTED
            await self._session.commit()
        return DurableBridgeAliasRegistration.REGISTERED

    async def register_reversible_turn_state_alias(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        instance_id: str,
        owner_epoch: int,
        turn_state: str,
        lease_ttl_seconds: float,
    ) -> DurableBridgeAliasRegistrationReceipt:
        """Publish a pre-dispatch turn alias with enough state for exact rollback."""

        alias_kind = "turn_state"
        async with sqlite_writer_section():
            now = utcnow()
            # The first UPDATE both fences ownership and acquires the target-row
            # lock (and SQLite's writer lock) before prior alias state is read.
            fenced_lock = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
                    last_seen_at=now,
                )
                .returning(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                    HttpBridgeSessionRecord.latest_turn_state,
                )
            )
            target = fenced_lock.one_or_none()
            if target is None:
                await self._session.rollback()
                return DurableBridgeAliasRegistrationReceipt(
                    status=DurableBridgeAliasRegistration.OWNER_FENCED,
                    session_id=session_id,
                    api_key_scope=api_key_scope,
                    alias_kind=alias_kind,
                    alias_value=turn_state,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    previous_alias_session_id=None,
                    previous_alias_owner_epoch=None,
                    previous_alias_account_id=None,
                    previous_latest_turn_state=None,
                )

            previous_latest_turn_state = target.latest_turn_state
            previous_alias_session_id = await self._session.scalar(
                select(HttpBridgeSessionAlias.session_id)
                .where(
                    HttpBridgeSessionAlias.alias_kind == alias_kind,
                    HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(turn_state),
                    HttpBridgeSessionAlias.alias_value == turn_state,
                    HttpBridgeSessionAlias.api_key_scope == api_key_scope,
                )
                .with_for_update()
            )
            previous_alias_owner_epoch = None
            previous_alias_account_id = None
            if previous_alias_session_id is not None:
                previous_alias_owner = (
                    await self._session.execute(
                        select(
                            HttpBridgeSessionRecord.owner_epoch,
                            HttpBridgeSessionRecord.account_id,
                        ).where(HttpBridgeSessionRecord.id == previous_alias_session_id)
                    )
                ).one_or_none()
                if previous_alias_owner is not None:
                    previous_alias_owner_epoch = previous_alias_owner.owner_epoch
                    previous_alias_account_id = previous_alias_owner.account_id
            await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(HttpBridgeSessionRecord.id == session_id)
                .values(latest_turn_state=turn_state)
            )
            registered = await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=turn_state,
                api_key_scope=api_key_scope,
                target_account_neutral_replay=is_http_bridge_account_neutral_replay(
                    kind=target.session_key_kind,
                    key=target.session_key_value,
                ),
            )
            if not registered:
                await self._session.rollback()
                return DurableBridgeAliasRegistrationReceipt(
                    status=DurableBridgeAliasRegistration.ALIAS_PROTECTED,
                    session_id=session_id,
                    api_key_scope=api_key_scope,
                    alias_kind=alias_kind,
                    alias_value=turn_state,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    previous_alias_session_id=previous_alias_session_id,
                    previous_alias_owner_epoch=previous_alias_owner_epoch,
                    previous_alias_account_id=previous_alias_account_id,
                    previous_latest_turn_state=previous_latest_turn_state,
                )
            await self._session.commit()

        return DurableBridgeAliasRegistrationReceipt(
            status=DurableBridgeAliasRegistration.REGISTERED,
            session_id=session_id,
            api_key_scope=api_key_scope,
            alias_kind=alias_kind,
            alias_value=turn_state,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            previous_alias_session_id=previous_alias_session_id,
            previous_alias_owner_epoch=previous_alias_owner_epoch,
            previous_alias_account_id=previous_alias_account_id,
            previous_latest_turn_state=previous_latest_turn_state,
        )

    async def rollback_reversible_turn_state_alias(
        self,
        *,
        receipt: DurableBridgeAliasRegistrationReceipt,
    ) -> bool:
        """Undo a registered pre-dispatch alias while the same owner is fenced in."""

        if receipt.status != DurableBridgeAliasRegistration.REGISTERED:
            return False

        async with sqlite_writer_section():
            previous_session_valid = False
            dialect = self._session.get_bind().dialect.name
            if dialect == "postgresql":
                session_ids = {receipt.session_id}
                if receipt.previous_alias_session_id is not None:
                    session_ids.add(receipt.previous_alias_session_id)
                locked_records = (
                    (
                        await self._session.execute(
                            select(HttpBridgeSessionRecord)
                            .where(HttpBridgeSessionRecord.id.in_(session_ids))
                            .order_by(HttpBridgeSessionRecord.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                records_by_id = {record.id: record for record in locked_records}
                target_record = records_by_id.get(receipt.session_id)
                if (
                    target_record is None
                    or target_record.api_key_scope != receipt.api_key_scope
                    or target_record.owner_instance_id != receipt.instance_id
                    or target_record.owner_epoch != receipt.owner_epoch
                ):
                    await self._session.rollback()
                    return False
                previous_record = (
                    records_by_id.get(receipt.previous_alias_session_id)
                    if receipt.previous_alias_session_id is not None
                    else None
                )
                previous_session_valid = previous_record is not None and (
                    previous_record.owner_epoch == receipt.previous_alias_owner_epoch
                    and previous_record.account_id == receipt.previous_alias_account_id
                )

            fenced_restore = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == receipt.session_id,
                    HttpBridgeSessionRecord.api_key_scope == receipt.api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == receipt.instance_id,
                    HttpBridgeSessionRecord.owner_epoch == receipt.owner_epoch,
                )
                .values(
                    latest_turn_state=case(
                        (
                            HttpBridgeSessionRecord.latest_turn_state == receipt.alias_value,
                            receipt.previous_latest_turn_state,
                        ),
                        else_=HttpBridgeSessionRecord.latest_turn_state,
                    )
                )
                .returning(HttpBridgeSessionRecord.id)
            )
            if fenced_restore.scalar_one_or_none() is None:
                await self._session.rollback()
                return False

            if dialect != "postgresql" and receipt.previous_alias_session_id is not None:
                previous_record = (
                    await self._session.execute(
                        select(
                            HttpBridgeSessionRecord.owner_epoch,
                            HttpBridgeSessionRecord.account_id,
                        ).where(HttpBridgeSessionRecord.id == receipt.previous_alias_session_id)
                    )
                ).one_or_none()
                previous_session_valid = previous_record is not None and (
                    previous_record.owner_epoch == receipt.previous_alias_owner_epoch
                    and previous_record.account_id == receipt.previous_alias_account_id
                )

            alias_predicate = (
                HttpBridgeSessionAlias.session_id == receipt.session_id,
                HttpBridgeSessionAlias.alias_kind == receipt.alias_kind,
                HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(receipt.alias_value),
                HttpBridgeSessionAlias.alias_value == receipt.alias_value,
                HttpBridgeSessionAlias.api_key_scope == receipt.api_key_scope,
            )
            current_alias_session_id = await self._session.scalar(
                select(HttpBridgeSessionAlias.session_id).where(*alias_predicate).with_for_update()
            )
            if current_alias_session_id == receipt.session_id:
                previous_session_id = receipt.previous_alias_session_id
                if previous_session_id is None:
                    await self._session.execute(delete(HttpBridgeSessionAlias).where(*alias_predicate))
                elif previous_session_id != receipt.session_id:
                    if not previous_session_valid:
                        await self._session.execute(delete(HttpBridgeSessionAlias).where(*alias_predicate))
                    else:
                        await self._session.execute(
                            update(HttpBridgeSessionAlias)
                            .where(*alias_predicate)
                            .values(session_id=previous_session_id, updated_at=utcnow())
                        )
            await self._session.commit()
        return True

    async def _execute_alias_upsert(
        self,
        *,
        session_id: str,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
        target_account_neutral_replay: bool | None = None,
    ) -> bool:
        dialect = self._session.get_bind().dialect.name
        now = utcnow()
        values = {
            "session_id": session_id,
            "alias_kind": alias_kind,
            "alias_value": alias_value,
            "alias_hash": durable_bridge_hash(alias_value),
            "api_key_scope": api_key_scope,
        }
        existing_target_is_account_neutral_replay = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind == HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
                HttpBridgeSessionRecord.session_key_value.like(f"{HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX}%"),
                HttpBridgeSessionRecord.session_key_value != HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
            )
        )
        existing_target_is_rebindable = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind.in_(HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS),
            )
        )
        existing_target_is_replaceable_recovery = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind == HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
                HttpBridgeSessionRecord.session_key_value.like(f"{HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX}%"),
                HttpBridgeSessionRecord.session_key_value != HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
                or_(
                    HttpBridgeSessionRecord.owner_instance_id.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at <= now,
                ),
            )
        )
        conflict_where = None
        if target_account_neutral_replay is True:
            conflict_where = or_(
                HttpBridgeSessionAlias.session_id == session_id,
                existing_target_is_replaceable_recovery,
                existing_target_is_rebindable,
            )
        elif target_account_neutral_replay is False:
            conflict_where = or_(
                HttpBridgeSessionAlias.session_id == session_id,
                ~existing_target_is_account_neutral_replay,
            )
        if dialect == "postgresql":
            statement = (
                pg_insert(HttpBridgeSessionAlias)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        HttpBridgeSessionAlias.alias_kind,
                        HttpBridgeSessionAlias.alias_hash,
                        HttpBridgeSessionAlias.api_key_scope,
                    ],
                    set_={
                        "session_id": session_id,
                        "alias_value": alias_value,
                        "updated_at": now,
                    },
                    where=conflict_where,
                )
                .returning(HttpBridgeSessionAlias.session_id)
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(HttpBridgeSessionAlias)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        HttpBridgeSessionAlias.alias_kind,
                        HttpBridgeSessionAlias.alias_hash,
                        HttpBridgeSessionAlias.api_key_scope,
                    ],
                    set_={
                        "session_id": session_id,
                        "alias_value": alias_value,
                        "updated_at": now,
                    },
                    where=conflict_where,
                )
                .returning(HttpBridgeSessionAlias.session_id)
            )
        else:
            raise RuntimeError(f"DurableBridgeRepository alias upsert unsupported for dialect={dialect!r}")
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def _clear_aliases_for_session(self, session_id: str) -> None:
        await self._session.execute(
            delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id == session_id)
        )


async def missing_durable_bridge_tables(session: AsyncSession) -> tuple[str, ...]:
    dialect = session.get_bind().dialect.name
    expected = set(REQUIRED_DURABLE_BRIDGE_TABLES)
    if dialect == "sqlite":
        result = await session.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name IN ('http_bridge_sessions', 'http_bridge_session_aliases', 'http_bridge_retry_circuits', "
                "'http_bridge_recovery_attempts', 'http_bridge_operations', 'http_bridge_operation_events', "
                "'http_bridge_operation_event_chunks')"
            )
        )
    else:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name IN ("
                "'http_bridge_sessions', 'http_bridge_session_aliases', 'http_bridge_retry_circuits', "
                "'http_bridge_recovery_attempts', 'http_bridge_operations', 'http_bridge_operation_events', "
                "'http_bridge_operation_event_chunks'"
                ")"
            )
        )
    present = {str(row[0]) for row in result.fetchall()}
    return tuple(sorted(expected - present))


_SNAPSHOT_COLUMNS = (
    HttpBridgeSessionRecord.id,
    HttpBridgeSessionRecord.session_key_kind,
    HttpBridgeSessionRecord.session_key_value,
    HttpBridgeSessionRecord.session_key_hash,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.owner_instance_id,
    HttpBridgeSessionRecord.owner_process_epoch,
    HttpBridgeSessionRecord.owner_epoch,
    HttpBridgeSessionRecord.lease_expires_at,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.account_id,
    HttpBridgeSessionRecord.model,
    HttpBridgeSessionRecord.service_tier,
    HttpBridgeSessionRecord.latest_turn_state,
    HttpBridgeSessionRecord.latest_response_id,
    HttpBridgeSessionRecord.latest_input_item_count,
    HttpBridgeSessionRecord.latest_input_full_fingerprint,
    HttpBridgeSessionRecord.latest_pending_tool_calls_json,
    HttpBridgeSessionRecord.last_seen_at,
    HttpBridgeSessionRecord.closed_at,
)


def _returned_row_to_snapshot(row: Row[tuple[object, ...]]) -> DurableBridgeSessionSnapshot:
    mapping = row._mapping
    return DurableBridgeSessionSnapshot(
        id=mapping[HttpBridgeSessionRecord.id],
        session_key_kind=mapping[HttpBridgeSessionRecord.session_key_kind],
        session_key_value=mapping[HttpBridgeSessionRecord.session_key_value],
        session_key_hash=mapping[HttpBridgeSessionRecord.session_key_hash],
        api_key_scope=mapping[HttpBridgeSessionRecord.api_key_scope],
        owner_instance_id=mapping[HttpBridgeSessionRecord.owner_instance_id],
        owner_process_epoch=mapping[HttpBridgeSessionRecord.owner_process_epoch],
        owner_epoch=mapping[HttpBridgeSessionRecord.owner_epoch],
        lease_expires_at=mapping[HttpBridgeSessionRecord.lease_expires_at],
        state=mapping[HttpBridgeSessionRecord.state],
        account_id=mapping[HttpBridgeSessionRecord.account_id],
        model=mapping[HttpBridgeSessionRecord.model],
        service_tier=mapping[HttpBridgeSessionRecord.service_tier],
        latest_turn_state=mapping[HttpBridgeSessionRecord.latest_turn_state],
        latest_response_id=mapping[HttpBridgeSessionRecord.latest_response_id],
        latest_input_item_count=mapping[HttpBridgeSessionRecord.latest_input_item_count],
        latest_input_full_fingerprint=mapping[HttpBridgeSessionRecord.latest_input_full_fingerprint],
        latest_pending_tool_calls=_decode_pending_tool_calls(
            mapping[HttpBridgeSessionRecord.latest_response_id],
            mapping[HttpBridgeSessionRecord.latest_pending_tool_calls_json],
        ),
        last_seen_at=mapping[HttpBridgeSessionRecord.last_seen_at],
        closed_at=mapping[HttpBridgeSessionRecord.closed_at],
    )


def _to_snapshot(row: HttpBridgeSessionRecord | None) -> DurableBridgeSessionSnapshot | None:
    if row is None:
        return None
    return DurableBridgeSessionSnapshot(
        id=row.id,
        session_key_kind=row.session_key_kind,
        session_key_value=row.session_key_value,
        session_key_hash=row.session_key_hash,
        api_key_scope=row.api_key_scope,
        owner_instance_id=row.owner_instance_id,
        owner_process_epoch=row.owner_process_epoch,
        owner_epoch=row.owner_epoch,
        lease_expires_at=row.lease_expires_at,
        state=row.state,
        account_id=row.account_id,
        model=row.model,
        service_tier=row.service_tier,
        latest_turn_state=row.latest_turn_state,
        latest_response_id=row.latest_response_id,
        latest_input_item_count=row.latest_input_item_count,
        latest_input_full_fingerprint=row.latest_input_full_fingerprint,
        latest_pending_tool_calls=_decode_pending_tool_calls(
            row.latest_response_id,
            row.latest_pending_tool_calls_json,
        ),
        last_seen_at=row.last_seen_at,
        closed_at=row.closed_at,
    )


def _to_snapshot_required(row: HttpBridgeSessionRecord) -> DurableBridgeSessionSnapshot:
    snapshot = _to_snapshot(row)
    if snapshot is None:
        raise RuntimeError("Expected durable bridge session snapshot")
    return snapshot


def _to_recovery_attempt_snapshot(
    row: HttpBridgeRecoveryAttemptRecord,
) -> DurableBridgeRecoveryAttemptSnapshot:
    return DurableBridgeRecoveryAttemptSnapshot(
        session_id=row.session_id,
        request_fingerprint=row.request_fingerprint,
        request_id=row.request_id,
        account_id=row.account_id,
        model=row.model,
        replay_safe=bool(row.replay_safe),
        state=row.state,
        response_id=row.response_id,
    )


def _to_operation_snapshot(
    row: HttpBridgeOperationRecord,
    *,
    created: bool = False,
    rebound: bool = False,
    rebound_from_session_id: str | None = None,
    rebound_from_account_id: str | None = None,
    rebound_from_model: str | None = None,
    rebound_from_parent_response_id: str | None = None,
) -> DurableBridgeOperationSnapshot:
    return DurableBridgeOperationSnapshot(
        operation_id=row.operation_id,
        session_id=row.session_id,
        request_fingerprint=row.request_fingerprint,
        account_id=row.account_id,
        model=row.model,
        parent_response_id=row.parent_response_id,
        state=row.state,
        response_id=row.response_id,
        recovery_dispatch_count=row.recovery_dispatch_count,
        request_text=row.request_text,
        event_spool_complete=bool(row.event_spool_complete),
        created=created,
        rebound=rebound,
        rebound_from_session_id=rebound_from_session_id,
        rebound_from_account_id=rebound_from_account_id,
        rebound_from_model=rebound_from_model,
        rebound_from_parent_response_id=rebound_from_parent_response_id,
    )


def _to_retry_circuit_snapshot(row: HttpBridgeRetryCircuit | None) -> DurableBridgeRetryCircuitSnapshot | None:
    if row is None:
        return None
    return DurableBridgeRetryCircuitSnapshot(
        session_key_kind=row.session_key_kind,
        session_key_hash=row.session_key_hash,
        api_key_scope=row.api_key_scope,
        consecutive_failures=row.consecutive_failures,
        cooldown_until_epoch=row.cooldown_until_epoch,
        last_detail=row.last_detail,
        updated_at_epoch=row.updated_at_epoch,
        admission_generation=row.admission_generation,
    )
