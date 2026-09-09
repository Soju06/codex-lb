"""WebSocket parity for subscription-exhaustion overflow (#2123 WP-D, folded into WP-C2).

Two helpers the websocket mixin calls from exactly two sites; every decision,
row write and event shape lives here (design v3 §3, §6, §7.3, §7.5).

* ``bounce_exhausted_websocket_turn`` -- called only after account selection
  answered ``usage_limit_reached`` (exhaustion is established; nothing here
  probes). When the turn would be eligible for fresh overflow over HTTP it is
  bounced in-band: a 60 s drain-capped bounce row keyed by the thread (so the
  client's re-handshake meets the 426 denial on any replica) and the existing
  wrapped 503 connect-failure event, whose top-level numeric ``status`` and
  code ``model_source_requires_http_transport`` make Codex retry, re-handshake
  and switch that session to HTTP. Never ``server_is_overloaded`` /
  ``slow_down``: Codex treats those as non-retryable. Every decline leaves
  today's 429 event to the caller, byte-identical.
* ``bounce_pinned_or_anchored_websocket_turn`` -- one bounded thread-pin lookup
  (``live``/``expired`` -> bounce) and, for a ``previous_response_id`` without a
  recorded subscription owner, one anchor lookup (``live`` -> bounce). A lookup
  timeout or infrastructure error bounces too: fail-closed toward HTTP, where
  the decision answers 503 for the pinned context (I7, CL-12). It runs before
  the socket-reuse guard, so a first turn and a reused socket behave alike,
  and it owns the prepared turn across its own awaits: the mixin calls it
  after the turn's API-key usage was reserved and before the turn is
  registered in ``pending_requests`` (the scope cleanup fails registered
  turns only), so a scope cancellation delivered inside the settings read, a
  lookup or the bounce-row write releases the reservation and writes the
  turn's ``cancelled`` row before it propagates (I13 on this transport).

Ship-dark (I9): both helpers start with two attribute reads on the already-warm
settings row and one comparison; with the designation and the drain deadline
both off they return ``False`` without a probe, lookup, selection or body walk.
The WebSocket decision is advisory: the HTTP route re-decides authoritatively
after the client's session-scoped downgrade. Handshake 426 denial lives in
``api.py`` via ``app.modules.proxy.overflow.handshake_denial``.

Zero timing allowance: the bounded lookups and the bounce write take the proxy
service's ``Scheduler``/``Clock``; the only owned task is the
cancellation-deferred disposal of a prepared turn, spawned through the
scheduler seam.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.clock import clock_for, scheduler_for
from app.core.errors import openai_error
from app.core.metrics import prometheus as _prometheus
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation
from app.modules.model_sources.projection import strip_source_telemetry
from app.modules.model_sources.selection import select_overflow_model_source
from app.modules.proxy.model_source_pins import (
    PIN_KIND_BOUNCE,
    PinIntent,
    PinLookupTimeout,
    PinWrite,
    PinWriteExecutor,
    anchor_pin_key,
    bounce_pin_key,
    drain_deadline_from_settings,
    lookup_pin_bounded,
    thread_pin_key,
)
from app.modules.proxy.overflow import (
    ROUTE_WEBSOCKET,
    WS_BOUNCE_CODE,
    fresh_decline_reason,
    get_fast_decline_set,
    get_source_breaker,
    overflow_thread_key,
    portability_decline,
)

if TYPE_CHECKING:
    import anyio
    from fastapi import WebSocket

    from app.core.types import JsonValue
    from app.modules.api_keys.service import ApiKeyData
    from app.modules.proxy._service.support import _WebSocketRequestState
    from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol
    from app.modules.proxy.overflow import FastDeclineSet

__all__ = [
    "OUTCOME_BOUNCED_WS_EVENT",
    "OUTCOME_DECISION_ERROR",
    "OUTCOME_PINNED_LOOKUP_TIMEOUT",
    "WS_BOUNCE_FRESH_MESSAGE",
    "WS_BOUNCE_PINNED_MESSAGE",
    "bounce_exhausted_websocket_turn",
    "bounce_pinned_or_anchored_websocket_turn",
]

logger = logging.getLogger(__name__)

# ``outcome`` labels recorded under ``route="websocket"`` (closed enum, design §11).
OUTCOME_BOUNCED_WS_EVENT = "bounced_ws_event"
OUTCOME_PINNED_LOOKUP_TIMEOUT = "pinned_lookup_timeout"
OUTCOME_DECISION_ERROR = "decision_error"

# Decline labels this helper emits itself (the pure helpers return the rest).
_DECLINED_DRAIN_MODE = "declined_drain_mode"
# The designated-source query answers "source unusable" and "model unlisted"
# alike with ``None``; the HTTP decision refines the label after the downgrade.
_DECLINED_NO_SOURCE = "declined_no_source"
_DECLINED_NOT_PORTABLE_INPUT = "declined_not_portable_input"

# Client-visible messages of the in-band bounce. The source is never named
# (design §11); Codex suppresses the first retry notice anyway.
WS_BOUNCE_FRESH_MESSAGE = (
    "The subscription pool is exhausted and this turn can be served by the configured overflow model source, "
    "which is only reachable over the HTTP transport; retry the request over HTTPS."
)
WS_BOUNCE_PINNED_MESSAGE = (
    "This conversation is served by an overflow model source, which is only reachable over the HTTP "
    "transport; retry the request over HTTPS."
)

# Bounce rows go through the base executor on purpose: a bounce write that
# does not land must not fast-decline the thread -- the row is a latency
# optimisation (Codex re-handshakes on every retry), not a correctness
# requirement (design §7.3).
_BOUNCE_EXECUTOR = PinWriteExecutor()

# Row of a prepared turn the scope cancelled before it was registered: the code,
# message and status the session's own scope cleanup writes for a registered
# turn (``finalize_websocket_scope``).
_CANCELLED_TURN_CODE = "stream_incomplete"
_CANCELLED_TURN_MESSAGE = "Websocket scope cancelled before response.completed"


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


def _count(outcome: str) -> None:
    counter = getattr(_prometheus, "subscription_overflow_total", None)
    if counter is None:
        return
    counter.labels(route=ROUTE_WEBSOCKET, outcome=outcome).inc()


def _request_id(request_state: _WebSocketRequestState) -> str:
    return request_state.request_log_id or request_state.request_id


def _fast_decline_set() -> FastDeclineSet:
    # The process-wide set the overflow pin executor marks on every
    # non-``written`` pin commit, so a thread that just failed to pin over HTTP
    # is not bounced back to it within the 60 s window.
    return get_fast_decline_set()


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _overflow_window(proxy: object) -> tuple[str | None, datetime | None] | None:
    """``(designated_source_id, drain_until)``, or ``None`` when overflow is off -- the ship-dark fast path.

    Two attribute reads on the warm settings row and one comparison. The drain
    deadline is materialised only once it is armed; an elapsed deadline
    counts as off.
    """

    settings = await _facade().get_settings_cache().get()
    designated = getattr(settings, "subscription_overflow_source_id", None)
    if designated is None and getattr(settings, "subscription_overflow_drain_until", None) is None:
        return None
    drain_until = drain_deadline_from_settings(settings)
    if designated is None and (drain_until is None or _aware_utc(clock_for(proxy).now()) >= drain_until):
        return None
    return designated, drain_until


def _frame_body(request_state: _WebSocketRequestState) -> dict[str, JsonValue] | None:
    """The turn's Responses body as the HTTP route would judge it: the frame minus its ``type`` envelope, stripped.

    ``request_text`` is the frame of a turn that already reached an upstream;
    the connect path of a fresh turn carries the frame in
    ``fresh_upstream_request_text``. A turn with neither is not judged.
    """

    text = request_state.request_text or request_state.fresh_upstream_request_text
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    payload.pop("type", None)
    return strip_source_telemetry(payload, strip_service_tier=True)


def _fresh_turn_is_judged(request_state: _WebSocketRequestState) -> bool:
    """Turns the fresh-overflow bounce never applies to; each keeps today's 429 event."""

    if request_state.require_security_work_authorized:
        # Capability sessions resolve only on this transport and are exempt
        # from the handshake 426; bouncing one would loop it into the HTTP
        # path's capability 400 (api.py).
        return False
    if request_state.previous_response_id is not None or request_state.proxy_injected_previous_response_id:
        # The HTTP decision never fresh-overflows a continuation (§4.2 (3));
        # source-anchored chains are the pinned helper's job.
        return False
    if request_state.precreated_replay_reason is not None:
        # A pre-acceptance rejection is being replayed; the connect-failure
        # emitter substitutes the original upstream error for whatever we pass.
        return False
    return True


