"""Single-owner lifecycle for a dispatched model-source attempt (#2123 WP-C1).

Every request forwarded to an OpenAI-compatible model source is owned by
exactly one ``SourceDispatch`` that ends in exactly one ``finish()`` (I10): the
source connection is closed, the API-key reservation is settled or released
exactly once (I3), the admission claims are released, and one request-log row
is written per attempt -- each step independently latched and failure-isolated
(design v3 §6). ``abandon()`` is the cancellation-class ``finish()`` for a
client that left during the open or before the body started (§6.4 table).

Composition rules:

* the settlement generator (``settlement_stream``) is the *outermost* body
  layer for every composition: it sees the terminal outcome and the
  cancellation first, and it owns the reservation;
* ``SourceStreamingResponse.__call__`` wraps the transport in
  ``try/finally: _await_cleanup_deferring_cancellation(owner.finalize_transport(), scheduler=owner.scheduler)``
  so a client that leaves before Starlette starts the body still reaches one
  ``finish()``; the direct chat-completions stream route, which has no
  dispatch owner, hands the same response class a ``SourceChatStreamOwner``
  so its never-started body closes the source transport too;
* the handler segment uses ``except BaseException -> abandon(); raise``
  (``CancelledError`` is a ``BaseException`` and is never caught by
  ``except Exception``);
* every timer, wait and task spawn goes through ``owner.scheduler`` /
  ``owner.clock`` (``scripts/check_proxy_timing_seams.py`` has a zero
  allowance for this module) and every cleanup await defers cancellation
  (``scripts/check_cancellation_safety.py``).

Settle policy (design §6.1, CL-8/P4/CP-16): ``success`` with usage finalizes
at the source's usage and cost; ``success`` without usage on a limited key
settles at an estimate (never at zero, never released); a cancel *after*
content was delivered to the client (an event frame ``settlement_stream``
handed to the transport -- not the parser's observation, nor the body's flush
point, which sits below wrapper layers that can still drop or park a frame)
on a limited key settles at the estimate (a key must not consume most of an
answer and disconnect unmetered); a cancel before it, and every
``error``, release. A stream the source ends with a failure terminal, or
closes without any terminal the client could complete on, is an ``error``
(``model_source_response_failed`` / ``model_source_stream_truncated``) and
releases: the client received a failure, so nothing is charged. Estimates are
never written to the request-log row
as usage -- they are visible through the WARN line and the
``codex_lb_model_source_usage_estimated_total`` counter.

The overflow decision (WP-C2) supplies ``request_log_source``,
``dispatch_kind``, the pin intent and the ``on_finished`` hook; this module
never spells the designation itself. The pin intent is resolved against the
source response id at the content trigger (``PinIntent.resolve``) so an anchor
row for an SDK ``previous_response_id`` chain lands in the same transaction as
the thread pin.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.metrics.prometheus import (
    model_source_dispatch_abandoned_total,
    model_source_dispatch_total,
    model_source_timeout_total,
    model_source_usage_estimated_total,
)
from app.core.openai.parsing import classify_event_type
from app.core.request_locality import resolve_request_client_host
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id
from app.core.utils.shared_future import (
    _await_cleanup_deferring_cancellation,
    _await_result_deferring_cancellation,
    _await_task_deferring_cancellation,
)
from app.core.utils.sse import _SSE_LINE_BOUNDARY, format_sse_event, parse_sse_data_json
from app.db.models import ModelSource
from app.db.session import get_background_session
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.model_sources.catalog import source_model_cost_usd
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    SourceChatStream,
    SourceResponsesStream,
    SourceTimings,
    SourceUsage,
    SourceUsageHolder,
    TimeoutPhase,
    classify_responses_frame,
)
from app.modules.proxy._service.support import _request_log_client_fields
from app.modules.proxy.affinity import _owner_lookup_session_id_from_headers
from app.modules.proxy.model_source_pins import PinIntent, PinWriteExecutor, PinWriteOutcome
from app.modules.proxy.source_admission import SourceAdmission, TrialResult
from app.modules.request_logs.repository import RequestLogsRepository

logger = logging.getLogger(__name__)

T = TypeVar("T")

DispatchStatus = Literal["success", "error", "cancelled"]
EstimateCause = Literal["missing_usage", "client_cancel"]
AbandonStage = Literal["during_open", "before_body", "stall"]

# Attribution defaults for direct source routing; the overflow decision (WP-C2)
# passes its own values so this module never spells them.
DEFAULT_REQUEST_LOG_SOURCE = "model_source"
DEFAULT_DISPATCH_KIND = "direct"

# A client that leaves while the open is still pending is a stall abandonment
# once the source has been silent this long without a first frame.
STALL_EVIDENCE_SECONDS = 10.0
# Disconnect poll cadence while the source open is pending.
OPEN_DISCONNECT_POLL_SECONDS = 0.25

# Terminal codes written by the owner itself (stage-naming ``error_code``).
ABANDON_CLIENT_DISCONNECTED_DURING_OPEN = "client_disconnected_during_open"
ABANDON_SOURCE_STALL = "source_stall_abandoned"
ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY = "client_disconnected_before_body"
ABANDON_DISPATCH_INTERRUPTED = "dispatch_interrupted"
ERROR_USAGE_SETTLEMENT_FAILED = "usage_settlement_failed"
ERROR_MODEL_SOURCE_STREAM = "model_source_stream_error"
# The source ended the stream with ``response.failed`` / ``error``: no answer was
# delivered, so the attempt is an error and the reservation is released.
ERROR_MODEL_SOURCE_RESPONSE_FAILED = "model_source_response_failed"
# The source closed the stream without any terminal frame (the public wrapper
# synthesizes ``response.failed`` for SDK clients): no answer was delivered.
ERROR_MODEL_SOURCE_STREAM_TRUNCATED = "model_source_stream_truncated"
# The source ended with a success terminal the public contract could not accept
# (``response`` not an object, invalid output items): the wrapper relayed
# ``response.failed`` in its place, so the client received a failure and no
# answer was delivered.
ERROR_MODEL_SOURCE_RESPONSE_INVALID = "model_source_response_invalid"
_FAILURE_TERMINAL_KINDS = frozenset({"failed", "error"})
_SUCCESS_TERMINAL_KINDS = frozenset({"completed", "incomplete"})
_RELAYED_TERMINAL_KINDS: Mapping[str, str] = {
    "response.completed": "completed",
    "response.incomplete": "incomplete",
    "response.failed": "failed",
    "error": "error",
}
# Frames the settlement layer never takes as the stream's last event: SSE
# comments (``: keepalive``), the ``[DONE]`` sentinel and the Codex keepalive.
_NON_EVENT_FRAME_PREFIXES = ("data: [DONE]", "event: codex.keepalive")
# ``classify_responses_frame`` kinds that deliver response content to the
# client (the api-keys delta: an output item, a ``*.delta``, a success terminal
# or any event type this proxy does not know); bookkeeping and failure
# terminals do not.
_DELIVERING_FRAME_KINDS = frozenset({"content", "success_terminal"})
CANCELLED_CLIENT_DISCONNECTED = "client_disconnected"
# The overflow decision (WP-C2) overrides these with its own codes; a pin
# intent is never armed by direct routing, so they are unreachable in
# production until then.
DEFAULT_PIN_FAILURE_ERROR_CODE = "model_source_pin_unavailable"
DEFAULT_PIN_UNVERIFIED_ERROR_CODE = "model_source_pin_unverified"

_ABANDON_STAGES: Mapping[str, AbandonStage] = {
    ABANDON_CLIENT_DISCONNECTED_DURING_OPEN: "during_open",
    ABANDON_DISPATCH_INTERRUPTED: "during_open",
    ABANDON_SOURCE_STALL: "stall",
    ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY: "before_body",
}


class CleanupScheduler(Protocol):
    """``ProxyService`` satisfies this: release retries ride its cancel-safe cleanup tasks."""

    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> asyncio.Task[None]: ...


class ReservationSettler(Protocol):
    """``api.py``'s ``_settle_source_reservation`` shape: finalize at ``usage``; ``False`` after a release fallback."""

    def __call__(
        self,
        reservation: ApiKeyUsageReservationData,
        *,
        source: ModelSource,
        model: str,
        usage: SourceUsage | None,
        cost_usd_override: float | None = None,
    ) -> Coroutine[Any, Any, bool]: ...


