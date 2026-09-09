"""Subscription-exhaustion overflow to a designated model source (#2123 WP-C2, WP-D folded).

Shared interface contract for the overflow wiring: constants, typed
protocols/dataclasses and the function signatures every package (decision
module, HTTP route insertion, WebSocket parity, spec/docs) codes against.
This scaffold carries **no behaviour** -- every body raises
``NotImplementedError`` until the decision module lands.

Design (v3) obligations this module will own once implemented:

* Ship-dark (I9): the first step of every entry point is two attribute reads
  on the already-warm ``SettingsCache`` row (``subscription_overflow_source_id``
  and ``subscription_overflow_drain_until``) plus one comparison; with both
  ``NULL`` the request path performs zero probe / lookup / select / body-walk
  calls and an exhausted pool answers byte-identically to today's 429.
* Fail-closed (I5): every decline renders today's ``usage_limit_reached`` 429
  byte-identically except the ``not_portable_history`` hint; pinned and
  anchored contexts never fall through to the subscription pool.
* Exactly-once claims (I13): admission claims are the last await-free step of
  the decision and are released by exactly one latch (route helper or
  ``SourceDispatch``); ``CancelledError`` is a ``BaseException`` and is never
  swallowed.
* Zero timing allowance: this module never calls ``asyncio`` timing
  primitives or ``time.monotonic()`` directly -- deadlines flow through the
  ``Scheduler`` / ``Clock`` passed in by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, get_args

from app.modules.proxy.model_source_pins import PinIntent, PinRecord, PinWriteExecutor
from app.modules.proxy.source_admission import SourceAdmission, SourceBulkhead, TrialClaim, TrialResult

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response

    from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
    from app.core.types import JsonValue
    from app.db.models import ModelSource
    from app.dependencies import ProxyContext
    from app.modules.api_keys.service import ApiKeyData
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
    "apply_usage_limit_hint",
    "background_job_allowed",
    "compact_pin_denial",
    "fresh_decline_reason",
    "get_overflow_pin_executor",
    "get_source_breaker",
    "handshake_denial",
    "overflow_thread_key",
    "portability_decline",
    "record_overflow_transport_decision",
    "resolve_subscription_overflow",
    "try_claim_overflow",
]

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

# -- breaker / claims / fast-decline tunables ---------------------------------------------------

BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 30.0
TRIAL_LEASE_TTL_SECONDS = 120.0
PIN_FAILURE_FAST_DECLINE_SECONDS = 60.0
FAST_DECLINE_MAX_KEYS = 10_000

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

# Closed ``outcome`` enum of ``codex_lb_subscription_overflow_total`` (design §11).
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
        "pinned_released_neutral",
        "pinned_lookup_timeout",
        "pin_commit_failed",
        "pin_commit_unverified",
        "decision_error",
    }
    | {f"declined_{reason}" for reason in get_args(DeclineReason)}
)


# -- breaker ------------------------------------------------------------------------------------

BreakerState = Literal["closed", "open", "half_open"]


class BreakerToken(TrialClaim):
    """Lease handed to one overflow dispatch; ``settle`` is told the outcome exactly once.

    Issued in ``closed`` (records failures toward the threshold) and
    ``half_open`` (the single leased trial); never in ``open``.
    """

    __slots__ = ("_breaker", "_issued_at", "_settled", "source_id")

    def __init__(self, breaker: SourceBreaker, source_id: str, *, issued_at: float) -> None:
        self._breaker = breaker
        self.source_id = source_id
        self._issued_at = issued_at
        self._settled = False

    def settle(self, result: TrialResult) -> None:
        raise NotImplementedError


class SourceBreaker:
    """Per-source failure breaker for overflow dispatches (3 failures / 30 s open / leased half-open trial).

    Counts only overflow dispatches; direct routing keeps ``try_claim(source)``
    without a token (documented deviation).
    """

    def __init__(self) -> None:
        raise NotImplementedError

    def state(self, source_id: str, now: float) -> BreakerState:
        raise NotImplementedError

    def is_open(self, source_id: str, now: float) -> bool:
        raise NotImplementedError

    def claim(self, source_id: str, now: float) -> BreakerToken | None:
        """Token in ``closed``/``half_open``; ``None`` in ``open`` or while the half-open trial lease is fresh."""

        raise NotImplementedError


def get_source_breaker() -> SourceBreaker:
    """Process-wide breaker instance (one per replica worker)."""

    raise NotImplementedError


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
    """``try_claim(source, bulkhead=..., trial=breaker.claim(...))``; the token is released with the slot on denial."""

    raise NotImplementedError


# -- fast decline after a pin-commit failure ----------------------------------------------------


class FastDeclineSet:
    """Thread keys declined for ``PIN_FAILURE_FAST_DECLINE_SECONDS`` after a non-``written`` pin commit.

    Lazy eviction, bounded to ``FAST_DECLINE_MAX_KEYS`` entries.
    """

    def __init__(self, *, ttl_seconds: float = PIN_FAILURE_FAST_DECLINE_SECONDS, max_keys: int = FAST_DECLINE_MAX_KEYS):
        raise NotImplementedError

    def mark(self, thread_key: str, now: float) -> None:
        raise NotImplementedError

    def contains(self, thread_key: str, now: float) -> bool:
        raise NotImplementedError


class OverflowPinExecutor(PinWriteExecutor):
    """``PinWriteExecutor`` whose ``commit`` fast-declines ``intent.thread_key`` on any non-``written`` outcome.

    Bounce rows use the base executor (no fast-decline mark).
    """

    def __init__(self, *, fast_decline: FastDeclineSet, **kwargs: Any) -> None:
        raise NotImplementedError


def get_overflow_pin_executor() -> OverflowPinExecutor:
    """Process-wide overflow pin executor sharing the process fast-decline set."""

    raise NotImplementedError


# -- pin touch (never on the request path) -----------------------------------------------------


class PinToucher:
    """Schedules a background ``ModelSourcePinRepository.touch`` once ``last_seen_at`` is older than the touch interval.

    Never awaited on the request path: the touch goes through ``_schedule_cancel_safe_cleanup``.
    """

    def maybe_touch(
        self,
        record: PinRecord,
        *,
        cleanup_scheduler: Any,
        drain_until: datetime | None,
        now: datetime,
    ) -> None:
        raise NotImplementedError


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
        ``on_finished``."""

        raise NotImplementedError


