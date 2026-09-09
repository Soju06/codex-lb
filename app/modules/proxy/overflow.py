"""Subscription-exhaustion overflow to a designated model source (#2123 WP-C2, WP-D folded).

The decision module behind the overflow wiring: constants, the per-source
breaker and admission claims, the fast-decline set, the pin executor and
toucher, the route-admission decision (``resolve_subscription_overflow``), the
compact and WebSocket-handshake pin checks, the ``not_portable_history`` hint
and the pure eligibility helpers the WebSocket parity helper shares.

Design (v3) obligations this module owns:

* Ship-dark (I9): the first step of every entry point is two attribute reads
  on the already-warm ``SettingsCache`` row (``subscription_overflow_source_id``
  and ``subscription_overflow_drain_until``) plus one comparison; with both
  ``NULL`` the request path performs zero probe / lookup / select / body-walk
  calls -- the coroutine completes without suspending -- and an exhausted pool
  answers byte-identically to today's 429.
* Fail-closed (I5, I7): every fresh decline returns ``None`` so the route
  renders today's ``usage_limit_reached`` 429 unchanged (only the
  ``not_portable_history`` decline leaves a hint on ``request.state``); pinned
  and anchored contexts never fall through -- they dispatch to their source,
  answer 400 (permanent) / 503 (transient), or are released to subscription
  only after a durable delete of a provably source-free pin.
* Exactly-once claims (I13): admission claims are the last await-free step of
  the decision and are released by exactly one latch (route helper or
  ``SourceDispatch``); ``CancelledError`` is a ``BaseException`` and is never
  swallowed. Nothing after the claim can raise or suspend.
* Zero timing allowance: this module never calls ``asyncio`` timing
  primitives or ``time.monotonic()`` directly -- deadlines flow through the
  ``Scheduler`` / ``Clock`` read from the proxy service (``scheduler_for`` /
  ``clock_for``) or passed in by the caller; pin touches ride the service's
  cancel-safe cleanup tasks and never run on the request path.
"""

from __future__ import annotations

import logging
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, get_args

from fastapi.responses import JSONResponse

from app.core.clients.proxy import _is_native_codex_request
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler, clock_for, scheduler_for
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.metrics.prometheus import model_source_breaker_state, subscription_overflow_total
from app.core.utils.request_id import ensure_request_id
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.api_keys.service import TRAFFIC_CLASS_OPPORTUNISTIC, ApiKeyData
from app.modules.model_sources.catalog import source_model_supported_tool_types, source_model_supports_vision
from app.modules.model_sources.projection import (
    Declined,
    PortabilityView,
    overflow_portability_view,
    strip_source_telemetry,
)
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.model_sources.selection import select_overflow_model_source
from app.modules.proxy._load_balancer.exhaustion_probe import probe_pool_usage_exhaustion
from app.modules.proxy.affinity import _codex_backend_identity, _CodexBackendIdentity
from app.modules.proxy.model_source_pins import (
    PIN_KIND_THREAD,
    PIN_TOUCH_INTERVAL_SECONDS,
    ModelSourcePinRepository,
    PinCache,
    PinIntent,
    PinLookupState,
    PinLookupTimeout,
    PinRecord,
    PinWrite,
    PinWriteExecutor,
    PinWriteOutcome,
    anchor_pin_key,
    bounce_pin_key,
    drain_deadline_from_settings,
    get_pin_cache,
    lookup_pin_bounded,
    lookup_pins_bounded,
    thread_pin_key,
)
from app.modules.proxy.replay_safety import (
    STATELESS_DECLARABLE_TOOL_TYPES,
    input_carries_image_parts,
    is_binding_turn_state,
    responses_payload_is_provider_portable,
    strip_input_item_ids,
    transcript_is_source_free,
)
from app.modules.proxy.source_admission import SourceAdmission, SourceBulkhead, TrialClaim, TrialResult, try_claim

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import Response

    from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
    from app.core.types import JsonValue
    from app.db.models import DashboardSettings, ModelSource
    from app.dependencies import ProxyContext
    from app.modules.proxy.load_balancer import AccountSelection
    from app.modules.proxy.source_dispatch import DispatchStatus, SourceDispatch

__all__ = [
    "BACKGROUND_ALLOWLIST",
    "BREAKER_FAILURE_THRESHOLD",
    "BREAKER_OPEN_SECONDS",
    "BREAKER_STATE_METRIC",
    "BreakerState",
    "BreakerToken",
    "ClaimDenied",
    "ClaimDeniedReason",
    "DISPATCH_KIND_ANCHOR",
    "DISPATCH_KIND_FRESH",
    "DISPATCH_KIND_PINNED",
    "DeclineReason",
    "DispatchKind",
    "FastDeclineSet",
    "HANDSHAKE_DENIAL_CODE",
    "HINT_HEADER",
    "HINT_NATIVE_TEXT",
    "HINT_SDK_SENTENCE",
    "HINT_STATE_ATTRIBUTE",
    "MEMGEN_HEADER",
    "MODEL_SOURCE_BUSY_CODE",
    "MODEL_SOURCE_UNAVAILABLE_CODE",
    "OVERFLOW_OUTCOMES",
    "OverflowFinishedHook",
    "OVERFLOW_TOTAL_METRIC",
    "OverflowDispatch",
    "OverflowPinExecutor",
    "PIN_FAILURE_FAST_DECLINE_SECONDS",
    "PIN_UNAVAILABLE_CODE",
    "PIN_UNVERIFIED_CODE",
    "PinToucher",
    "REQUEST_LOG_SOURCE_FRESH",
    "REQUEST_LOG_SOURCE_PINNED",
    "RETRY_AFTER_SECONDS",
    "ROUTE_CODEX_RESPONSES",
    "ROUTE_COMPACT",
    "ROUTE_V1_RESPONSES",
    "ROUTE_WEBSOCKET",
    "ROUTE_WEBSOCKET_HANDSHAKE",
    "SOURCE_UNAVAILABLE_CODE",
    "SUBAGENT_HEADER",
    "SourceBreaker",
    "TRIAL_LEASE_TTL_SECONDS",
    "UNSUPPORTED_INPUT_CODE",
    "WS_BOUNCE_CODE",
    "anchor_requested",
    "apply_usage_limit_hint",
    "background_job_allowed",
    "client_store_intent",
    "compact_pin_denial",
    "fresh_decline_reason",
    "get_fast_decline_set",
    "get_overflow_pin_executor",
    "get_pin_toucher",
    "get_source_breaker",
    "handshake_denial",
    "overflow_thread_key",
    "portability_decline",
    "pin_commit_outcome_label",
    "record_overflow_outcome",
    "record_overflow_transport_decision",
    "resolve_subscription_overflow",
    "restore_client_store",
    "try_claim_overflow",
]

logger = logging.getLogger(__name__)

# -- routes (``route`` label of ``codex_lb_subscription_overflow_total``) ----------------------

ROUTE_CODEX_RESPONSES = "codex_responses"
ROUTE_V1_RESPONSES = "v1_responses"
ROUTE_WEBSOCKET_HANDSHAKE = "websocket_handshake"
ROUTE_WEBSOCKET = "websocket"
ROUTE_COMPACT = "compact"

# -- request-log ``source`` values and dispatch kinds --------------------------------------------

REQUEST_LOG_SOURCE_FRESH = "subscription_overflow"
# Anchor dispatches (SDK ``previous_response_id`` chains) share the pinned label.
REQUEST_LOG_SOURCE_PINNED = "subscription_overflow_pinned"

DispatchKind = Literal["fresh", "pinned", "anchor"]
DISPATCH_KIND_FRESH: DispatchKind = "fresh"
DISPATCH_KIND_PINNED: DispatchKind = "pinned"
DISPATCH_KIND_ANCHOR: DispatchKind = "anchor"

# -- error codes (design §7.4) -----------------------------------------------------------------