class ReservationReleaser(Protocol):
    """``api.py``'s ``_release_reservation`` shape."""

    def __call__(self, reservation: ApiKeyUsageReservationData) -> Coroutine[Any, Any, None]: ...


class SourcePinCommitError(Exception):
    """The pre-content pin write did not verify as durable; the stream must not deliver content."""

    def __init__(self, outcome: Literal["not_written", "unknown"]) -> None:
        super().__init__(outcome)
        self.outcome: Literal["not_written", "unknown"] = outcome


class ClientDisconnectedDuringOpen(Exception):
    """The client left while the source open was pending."""

    def __init__(self, *, pending_seconds: float, stall: bool) -> None:
        super().__init__(f"client disconnected after {pending_seconds:.3f}s (stall={stall})")
        self.pending_seconds = pending_seconds
        self.stall = stall


def relayed_terminal_kind(frame: str | None) -> str | None:
    """Terminal kind of an event frame relayed to the client; ``None`` when it is not a terminal.

    Read from the ``event:`` line the public wrapper frames every typed event
    with, falling back to the ``data:`` JSON for a raw pass-through block.
    ``settlement_stream`` latches the kinds it sees into the client-visible
    outcome: when the parser saw no terminal (the frame outgrew its cap, or the
    source closed without one) it decides between a delivered success and a
    truncated stream.
    """

    if not frame:
        return None
    event_type = _relayed_event_type(frame)
    return _RELAYED_TERMINAL_KINDS.get(event_type) if event_type is not None else None


def relayed_frame_delivers_content(frame: str | None) -> bool:
    """``True`` when a relayed event frame carries response content the client can use.

    Classified with the parser's own ``classify_responses_frame`` so the body
    and the settlement layer agree on what content is: an output item, a
    ``*.delta``, a success terminal or any event type this proxy does not know
    delivers content; ``response.created`` / ``in_progress`` / ``queued``, a
    failure terminal and a frame without a typed event (a comment, ``[DONE]``,
    the Codex keepalive, unparseable ``data``) do not.
    """

    if not frame or not _is_event_frame(frame):
        return False
    return classify_responses_frame(_relayed_event_type(frame)) in _DELIVERING_FRAME_KINDS


def _is_event_frame(chunk: str) -> bool:
    """An SSE block that carries an event: not a comment, not ``[DONE]``, not the Codex keepalive."""

    return bool(chunk) and chunk[0] != ":" and not chunk.startswith(_NON_EVENT_FRAME_PREFIXES)


def _relayed_event_type(frame: str) -> str | None:
    """Event type of a relayed frame: the ``event:`` line the public wrapper frames every typed event with, else the
    type of a raw pass-through block's ``data:`` JSON as the wrapper's own ``classify_event_type`` reads it (a
    typeless ``{"error": {...}}`` record is an ``error`` terminal there too); ``None`` when neither names one.

    Lines end at the SSE boundary set (CR, LF or CRLF -- ``_SSE_LINE_BOUNDARY``, never ``str.splitlines``, whose
    extra Unicode breaks can sit unescaped inside a JSON string) and a multi-line ``data:`` is joined as the parser
    joins it (``parse_sse_data_json``), so a spec-legal framing the wrapper relays verbatim classifies as the frame
    the client parses: a bare-CR ``response.created`` is bookkeeping, not an unknown content-bearing type.
    """

    if frame.startswith("event: "):
        return _SSE_LINE_BOUNDARY.split(frame[7:], maxsplit=1)[0]
    return classify_event_type(parse_sse_data_json(frame))


