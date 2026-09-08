from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.utils.time import to_utc_naive
from app.db.models import HttpBridgeSessionState
from app.db.session import close_session
from app.modules.proxy.continuity import is_http_bridge_account_neutral_replay
from app.modules.proxy.durable_bridge_repository import (
    DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    REBIND_ANCHOR_UNFENCED,
    DurableBridgeAliasRegistration,
    DurableBridgeAliasRegistrationReceipt,
    DurableBridgeOperationAbandonment,
    DurableBridgeOperationAbandonmentScanCursor,
    DurableBridgeOperationEventInput,
    DurableBridgeOperationPurgeBatchResult,
    DurableBridgeOperationSnapshot,
    DurableBridgeRecoveryAttemptSnapshot,
    DurableBridgeRepository,
    DurableBridgeRetryCircuitSnapshot,
    DurableBridgeSessionSnapshot,
    DurableBridgeTranscriptTurn,
    durable_bridge_api_key_scope,
)

_DURABLE_TURN_STATE_ALIAS = "turn_state"
_DURABLE_PREVIOUS_RESPONSE_ALIAS = "previous_response_id"
_DURABLE_SESSION_HEADER_ALIAS = "session_header"


@dataclass(frozen=True, slots=True)
class DurableBridgeLookup:
    session_id: str
    canonical_kind: str
    canonical_key: str
    api_key_scope: str
    account_id: str | None
    owner_instance_id: str | None
    owner_epoch: int
    lease_expires_at: datetime | None
    state: HttpBridgeSessionState
    latest_turn_state: str | None
    latest_response_id: str | None
    latest_input_item_count: int | None = None
    latest_input_full_fingerprint: str | None = None
    model: str | None = None
    latest_pending_tool_calls: dict[str, str] | None = None
    owner_process_epoch: str | None = None

    def lease_is_active(self, *, now: datetime) -> bool:
        if self.owner_instance_id is None:
            return False
        if self.lease_expires_at is None:
            return False
        # lease_expires_at is a timestamptz column: PostgreSQL yields it
        # offset-aware while the app clock (utcnow) and SQLite yield naive
        # UTC. Normalize both sides — comparing them raw raises TypeError
        # on the anchored-lookup hot path.
        return to_utc_naive(self.lease_expires_at) > to_utc_naive(now)


def durable_bridge_snapshot_is_detached(snapshot: DurableBridgeSessionSnapshot) -> bool:
    """Return True for a row whose owner account was invalidated.

    Account deactivation, re-authentication demands, proxy-binding changes and
    deletion detach every durable bridge row of that account: the row is
    CLOSED, its owner account, lease and every continuity anchor are cleared,
    and its aliases are deleted. Such a row proves only that a conversation
    once existed; it names no account that could still preserve continuity,
    so it MUST NOT be treated as durable owner evidence. Reporting it as a
    lookup hit made every hard-affinity (thread/session header) continuation
    fail closed forever with ``previous_response_owner_unavailable`` even
    after the account was reactivated, while a fresh thread on the same
    client worked.

    A CLOSED row that still names its account (ordinary release) keeps its
    continuity value and is intentionally not covered here.
    """

    return (
        snapshot.account_id is None
        and snapshot.state == HttpBridgeSessionState.CLOSED
        and snapshot.owner_instance_id is None
        and snapshot.latest_turn_state is None
        and snapshot.latest_response_id is None
    )