# 503: pin commit did not land / could not be verified before the first content frame.
PIN_UNAVAILABLE_CODE = "subscription_overflow_pin_unavailable"
PIN_UNVERIFIED_CODE = "subscription_overflow_pin_unverified"
# 400: pinned conversation whose source is unservable and whose transcript is not source-free.
SOURCE_UNAVAILABLE_CODE = "subscription_overflow_source_unavailable"
# 400: tombstoned/pinned conversation input the source cannot take (incl. compaction, CP-10).
UNSUPPORTED_INPUT_CODE = "subscription_overflow_unsupported_input"
# 426: WebSocket handshake denied on live pin / tombstone / bounce evidence.
HANDSHAKE_DENIAL_CODE = "subscription_overflow_requires_http_transport"
# 503 in-band WS bounce (existing code; never ``server_is_overloaded``/``slow_down``).
WS_BOUNCE_CODE = "model_source_requires_http_transport"
# 503 transient: breaker open, lookup timeout, infrastructure failure on a pinned/anchored context.
MODEL_SOURCE_UNAVAILABLE_CODE = "model_source_unavailable"
# 503 transient: bulkhead saturated.
MODEL_SOURCE_BUSY_CODE = "model_source_busy"

RETRY_AFTER_SECONDS = 2
BUSY_RETRY_AFTER_SECONDS = 1

# Client-visible wording (design §7.2, CP-10). Source names/ids never appear here.
SOURCE_UNAVAILABLE_MESSAGE = (
    "This conversation was served by an overflow model source that is no longer available; start a new conversation."
)
PIN_EXPIRED_MESSAGE = (
    "This conversation's overflow model-source pin has expired and it cannot be resumed; start a new conversation."
)
UNSUPPORTED_INPUT_MESSAGE = (
    "This conversation is being served by the overflow model source, which cannot take file references or "
    "compaction yet; start a new conversation."
)
# 400 variant (P16): an ``input_image`` on a pinned/anchored conversation whose source model lacks vision.
UNSUPPORTED_VISION_MESSAGE = (
    "This conversation is being served by the overflow model source, whose model cannot take images; "
    "remove the image or start a new conversation."
)
COMPACT_DENIAL_MESSAGE = (
    "This conversation is being served by the overflow model source and cannot be compacted there yet; "
    "start a new conversation."
)
MODEL_SOURCE_UNAVAILABLE_MESSAGE = "The overflow model source is temporarily unavailable; retry shortly."
MODEL_SOURCE_BUSY_MESSAGE = "The overflow model source is at capacity; retry shortly."
HANDSHAKE_DENIAL_MESSAGE = "This conversation is served over HTTP by the overflow model source; retry over HTTP."

# -- breaker / claims / fast-decline tunables ---------------------------------------------------

BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 30.0
TRIAL_LEASE_TTL_SECONDS = 120.0
PIN_FAILURE_FAST_DECLINE_SECONDS = 60.0
FAST_DECLINE_MAX_KEYS = 10_000
# Configuration-class decline WARNs are rate-limited per (source, reason).
CONFIGURATION_WARN_INTERVAL_SECONDS = 60.0

# -- ``not_portable_history`` hint (owner decision Q11) ------------------------------------------

HINT_HEADER = "x-codex-promo-message"
HINT_NATIVE_TEXT = "Start a new conversation to continue on the configured overflow model source"
HINT_SDK_SENTENCE = (
    "This conversation cannot be moved to the configured overflow model source because it contains "
    "prior reasoning; a new conversation can be served by it."
)
# ``request.state`` attribute set only by the ``not_portable_history`` decline.
HINT_STATE_ATTRIBUTE = "subscription_overflow_hint"

# -- background-job allowlist (owner decision Q8) ------------------------------------------------

BACKGROUND_ALLOWLIST = frozenset({"review", "compact", "collab_spawn"})
SUBAGENT_HEADER = "x-openai-subagent"
MEMGEN_HEADER = "x-openai-memgen-request"

# -- metric names (must agree verbatim with the observability spec delta) -----------------------

OVERFLOW_TOTAL_METRIC = "codex_lb_subscription_overflow_total"
BREAKER_STATE_METRIC = "codex_lb_model_source_breaker_state"

DeclineReason = Literal[
    "pin_commit_recent_failure",
    "turn_state_bound",
    "opportunistic",
    "key_scope",
    "no_thread_key",
    "background_job",
    "source_excluded",
    "breaker_open",
    "drain_mode",
    "no_source",
    "model_unlisted",
    "not_portable_history",
    "not_portable_input",
    "source_busy",
]

# Declines a new conversation would reproduce identically: WARN (rate-limited) with the
# offending type/field, never a hint (design §8.7).
_CONFIGURATION_DECLINES: frozenset[str] = frozenset({"no_source", "model_unlisted", "not_portable_input"})

# Closed ``outcome`` enum of ``codex_lb_subscription_overflow_total`` (design §11). Pinned and
# anchored contexts share the ``pinned_*`` outcomes.
OVERFLOW_OUTCOMES: frozenset[str] = frozenset(
    {
        "dispatched_fresh",
        "dispatched_pinned",
        "dispatched_anchor",
        "bounced_ws_handshake",
        "bounced_ws_event",
        "pinned_unservable_source_disabled",
        "pinned_unservable_source_deleted",
        "pinned_unservable_model_unlisted",
        "pinned_unservable_tombstone",
        "pinned_unsupported_input",
        "pinned_breaker_open",
        "pinned_busy",
        "pinned_released_neutral",
        "pinned_release_failed",
        "pinned_lookup_timeout",
        "pin_commit_failed",
        "pin_commit_unverified",
        "decision_error",
    }
    | {f"declined_{reason}" for reason in get_args(DeclineReason)}
)

# Why a pinned/anchored source can no longer serve the conversation (design §7.2).
UnservableCause = Literal["source_deleted", "source_disabled", "model_unlisted", "key_scope", "tombstone"]
_UNSERVABLE_OUTCOME: Mapping[str, str] = {
    "source_deleted": "pinned_unservable_source_deleted",
    "source_disabled": "pinned_unservable_source_disabled",
    "model_unlisted": "pinned_unservable_model_unlisted",
    # A scoped key that cannot reach the pinned source: for the closed enum the model is unlisted for it.
    "key_scope": "pinned_unservable_model_unlisted",
    "tombstone": "pinned_unservable_tombstone",
}
_PIN_EVIDENCE: frozenset[PinLookupState] = frozenset({"live", "expired"})
_HANDSHAKE_EVIDENCE: frozenset[PinLookupState] = frozenset({"live", "expired", "bounce"})


def record_overflow_outcome(route: str, outcome: str) -> None:
    """``codex_lb_subscription_overflow_total{route,outcome}`` += 1; an outcome outside the closed enum is a bug."""

    if outcome not in OVERFLOW_OUTCOMES:
        logger.error("subscription_overflow_metric_outcome_unknown route=%s outcome=%s", route, outcome)
        return
    if subscription_overflow_total is None:
        return
    try:
        subscription_overflow_total.labels(route=route, outcome=outcome).inc()
    except Exception:  # metrics never break the request path
        logger.debug("subscription_overflow_metric_failed route=%s outcome=%s", route, outcome, exc_info=True)


# -- breaker ------------------------------------------------------------------------------------

BreakerState = Literal["closed", "open", "half_open"]
_BREAKER_GAUGE_VALUE: Mapping[BreakerState, int] = {"closed": 0, "open": 1, "half_open": 2}
TokenKind = Literal["closed", "trial"]


