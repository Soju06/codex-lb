from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import anyio

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, http_bridge_retry_circuit_total
from app.modules.proxy._service.http_bridge.quarantine import (
    _HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
    _quarantine_http_bridge_session,
)
from app.modules.proxy._service.observability import _hash_identifier
from app.modules.proxy._service.support import (
    _HTTPBridgeResponseCreateAttempt,
    _HTTPBridgeRetryCircuitAttemptSelection,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
)
from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

logger = logging.getLogger(__name__)

_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD = 2
_HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS = 60.0
_HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS = 600.0
_HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS = 30.0
_HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS = 600.0
_HTTP_BRIDGE_RETRY_CIRCUIT_CLAIM_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_DETAILS = frozenset(
    {
        "stream_incomplete",
        "clean_close",
        "stream_idle_timeout",
    }
)
_HTTP_BRIDGE_RETRY_CIRCUIT_DETAIL_ALIASES = {
    # These diagnostics describe the same ambiguous idle/incomplete
    # transport class. Keep the durable contract to the three documented
    # failure classes while retaining the more specific event in logs.
    "upstream_keepalive_timeout": "stream_idle_timeout",
    "missing_response_created_timeout": "stream_idle_timeout",
    "response_create_gate_timeout_stuck_pending": "stream_idle_timeout",
}
_HTTP_BRIDGE_ANCHOR_POISON_DETAILS = {
    "stream_idle_timeout": "repeated_zero_event_idle_timeout",
    "stream_incomplete": "repeated_zero_event_stream_incomplete",
}


def _http_bridge_anchor_poison_detail(detail: str | None) -> str | None:
    """Map an eventless retry-circuit failure class to its anchor-poison detail.

    Consecutive eventless failures on one bridge key are same-anchor failures:
    the durable anchor only advances on a completed response, which resets the
    circuit. Both ambiguous transport classes therefore count toward anchor
    poison (issue #1830); ``clean_close`` never does.
    """
    if detail is None:
        return None
    aliased = _HTTP_BRIDGE_RETRY_CIRCUIT_DETAIL_ALIASES.get(detail, detail)
    return _HTTP_BRIDGE_ANCHOR_POISON_DETAILS.get(aliased)


def _http_bridge_effective_anchor_poison_threshold(configured: int) -> int:
    """Cap the configured anchor-poison threshold at the circuit's own threshold.

    Once the circuit opens it refuses the key for 60-600s per strike, so a
    higher poison threshold cannot be reached at any useful rate; that
    unreachability is issue #1830/#1852 itself. Capping it here also makes the
    decision replica-safe. The quarantine armed at the opening is process-local,
    so between the circuit threshold and a higher configured one the durable
    anchor would survive for another worker to plan. Clearing no later than the
    opening is what stops that. A configured value below the circuit threshold
    is still honoured, since clearing earlier is always safe.
    """
    return max(1, min(configured, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD))


def _http_bridge_poison_quarantine_minimum_seconds(cooldown_remaining: float) -> float:
    """Keep a poison quarantine alive across the cooldown and its probe window.

    The probe that has to be planned unanchored is only admitted once the
    cooldown expires, and it may then be admitted anywhere inside the
    half-open lease that follows. Quarantine's default TTL equals the maximum
    cooldown, so at that cooldown both would lapse in the same instant and the
    probe would be planned with the anchor the circuit opened on. This is the
    same span the durable retry-circuit row already reserves for itself.
    """
    return max(0.0, cooldown_remaining) + _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS


@dataclass(slots=True)
class _HTTPBridgeRetryCircuitState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_detail: str | None = None
    last_touched_monotonic: float = 0.0
    persisted_updated_at_epoch: float = 0.0
    last_failure_monotonic: float = 0.0
    last_durable_load_monotonic: float = 0.0
    half_open_until: float = 0.0


def _initialize_http_bridge_retry_circuit(service: Any, reset_transient_cache: Any = None) -> None:
    if reset_transient_cache is not None:
        reset_transient_cache()
    service._http_bridge_retry_circuits = {}
    service._http_bridge_retry_circuit_loaded_keys = set()
    service._http_bridge_retry_circuit_persisted_keys = set()
    service._http_bridge_retry_circuit_lock = anyio.Lock()