async def _fresh_eligibility(
    proxy: object,
    *,
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    designated: str,
) -> str | tuple[str | None, str]:
    """A decline outcome label, or ``(thread_key, source_id)`` when the turn would overflow fresh over HTTP.

    Mirrors the HTTP decision's pre-dispatch steps (design §4.2 (4), (6), (7))
    on the frame body; advisory -- the HTTP route re-decides.
    """

    thread_key = overflow_thread_key(headers)
    reason = fresh_decline_reason(
        headers,
        api_key,
        thread_key=thread_key,
        source_route_excluded=request_state.source_route_excluded,
        fast_decline=_fast_decline_set(),
        breaker=get_source_breaker(),
        source_id=designated,
        now=clock_for(proxy).monotonic(),
    )
    if reason is not None:
        return f"declined_{reason}"
    resolved = await select_overflow_model_source(
        designated,
        request_state.model or "",
        api_key,
        raw_model=request_state.raw_source_model,
        require_streaming=True,
    )
    if resolved is None:
        return _DECLINED_NO_SOURCE
    source, model = resolved
    body = _frame_body(request_state)
    if body is None:
        logger.debug(
            "subscription_overflow_websocket_declined reason=no_frame_body request_id=%s", _request_id(request_state)
        )
        return _DECLINED_NOT_PORTABLE_INPUT
    reason, detail = portability_decline(body, headers, source=source, model=model)
    if reason is not None:
        logger.debug(
            "subscription_overflow_websocket_declined reason=%s detail=%s request_id=%s",
            reason,
            detail,
            _request_id(request_state),
        )
        return f"declined_{reason}"
    return thread_key, source.id