class DurableBridgeSessionCoordinator:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory
        self._operation_abandonment_scan_cursor: DurableBridgeOperationAbandonmentScanCursor | None = None

    async def lookup_request_targets(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        turn_state: str | None,
        session_header: str | None,
        previous_response_id: str | None,
    ) -> DurableBridgeLookup | None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            resolved_aliases: list[tuple[str, DurableBridgeSessionSnapshot]] = []
            for alias_kind, alias_value in (
                (_DURABLE_TURN_STATE_ALIAS, turn_state),
                (_DURABLE_PREVIOUS_RESPONSE_ALIAS, previous_response_id),
                (_DURABLE_SESSION_HEADER_ALIAS, session_header),
            ):
                if alias_value is None:
                    continue
                snapshot = await repository.resolve_alias(
                    alias_kind=alias_kind,
                    alias_value=alias_value,
                    api_key_scope=api_key_scope,
                )
                if snapshot is not None and not durable_bridge_snapshot_is_detached(snapshot):
                    resolved_aliases.append((alias_kind, snapshot))
            resolved_identities = {(snapshot.id, snapshot.account_id) for _alias_kind, snapshot in resolved_aliases}
            resolved_account_ids = {
                snapshot.account_id for _alias_kind, snapshot in resolved_aliases if snapshot.account_id is not None
            }
            has_ownerless_snapshot = any(snapshot.account_id is None for _alias_kind, snapshot in resolved_aliases)
            if len(resolved_identities) > 1:
                same_account_handoff = len(resolved_account_ids) == 1 and not has_ownerless_snapshot
                if same_account_handoff:
                    account_id = next(iter(resolved_account_ids))
                    same_account_snapshots = [
                        snapshot for _alias_kind, snapshot in resolved_aliases if snapshot.account_id == account_id
                    ]
                    requested_response_snapshot = next(
                        (
                            snapshot
                            for alias_kind, snapshot in resolved_aliases
                            if alias_kind == _DURABLE_PREVIOUS_RESPONSE_ALIAS and snapshot.account_id == account_id
                        ),
                        None,
                    )
                    account_snapshot = requested_response_snapshot or max(
                        same_account_snapshots,
                        key=lambda snapshot: (
                            snapshot.latest_response_id is not None,
                            snapshot.last_seen_at,
                        ),
                    )
                    # Same-account aliases may point at different durable rows
                    # during a handoff. Preserve an explicitly requested
                    # response anchor; otherwise prefer the newest persisted
                    # response anchor rather than alias-resolution order.
                    return _to_lookup(account_snapshot)
                specific_aliases = [
                    (alias_kind, snapshot)
                    for alias_kind, snapshot in resolved_aliases
                    if alias_kind != _DURABLE_SESSION_HEADER_ALIAS
                ]
                specific_identities = {(snapshot.id, snapshot.account_id) for _alias_kind, snapshot in specific_aliases}
                if len(specific_identities) == 1:
                    specific_snapshot = specific_aliases[0][1]
                    specific_identity = (specific_snapshot.id, specific_snapshot.account_id)
                    conflicting_alias_kinds = {
                        alias_kind
                        for alias_kind, snapshot in resolved_aliases
                        if (snapshot.id, snapshot.account_id) != specific_identity
                    }
                    if is_http_bridge_account_neutral_replay(
                        kind=specific_snapshot.session_key_kind,
                        key=specific_snapshot.session_key_value,
                    ) and conflicting_alias_kinds == {_DURABLE_SESSION_HEADER_ALIAS}:
                        return _to_lookup(specific_snapshot)
                # Turn-state/response/session aliases are independent hard
                # evidence. Returning the first match would silently discard a
                # conflicting durable owner based on source ordering.
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "continuity_owner_conflict",
                        "Durable continuity aliases resolve to conflicting upstream owners.",
                        error_type="server_error",
                    ),
                )
            if resolved_aliases:
                return _to_lookup(resolved_aliases[0][1])
            snapshot = await repository.get_session(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=api_key_scope,
            )
            if snapshot is not None and durable_bridge_snapshot_is_detached(snapshot):
                # The canonical key still maps to a row the account
                # invalidation path detached. Let the request start fresh on
                # a selectable account (the later claim re-owns this row)
                # instead of failing closed on an owner that no longer exists.
                snapshot = None
            if snapshot is None:
                if turn_state is not None:
                    snapshot = await repository.find_session_by_latest_turn_state(
                        turn_state=turn_state,
                        api_key_scope=api_key_scope,
                    )
                if snapshot is None and previous_response_id is not None:
                    snapshot = await repository.find_session_by_latest_response_id(
                        response_id=previous_response_id,
                        api_key_scope=api_key_scope,
                    )
            if snapshot is None:
                return None
            return _to_lookup(snapshot)

    async def lookup_turn_state_target(
        self,
        *,
        turn_state: str,
        api_key_id: str | None,
    ) -> DurableBridgeLookup | None:
        """Resolve only a previously registered turn-state continuity anchor."""

        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            snapshot = await repository.resolve_alias(
                alias_kind=_DURABLE_TURN_STATE_ALIAS,
                alias_value=turn_state,
                api_key_scope=api_key_scope,
            )
            return _to_lookup(snapshot) if snapshot is not None else None

    async def lookup_sessions(self, *, session_ids: Sequence[str]) -> list[DurableBridgeLookup]:
        """Batch-load durable session snapshots for ownership reconciliation."""

        if not session_ids:
            return []
        async with self._session() as session:
            snapshots = await DurableBridgeRepository(session).get_sessions_by_ids(session_ids)
        return [_to_lookup(snapshot) for snapshot in snapshots]

    async def lookup_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
    ) -> DurableBridgeRetryCircuitSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_retry_circuit(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
            )

    async def persist_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        consecutive_failures: int,
        cooldown_until_epoch: float,
        last_detail: str | None,
        updated_at_epoch: float,
        base_updated_at_epoch: float = 0.0,
        failure_threshold: int = 1,
        poison_sticky_threshold: int | None = None,
        conflict_cooldown_until_epoch: float | None = None,
        base_backoff_seconds: float = 60.0,
        max_backoff_seconds: float = 600.0,
        clean_close_max_backoff_seconds: float = 30.0,
    ) -> DurableBridgeRetryCircuitSnapshot | None:
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            await repository.upsert_retry_circuit(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                consecutive_failures=consecutive_failures,
                cooldown_until_epoch=cooldown_until_epoch,
                last_detail=last_detail,
                updated_at_epoch=updated_at_epoch,
                base_updated_at_epoch=base_updated_at_epoch,
                failure_threshold=failure_threshold,
                poison_sticky_threshold=poison_sticky_threshold,
                conflict_cooldown_until_epoch=conflict_cooldown_until_epoch,
                base_backoff_seconds=base_backoff_seconds,
                max_backoff_seconds=max_backoff_seconds,
                clean_close_max_backoff_seconds=clean_close_max_backoff_seconds,
            )
            return await repository.get_retry_circuit(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
            )

    async def clear_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        expected_updated_at_epoch: float | None = None,
        expected_admission_generation: int | None = None,
        expected_consecutive_failures: int | None = None,
        reset_detail: str | None = None,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).delete_retry_circuit(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                expected_consecutive_failures=expected_consecutive_failures,
                expected_updated_at_epoch=expected_updated_at_epoch,
                expected_admission_generation=expected_admission_generation,
                reset_detail=reset_detail,
            )

    async def supersede_retry_circuit_detail(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        expected_updated_at_epoch: float,
        expected_consecutive_failures: int,
        expected_last_detail: str | None,
        last_detail: str | None,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).supersede_retry_circuit_detail(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                expected_updated_at_epoch=expected_updated_at_epoch,
                expected_consecutive_failures=expected_consecutive_failures,
                expected_last_detail=expected_last_detail,
                last_detail=last_detail,
            )

    async def claim_retry_circuit_generation(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        expected_updated_at_epoch: float | None,
        expected_admission_generation: int,
        expected_consecutive_failures: int,
        expected_cooldown_until_epoch: float,
    ) -> DurableBridgeRetryCircuitSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).claim_retry_circuit_generation(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                expected_updated_at_epoch=expected_updated_at_epoch,
                expected_admission_generation=expected_admission_generation,
                expected_consecutive_failures=expected_consecutive_failures,
                expected_cooldown_until_epoch=expected_cooldown_until_epoch,
            )

    async def purge_retry_circuit(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        expected_updated_at_epoch: float | None = None,
        expected_admission_generation: int | None = None,
        expected_consecutive_failures: int | None = None,
        fence_last_detail: bool = False,
        expected_last_detail: str | None = None,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).purge_retry_circuit(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                expected_updated_at_epoch=expected_updated_at_epoch,
                expected_admission_generation=expected_admission_generation,
                expected_consecutive_failures=expected_consecutive_failures,
                fence_last_detail=fence_last_detail,
                expected_last_detail=expected_last_detail,
            )

    async def claim_live_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
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
    ) -> DurableBridgeLookup:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).claim_session(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=api_key_scope,
                instance_id=instance_id,
                lease_ttl_seconds=lease_ttl_seconds,
                account_id=account_id,
                model=model,
                service_tier=service_tier,
                latest_turn_state=latest_turn_state,
                latest_response_id=latest_response_id,
                allow_takeover=allow_takeover,
                owner_process_epoch=owner_process_epoch,
                force_owner_epoch_advance=force_owner_epoch_advance,
            )
        return _to_lookup(snapshot)

    async def renew_live_session(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        latest_input_item_count: int | None = None,
        latest_input_full_fingerprint: str | None = None,
        latest_pending_tool_calls: Mapping[str, str] | None = None,
        state: HttpBridgeSessionState | None = None,
    ) -> DurableBridgeLookup | None:
        del api_key_id
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).renew_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_turn_state=latest_turn_state,
                latest_response_id=latest_response_id,
                latest_input_item_count=latest_input_item_count,
                latest_input_full_fingerprint=latest_input_full_fingerprint,
                latest_pending_tool_calls=latest_pending_tool_calls,
                state=state,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def rebind_session_account(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        account_id: str,
        clear_continuity: bool = False,
        expected_latest_response_id: object = REBIND_ANCHOR_UNFENCED,
        expected_latest_turn_state: object = REBIND_ANCHOR_UNFENCED,
    ) -> bool:
        del api_key_id
        async with self._session() as session:
            return await DurableBridgeRepository(session).rebind_session_account(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                account_id=account_id,
                clear_continuity=clear_continuity,
                expected_latest_response_id=expected_latest_response_id,
                expected_latest_turn_state=expected_latest_turn_state,
            )

    async def session_latest_continuity(self, *, session_id: str) -> tuple[str | None, str | None] | None:
        """Read the session's current continuity anchors for a fenced clear."""
        async with self._session() as session:
            return await DurableBridgeRepository(session).latest_session_continuity(session_id=session_id)

    async def release_live_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeLookup | None:
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).release_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                draining=draining,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def clear_live_session_response_anchor(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> DurableBridgeLookup | None:
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).clear_latest_response_anchor(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def clear_live_session_response_anchor_if_matches(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        response_id: str,
    ) -> DurableBridgeLookup | None:
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).clear_latest_response_anchor_if_matches(
                session_id=session_id,
                api_key_scope=durable_bridge_api_key_scope(api_key_id),
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                response_id=response_id,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def record_recovery_attempt(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
        request_id: str,
        account_id: str | None,
        model: str | None,
        replay_safe: bool,
    ) -> DurableBridgeRecoveryAttemptSnapshot | None:
        del api_key_id
        async with self._session() as session:
            return await DurableBridgeRepository(session).record_recovery_attempt(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
                request_id=request_id,
                account_id=account_id,
                model=model,
                replay_safe=replay_safe,
            )

    async def lookup_recovery_attempt(
        self,
        *,
        session_id: str,
        request_fingerprint: str,
    ) -> DurableBridgeRecoveryAttemptSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).lookup_recovery_attempt(
                session_id=session_id,
                request_fingerprint=request_fingerprint,
            )

    async def mark_recovery_attempt_replayed(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
        response_id: str | None = None,
    ) -> bool:
        del api_key_id
        async with self._session() as session:
            return await DurableBridgeRepository(session).mark_recovery_attempt_replayed(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
                response_id=response_id,
            )

    async def rollback_recovery_attempt_replayed(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
    ) -> bool:
        del api_key_id
        async with self._session() as session:
            return await DurableBridgeRepository(session).rollback_recovery_attempt_replayed(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
            )

    async def rollback_recovery_attempt_before_dispatch(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        request_fingerprint: str,
    ) -> bool:
        del api_key_id
        async with self._session() as session:
            return await DurableBridgeRepository(session).rollback_recovery_attempt_before_dispatch(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).record_operation(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
                api_key_scope=api_key_scope,
                account_id=account_id,
                model=model,
                parent_response_id=parent_response_id,
                request_text=request_text,
                recovery_attempt_session_id=recovery_attempt_session_id,
                recovery_attempt_owner_epoch=recovery_attempt_owner_epoch,
                recovery_attempt_fingerprint=recovery_attempt_fingerprint,
                recovery_attempt_consumed=recovery_attempt_consumed,
            )

    async def get_operation_events(self, *, operation_id: str) -> list[str]:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_operation_events(
                operation_id=operation_id,
                max_bytes=int(
                    getattr(
                        get_settings(), "http_responses_session_bridge_operation_event_spool_max_bytes", 2 * 1024 * 1024
                    )
                ),
            )

    async def abandon_stale_operations(
        self,
        *,
        cutoff: datetime,
        lease_expired_before: datetime,
        protected_operation_ids: Collection[str] = (),
        batch_size: int = 500,
    ) -> list[DurableBridgeOperationAbandonment]:
        async with self._session() as session:
            sweep = await DurableBridgeRepository(session).abandon_stale_operations(
                cutoff=cutoff,
                lease_expired_before=lease_expired_before,
                protected_operation_ids=protected_operation_ids,
                batch_size=batch_size,
                scan_cursor=self._operation_abandonment_scan_cursor,
            )
        self._operation_abandonment_scan_cursor = sweep.next_cursor
        return list(sweep.abandonments)

    async def get_replayable_transcript(
        self,
        *,
        response_id: str,
        max_turns: int = 128,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> list[DurableBridgeTranscriptTurn] | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_replayable_transcript(
                response_id=response_id,
                max_turns=max_turns,
                max_bytes=max_bytes,
            )

    async def purge_operation_spool(
        self,
        *,
        cutoff: datetime,
        batch_size: int = DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    ) -> int:
        return (await self.purge_operation_spool_batch(cutoff=cutoff, batch_size=batch_size)).deleted_operations

    async def purge_operation_spool_batch(
        self,
        *,
        cutoff: datetime,
        batch_size: int = DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    ) -> DurableBridgeOperationPurgeBatchResult:
        async with self._session() as session:
            return await DurableBridgeRepository(session).purge_operation_spool_batch(
                cutoff=cutoff,
                batch_size=batch_size,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).append_operation_event(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                max_bytes=max_bytes,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).append_terminal_operation_event(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                max_bytes=max_bytes,
                state=state,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                response_id=response_id,
            )

    async def append_operation_events(
        self,
        *,
        events: Sequence[DurableBridgeOperationEventInput],
        max_bytes: int,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).append_operation_events(
                events=events,
                max_bytes=max_bytes,
            )

    async def append_operation_event_chunk(
        self,
        *,
        events: Sequence[DurableBridgeOperationEventInput],
        max_bytes: int,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).append_operation_event_chunk(
                events=events,
                max_bytes=max_bytes,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).append_terminal_operation_chunk(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                event_text=event_text,
                max_bytes=max_bytes,
                state=state,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                response_id=response_id,
            )

    async def finalize_operation_event_spool(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).finalize_operation_event_spool(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).settle_terminal_append_failure(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                state=state,
                expected_response_id=expected_response_id,
                expected_recovery_dispatch_count=expected_recovery_dispatch_count,
                alternate_expected_response_id=alternate_expected_response_id,
                response_id=response_id,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).update_operation(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                state=state,
                response_id=response_id,
            )

    async def get_operation(self, *, operation_id: str) -> DurableBridgeOperationSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_operation(operation_id=operation_id)

    async def reset_operation_event_spool(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).reset_operation_event_spool(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
            )

    async def claim_unknown_operation_for_recovery(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        max_recovery_dispatches: int | None = None,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).claim_unknown_operation_for_recovery(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                max_recovery_dispatches=max_recovery_dispatches,
            )

    async def mark_operation_unknown(
        self,
        *,
        operation_id: str,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        restore_recovery_dispatch_claim: bool = False,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).mark_operation_unknown(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                restore_recovery_dispatch_claim=restore_recovery_dispatch_claim,
            )

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
        async with self._session() as session:
            return await DurableBridgeRepository(session).rollback_operation_before_dispatch(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                restore_rebound=restore_rebound,
                rebound_from_session_id=rebound_from_session_id,
                rebound_from_account_id=rebound_from_account_id,
                rebound_from_model=rebound_from_model,
                rebound_from_parent_response_id=rebound_from_parent_response_id,
            )

    async def get_operation_by_fingerprint(
        self,
        *,
        request_fingerprint: str,
        api_key_scope: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_operation_by_fingerprint(
                request_fingerprint=request_fingerprint,
                api_key_scope=api_key_scope,
            )

    async def get_latest_completed_operation(
        self,
        *,
        session_id: str,
        parent_response_id: str,
        request_fingerprint: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_latest_completed_operation(
                session_id=session_id,
                parent_response_id=parent_response_id,
                request_fingerprint=request_fingerprint,
            )

    async def get_latest_completed_operation_any_session(
        self,
        *,
        parent_response_id: str,
        api_key_scope: str | None = None,
        request_fingerprint: str | None = None,
    ) -> DurableBridgeOperationSnapshot | None:
        async with self._session() as session:
            return await DurableBridgeRepository(session).get_latest_completed_operation_any_session(
                parent_response_id=parent_response_id,
                api_key_scope=api_key_scope,
                request_fingerprint=request_fingerprint,
            )

    async def mark_instance_draining(self, *, instance_id: str) -> int:
        async with self._session() as session:
            return await DurableBridgeRepository(session).mark_owner_draining(instance_id=instance_id)

    async def purge_owned_sessions_on_startup(
        self,
        *,
        instance_id: str,
        owner_process_epoch: str | None = None,
        ownerless_cutoff: datetime | None = None,
    ) -> int:
        async with self._session() as session:
            return await DurableBridgeRepository(session).purge_owned_sessions_on_startup(
                instance_id=instance_id,
                owner_process_epoch=owner_process_epoch,
                ownerless_cutoff=ownerless_cutoff,
            )

    async def register_turn_state(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        turn_state: str,
        lease_ttl_seconds: float,
    ) -> DurableBridgeAliasRegistration:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            return await DurableBridgeRepository(session).register_owned_alias(
                session_id=session_id,
                api_key_scope=api_key_scope,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                alias_kind=_DURABLE_TURN_STATE_ALIAS,
                alias_value=turn_state,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_turn_state=turn_state,
            )

    async def register_recovery_turn_state(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        turn_state: str,
        lease_ttl_seconds: float,
    ) -> DurableBridgeAliasRegistrationReceipt:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            return await DurableBridgeRepository(session).register_reversible_turn_state_alias(
                session_id=session_id,
                api_key_scope=api_key_scope,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                turn_state=turn_state,
                lease_ttl_seconds=lease_ttl_seconds,
            )

    async def rollback_recovery_turn_state_registration(
        self,
        *,
        receipt: DurableBridgeAliasRegistrationReceipt,
    ) -> bool:
        async with self._session() as session:
            return await DurableBridgeRepository(session).rollback_reversible_turn_state_alias(
                receipt=receipt,
            )

    async def register_previous_response_id(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        response_id: str,
        lease_ttl_seconds: float,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
        pending_tool_calls: Mapping[str, str] | None = None,
    ) -> DurableBridgeAliasRegistration:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            return await DurableBridgeRepository(session).register_owned_alias(
                session_id=session_id,
                api_key_scope=api_key_scope,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                alias_kind=_DURABLE_PREVIOUS_RESPONSE_ALIAS,
                alias_value=response_id,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_response_id=response_id,
                latest_input_item_count=input_item_count,
                latest_input_full_fingerprint=input_full_fingerprint,
                latest_pending_tool_calls=pending_tool_calls,
            )

    async def register_session_header(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        session_header: str,
    ) -> None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            await DurableBridgeRepository(session).upsert_alias(
                session_id=session_id,
                alias_kind=_DURABLE_SESSION_HEADER_ALIAS,
                alias_value=session_header,
                api_key_scope=api_key_scope,
            )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
        finally:
            await close_session(session)


def _to_lookup(snapshot: DurableBridgeSessionSnapshot) -> DurableBridgeLookup:
    return DurableBridgeLookup(
        session_id=snapshot.id,
        canonical_kind=snapshot.session_key_kind,
        canonical_key=snapshot.session_key_value,
        api_key_scope=snapshot.api_key_scope,
        account_id=snapshot.account_id,
        owner_instance_id=snapshot.owner_instance_id,
        owner_process_epoch=snapshot.owner_process_epoch,
        owner_epoch=snapshot.owner_epoch,
        lease_expires_at=snapshot.lease_expires_at,
        state=snapshot.state,
        latest_turn_state=snapshot.latest_turn_state,
        latest_response_id=snapshot.latest_response_id,
        latest_input_item_count=snapshot.latest_input_item_count,
        latest_input_full_fingerprint=snapshot.latest_input_full_fingerprint,
        model=snapshot.model,
        latest_pending_tool_calls=snapshot.latest_pending_tool_calls,
    )
