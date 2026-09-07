"""Replay accepted, output-free capacity failures within one response lifecycle.

Upstream can accept a ``response.create`` -- ``response.created`` and usually
``response.in_progress`` reach the client -- and then fail the turn before it
produces any output, either with a capacity terminal (``server_is_overloaded``,
``overloaded_error``, ``model_at_capacity``, or the "selected model is at
capacity" message) or by dropping the transport. Every side effect of a
Responses turn is reported as an output item or as billed output tokens, so
such a turn is as replay-safe as a pre-created failure. The helpers here let
the existing pre-created replay machinery (``replay_downstream_response_id`` +
prelude suppression + id rewriting) cover that state: the request is re-sent
once, normally on another account, while the client keeps reading the single
lifecycle it already observed. Quota and rate-limit terminals after acceptance
deliberately stay fail-closed; see the openspec change
``retry-accepted-output-free-capacity-failures``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from app.core.types import JsonValue
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_WEBSOCKET,
    _websocket_request_is_accepted_lifecycle_only,
    _WebSocketRequestState,
)
from app.modules.proxy.helpers import is_upstream_model_capacity_error

logger = logging.getLogger("app.modules.proxy.service")

_ACCEPTED_CAPACITY_REPLAY_ERROR_CODES = frozenset({"server_is_overloaded", "overloaded_error", "model_at_capacity"})
# Quota and rate-limit codes keep their stronger classification after
# acceptance even when the message names the selected-model capacity: an
# accepted turn that hit a quota wall may already be billed.
_ACCEPTED_REPLAY_FAIL_CLOSED_ERROR_CODES = frozenset(
    {"rate_limit_exceeded", "usage_limit_reached", "insufficient_quota", "usage_not_included", "quota_exceeded"}
)
_TERMINAL_EVENT_TYPES = frozenset({"error", "response.failed"})


def _websocket_accepted_replay_candidate(
    request_state: _WebSocketRequestState,
    *,
    has_other_pending_requests: bool,
) -> bool:
    """Return whether an accepted request may still be replayed once.

    Mirrors the fresh-replay rules of the pre-created path: a single pending
    request with its body retained, no replay consumed yet, a send boundary on
    the direct websocket transport, and either no anchor or a retry-safe fresh
    payload that drops it.
    """
    if has_other_pending_requests:
        return False
    if not _websocket_request_is_accepted_lifecycle_only(request_state):
        return False
    if not request_state.request_text or request_state.replay_count != 0:
        return False
    if request_state.transport == _REQUEST_TRANSPORT_WEBSOCKET and request_state.response_create_sent_at is None:
        return False
    if request_state.previous_response_id is not None and not (
        request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text
    ):
        return False
    return True


def _positive_token_count(value: JsonValue | None) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _terminal_payload_reports_output(payload: dict[str, JsonValue] | None) -> bool:
    """Return whether a terminal payload proves the model already ran.

    A non-empty ``output`` list or billed output/reasoning tokens (on the event
    or nested under ``response``) mean the turn produced something the client
    may be charged for; such a terminal is never replayed.
    """
    if not isinstance(payload, dict):
        return False
    response = payload.get("response")
    scopes: list[dict[str, JsonValue]] = [payload]
    if isinstance(response, dict):
        scopes.append(response)
    for scope in scopes:
        output = scope.get("output")
        if output is not None and (not isinstance(output, list) or output):
            return True
        usage = scope.get("usage")
        if not isinstance(usage, dict):
            continue
        if _positive_token_count(usage.get("output_tokens")):
            return True
        output_token_details = usage.get("output_tokens_details")
        if isinstance(output_token_details, dict) and _positive_token_count(
            output_token_details.get("reasoning_tokens")
        ):
            return True
    return False


def _websocket_accepted_capacity_retry_error_code(
    request_state: _WebSocketRequestState,
    *,
    event_type: str | None,
    error_code: str | None,
    error_message: str | None,
    payload_response_id: str | None,
    payload: dict[str, JsonValue] | None,
    has_other_pending_requests: bool,
) -> str | None:
    """Classify an output-free capacity terminal of an accepted request.

    Returns the replay error code, or ``None`` when the terminal must surface
    to the client unchanged. Only capacity codes and the selected-model
    capacity message qualify; quota and rate-limit codes are refused before
    the message-only fallback so they can never be reclassified as overload.
    """
    if event_type not in _TERMINAL_EVENT_TYPES:
        return None
    if not _websocket_accepted_replay_candidate(request_state, has_other_pending_requests=has_other_pending_requests):
        return None
    if payload_response_id is not None and payload_response_id != request_state.response_id:
        return None
    if _terminal_payload_reports_output(payload):
        return None
    if error_code in _ACCEPTED_REPLAY_FAIL_CLOSED_ERROR_CODES:
        return None
    if is_upstream_model_capacity_error(error_message):
        return error_code if error_code in _ACCEPTED_CAPACITY_REPLAY_ERROR_CODES else "server_is_overloaded"
    if error_code in _ACCEPTED_CAPACITY_REPLAY_ERROR_CODES:
        return error_code
    return None


async def _claim_websocket_replay_create_gate(
    request_state: _WebSocketRequestState,
    gate: asyncio.Semaphore,
) -> bool:
    """Re-claim the session response-create gate for a replay without waiting.

    ``awaiting_response_created=True`` must imply that the request holds the
    gate: the capacity wait sleeps the sole upstream reader and a younger
    ``response.create`` admitted meanwhile would be matched to this request's
    identity. An accepted request released the gate at ``response.created``,
    so take it back only if nobody else holds it; a contended gate is never
    awaited from the reader.

    ``response.created`` released the gate together with the shared work
    admission, so taking it back for an accepted request also marks the
    admission for re-acquisition: the replay's ``response.create`` must
    re-enter the configured work limit before it is sent, on the
    transport-close path exactly as on the terminal-error path.
    """
    if request_state.response_create_gate_acquired:
        return True
    if gate.locked():
        return False
    accepted = request_state.response_id is not None and not request_state.awaiting_response_created
    await gate.acquire()
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    if accepted and request_state.response_create_admission is None:
        request_state.response_create_admission_reacquire_required = True
    return True


async def _stage_websocket_request_state_for_replay(
    request_state: _WebSocketRequestState,
    *,
    create_gate: asyncio.Semaphore | None,
    surface: Literal["http_bridge", "websocket"],
    trigger: str,
) -> bool:
    """Reset a request to the pre-created shape before its replay is prepared.

    For an accepted request this captures the client-visible response id and
    arms the prelude suppression *before* ``response_id`` is cleared (clearing
    first is what leaks a second ``response.created``), marks the shared work
    admission for re-acquisition, and re-claims the session create gate when
    one is supplied. Returns ``False`` without touching the state when the gate
    is held by another request. Pre-created requests pass through unchanged.

    ``terminal_settlement_phase`` is deliberately left alone. The bridge
    terminal bookkeeping that stages a replay recorded a ``"claimed"`` marker
    when it popped the request, and that marker is what lets the shielded
    abort settlement (issue #1594) release the API-key reservation when the
    replay fails and the continuation is cancelled or raises before it
    finalizes; a replay that succeeds keeps the request in pending ownership,
    where the abort helper skips it, and the bookkeeping clears the marker on
    its normal exit.
    """
    accepted = request_state.response_id is not None and not request_state.awaiting_response_created
    if accepted:
        if create_gate is not None and not await _claim_websocket_replay_create_gate(request_state, create_gate):
            return False
        request_state.replay_downstream_response_id = (
            request_state.replay_downstream_response_id or request_state.response_id
        )
        request_state.suppress_next_created_downstream = True
        request_state.suppress_next_in_progress_downstream = request_state.response_event_count >= 2
        request_state.response_create_admission_reacquire_required = True
        logger.info(
            "Accepted output-free replay staged request_id=%s surface=%s trigger=%s visible_response_id=%s events=%d",
            request_state.request_log_id or request_state.request_id,
            surface,
            trigger,
            request_state.replay_downstream_response_id,
            request_state.response_event_count,
        )
    request_state.awaiting_response_created = True
    request_state.response_id = None
    request_state.response_event_count = 0
    return True