async def _write_bounce_row(
    proxy: object,
    *,
    thread_key: str,
    source_id: str,
    api_key_id: str | None,
    drain_until: datetime | None,
) -> None:
    intent = PinIntent(
        writes=(PinWrite(bounce_pin_key(thread_key), PIN_KIND_BOUNCE, source_id, api_key_id),),
        thread_key=thread_key,
    )
    try:
        outcome = await _BOUNCE_EXECUTOR.commit(
            intent,
            drain_until=drain_until,
            scheduler=scheduler_for(proxy),
            clock=clock_for(proxy),
        )
    except Exception:
        # Never fatal: the client's retry ladder re-handshakes per attempt and
        # the HTTP route re-decides. ``CancelledError`` is a ``BaseException``
        # and propagates.
        logger.warning("subscription_overflow_bounce_row outcome=error", exc_info=True)
        return
    if outcome != "written":
        logger.warning("subscription_overflow_bounce_row outcome=%s", outcome)


async def _bounce(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    thread_key: str | None,
    source_id: str | None,
    drain_until: datetime | None,
    message: str,
    outcome: str,
) -> None:
    """Bounce row (when the thread and its source are known) first, then the in-band 503 event, then the counter."""

    bounce_row = thread_key is not None and source_id is not None
    if thread_key is not None and source_id is not None:
        # Written before the event so the re-handshake this event triggers
        # finds the row on every replica.
        await _write_bounce_row(
            proxy,
            thread_key=thread_key,
            source_id=source_id,
            api_key_id=api_key.id if api_key is not None else None,
            drain_until=drain_until,
        )
    await _emit_bounce(
        proxy,
        websocket,
        client_send_lock=client_send_lock,
        api_key=api_key,
        request_state=request_state,
        thread_key_present=thread_key is not None,
        bounce_row=bounce_row,
        message=message,
        outcome=outcome,
    )


async def _emit_bounce(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    thread_key_present: bool,
    bounce_row: bool,
    message: str,
    outcome: str,
) -> None:
    """The in-band 503 event through the connect-failure emitter (which releases the reservation and writes the row),
    then the counter."""

    logger.info(
        "subscription_overflow_websocket_bounce outcome=%s request_id=%s thread_key_present=%s bounce_row=%s",
        outcome,
        _request_id(request_state),
        thread_key_present,
        bounce_row,
    )
    # ``_emit_websocket_connect_failure`` releases the turn's usage
    # reservation, finalizes its request-log row and sends the wrapped
    # ``{"type": "error", "status": 503, "error": {...}}`` event: a 503 with a
    # top-level numeric ``status`` is what Codex retries with a re-handshake
    # per attempt; a 4xx is terminal client-side and would strand the session.
    await proxy._emit_websocket_connect_failure(
        websocket,
        client_send_lock=client_send_lock,
        account_id=None,
        api_key=api_key,
        request_state=request_state,
        status_code=503,
        payload=openai_error(WS_BOUNCE_CODE, message, error_type="server_error"),
        error_code=WS_BOUNCE_CODE,
        error_message=message,
    )
    _count(outcome)


