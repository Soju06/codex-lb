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
once -- on another account when its body is account-neutral, otherwise to the
account that accepted it (a client-supplied anchor or an account-bound fresh
body keeps the anchored body on its owner) -- while the client keeps reading
the single lifecycle it already observed. Quota and rate-limit terminals after
acceptance deliberately stay fail-closed; see the openspec change
``retry-accepted-output-free-capacity-failures``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from app.core.types import JsonValue
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_WEBSOCKET,
    _affinity_may_resolve_hard_owner,
    _HTTPBridgeSession,
    _websocket_request_is_accepted_lifecycle_only,
    _WebSocketRequestState,
)
from app.modules.proxy.helpers import is_upstream_model_capacity_error

logger = logging.getLogger("app.modules.proxy.service")

_ACCEPTED_CAPACITY_REPLAY_ERROR_CODES = frozenset({"server_is_overloaded", "overloaded_error", "model_at_capacity"})
# The code handed back is what the account-health write consumes and what the
# spec requires to be a transparent replay code
# (``_WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES``). ``model_at_capacity`` is not
# one on either surface, so it is reported as ``server_is_overloaded`` exactly
# as the pre-created classifier reports the selected-model capacity message;
# the accepted replay itself no longer branches on the transparent set.
_ACCEPTED_CAPACITY_REPLAY_REPORTED_CODES = {
    "server_is_overloaded": "server_is_overloaded",
    "overloaded_error": "overloaded_error",
    "model_at_capacity": "server_is_overloaded",
}
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
    the direct websocket transport, no finite ``sequence_number`` forwarded
    downstream, and either no anchor or a retry-safe fresh payload. The fresh
    payload replaces the anchored body only when the owner-switch prep proves
    the move safe (proxy-injected anchor, account-neutral body); otherwise the
    anchored body is re-sent to the owner that accepted it, which the terminal
    proved produced nothing.

    The sequence guard is the direct websocket surface's existing contract
    (openspec: "Direct WebSocket replay never mixes numeric response
    sequences"): a fresh upstream generation restarts its counter, so once a
    sequenced prelude frame reached the client the terminal is finalized and
    surfaced unchanged instead of being replayed. The HTTP bridge never
    records a downstream watermark, so its behaviour is unaffected.
    """
    if has_other_pending_requests:
        return False
    if request_state.last_downstream_sequence_number is not None:
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


def _http_bridge_accepted_anchored_replay_candidate(request_state: _WebSocketRequestState) -> bool:
    """Return whether an accepted anchored bridge request may be staged for the pre-created retry.

    The bridge's transparent-code branch used to stage only unanchored
    requests, so an accepted follow-up whose anchor the proxy injected was
    replayed after the selected-model capacity *message* (the wait branch) but
    forwarded unchanged after a bare ``server_is_overloaded`` /
    ``overloaded_error`` code. ``_retry_http_bridge_precreated_request``
    re-sends a proxy-injected anchor through the owner-switch prep (fresh body
    on another account) or, when the fresh body is account-bound, the anchored
    body to its owner, so those requests qualify. A client-supplied anchor is
    replayed only by the transport-failure path (its proof-gated full resend
    is transport-only, see ``request_is_retryable``), so its capacity terminal
    keeps being forwarded rather than staged into a retry that would refuse
    it and rewrite the terminal.
    """
    return bool(
        request_state.response_id is not None
        and not request_state.awaiting_response_created
        and request_state.previous_response_id is not None
        and request_state.proxy_injected_previous_response_id
        and request_state.fresh_upstream_request_is_retry_safe
        and request_state.fresh_upstream_request_text
    )


def _http_bridge_accepted_capacity_retry_allowed(request_state: _WebSocketRequestState) -> bool:
    """Return whether the bridge capacity branches may reserve and stage this request.

    Pre-created requests keep their existing rules. An accepted request
    qualifies unanchored, or anchored only as
    ``_http_bridge_accepted_anchored_replay_candidate`` allows (proxy-injected
    anchor with a retry-safe fresh body). The selected-model capacity *message*
    used to reach the wait branch for every accepted request that retained a
    retry-safe fresh body, including one whose anchor the client supplied: the
    request was reserved, staged and penalized, then the pre-created retry
    refused its transport-only full resend and the raw capacity terminal was
    rewritten into a synthetic ``stream_incomplete`` (HTTP 502). Excluding the
    client-supplied anchor before the wait keeps that terminal forwarded
    unchanged, exactly as the bare transparent-code branch already does.
    """
    accepted = request_state.response_id is not None and not request_state.awaiting_response_created
    if not accepted or request_state.previous_response_id is None:
        return True
    return _http_bridge_accepted_anchored_replay_candidate(request_state)


def _http_bridge_accepted_replay_may_exclude_account(
    request_state: _WebSocketRequestState,
    session: _HTTPBridgeSession,
) -> bool:
    """Return whether the bridge's fresh-request replay may exclude the account that accepted it.

    ``_retry_http_bridge_precreated_request`` moves an account-neutral fresh
    request off the failing account by excluding it from the reconnect
    selection. The accepted-lifecycle replay of #2127 inherited that
    exclusion on hard session keys (``session_header`` / ``thread_header`` /
    ``turn_state_header``, every native Codex bridge session) -- yet the
    reconnect selects with the session's affinity, and when that affinity
    resolves a hard ``CODEX_SESSION`` owner (a turn-state row, or the raw
    compatibility row an old replica persisted for the bare session header)
    selection is narrowed to that owner. Excluding it makes every
    re-selection fail with ``hard_affinity_saturated``, which the reconnect
    loop treats as a transient owner outage and sleeps on until the bridge
    request budget (7200s by default) is spent, long after the client gave up.

    Mirror of ``_websocket_accepted_replay_may_exclude_account``: an accepted
    replay (``replay_downstream_response_id`` captured) on a hard session key
    whose affinity may resolve a hard owner reconnects without an exclusion,
    so the same owner is re-selected and the request is re-sent to it within
    the single lifecycle the client is reading. Everything else keeps the
    established exclusion: the created-only replay (unchanged from before
    accepted replays existed), soft session keys (never derived alongside a
    hard-capable affinity -- turn-state, thread and bare session headers all
    produce hard keys), and hard keys whose affinity cannot resolve an owner.
    """
    if request_state.replay_downstream_response_id is None:
        return True
    if session.key.strength != "hard":
        return True
    return not _affinity_may_resolve_hard_owner(session.affinity)


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
    The returned code is always a transparent replay code
    (``model_at_capacity`` is reported as ``server_is_overloaded``).
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
    if is_upstream_model_capacity_error(error_message) or error_code in _ACCEPTED_CAPACITY_REPLAY_ERROR_CODES:
        return _ACCEPTED_CAPACITY_REPLAY_REPORTED_CODES.get(error_code or "", "server_is_overloaded")
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

    ``terminal_settlement_phase`` is deliberately left alone: the settlement
    claim belongs to the bridge terminal bookkeeping, not to staging. The
    transparent-code branch recorded a ``"claimed"`` marker when it popped the
    request; the capacity-message wait branch keeps the request in pending
    ownership while it waits and records the marker itself when it gives that
    ownership up (``_relinquish_http_bridge_capacity_wait_ownership``). Either
    way the marker is what lets the shielded abort settlement (issue #1594)
    release the API-key reservation when the replay fails and the continuation
    is cancelled or raises before it finalizes; a replay that succeeds keeps
    the request in pending ownership, where the abort helper skips it, and the
    bookkeeping clears the marker on its normal exit.
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