# -- entry points (api.py hunks) ----------------------------------------------------------------


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
) -> OverflowDispatch | Response | None:
    """Decide once at route admission (design §4.2 order).

    ``None``: fall through to the subscription path unchanged (both settings
    columns off, or a decline). ``Response``: a fail-closed answer for a
    pinned/anchored context. ``OverflowDispatch``: dispatch to the source with
    claims already held (released by the route helper latch when no owner
    takes them).
    """

    raise NotImplementedError


async def compact_pin_denial(
    request: Request,
    payload: ResponsesCompactRequest,
    *,
    context: ProxyContext,
) -> JSONResponse | None:
    """400 ``subscription_overflow_unsupported_input`` for a live/expired pinned conversation; 503 on lookup timeout."""

    raise NotImplementedError


async def handshake_denial(
    headers: Mapping[str, str],
    *,
    context: ProxyContext,
) -> JSONResponse | None:
    """426 ``subscription_overflow_requires_http_transport`` on live pin / tombstone / bounce evidence (never probes).

    ``PinLookupTimeout`` -> 426 (fail-closed toward HTTP, counted ``pinned_lookup_timeout``).
    """

    raise NotImplementedError


def apply_usage_limit_hint(
    request: Request,
    content: Mapping[str, JsonValue],
    headers: dict[str, str],
) -> tuple[Mapping[str, JsonValue], dict[str, str]]:
    """Add the ``not_portable_history`` hint to a 429: ``HINT_HEADER`` (native) or an ``error.message`` sentence."""

    raise NotImplementedError


# -- pure eligibility helpers (shared with the WebSocket parity helper) ----------------------------


def overflow_thread_key(headers: Mapping[str, str]) -> str | None:
    """``thread_only`` pin key derived from the native ``thread-id`` header; ``None`` when absent."""

    raise NotImplementedError


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
    """O(1) declines of step 4.

    ``pin_commit_recent_failure`` | ``turn_state_bound`` | ``opportunistic`` | ``key_scope`` | ``no_thread_key`` |
    ``background_job`` | ``source_excluded`` | ``breaker_open``.
    """

    raise NotImplementedError


def portability_decline(
    body: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    *,
    source: ModelSource,
    model: str,
) -> tuple[DeclineReason | None, str | None]:
    """Strip -> portability view -> verdict; returns ``(reason, detail)``."""

    raise NotImplementedError


def background_job_allowed(headers: Mapping[str, str]) -> bool:
    """``x-openai-subagent`` absent or allowlisted; refused whenever ``x-openai-memgen-request`` is present."""

    raise NotImplementedError


# -- ``SourceDispatch.on_finished`` hook ----------------------------------------------------------


def record_overflow_transport_decision(owner: SourceDispatch, status: DispatchStatus) -> None:
    """``on_finished`` hook: records the upstream transport decision with ``policy="subscription_overflow"``.

    ``sticky=owner.dispatch_kind != "fresh"``; the observability import happens at call time.
    """

    raise NotImplementedError