async def bounce_exhausted_websocket_turn(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    headers: Mapping[str, str],
) -> bool:
    """Called only when selection answered ``USAGE_LIMIT_REACHED`` (exhaustion established; no probe).

    ``True``: eligible -> bounce row written (when a thread key exists) and the
    in-band 503 bounce event emitted through ``proxy._emit_websocket_connect_failure``
    (reservation released, connect-failure row written). ``False``: not
    eligible -> today's 429 event is emitted by the caller unchanged.
    """

    window = await _overflow_window(proxy)
    if window is None:
        return False
    designated, drain_until = window
    if designated is None:
        # Drain mode: pinned conversations keep resolving, nothing fresh overflows (§8.8).
        _count(_DECLINED_DRAIN_MODE)
        return False
    if not _fresh_turn_is_judged(request_state):
        return False
    try:
        verdict = await _fresh_eligibility(
            proxy,
            headers=headers,
            api_key=api_key,
            request_state=request_state,
            designated=designated,
        )
    except Exception:
        # Never ``CancelledError`` (a ``BaseException``). A decision failure
        # keeps today's answer (§8.1): the caller emits the 429 event.
        logger.warning(
            "subscription_overflow_decision_error stage=websocket_fresh request_id=%s",
            _request_id(request_state),
            exc_info=True,
        )
        _count(OUTCOME_DECISION_ERROR)
        return False
    if isinstance(verdict, str):
        _count(verdict)
        return False
    thread_key, source_id = verdict
    await _bounce(
        proxy,
        websocket,
        client_send_lock=client_send_lock,
        api_key=api_key,
        request_state=request_state,
        thread_key=thread_key,
        source_id=source_id,
        drain_until=drain_until,
        message=WS_BOUNCE_FRESH_MESSAGE,
        outcome=OUTCOME_BOUNCED_WS_EVENT,
    )
    return True


@dataclass(frozen=True, slots=True)
class _PinnedBounce:
    """What the pinned/anchored lookups decided: the bounce row's key material (``None`` if unknown) and the outcome."""

    thread_key: str | None
    source_id: str | None
    outcome: str


async def _pinned_or_anchored_bounce(
    proxy: _WebSocketServiceProtocol,
    *,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    headers: Mapping[str, str],
) -> _PinnedBounce | None:
    """The lookups of ``bounce_pinned_or_anchored_websocket_turn``; ``None`` when the turn is not bounced."""

    scheduler = scheduler_for(proxy)
    clock = clock_for(proxy)
    thread_key: str | None = None
    try:
        thread_key = overflow_thread_key(headers)
        previous_response_id = (request_state.previous_response_id or "").strip()
        if thread_key is None and not previous_response_id:
            return None
        if thread_key is not None:
            pin = await lookup_pin_bounded(thread_pin_key(thread_key), cache=None, scheduler=scheduler, clock=clock)
            if pin.state in ("live", "expired") and pin.record is not None:
                return _PinnedBounce(thread_key, pin.record.source_id, OUTCOME_BOUNCED_WS_EVENT)
        if previous_response_id and request_state.previous_response_owner_account_id is None:
            api_key_id = api_key.id if api_key is not None else None
            anchor = await lookup_pin_bounded(
                anchor_pin_key(api_key_id, previous_response_id), cache=None, scheduler=scheduler, clock=clock
            )
            if anchor.state == "live" and anchor.record is not None:
                return _PinnedBounce(thread_key, anchor.record.source_id, OUTCOME_BOUNCED_WS_EVENT)
    except PinLookupTimeout:
        logger.warning("subscription_overflow_websocket_pin_lookup_timeout request_id=%s", _request_id(request_state))
        # No bounce row: the pin store just failed to answer, and the event
        # alone makes the client re-handshake (fail-closed toward HTTP).
        return _PinnedBounce(None, None, OUTCOME_PINNED_LOOKUP_TIMEOUT)
    except Exception:
        # Never ``CancelledError``. A thread-keyed or anchored turn whose pin
        # state is unknowable fails closed toward HTTP (CL-12), where the
        # decision answers 503 ``model_source_unavailable``.
        logger.warning(
            "subscription_overflow_decision_error stage=websocket_pin_lookup request_id=%s",
            _request_id(request_state),
            exc_info=True,
        )
        return _PinnedBounce(None, None, OUTCOME_DECISION_ERROR)
    return None


