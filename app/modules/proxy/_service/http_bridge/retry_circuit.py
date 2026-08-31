from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import anyio

from app.core.errors import HTTP_BRIDGE_EVENTLESS_TIMEOUT_CODE
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, http_bridge_retry_circuit_total
from app.modules.proxy._service.http_bridge.quarantine import (
    _HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
    _http_bridge_quarantine_clear_fence,
    _http_bridge_quarantine_generation,
    _http_bridge_quarantine_registry,
    _http_bridge_session_key_poison_quarantined,
    _quarantine_http_bridge_session,
    _revoke_http_bridge_poison_quarantine,
)
from app.modules.proxy._service.observability import _hash_identifier, _service_get_settings
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
_HTTP_BRIDGE_RETRY_CIRCUIT_PLANNING_MISS_CAP = 4096
_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_DETAILS = frozenset(
    {
        "stream_incomplete",
        "clean_close",
        "stream_idle_timeout",
        # Pre-response-start bridge silence. Deliberately *not* aliased onto
        # stream_idle_timeout: the whole point is that a circuit opened before
        # any response existed must be distinguishable from one opened by a
        # started stream going quiet.
        HTTP_BRIDGE_EVENTLESS_TIMEOUT_CODE,
    }
)
_HTTP_BRIDGE_RETRY_CIRCUIT_DETAIL_ALIASES = {
    # These diagnostics describe the same ambiguous idle/incomplete
    # transport class. Keep the durable contract to the documented
    # failure classes while retaining the more specific event in logs.
    "upstream_keepalive_timeout": "stream_idle_timeout",
    "missing_response_created_timeout": "stream_idle_timeout",
    "response_create_gate_timeout_stuck_pending": "stream_idle_timeout",
}
# Sentinel: the consult could not capture the durable anchor; the
# abandonment then clears continuity unfenced (degraded, logged).
_POISON_ANCHOR_CAPTURE_UNAVAILABLE: object = object()
_HTTP_BRIDGE_ANCHOR_POISON_DETAILS = {
    "stream_idle_timeout": "repeated_zero_event_idle_timeout",
    "stream_incomplete": "repeated_zero_event_stream_incomplete",
    # The pre-response eventless timeout keeps its distinguishable durable
    # detail (deliberately not aliased onto stream_idle_timeout), but a
    # repeatedly eventless anchored request is the original silent-wedge
    # signature, so at threshold it authorizes the same fenced abandonment.
    HTTP_BRIDGE_EVENTLESS_TIMEOUT_CODE: "repeated_zero_event_idle_timeout",
}
# Written over a surviving at-threshold row when a completion registered a
# fresh anchor but its settlement failed. Deliberately outside the poison
# detail map: a replica loading the row keeps the cooldown but neither arms
# quarantine against the fresh anchor nor authorizes an abandonment for
# failures recorded against the superseded one.
_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL = "anchor_superseded"
# Written onto the zeroed row when an ABANDONMENT settles the circuit: the
# continuity columns are already empty, so without a durable marker a
# restarted worker or another replica would dispatch a delta-only follow-up
# as a brand-new conversation and silently drop its context. A completion's
# settle writes None instead, erasing the tombstone on real recovery.
_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL = "anchor_abandoned"


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


def _http_bridge_retry_circuit_suppression_message(block_reason: str, retry_after_seconds: int) -> str:
    """Describe the timer that is actually refusing a suppressed submission.

    Naming the cooldown while the half-open lease is what refuses the request
    tells the client to come back in about a second when it is barred for the
    rest of the lease, which turns one wedged key into a retry storm.
    """
    if block_reason == "hard_key_half_open":
        return (
            "HTTP responses session bridge is probing recovery for this conversation; "
            f"retry after {retry_after_seconds}s."
        )
    return (
        "HTTP responses session bridge is recovering from repeated upstream failures; "
        f"retry after {retry_after_seconds}s."
    )


@dataclass(slots=True)
class _HTTPBridgeRetryCircuitState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_detail: str | None = None
    last_touched_monotonic: float = 0.0
    persisted_updated_at_epoch: float = 0.0
    # Admission generation observed with the epoch above: a replica's replay
    # claim bumps only this column, so the settle fence must carry it or a
    # reset can clear the circuit beneath a claim it never observed.
    persisted_admission_generation: int = 0
    last_failure_monotonic: float = 0.0
    last_durable_load_monotonic: float = 0.0
    half_open_until: float = 0.0
    # One poisoned anchor is abandoned once. Capping the poison threshold at
    # the circuit threshold makes every later strike in the same episode meet
    # it too, so without this marker each one re-issues the durable clear. A
    # failed clear leaves it unset, which is what lets the next strike retry.
    poison_anchor_cleared: bool = False
    # The poison class this episode still owes an abandonment for. A later
    # non-poison strike (an unanchored probe closing clean) overwrites
    # ``last_detail`` durably, and without this record the owed clear would
    # never be retried and other replicas would stop arming against it.
    owed_poison_detail: str | None = None


@dataclass(slots=True)
class _HTTPBridgeRetryCircuitKeyProbe:
    """Key-scoped stand-in for the planning-time first-touch load.

    Planning runs before any bridge session exists, but the load path and the
    quarantine arm only need the key plus logging context; the probe carries
    exactly that so an at-threshold poison row recorded by another replica can
    arm this worker's quarantine before the anchor is planned into a payload.
    """

    key: _HTTPBridgeSessionKey
    request_model: str | None = None
    account: Any = None
    quarantined: bool = False

    def __post_init__(self) -> None:
        if self.account is None:
            self.account = SimpleNamespace(id=None)


