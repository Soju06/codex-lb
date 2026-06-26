from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio

from app.core.balancer.types import UpstreamError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.openai.models import OpenAIEvent
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.db.models import Account
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.proxy.affinity import _AffinityPolicy
from app.modules.proxy.load_balancer import AccountLease, AccountSelection
from app.modules.proxy.work_admission import AdmissionLease

logger = logging.getLogger(__name__)

_REQUEST_TRANSPORT_HTTP = "http"
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS = 20
_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS = 0.05
_HARD_HTTP_BRIDGE_AFFINITY_KINDS = frozenset({"turn_state_header", "session_header"})
_ACCOUNT_SELECTION_RECOVERY_MIN_SLEEP_SECONDS = 1.0
_ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS = 30.0
_ACCOUNT_SELECTION_RECOVERY_MAX_SLEEP_SECONDS = 300.0
_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS = 10.0
_ACCOUNT_SELECTION_RETRY_HINT_RE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _account_selection_recovery_sleep_seconds_from_message(message: str | None) -> float | None:
    message = (message or "").strip()
    if not message:
        return None

    lowered = message.lower()
    if (
        "require re-authentication" in lowered
        or "all accounts are paused" in lowered
        or "no accounts with a plan" in lowered
        or "no accounts with available additional quota" in lowered
        or "no fresh additional quota data" in lowered
    ):
        return None

    retry_hint = _ACCOUNT_SELECTION_RETRY_HINT_RE.search(message)
    if retry_hint is not None:
        try:
            hinted_seconds = float(retry_hint.group(1))
        except ValueError:
            hinted_seconds = _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS
        return min(
            max(hinted_seconds, _ACCOUNT_SELECTION_RECOVERY_MIN_SLEEP_SECONDS),
            _ACCOUNT_SELECTION_RECOVERY_MAX_SLEEP_SECONDS,
        )

    if "hit your spend cap set by the owner of your workspace" in lowered:
        return _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS

    return None


def _account_selection_recovery_sleep_seconds(selection: AccountSelection) -> float | None:
    return _account_selection_recovery_sleep_seconds_from_message(selection.error_message)


def _account_capacity_wait_payload(
    request_state: "_WebSocketRequestState | None",
    *,
    request_id: str | None,
    reason: str | None,
    retry_after_seconds: float | None,
    started_at: float | None = None,
) -> dict[str, JsonValue]:
    wait_started_at = request_state.account_capacity_wait_started_at if request_state is not None else started_at
    waited_seconds = int(max(0.0, time.monotonic() - wait_started_at)) if wait_started_at is not None else 0
    payload: dict[str, JsonValue] = {
        "type": "codex.keepalive",
        "status": "waiting_for_account_capacity",
        "request_id": request_id or (request_state.request_id if request_state is not None else None),
        "waited_seconds": waited_seconds,
    }
    if reason:
        payload["reason"] = reason
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = int(max(0.0, retry_after_seconds))
    return payload


async def _sleep_for_account_selection_recovery(
    selection: AccountSelection,
    *,
    request_id: str | None,
    kind: str,
    request_stage: str,
    model: str | None,
    max_sleep_seconds: float | None = None,
    request_state: "_WebSocketRequestState | None" = None,
    heartbeat: Callable[[float], Awaitable[None]] | None = None,
) -> bool:
    sleep_seconds = _account_selection_recovery_sleep_seconds(selection)
    if sleep_seconds is None:
        return False
    if max_sleep_seconds is not None:
        if max_sleep_seconds <= 0:
            return False
        sleep_seconds = min(sleep_seconds, max_sleep_seconds)

    if request_state is not None:
        request_state.account_capacity_waiting = True
        request_state.account_capacity_wait_reason = selection.error_message
        request_state.account_capacity_wait_started_at = (
            request_state.account_capacity_wait_started_at or time.monotonic()
        )
        request_state.account_capacity_wait_retry_after_seconds = sleep_seconds

    logger.info(
        "Waiting for an account to recover before retrying selection request_id=%s kind=%s "
        "request_stage=%s model=%s sleep_seconds=%.1f error=%s",
        request_id,
        kind,
        request_stage,
        model,
        sleep_seconds,
        selection.error_message,
    )
    remaining = sleep_seconds
    try:
        while remaining > 0:
            if heartbeat is not None:
                await heartbeat(remaining)
            chunk = min(remaining, _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS)
            await asyncio.sleep(chunk)
            remaining -= chunk
    finally:
        if request_state is not None:
            request_state.account_capacity_waiting = False
            request_state.account_capacity_wait_reason = None
            request_state.account_capacity_wait_retry_after_seconds = None
    return True