def _record_http_bridge_retry_circuit_duplicate_suppressed(
    session: _HTTPBridgeSession,
    *,
    attempt: _HTTPBridgeResponseCreateAttempt,
    consecutive_failures: int,
    detail: str,
) -> None:
    if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
        http_bridge_retry_circuit_total.labels(outcome="duplicate_suppressed").inc()
    logger.info(
        "http_bridge_retry_circuit event=duplicate_suppressed bridge_kind=%s bridge_key=%s "
        "failures=%s detail=%s attempt=%s",
        session.key.affinity_kind,
        _hash_identifier(session.key.affinity_key),
        consecutive_failures,
        detail,
        attempt.ordinal,
    )


class _HTTPBridgeRetryCircuitMixin:
    async def _http_bridge_retry_circuit_generation(
        self: Any,
        session: _HTTPBridgeSession,
    ) -> tuple[bool, tuple[int, float, int, float, int, float, float] | None]:
        return await self._http_bridge_retry_circuit_generation_for_key(session.key)

    async def _http_bridge_retry_circuit_generation_for_key(
        self: Any,
        key: _HTTPBridgeSessionKey,
    ) -> tuple[bool, tuple[int, float, int, float, int, float, float] | None]:
        try:
            persisted = await self._durable_bridge.lookup_retry_circuit(
                session_key_kind=key.affinity_kind,
                session_key_value=key.affinity_key,
                api_key_id=key.api_key_id,
            )
        except Exception:
            logger.warning(
                "Failed to inspect HTTP bridge retry circuit generation bridge_kind=%s bridge_key=%s",
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                exc_info=True,
            )
            return False, None
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(key)
            if state is None and persisted is None:
                return True, None
            persisted_updated_at_epoch = max(
                state.persisted_updated_at_epoch if state is not None else 0.0,
                persisted.updated_at_epoch if persisted is not None else 0.0,
            )
            persisted_consecutive_failures = persisted.consecutive_failures if persisted is not None else 0
            durable_cooldown_until_epoch = persisted.cooldown_until_epoch if persisted is not None else 0.0
            local_consecutive_failures = state.consecutive_failures if state is not None else 0
            last_failure_monotonic = state.last_failure_monotonic if state is not None else 0.0
            local_cooldown_until = state.cooldown_until if state is not None else 0.0
            return True, (
                getattr(persisted, "admission_generation", 0) if persisted is not None else 0,
                persisted_updated_at_epoch,
                persisted_consecutive_failures,
                durable_cooldown_until_epoch,
                local_consecutive_failures,
                last_failure_monotonic,
                local_cooldown_until,
            )

    async def _http_bridge_retry_circuit_generation_is_not_newer(
        self: Any,
        *,
        key: _HTTPBridgeSessionKey,
        captured: bool,
        generation: tuple[int, float, int, float, int, float, float] | None,
    ) -> bool:
        if not captured:
            return False
        load_succeeded, current_generation = await self._http_bridge_retry_circuit_generation_for_key(key)
        if not load_succeeded:
            return False
        if generation is None:
            return current_generation is None
        if current_generation is None:
            return True
        return all(
            current <= captured_value for current, captured_value in zip(current_generation, generation, strict=True)
        )

    async def _claim_http_bridge_retry_circuit_generation(
        self: Any,
        *,
        key: _HTTPBridgeSessionKey,
        captured: bool,
        generation: tuple[int, float, int, float, int, float, float] | None,
    ) -> bool:
        """Atomically linearize replay admission against the captured circuit."""
        if not captured:
            return False
        expected_admission_generation = generation[0] if generation is not None else 0
        expected_persisted_updated_at = generation[1] if generation is not None else 0.0
        expected_persisted_failures = generation[2] if generation is not None else 0
        expected_persisted_cooldown = generation[3] if generation is not None else 0.0
        expected_local_failures = generation[4] if generation is not None else 0
        expected_last_failure = generation[5] if generation is not None else 0.0
        expected_local_cooldown = generation[6] if generation is not None else 0.0
        claim_generation = getattr(self._durable_bridge, "claim_retry_circuit_generation", None)
        if not callable(claim_generation):
            return False

        # Keep local failure recording behind the same lock until the durable
        # CAS commits. Cross-replica failures serialize at the row CAS; local
        # failures serialize here before they can mutate their in-memory base.
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(key)
            if state is not None and (
                state.consecutive_failures > expected_local_failures
                or state.last_failure_monotonic > expected_last_failure
                or state.cooldown_until > expected_local_cooldown
            ):
                return False
            try:
                claimed = await asyncio.wait_for(
                    claim_generation(
                        session_key_kind=key.affinity_kind,
                        session_key_value=key.affinity_key,
                        api_key_id=key.api_key_id,
                        expected_updated_at_epoch=(
                            expected_persisted_updated_at if expected_persisted_updated_at > 0 else None
                        ),
                        expected_admission_generation=expected_admission_generation,
                        expected_consecutive_failures=expected_persisted_failures,
                        expected_cooldown_until_epoch=expected_persisted_cooldown,
                    ),
                    timeout=_HTTP_BRIDGE_RETRY_CIRCUIT_CLAIM_TIMEOUT_SECONDS,
                )
            except Exception:
                logger.warning(
                    "Failed to claim HTTP bridge retry circuit generation bridge_kind=%s bridge_key=%s",
                    key.affinity_kind,
                    _hash_identifier(key.affinity_key),
                    exc_info=True,
                )
                return False
            if claimed is None:
                return False
            self._http_bridge_retry_circuit_loaded_keys.add(key)
            self._http_bridge_retry_circuit_persisted_keys.add(key)
            return True

    async def _http_bridge_retry_circuit_current_count(self: Any, session: _HTTPBridgeSession) -> int:
        async with self._http_bridge_retry_circuit_lock:
            current_state = self._http_bridge_retry_circuits.get(session.key)
            return current_state.consecutive_failures if current_state is not None else 0

    async def _await_http_bridge_retry_circuit_attempt_settlement(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        attempt: _HTTPBridgeResponseCreateAttempt,
        detail: str,
    ) -> int:
        settled = attempt.retry_circuit_failure_settled
        if settled is not None:
            await settled.wait()
        consecutive_failures = await self._http_bridge_retry_circuit_current_count(session)
        _record_http_bridge_retry_circuit_duplicate_suppressed(
            session,
            attempt=attempt,
            consecutive_failures=consecutive_failures,
            detail=detail,
        )
        return consecutive_failures

    async def _record_http_bridge_retry_circuit_failure_for_attempt_selection(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        detail: str,
        selection: _HTTPBridgeRetryCircuitAttemptSelection,
    ) -> int | None:
        attempt = selection.attempt
        if attempt is not None:
            return await self._record_http_bridge_retry_circuit_failure(
                session,
                detail=detail,
                attempt=attempt,
            )
        if selection.kind == "absent":
            return await self._record_http_bridge_retry_circuit_failure(session, detail=detail)
        if selection.kind == "recorded":
            for recorded_attempt in selection.attempts:
                settled = recorded_attempt.retry_circuit_failure_settled
                if settled is not None:
                    await settled.wait()
            consecutive_failures = await self._http_bridge_retry_circuit_current_count(session)
            for recorded_attempt in selection.attempts:
                _record_http_bridge_retry_circuit_duplicate_suppressed(
                    session,
                    attempt=recorded_attempt,
                    consecutive_failures=consecutive_failures,
                    detail=detail,
                )
            return consecutive_failures

        outcome = "ambiguous_suppressed" if selection.ambiguous else "ineligible_suppressed"
        if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
            http_bridge_retry_circuit_total.labels(outcome=outcome).inc()
        logger.info(
            "http_bridge_retry_circuit event=%s bridge_kind=%s bridge_key=%s detail=%s candidate_attempts=%s",
            outcome,
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            detail,
            len(selection.attempts),
        )
        return None

    def _prune_http_bridge_retry_circuit_state(self: Any, now: float) -> None:
        expiry = now - DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
        for key, state in list(self._http_bridge_retry_circuits.items()):
            if state.last_touched_monotonic > expiry:
                continue
            self._http_bridge_retry_circuits.pop(key, None)
            self._http_bridge_retry_circuit_loaded_keys.discard(key)
            self._http_bridge_retry_circuit_persisted_keys.discard(key)

    async def _load_http_bridge_retry_circuit(self: Any, session: _HTTPBridgeSession) -> bool:
        if session.key.strength != "hard":
            return True

        now_monotonic = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            self._prune_http_bridge_retry_circuit_state(now_monotonic)
            local_state = self._http_bridge_retry_circuits.get(session.key)
            if local_state is not None:
                local_state.last_touched_monotonic = now_monotonic
        try:
            persisted = await self._durable_bridge.lookup_retry_circuit(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
            )
        except Exception:
            if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                http_bridge_retry_circuit_total.labels(outcome="lookup_failed").inc()
            logger.warning(
                "Failed to load persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )
            return False

        if persisted is None:
            # A durable miss clears state loaded from another replica, but it
            # must not discard a failure recorded locally after the last
            # durable read. That local circuit is the only protection against
            # immediately replaying the same failing upstream request.
            async with self._http_bridge_retry_circuit_lock:
                local_state = self._http_bridge_retry_circuits.get(session.key)
                locally_updated = bool(
                    local_state is not None
                    and local_state.last_failure_monotonic > local_state.last_durable_load_monotonic
                )
                if session.key in self._http_bridge_retry_circuit_persisted_keys and not locally_updated:
                    self._http_bridge_retry_circuits.pop(session.key, None)
                    self._http_bridge_retry_circuit_loaded_keys.discard(session.key)
                    self._http_bridge_retry_circuit_persisted_keys.discard(session.key)
            return True

        now_epoch = time.time()
        if now_epoch - persisted.updated_at_epoch > DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS:
            async with self._http_bridge_retry_circuit_lock:
                stale_local_state = self._http_bridge_retry_circuits.get(session.key)
            try:
                await self._durable_bridge.purge_retry_circuit(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    expected_updated_at_epoch=persisted.updated_at_epoch,
                )
            except Exception:
                logger.warning(
                    "Failed to remove stale HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    exc_info=True,
                )
                # Keep a newer process-local circuit when persistence is
                # unavailable. The next failure can still open the local
                # circuit even though the expired durable row remains.
                return False
            async with self._http_bridge_retry_circuit_lock:
                current_local_state = self._http_bridge_retry_circuits.get(session.key)
                local_state_is_newer = bool(
                    current_local_state is not None
                    and current_local_state.last_failure_monotonic > current_local_state.last_durable_load_monotonic
                )
                if current_local_state is None or (
                    current_local_state is stale_local_state and not local_state_is_newer
                ):
                    self._http_bridge_retry_circuits.pop(session.key, None)
                    self._http_bridge_retry_circuit_loaded_keys.discard(session.key)
                    self._http_bridge_retry_circuit_persisted_keys.discard(session.key)
            return True

        cooldown_remaining = max(0.0, persisted.cooldown_until_epoch - now_epoch)
        persisted_cooldown_until = now_monotonic + cooldown_remaining
        async with self._http_bridge_retry_circuit_lock:
            self._http_bridge_retry_circuit_persisted_keys.add(session.key)
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None:
                state = _HTTPBridgeRetryCircuitState(last_touched_monotonic=now_monotonic)
                self._http_bridge_retry_circuits[session.key] = state
            local_failure_is_newer = state.last_failure_monotonic > state.last_durable_load_monotonic
            if persisted.updated_at_epoch > state.persisted_updated_at_epoch and not local_failure_is_newer:
                state.consecutive_failures = max(0, persisted.consecutive_failures)
                state.cooldown_until = persisted_cooldown_until
                state.last_detail = persisted.last_detail
            else:
                state.consecutive_failures = max(state.consecutive_failures, max(0, persisted.consecutive_failures))
                state.cooldown_until = max(state.cooldown_until, persisted_cooldown_until)
                if local_failure_is_newer:
                    state.last_detail = state.last_detail or persisted.last_detail
                else:
                    state.last_detail = persisted.last_detail or state.last_detail
            state.persisted_updated_at_epoch = max(state.persisted_updated_at_epoch, persisted.updated_at_epoch)
            state.last_touched_monotonic = now_monotonic
            state.last_durable_load_monotonic = now_monotonic
            self._http_bridge_retry_circuit_loaded_keys.add(session.key)
        return True

    async def _persist_http_bridge_retry_circuit(
        self: Any,
        session: _HTTPBridgeSession,
        state: _HTTPBridgeRetryCircuitState,
    ) -> None:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        async with self._http_bridge_retry_circuit_lock:
            if self._http_bridge_retry_circuits.get(session.key) is not state:
                return
            consecutive_failures = state.consecutive_failures
            cooldown_until = state.cooldown_until
            last_detail = state.last_detail
            persisted_updated_at_epoch = state.persisted_updated_at_epoch
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        if last_detail == "clean_close":
            base_backoff = min(
                base_backoff,
                max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS),
            )
        try:
            persisted = await self._durable_bridge.persist_retry_circuit(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
                consecutive_failures=consecutive_failures,
                cooldown_until_epoch=now_wall + max(0.0, cooldown_until - now_monotonic),
                last_detail=last_detail,
                updated_at_epoch=now_wall,
                base_updated_at_epoch=persisted_updated_at_epoch,
                failure_threshold=threshold,
                conflict_cooldown_until_epoch=now_wall + base_backoff,
                base_backoff_seconds=max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS),
                max_backoff_seconds=max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS),
                clean_close_max_backoff_seconds=max(
                    0.001,
                    _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS,
                ),
            )
            if persisted is not None:
                persisted_cooldown_until = now_monotonic + max(0.0, persisted.cooldown_until_epoch - now_wall)
                async with self._http_bridge_retry_circuit_lock:
                    current = self._http_bridge_retry_circuits.get(session.key)
                    if current is state:
                        local_failure_is_newer = state.last_failure_monotonic > state.last_durable_load_monotonic
                        if persisted.updated_at_epoch > state.persisted_updated_at_epoch and not local_failure_is_newer:
                            state.consecutive_failures = max(0, persisted.consecutive_failures)
                            state.cooldown_until = persisted_cooldown_until
                            state.last_detail = persisted.last_detail
                        else:
                            state.consecutive_failures = max(state.consecutive_failures, persisted.consecutive_failures)
                            state.cooldown_until = max(state.cooldown_until, persisted_cooldown_until)
                            if local_failure_is_newer:
                                state.last_detail = state.last_detail or persisted.last_detail
                            else:
                                state.last_detail = persisted.last_detail or state.last_detail
                        state.persisted_updated_at_epoch = max(
                            state.persisted_updated_at_epoch,
                            persisted.updated_at_epoch,
                        )
                        # This write is now the durable baseline for the
                        # captured local failure. A failure recorded while
                        # the write was in flight still has a later
                        # monotonic timestamp and will remain dominant.
                        state.last_durable_load_monotonic = max(
                            state.last_durable_load_monotonic,
                            now_monotonic,
                        )
            async with self._http_bridge_retry_circuit_lock:
                if self._http_bridge_retry_circuits.get(session.key) is state:
                    self._http_bridge_retry_circuit_persisted_keys.add(session.key)
        except Exception:
            if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                http_bridge_retry_circuit_total.labels(outcome="persist_failed").inc()
            logger.warning(
                "Failed to persist HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )

    async def _http_bridge_precreated_retry_allowed(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        allow_fresh_hard_account_switch: bool = False,
        allow_proof_gated_continuity_replay: bool = False,
        allow_operation_fenced_continuity_replay: bool = False,
    ) -> bool:
        """Avoid replaying a repeatedly failing hard-affinity request in a tight loop."""
        if session.key.strength != "hard":
            return True

        await self._load_http_bridge_retry_circuit(session)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None or state.cooldown_until <= now:
                if (
                    state is not None
                    and state.consecutive_failures >= _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD
                    and state.half_open_until > now
                    and not allow_fresh_hard_account_switch
                    and not allow_proof_gated_continuity_replay
                ):
                    if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                        http_bridge_retry_circuit_total.labels(outcome="suppressed").inc()
                    return False
                if state is not None and state.cooldown_until > 0:
                    state.cooldown_until = 0.0
                    state.half_open_until = now + _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS
                    logger.info(
                        "http_bridge_retry_circuit event=half_open bridge_kind=%s bridge_key=%s failures=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        state.consecutive_failures,
                    )
                return True

            retry_after = max(0.0, state.cooldown_until - now)
            if allow_fresh_hard_account_switch:
                logger.info(
                    "http_bridge_retry_circuit event=bypass_fresh_account_switch bridge_kind=%s "
                    "bridge_key=%s failures=%s retry_after_seconds=%.1f",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    state.consecutive_failures,
                    retry_after,
                )
                return True
            if allow_proof_gated_continuity_replay:
                logger.info(
                    "http_bridge_retry_circuit event=bypass_proof_gated_continuity_replay bridge_kind=%s "
                    "bridge_key=%s failures=%s retry_after_seconds=%.1f",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    state.consecutive_failures,
                    retry_after,
                )
                return True
            if allow_operation_fenced_continuity_replay:
                logger.info(
                    "http_bridge_retry_circuit event=bypass_operation_fenced_continuity_replay bridge_kind=%s "
                    "bridge_key=%s failures=%s retry_after_seconds=%.1f",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    state.consecutive_failures,
                    retry_after,
                )
                return True
            if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                http_bridge_retry_circuit_total.labels(outcome="suppressed").inc()
            logger.info(
                "http_bridge_retry_circuit event=suppressed bridge_kind=%s bridge_key=%s "
                "failures=%s retry_after_seconds=%.1f detail=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                state.consecutive_failures,
                retry_after,
                state.last_detail,
            )
            return False

    async def _http_bridge_precreated_retry_block(
        self: Any,
        session: _HTTPBridgeSession,
    ) -> tuple[float, str]:
        """Return ``(seconds_blocked, reason)`` for a suppressed submission.

        The cooldown is not always what is refusing the request: once it has
        expired, the half-open lease keeps refusing everything except the one
        admitted probe. Reporting the cooldown in that window advertises a
        retry-after of ~1s while the caller is barred for the rest of the
        lease, which turns a wedged key into a client retry storm.
        """
        if session.key.strength != "hard":
            return 0.0, "none"

        await self._load_http_bridge_retry_circuit(session)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None:
                return 0.0, "none"
            cooldown_remaining = max(0.0, state.cooldown_until - now)
            half_open_remaining = (
                max(0.0, state.half_open_until - now)
                if state.consecutive_failures >= _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD
                else 0.0
            )
        if half_open_remaining > cooldown_remaining:
            return half_open_remaining, "hard_key_half_open"
        return cooldown_remaining, "hard_key_cooldown"

    async def _http_bridge_precreated_retry_cooldown_seconds(self: Any, session: _HTTPBridgeSession) -> float:
        if session.key.strength != "hard":
            return 0.0

        await self._load_http_bridge_retry_circuit(session)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None:
                return 0.0
            return max(0.0, state.cooldown_until - now)

    async def _http_bridge_retry_circuit_cooldown_seconds_for_key(
        self: Any,
        key: _HTTPBridgeSessionKey,
    ) -> float:
        """Return the source-key cooldown used to suppress a replacement."""
        load_succeeded, generation = await self._http_bridge_retry_circuit_generation_for_key(key)
        if not load_succeeded or generation is None:
            return 0.0
        return max(
            0.0,
            generation[3] - time.time(),
            generation[6] - time.monotonic(),
        )

    async def _record_http_bridge_retry_circuit_failure(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        detail: str,
        attempt: _HTTPBridgeResponseCreateAttempt | None = None,
        terminal_pre_response_frame: bool = False,
    ) -> int | None:
        """Count one hard-key failure against the retry circuit.

        ``terminal_pre_response_frame`` is asserted only by the settlement path
        for an upstream terminal frame that failed the request before any
        response event. ``response.failed``/``response.incomplete`` mark the
        attempt observed without counting a response event, so without that
        assertion a native terminal envelope is rejected below as already
        settled and never consumes a strike. The eventless retirement funnel
        never asserts it, so its "upstream answered" guard is unchanged.
        """
        detail = _HTTP_BRIDGE_RETRY_CIRCUIT_DETAIL_ALIASES.get(detail, detail)
        if session.key.strength != "hard" or detail not in _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_DETAILS:
            return None

        scoped_attempt = attempt
        if scoped_attempt is not None:
            if scoped_attempt.retry_circuit_failure_recorded:
                return await self._await_http_bridge_retry_circuit_attempt_settlement(
                    session,
                    attempt=scoped_attempt,
                    detail=detail,
                )
            if scoped_attempt.disarmed or (scoped_attempt.response_observed and not terminal_pre_response_frame):
                return None

        await self._load_http_bridge_retry_circuit(session)
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        max_backoff = max(base_backoff, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS)
        clean_close_max_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS)
        now = time.monotonic()
        duplicate_attempt: _HTTPBridgeResponseCreateAttempt | None = None
        state: _HTTPBridgeRetryCircuitState | None = None
        poison_class_failure = _http_bridge_anchor_poison_detail(detail) is not None
        quarantine_poisoned_anchor = False
        quarantine_cooldown_remaining = 0.0
        async with self._http_bridge_retry_circuit_lock:
            if scoped_attempt is not None and scoped_attempt.retry_circuit_failure_recorded:
                duplicate_attempt = scoped_attempt
            elif scoped_attempt is not None and (
                scoped_attempt.disarmed or (scoped_attempt.response_observed and not terminal_pre_response_frame)
            ):
                return None
            else:
                state = self._http_bridge_retry_circuits.setdefault(
                    session.key,
                    _HTTPBridgeRetryCircuitState(last_touched_monotonic=now),
                )
                state.last_touched_monotonic = now
                state.last_failure_monotonic = now
                state.half_open_until = 0.0
                if scoped_attempt is not None:
                    scoped_attempt.retry_circuit_failure_recorded = True
                    scoped_attempt.retry_circuit_failure_settled = anyio.Event()
                state.consecutive_failures += 1
                state.last_detail = detail
                if state.consecutive_failures >= threshold:
                    backoff = min(
                        max_backoff,
                        base_backoff * (2 ** min(state.consecutive_failures - threshold, 30)),
                    )
                    if detail == "clean_close":
                        backoff = min(backoff, clean_close_max_backoff)
                    state.cooldown_until = max(state.cooldown_until, now + backoff)
                    # The probe admitted after this cooldown is planned before
                    # it reaches the gate, so an anchor the circuit opened on
                    # has to be suppressed at planning time. Quarantining the
                    # key routes a full-resend probe through the existing
                    # unanchored fresh path; delta-only payloads keep their
                    # anchor there, because it is their only context.
                    quarantine_poisoned_anchor = poison_class_failure
                    quarantine_cooldown_remaining = max(0.0, state.cooldown_until - now)
                    if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                        http_bridge_retry_circuit_total.labels(outcome="opened").inc()
                    logger.warning(
                        "http_bridge_retry_circuit event=opened bridge_kind=%s bridge_key=%s "
                        "failures=%s cooldown_seconds=%.1f detail=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        state.consecutive_failures,
                        backoff,
                        detail,
                    )
        if duplicate_attempt is not None:
            return await self._await_http_bridge_retry_circuit_attempt_settlement(
                session,
                attempt=duplicate_attempt,
                detail=detail,
            )
        assert state is not None
        if quarantine_poisoned_anchor:
            _quarantine_http_bridge_session(
                self,
                session,
                reason=_HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
                minimum_seconds=_http_bridge_poison_quarantine_minimum_seconds(quarantine_cooldown_remaining),
            )
        try:
            await self._persist_http_bridge_retry_circuit(session, state)
            merged_cooldown_remaining = 0.0
            merged_poison_opened = False
            async with self._http_bridge_retry_circuit_lock:
                if self._http_bridge_retry_circuits.get(session.key) is state:
                    self._http_bridge_retry_circuit_loaded_keys.add(session.key)
                consecutive_failures = state.consecutive_failures
                # Replicas that each record their locally-first failure stay
                # below the threshold under their own lock, and it is the
                # durable merge that opens the circuit. Neither worker ever saw
                # an open circuit above, so the quarantine decision has to be
                # revisited against the merged state or the probe admitted
                # after this cooldown is planned with the poisoned anchor.
                #
                # The merged cooldown can already have elapsed — another replica
                # may have opened the circuit long enough ago that its deadline
                # is in the past by the time this write returns. That key is at
                # its threshold with no cooldown left, so the very next request
                # is the half-open probe: it needs the quarantine most, not
                # least. Track the opening itself, and let a zero remainder fall
                # through to the bare half-open lease.
                #
                # This deliberately does not skip keys quarantined from the
                # local opening. A local open arms the floor from its own
                # backoff, which can be 60s, and the merge can then replace
                # `cooldown_until` with a deadline up to 600s out. Re-arming is
                # idempotent because the entry keeps the later of the two
                # deadlines, so recomputing against the merged cooldown can only
                # extend a floor that would otherwise expire mid-cooldown.
                merged_poison_opened = poison_class_failure and consecutive_failures >= threshold
                if merged_poison_opened:
                    merged_cooldown_remaining = max(0.0, state.cooldown_until - time.monotonic())
            if merged_poison_opened:
                _quarantine_http_bridge_session(
                    self,
                    session,
                    reason=_HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
                    minimum_seconds=_http_bridge_poison_quarantine_minimum_seconds(merged_cooldown_remaining),
                )
            return consecutive_failures
        finally:
            if scoped_attempt is not None and scoped_attempt.retry_circuit_failure_settled is not None:
                scoped_attempt.retry_circuit_failure_settled.set()

    async def _clear_http_bridge_retry_circuit(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        settle_unfenced: bool = False,
    ) -> None:
        """Settle a hard key's retry circuit.

        ``settle_unfenced`` is asserted by callers that have removed the cause
        of the circuit rather than merely outlived it. The version fence below
        exists so a worker that observed nothing cannot clobber a row another
        replica just wrote, but it also skips the durable delete whenever this
        worker's own state has not been persisted yet. A circuit opened and
        remediated in the same instant is exactly that case: the row survives,
        the next load rehydrates it, and the key keeps cooling for a cause that
        is gone.
        """
        if session.key.strength != "hard":
            return

        durable_load_succeeded = await self._load_http_bridge_retry_circuit(session)
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.pop(session.key, None)
            self._http_bridge_retry_circuit_loaded_keys.discard(session.key)
            self._http_bridge_retry_circuit_persisted_keys.discard(session.key)
            expected_updated_at_epoch = (
                state.persisted_updated_at_epoch if state is not None and state.persisted_updated_at_epoch > 0 else None
            )
        # A confirmed miss has no version fence to protect a row created
        # concurrently, so leave the durable row untouched when no state was
        # observed. Preserve the existing best-effort clear on read failures,
        # which is still useful for settling a row after a transient outage.
        if durable_load_succeeded and (state is None or (expected_updated_at_epoch is None and not settle_unfenced)):
            return
        try:
            # Clearing is idempotent and must be attempted even when the
            # preceding lookup failed; a successful request should settle
            # a previously persisted circuit after a transient read error.
            await self._durable_bridge.clear_retry_circuit(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
                expected_updated_at_epoch=expected_updated_at_epoch,
            )
        except Exception:
            logger.warning(
                "Failed to clear persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )
        if state is None:
            return
        if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
            http_bridge_retry_circuit_total.labels(outcome="reset").inc()
        logger.info(
            "http_bridge_retry_circuit event=reset bridge_kind=%s bridge_key=%s failures=%s",
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            state.consecutive_failures,
        )