def _initialize_http_bridge_retry_circuit(service: Any, reset_transient_cache: Any = None) -> None:
    if reset_transient_cache is not None:
        reset_transient_cache()
    service._http_bridge_retry_circuits = {}
    service._http_bridge_retry_circuit_loaded_keys = set()
    service._http_bridge_retry_circuit_persisted_keys = set()
    service._http_bridge_retry_circuit_lock = anyio.Lock()
    service._http_bridge_retry_circuit_key_locks = {}
    # A settlement pops the state object, taking its stale-load watermark
    # with it; this per-key watermark survives the pop so a lookup that
    # began before the settlement cannot adopt the pre-settlement row into
    # a fresh state and resurrect the settled cooldown.
    service._http_bridge_retry_circuit_reconcile_watermarks = {}
    # Timestamped negative cache for confirmed durable misses: healthy hard
    # keys have no circuit row, and without this every planning pass pays a
    # durable read on top of the submit-time load. Entries live for the
    # planning window only; a row another replica creates after a cached
    # miss is still enforced at submission while its cooldown runs.
    service._http_bridge_retry_circuit_planning_misses = {}


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
    ) -> bool | None:
        """Atomically linearize replay admission against the captured circuit.

        ``True`` means claimed; ``False`` means a confirmed loss — the durable
        CAS answered and matched nothing, or local state already advanced past
        the capture; ``None`` means the claim could not be decided (timeout,
        durable error, or the capability being absent), which is
        infrastructure trouble rather than evidence that a remote probe holds
        the lease.
        """
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
            return None

        # Local failure recording for this key serializes on the key lock,
        # so holding it across the durable CAS keeps the claim linearized
        # against local strikes without parking every unrelated hard key
        # behind the global registry lock for up to the claim timeout. The
        # global lock is held only for the short state checks.
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(key)
        try:
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
                return None
            if claimed is None:
                return False
            async with self._http_bridge_retry_circuit_lock:
                self._http_bridge_retry_circuit_loaded_keys.add(key)
                self._http_bridge_retry_circuit_persisted_keys.add(key)
            return True
        finally:
            key_lock.release()

    async def _ensure_http_bridge_retry_circuit_loaded_for_key(
        self: Any,
        key: _HTTPBridgeSessionKey,
        *,
        request_model: str | None,
    ) -> None:
        """Planning-time durable load so a poison row arms quarantine pre-planning.

        The submit-time load runs after the payload is already planned and
        serialized, so on an expired at-threshold poison key the probe would
        carry the poisoned anchor. The cached view is honored only while it
        is younger than the minimum cooldown: a circuit another replica
        opens after this worker's last load is then either still cooling
        (the submit-time gate suppresses the request before dispatch) or
        refreshed here before its expired cooldown can admit a probe, so a
        cached below-threshold or reset row cannot hide a remote opening
        from the anchor decision.
        """
        if key.strength != "hard":
            return
        async with self._http_bridge_retry_circuit_lock:
            if key in self._http_bridge_retry_circuit_loaded_keys:
                state = self._http_bridge_retry_circuits.get(key)
                if (
                    state is not None
                    and time.monotonic() - state.last_durable_load_monotonic
                    <= _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS
                ):
                    return
            missed_at = self._http_bridge_retry_circuit_planning_misses.get(key)
            if (
                missed_at is not None
                and time.monotonic() - missed_at <= _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS
            ):
                # A confirmed durable miss within the planning window: a row
                # another replica created since is still enforced by the
                # submit-time load while its cooldown runs, so planning does
                # not need a second round trip per healthy request.
                return
        probe: Any = _HTTPBridgeRetryCircuitKeyProbe(key=key, request_model=request_model)
        await self._load_http_bridge_retry_circuit(probe)

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
        for key, watermark in list(self._http_bridge_retry_circuit_reconcile_watermarks.items()):
            if watermark <= expiry:
                self._http_bridge_retry_circuit_reconcile_watermarks.pop(key, None)
        miss_expiry = now - _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS
        misses = self._http_bridge_retry_circuit_planning_misses
        # Entries are kept in insertion order (a re-record pops first), so
        # expiry only ever sweeps the front. A full-map scan here would run
        # under the shared circuit lock on every load and cost R²·window
        # entry checks per second at R new healthy keys per second.
        while misses:
            oldest_key = next(iter(misses))
            if misses[oldest_key] > miss_expiry:
                break
            del misses[oldest_key]
        while len(misses) > _HTTP_BRIDGE_RETRY_CIRCUIT_PLANNING_MISS_CAP:
            # The cap sheds the oldest confirmed misses first; an evicted
            # key merely pays one planning-time load again.
            del misses[next(iter(misses))]
        for key, key_lock in list(self._http_bridge_retry_circuit_key_locks.items()):
            if key not in self._http_bridge_retry_circuits and not key_lock.locked():
                self._http_bridge_retry_circuit_key_locks.pop(key, None)

    async def _acquire_http_bridge_retry_circuit_key_lock(
        self: Any,
        key: _HTTPBridgeSessionKey,
    ) -> asyncio.Lock:
        """Serialize durable circuit writes and settles for one key.

        A strike write and a settle for the same key must not interleave
        across their durable awaits: a settle landing under an in-flight
        write resurrected the row, and a stale write landing after a settle
        merged a finished episode's strike into whatever episode owned the
        row next. The lock is per key, so unrelated keys never wait on each
        other's durable I/O. The acquire loop re-checks registration because
        pruning may drop an idle lock between the fetch and the acquire.
        """
        while True:
            async with self._http_bridge_retry_circuit_lock:
                key_lock = self._http_bridge_retry_circuit_key_locks.setdefault(key, asyncio.Lock())
            await key_lock.acquire()
            # Ownership transfers to the caller only on return. The
            # registration re-check is itself a cancellable await; a
            # cancellation landing there would otherwise leave the key lock
            # held forever, wedging every later persist and settle for the
            # key.
            try:
                async with self._http_bridge_retry_circuit_lock:
                    if self._http_bridge_retry_circuit_key_locks.get(key) is key_lock:
                        return key_lock
            except BaseException:
                key_lock.release()
                raise
            key_lock.release()

    async def _load_http_bridge_retry_circuit(self: Any, session: _HTTPBridgeSession) -> bool:
        key = session.key
        if key.strength != "hard":
            return True

        now_monotonic = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            self._prune_http_bridge_retry_circuit_state(now_monotonic)
            local_state = self._http_bridge_retry_circuits.get(key)
            if local_state is not None:
                local_state.last_touched_monotonic = now_monotonic
        try:
            persisted = await self._durable_bridge.lookup_retry_circuit(
                session_key_kind=key.affinity_kind,
                session_key_value=key.affinity_key,
                api_key_id=key.api_key_id,
            )
        except Exception:
            if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                http_bridge_retry_circuit_total.labels(outcome="lookup_failed").inc()
            logger.warning(
                "Failed to load persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                exc_info=True,
            )
            return False

        if persisted is None:
            # A durable miss clears state loaded from another replica, but it
            # must not discard a failure recorded locally after the last
            # durable read. That local circuit is the only protection against
            # immediately replaying the same failing upstream request.
            async with self._http_bridge_retry_circuit_lock:
                local_state = self._http_bridge_retry_circuits.get(key)
                key_watermark = self._http_bridge_retry_circuit_reconcile_watermarks.get(key, 0.0)
                if now_monotonic < key_watermark or (
                    local_state is not None and now_monotonic < local_state.last_durable_load_monotonic
                ):
                    # This lookup began before a same-key write completed:
                    # its miss predates the row that write created, and
                    # popping the episode here would let the admission
                    # decision that follows bypass the active cooldown the
                    # completed write just opened.
                    return True
                locally_updated = bool(
                    local_state is not None
                    and local_state.last_failure_monotonic > local_state.last_durable_load_monotonic
                )
                # A confirmed miss past the stale-lookup guards is durable
                # knowledge worth caching for the planning window; stamped
                # with the load's start so it expires conservatively, and
                # re-inserted so the map stays in insertion order for the
                # front-only expiry sweep.
                self._http_bridge_retry_circuit_planning_misses.pop(key, None)
                self._http_bridge_retry_circuit_planning_misses[key] = now_monotonic
                while (
                    len(self._http_bridge_retry_circuit_planning_misses) > _HTTP_BRIDGE_RETRY_CIRCUIT_PLANNING_MISS_CAP
                ):
                    # The pre-lookup prune ran before this load's durable
                    # await, so a burst of concurrent first-touch misses can
                    # land past it; the cap is enforced where entries are
                    # added.
                    del self._http_bridge_retry_circuit_planning_misses[
                        next(iter(self._http_bridge_retry_circuit_planning_misses))
                    ]
                if key in self._http_bridge_retry_circuit_persisted_keys and not locally_updated:
                    self._http_bridge_retry_circuits.pop(key, None)
                    self._http_bridge_retry_circuit_loaded_keys.discard(key)
                    self._http_bridge_retry_circuit_persisted_keys.discard(key)
                    if _http_bridge_session_key_poison_quarantined(self, key):
                        # The durable episode this quarantine fenced is gone
                        # — settled or purged by another replica — and a
                        # remotely recovered key must not stay suppressed
                        # for the stale deadline.
                        _revoke_http_bridge_poison_quarantine(
                            self,
                            key,
                            generation=_http_bridge_quarantine_clear_fence(self, key),
                        )
            return True

        now_epoch = time.time()
        row_age = now_epoch - persisted.updated_at_epoch
        if (
            row_age > DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
            # An abandonment tombstone guards continuity that outlives the
            # circuit TTL: purging it here would let the unanchored-delta
            # gate dispatch a context-free request. Its lifetime belongs to
            # the scheduled purge's bridge-retention cutoff.
            and persisted.last_detail != _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
            and (
                getattr(persisted, "admission_generation", 0) == 0
                or row_age > 2 * DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
            )
        ):
            async with self._http_bridge_retry_circuit_lock:
                stale_local_state = self._http_bridge_retry_circuits.get(key)
            try:
                stale_row_purged = await self._durable_bridge.purge_retry_circuit(
                    session_key_kind=key.affinity_kind,
                    session_key_value=key.affinity_key,
                    api_key_id=key.api_key_id,
                    expected_updated_at_epoch=persisted.updated_at_epoch,
                    expected_admission_generation=getattr(persisted, "admission_generation", 0),
                    # Detail-only transitions (the transitional tombstone
                    # supersede) and lagging-clock merges move neither the
                    # epoch nor the admission generation; fencing on the
                    # observed count and detail keeps this purge from
                    # deleting a row another replica just rewrote.
                    expected_consecutive_failures=persisted.consecutive_failures,
                    fence_last_detail=True,
                    expected_last_detail=persisted.last_detail,
                )
            except Exception:
                logger.warning(
                    "Failed to remove stale HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                    key.affinity_kind,
                    _hash_identifier(key.affinity_key),
                    exc_info=True,
                )
                # Keep a newer process-local circuit when persistence is
                # unavailable. The next failure can still open the local
                # circuit even though the expired durable row remains.
                return False
            if stale_row_purged is False:
                # The fenced purge matched nothing: another replica moved
                # the row after this worker's lookup — a fresh strike or
                # opening — and proceeding as though it was deleted would
                # pop the local circuit, revoke the quarantine, and let the
                # very next admission dispatch with the poisoned anchor.
                # Reconcile against the surviving row instead.
                try:
                    refreshed = await self._durable_bridge.lookup_retry_circuit(
                        session_key_kind=key.affinity_kind,
                        session_key_value=key.affinity_key,
                        api_key_id=key.api_key_id,
                    )
                except Exception:
                    logger.warning(
                        "Failed to reload HTTP bridge retry circuit after purge miss bridge_kind=%s bridge_key=%s",
                        key.affinity_kind,
                        _hash_identifier(key.affinity_key),
                        exc_info=True,
                    )
                    return False
                if refreshed is not None and (
                    # A detail-only tombstone rewrite keeps the old epoch, so
                    # an age check would reject the very fence the missed
                    # purge just proved exists — and planning would inject
                    # the dead anchor the tombstone guards. Tombstones are
                    # adopted regardless of circuit age; their lifetime
                    # belongs to the bridge-retention purge.
                    refreshed.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
                    or now_epoch - refreshed.updated_at_epoch
                    <= (
                        DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
                        * (2 if getattr(refreshed, "admission_generation", 0) > 0 else 1)
                    )
                ):
                    persisted = refreshed
                else:
                    # The surviving row vanished again or is itself stale:
                    # nothing current to adopt, and nothing was deleted this
                    # worker can vouch for.
                    return True
            if stale_row_purged is not False:
                async with self._http_bridge_retry_circuit_lock:
                    current_local_state = self._http_bridge_retry_circuits.get(key)
                    local_state_is_newer = bool(
                        current_local_state is not None
                        and current_local_state.last_failure_monotonic > current_local_state.last_durable_load_monotonic
                    )
                    if current_local_state is None or (
                        current_local_state is stale_local_state and not local_state_is_newer
                    ):
                        self._http_bridge_retry_circuits.pop(key, None)
                        self._http_bridge_retry_circuit_loaded_keys.discard(key)
                        self._http_bridge_retry_circuit_persisted_keys.discard(key)
                        if _http_bridge_session_key_poison_quarantined(self, key):
                            # The expired row just purged is the episode this
                            # quarantine fenced; it ended long ago.
                            _revoke_http_bridge_poison_quarantine(
                                self,
                                key,
                                generation=_http_bridge_quarantine_clear_fence(self, key),
                            )
                return True

        cooldown_remaining = max(0.0, persisted.cooldown_until_epoch - now_epoch)
        persisted_cooldown_until = now_monotonic + cooldown_remaining
        arm_poison_quarantine = False
        poison_cooldown_remaining = 0.0
        async with self._http_bridge_retry_circuit_lock:
            self._http_bridge_retry_circuit_persisted_keys.add(key)
            self._http_bridge_retry_circuit_planning_misses.pop(key, None)
            state = self._http_bridge_retry_circuits.get(key)
            if state is None:
                state = _HTTPBridgeRetryCircuitState(last_touched_monotonic=now_monotonic)
                self._http_bridge_retry_circuits[key] = state
            key_watermark = self._http_bridge_retry_circuit_reconcile_watermarks.get(key, 0.0)
            if now_monotonic < state.last_durable_load_monotonic or now_monotonic < key_watermark:
                # This load's lookup began before a same-key strike or
                # settlement completed its durable write, so its row snapshot
                # may predate that write: adopting it would erase a
                # just-opened cooldown or resurrect a just-settled episode.
                # The completed operation already reconciled the state from
                # its own returned row (or popped it at settlement, which
                # the per-key watermark records past the pop); an older
                # snapshot has nothing newer to add.
                self._http_bridge_retry_circuit_loaded_keys.add(key)
                return True
            local_failure_is_newer = state.last_failure_monotonic > state.last_durable_load_monotonic
            # A foreign write is identified by ANY observed column moving,
            # not the timestamp alone: a lagging-clock strike merges through
            # greatest() without moving the epoch while incrementing the
            # count and rewriting the detail, and an anchor supersession
            # rewrites the detail in place by design. Epoch-only detection
            # let a marker survive a new failure against the advanced
            # anchor and left an active quarantine unextended.
            episode_replaced = (
                persisted.updated_at_epoch != state.persisted_updated_at_epoch
                or persisted.consecutive_failures != state.consecutive_failures
                or persisted.last_detail != state.last_detail
            )
            if not local_failure_is_newer:
                # No local strike is waiting on its durable write, so the row
                # is strictly newer knowledge and is adopted wholesale.
                # Adoption compares no wall clocks: a reset stamped by a
                # lagging replica clock still replaces the local episode
                # instead of leaving a settled key suppressed for the rest
                # of its stale cooldown with a base that no longer exists.
                if episode_replaced:
                    # A write this worker did not produce: either the same
                    # episode struck elsewhere (then a set marker means the
                    # anchor is already cleared and the empty-continuity
                    # consult refuses regardless), or the episode was
                    # replaced — possibly at an equal or higher count — and
                    # the replacement's newly poisoned anchor must be
                    # allowed its one abandonment. Resetting on lineage
                    # rather than count ordering covers the equal-count
                    # replacement the count check below cannot see.
                    state.poison_anchor_cleared = False
                    # The local debt dies with every foreign write: counts
                    # and epochs cannot distinguish same-lineage advancement
                    # from a reset-and-overtaken replacement, and guessing
                    # wrong clears a replacement's fresh continuity. The
                    # durable row itself carries the cross-replica debt —
                    # an at-threshold poison detail is sticky against
                    # non-poison strikes in the merge — so the arm below
                    # re-derives the debt from the adopted row.
                    state.owed_poison_detail = None
                if persisted.consecutive_failures < state.consecutive_failures:
                    # The row does not carry this worker's failures: the
                    # lineage was reset, purged, or replaced, and the marker
                    # belonged to the ended episode.
                    state.poison_anchor_cleared = False
                    state.owed_poison_detail = None
                state.consecutive_failures = max(0, persisted.consecutive_failures)
                state.cooldown_until = persisted_cooldown_until
                state.last_detail = persisted.last_detail
                if state.consecutive_failures == 0:
                    # A zero-failure row is a durable reset: the episode the
                    # marker belonged to is over, and the next poison episode
                    # on this state object must be allowed its one
                    # abandonment.
                    state.poison_anchor_cleared = False
                    state.owed_poison_detail = None
            else:
                # A local strike sits between its record and its durable
                # write; keep it dominant here and let that write's own
                # merge reconcile clock-free against the returned row.
                state.consecutive_failures = max(state.consecutive_failures, max(0, persisted.consecutive_failures))
                state.cooldown_until = max(state.cooldown_until, persisted_cooldown_until)
                state.last_detail = state.last_detail or persisted.last_detail
            if state.cooldown_until > now_monotonic:
                # A cooling key is not probing. Without this, a merged
                # persisted cooldown leaves a leftover half-open lease
                # armed, and once the cooldown expires the admission gate
                # reads that stale lease as an in-flight probe and keeps
                # suppressing the key for the rest of the lease.
                state.half_open_until = 0.0
            elif episode_replaced and not local_failure_is_newer:
                # The adopted row belongs to a replacement episode whose
                # cooldown has already elapsed. A lease left over from the
                # ended episode would read as this worker's in-flight probe
                # for the new one and suppress the key for the rest of a
                # window it never opened.
                state.half_open_until = 0.0
            state.persisted_updated_at_epoch = persisted.updated_at_epoch
            state.persisted_admission_generation = getattr(persisted, "admission_generation", 0)
            state.last_touched_monotonic = now_monotonic
            state.last_durable_load_monotonic = now_monotonic
            self._http_bridge_retry_circuit_loaded_keys.add(key)
            # A poison opening recorded by another replica reaches this
            # worker only through this load, and its quarantine is
            # process-local: without re-arming here, this worker's probe is
            # planned with the anchor the row's failures were recorded
            # against.
            effective_poison_threshold = _http_bridge_effective_anchor_poison_threshold(
                getattr(
                    _service_get_settings(),
                    "http_responses_session_bridge_anchor_poison_failure_threshold",
                    _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD,
                )
            )
            arm_poison_quarantine = (
                not local_failure_is_newer
                # An episode whose one-clear marker is set already abandoned
                # its anchor (a completed verified replay deliberately keeps
                # the row and clears the quarantine); re-arming off the
                # unchanged row would re-fence the recovered key for another
                # full deadline.
                and not state.poison_anchor_cleared
                and state.consecutive_failures >= effective_poison_threshold
                and _http_bridge_anchor_poison_detail(state.last_detail) is not None
            )
            if arm_poison_quarantine:
                state.owed_poison_detail = state.last_detail
            # The adopted row can also disprove the episode a local poison
            # quarantine fenced: a zero-failure reset, a deliberate anchor
            # supersession, or a replaced lineage that has not proven poison
            # ends it, and a remotely recovered key must not stay suppressed
            # for the stale deadline. A replaced lineage at or past the
            # threshold with a non-poison last strike is left fenced — its
            # earlier strikes may still be the poison evidence.
            revoke_stale_poison_quarantine = (
                not local_failure_is_newer
                # A transitional abandonment tombstone is not disproof: the
                # fresh anchor's registration has not been confirmed, the
                # old poisoned anchor may still be the stored one, and the
                # surviving quarantine is what keeps full-resend planning
                # from injecting it.
                and persisted.last_detail != _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
                and (
                    persisted.consecutive_failures == 0
                    or persisted.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL
                    or (
                        episode_replaced
                        and _http_bridge_anchor_poison_detail(persisted.last_detail) is None
                        and persisted.consecutive_failures < effective_poison_threshold
                    )
                )
            )
            poison_cooldown_remaining = max(0.0, state.cooldown_until - now_monotonic)
        if arm_poison_quarantine and (episode_replaced or not _http_bridge_session_key_poison_quarantined(self, key)):
            # Skipped only for a truly unchanged episode, so ordinary loads
            # do not bump the quarantine generation that recovery fences
            # observe. An adopted foreign write re-arms even while a
            # quarantine is active: a later durable strike can extend the
            # cooldown past the old deadline, and leaving that deadline in
            # place would let the quarantine lapse mid-cooldown — with the
            # planning cache still fresh — and hand the next half-open probe
            # the poisoned anchor. The re-arm extends the deadline against
            # the adopted cooldown and refreshes the poison provenance to
            # the lineage that now owns the row.
            _quarantine_http_bridge_session(
                self,
                session,
                reason=_HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
                minimum_seconds=_http_bridge_poison_quarantine_minimum_seconds(poison_cooldown_remaining),
            )
        elif revoke_stale_poison_quarantine and _http_bridge_session_key_poison_quarantined(self, key):
            _revoke_http_bridge_poison_quarantine(
                self,
                key,
                generation=_http_bridge_quarantine_clear_fence(self, key),
            )
        return True

    async def _persist_http_bridge_retry_circuit(
        self: Any,
        session: _HTTPBridgeSession,
        state: _HTTPBridgeRetryCircuitState,
    ) -> None:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(session.key)
        try:
            await self._persist_http_bridge_retry_circuit_serialized(
                session,
                state,
                now_monotonic=now_monotonic,
                now_wall=now_wall,
                threshold=threshold,
            )
        finally:
            key_lock.release()

    async def _persist_http_bridge_retry_circuit_serialized(
        self: Any,
        session: _HTTPBridgeSession,
        state: _HTTPBridgeRetryCircuitState,
        *,
        now_monotonic: float,
        now_wall: float,
        threshold: int,
    ) -> None:
        async with self._http_bridge_retry_circuit_lock:
            # Re-checked under the key lock: a settle or a replacement
            # episode that took the key while this writer waited means this
            # strike belongs to a finished episode, and writing it would
            # merge that stale failure into whatever episode owns the row
            # now.
            if self._http_bridge_retry_circuits.get(session.key) is not state:
                return
            consecutive_failures = state.consecutive_failures
            cooldown_until = state.cooldown_until
            last_detail = state.last_detail
            persisted_updated_at_epoch = state.persisted_updated_at_epoch
        poison_sticky_threshold = _http_bridge_effective_anchor_poison_threshold(
            getattr(
                _service_get_settings(),
                "http_responses_session_bridge_anchor_poison_failure_threshold",
                threshold,
            )
        )
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
                poison_sticky_threshold=poison_sticky_threshold,
                conflict_cooldown_until_epoch=now_wall + base_backoff,
                base_backoff_seconds=max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS),
                max_backoff_seconds=max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS),
                clean_close_max_backoff_seconds=max(
                    0.001,
                    _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS,
                ),
            )
            if persisted is not None and persisted_updated_at_epoch <= 0 and persisted.updated_at_epoch != now_wall:
                # Blind base: the pre-strike load failed, so this write
                # carried no valid base and dropped against a row that
                # already existed. A stale-episode drop loses at most its
                # own already-counted strike; a blind drop loses the only
                # record of a genuinely new failure, both durably and — via
                # the wholesale adoption below — locally. Re-strike exactly
                # this one failure on top of the observed lineage: strikes
                # are serialized per key, so this write carries one new
                # strike, and earlier local strikes either already landed
                # unobserved or were lost to their own failed writes. A
                # second drop means the row moved again and the ordinary
                # undercount trade applies.
                persisted = await self._durable_bridge.persist_retry_circuit(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    consecutive_failures=max(0, persisted.consecutive_failures) + 1,
                    cooldown_until_epoch=now_wall + max(0.0, cooldown_until - now_monotonic),
                    last_detail=last_detail,
                    updated_at_epoch=now_wall,
                    base_updated_at_epoch=persisted.updated_at_epoch,
                    failure_threshold=threshold,
                    poison_sticky_threshold=poison_sticky_threshold,
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
                        # The upsert returns the post-write row: when this
                        # write landed the row reflects it, and when its base
                        # mismatched the row is the lineage that owns the key
                        # now and this writer must reconcile from it. Strikes
                        # for one key are serialized across their durable
                        # awaits, so no local failure can be recorded while
                        # this persist is in flight, and the returned row is
                        # adopted wholesale. Adoption compares no wall
                        # clocks: a reset stamped by a lagging replica clock
                        # still replaces the local episode, and taking the
                        # row's epoch exactly — never the max — is what lets
                        # the next strike carry a base that actually exists.
                        if persisted.consecutive_failures < state.consecutive_failures:
                            # The row does not carry this worker's failures:
                            # the lineage was reset, purged, or replaced.
                            # The marker belonged to the ended episode; a
                            # spurious trip only allows one extra fenced
                            # abandonment.
                            state.poison_anchor_cleared = False
                            state.owed_poison_detail = None
                        if (
                            persisted.consecutive_failures != consecutive_failures
                            or persisted.last_detail != last_detail
                        ):
                            # The returned row carries a contribution this
                            # write did not submit: a same-base foreign
                            # strike merged under a lagging clock keeps the
                            # epoch while moving the count or detail, and
                            # its evidence targets whatever anchor is
                            # current — the marker cannot survive it.
                            state.poison_anchor_cleared = False
                            state.owed_poison_detail = (
                                persisted.last_detail
                                if _http_bridge_anchor_poison_detail(persisted.last_detail) is not None
                                else None
                            )
                        if (
                            persisted.updated_at_epoch != now_wall
                            and persisted.updated_at_epoch != persisted_updated_at_epoch
                        ):
                            # This write lost to a foreign replacement row —
                            # a landed write stamps max(base, now_wall), and
                            # an unchanged row keeps the base. Adopting the
                            # foreign epoch below makes later loads see the
                            # episode as unchanged, so this is the only point
                            # that can see the replacement; an equal-or-higher
                            # count hides it from the check above, and a
                            # marker carried across it would permanently
                            # refuse the replacement episode its one
                            # abandonment.
                            state.poison_anchor_cleared = False
                            state.owed_poison_detail = None
                        state.consecutive_failures = max(0, persisted.consecutive_failures)
                        state.cooldown_until = persisted_cooldown_until
                        state.last_detail = persisted.last_detail
                        if state.consecutive_failures == 0:
                            # A zero-failure row is a durable reset ending
                            # the marker's episode.
                            state.poison_anchor_cleared = False
                            state.owed_poison_detail = None
                        if state.cooldown_until > now_monotonic:
                            # A cooling key is not probing: drop any leftover
                            # half-open lease so the admission gate cannot
                            # read it as an in-flight probe after this
                            # cooldown expires.
                            state.half_open_until = 0.0
                        state.persisted_updated_at_epoch = persisted.updated_at_epoch
                        state.persisted_admission_generation = getattr(persisted, "admission_generation", 0)
                        # Post-write time, not this persist's entry time: a
                        # load that started while the durable write was in
                        # flight can carry a start stamp later than the entry
                        # time while its row snapshot predates the write, and
                        # the stale-load guard compares against this value.
                        state.last_durable_load_monotonic = max(
                            state.last_durable_load_monotonic,
                            time.monotonic(),
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
        claimed_lease_out: list[float] | None = None,
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
                    if claimed_lease_out is not None:
                        # The exact lease this admission claimed, handed out
                        # under the same lock that installed it, so a
                        # fail-closed caller can return precisely its own
                        # probe under any interleaving.
                        claimed_lease_out.append(state.half_open_until)
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

    async def _http_bridge_poison_anchor_clear_owed(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        consecutive_failures: int | None,
        configured_threshold: int,
    ) -> "tuple[_HTTPBridgeRetryCircuitState | None, object]":
        """Return the owed episode and the durable anchor captured with it.

        Returns ``(episode, captured_anchor)`` — the validated registered
        episode (or ``None`` when nothing is owed) and the durable session's
        continuity anchor sampled before the validation reads. The caller
        passes exactly this episode to the post-abandonment marker and fences
        the continuity clear on the captured anchor: with the completion path
        settling the circuit before it registers a fresh anchor, any
        completion this consult did not veto changes the anchor after this
        capture, and the fenced clear then matches nothing.

        Capping the poison threshold at the circuit threshold is what makes the
        clear reachable at all, but it also means every later strike in the
        same episode meets the threshold too. Only the first successful
        abandonment settles the anchor; a failed one leaves the marker unset so
        the next strike retries it, which is the contract the retirement
        funnels already advertise in their failure telemetry.

        The decision reads the currently registered episode, not the caller's
        captured count. Between the strike and this consult a multiplexed
        sibling can complete, settle the circuit, and persist a fresh valid
        anchor; the caller's detached state still reports the old count, and
        clearing on it would delete the anchor the sibling just stored. An
        absent or below-threshold registered episode therefore owes nothing.
        """
        if consecutive_failures is None:
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        effective_threshold = _http_bridge_effective_anchor_poison_threshold(configured_threshold)
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            live_episode_owes = (
                state is not None
                and state.consecutive_failures >= effective_threshold
                and not state.poison_anchor_cleared
            )
        if not live_episode_owes:
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        # An abandonment without a captured fence is refused outright: an
        # unfenced clear on this degraded path could delete a fresh anchor a
        # sibling registered after the recheck below. A later strike retries
        # the consult with a working capture.
        anchor_reader = getattr(self._durable_bridge, "session_latest_continuity", None)
        durable_session_id = getattr(session, "durable_session_id", None)
        if anchor_reader is None or durable_session_id is None:
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        try:
            expected_anchor = await anchor_reader(session_id=durable_session_id)
        except Exception:
            logger.warning(
                "Failed to capture durable anchor before poison clear bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        if expected_anchor is None:
            # The durable session row itself is gone; there is no continuity
            # to clear and no columns for a fence to protect.
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        if expected_anchor == (None, None):
            # Both continuity columns are already empty: there is no anchor
            # left to abandon, so a clear here removes no failure cause.
            # Authorizing it anyway would let the settle-on-abandon path
            # reset a circuit that is cooling on genuinely unanchored
            # upstream failures, and that cooldown is the only protection
            # those failures have.
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        # The one-clear marker lives in process memory, so a restart or
        # another replica cannot see it. The durable circuit row is the
        # episode's replica-visible record instead: a completed response
        # settles the circuit and deletes the row, and the fresh anchor that
        # completion persisted must not be deleted by a stale local episode
        # that survived the reset. The settle updates the row to zero rather
        # than deleting it, so a reset row proves the episode ended exactly
        # as an absent one does; only a row still at the effective threshold
        # is a live episode. A lookup failure proves nothing either, and the
        # next strike retries.
        try:
            persisted = await self._durable_bridge.lookup_retry_circuit(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
            )
        except Exception:
            logger.warning(
                "Failed to confirm durable retry-circuit episode before anchor clear bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        if persisted is None or persisted.consecutive_failures < effective_threshold:
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE
        # The row must be a poison episode of its own: another replica can
        # reset the old episode, register a fresh anchor, and open a new
        # at-threshold episode on clean_close, and a count-only check would
        # let the stale local episode clear that fresh anchor. An episode
        # that still OWES a poison clear stays authorized even after a later
        # clean probe failure overwrote the durable detail — fenced on the
        # exact reconciled lineage, so a replaced row never inherits the
        # stale owed state.
        row_detail_is_poison = _http_bridge_anchor_poison_detail(persisted.last_detail) is not None
        # Re-check the live episode after the durable await: a multiplexed
        # sibling can complete, settle the registry, and reset the row while
        # the lookup was in flight, and the stale snapshot must not authorize
        # a clear against the fresh anchor that completion persisted.
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if (
                state is not None
                and state.consecutive_failures >= effective_threshold
                and not state.poison_anchor_cleared
                and (
                    row_detail_is_poison
                    or (
                        state.owed_poison_detail is not None
                        and persisted.updated_at_epoch == state.persisted_updated_at_epoch
                    )
                )
            ):
                if row_detail_is_poison:
                    # The consulted poison row IS the authorization: the
                    # abandonment fences from this episode snapshot, and a
                    # stale local identity — an unpersisted write (epoch
                    # zero) or a cross-replica strike that moved the row
                    # after this worker's own — would make both settlement
                    # attempts reject the newer fence and leave the removed
                    # anchor's cooldown and quarantine standing. Adopt the
                    # row's epoch, admission generation, and higher count
                    # unconditionally.
                    state.persisted_updated_at_epoch = persisted.updated_at_epoch
                    state.persisted_admission_generation = getattr(persisted, "admission_generation", 0)
                    if persisted.consecutive_failures > state.consecutive_failures:
                        state.consecutive_failures = persisted.consecutive_failures
                return state, expected_anchor
            return None, _POISON_ANCHOR_CAPTURE_UNAVAILABLE

    async def _http_bridge_effective_anchor_poison_detail(
        self: Any,
        session: _HTTPBridgeSession,
        detail: str | None,
    ) -> str | None:
        """Map a strike to its poison class, falling back to the owed one.

        A failed abandonment leaves the episode's clear owed, and the spec
        promises the NEXT strike retries it — not only the next poison-class
        strike. A clean probe failure would otherwise overwrite the durable
        detail and strand the owed clear forever.
        """
        mapped = _http_bridge_anchor_poison_detail(detail)
        if mapped is not None:
            return mapped
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            owed = state.owed_poison_detail if state is not None else None
        return _http_bridge_anchor_poison_detail(owed)

    async def _http_bridge_mark_poison_anchor_cleared(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        episode: _HTTPBridgeRetryCircuitState,
    ) -> None:
        """Record that this episode's poisoned anchor has been abandoned.

        The marker belongs to the episode that performed the abandonment. If
        that episode was settled and replaced while the durable rebind was in
        flight, marking whatever is registered now would suppress the new
        episode's own required abandonment and leave its poisoned anchor
        reusable, so a caller that captured its episode marks only that one.
        """
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is not None and state is episode:
                state.poison_anchor_cleared = True
                state.owed_poison_detail = None

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

    async def _http_bridge_precreated_retry_block_for_key(
        self: Any,
        key: _HTTPBridgeSessionKey,
        *,
        assume_remote_half_open_lease: bool = False,
    ) -> tuple[float, str]:
        """Return ``(seconds_blocked, reason)`` for a suppressed replacement.

        The same-owner stale-anchor recovery dispatches on a unique internal
        session while the circuit that refused it lives on the source hard
        key, so the block has to be read from that key. During the half-open
        lease the cooldown is already zero; reading only the replacement
        session (which has no circuit) and the source key's cooldown
        advertises a ~1s retry-after while the caller is barred for the rest
        of the lease.

        ``assume_remote_half_open_lease`` covers the timer this process
        cannot see: a caller that just lost its dispatch claim was refused by
        a probe admitted somewhere, and when neither a local cooldown nor a
        local lease is visible that probe's lease lives in another process
        (or died with a replaced local state). The lease deadline is not
        persisted, so the block is reported at the configured lease duration
        — the upper bound of the timer actually refusing the caller — rather
        than a fabricated ~1s.
        """
        if key.strength != "hard":
            return 0.0, "none"
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(key)
            local_cooldown_remaining = max(0.0, state.cooldown_until - now) if state is not None else 0.0
            half_open_remaining = (
                max(0.0, state.half_open_until - now)
                if state is not None and state.consecutive_failures >= _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD
                else 0.0
            )
        cooldown_remaining = max(
            local_cooldown_remaining,
            await self._http_bridge_retry_circuit_cooldown_seconds_for_key(key),
        )
        if cooldown_remaining <= 0.0 and half_open_remaining <= 0.0:
            if assume_remote_half_open_lease:
                return _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS, "hard_key_half_open"
            return 0.0, "none"
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
            if (
                scoped_attempt.disarmed
                or (scoped_attempt.response_observed and not terminal_pre_response_frame)
                or (terminal_pre_response_frame and scoped_attempt.non_terminal_response_observed)
            ):
                return None

        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        max_backoff = max(base_backoff, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS)
        clean_close_max_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS)
        duplicate_attempt: _HTTPBridgeResponseCreateAttempt | None = None
        state: _HTTPBridgeRetryCircuitState | None = None
        poison_class_failure = _http_bridge_anchor_poison_detail(detail) is not None
        quarantine_poisoned_anchor = False
        quarantine_cooldown_remaining = 0.0
        recorded_failures: int | None = None
        # The registry mutation and its durable write hold the key lock
        # together: a settle for this key either completes before the strike
        # registers (the strike then opens a fresh episode) or waits until
        # the strike's write has landed. Without this, a failure recorded
        # after a settlement linearized could be dropped as superseded and
        # disappear instead of opening the circuit.
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(session.key)
        try:
            # The load runs under the key lock too: outside it, a settle in
            # flight lets the load re-hydrate the just-popped counts from the
            # not-yet-reset row, and this strike would then extend the ended
            # episode instead of opening a fresh one.
            await self._load_http_bridge_retry_circuit(session)
            # Sampled after the keyed wait and the load: a wait approaching
            # the base backoff would otherwise persist an already-aged
            # cooldown and make the fresh failure look older than the
            # durable load for merge bookkeeping.
            now = time.monotonic()
            async with self._http_bridge_retry_circuit_lock:
                if scoped_attempt is not None and scoped_attempt.retry_circuit_failure_recorded:
                    duplicate_attempt = scoped_attempt
                elif scoped_attempt is not None and (
                    scoped_attempt.disarmed
                    or (scoped_attempt.response_observed and not terminal_pre_response_frame)
                    # ``terminal_pre_response_frame`` exists because the terminal
                    # frame itself marks the attempt observed. It must not also
                    # steamroll genuine midstream evidence: a deferred-reasoning
                    # prelude observed a non-terminal response event without
                    # counting it, so a later terminal failure is a midstream
                    # failure, not a pre-response strike.
                    or (terminal_pre_response_frame and scoped_attempt.non_terminal_response_observed)
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
                    prior_recorded_detail = state.last_detail
                    state.last_detail = detail
                    if _http_bridge_anchor_poison_detail(detail) is not None:
                        if prior_recorded_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL:
                            # The supersession marked the OLD anchor's clear
                            # as no longer owed; this strike is evidence
                            # against the freshly registered anchor and
                            # starts a new abandonment story of its own.
                            state.poison_anchor_cleared = False
                        if state.consecutive_failures >= _http_bridge_effective_anchor_poison_threshold(
                            getattr(
                                _service_get_settings(),
                                "http_responses_session_bridge_anchor_poison_failure_threshold",
                                threshold,
                            )
                        ):
                            # Debt exists only for an episode that reached the
                            # effective poison threshold on poison evidence —
                            # the same threshold that authorizes the
                            # abandonment, so a configured threshold of one
                            # arms the debt its failed clear leaves owed.
                            # Arming below it let a clean_close opener
                            # resurrect an earlier poison detail and clear a
                            # valid anchor with no quarantine covering the
                            # race.
                            state.owed_poison_detail = detail
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
                    if poison_class_failure and not quarantine_poisoned_anchor:
                        # A configured abandonment threshold below the circuit
                        # threshold clears the anchor before the circuit ever
                        # opens, and the terminal frame is published before that
                        # clear. The quarantine has to cover this window too, or
                        # an immediate client retry is planned with the dead
                        # anchor while the clear is still awaiting I/O.
                        configured_poison_threshold = getattr(
                            _service_get_settings(),
                            "http_responses_session_bridge_anchor_poison_failure_threshold",
                            threshold,
                        )
                        if state.consecutive_failures >= _http_bridge_effective_anchor_poison_threshold(
                            configured_poison_threshold
                        ):
                            quarantine_poisoned_anchor = True
                            quarantine_cooldown_remaining = max(0.0, state.cooldown_until - now)
            if duplicate_attempt is None:
                assert state is not None
                armed_quarantine_generation: int | None = None
                pre_arm_quarantine_reason: str | None = None
                pre_arm_quarantine_until = 0.0
                if quarantine_poisoned_anchor:
                    # A revocation after a lost cross-replica race must not
                    # evict a weaker fence this arm merely upgraded, so the
                    # prior active entry is captured for restoration.
                    prior_entry = _http_bridge_quarantine_registry(self).get(session.key)
                    if (
                        prior_entry is not None
                        and prior_entry.quarantined_until > time.monotonic()
                        and prior_entry.reason != _HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON
                    ):
                        pre_arm_quarantine_reason = prior_entry.reason
                        pre_arm_quarantine_until = prior_entry.quarantined_until
                    _quarantine_http_bridge_session(
                        self,
                        session,
                        reason=_HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
                        minimum_seconds=_http_bridge_poison_quarantine_minimum_seconds(quarantine_cooldown_remaining),
                    )
                    # Captured so a lost cross-replica race can revoke exactly
                    # this speculative arm and nothing re-armed after it.
                    armed_quarantine_generation = _http_bridge_quarantine_generation(self, session.key)
                recorded_failures = await self._record_http_bridge_retry_circuit_failure_locked(
                    session,
                    state,
                    scoped_attempt=scoped_attempt,
                    threshold=threshold,
                    quarantine_poisoned_anchor=quarantine_poisoned_anchor,
                    quarantine_cooldown_remaining=quarantine_cooldown_remaining,
                    armed_quarantine_generation=armed_quarantine_generation,
                    pre_arm_quarantine_reason=pre_arm_quarantine_reason,
                    pre_arm_quarantine_until=pre_arm_quarantine_until,
                )
        finally:
            key_lock.release()
        if duplicate_attempt is not None:
            return await self._await_http_bridge_retry_circuit_attempt_settlement(
                session,
                attempt=duplicate_attempt,
                detail=detail,
            )
        return recorded_failures

    async def _record_http_bridge_retry_circuit_failure_locked(
        self: Any,
        session: _HTTPBridgeSession,
        state: _HTTPBridgeRetryCircuitState,
        *,
        scoped_attempt: _HTTPBridgeResponseCreateAttempt | None,
        threshold: int,
        quarantine_poisoned_anchor: bool,
        quarantine_cooldown_remaining: float,
        armed_quarantine_generation: int | None = None,
        pre_arm_quarantine_reason: str | None = None,
        pre_arm_quarantine_until: float = 0.0,
    ) -> int | None:
        try:
            await self._persist_http_bridge_retry_circuit_serialized(
                session,
                state,
                now_monotonic=time.monotonic(),
                now_wall=time.time(),
                threshold=threshold,
            )
            merged_cooldown_remaining = 0.0
            merged_poison_opened = False
            merged_poison_revoked = False
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
                #
                # Only when the registry still maps this key to this state. A
                # multiplexed sibling that completed while the persist was in
                # flight clears the circuit and drops this object, and it also
                # advanced the anchor; re-arming off the detached state would
                # resurrect a poisoned key whose anchor is now valid and let a
                # later explicit rejection discard it. Nor is there anything to
                # re-arm when this call already quarantined the key from its own
                # opening and the merge did not move the deadline: that arm is a
                # no-op except for the generation bump, which is the fence the
                # verified stale-anchor replay claims at dispatch (#1863).
                if self._http_bridge_retry_circuits.get(session.key) is state:
                    merged_cooldown_remaining = max(0.0, state.cooldown_until - time.monotonic())
                    # The persist merge adopted the returned row, so the
                    # verdict must come from the adopted detail and count,
                    # not the local strike's class: a clean_close losing to
                    # a poison opening must still quarantine, and a poison
                    # strike losing to a reset or clean lineage must not
                    # leave its speculative quarantine suppressing a valid
                    # anchor.
                    adopted_poison_class = _http_bridge_anchor_poison_detail(state.last_detail) is not None
                    # The effective anchor-poison threshold, which sits at
                    # or below the circuit threshold: with a configured
                    # threshold of one, a non-poison local strike adopting a
                    # one-failure poison row must still arm the quarantine,
                    # or the loaded-key cache hands the next request the
                    # dead anchor.
                    poison_arm_threshold = _http_bridge_effective_anchor_poison_threshold(
                        getattr(
                            _service_get_settings(),
                            "http_responses_session_bridge_anchor_poison_failure_threshold",
                            threshold,
                        )
                    )
                    merged_poison_opened = (
                        adopted_poison_class
                        and consecutive_failures >= poison_arm_threshold
                        and (
                            not quarantine_poisoned_anchor or merged_cooldown_remaining > quarantine_cooldown_remaining
                        )
                    )
                    # Revocation mirrors the arm's own justification: the
                    # quarantine survives whenever the adopted state still
                    # satisfies that same bar.
                    merged_poison_revoked = quarantine_poisoned_anchor and not (
                        adopted_poison_class and consecutive_failures >= poison_arm_threshold
                    )
            if merged_poison_opened:
                _quarantine_http_bridge_session(
                    self,
                    session,
                    reason=_HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON,
                    minimum_seconds=_http_bridge_poison_quarantine_minimum_seconds(merged_cooldown_remaining),
                )
            elif merged_poison_revoked:
                _revoke_http_bridge_poison_quarantine(
                    self,
                    session.key,
                    generation=armed_quarantine_generation,
                    restore_reason=pre_arm_quarantine_reason,
                    restore_until=pre_arm_quarantine_until,
                )
            return consecutive_failures
        finally:
            if scoped_attempt is not None and scoped_attempt.retry_circuit_failure_settled is not None:
                scoped_attempt.retry_circuit_failure_settled.set()

    async def _http_bridge_suppress_poison_clear_after_anchor_advance(
        self: Any,
        session: _HTTPBridgeSession,
    ) -> tuple[Any, str | None, str | None, float, int, bool] | None:
        """Mark the surviving episode's clear as no longer owed.

        A completed response just advanced the durable anchor while its
        circuit settlement failed and the old episode was restored. That
        episode's recorded failures were all against the superseded anchor,
        so its owed abandonment must not fire against the fresh one: one
        later eventless failure would otherwise ride the restored
        at-threshold count straight into clearing continuity the episode
        never proved dead. The cooldown itself is left standing and settles
        at the next opportunity.

        The suppression is also persisted by rewriting the surviving row's
        failure detail under the row's version fence: the local marker
        protects only this worker, and another replica loading the
        surviving at-threshold poison row would otherwise arm quarantine
        against the fresh anchor and authorize an abandonment for failures
        recorded against the superseded one. The durable transitional
        detail is the fail-closed ``anchor_abandoned`` tombstone, not
        ``anchor_superseded``: the registration has not landed yet, so a
        crash inside the window must read as "deltas fail closed", never
        as a disproved episode that hands replicas the old poisoned
        anchor. The caller promotes the tombstone to ``anchor_superseded``
        once the fresh anchor commits. A concurrent strike outracing the
        rewrite keeps its poison class — it is new evidence — and a failed
        rewrite leaves the suppression process-local, no worse than
        before.
        """
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(session.key)
        try:
            async with self._http_bridge_retry_circuit_lock:
                state = self._http_bridge_retry_circuits.get(session.key)
                if state is None:
                    return None
                if (
                    _http_bridge_anchor_poison_detail(state.last_detail) is None
                    and state.last_detail != _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
                    # Owed debt IS poison evidence even when a later
                    # non-poison strike overwrote the local detail (its
                    # durable persist and reload can both fail): without a
                    # token the debt survives the fresh registration and a
                    # later strike abandons the anchor just registered.
                    and state.owed_poison_detail is None
                ):
                    # A clean episode owes no abandonment; marking it would
                    # refuse the clear a LATER poison strike genuinely owes,
                    # since only the supersession sentinel resets the marker
                    # on a fresh strike. An existing tombstone is NOT clean:
                    # it is already the transitional durable state (a sticky
                    # tombstone can carry later strikes onto a positive
                    # count), and returning no token here would leave a
                    # committed registration with nothing to promote — every
                    # later submission carrying the freshly registered
                    # anchor would then be rejected against the lingering
                    # tombstone.
                    return None
                prior_owed = state.owed_poison_detail
                state.poison_anchor_cleared = True
                state.owed_poison_detail = None
                if state.persisted_updated_at_epoch <= 0:
                    return (state, None, prior_owed, state.persisted_updated_at_epoch, 0, False)
                captured_state = state
                captured_epoch = state.persisted_updated_at_epoch
                prior_detail = state.last_detail
                supersede_base_epoch = state.persisted_updated_at_epoch
                supersede_base_count = state.consecutive_failures
                if state.last_detail != _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL:
                    # A local tombstone stays a tombstone until the fresh
                    # anchor's registration commits and the promotion
                    # rewrites the durable row: flipping the local cache to
                    # the superseded sentinel early would let a concurrent
                    # proof-gated resend that cannot reload bypass the
                    # fail-closed gates and send the still-stored poisoned
                    # anchor.
                    state.last_detail = _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL
            superseded = False
            try:
                # A detail-only fenced update, not a strike write: the strike
                # upsert increments the merged count by design, and a phantom
                # strike here would extend the cooldown. The row's version is
                # left unchanged so a concurrent strike still merges onto it
                # and its poison class overwrites this sentinel — new
                # evidence outranks the supersession in either order. The
                # fence carries the observed count as well: a lagging-clock
                # strike can merge without moving the version, and every
                # landed merge increments the count, so the count is what
                # makes such a strike outrank a supersession that follows it.
                superseded = await self._durable_bridge.supersede_retry_circuit_detail(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    expected_updated_at_epoch=supersede_base_epoch,
                    expected_consecutive_failures=supersede_base_count,
                    expected_last_detail=prior_detail,
                    last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
                )
            except Exception:
                logger.warning(
                    "Failed to persist HTTP bridge anchor supersession bridge_kind=%s bridge_key=%s",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    exc_info=True,
                )
            if not superseded:
                async with self._http_bridge_retry_circuit_lock:
                    current = self._http_bridge_retry_circuits.get(session.key)
                    if current is state and state.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL:
                        # The fenced rewrite missed (the row moved) or the
                        # write failed: keep the local view matching the
                        # durable row; the suppression stays process-local,
                        # no worse than before.
                        state.last_detail = prior_detail
            return (captured_state, prior_detail, prior_owed, captured_epoch, supersede_base_count, superseded)
        finally:
            key_lock.release()

    async def _http_bridge_restore_poison_row_after_failed_registration(
        self: Any,
        session: _HTTPBridgeSession,
        poison_detail: str,
    ) -> None:
        """Re-seed durable poison evidence after a failed anchor registration.

        The settle-before-registration ordering is fence-critical, so a
        registration failure lands after the circuit was already zeroed
        while the old poisoned anchor is still the stored one. The kept
        local quarantine alone cannot hold: the next planning load reads
        the zeroed row as a disproved episode and revokes it, and other
        replicas never arm. Re-opening the row at the circuit threshold
        with the prior poison class restores the protection everywhere; a
        CAS drop against a row that moved concurrently defers to that
        newer evidence.
        """
        key = session.key
        now_wall = time.time()
        now_monotonic = time.monotonic()
        try:
            row = await self._durable_bridge.lookup_retry_circuit(
                session_key_kind=key.affinity_kind,
                session_key_value=key.affinity_key,
                api_key_id=key.api_key_id,
            )
        except Exception:
            logger.warning(
                "Failed to reload HTTP bridge retry circuit before poison restore bridge_kind=%s bridge_key=%s",
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                exc_info=True,
            )
            return
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        if row is not None and row.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL:
            # The failed registration follows this completion's OWN settle,
            # which wrote the transitional tombstone; the strike merge's
            # sticky-tombstone rule would silently refuse to write the
            # poison class back over it, degrading the re-seed to a
            # threshold tombstone no replica arms a quarantine from. The
            # fenced detail-only supersede is the one path allowed through
            # that stickiness; a CAS miss means newer evidence owns the row
            # and the merge below defers to it.
            try:
                await self._durable_bridge.supersede_retry_circuit_detail(
                    session_key_kind=key.affinity_kind,
                    session_key_value=key.affinity_key,
                    api_key_id=key.api_key_id,
                    expected_updated_at_epoch=row.updated_at_epoch,
                    expected_consecutive_failures=row.consecutive_failures,
                    expected_last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
                    last_detail=poison_detail,
                )
            except Exception:
                logger.warning(
                    "Failed to transition HTTP bridge tombstone before poison restore bridge_kind=%s bridge_key=%s",
                    key.affinity_kind,
                    _hash_identifier(key.affinity_key),
                    exc_info=True,
                )
            if row.consecutive_failures >= threshold:
                return
        elif row is not None and row.consecutive_failures >= threshold:
            return
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        try:
            await self._durable_bridge.persist_retry_circuit(
                session_key_kind=key.affinity_kind,
                session_key_value=key.affinity_key,
                api_key_id=key.api_key_id,
                consecutive_failures=threshold,
                cooldown_until_epoch=now_wall + base_backoff,
                last_detail=poison_detail,
                updated_at_epoch=now_wall,
                base_updated_at_epoch=row.updated_at_epoch if row is not None else 0.0,
                failure_threshold=threshold,
                poison_sticky_threshold=_http_bridge_effective_anchor_poison_threshold(
                    getattr(
                        _service_get_settings(),
                        "http_responses_session_bridge_anchor_poison_failure_threshold",
                        threshold,
                    )
                ),
                conflict_cooldown_until_epoch=now_wall + base_backoff,
                base_backoff_seconds=base_backoff,
                max_backoff_seconds=max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS),
                clean_close_max_backoff_seconds=max(
                    0.001,
                    _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS,
                ),
            )
        except Exception:
            logger.warning(
                "Failed to restore HTTP bridge poison evidence bridge_kind=%s bridge_key=%s",
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                exc_info=True,
            )
            return
        del now_monotonic
        # Adopt the restored row locally and re-arm the quarantine through
        # the ordinary load path.
        await self._load_http_bridge_retry_circuit(session)

    async def _http_bridge_restore_poison_clear_after_failed_anchor_advance(
        self: Any,
        session: _HTTPBridgeSession,
        supersession: tuple[Any, str | None, str | None, float, int, bool] | None,
    ) -> None:
        """Roll a transitional anchor-advance suppression back.

        The suppression is applied before the fresh anchor is registered, so
        a concurrent consult whose continuity read lands after publication
        cannot validate the old poison row against the just-published
        anchor. When that registration then fails, the old anchor is still
        the stored one, and leaving the suppression in place would strand it
        with no funnel willing to clear it — the failure the
        registration-gated ordering existed to prevent.
        """
        if supersession is None:
            return
        captured_state, prior_detail, prior_owed, base_epoch, base_count, superseded = supersession
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(session.key)
        try:
            async with self._http_bridge_retry_circuit_lock:
                state = self._http_bridge_retry_circuits.get(session.key)
                if (
                    state is not None
                    # Fenced to the exact captured episode and lineage: a
                    # same-key load, strike, or settlement replacing or
                    # reconciling the state while the registration was in
                    # flight owns the key now, and copying the ended
                    # lineage's owed evidence into it would let a later
                    # strike clear continuity that episode never proved
                    # dead.
                    and state is captured_state
                    and state.persisted_updated_at_epoch == base_epoch
                ):
                    state.poison_anchor_cleared = False
                    if prior_owed is not None:
                        state.owed_poison_detail = prior_owed
                    if (
                        prior_detail is not None
                        and state.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL
                    ):
                        state.last_detail = prior_detail
            if superseded and prior_detail is not None:
                try:
                    # A fence miss here means a strike moved the row and its
                    # own poison class already replaced the sentinel.
                    await self._durable_bridge.supersede_retry_circuit_detail(
                        session_key_kind=session.key.affinity_kind,
                        session_key_value=session.key.affinity_key,
                        api_key_id=session.key.api_key_id,
                        expected_updated_at_epoch=base_epoch,
                        expected_consecutive_failures=base_count,
                        expected_last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
                        last_detail=prior_detail,
                    )
                except Exception:
                    logger.warning(
                        "Failed to roll back HTTP bridge anchor supersession bridge_kind=%s bridge_key=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        exc_info=True,
                    )
        finally:
            key_lock.release()

    async def _http_bridge_reconcile_transitional_tombstone(self: Any, session: _HTTPBridgeSession) -> None:
        """Rewrite a lingering transitional tombstone off the row's own values.

        Strike merges keep ``anchor_abandoned`` sticky, so a CAS miss on a
        promote or erase does NOT mean the tombstone was replaced — it means
        the count moved underneath the fence while the tombstone stood, and
        the freshly committed anchor would stay rejected on every replica.
        One reload-and-rewrite round converges the interleavings: a zeroed
        row erases to a plain reset, a positive count promotes to the
        superseded sentinel, and a second miss defers to newer evidence and
        the next completion's settle-and-erase.
        """
        for reconcile_attempt in range(2):
            try:
                row = await self._durable_bridge.lookup_retry_circuit(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                )
                if row is None or row.last_detail != _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL:
                    return
                rewrote = await self._durable_bridge.supersede_retry_circuit_detail(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    expected_updated_at_epoch=row.updated_at_epoch,
                    expected_consecutive_failures=row.consecutive_failures,
                    expected_last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
                    last_detail=(
                        None if row.consecutive_failures == 0 else _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL
                    ),
                )
                if rewrote:
                    return
            except Exception:
                if reconcile_attempt == 0:
                    continue
                logger.debug(
                    "Failed to reconcile transitional circuit tombstone",
                    exc_info=True,
                )
                return

    async def _http_bridge_promote_transitional_supersession(
        self: Any,
        session: _HTTPBridgeSession,
        supersession: tuple[Any, str | None, str | None, float, int, bool] | None,
    ) -> None:
        """Promote a landed transitional tombstone to ``anchor_superseded``.

        The fresh anchor just committed, so the fail-closed tombstone the
        suppression persisted is no longer the right durable record: left
        in place it would 404 healthy delta follow-ups riding the valid
        fresh anchor, and other replicas' stale quarantines would never
        see the disproof. A fence miss means a concurrent strike moved the
        row and its newer evidence stands; a failed promote leaves the
        conservative tombstone, which only fails closed.
        """
        if supersession is None:
            return
        _captured_state, _prior_detail, _prior_owed, base_epoch, base_count, superseded = supersession
        if not superseded:
            return
        for promote_attempt in range(2):
            try:
                promoted = await self._durable_bridge.supersede_retry_circuit_detail(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    expected_updated_at_epoch=base_epoch,
                    expected_consecutive_failures=base_count,
                    expected_last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
                    last_detail=_HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_SUPERSEDED_DETAIL,
                )
                if promoted is False:
                    # A concurrent strike moved the count while stickiness
                    # kept the tombstone detail: reporting success here
                    # would leave the freshly committed anchor rejected on
                    # every replica. Reconcile on the row's own values.
                    await self._http_bridge_reconcile_transitional_tombstone(session)
                return
            except Exception:
                # One immediate retry covers a transient durable blip: a
                # skipped promotion leaves the fresh anchor rejected on
                # every replica until the next completion's settle-and-erase
                # heals it. A second failure defers to that healing.
                if promote_attempt == 0:
                    continue
                logger.warning(
                    "Failed to promote HTTP bridge anchor supersession bridge_kind=%s bridge_key=%s",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    exc_info=True,
                )

    async def _clear_http_bridge_retry_circuit(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        settled_detail: str | None = None,
        settled_detail_authoritative: bool = False,
        expected_episode: tuple[float, int, int] | None = None,
    ) -> bool:
        """Settle a hard key's retry circuit; ``True`` when settlement held.

        The load below re-hydrates any persisted row into the local state, so
        after a successful load the fence is absent only when no durable row
        was observed at all — and an unfenced delete there could only remove a
        row another writer created concurrently. A strike write still in
        flight while this clear runs is undone by its own writer:
        ``_persist_http_bridge_retry_circuit`` re-checks the registry after
        its durable write lands and deletes exactly the row it created.
        """
        key = session.key
        if key.strength != "hard":
            return True
        key_lock = await self._acquire_http_bridge_retry_circuit_key_lock(key)
        try:
            return await self._clear_http_bridge_retry_circuit_serialized(
                session,
                settled_detail=settled_detail,
                settled_detail_authoritative=settled_detail_authoritative,
                expected_episode=expected_episode,
            )
        finally:
            key_lock.release()

    async def _clear_http_bridge_retry_circuit_serialized(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        settled_detail: str | None = None,
        settled_detail_authoritative: bool = False,
        expected_episode: tuple[float, int, int] | None = None,
    ) -> bool:
        key = session.key
        durable_load_succeeded = await self._load_http_bridge_retry_circuit(session)
        if expected_episode is not None:
            # The caller settles a specific authorized episode — an
            # abandonment clears only the circuit its abandonment
            # invalidated. A replacement episode opened between the
            # authorization and this settle (fresh strikes against a newly
            # registered anchor) carries its own valid cooldown; resetting
            # it would let the newly poisoned anchor retry immediately.
            # The count discriminates lagging-clock merges the epoch alone
            # cannot see. Nothing is popped on a mismatch: the settlement
            # stays owed and the newer row stands untouched.
            async with self._http_bridge_retry_circuit_lock:
                fenced_state = self._http_bridge_retry_circuits.get(key)
            if fenced_state is not None and (
                fenced_state.persisted_updated_at_epoch != expected_episode[0]
                or fenced_state.consecutive_failures != expected_episode[1]
                # A replay claim advances only the admission generation:
                # resetting beneath it would clear the circuit a claimed
                # replay depends on and admit a second dispatch beside it.
                or fenced_state.persisted_admission_generation != expected_episode[2]
            ):
                return False
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.pop(key, None)
            self._http_bridge_retry_circuit_loaded_keys.discard(key)
            self._http_bridge_retry_circuit_persisted_keys.discard(key)
            if state is not None or not durable_load_succeeded:
                # The pop takes the state's stale-load watermark with it;
                # keep a per-key watermark so a lookup that began before
                # this settlement cannot adopt the pre-settlement row into a
                # fresh state and resurrect the settled cooldown. A healthy
                # key with no state and a confirmed durable miss has nothing
                # to resurrect, and stamping every completed conversation
                # would grow this map — and its global-lock prune scan —
                # with every key the process ever serves.
                self._http_bridge_retry_circuit_reconcile_watermarks[key] = time.monotonic()
            expected_updated_at_epoch = (
                state.persisted_updated_at_epoch if state is not None and state.persisted_updated_at_epoch > 0 else None
            )
            expected_admission_generation = state.persisted_admission_generation if state is not None else None
            # A lagging-clock strike merges a higher count without moving the
            # epoch; the reset CAS carries the observed count so it cannot
            # zero a newer episode's cooldown it never saw. A completion
            # settle defeated this way still wins through the chase.
            expected_consecutive_failures = state.consecutive_failures if state is not None else None
        if (
            settled_detail is None
            # An authoritative None was chosen after consulting continuity —
            # the caller SAW fresh evidence replacing the poisoned anchor —
            # and upgrading it back to a tombstone would fail the fresh
            # continuity closed. Only a blind None is upgraded.
            and not settled_detail_authoritative
            and state is not None
            and (
                _http_bridge_anchor_poison_detail(state.last_detail) is not None
                or state.owed_poison_detail is not None
                or state.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
            )
        ):
            # The caller's pre-settle capture can be blind — a transient
            # read failure before this call — while this settle's own load
            # just adopted a poison episode or an existing tombstone.
            # Resetting such a row plain would leave the old poisoned
            # anchor behind a reset no replica reads as suspect, so the
            # reset detail is derived from the state actually settled: the
            # fail-closed tombstone, healed by a later completion's
            # fence-aware settle-and-erase.
            settled_detail = _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
        # A confirmed miss has no version fence to protect a row created
        # concurrently, so leave the durable row untouched when no state was
        # observed. Preserve the existing best-effort clear on read failures,
        # which is still useful for settling a row after a transient outage.
        if durable_load_succeeded and (state is None or expected_updated_at_epoch is None):
            return True
        if state is None and not durable_load_succeeded:
            # No local episode and no durable observation: this worker holds
            # no fence at all, and an unfenced reset here could clear a
            # poison episode or a claimed admission generation another
            # replica created during the outage. The best-effort clear is
            # reserved for a worker that at least carries a local episode;
            # with nothing observed, the settlement stays owed.
            return False
        try:
            # Clearing is idempotent and must be attempted even when the
            # preceding lookup failed; a successful request should settle
            # a previously persisted circuit after a transient read error.
            reset_matched = await self._durable_bridge.clear_retry_circuit(
                session_key_kind=key.affinity_kind,
                session_key_value=key.affinity_key,
                api_key_id=key.api_key_id,
                expected_updated_at_epoch=expected_updated_at_epoch,
                expected_admission_generation=expected_admission_generation,
                expected_consecutive_failures=expected_consecutive_failures,
                reset_detail=settled_detail,
            )
            if reset_matched is False and expected_updated_at_epoch is not None:
                # The fenced reset matched no row: another writer moved it
                # after this worker's lookup. This settlement carries the
                # newer evidence — the completed response proved the key
                # works — so reload the moved row once and retry the fence
                # against its current version, the same settle-wins rule the
                # in-process race already follows (a strike writer undoes
                # its own row when the registry no longer maps its state).
                moved = await self._durable_bridge.lookup_retry_circuit(
                    session_key_kind=key.affinity_kind,
                    session_key_value=key.affinity_key,
                    api_key_id=key.api_key_id,
                )
                if moved is None or moved.consecutive_failures <= 0:
                    # The moved row was purged or already reset: settled.
                    reset_matched = True
                elif (
                    expected_admission_generation is not None
                    and moved.updated_at_epoch == expected_updated_at_epoch
                    and getattr(moved, "admission_generation", 0) != expected_admission_generation
                ):
                    # Same failure observation, advanced admission
                    # generation: a replica claimed the replay admission
                    # this circuit fences, and the claim deliberately holds
                    # the circuit open for its dispatch. Chasing the moved
                    # generation here would clear it anyway — leave the
                    # settlement owed instead.
                    pass
                elif expected_episode is not None and (
                    moved.updated_at_epoch != expected_episode[0]
                    # Strictly greater, not unequal: merges only ever
                    # increment the count, so a LOWER count at the same
                    # epoch and admission generation cannot be foreign — it
                    # is this worker's own episode observed before local
                    # strikes whose durable writes failed. Refusing to
                    # settle it would leave a confirmed abandonment's
                    # cooldown standing against an anchor already removed.
                    or moved.consecutive_failures > expected_episode[1]
                    or getattr(moved, "admission_generation", 0) != expected_episode[2]
                ):
                    # An episode-fenced settle never chases a moved row: the
                    # caller authorized clearing exactly one episode, and a
                    # row that no longer matches the captured epoch, count,
                    # and admission generation is a replacement lineage —
                    # re-fencing on ITS version would durably zero the valid
                    # cooldown a cross-replica completion-plus-strikes just
                    # opened against the fresh anchor. The settlement stays
                    # owed; the settle-wins chase is for completion callers,
                    # whose own evidence outranks any concurrent strike.
                    pass
                else:
                    reset_matched = await self._durable_bridge.clear_retry_circuit(
                        session_key_kind=key.affinity_kind,
                        session_key_value=key.affinity_key,
                        api_key_id=key.api_key_id,
                        expected_updated_at_epoch=moved.updated_at_epoch,
                        expected_admission_generation=getattr(moved, "admission_generation", None),
                        expected_consecutive_failures=moved.consecutive_failures,
                        reset_detail=settled_detail,
                    )
            if reset_matched is False and expected_updated_at_epoch is not None:
                # Two fenced attempts both matched nothing: the row is
                # moving faster than this settlement can chase it, so
                # dropping the local state would report settlement while the
                # durable episode survives to be reloaded. Keep the episode
                # for the next clear opportunity — but reconcile it onto the
                # row that actually survived first: restoring the original
                # snapshot would hand the anchor-supersession rewrite an
                # obsolete fence, and the next load would then read the
                # surviving row as a foreign episode, reset the one-clear
                # marker, and authorize abandoning the fresh anchor this
                # completion just registered.
                surviving = None
                refresh_failed = False
                try:
                    surviving = await self._durable_bridge.lookup_retry_circuit(
                        session_key_kind=key.affinity_kind,
                        session_key_value=key.affinity_key,
                        api_key_id=key.api_key_id,
                    )
                except Exception:
                    refresh_failed = True
                    logger.warning(
                        "Failed to reload HTTP bridge retry circuit after settle misses bridge_kind=%s bridge_key=%s",
                        key.affinity_kind,
                        _hash_identifier(key.affinity_key),
                        exc_info=True,
                    )
                if not refresh_failed and (surviving is None or surviving.consecutive_failures <= 0):
                    # The chase ended settled after all: the row was purged
                    # or reset by another writer while this settlement was
                    # missing its fences.
                    reset_matched = True
                else:
                    async with self._http_bridge_retry_circuit_lock:
                        if state is not None and self._http_bridge_retry_circuits.get(key) is None:
                            if surviving is not None and surviving.consecutive_failures > 0:
                                if surviving.updated_at_epoch != state.persisted_updated_at_epoch:
                                    # Adopting a lineage this worker did not
                                    # produce: same marker rule as the load
                                    # and persist merges.
                                    state.poison_anchor_cleared = False
                                    state.owed_poison_detail = None
                                state.consecutive_failures = max(0, surviving.consecutive_failures)
                                state.cooldown_until = time.monotonic() + max(
                                    0.0, surviving.cooldown_until_epoch - time.time()
                                )
                                state.last_detail = surviving.last_detail
                                if state.cooldown_until > time.monotonic():
                                    state.half_open_until = 0.0
                                state.persisted_updated_at_epoch = surviving.updated_at_epoch
                                state.persisted_admission_generation = getattr(surviving, "admission_generation", 0)
                                state.last_durable_load_monotonic = max(
                                    state.last_durable_load_monotonic,
                                    time.monotonic(),
                                )
                            self._http_bridge_retry_circuits[key] = state
                            self._http_bridge_retry_circuit_loaded_keys.add(key)
                            self._http_bridge_retry_circuit_persisted_keys.add(key)
                    return False
        except Exception:
            logger.warning(
                "Failed to clear persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                exc_info=True,
            )
            # A fenced delete failing means a durable row this worker wrote
            # is known to have survived, so this is not a completed
            # settlement: put the popped episode back (unless a newer one
            # already took the key) so the process keeps its version fence
            # and the next clear opportunity retries the fenced delete,
            # instead of a later load resurrecting the row as a fresh
            # cooldown against a cause that is already gone. An unfenced
            # best-effort delete after a failed load proves nothing about
            # any row and keeps its old settle-anyway semantics.
            if state is not None and expected_updated_at_epoch is not None:
                async with self._http_bridge_retry_circuit_lock:
                    if self._http_bridge_retry_circuits.get(key) is None:
                        self._http_bridge_retry_circuits[key] = state
                        self._http_bridge_retry_circuit_loaded_keys.add(key)
                        self._http_bridge_retry_circuit_persisted_keys.add(key)
                return False
        async with self._http_bridge_retry_circuit_lock:
            # Re-stamp past the durable delete: the pop-time watermark above
            # predates the commit, so a load that started between them passes
            # the stale-load guard while its snapshot still shows the deleted
            # row. Strikes hold this key's lock for the whole settle, so any
            # state present here was resurrected by exactly such a load —
            # drop it with the same fence.
            self._http_bridge_retry_circuit_reconcile_watermarks[key] = time.monotonic()
            resurrected = self._http_bridge_retry_circuits.get(key)
            if resurrected is not None and resurrected is not state:
                self._http_bridge_retry_circuits.pop(key, None)
                self._http_bridge_retry_circuit_loaded_keys.discard(key)
                self._http_bridge_retry_circuit_persisted_keys.discard(key)
        if state is None:
            return True
        if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
            http_bridge_retry_circuit_total.labels(outcome="reset").inc()
        logger.info(
            "http_bridge_retry_circuit event=reset bridge_kind=%s bridge_key=%s failures=%s",
            key.affinity_kind,
            _hash_identifier(key.affinity_key),
            state.consecutive_failures,
        )
        return True