def _request_log_useragent_fields(headers: Mapping[str, str]) -> tuple[str | None, str | None]:
    raw_useragent = next((value for key, value in headers.items() if key.lower() == "user-agent"), None)
    if raw_useragent is None:
        return None, None
    useragent = raw_useragent.strip()
    if not useragent:
        return None, None
    first_token = useragent.split(maxsplit=1)[0]
    useragent_group = first_token.split("/", 1)[0].strip() or None
    return useragent, useragent_group


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError, *, exclude_account: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.error = error
        self.exclude_account = exclude_account


class _WebSocketConnectFailureEmitted(Exception):
    pass


class _TransientStreamError(Exception):
    """Transient upstream error (e.g. 500 server_error) - retry on same account first."""

    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


class _TerminalStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


@dataclass
class _ApiKeyReservationTouchState:
    last_touch_at: float


@dataclass
class _StreamSettlement:
    """Populated by _stream_once(), consumed by _stream_with_retry() for reservation settlement."""

    status: str = "success"
    model: str = ""
    service_tier: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    error: UpstreamError | None = None
    account_health_error: bool = False
    record_success: bool = True
    downstream_visible: bool = False
    downstream_text_visible: bool = False
    response_id: str | None = None


def _stream_settlement_error_payload(settlement: _StreamSettlement) -> UpstreamError:
    if settlement.error is not None:
        return settlement.error
    payload: UpstreamError = {}
    if settlement.error_message:
        payload["message"] = settlement.error_message
    else:
        payload["message"] = "Upstream error"
    return payload


def _consume_api_key_reservation_heartbeat_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("API key reservation heartbeat task failed during cancellation", exc_info=True)


@dataclass(frozen=True, slots=True)
class _FilePinEntry:
    account_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class _RequestLogFailureMetadata:
    failure_phase: str | None = None
    failure_detail: str | None = None
    failure_exception_type: str | None = None
    upstream_status_code: int | None = None
    upstream_error_code: str | None = None
    bridge_stage: str | None = None


@dataclass
class _WebSocketRequestState:
    request_id: str
    model: str | None
    service_tier: str | None
    reasoning_effort: str | None
    api_key_reservation: ApiKeyUsageReservationData | None
    started_at: float
    latency_first_token_ms: int | None = None
    request_log_id: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    response_id: str | None = None
    awaiting_response_created: bool = False
    event_queue: asyncio.Queue[str | None] | None = None
    transport: str = _REQUEST_TRANSPORT_WEBSOCKET
    upstream_transport: str | None = _REQUEST_TRANSPORT_WEBSOCKET
    api_key: ApiKeyData | None = None
    request_usage_budget: ApiKeyRequestUsageBudget | None = None
    request_text: str | None = None
    replay_count: int = 0
    auth_replay_count: int = 0
    auth_replay_counts_by_account: dict[str, int] = field(default_factory=dict)
    force_refresh_account_id: str | None = None
    excluded_account_ids: set[str] = field(default_factory=set)
    skip_request_log: bool = False
    previous_response_id: str | None = None
    session_id: str | None = None
    proxy_injected_previous_response_id: bool = False
    expose_stale_previous_response_classifier: bool = False
    fresh_upstream_request_text: str | None = None
    # True only when ``fresh_upstream_request_text`` contains a *safe* pre-
    # injection form of this request that can be replayed as a fresh turn.
    # Durable-anchor injection captures the original unanchored full-resend
    # payload, so dropping the anchor and replaying is equivalent to the
    # client's own retry. Session-level anchor injection does not set this:
    # the original payload may have omitted history the conversation depended
    # on, and dropping the anchor there would silently turn a continuation into
    # a context-free fresh turn.
    fresh_upstream_request_is_retry_safe: bool = False
    request_stage: str = "first_turn"
    preferred_account_id: str | None = None
    require_security_work_authorized: bool = False
    file_required_preferred_account: bool = False
    error_code_override: str | None = None
    error_message_override: str | None = None
    error_type_override: str | None = None
    error_param_override: str | None = None
    error_http_status_override: int | None = None
    response_event_count: int = 0
    previous_response_not_found_rewritten: bool = False
    response_create_gate_acquired: bool = False
    response_create_gate: asyncio.Semaphore | None = None
    response_create_admission: AdmissionLease | None = None
    account_response_create_lease: AccountLease | None = None
    account_response_create_release: Callable[[AccountLease | None], Coroutine[Any, Any, None]] | None = None
    websocket_stream_lease: AccountLease | None = None
    affinity_policy: _AffinityPolicy = field(default_factory=_AffinityPolicy)
    suppressed_downstream_tool_call: bool = False
    suppressed_duplicate_tool_call: bool = False
    pending_function_call_ids: list[str] = field(default_factory=list)
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None] = field(default_factory=dict)
    input_item_count: int = 0
    input_full_fingerprint: str | None = None
    api_key_reservation_last_touch_at: float = field(default_factory=time.monotonic)
    api_key_reservation_heartbeat_stop: asyncio.Event | None = None
    api_key_reservation_heartbeat_task: asyncio.Task[None] | None = None
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None
    useragent: str | None = None
    useragent_group: str | None = None
    downstream_visible: bool = False
    suppress_next_created_downstream: bool = False
    replay_downstream_response_id: str | None = None
    draining_until_terminal: bool = False
    account_capacity_waiting: bool = False
    account_capacity_wait_reason: str | None = None
    account_capacity_wait_started_at: float | None = None
    account_capacity_wait_retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _HTTPBridgeSessionKey:
    affinity_kind: str
    affinity_key: str
    api_key_id: str | None
    strength: Literal["hard", "soft"] | None = None

    def __post_init__(self) -> None:
        strength = self.strength
        if strength is None:
            strength = "hard" if self.affinity_kind in _HARD_HTTP_BRIDGE_AFFINITY_KINDS else "soft"
        object.__setattr__(self, "strength", strength)