class BreakerToken(TrialClaim):
    """Lease handed to one overflow dispatch; ``settle`` is told the outcome exactly once.

    Issued in ``closed`` (records failures toward the threshold) and
    ``half_open`` (the single leased trial); never in ``open``. A trial token
    concludes at the dispatch's first output item (``observe_first_output_item``
    closes the breaker, design §8.3) and then counts like a closed-state token
    at its terminal ``settle``, so a stream that stalls after its first item is
    still a counted failure.
    """

    __slots__ = ("_breaker", "_issued_at", "_settled", "kind", "source_id")

    def __init__(self, breaker: SourceBreaker, source_id: str, *, issued_at: float, kind: TokenKind = "closed") -> None:
        self._breaker = breaker
        self.source_id = source_id
        self._issued_at = issued_at
        self._settled = False
        self.kind: TokenKind = kind

    @property
    def issued_at(self) -> float:
        return self._issued_at

    @property
    def settled(self) -> bool:
        return self._settled

    def observe_first_output_item(self) -> None:
        """The dispatch yielded its first output item: a half-open trial succeeded and the breaker closes now.

        The token stays unsettled and becomes a ``closed``-state token, so the
        terminal ``settle`` still counts a failure later in the same stream
        toward the threshold. A ``closed``-issued token is unaffected -- its
        terminal result decides, otherwise three item-then-stall streams could
        never open the breaker -- and a superseded lease stays superseded.
        """

        if self._settled or self.kind != "trial":
            return
        if self._breaker._trial_succeeded(self):
            self.kind = "closed"

    def settle(self, result: TrialResult) -> None:
        if self._settled:
            return
        self._settled = True
        self._breaker._settle(self, result)


@dataclass(slots=True)
class _BreakerEntry:
    failures: int = 0
    opened_at: float | None = None
    trial: BreakerToken | None = None


class SourceBreaker:
    """Per-source failure breaker for overflow dispatches (3 failures / 30 s open / leased half-open trial).

    ``closed``: every dispatch gets a token; ``BREAKER_FAILURE_THRESHOLD``
    consecutive counted failures open the breaker for ``BREAKER_OPEN_SECONDS``.
    ``open``: no token (fresh declines ``breaker_open``, pinned/anchored answer
    503). ``half_open``: one leased trial token at a time
    (``TRIAL_LEASE_TTL_SECONDS`` backstop for a lost claim); its first output
    item closes the breaker at once (the slot stays with the dispatch owner),
    a terminal ``success`` closes, ``failure`` re-opens, ``inconclusive`` (a
    decline, a client cancel before the first item, a pin failure) releases
    the trial so the next request becomes it (CL-2). A token that no longer describes the current
    state (a closed-issued token settled after the trip, a superseded lease)
    is ignored. Counts only overflow dispatches; direct routing keeps
    ``try_claim(source)`` without a token (documented deviation).

    ``clock`` is read only when a token settles (the opening instant); callers
    pass ``now`` explicitly everywhere else so the decision reads one clock.
    """

    def __init__(
        self,
        *,
        clock: Clock = REAL_CLOCK,
        failure_threshold: int = BREAKER_FAILURE_THRESHOLD,
        open_seconds: float = BREAKER_OPEN_SECONDS,
        trial_lease_seconds: float = TRIAL_LEASE_TTL_SECONDS,
    ) -> None:
        if failure_threshold <= 0 or open_seconds <= 0 or trial_lease_seconds <= 0:
            raise ValueError("breaker tunables must be positive")
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._trial_lease_seconds = trial_lease_seconds
        self._entries: dict[str, _BreakerEntry] = {}

    def state(self, source_id: str, now: float) -> BreakerState:
        entry = self._entries.get(source_id)
        if entry is None or entry.opened_at is None:
            return "closed"
        return "open" if now - entry.opened_at < self._open_seconds else "half_open"

    def is_open(self, source_id: str, now: float) -> bool:
        return self.state(source_id, now) == "open"

    def failures(self, source_id: str) -> int:
        entry = self._entries.get(source_id)
        return 0 if entry is None else entry.failures

    def claim(self, source_id: str, now: float) -> BreakerToken | None:
        """Token in ``closed``/``half_open``; ``None`` in ``open`` or while the half-open trial lease is fresh."""

        state = self.state(source_id, now)
        self._publish(source_id, state)
        if state == "open":
            return None
        entry = self._entries.setdefault(source_id, _BreakerEntry())
        if state == "closed":
            return BreakerToken(self, source_id, issued_at=now, kind="closed")
        trial = entry.trial
        if trial is not None and not trial.settled:
            if now - trial.issued_at < self._trial_lease_seconds:
                return None
            logger.warning(
                "model_source_breaker state=half_open trial=lease_expired source_id=%s lease_seconds=%.0f",
                source_id,
                self._trial_lease_seconds,
            )
        token = BreakerToken(self, source_id, issued_at=now, kind="trial")
        entry.trial = token
        return token

    def _trial_succeeded(self, token: BreakerToken) -> bool:
        """The half-open trial yielded its first output item: close now (§8.3); ``False`` for a superseded lease."""

        entry = self._entries.get(token.source_id)
        if entry is None or entry.trial is not token:
            return False
        self._close(entry, token.source_id, cause="trial_first_output_item")
        return True

    def _settle(self, token: BreakerToken, result: TrialResult) -> None:
        entry = self._entries.get(token.source_id)
        if entry is None:
            return
        now = self._clock.monotonic()
        if token.kind == "trial":
            if entry.trial is not token:
                return  # superseded lease: the breaker moved on without this token
            entry.trial = None
            if result == "success":
                self._close(entry, token.source_id, cause="trial_succeeded")
            elif result == "failure":
                self._open(entry, token.source_id, now, cause="trial_failed")
            return
        if entry.opened_at is not None:
            return  # tripped since this closed-state token was issued; its outcome predates the trip
        if result == "success":
            entry.failures = 0
        elif result == "failure":
            entry.failures += 1
            if entry.failures >= self._failure_threshold:
                self._open(entry, token.source_id, now, cause="failure_threshold")

    def _open(self, entry: _BreakerEntry, source_id: str, now: float, *, cause: str) -> None:
        entry.opened_at = now
        entry.trial = None
        logger.warning(
            "model_source_breaker state=open source_id=%s failures=%d cause=%s open_seconds=%.0f",
            source_id,
            entry.failures,
            cause,
            self._open_seconds,
        )
        self._publish(source_id, "open")

    def _close(self, entry: _BreakerEntry, source_id: str, *, cause: str) -> None:
        entry.failures = 0
        entry.opened_at = None
        entry.trial = None
        logger.warning("model_source_breaker state=closed source_id=%s cause=%s", source_id, cause)
        self._publish(source_id, "closed")

    @staticmethod
    def _publish(source_id: str, state: BreakerState) -> None:
        if model_source_breaker_state is None:
            return
        try:
            model_source_breaker_state.labels(source_id=source_id).set(_BREAKER_GAUGE_VALUE[state])
        except Exception:  # metrics never break the request path
            logger.debug("model_source_breaker_gauge_failed source_id=%s", source_id, exc_info=True)


_BREAKER: SourceBreaker | None = None


def get_source_breaker() -> SourceBreaker:
    """Process-wide breaker instance (one per replica worker)."""

    global _BREAKER
    if _BREAKER is None:
        _BREAKER = SourceBreaker()
    return _BREAKER


# -- admission claims ---------------------------------------------------------------------------

ClaimDeniedReason = Literal["source_busy", "breaker_open"]


@dataclass(frozen=True, slots=True)
class ClaimDenied:
    reason: ClaimDeniedReason


def try_claim_overflow(
    source: ModelSource,
    *,
    breaker: SourceBreaker,
    now: float,
    bulkhead: SourceBulkhead | None = None,
) -> SourceAdmission | ClaimDenied:
    """``try_claim(source, bulkhead=..., trial=breaker.claim(...))``; the token is released with the slot on denial.

    Synchronous and the last step of every dispatch decision: nothing between
    the claim and the caller's return can suspend or raise (I13).
    """

    token = breaker.claim(source.id, now)
    if token is None:
        return ClaimDenied("breaker_open")
    claims = try_claim(source, bulkhead=bulkhead, trial=token)
    if claims is None:
        # A saturated source never settles the trial: release it here so the
        # next request can take it (CL-2).
        token.settle("inconclusive")
        return ClaimDenied("source_busy")
    return claims


# -- fast decline after a pin-commit failure ----------------------------------------------------