def error_code_from_payload(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def error_message_from_payload(payload: Mapping[str, JsonValue]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    message = error.get("message")
    return message if isinstance(message, str) else None


def source_usage_cost_usd(source: ModelSource, model: str, usage: SourceUsage | None) -> float | None:
    """Source pricing for ``usage``; unpriced entries cost ``0.0`` (never ``None`` for known usage)."""

    if usage is None:
        return None
    cost_usd = source_model_cost_usd(
        source,
        model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    return 0.0 if cost_usd is None else cost_usd


def _reservation_requires_usage(reservation: ApiKeyUsageReservationData | None) -> bool:
    return bool(reservation is not None and reservation.has_applicable_limits)


def forwarding_error_trial_result(exc: ModelSourceForwardingError) -> TrialResult:
    """Breaker classification of a pre-body source failure (design §8.3): 4xx other than 429 is not counted."""

    if exc.timeout_phase is not None or exc.upstream_status_code is None:
        return "failure"
    if exc.upstream_status_code == 429 or exc.upstream_status_code >= 500:
        return "failure"
    return "inconclusive"


# Row codes of a started stream that broke off: an exception out of the body
# (a transport drop) or a clean EOF without a terminal (the wrapper synthesized
# ``response.failed``; the source never finished its answer). Counted by the
# breaker before and after the first output item alike, like the idle deadline
# (design §8.3: "after the first frame: idle timeout, transport drop").
_TRANSPORT_DROP_ERROR_CODES: frozenset[str] = frozenset(
    {ERROR_MODEL_SOURCE_STREAM, ERROR_MODEL_SOURCE_STREAM_TRUNCATED}
)


def stream_trial_result(
    status: DispatchStatus,
    *,
    error_code: str | None,
    first_output_item_seen: bool,
    forwarding_error: ModelSourceForwardingError | None,
) -> TrialResult:
    """Breaker classification of a started stream's terminal outcome (design §8.3).

    ``success`` counts only once an output item was produced (an empty
    completion is inconclusive). A mid-stream ``ModelSourceForwardingError``
    (the idle deadline, a transport failure, the withheld-bytes cap) and a
    transport drop are counted before and after the first output item alike.
    A failure terminal -- the source's ``response.failed``/``error``, or a
    success terminal the public contract rewrote into one -- is counted only
    before the first output item: after it the source itself ended the
    answer. A client cancel is never counted here (``abandon`` classifies the
    stall evidence of a body that never started).
    """

    if status == "success":
        return "success" if first_output_item_seen else "inconclusive"
    if status != "error":
        return "inconclusive"
    if forwarding_error is not None:
        return forwarding_error_trial_result(forwarding_error)
    if error_code in _TRANSPORT_DROP_ERROR_CODES:
        return "failure"
    return "inconclusive" if first_output_item_seen else "failure"


def estimate_settlement_usage(*, admission_budget: ApiKeyRequestUsageBudget | None, delta_chars: int) -> SourceUsage:
    """Settle-at-estimate figures: input = admission estimate or default; output = max(default, delta_chars // 4)."""

    input_tokens = API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS
    if admission_budget is not None and admission_budget.input_tokens is not None:
        input_tokens = admission_budget.input_tokens
    output_tokens = max(API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS, max(0, delta_chars) // 4)
    return SourceUsage(input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=0)


def synthesized_pin_failure_frames(created_envelope: Mapping[str, JsonValue] | None, *, error_code: str) -> list[str]:
    """The ``response.created`` + ``response.failed`` pair (sequence 0/1) emitted on a verified pin non-write.

    Nothing from the source was yielded before the pin hook fired, so this is
    the only lifecycle the client sees (I2). The captured source envelope keeps
    the source's ids; without one a minimal envelope is synthesized.
    """

    envelope: dict[str, JsonValue] = dict(created_envelope) if created_envelope is not None else {}
    envelope.setdefault("object", "response")
    created_envelope_out: dict[str, JsonValue] = {**envelope, "status": "in_progress"}
    failed_envelope: dict[str, JsonValue] = {
        **envelope,
        "status": "failed",
        "error": {
            "code": error_code,
            "type": "server_error",
            "message": "The model source could not record continuity for this conversation; retry the request",
        },
    }
    created_event: dict[str, JsonValue] = {
        "type": "response.created",
        "sequence_number": 0,
        "response": created_envelope_out,
    }
    failed_event: dict[str, JsonValue] = {
        "type": "response.failed",
        "sequence_number": 1,
        "response": failed_envelope,
    }
    return [format_sse_event(created_event), format_sse_event(failed_event)]


def _inc(counter: Any, **labels: str) -> None:
    if counter is None:
        return
    try:
        counter.labels(**labels).inc()
    except Exception:  # metrics never break the request path
        logger.debug("model_source_dispatch_metric_failed labels=%s", labels, exc_info=True)


@dataclass(slots=True)
class SourceDispatch:
    """Owner of one dispatched attempt; ``finish()``/``abandon()`` is the single latch."""

    request: Request
    source: ModelSource
    model: str
    api_key: ApiKeyData | None
    reservation: ApiKeyUsageReservationData | None
    claims: SourceAdmission
    admission_budget: ApiKeyRequestUsageBudget | None
    requested_service_tier: str | None
    request_log_source: str = DEFAULT_REQUEST_LOG_SOURCE
    dispatch_kind: str = DEFAULT_DISPATCH_KIND
    pin_intent: PinIntent | None = None
    pin_executor: PinWriteExecutor | None = None
    drain_until: datetime | None = None
    cleanup_scheduler: CleanupScheduler | None = None
    scheduler: Scheduler = REAL_SCHEDULER
    clock: Clock = REAL_CLOCK
    stream: SourceResponsesStream | None = None
    sent_at: float = 0.0
    first_frame_at: float | None = None
    first_output_item_seen: bool = False
    # Set by ``settlement_stream`` when it hands the transport an event frame
    # that carries content (``relayed_frame_delivers_content``); the cancel
    # settlement policy keys on this alone. It is never mirrored from
    # ``SourceUsageHolder.content_delivered`` (the body's flush point): the
    # public wrapper above that point still drops vendor events, parks
    # ``response.*`` frames ahead of ``response.created`` and holds reasoning
    # deltas, so a chunk the body counted may never reach the client.
    content_delivered: bool = False
    delta_chars: int = 0
    body_started: bool = False
    finished: bool = False
    # Reservation primitives are injected by the route (``api.py`` owns them and
    # its tests patch them there); a dispatch that holds a reservation without
    # them cannot settle and logs an error instead of leaking silently.
    settle_reservation: ReservationSettler | None = None
    release_reservation: ReservationReleaser | None = None
    request_id: str | None = None
    # Outermost body iterator handed to ``SourceStreamingResponse`` (closed by ``finalize_transport``).
    body: AsyncIterator[str] | None = None
    pin_failure_error_code: str = DEFAULT_PIN_FAILURE_ERROR_CODE
    pin_unverified_error_code: str = DEFAULT_PIN_UNVERIFIED_ERROR_CODE
    # Called exactly once per lifecycle from ``finish()`` with the terminal
    # status, after the result was recorded and before the row is written
    # (the overflow decision records its transport-decision counter here);
    # failure-isolated like every other step.
    on_finished: Callable[[SourceDispatch, DispatchStatus], None] | None = None
    pin_outcome: PinWriteOutcome | None = None
    # Non-stream completions carry the source response id in the JSON body
    # (streams expose it through the usage holder).
    source_response_id: str | None = None
    settlement_failed: bool = False
    _source_closed: bool = field(default=False, init=False, repr=False)
    _reservation_done: bool = field(default=False, init=False, repr=False)
    _claims_released: bool = field(default=False, init=False, repr=False)
    _result_recorded: bool = field(default=False, init=False, repr=False)
    _row_written: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.request_id is None:
            self.request_id = ensure_request_id()

    # -- observations -----------------------------------------------------------

    @property
    def usage_holder(self) -> SourceUsageHolder | None:
        return self.stream.usage_holder if self.stream is not None else None

    def observe_stream(self) -> SourceUsageHolder | None:
        """Mirror the parser's observations (first frame, first output item, delta chars).

        ``content_delivered`` is deliberately not mirrored: the holder's flag is
        the body's flush-point evidence, delivery is decided by
        ``settlement_stream`` from the frames it hands to the transport.
        """

        holder = self.usage_holder
        if holder is None:
            return None
        if self.first_frame_at is None and holder.first_frame_at is not None:
            self.first_frame_at = holder.first_frame_at
        self.first_output_item_seen = self.first_output_item_seen or holder.first_output_item_seen
        self.delta_chars = max(self.delta_chars, holder.delta_chars)
        return holder

    @property
    def pin_failure_row_code(self) -> str:
        return self.pin_unverified_error_code if self.pin_outcome == "unknown" else self.pin_failure_error_code

    def observe_first_output_item(self) -> None:
        """The body released the source's first output item past the pin hook: a half-open breaker trial closes now.

        Called once by ``settlement_stream`` (design §8.3, CL-2); the
        concurrency slot stays held until ``finish()``. Failure-isolated: the
        breaker never breaks a stream that is delivering.
        """

        self.first_output_item_seen = True
        try:
            self.claims.observe_first_output_item()
        except Exception:
            logger.warning(
                "source_dispatch_first_output_item_failed request_id=%s source_id=%s",
                self.request_id,
                self.source.id,
                exc_info=True,
            )

    # -- hooks ----------------------------------------------------------------------

    async def on_first_content(self, holder: SourceUsageHolder) -> None:
        """Pin hook awaited before the first content frame; raises ``SourcePinCommitError``.

        The executor bounds acquisition only and verifies the outcome; anything
        but ``written`` fails the lifecycle closed (design §8.5) -- never
        "proceed unpinned".
        """

        if self.pin_intent is None or self.pin_executor is None:
            return
        # The source response id is known only now: an anchored intent appends
        # the anchor row here so both rows land in one transaction (design §3,
        # §6.3). An intent that still owes its anchor at this point (the source
        # minted no ``response.id`` yet) is refused by the executor
        # (``anchor_pending`` -> ``not_written``, nothing issued), so no content
        # frame is ever delivered unanchored (decision 78).
        outcome = await self.pin_executor.commit(
            self.pin_intent.resolve(holder.response_id),
            drain_until=self.drain_until,
            scheduler=self.scheduler,
            clock=self.clock,
        )
        self.pin_outcome = outcome
        if outcome != "written":
            logger.warning(
                "model_source_pin_write outcome=%s request_id=%s source_id=%s",
                outcome,
                self.request_id,
                self.source.id,
            )
            raise SourcePinCommitError(outcome)

    # -- latched steps ----------------------------------------------------------------

    async def close_source(self) -> None:
        if self._source_closed:
            return
        self._source_closed = True
        stream = self.stream
        if stream is None:
            return
        try:
            await _await_cleanup_deferring_cancellation(stream.aclose(), scheduler=self.scheduler)
        except Exception:
            logger.warning(
                "model_source_dispatch_close_failed request_id=%s source_id=%s",
                self.request_id,
                self.source.id,
                exc_info=True,
            )

    async def settle_or_release(self, status: DispatchStatus, usage: SourceUsage | None) -> None:
        if self._reservation_done:
            return
        self._reservation_done = True
        reservation = self.reservation
        if reservation is None:
            return
        self.observe_stream()
        if status == "error":
            await self._release_reservation_step(reservation)
            return
        if usage is not None:
            await self._settle_reservation_step(reservation, usage, cause=None)
            return
        if status == "success" and _reservation_requires_usage(reservation):
            await self._settle_reservation_step(reservation, self._estimate(), cause="missing_usage")
            return
        if status == "cancelled" and self.content_delivered and _reservation_requires_usage(reservation):
            # Keyed on a content frame the settlement layer handed to the
            # transport, not on the parser having seen an output item nor on
            # the body having flushed a chunk: a delta-only or completed-only
            # answer that was relayed is charged; an output item still withheld
            # ahead of an unfinished pin write, a vendor event the public
            # contract dropped or a delta parked ahead of ``response.created``
            # is not.
            await self._settle_reservation_step(reservation, self._estimate(), cause="client_cancel")
            return
        await self._release_reservation_step(reservation)

    def _estimate(self) -> SourceUsage:
        return estimate_settlement_usage(admission_budget=self.admission_budget, delta_chars=self.delta_chars)

    async def _settle_reservation_step(
        self,
        reservation: ApiKeyUsageReservationData,
        usage: SourceUsage,
        *,
        cause: EstimateCause | None,
    ) -> None:
        settle = self.settle_reservation
        if settle is None:
            logger.error(
                "source_dispatch_missing_settler request_id=%s source_id=%s reservation_id=%s",
                self.request_id,
                self.source.id,
                getattr(reservation, "reservation_id", None),
            )
            await self._release_reservation_step(reservation)
            return
        if cause is not None:
            logger.warning(
                "source_usage_missing_settled_at_estimate cause=%s request_id=%s source_id=%s key_id=%s model=%s "
                "input_tokens=%d output_tokens=%d delta_chars=%d",
                cause,
                self.request_id,
                self.source.id,
                self.api_key.id if self.api_key is not None else None,
                self.model,
                usage.input_tokens,
                usage.output_tokens,
                self.delta_chars,
            )
            _inc(model_source_usage_estimated_total, source_id=self.source.id, cause=cause)
        settled = False
        try:
            settled, _cancellation = await _await_result_deferring_cancellation(
                settle(
                    reservation,
                    source=self.source,
                    model=self.model,
                    usage=usage,
                    cost_usd_override=source_usage_cost_usd(self.source, self.model, usage),
                ),
                scheduler=self.scheduler,
            )
        except Exception:
            logger.warning(
                "source_dispatch_settlement_raised request_id=%s source_id=%s",
                self.request_id,
                self.source.id,
                exc_info=True,
            )
            await self._release_reservation_step(reservation)
        if not settled:
            self.settlement_failed = True

    async def _release_reservation_step(self, reservation: ApiKeyUsageReservationData) -> None:
        release = self.release_reservation
        if release is None:
            logger.error(
                "source_dispatch_missing_releaser request_id=%s source_id=%s reservation_id=%s",
                self.request_id,
                self.source.id,
                getattr(reservation, "reservation_id", None),
            )
            return
        try:
            await _await_cleanup_deferring_cancellation(release(reservation), scheduler=self.scheduler)
        except Exception:
            logger.warning(
                "source_dispatch_release_failed request_id=%s source_id=%s",
                self.request_id,
                self.source.id,
                exc_info=True,
            )
            if self.cleanup_scheduler is not None:
                self.cleanup_scheduler._schedule_cancel_safe_cleanup(
                    release(reservation),
                    action="source_dispatch_release_retry",
                    request_id=self.request_id or ensure_request_id(),
                )

    def release_claims(self, trial_result: TrialResult) -> None:
        if self._claims_released:
            return
        self._claims_released = True
        try:
            self.claims.release(trial_result)
        except Exception:
            logger.warning("source_dispatch_claims_release_failed request_id=%s", self.request_id, exc_info=True)

    def record_result(self, status: DispatchStatus) -> None:
        if self._result_recorded:
            return
        self._result_recorded = True
        _inc(model_source_dispatch_total, kind=self.dispatch_kind, status=status)

    def _notify_finished(self, status: DispatchStatus) -> None:
        hook = self.on_finished
        if hook is None:
            return
        try:
            hook(self, status)
        except Exception:  # the hook never breaks the latch
            logger.warning(
                "source_dispatch_on_finished_failed request_id=%s source_id=%s",
                self.request_id,
                self.source.id,
                exc_info=True,
            )

    async def write_row(
        self,
        *,
        status: DispatchStatus,
        error_code: str | None,
        error_message: str | None,
        usage: SourceUsage | None,
        timings: SourceTimings | None,
        upstream_status_code: int | None,
    ) -> None:
        if self._row_written:
            return
        self._row_written = True
        request = self.request
        headers = request.headers
        holder = self.usage_holder
        proxy_request_id = self.request_id or ensure_request_id()
        source_response_id = self.source_response_id
        if source_response_id is None and holder is not None and holder.response_id:
            source_response_id = holder.response_id
        _useragent, _useragent_group, conversation_id = _request_log_client_fields(headers)
        try:
            async with get_background_session() as session:
                await RequestLogsRepository(session).add_log(
                    account_id=None,
                    request_id=source_response_id or proxy_request_id,
                    archive_request_id=proxy_request_id,
                    model_source_id=self.source.id,
                    model_source_kind=self.source.kind,
                    api_key_id=self.api_key.id if self.api_key is not None else None,
                    session_id=_owner_lookup_session_id_from_headers(headers),
                    model=self.model,
                    input_tokens=usage.input_tokens if usage is not None else None,
                    output_tokens=usage.output_tokens if usage is not None else None,
                    cached_input_tokens=usage.cached_input_tokens if usage is not None else None,
                    cost_usd=source_usage_cost_usd(self.source, self.model, usage),
                    latency_ms=timings.latency_ms if timings is not None else None,
                    latency_first_token_ms=timings.latency_first_token_ms if timings is not None else None,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    upstream_status_code=upstream_status_code,
                    transport="http",
                    upstream_transport="openai_compatible_http",
                    source=self.request_log_source,
                    requested_service_tier=self.requested_service_tier,
                    service_tier=None,
                    useragent=headers.get("user-agent"),
                    conversation_id=conversation_id,
                    client_ip=resolve_request_client_host(request),
                )
        except Exception:
            logger.warning(
                "failed to write source dispatch request log request_id=%s source_id=%s model=%s status=%s",
                proxy_request_id,
                self.source.id,
                self.model,
                status,
                exc_info=True,
            )

    # -- the latch ----------------------------------------------------------------------

    async def finish(
        self,
        *,
        status: DispatchStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: SourceUsage | None = None,
        upstream_status_code: int | None = None,
        trial_result: TrialResult = "inconclusive",
        timings: SourceTimings | None = None,
    ) -> None:
        """``close_source -> settle_or_release -> release_claims + record_result + on_finished -> write_row``.

        Idempotent (``finished`` is the latch).

        Every step is awaited with cancellation deferred and isolated from the
        others: a failing release never skips the row, a failing row write
        never raises. A ``success`` whose settlement fell back to a release is
        recorded as ``error usage_settlement_failed``.
        """

        if self.finished:
            return
        self.finished = True
        holder = self.observe_stream()
        if usage is None and holder is not None:
            usage = holder.usage
        if timings is None and holder is not None:
            timings = holder.timings
        if upstream_status_code is None and self.stream is not None:
            upstream_status_code = self.stream.upstream_status_code
        try:
            await _await_cleanup_deferring_cancellation(self.close_source(), scheduler=self.scheduler)
        finally:
            try:
                await _await_cleanup_deferring_cancellation(
                    self.settle_or_release(status, usage), scheduler=self.scheduler
                )
            finally:
                if self.settlement_failed:
                    status = "error"
                    error_code = ERROR_USAGE_SETTLEMENT_FAILED
                    error_message = "source usage settlement failed"
                try:
                    self.release_claims(trial_result)
                    self.record_result(status)
                    self._notify_finished(status)
                finally:
                    await _await_cleanup_deferring_cancellation(
                        self.write_row(
                            status=status,
                            error_code=error_code,
                            error_message=error_message,
                            usage=usage,
                            timings=timings,
                            upstream_status_code=upstream_status_code,
                        ),
                        scheduler=self.scheduler,
                    )

    async def finish_with_forwarding_error(self, exc: ModelSourceForwardingError) -> None:
        """``finish("error")`` for a pre-body or mid-stream ``ModelSourceForwardingError`` (deadlines counted)."""

        if exc.timeout_phase is not None and not self.finished:
            _inc(model_source_timeout_total, phase=exc.timeout_phase)
        await self.finish(
            status="error",
            error_code=error_code_from_payload(exc.payload),
            error_message=error_message_from_payload(exc.payload),
            upstream_status_code=exc.upstream_status_code,
            trial_result=forwarding_error_trial_result(exc),
        )

    async def abandon(self, reason: str) -> None:
        """Cancellation-class ``finish()`` for a client that left during the open / before the body."""

        if self.finished:
            return
        pending_seconds = max(0.0, self.clock.monotonic() - self.sent_at) if self.sent_at else 0.0
        stage = _ABANDON_STAGES.get(reason, "during_open")
        logger.warning(
            "source_dispatch_abandoned stage=%s reason=%s pending_seconds=%.3f frame_seen=%s "
            "request_id=%s source_id=%s",
            stage,
            reason,
            pending_seconds,
            self.first_frame_at is not None,
            self.request_id,
            self.source.id,
        )
        _inc(model_source_dispatch_abandoned_total, stage=stage)
        await self.finish(
            status="cancelled",
            error_code=reason,
            error_message="client left before the model-source response was delivered",
            trial_result="failure" if reason == ABANDON_SOURCE_STALL else "inconclusive",
        )

    async def finalize_transport(self) -> None:
        """``aclose()`` the body, then ``finish("cancelled", "client_disconnected_before_body")`` if unfinished.

        ``aclose()`` on a started body drives the settlement generator's own
        cleanup (which calls ``finish``); on a never-started body it skips the
        generator entirely, which is why the unconditional check follows.
        """

        body = self.body
        if body is not None:
            aclose = getattr(body, "aclose", None)
            if aclose is not None:
                try:
                    await _await_cleanup_deferring_cancellation(aclose(), scheduler=self.scheduler)
                except Exception:
                    logger.warning(
                        "source_dispatch_body_close_failed request_id=%s source_id=%s",
                        self.request_id,
                        self.source.id,
                        exc_info=True,
                    )
        if not self.finished:
            await self.abandon(ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY)


class TransportOwner(Protocol):
    """Owner of a source transport whose ``finalize_transport()`` a ``SourceStreamingResponse`` always reaches.

    ``body`` is the outermost iterator handed to Starlette (assigned by the
    response); ``finalize_transport`` closes it and settles whatever a body that
    never started could not settle itself. ``SourceDispatch`` implements it for
    Responses dispatch, ``SourceChatStreamOwner`` for the direct chat route.
    """

    scheduler: Scheduler
    body: AsyncIterator[Any] | None

    async def finalize_transport(self) -> None: ...


@dataclass(slots=True)
class SourceChatStreamOwner:
    """Transport owner for the direct chat-completions stream route, which has no dispatch owner.

    The route's settlement generator wrapped around ``stream.body`` is the sole
    body iterator: it releases the reservation and writes the row, but only
    once Starlette iterates the body. Between the route returning and
    ``http.response.start`` completing there is one await in which a client
    departure (task cancellation) or a failed first write leaves the generator
    never started, and ``aclose()`` on a never-started async generator skips
    its ``finally`` -- the upstream response, the pooled lease and the
    reservation were held until garbage collection. ``finalize_transport``
    mirrors ``SourceDispatch.finalize_transport``: it closes the body (a
    started body settles itself), then, when the generator never ran, closes
    the source transport directly and runs ``on_abandoned_before_body`` (the
    route's reservation release and ``cancelled`` row). Every await defers
    cancellation; the finalizer is idempotent.
    """

    stream: SourceChatStream
    # Route-owned: releases the reservation and writes the ``cancelled`` row
    # with ``ABANDON_CLIENT_DISCONNECTED_BEFORE_BODY`` (``api.py`` owns both).
    on_abandoned_before_body: Callable[[], Awaitable[None]]
    scheduler: Scheduler = REAL_SCHEDULER
    # Outermost body iterator handed to ``SourceStreamingResponse`` (assigned by it).
    body: AsyncIterator[bytes] | None = None
    # Set by the settlement generator on entry: from then on it owns the outcome
    # through its own except/finally paths, whether it completes, is cancelled
    # mid-stream or is closed by the finalizer below.
    body_started: bool = False
    # Latch for the abandoned-before-body outcome recorded by the finalizer.
    finished: bool = False

    async def finalize_transport(self) -> None:
        body = self.body
        if body is not None:
            aclose = getattr(body, "aclose", None)
            if aclose is not None:
                try:
                    await _await_cleanup_deferring_cancellation(aclose(), scheduler=self.scheduler)
                except Exception:
                    logger.warning("source_chat_stream_body_close_failed", exc_info=True)
        if self.body_started or self.finished:
            return
        self.finished = True
        try:
            await _await_cleanup_deferring_cancellation(self.stream.aclose(), scheduler=self.scheduler)
        except Exception:
            logger.warning("source_chat_stream_close_failed", exc_info=True)
        await _await_cleanup_deferring_cancellation(self.on_abandoned_before_body(), scheduler=self.scheduler)


class SourceStreamingResponse(StreamingResponse):
    """``StreamingResponse`` whose transport exit always reaches ``owner.finalize_transport()``."""

    def __init__(
        self,
        content: AsyncIterator[str] | AsyncIterator[bytes],
        *,
        owner: TransportOwner,
        media_type: str = "text/event-stream",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self.owner = owner
        owner.body = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await _await_cleanup_deferring_cancellation(self.owner.finalize_transport(), scheduler=self.owner.scheduler)


async def open_with_disconnect_watch(request: Request, owner: SourceDispatch, coro: Coroutine[Any, Any, T]) -> T:
    """Run the source open while polling ``request.is_disconnected()``; raises ``ClientDisconnectedDuringOpen``.

    Owned concurrency (CL-5): the open runs as a task the watch always cancels
    and awaits to completion, so a stream that completed into a leaving caller
    is closed by ``abandon()`` (the open coroutine assigns ``owner.stream``
    itself before it completes), and the child's exception never replaces the
    caller's.
    """

    if not owner.sent_at:
        owner.sent_at = owner.clock.monotonic()
    task = owner.scheduler.create_task(coro)
    consumed = False
    try:
        while True:
            done, _pending = await owner.scheduler.wait({task}, timeout=OPEN_DISCONNECT_POLL_SECONDS)
            if done:
                # Completion observed before the poll is never dropped.
                consumed = True
                return task.result()
            if await request.is_disconnected():
                pending_seconds = max(0.0, owner.clock.monotonic() - owner.sent_at)
                stall = owner.first_frame_at is None and pending_seconds >= STALL_EVIDENCE_SECONDS
                raise ClientDisconnectedDuringOpen(pending_seconds=pending_seconds, stall=stall)
    finally:
        if not task.done():
            task.cancel()
        if not consumed:
            # Always awaited: a child that completed in the disconnect
            # iteration has assigned ``owner.stream`` (closed by ``abandon()``);
            # a child that failed is logged and never replaces our exception.
            try:
                await _await_task_deferring_cancellation(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning(
                    "model_source_open_failed_after_abandonment request_id=%s source_id=%s",
                    owner.request_id,
                    owner.source.id,
                    exc_info=True,
                )


async def _aclose_best_effort(stream: object, *, scheduler: Scheduler) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await _await_cleanup_deferring_cancellation(aclose(), scheduler=scheduler)
    except Exception:
        logger.debug("source stream layer close failed", exc_info=True)


async def settlement_stream(owner: SourceDispatch, wrapped: AsyncIterator[str]) -> AsyncIterator[str]:
    """Outermost body layer: settles/releases on the terminal outcome and calls ``owner.finish()`` in ``finally``.

    A disconnect surfaces as ``CancelledError`` (task cancellation) or
    ``GeneratorExit`` (``aclose()`` from ``finalize_transport``); both bypass
    ``except Exception`` and are recorded as ``cancelled`` (the settle policy
    decides between release and estimate) unless the client already received a
    terminal: a relayed failure terminal is an ``error``, a relayed success
    terminal a ``success``. A verified pin non-write yields the
    synthesized ``response.created`` + ``response.failed`` pair after the
    reservation was released and the source closed.

    Delivery is decided here as well: ``owner.content_delivered`` is set when
    an event frame ``relayed_frame_delivers_content`` classifies as content is
    handed to the transport, so the cancel estimate charges only for frames
    that left the proxy. The body's flush point sits below the public wrapper,
    which drops events outside ``response.*`` / ``error`` under the SDK
    contract, parks ``response.*`` frames ahead of ``response.created`` and
    holds reasoning-summary deltas; a chunk the body flushed may therefore
    never reach the client (the one ``send()`` in flight when the client
    leaves is the residual ambiguity, resolved as delivered).

    The client-visible terminal is latched from the relayed event frames, not
    read from the last one: a typed event trailing a terminal (a delta the
    source keeps emitting after the wrapper rewrote its ``response.completed``
    into ``response.failed``, a vendor frame after ``response.completed``)
    must not reset the classification, and a relayed failure terminal is
    sticky -- once the client received a failure, nothing that follows turns
    the attempt back into a charged success. The latch costs one
    ``startswith`` and a string slice per ``event:``-framed event; only a raw
    data-only pass-through block (a native-mode terminal, an unparseable
    block) pays a JSON parse.
    """

    status: DispatchStatus = "success"
    error_code: str | None = None
    error_message: str | None = None
    completed_normally = False
    timeout_phase: TimeoutPhase | None = None
    forwarding_error: ModelSourceForwardingError | None = None
    relayed_kind: str | None = None
    holder = owner.usage_holder
    # One attribute read per chunk until the parser reports the first output
    # item: a half-open breaker trial closes at the item, not at the terminal
    # (design §8.3). The parser runs ahead of this layer, so the flag is set by
    # the time the frame carrying the item -- or the bookkeeping the body
    # flushed ahead of it behind the pin hook -- reaches the transport.
    awaiting_first_item = holder is not None and not owner.first_output_item_seen
    owner.body_started = True
    try:
        async for chunk in wrapped:
            if awaiting_first_item and holder is not None and holder.first_output_item_seen:
                awaiting_first_item = False
                owner.observe_first_output_item()
            if _is_event_frame(chunk):
                if relayed_kind not in _FAILURE_TERMINAL_KINDS:
                    frame_kind = relayed_terminal_kind(chunk)
                    if frame_kind is not None:
                        relayed_kind = frame_kind
                if not owner.content_delivered and relayed_frame_delivers_content(chunk):
                    owner.content_delivered = True
            yield chunk
        completed_normally = True
        holder = owner.observe_stream()
        if holder is not None:
            if holder.terminal_kind in _FAILURE_TERMINAL_KINDS:
                # The public wrapper relays a failure terminal and ends the
                # stream normally; a limited key must not be charged (not even
                # at the estimate) for an answer the source never produced.
                status = "error"
                error_code = ERROR_MODEL_SOURCE_RESPONSE_FAILED
                error_message = f"source terminated the stream with response.{holder.terminal_kind}"
            elif holder.terminal_kind in _SUCCESS_TERMINAL_KINDS and relayed_kind in _FAILURE_TERMINAL_KINDS:
                # The parser read the source's success terminal, but the public
                # contract rewrote it into ``response.failed`` (``response`` not
                # an object, invalid output items): the client received a
                # failure, so the row and the settlement follow the wire, not
                # the parser's observation.
                status = "error"
                error_code = ERROR_MODEL_SOURCE_RESPONSE_INVALID
                error_message = f"source response.{holder.terminal_kind} was relayed to the client as response.failed"
            elif holder.terminal_kind is None and relayed_kind not in _SUCCESS_TERMINAL_KINDS:
                # The source closed without a terminal the parser could read
                # and the client did not receive a success terminal either (the
                # wrapper synthesized ``response.failed`` or relayed nothing
                # usable): no answer was delivered, so this is an error and the
                # reservation is released. A success terminal that only
                # outgrew the parser's frame cap still reached the client and
                # stays a success (settled at the estimate for a limited key).
                status = "error"
                error_code = ERROR_MODEL_SOURCE_STREAM_TRUNCATED
                error_message = "source ended the stream without a terminal event"
    except (asyncio.CancelledError, GeneratorExit):
        # A client that disconnects right after a relayed failure terminal --
        # Codex tears the stream down on ``response.failed``/``error`` while
        # many sources still send ``[DONE]``/keepalives before their own EOF --
        # received a failure, not a partial answer, so this is an ``error`` that
        # releases the reservation, never a cancel-after-first-item estimate
        # (design §6.4; api-keys "Failure terminal is never charged"). The
        # completed-normally branch already classifies this; the cancel path
        # keyed only on ``first_output_item_seen`` and charged the estimate.
        owner.observe_stream()
        holder = owner.usage_holder
        if (
            holder is not None and holder.terminal_kind in _FAILURE_TERMINAL_KINDS
        ) or relayed_kind in _FAILURE_TERMINAL_KINDS:
            status = "error"
            error_code = ERROR_MODEL_SOURCE_RESPONSE_FAILED
            error_message = "source terminated the stream with a failure terminal"
        elif relayed_kind in _SUCCESS_TERMINAL_KINDS:
            # Symmetric: the client received the success terminal before
            # leaving (Codex tears the stream down on ``response.completed``
            # while the source still sends ``[DONE]``/keepalives), so the whole
            # answer was delivered and this is a success settled like a stream
            # that ran to the source's EOF, never a cancel.
            status = "success"
        else:
            status = "cancelled"
            error_code = CANCELLED_CLIENT_DISCONNECTED
            error_message = "client disconnected before stream completed"
        # ``async for`` does not close the inner layer on an exception; the
        # wrapper's own ``finally`` blocks release everything below it.
        await _aclose_best_effort(wrapped, scheduler=owner.scheduler)
        raise
    except SourcePinCommitError:
        status = "error"
        error_code = owner.pin_failure_row_code
        error_message = "model-source pin write did not verify as durable"
        await owner.finish(status=status, error_code=error_code, error_message=error_message)
        holder = owner.usage_holder
        for frame in synthesized_pin_failure_frames(
            holder.created_envelope if holder is not None else None,
            error_code=owner.pin_failure_error_code,
        ):
            yield frame
        return
    except ModelSourceForwardingError as exc:
        status = "error"
        error_code = error_code_from_payload(exc.payload)
        error_message = error_message_from_payload(exc.payload)
        timeout_phase = exc.timeout_phase
        forwarding_error = exc
        raise
    except Exception as exc:
        status = "error"
        error_code = ERROR_MODEL_SOURCE_STREAM
        error_message = exc.__class__.__name__
        raise
    finally:
        if owner.finished:
            pass
        else:
            if timeout_phase is not None:
                _inc(model_source_timeout_total, phase=timeout_phase)
            owner.observe_stream()
            cancellation = await _await_cleanup_deferring_cancellation(
                owner.finish(
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    trial_result=stream_trial_result(
                        status,
                        error_code=error_code,
                        first_output_item_seen=owner.first_output_item_seen,
                        forwarding_error=forwarding_error,
                    ),
                ),
                scheduler=owner.scheduler,
            )
            if completed_normally and cancellation is not None:
                # The client left while the settlement was being committed: the
                # reservation is settled, the row is written; honour the
                # cancellation instead of continuing to a departed transport.
                raise asyncio.CancelledError