@dataclass(frozen=True, slots=True)
class _HTTPBridgeOwnerForward:
    owner_instance: str
    owner_endpoint: str
    key: _HTTPBridgeSessionKey


@dataclass(slots=True)
class _HTTPBridgeSession:
    key: _HTTPBridgeSessionKey
    headers: dict[str, str]
    affinity: _AffinityPolicy
    request_model: str | None
    account: Account
    upstream: UpstreamResponsesWebSocket
    upstream_control: _WebSocketUpstreamControl
    pending_requests: deque[_WebSocketRequestState]
    pending_lock: anyio.Lock
    response_create_gate: asyncio.Semaphore
    queued_request_count: int
    last_used_at: float
    idle_ttl_seconds: float
    lifecycle_lock: anyio.Lock = field(default_factory=anyio.Lock)
    api_key: ApiKeyData | None = None
    codex_session: bool = False
    prewarmed: bool = False
    prewarm_lock: anyio.Lock | None = None
    upstream_turn_state: str | None = None
    downstream_turn_state: str | None = None
    downstream_turn_state_aliases: set[str] = field(default_factory=set)
    previous_response_ids: set[str] = field(default_factory=set)
    last_completed_input_count: int = 0
    last_completed_response_id: str | None = None
    last_completed_input_prefix_fingerprint: str | None = None
    durable_session_id: str | None = None
    durable_owner_epoch: int | None = None
    upstream_reader: asyncio.Task[None] | None = None
    last_upstream_close_code: int | None = None
    closed: bool = False
    account_lease: AccountLease | None = None
    upstream_close_attempted: bool = False
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None] = field(default_factory=dict)
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None


@dataclass(slots=True)
class _WebSocketContinuityState:
    last_completed_input_count: int = 0
    last_completed_response_id: str | None = None
    last_completed_input_prefix_fingerprint: str | None = None
    last_pending_function_call_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _WebSocketContinuityAnchor:
    previous_response_id: str
    stored_input_item_count: int


@dataclass(slots=True)
class _WebSocketUpstreamControl:
    reconnect_requested: bool = False
    retire_after_drain: bool = False
    suppress_downstream_event: bool = False
    replay_request_state: _WebSocketRequestState | None = None
    downstream_texts: list[str] | None = None
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None] = field(default_factory=dict)


@dataclass(slots=True)
class _DownstreamWebSocketActivity:
    last_activity_at: float = field(default_factory=time.monotonic)
    disconnected: bool = False

    def mark(self) -> None:
        self.last_activity_at = time.monotonic()

    def mark_disconnected(self) -> None:
        self.disconnected = True
        self.mark()


def _clear_websocket_request_error_overrides(request_state: _WebSocketRequestState) -> None:
    request_state.error_code_override = None
    request_state.error_message_override = None
    request_state.error_type_override = None
    request_state.error_param_override = None
    request_state.error_http_status_override = None


def _record_response_event(request_state: _WebSocketRequestState | None, event_type: str | None) -> None:
    if request_state is None or event_type is None or not event_type.startswith("response."):
        return
    if event_type in {"response.failed", "response.incomplete"}:
        return
    request_state.response_event_count += 1