async def _dispose_prepared_turn(
    proxy: _WebSocketServiceProtocol,
    *,
    request_state: _WebSocketRequestState,
    api_key: ApiKeyData | None,
) -> None:
    """Release a prepared, unregistered turn's reservation and finalize its ``cancelled`` row, cancellation deferred.

    Each step is isolated: a failing release never skips the row and neither
    replaces the caller's ``CancelledError``. The reservation release is
    idempotent, so a later release by the mixin is harmless.
    """

    scheduler = scheduler_for(proxy)
    try:
        await _await_cleanup_deferring_cancellation(
            proxy._release_websocket_request_state_reservation(request_state), scheduler=scheduler
        )
    except Exception:
        logger.warning(
            "subscription_overflow_websocket_prepared_turn_release_failed request_id=%s",
            _request_id(request_state),
            exc_info=True,
        )
    try:
        await _await_cleanup_deferring_cancellation(
            proxy._write_websocket_connect_failure(
                account_id=None,
                api_key=api_key,
                request_state=request_state,
                error_code=_CANCELLED_TURN_CODE,
                error_message=_CANCELLED_TURN_MESSAGE,
                status="cancelled",
            ),
            scheduler=scheduler,
        )
    except Exception:
        logger.warning(
            "subscription_overflow_websocket_prepared_turn_row_failed request_id=%s",
            _request_id(request_state),
            exc_info=True,
        )
    logger.info("subscription_overflow_websocket_prepared_turn_cancelled request_id=%s", _request_id(request_state))


async def bounce_pinned_or_anchored_websocket_turn(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    headers: Mapping[str, str],
) -> bool:
    """One bounded thread-key lookup (``live``/``expired`` -> bounce) and, for ``previous_response_id`` without a
    recorded subscription owner, one anchor lookup (live -> bounce); ``PinLookupTimeout`` -> bounce.

    Works for the first turn (no upstream yet) and a reused socket alike; ``True`` means the caller ``continue``s.
    Lookups run while the designation is set or the drain deadline is armed
    (pinned conversations drain on their source, §8.8) and read the row
    directly: a pinned turn is bounced at once, and only the handshake denial
    keeps a positive replica cache.

    Cancellation (I13 on this transport): the caller has reserved the turn's
    API-key usage but not yet registered the turn in ``pending_requests``, and
    the session's scope cleanup fails registered turns only. Every await up to
    the connect-failure emitter -- the settings read, the lookups, the
    bounce-row write -- therefore runs under a guard that, on
    ``CancelledError``, releases the reservation and writes the turn's
    ``cancelled`` row with the cancellation deferred before re-raising. The
    emitter owns its own release and row and runs outside the guard, so
    nothing is disposed twice.
    """

    try:
        window = await _overflow_window(proxy)
        if window is None:
            return False
        _designated, drain_until = window
        bounce = await _pinned_or_anchored_bounce(proxy, api_key=api_key, request_state=request_state, headers=headers)
        if bounce is None:
            return False
        bounce_row = bounce.thread_key is not None and bounce.source_id is not None
        if bounce.thread_key is not None and bounce.source_id is not None:
            # Written before the event so the re-handshake this event triggers
            # finds the row on every replica.
            await _write_bounce_row(
                proxy,
                thread_key=bounce.thread_key,
                source_id=bounce.source_id,
                api_key_id=api_key.id if api_key is not None else None,
                drain_until=drain_until,
            )
    except asyncio.CancelledError:
        await _dispose_prepared_turn(proxy, request_state=request_state, api_key=api_key)
        raise
    await _emit_bounce(
        proxy,
        websocket,
        client_send_lock=client_send_lock,
        api_key=api_key,
        request_state=request_state,
        thread_key_present=bounce.thread_key is not None,
        bounce_row=bounce_row,
        message=WS_BOUNCE_PINNED_MESSAGE,
        outcome=bounce.outcome,
    )
    return True