class FastDeclineSet:
    """Thread keys declined for ``PIN_FAILURE_FAST_DECLINE_SECONDS`` after a non-``written`` pin commit.

    Lazy eviction, bounded to ``FAST_DECLINE_MAX_KEYS`` entries (expired first,
    then the earliest deadline).
    """

    def __init__(self, *, ttl_seconds: float = PIN_FAILURE_FAST_DECLINE_SECONDS, max_keys: int = FAST_DECLINE_MAX_KEYS):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_keys = max_keys
        self._until: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self._until)

    def mark(self, thread_key: str, now: float) -> None:
        if thread_key not in self._until and len(self._until) >= self._max_keys:
            self._evict(now)
        self._until[thread_key] = now + self._ttl_seconds

    def contains(self, thread_key: str, now: float) -> bool:
        until = self._until.get(thread_key)
        if until is None:
            return False
        if now >= until:
            del self._until[thread_key]
            return False
        return True

    def _evict(self, now: float) -> None:
        for key in [key for key, until in self._until.items() if now >= until]:
            del self._until[key]
        while len(self._until) >= self._max_keys:
            del self._until[min(self._until, key=self._until.__getitem__)]


_FAST_DECLINE: FastDeclineSet | None = None


def get_fast_decline_set() -> FastDeclineSet:
    """Process-wide fast-decline set shared by the decision and the overflow pin executor."""

    global _FAST_DECLINE
    if _FAST_DECLINE is None:
        _FAST_DECLINE = FastDeclineSet()
    return _FAST_DECLINE