def _websocket_request_can_replay_before_visible_output(request_state: _WebSocketRequestState) -> bool:
    if not request_state.request_text:
        return False
    if request_state.replay_count >= 1:
        return False
    if request_state.downstream_visible:
        return False
    has_retry_safe_fresh_payload = (
        request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text is not None
    )
    precreated_pending = request_state.response_id is None and request_state.awaiting_response_created
    if precreated_pending and request_state.previous_response_id is not None and not has_retry_safe_fresh_payload:
        return False
    created_only_pending = (
        request_state.response_id is not None
        and not request_state.awaiting_response_created
        and request_state.response_event_count <= 1
        and (request_state.previous_response_id is None or has_retry_safe_fresh_payload)
    )
    if precreated_pending and request_state.response_event_count > 0:
        return False
    return precreated_pending or created_only_pending


def _record_websocket_route_metadata(
    request_state: _WebSocketRequestState,
    *,
    upstream: UpstreamResponsesWebSocket | None = None,
    route: ResolvedUpstreamRoute | None = None,
    fallback_used: bool | None = None,
) -> None:
    request_state.upstream_proxy_route_mode = getattr(upstream, "upstream_proxy_route_mode", None) or (
        route.mode if route is not None else None
    )
    request_state.upstream_proxy_pool_id = getattr(upstream, "upstream_proxy_pool_id", None) or (
        route.pool_id if route is not None else None
    )
    request_state.upstream_proxy_endpoint_id = getattr(upstream, "upstream_proxy_endpoint_id", None) or (
        route.endpoint_id if route is not None else None
    )
    upstream_fallback = getattr(upstream, "upstream_proxy_fallback_used", None)
    request_state.upstream_proxy_fallback_used = upstream_fallback if upstream_fallback is not None else fallback_used
    if request_state.upstream_proxy_endpoint_id is None:
        request_state.upstream_proxy_fallback_used = None
    request_state.upstream_proxy_fail_closed_reason = None


def _copy_websocket_route_metadata_to_session(
    session: _HTTPBridgeSession,
    request_state: _WebSocketRequestState,
) -> None:
    session.upstream_proxy_route_mode = request_state.upstream_proxy_route_mode
    session.upstream_proxy_pool_id = request_state.upstream_proxy_pool_id
    session.upstream_proxy_endpoint_id = request_state.upstream_proxy_endpoint_id
    session.upstream_proxy_fallback_used = request_state.upstream_proxy_fallback_used
    session.upstream_proxy_fail_closed_reason = request_state.upstream_proxy_fail_closed_reason


def _copy_websocket_route_metadata_from_session(
    request_state: _WebSocketRequestState,
    session: _HTTPBridgeSession,
) -> None:
    request_state.upstream_proxy_route_mode = session.upstream_proxy_route_mode
    request_state.upstream_proxy_pool_id = session.upstream_proxy_pool_id
    request_state.upstream_proxy_endpoint_id = session.upstream_proxy_endpoint_id
    request_state.upstream_proxy_fallback_used = session.upstream_proxy_fallback_used
    request_state.upstream_proxy_fail_closed_reason = session.upstream_proxy_fail_closed_reason


def _websocket_route_log_kwargs(request_state: _WebSocketRequestState) -> dict[str, str | bool | None]:
    return {
        "upstream_proxy_route_mode": request_state.upstream_proxy_route_mode,
        "upstream_proxy_pool_id": request_state.upstream_proxy_pool_id,
        "upstream_proxy_endpoint_id": request_state.upstream_proxy_endpoint_id,
        "upstream_proxy_fallback_used": (
            request_state.upstream_proxy_fallback_used if request_state.upstream_proxy_endpoint_id else None
        ),
        "upstream_proxy_fail_closed_reason": request_state.upstream_proxy_fail_closed_reason,
    }


@dataclass(slots=True)
class _PreparedWebSocketRequest:
    text_data: str
    request_state: _WebSocketRequestState
    affinity_policy: _AffinityPolicy


@dataclass(frozen=True, slots=True)
class _WebSocketReceiveTimeout:
    timeout_seconds: float
    error_code: str
    error_message: str
    fail_all_pending: bool = False


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    if isinstance(payload.get("error"), dict):
        return "error"
    return None


async def _wait_for_websocket_continuity_gap(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        async with pending_lock:
            if not pending_requests:
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS, remaining))


async def _websocket_full_replay_should_wait_for_continuity(
    request_state: _WebSocketRequestState,
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
    codex_session_affinity: bool,
) -> bool:
    if (
        not codex_session_affinity
        or request_state.previous_response_id is not None
        or request_state.input_item_count < _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS
    ):
        return False
    async with pending_lock:
        return bool(pending_requests)