class OverflowPinExecutor(PinWriteExecutor):
    """``PinWriteExecutor`` whose ``commit`` fast-declines ``intent.thread_key`` on any non-``written`` outcome.

    A deferred caller cancellation the base re-raises after the outcome was
    logged marks too: the client left, so a 60 s fresh decline on that thread
    is the conservative answer to an outcome the caller never saw. Bounce rows
    use the base executor (no fast-decline mark).
    """

    def __init__(self, *, fast_decline: FastDeclineSet, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fast_decline = fast_decline

    @property
    def fast_decline(self) -> FastDeclineSet:
        return self._fast_decline

    async def commit(
        self,
        intent: PinIntent,
        *,
        drain_until: datetime | None,
        scheduler: Scheduler = REAL_SCHEDULER,
        clock: Clock = REAL_CLOCK,
    ) -> PinWriteOutcome:
        try:
            outcome = await super().commit(intent, drain_until=drain_until, scheduler=scheduler, clock=clock)
        except BaseException:
            if intent.thread_key is not None:
                self._fast_decline.mark(intent.thread_key, clock.monotonic())
            raise
        if outcome != "written" and intent.thread_key is not None:
            self._fast_decline.mark(intent.thread_key, clock.monotonic())
        return outcome


_PIN_EXECUTOR: OverflowPinExecutor | None = None


def get_overflow_pin_executor() -> OverflowPinExecutor:
    """Process-wide overflow pin executor sharing the process fast-decline set."""

    global _PIN_EXECUTOR
    if _PIN_EXECUTOR is None:
        _PIN_EXECUTOR = OverflowPinExecutor(fast_decline=get_fast_decline_set())
    return _PIN_EXECUTOR


# -- pin touch (never on the request path) -----------------------------------------------------


class CleanupScheduler(Protocol):
    """``ProxyService._schedule_cancel_safe_cleanup``: tracked, cancel-safe background tasks."""

    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> object: ...


def _cleanup_scheduler_for(service: object) -> CleanupScheduler | None:
    if callable(getattr(service, "_schedule_cancel_safe_cleanup", None)):
        return cast(CleanupScheduler, service)
    return None


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _touch_pin(pin_key: str, *, now: datetime, drain_until: datetime | None) -> None:
    async with sqlite_writer_section():
        async with get_background_session() as session:
            touched = await ModelSourcePinRepository(session).touch(pin_key, now=now, drain_until=drain_until)
            await session.commit()
    logger.debug("model_source_pin_touch touched=%s kind=%s", touched, pin_key.split("\n", 1)[0])


class PinToucher:
    """Schedules a background ``ModelSourcePinRepository.touch`` once ``last_seen_at`` is older than the touch interval.

    Never awaited on the request path: the touch goes through
    ``_schedule_cancel_safe_cleanup``. A per-replica memo keeps a pin served
    from the positive cache (whose record still shows the old
    ``last_seen_at``) from being touched on every turn within the interval.
    """

    def __init__(self, *, interval_seconds: float = PIN_TOUCH_INTERVAL_SECONDS, max_keys: int = FAST_DECLINE_MAX_KEYS):
        if interval_seconds <= 0 or max_keys <= 0:
            raise ValueError("touch interval and bound must be positive")
        self._interval = timedelta(seconds=interval_seconds)
        self._max_keys = max_keys
        self._scheduled_at: dict[str, datetime] = {}

    def maybe_touch(
        self,
        record: PinRecord,
        *,
        cleanup_scheduler: CleanupScheduler | None,
        drain_until: datetime | None,
        now: datetime,
    ) -> bool:
        """``True`` when a touch was scheduled."""

        if cleanup_scheduler is None:
            return False
        now = _aware_utc(now)
        if now - record.last_seen_at < self._interval:
            return False
        scheduled_at = self._scheduled_at.get(record.pin_key)
        if scheduled_at is not None and now - scheduled_at < self._interval:
            return False
        if record.pin_key not in self._scheduled_at and len(self._scheduled_at) >= self._max_keys:
            for key in [key for key, at in self._scheduled_at.items() if now - at >= self._interval]:
                del self._scheduled_at[key]
            while len(self._scheduled_at) >= self._max_keys:
                del self._scheduled_at[min(self._scheduled_at, key=self._scheduled_at.__getitem__)]
        self._scheduled_at[record.pin_key] = now
        cleanup_scheduler._schedule_cancel_safe_cleanup(
            _touch_pin(record.pin_key, now=now, drain_until=drain_until),
            action="model_source_pin_touch",
            request_id=ensure_request_id(),
        )
        return True


_PIN_TOUCHER: PinToucher | None = None


def get_pin_toucher() -> PinToucher:
    """Process-wide pin toucher (one per replica worker)."""

    global _PIN_TOUCHER
    if _PIN_TOUCHER is None:
        _PIN_TOUCHER = PinToucher()
    return _PIN_TOUCHER


# -- decision result ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverflowDispatch:
    """Everything the route helper needs to dispatch one overflow attempt to a source."""

    kind: DispatchKind
    source: ModelSource
    model: str
    thread_key: str | None
    body: dict[str, JsonValue]
    claims: SourceAdmission
    resets_at: int | None
    selection: AccountSelection | None
    route: str
    drain_until: datetime | None
    pin_intent: PinIntent
    pin_executor: PinWriteExecutor
    request_log_source: str

    def owner_kwargs(self) -> dict[str, Any]:
        """Exactly the ``SourceDispatch`` kwargs the route helper splats: ``request_log_source``, ``dispatch_kind``,
        ``pin_intent``, ``pin_executor``, ``drain_until``, ``pin_failure_error_code``, ``pin_unverified_error_code``,
        ``on_finished`` (the route-bound ``OverflowFinishedHook``)."""

        return {
            "request_log_source": self.request_log_source,
            "dispatch_kind": self.kind,
            "pin_intent": self.pin_intent,
            "pin_executor": self.pin_executor,
            "drain_until": self.drain_until,
            "pin_failure_error_code": PIN_UNAVAILABLE_CODE,
            "pin_unverified_error_code": PIN_UNVERIFIED_CODE,
            "on_finished": OverflowFinishedHook(self.route),
        }


# -- shared building blocks -----------------------------------------------------------------------


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header read for plain dictionaries and Starlette ``Headers`` alike."""

    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def client_store_intent(payload: ResponsesRequest) -> bool | None:
    """The client's own ``store`` value: ``True``/``False`` when it sent one, ``None`` when it omitted the field.

    ``ResponsesRequest.store`` is forced to ``False`` by its validator for the
    ChatGPT backend; ``normalize_responses_request_payload`` keeps the client's
    value in ``_codex_lb_client_store``. A payload validated elsewhere (no
    capture) falls back to ``model_fields_set``: a client-sent value the
    validator collapsed can only be trusted as ``False`` (Codex's
    ``store: false``), an absent field is the API default.
    """

    captured = payload._codex_lb_client_store
    if captured is not None:
        return captured
    return False if "store" in payload.model_fields_set else None


def anchor_requested(payload: ResponsesRequest) -> bool:
    """Whether the client left storage on, i.e. did not send ``store: false`` (design §7.2: anchor iff so).

    Codex always sends ``store: false`` (never anchored); SDK clients that omit
    ``store`` or send ``store: true`` keep their responses stored at the source
    (anchored, so a ``previous_response_id`` follow-up returns to it).
    """

    return client_store_intent(payload) is not False


def restore_client_store(body: dict[str, JsonValue], payload: ResponsesRequest) -> dict[str, JsonValue]:
    """Put the client's own ``store`` back on the source-direction body, in place; returns ``body``.

    The forwarding dump carries the ``store: false`` the ChatGPT validator
    forced. A source honours the client's storage intent instead: the field is
    dropped when the client omitted it (the source applies its default, which
    is what an SDK ``previous_response_id`` chain relies on) and carried
    verbatim when the client sent it. Direct source routing is untouched.
    """

    intent = client_store_intent(payload)
    if intent is None:
        body.pop("store", None)
    else:
        body["store"] = intent
    return body


def _settings_off(settings: DashboardSettings, clock: Clock) -> tuple[bool, datetime | None]:
    """The ship-dark fast path: ``(both columns off, aware drain deadline)`` from two attribute reads."""

    designated = settings.subscription_overflow_source_id
    drain_until_raw = settings.subscription_overflow_drain_until
    if designated is None and drain_until_raw is None:
        return True, None
    drain_until = drain_deadline_from_settings(settings)
    if designated is None and drain_until is not None and clock.now() >= drain_until:
        return True, drain_until
    return False, drain_until


def _permanent_denial(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content=openai_error(code, message, error_type="invalid_request_error"))


def _transient_denial(code: str, message: str, retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=openai_error(code, message, error_type="upstream_error"),
        headers={"Retry-After": str(retry_after)},
    )


def _handshake_denial_response() -> JSONResponse:
    return JSONResponse(
        status_code=426,
        content=openai_error(HANDSHAKE_DENIAL_CODE, HANDSHAKE_DENIAL_MESSAGE, error_type="server_error"),
    )


def _source_body(payload: ResponsesRequest) -> dict[str, JsonValue]:
    """The overflow source body: the forwarding dump with telemetry and ``service_tier`` stripped (§4.6, P9)
    and the client's ``store`` restored (the route helper shapes the wire body the same way)."""

    return restore_client_store(
        strip_source_telemetry(payload.model_dump_for_forwarding(), strip_service_tier=True), payload
    )


_warned_at: dict[tuple[str, str], float] = {}


def _decline(
    route: str,
    reason: DeclineReason,
    *,
    detail: str | None = None,
    source_id: str | None = None,
    now: float = 0.0,
) -> None:
    """Count and log a fresh decline; the caller returns ``None`` so the route renders today's 429."""

    record_overflow_outcome(route, f"declined_{reason}")
    if reason in _CONFIGURATION_DECLINES:
        key = (source_id or "-", reason)
        last = _warned_at.get(key)
        if last is None or now - last >= CONFIGURATION_WARN_INTERVAL_SECONDS:
            _warned_at[key] = now
            logger.warning(
                "subscription_overflow_declined reason=%s detail=%s source_id=%s route=%s (configuration)",
                reason,
                detail,
                source_id,
                route,
            )
            return
    logger.debug("subscription_overflow_declined reason=%s detail=%s route=%s", reason, detail, route)


# -- entry points (api.py hunks) ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Decision:
    """One route-admission decision: the request, its settings snapshot and the collaborators."""

    request: Request
    payload: ResponsesRequest
    service: Any
    api_key: ApiKeyData | None
    raw_model: str | None
    require_streaming: bool
    source_route_excluded: bool
    route: str
    settings: DashboardSettings
    drain_until: datetime | None
    thread_key: str | None
    scheduler: Scheduler
    clock: Clock
    breaker: SourceBreaker
    fast_decline: FastDeclineSet
    pin_executor: OverflowPinExecutor
    cache: PinCache
    toucher: PinToucher

    @property
    def api_key_id(self) -> str | None:
        return self.api_key.id if self.api_key is not None else None

    @property
    def headers(self) -> Mapping[str, str]:
        return self.request.headers


async def resolve_subscription_overflow(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None,
    require_streaming: bool,
    source_route_excluded: bool,
    route: str,
    direct_source: ModelSource | None = None,
) -> OverflowDispatch | Response | None:
    """Decide once at route admission (design §4.2 order).

    ``None``: fall through to the unchanged path (both settings columns off,
    a decline, or a directly owned model without pin evidence). ``Response``:
    a fail-closed answer for a pinned/anchored context. ``OverflowDispatch``:
    dispatch to the source with claims already held (released by the route
    helper latch when no owner takes them).

    Order: thread pin (durable knowledge beats a turn-state claim) -> anchor
    (also in drain mode) -> drain check -> O(1) declines -> probe with the
    step-1 settings snapshot -> source/model -> strip/view/verdict -> claims
    (last; nothing fallible after them). ``except Exception`` (never
    ``CancelledError``) falls through except on a pinned/anchored context or a
    failed mandated lookup, which answer 503 (§8.1).

    ``direct_source`` is the source direct routing selected for the model
    (``None`` on the subscription path). The route calls the decision before
    the direct dispatch because a pin or anchor owns the conversation
    regardless of which source serves the model directly (I7, design §7.2):
    a live pin or anchor is dispatched by the pinned rules -- to the pinned
    source when it serves the model, otherwise the unservable rules (neutral
    release, after which the caller's direct route serves the id-stripped
    body, or 400) -- and without evidence the decision returns ``None`` right
    after the lookups: a model a source serves directly is that source's
    request, never a subscription exhaustion, so nothing is probed, selected
    or counted.
    """

    settings = await get_settings_cache().get()
    off, drain_until = _settings_off(settings, clock_for(context.service))
    if off:
        return None
    service = context.service
    decision = _Decision(
        request=request,
        payload=payload,
        service=service,
        api_key=api_key,
        raw_model=raw_model,
        require_streaming=require_streaming,
        source_route_excluded=source_route_excluded,
        route=route,
        settings=settings,
        drain_until=drain_until,
        thread_key=overflow_thread_key(request.headers),
        scheduler=scheduler_for(service),
        clock=clock_for(service),
        breaker=get_source_breaker(),
        fast_decline=get_fast_decline_set(),
        pin_executor=get_overflow_pin_executor(),
        cache=get_pin_cache(),
        toucher=get_pin_toucher(),
    )
    designated = settings.subscription_overflow_source_id
    previous_response_id = payload.previous_response_id
    stage = "pin_lookup"
    pinned_context = False
    try:
        if decision.thread_key is not None:
            pin = await lookup_pin_bounded(
                thread_pin_key(decision.thread_key),
                cache=decision.cache,
                scheduler=decision.scheduler,
                clock=decision.clock,
            )
            if pin.state in _PIN_EVIDENCE and pin.record is not None:
                pinned_context = True
                stage = "dispatch_pinned"
                return await dispatch_pinned(decision, pin.record, pin.state)
        if previous_response_id is not None:
            stage = "anchor_lookup"
            anchor = await lookup_pin_bounded(
                anchor_pin_key(decision.api_key_id, previous_response_id),
                cache=decision.cache,
                scheduler=decision.scheduler,
                clock=decision.clock,
            )
            if anchor.state in _PIN_EVIDENCE and anchor.record is not None:
                pinned_context = True
                stage = "dispatch_anchor"
                return await dispatch_anchor(decision, anchor.record, anchor.state)
            if direct_source is None:
                # Unknown id: the continuation lives elsewhere -- today's owner
                # fail-closed path answers unchanged (design §4.2 (3)); never the
                # fresh pipeline, so no probe, no select and no history hint.
                _decline(route, "not_portable_history", detail="previous_response_id:unanchored")
                return None
        if direct_source is not None:
            # No pin or anchor evidence: the model's own source serves it. Not
            # an exhaustion event, so no drain decline, no probe, no outcome.
            return None
        if designated is None:
            _decline(route, "drain_mode")
            return None
        stage = "fresh"
        return await _resolve_fresh(decision, designated)
    except PinLookupTimeout:
        record_overflow_outcome(route, "pinned_lookup_timeout")
        logger.warning(
            "subscription_overflow_pinned_unservable cause=lookup_timeout stage=%s route=%s request_id=%s",
            stage,
            route,
            ensure_request_id(),
        )
        return _transient_denial(MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS)
    except Exception:
        record_overflow_outcome(route, "decision_error")
        logger.warning(
            "subscription_overflow_decision_error stage=%s route=%s request_id=%s",
            stage,
            route,
            ensure_request_id(),
            exc_info=True,
        )
        if pinned_context or stage in ("pin_lookup", "anchor_lookup"):
            # A pin/anchor was found, or a mandated lookup failed while the
            # request carries a thread key / previous_response_id: falling
            # through could hand source ciphertext to a subscription account.
            logger.warning(
                "subscription_overflow_pinned_unservable cause=decision_error stage=%s route=%s", stage, route
            )
            return _transient_denial(
                MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS
            )
        return None


async def _resolve_fresh(decision: _Decision, designated: str) -> OverflowDispatch | None:
    route = decision.route
    payload = decision.payload
    monotonic_now = decision.clock.monotonic()
    reason = fresh_decline_reason(
        decision.headers,
        decision.api_key,
        thread_key=decision.thread_key,
        source_route_excluded=decision.source_route_excluded,
        fast_decline=decision.fast_decline,
        breaker=decision.breaker,
        source_id=designated,
        now=monotonic_now,
    )
    if reason is not None:
        _decline(route, reason, source_id=designated, now=monotonic_now)
        return None
    exhaustion = await probe_pool_usage_exhaustion(
        decision.service,
        settings=decision.settings,
        api_key=decision.api_key,
        model=payload.model,
        service_tier=payload.service_tier,
    )
    if exhaustion is None:
        return None
    selection = await select_overflow_model_source(
        designated,
        payload.model,
        decision.api_key,
        raw_model=decision.raw_model,
        require_streaming=decision.require_streaming,
    )
    if selection is None:
        cause = await _source_unservable_cause(designated)
        _decline(
            route,
            "model_unlisted" if cause == "model_unlisted" else "no_source",
            detail=f"{cause}:{payload.model}",
            source_id=designated,
            now=monotonic_now,
        )
        return None
    source, model = selection
    body = _source_body(payload)
    portability_reason, detail = portability_decline(body, decision.headers, source=source, model=model)
    if portability_reason is not None:
        if portability_reason == "not_portable_history":
            setattr(decision.request.state, HINT_STATE_ATTRIBUTE, "not_portable_history")
        _decline(route, portability_reason, detail=detail, source_id=source.id, now=monotonic_now)
        return None
    claims = try_claim_overflow(source, breaker=decision.breaker, now=decision.clock.monotonic())
    if isinstance(claims, ClaimDenied):
        _decline(route, claims.reason, source_id=source.id, now=monotonic_now)
        return None
    # No await and no fallible call from here to the return (I13).
    thread_key = decision.thread_key
    writes: tuple[PinWrite, ...] = ()
    if thread_key is not None:
        writes = (PinWrite(thread_pin_key(thread_key), PIN_KIND_THREAD, source.id, decision.api_key_id),)
    dispatch = OverflowDispatch(
        kind=DISPATCH_KIND_FRESH,
        source=source,
        model=model,
        thread_key=thread_key,
        body=body,
        claims=claims,
        resets_at=exhaustion.resets_at,
        selection=exhaustion.selection,
        route=route,
        drain_until=decision.drain_until,
        pin_intent=PinIntent(
            writes=writes,
            thread_key=thread_key,
            source_id=source.id,
            anchor_api_key_id=decision.api_key_id,
            anchor=anchor_requested(payload),
        ),
        pin_executor=decision.pin_executor,
        request_log_source=REQUEST_LOG_SOURCE_FRESH,
    )
    record_overflow_outcome(route, "dispatched_fresh")
    logger.info(
        "subscription_overflow_dispatched kind=fresh route=%s source_id=%s model=%s pinned=%s anchor=%s",
        route,
        source.id,
        model,
        thread_key is not None,
        dispatch.pin_intent.anchor,
    )
    return dispatch


async def _source_unservable_cause(source_id: str) -> UnservableCause:
    """Why the designated/pinned source could not resolve the model: gone, switched off, or the model unlisted."""

    async with get_background_session() as session:
        source = await ModelSourcesRepository(session).get_by_id(source_id)
        if source is None:
            return "source_deleted"
        if not source.is_enabled or not source.supports_responses or source.kind != "openai_compatible":
            return "source_disabled"
        return "model_unlisted"


async def _resolve_pinned_source(decision: _Decision, record: PinRecord) -> tuple[ModelSource, str] | UnservableCause:
    api_key = decision.api_key
    if (
        api_key is not None
        and api_key.source_assignment_scope_enabled
        and record.source_id not in api_key.assigned_source_ids
    ):
        return "key_scope"
    selection = await select_overflow_model_source(
        record.source_id,
        decision.payload.model,
        api_key,
        raw_model=decision.raw_model,
        require_streaming=decision.require_streaming,
    )
    if selection is None:
        return await _source_unservable_cause(record.source_id)
    return selection


async def _unservable_answer(
    decision: _Decision,
    record: PinRecord,
    *,
    cause: UnservableCause,
    kind: DispatchKind,
    neutral_release: bool,
) -> Response | None:
    """400 (permanent) for a pinned/anchored conversation its source can no longer serve -- after the neutral release.

    Neutral release (CP-6): the row is re-read (never the positive cache), the
    transcript must be provably source-free, the pin is deleted durably first,
    and only then is the body (item ids stripped) handed back to the
    subscription path with ``None``. A delete that did not verify answers 503
    (retryable); a row that vanished meanwhile falls through unpinned.
    """

    route = decision.route
    payload = decision.payload
    if neutral_release:
        current = await lookup_pin_bounded(
            record.pin_key, cache=None, scheduler=decision.scheduler, clock=decision.clock
        )
        if current.state == "none":
            logger.info("subscription_overflow_pin_vanished kind=%s cause=%s route=%s", kind, cause, route)
            return None
        # Steps 1-2 of the portability predicate on the id-stripped body (design §7.2):
        # the source-minted item ids are exactly what the release removes.
        body = _source_body(payload)
        stripped_input = strip_input_item_ids(payload.input) if isinstance(payload.input, list) else None
        if stripped_input is not None:
            body["input"] = stripped_input
        view = overflow_portability_view(body)
        # The release target is a subscription account, for which the stateless Codex tool
        # declarations (shell/apply_patch/local_shell/tool_search) are native, so they must
        # not make an otherwise source-free transcript unreleasable (they are exactly the
        # declarations the fresh path admitted when it pinned the thread).
        if isinstance(view, PortabilityView) and transcript_is_source_free(
            view, supported_tool_types=STATELESS_DECLARABLE_TOOL_TYPES
        ):
            outcome = await decision.pin_executor.delete_durably(
                record.pin_key, scheduler=decision.scheduler, cache=decision.cache
            )
            if outcome != "written":
                record_overflow_outcome(route, "pinned_release_failed")
                logger.warning(
                    "subscription_overflow_pinned_release_failed outcome=%s cause=%s route=%s source_id=%s",
                    outcome,
                    cause,
                    route,
                    record.source_id,
                )
                return _transient_denial(
                    MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS
                )
            if stripped_input is not None:
                payload.input = stripped_input
            record_overflow_outcome(route, "pinned_released_neutral")
            logger.warning(
                "subscription_overflow_pinned_released_neutral cause=%s route=%s source_id=%s kind=%s",
                cause,
                route,
                record.source_id,
                record.kind,
            )
            return None
    record_overflow_outcome(route, _UNSERVABLE_OUTCOME[cause])
    logger.warning(
        "subscription_overflow_pinned_unservable cause=%s kind=%s route=%s source_id=%s",
        cause,
        kind,
        route,
        record.source_id,
    )
    if cause == "tombstone":
        return _permanent_denial(UNSUPPORTED_INPUT_CODE, PIN_EXPIRED_MESSAGE)
    return _permanent_denial(SOURCE_UNAVAILABLE_CODE, SOURCE_UNAVAILABLE_MESSAGE)


def _claim_for_pinned(decision: _Decision, source: ModelSource) -> SourceAdmission | Response:
    claims = try_claim_overflow(source, breaker=decision.breaker, now=decision.clock.monotonic())
    if isinstance(claims, ClaimDenied):
        if claims.reason == "breaker_open":
            record_overflow_outcome(decision.route, "pinned_breaker_open")
            return _transient_denial(
                MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS
            )
        record_overflow_outcome(decision.route, "pinned_busy")
        return _transient_denial(MODEL_SOURCE_BUSY_CODE, MODEL_SOURCE_BUSY_MESSAGE, BUSY_RETRY_AFTER_SECONDS)
    return claims


def _pinned_unsupported(
    decision: _Decision, *, cause: str, kind: DispatchKind, source: ModelSource, message: str
) -> JSONResponse:
    """400 ``subscription_overflow_unsupported_input`` for input the pinned source cannot take; the pin is kept."""

    record_overflow_outcome(decision.route, "pinned_unsupported_input")
    logger.warning(
        "subscription_overflow_pinned_unservable cause=%s kind=%s route=%s source_id=%s",
        cause,
        kind,
        decision.route,
        source.id,
    )
    return _permanent_denial(UNSUPPORTED_INPUT_CODE, message)


async def _dispatch_bound(
    decision: _Decision,
    record: PinRecord,
    state: PinLookupState,
    *,
    kind: DispatchKind,
    neutral_release: bool,
) -> OverflowDispatch | Response | None:
    """Shared pinned/anchored dispatch: unservable -> 400 (or neutral release); excluded input or an image the
    source model cannot see -> 400 (pin kept); claims -> 503."""

    if state == "expired":
        return await _unservable_answer(decision, record, cause="tombstone", kind=kind, neutral_release=neutral_release)
    resolved = await _resolve_pinned_source(decision, record)
    if isinstance(resolved, str):
        return await _unservable_answer(decision, record, cause=resolved, kind=kind, neutral_release=neutral_release)
    source, model = resolved
    if decision.source_route_excluded:
        return _pinned_unsupported(
            decision, cause="unsupported_input", kind=kind, source=source, message=UNSUPPORTED_INPUT_MESSAGE
        )
    body = _source_body(decision.payload)
    # A pin overrides body portability except for what the source model cannot
    # see (design §7.2 P16): an ``input_image`` needs its ``supports_vision``.
    if not source_model_supports_vision(source, model) and input_carries_image_parts(body.get("input")):
        return _pinned_unsupported(
            decision, cause="unsupported_vision", kind=kind, source=source, message=UNSUPPORTED_VISION_MESSAGE
        )
    claims = _claim_for_pinned(decision, source)
    if not isinstance(claims, SourceAdmission):
        return claims
    # No await and no fallible call from here to the return (I13).
    dispatch = OverflowDispatch(
        kind=kind,
        source=source,
        model=model,
        thread_key=decision.thread_key,
        body=body,
        claims=claims,
        resets_at=None,
        selection=None,
        route=decision.route,
        drain_until=decision.drain_until,
        pin_intent=PinIntent(
            writes=(),
            thread_key=decision.thread_key,
            source_id=source.id,
            anchor_api_key_id=decision.api_key_id,
            anchor=anchor_requested(decision.payload),
        ),
        pin_executor=decision.pin_executor,
        request_log_source=REQUEST_LOG_SOURCE_PINNED,
    )
    decision.toucher.maybe_touch(
        record,
        cleanup_scheduler=_cleanup_scheduler_for(decision.service),
        drain_until=decision.drain_until,
        now=decision.clock.now(),
    )
    record_overflow_outcome(decision.route, f"dispatched_{kind}")
    logger.info(
        "subscription_overflow_dispatched kind=%s route=%s source_id=%s model=%s",
        kind,
        decision.route,
        source.id,
        model,
    )
    return dispatch


async def dispatch_pinned(
    decision: _Decision, record: PinRecord, state: PinLookupState
) -> OverflowDispatch | Response | None:
    """A conversation pinned to a source (design §7.2): source regardless of pool state and portability."""

    return await _dispatch_bound(decision, record, state, kind=DISPATCH_KIND_PINNED, neutral_release=True)


async def dispatch_anchor(
    decision: _Decision, record: PinRecord, state: PinLookupState
) -> OverflowDispatch | Response | None:
    """An SDK chain anchored on a source-minted ``previous_response_id`` (§7.2): same source, also in drain mode.

    No neutral release: a body carrying ``previous_response_id`` is never
    source-free, so an unservable anchor answers 400 directly.
    """

    return await _dispatch_bound(decision, record, state, kind=DISPATCH_KIND_ANCHOR, neutral_release=False)


async def compact_pin_denial(
    request: Request,
    payload: ResponsesCompactRequest,
    *,
    context: ProxyContext,
) -> JSONResponse | None:
    """400 ``subscription_overflow_unsupported_input`` for a live/expired pinned conversation; 503 on lookup timeout.

    Runs before any reservation on both compact routes (P5, CP-10). A lookup
    failure while lookups are mandated fails closed (503) like the decision.
    """

    settings = await get_settings_cache().get()
    service = context.service
    clock = clock_for(service)
    off, _drain_until = _settings_off(settings, clock)
    if off:
        return None
    thread_key = overflow_thread_key(request.headers)
    if thread_key is None:
        return None
    try:
        result = await lookup_pin_bounded(
            thread_pin_key(thread_key), cache=get_pin_cache(), scheduler=scheduler_for(service), clock=clock
        )
    except PinLookupTimeout:
        record_overflow_outcome(ROUTE_COMPACT, "pinned_lookup_timeout")
        logger.warning("subscription_overflow_pinned_unservable cause=lookup_timeout route=%s", ROUTE_COMPACT)
        return _transient_denial(MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS)
    except Exception:
        record_overflow_outcome(ROUTE_COMPACT, "decision_error")
        logger.warning("subscription_overflow_decision_error stage=pin_lookup route=%s", ROUTE_COMPACT, exc_info=True)
        return _transient_denial(MODEL_SOURCE_UNAVAILABLE_CODE, MODEL_SOURCE_UNAVAILABLE_MESSAGE, RETRY_AFTER_SECONDS)
    if result.state not in _PIN_EVIDENCE:
        return None
    record_overflow_outcome(ROUTE_COMPACT, "pinned_unsupported_input")
    logger.warning(
        "subscription_overflow_pinned_unservable cause=compaction route=%s pin_state=%s", ROUTE_COMPACT, result.state
    )
    return _permanent_denial(UNSUPPORTED_INPUT_CODE, COMPACT_DENIAL_MESSAGE)


async def handshake_denial(
    headers: Mapping[str, str],
    *,
    context: ProxyContext,
) -> JSONResponse | None:
    """426 ``subscription_overflow_requires_http_transport`` on live pin / tombstone / bounce evidence (never probes).

    One bounded read for the thread's pin and bounce rows. ``PinLookupTimeout``
    (and any other lookup failure) -> 426: fail-closed toward HTTP, where the
    route decides authoritatively (counted ``pinned_lookup_timeout`` /
    ``decision_error``). Exhaustion alone is never evidence (CP-8).
    """

    settings = await get_settings_cache().get()
    service = context.service
    clock = clock_for(service)
    off, _drain_until = _settings_off(settings, clock)
    if off:
        return None
    thread_key = overflow_thread_key(headers)
    if thread_key is None:
        return None
    try:
        results = await lookup_pins_bounded(
            (thread_pin_key(thread_key), bounce_pin_key(thread_key)),
            cache=get_pin_cache(),
            scheduler=scheduler_for(service),
            clock=clock,
        )
    except PinLookupTimeout:
        record_overflow_outcome(ROUTE_WEBSOCKET_HANDSHAKE, "pinned_lookup_timeout")
        logger.warning(
            "subscription_overflow_handshake_denied cause=lookup_timeout route=%s", ROUTE_WEBSOCKET_HANDSHAKE
        )
        return _handshake_denial_response()
    except Exception:
        record_overflow_outcome(ROUTE_WEBSOCKET_HANDSHAKE, "decision_error")
        logger.warning(
            "subscription_overflow_decision_error stage=handshake_lookup route=%s",
            ROUTE_WEBSOCKET_HANDSHAKE,
            exc_info=True,
        )
        return _handshake_denial_response()
    evidence = sorted(result.state for result in results.values() if result.state in _HANDSHAKE_EVIDENCE)
    if not evidence:
        return None
    record_overflow_outcome(ROUTE_WEBSOCKET_HANDSHAKE, "bounced_ws_handshake")
    logger.info(
        "subscription_overflow_handshake_denied evidence=%s route=%s", ",".join(evidence), ROUTE_WEBSOCKET_HANDSHAKE
    )
    return _handshake_denial_response()


def apply_usage_limit_hint(
    request: Request,
    content: Mapping[str, JsonValue],
    headers: dict[str, str],
) -> tuple[Mapping[str, JsonValue], dict[str, str]]:
    """Add the ``not_portable_history`` hint to a 429: ``HINT_HEADER`` (native) or an ``error.message`` sentence.

    O(1): one ``getattr`` on ``request.state``; every other 429 passes through
    untouched (byte-identical to today's answer).
    """

    if getattr(request.state, HINT_STATE_ATTRIBUTE, None) != "not_portable_history":
        return content, headers
    if _is_native_codex_request(request.headers):
        hinted_headers = dict(headers)
        hinted_headers[HINT_HEADER] = HINT_NATIVE_TEXT
        return content, hinted_headers
    error = content.get("error")
    if not isinstance(error, Mapping):
        return content, headers
    message = error.get("message")
    hinted_error: dict[str, JsonValue] = dict(error)
    hinted_error["message"] = (
        f"{message.rstrip()} {HINT_SDK_SENTENCE}" if isinstance(message, str) and message.strip() else HINT_SDK_SENTENCE
    )
    hinted_content: dict[str, JsonValue] = dict(content)
    hinted_content["error"] = hinted_error
    return hinted_content, headers


# -- pure eligibility helpers (shared with the WebSocket parity helper) ----------------------------


def overflow_thread_key(headers: Mapping[str, str]) -> str | None:
    """``thread_only`` selection key derived from the native ``thread-id`` header; ``None`` when absent.

    Feed it to ``thread_pin_key`` / ``bounce_pin_key``; never the
    ``process-thread`` form (design §3, P6): the same conversation resolves to
    the same row from every Codex process, session and API key.
    """

    identity = _codex_backend_identity(headers)
    if identity.thread_id is None:
        return None
    return _CodexBackendIdentity(process_session=None, thread_id=identity.thread_id).thread_selection_key


def fresh_decline_reason(
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    *,
    thread_key: str | None,
    source_route_excluded: bool,
    fast_decline: FastDeclineSet,
    breaker: SourceBreaker,
    source_id: str,
    now: float,
) -> DeclineReason | None:
    """O(1) declines of step 4, in design order.

    ``pin_commit_recent_failure`` | ``turn_state_bound`` | ``opportunistic`` | ``key_scope`` | ``no_thread_key`` |
    ``background_job`` | ``source_excluded`` | ``breaker_open``.
    """

    if thread_key is not None and fast_decline.contains(thread_key, now):
        return "pin_commit_recent_failure"
    if is_binding_turn_state(headers):
        return "turn_state_bound"
    if api_key is not None and api_key.traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC:
        return "opportunistic"
    if api_key is not None and api_key.source_assignment_scope_enabled:
        return "key_scope"
    if thread_key is None and _is_native_codex_request(headers):
        return "no_thread_key"
    if not background_job_allowed(headers):
        return "background_job"
    if source_route_excluded:
        return "source_excluded"
    if breaker.is_open(source_id, now):
        return "breaker_open"
    return None


def portability_decline(
    body: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    *,
    source: ModelSource,
    model: str,
) -> tuple[DeclineReason | None, str | None]:
    """Portability view -> verdict on the stripped source body; returns ``(reason, detail)``.

    The view's and the verdict's fine-grained reasons collapse onto the closed
    label set: ``not_portable_history`` (the only hinted reason) and
    ``turn_state_bound`` keep their names, every other class is
    ``not_portable_input`` with the fine-grained reason and offending
    type/field in ``detail`` (WARN line, never a label).
    """

    view = overflow_portability_view(body)
    if isinstance(view, Declined):
        return "not_portable_input", _portability_detail(view.reason, view.detail)
    verdict = responses_payload_is_provider_portable(
        view,
        headers,
        supported_tool_types=source_model_supported_tool_types(source, model),
        supports_vision=source_model_supports_vision(source, model),
    )
    if verdict.portable or verdict.reason is None:
        return None, None
    if verdict.reason in ("not_portable_history", "turn_state_bound"):
        return verdict.reason, verdict.detail
    return "not_portable_input", _portability_detail(verdict.reason, verdict.detail)


def _portability_detail(reason: str, detail: str | None) -> str:
    return reason if detail is None else f"{reason}:{detail}"


def background_job_allowed(headers: Mapping[str, str]) -> bool:
    """``x-openai-subagent`` absent or allowlisted; refused whenever ``x-openai-memgen-request`` is present.

    A positive allowlist (CP-9): ``memory_consolidation``, ``guardian`` and any
    label this proxy has never seen are refused.
    """

    if _header(headers, MEMGEN_HEADER) is not None:
        return False
    subagent = _header(headers, SUBAGENT_HEADER)
    if subagent is None or not subagent.strip():
        return True
    return subagent.strip() in BACKGROUND_ALLOWLIST


# -- ``SourceDispatch.on_finished`` hook ----------------------------------------------------------


def record_overflow_transport_decision(owner: SourceDispatch, status: DispatchStatus) -> None:
    """Records the upstream transport decision with ``policy="subscription_overflow"``.

    ``sticky=owner.dispatch_kind != "fresh"``; the observability import happens at call time.
    """

    from app.modules.proxy._service.observability import _record_upstream_transport_decision

    _record_upstream_transport_decision(
        downstream_transport="http",
        upstream_transport="openai_compatible_http",
        policy="subscription_overflow",
        sticky=owner.dispatch_kind != DISPATCH_KIND_FRESH,
        status=status,
    )


def pin_commit_outcome_label(pin_outcome: PinWriteOutcome | None) -> str | None:
    """``pin_commit_failed`` / ``pin_commit_unverified`` for a pin commit that did not verify; ``None`` otherwise."""

    if pin_outcome is None or pin_outcome == "written":
        return None
    return "pin_commit_unverified" if pin_outcome == "unknown" else "pin_commit_failed"


@dataclass(frozen=True, slots=True)
class OverflowFinishedHook:
    """``SourceDispatch.on_finished`` of an overflow dispatch, bound to the decision's ``route``.

    Called exactly once per lifecycle from ``finish()``: counts the pin-commit
    outcome (``pin_commit_failed`` / ``pin_commit_unverified``, design §8.5)
    when the owner's ``pin_outcome`` did not verify -- the streaming hook and
    the non-streaming route both set it before the pin-failure ``finish()`` --
    and records the transport decision. A commit interrupted by the caller's
    cancellation leaves ``pin_outcome`` unset and counts nothing: the outcome
    was never seen by this dispatch (the fast-decline mark still applies).
    """

    route: str

    def __call__(self, owner: SourceDispatch, status: DispatchStatus) -> None:
        outcome = pin_commit_outcome_label(owner.pin_outcome)
        if outcome is not None:
            record_overflow_outcome(self.route, outcome)
        record_overflow_transport_decision(owner, status)
