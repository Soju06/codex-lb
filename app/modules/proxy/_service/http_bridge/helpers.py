from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from typing import Any, Literal, Mapping, TypeVar, cast
from urllib.parse import urlparse

from app.core.balancer.rendezvous_hash import select_node
from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.proxy import CodexControlResponse as CodexControlResponse
from app.core.clients.proxy import (  # noqa: F401  # noqa: F401
    ImageFetchSession,
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    _as_image_fetch_session,
    _inline_content_images,
    _inline_input_image_urls,
    _ws_transport_payload_budget_bytes,
    filter_inbound_headers,
    pop_compact_timeout_overrides,
    pop_stream_timeout_overrides,
    pop_transcribe_timeout_overrides,
    push_compact_timeout_overrides,
    push_stream_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy import codex_control_request as core_codex_control_request  # noqa: F401
from app.core.clients.proxy import compact_responses as core_compact_responses  # noqa: F401
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio  # noqa: F401
from app.core.config.settings import Settings, get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import (
    OpenAIErrorDetail,
    OpenAIErrorEnvelope,
    openai_error,
    previous_response_stream_incomplete_error,
    response_failed_event,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_drain_recovery_allowed_total,
    bridge_first_turn_timeout_total,
    bridge_instance_mismatch_total,
    bridge_reattach_total,
    http_bridge_prewarm_total,
    http_bridge_stuck_retire_total,
)
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.overload import local_overload_error
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    AccountStatus,
    DashboardSettings,
    HttpBridgeSessionState,
    StickySessionKind,
)
from app.modules.api_keys.service import (
    ApiKeyData,
)
from app.modules.proxy._service.api_key_usage import (
    _API_KEY_RESERVATION_HEARTBEAT_SECONDS as _API_KEY_RESERVATION_HEARTBEAT_SECONDS,
)
from app.modules.proxy._service.compact import (
    _service_tier_from_compact_payload as _service_tier_from_compact_payload,
)
from app.modules.proxy._service.compact import (
    _sticky_key_for_compact_request as _sticky_key_for_compact_request,
)
from app.modules.proxy._service.compact import (
    _sticky_key_from_compact_payload as _sticky_key_from_compact_payload,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.observability import (
    _hash_identifier as _hash_identifier,
)
from app.modules.proxy._service.observability import (
    _hash_identifier_or_none as _hash_identifier_or_none,
)
from app.modules.proxy._service.observability import (
    _interesting_header_keys as _interesting_header_keys,
)
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_request_payload as _maybe_log_proxy_request_payload,
)
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_request_shape as _maybe_log_proxy_request_shape,
)
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_service_tier_trace as _maybe_log_proxy_service_tier_trace,
)
from app.modules.proxy._service.observability import (
    _record_continuity_fail_closed as _record_continuity_fail_closed,
)
from app.modules.proxy._service.observability import (
    _record_continuity_owner_resolution as _record_continuity_owner_resolution,
)
from app.modules.proxy._service.observability import (
    _summarize_input as _summarize_input,
)
from app.modules.proxy._service.observability import (
    _tools_hash as _tools_hash,
)
from app.modules.proxy._service.observability import (
    _truncate_identifier as _truncate_identifier,
)
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _http_bridge_session_supports_service_tier,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _WebSocketRequestState,
)
from app.modules.proxy._service.support import (
    _websocket_route_log_kwargs as _websocket_route_log_kwargs,
)
from app.modules.proxy._service.warmup import (
    WarmupExecutionData as WarmupExecutionData,
)
from app.modules.proxy._service.warmup import (
    WarmupFailedAccountData as WarmupFailedAccountData,
)
from app.modules.proxy._service.warmup import (
    WarmupSkippedAccountData as WarmupSkippedAccountData,
)
from app.modules.proxy._service.warmup import (
    WarmupSubmittedAccountData as WarmupSubmittedAccountData,
)
from app.modules.proxy._service.warmup import (
    _is_warmup_usage_eligible as _is_warmup_usage_eligible,
)
from app.modules.proxy._service.warmup import (
    _materialize_warmup_account as _materialize_warmup_account,
)
from app.modules.proxy._service.warmup import (
    _snapshot_warmup_account as _snapshot_warmup_account,
)
from app.modules.proxy._service.warmup import (
    _WarmupAccountSnapshot as _WarmupAccountSnapshot,
)
from app.modules.proxy._service.warmup import (
    _WarmupSubmitResult as _WarmupSubmitResult,
)
from app.modules.proxy._service.warmup import (
    _WarmupUsageSnapshot as _WarmupUsageSnapshot,
)
from app.modules.proxy.account_cache import is_account_routing_unavailable
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _extract_model_class,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
    _parse_openai_error,
)
from app.modules.proxy.ring_membership import (
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)

logger = logging.getLogger("app.modules.proxy.service")
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_EVENTLESS_RESPONSE_CREATED_MAX_SECONDS = 240.0
_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL = "missing_response_created_timeout"
T = TypeVar("T")

_HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR = "_codex_lb_started_at"
_HTTP_BRIDGE_STALE_INFLIGHT_MIN_SECONDS = 120.0
_HTTP_BRIDGE_STALE_INFLIGHT_TIMEOUT_MULTIPLIER = 6.0


@dataclass(frozen=True, slots=True)
class _HTTPBridgeRuntimeConfig:
    enabled: bool
    idle_ttl_seconds: float
    codex_idle_ttl_seconds: float
    max_sessions: int
    queue_limit: int
    prompt_cache_idle_ttl_seconds: float
    gateway_safe_mode: bool


def _service_module() -> Any:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        raise RuntimeError("app.modules.proxy.service is not loaded")
    return service_module


def _service_global(name: str) -> Any:
    return getattr(_service_module(), name)


def _service_global_or(name: str, fallback: T) -> T:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        return fallback
    return cast(T, getattr(service_module, name, fallback))


def _service_get_settings() -> Any:
    return _service_global_or("get_settings", get_settings)()


def _service_get_settings_cache() -> Any:
    return _service_global_or("get_settings_cache", get_settings_cache)()


def _service_time() -> Any:
    return _service_global_or("time", time)


def _proxy_admission_wait_timeout_seconds(settings: Any | None = None) -> float:
    return cast(Callable[[Any | None], float], _service_global("_proxy_admission_wait_timeout_seconds"))(settings)


def _http_bridge_stale_inflight_seconds() -> float:
    try:
        admission_timeout = _proxy_admission_wait_timeout_seconds()
    except Exception:
        admission_timeout = 10.0
    return max(
        _HTTP_BRIDGE_STALE_INFLIGHT_MIN_SECONDS,
        admission_timeout * _HTTP_BRIDGE_STALE_INFLIGHT_TIMEOUT_MULTIPLIER,
    )


def _normalize_responses_request_payload_for_bridge(payload: ResponsesRequest) -> ResponsesRequest:
    return cast(
        Callable[[ResponsesRequest], ResponsesRequest],
        _service_global("_normalize_responses_request_payload_for_bridge"),
    )(payload)


def _websocket_top_level_error_payload(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_top_level_error_payload")(*args, **kwargs)


def _header_value_case_insensitive(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_header_value_case_insensitive")(*args, **kwargs)


def _is_previous_response_not_found_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_is_previous_response_not_found_error")(*args, **kwargs)


def _http_bridge_startup_wait_timeout_error(
    stage: str,
    *,
    code: str = "global_admission_timeout",
) -> ProxyResponseError:
    message = f"codex-lb is temporarily overloaded during {stage}"
    return ProxyResponseError(429, local_overload_error(message, code=code))


def _http_bridge_pending_count_nowait(
    session: "_HTTPBridgeSession",
    *,
    context: str,
) -> int | None:
    try:
        session.pending_lock.acquire_nowait()
    except Exception as exc:
        if type(exc).__name__ not in {"WouldBlock", "RuntimeError"}:
            raise
        logger.warning(
            "http_bridge_pending_count_unavailable context=%s bridge_kind=%s bridge_key=%s account_id=%s model=%s",
            context,
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            session.account.id,
            session.request_model,
        )
        return None
    try:
        request_counts_against_queue = _service_global("_http_bridge_request_counts_against_queue")
        visible_pending_count = sum(
            1 for request_state in session.pending_requests if request_counts_against_queue(request_state)
        )
        return max(visible_pending_count, session.queued_request_count)
    finally:
        session.pending_lock.release()


def _cleanup_http_bridge_inflight_sessions_nowait(service: Any) -> dict[str, int]:
    now = _service_time().monotonic()
    stale_after_seconds = _http_bridge_stale_inflight_seconds()
    cleaned = 0
    stale = 0
    oldest_age_seconds = 0
    try:
        service._http_bridge_lock.acquire_nowait()
    except Exception as exc:
        if type(exc).__name__ not in {"WouldBlock", "RuntimeError"}:
            raise
        for future in service._http_bridge_inflight_sessions.values():
            started_at = getattr(future, _HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR, None)
            age_seconds = max(0.0, now - started_at) if isinstance(started_at, (int, float)) else 0.0
            oldest_age_seconds = max(oldest_age_seconds, int(age_seconds))
            if isinstance(started_at, (int, float)) and age_seconds >= stale_after_seconds:
                stale += 1
        return {
            "cleaned": 0,
            "stale": stale,
            "oldest_age_seconds": oldest_age_seconds,
        }
    try:
        for key, future in list(service._http_bridge_inflight_sessions.items()):
            current_future = service._http_bridge_inflight_sessions.get(key)
            if current_future is not future:
                continue
            started_at = getattr(future, _HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR, None)
            age_seconds = max(0.0, now - started_at) if isinstance(started_at, (int, float)) else 0.0
            oldest_age_seconds = max(oldest_age_seconds, int(age_seconds))
            cleanup_reason: str | None = None
            is_stale = isinstance(started_at, (int, float)) and age_seconds >= stale_after_seconds
            if is_stale:
                stale += 1
            if future.done():
                cleanup_reason = "done"
            if cleanup_reason is None:
                continue
            service._http_bridge_inflight_sessions.pop(key, None)
            cleaned += 1
            if future.done() and not future.cancelled():
                try:
                    future.exception()
                except Exception:
                    pass
            logger.warning(
                "http_bridge_inflight_session_create_cleanup reason=%s bridge_kind=%s bridge_key=%s"
                " age_seconds=%d stale_after_seconds=%d done=%s cancelled=%s",
                cleanup_reason,
                key.affinity_kind,
                _hash_identifier(key.affinity_key),
                int(age_seconds),
                int(stale_after_seconds),
                future.done(),
                future.cancelled(),
            )
    finally:
        service._http_bridge_lock.release()
    return {
        "cleaned": cleaned,
        "stale": stale,
        "oldest_age_seconds": oldest_age_seconds,
    }


def http_bridge_activity_snapshot_nowait(service: Any) -> dict[str, int | bool]:
    inflight_cleanup = _cleanup_http_bridge_inflight_sessions_nowait(service)
    live_sessions = 0
    pending_or_queued_requests = 0
    pending_unknown_sessions = 0

    for session in list(service._http_bridge_sessions.values()):
        if session.closed and not _http_bridge_session_has_admission_waiter(session):
            continue
        if not session.closed:
            live_sessions += 1
        pending_count = _http_bridge_pending_count_nowait(session, context="drain_status")
        if pending_count is None:
            pending_unknown_sessions += 1
        else:
            pending_or_queued_requests += max(0, pending_count)

    inflight_session_creates = len(service._http_bridge_inflight_sessions)
    active_cleanup_tasks = sum(
        1
        for task in service._background_cleanup_tasks
        if not task.done()
        and (
            task.get_name().startswith("proxy-http_bridge_session_close-")
            or task.get_name().startswith("http-bridge-close-")
        )
    )
    bridge_active = (
        live_sessions > 0
        or pending_or_queued_requests > 0
        or pending_unknown_sessions > 0
        or inflight_session_creates > 0
    )
    restart_blocking = pending_or_queued_requests > 0 or pending_unknown_sessions > 0 or inflight_session_creates > 0
    return {
        "http_bridge_live_sessions": live_sessions,
        "http_bridge_pending_or_queued_requests": pending_or_queued_requests,
        "http_bridge_pending_unknown_sessions": pending_unknown_sessions,
        "http_bridge_inflight_session_creates": inflight_session_creates,
        "http_bridge_inflight_session_create_oldest_age_seconds": inflight_cleanup["oldest_age_seconds"],
        "http_bridge_stale_inflight_session_creates": inflight_cleanup["stale"],
        "http_bridge_cleaned_inflight_session_creates": inflight_cleanup["cleaned"],
        "http_bridge_background_cleanup_tasks": active_cleanup_tasks,
        "http_bridge_active": bridge_active,
        "http_bridge_restart_blocking": restart_blocking,
    }


def _log_http_bridge_startup_wait_timeout(
    *,
    stage: str,
    timeout_seconds: float,
    key: "_HTTPBridgeSessionKey | None" = None,
    request_id: str | None = None,
    request_model: str | None = None,
    pending_count: int | None = None,
    inflight_count: int | None = None,
    queued_count: int | None = None,
    available: int | None = None,
    pending_request_ids: Sequence[str] | None = None,
    pending_request_ages_seconds: Sequence[float] | None = None,
) -> None:
    logger.warning(
        "http_bridge_startup_wait_timeout request_id=%s stage=%s wait_timeout_seconds=%.1f "
        "affinity_kind=%s bridge_key=%s model_class=%s pending_count=%s queued_count=%s "
        "inflight_count=%s available=%s pending_request_ids=%s pending_request_ages_seconds=%s",
        request_id or get_request_id() or "unknown",
        stage,
        timeout_seconds,
        key.affinity_kind if key is not None else None,
        _hash_identifier(key.affinity_key) if key is not None else None,
        _extract_model_class(request_model) if request_model else None,
        pending_count,
        queued_count,
        inflight_count,
        available,
        ",".join(pending_request_ids) if pending_request_ids else None,
        ",".join(f"{age:.1f}" for age in pending_request_ages_seconds) if pending_request_ages_seconds else None,
    )


def _http_bridge_precreated_retry_failure_error(exc: BaseException) -> tuple[int, str, str, str, str | None]:
    if isinstance(exc, ProxyResponseError):
        parsed = _parse_openai_error(exc.payload)
        code = _normalize_error_code(parsed.code if parsed else None, parsed.type if parsed else None)
        message = parsed.message if parsed and parsed.message else "HTTP bridge pre-created retry failed"
        error_type = parsed.type if parsed and parsed.type else "server_error"
        error_param = parsed.param if parsed else None
        return exc.status_code, code, message, error_type, error_param
    if isinstance(exc, TimeoutError):
        return (
            502,
            "upstream_unavailable",
            "HTTP bridge pre-created retry failed: upstream websocket reconnect timed out",
            "server_error",
            None,
        )
    message = str(exc).strip() or "HTTP bridge pre-created retry failed"
    return 502, "upstream_unavailable", message, "server_error", None


def _trim_http_bridge_previous_response_input_items(input_items: list[JsonValue]) -> list[JsonValue]:
    first_output_index = next(
        (
            index
            for index, item in enumerate(input_items)
            if _http_bridge_input_item_type(item)
            in {"function_call_output", "custom_tool_call_output", "apply_patch_call_output"}
        ),
        None,
    )
    if first_output_index is None or first_output_index == 0:
        return input_items
    prefix = input_items[:first_output_index]
    if not all(_is_http_bridge_previous_response_output_item(item) for item in prefix):
        return input_items
    return input_items[first_output_index:]


def _is_http_bridge_previous_response_output_item(item: JsonValue) -> bool:
    item_type = _http_bridge_input_item_type(item)
    if item_type in {"reasoning", "function_call", "custom_tool_call", "apply_patch_call"}:
        return _has_http_bridge_response_output_marker(item)
    if item_type != "message" or not isinstance(item, dict):
        return False
    role = item.get("role")
    return role == "assistant" and _has_http_bridge_response_output_marker(item)


def _has_http_bridge_response_output_marker(item: JsonValue) -> bool:
    if not isinstance(item, dict):
        return False
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id.strip():
        return True
    status = item.get("status")
    return status in {"completed", "in_progress"}


def _http_bridge_input_item_type(item: JsonValue) -> str | None:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    return item_type if isinstance(item_type, str) else None


def _normalize_http_bridge_error_event(
    *,
    event: OpenAIEvent | None,
    payload: dict[str, JsonValue] | None,
    request_state: _WebSocketRequestState | None,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str]:
    error_code_value: str | None = None
    error_type_value: str | None = None
    error_message_value: str | None = None
    error_param_value: str | None = None
    explicit_error_code = False
    rate_limit_metadata: OpenAIErrorDetail = {}

    if event is not None and event.error is not None:
        error_code_value = event.error.code
        error_type_value = event.error.type
        error_message_value = event.error.message
        error_param_value = event.error.param
        if isinstance(error_code_value, str) and error_code_value.strip():
            explicit_error_code = True
    elif isinstance(payload, dict):
        payload_error = payload.get("error")
        if not isinstance(payload_error, dict):
            payload_error = _websocket_top_level_error_payload(payload)
        if isinstance(payload_error, dict):
            code_value = payload_error.get("code")
            if isinstance(code_value, str):
                stripped = code_value.strip()
                if stripped:
                    error_code_value = stripped
                    explicit_error_code = True
            type_value = payload_error.get("type")
            if isinstance(type_value, str):
                stripped = type_value.strip()
                if stripped:
                    error_type_value = stripped
            message_value = payload_error.get("message")
            if isinstance(message_value, str):
                stripped = message_value.strip()
                if stripped:
                    error_message_value = stripped
            param_value = payload_error.get("param")
            if isinstance(param_value, str):
                stripped = param_value.strip()
                if stripped:
                    error_param_value = stripped

    if isinstance(payload, dict):
        raw_error = payload.get("error")
        if not isinstance(raw_error, dict):
            raw_error = _websocket_top_level_error_payload(payload)
        if isinstance(raw_error, dict):
            plan_type = raw_error.get("plan_type")
            if isinstance(plan_type, str):
                rate_limit_metadata["plan_type"] = plan_type
            resets_at = raw_error.get("resets_at")
            if isinstance(resets_at, int | float):
                rate_limit_metadata["resets_at"] = resets_at
            resets_in = raw_error.get("resets_in_seconds")
            if isinstance(resets_in, int | float):
                rate_limit_metadata["resets_in_seconds"] = resets_in

    if request_state is not None:
        if request_state.error_code_override is not None:
            error_code_value = request_state.error_code_override
            explicit_error_code = True
        if request_state.error_type_override is not None:
            error_type_value = request_state.error_type_override
        if request_state.error_message_override is not None:
            error_message_value = request_state.error_message_override
        if request_state.error_param_override is not None:
            error_param_value = request_state.error_param_override

    normalized_error_code = _normalize_error_code(error_code_value, error_type_value) or "upstream_error"
    if not explicit_error_code and normalized_error_code == "error":
        normalized_error_code = "upstream_error"
    normalized_error_type = error_type_value or "server_error"
    normalized_error_message = error_message_value or "Upstream error"

    normalized_response_id = None
    if request_state is not None:
        normalized_response_id = request_state.response_id or request_state.request_id

    normalized_event = response_failed_event(
        normalized_error_code,
        normalized_error_message,
        error_type=normalized_error_type,
        response_id=normalized_response_id,
        error_param=error_param_value,
    )
    if rate_limit_metadata:
        normalized_event["response"]["error"].update(rate_limit_metadata)
    normalized_event_block = format_sse_event(normalized_event)
    normalized_payload = parse_sse_data_json(normalized_event_block)
    parsed_event = parse_sse_event(normalized_event_block)
    return normalized_event_block, normalized_payload, parsed_event, "response.failed"


def _http_bridge_request_counts_against_queue(request_state: _WebSocketRequestState) -> bool:
    return not request_state.draining_until_terminal


def _http_bridge_eventless_precreated_deadline(
    request_state: _WebSocketRequestState,
    *,
    stuck_gate_retire_after_seconds: float,
) -> float | None:
    sent_at = request_state.response_create_sent_at
    if (
        request_state.transport != "http"
        or request_state.skip_request_log
        or not request_state.response_create_gate_acquired
        or request_state.response_create_gate is None
        or not request_state.awaiting_response_created
        or sent_at is None
        or request_state.response_id is not None
        or request_state.latency_response_created_ms is not None
        or request_state.response_event_count != 0
        or request_state.downstream_visible
        or request_state.last_downstream_sequence_number is not None
    ):
        return None
    return sent_at + min(
        float(stuck_gate_retire_after_seconds),
        _HTTP_BRIDGE_EVENTLESS_RESPONSE_CREATED_MAX_SECONDS,
    )


def _http_bridge_session_has_admission_waiter(session: object | None) -> bool:
    """Keep a closed bridge registered while an unsent request owns its handoff."""
    return session is not None and bool(getattr(session, "admission_waiter_count", 0))


def _http_bridge_session_has_visible_requests(session: "_HTTPBridgeSession") -> bool:
    return session.queued_request_count > 0 or any(
        _http_bridge_request_counts_against_queue(request_state) for request_state in session.pending_requests
    )


async def _close_http_bridge_session_bounded(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    reason: str,
) -> None:
    close_task = asyncio.create_task(
        service._close_http_bridge_session(session),
        name=f"http-bridge-close-{_hash_identifier(session.key.affinity_key)}",
    )

    def track_after_interruption(*, interruption: str) -> None:
        if close_task.done():
            return
        service._background_cleanup_tasks.add(close_task)

        def close_done(done_task: asyncio.Task[None]) -> None:
            service._background_cleanup_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                logger.warning(
                    "http_bridge_session_close_cancelled_after_%s reason=%s bridge_kind=%s "
                    "bridge_key=%s account_id=%s model=%s",
                    interruption,
                    reason,
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    session.account.id,
                    session.request_model,
                )
            except Exception:
                logger.warning(
                    "http_bridge_session_close_failed_after_%s reason=%s bridge_kind=%s "
                    "bridge_key=%s account_id=%s model=%s",
                    interruption,
                    reason,
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    session.account.id,
                    session.request_model,
                    exc_info=True,
                )

        close_task.add_done_callback(close_done)

    try:
        await asyncio.wait_for(
            asyncio.shield(close_task),
            timeout=_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        track_after_interruption(interruption="timeout")
        logger.warning(
            "http_bridge_session_close_timeout reason=%s bridge_kind=%s bridge_key=%s "
            "account_id=%s model=%s timeout_seconds=%.1f background_cleanup_tasks=%d",
            reason,
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            session.account.id,
            session.request_model,
            _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            len(service._background_cleanup_tasks),
        )
    except asyncio.CancelledError:
        track_after_interruption(interruption="cancellation")
        raise
    except Exception:
        logger.warning(
            "http_bridge_session_close_failed reason=%s bridge_kind=%s bridge_key=%s account_id=%s model=%s",
            reason,
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            session.account.id,
            session.request_model,
            exc_info=True,
        )


def _http_bridge_models_compatible(existing_model: str | None, request_model: str | None) -> bool:
    """Never reuse a bridge across two explicitly different model slugs."""

    if existing_model is None or request_model is None:
        return True
    return existing_model.strip().lower() == request_model.strip().lower()


def _http_bridge_compatible(
    session: _HTTPBridgeSession,
    request_model: str | None,
    request_service_tier: str | None,
    same_model_required: bool = False,
) -> bool:
    """Check catalog compatibility while preserving stricter legacy reuse paths."""

    model_compatible = not same_model_required or _http_bridge_models_compatible(
        session.request_model,
        request_model,
    )
    return model_compatible and _http_bridge_session_supports_service_tier(
        session,
        request_model=request_model,
        request_service_tier=request_service_tier,
    )


def _http_bridge_alias_target_is_stale(session: _HTTPBridgeSession | None) -> bool:
    return session is None or session.closed or not _http_bridge_session_account_active(session)


def _http_bridge_incompatible_model_fork_key(
    *,
    key: "_HTTPBridgeSessionKey",
    existing_model: str | None,
    request_model: str | None,
    request_scope_id: str,
) -> "_HTTPBridgeSessionKey | None":
    if key.affinity_kind not in {
        "session_header",
        "turn_state_header",
        "internal_unanchored_parallel",
        "internal_model_parallel",
    }:
        return None
    if _http_bridge_models_compatible(existing_model, request_model):
        return None
    if key.affinity_kind == "internal_model_parallel":
        return None
    fork_key = _HTTPBridgeSessionKey(
        "internal_model_parallel",
        sha256(
            f"{key.affinity_kind}\0{key.affinity_key}\0{request_model or ''}\0{request_scope_id}".encode()
        ).hexdigest(),
        key.api_key_id,
    )
    _log_http_bridge_event(
        "model_transition_fork",
        fork_key,
        account_id=None,
        model=request_model,
        detail=f"previous_model={existing_model}",
        cache_key_family=key.affinity_kind,
        model_class=_extract_model_class(request_model) if request_model else None,
        owner_check_applied=False,
    )
    return fork_key


def _http_bridge_locally_owned_fork_key(
    fork_key: "_HTTPBridgeSessionKey",
    forwarded_request: bool,
    forwarded_original_request_unanchored: bool,
) -> "_HTTPBridgeSessionKey | None":
    if not forwarded_request:
        return None
    if fork_key.affinity_kind == "internal_request_parallel":
        return fork_key
    if forwarded_original_request_unanchored and fork_key.affinity_kind == "internal_unanchored_parallel":
        return fork_key
    return None


def _http_bridge_parallel_fork_key(
    *,
    key: "_HTTPBridgeSessionKey",
    session: "_HTTPBridgeSession | None",
    inflight_creation: bool,
    incoming_turn_state: str | None,
    previous_response_id: str | None,
    request_model: str | None,
    request_service_tier: str | None,
    request_scope_id: str,
    allow_model_fork: bool = True,
    same_model_required: bool = False,
) -> "_HTTPBridgeSessionKey | None":
    """Give incompatible or concurrent requests an independent websocket lane."""

    reason: str | None = None
    if key.affinity_kind == "session_header" and incoming_turn_state is None and previous_response_id is None:
        if inflight_creation:
            reason = "session_creation_inflight"
        elif session is not None and not session.closed:
            if _http_bridge_session_has_visible_requests(session):
                reason = "active_request"
            elif (
                reservation_id := getattr(session, "unanchored_reservation_id", None)
            ) is not None and reservation_id != request_scope_id:
                reason = "session_reserved"
            elif not _http_bridge_models_compatible(session.request_model, request_model):
                reason = "model_change"
    if reason is not None:
        fork_key = _HTTPBridgeSessionKey(
            "internal_unanchored_parallel",
            sha256(f"{key.affinity_key}\0{request_scope_id}".encode()).hexdigest(),
            key.api_key_id,
        )
        _log_http_bridge_event(
            "unanchored_parallel_fork",
            fork_key,
            account_id=None,
            model=request_model,
            detail=f"reason={reason}",
            cache_key_family=key.affinity_kind,
            model_class=_extract_model_class(request_model) if request_model else None,
            owner_check_applied=False,
        )
        return fork_key

    if session is None or session.closed or not _http_bridge_session_account_active(session):
        return None
    if allow_model_fork:
        model_fork_key = _http_bridge_incompatible_model_fork_key(
            key=key,
            existing_model=session.request_model,
            request_model=request_model,
            request_scope_id=request_scope_id,
        )
        if model_fork_key is not None:
            return model_fork_key
    if _http_bridge_compatible(
        session,
        request_model,
        request_service_tier,
        same_model_required,
    ):
        return None
    if incoming_turn_state is not None or previous_response_id is not None:
        raise ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())

    fork_key = _HTTPBridgeSessionKey(
        "internal_request_parallel",
        sha256(f"{key.affinity_kind}\0{key.affinity_key}\0{request_scope_id}".encode()).hexdigest(),
        key.api_key_id,
    )
    _log_http_bridge_event(
        "request_compatibility_fork",
        fork_key,
        account_id=session.account.id,
        model=request_model,
        detail=f"source_kind={key.affinity_kind}",
        cache_key_family=key.affinity_kind,
        model_class=_extract_model_class(request_model) if request_model else None,
        owner_check_applied=False,
    )
    return fork_key


def _http_bridge_request_needs_unanchored_handoff(
    key: "_HTTPBridgeSessionKey",
    incoming_turn_state: str | None,
    previous_response_id: str | None,
    forwarded_request: bool,
    forwarded_original_request_unanchored: bool,
) -> bool:
    if forwarded_request:
        return forwarded_original_request_unanchored
    return key.affinity_kind == "session_header" and incoming_turn_state is None and previous_response_id is None


def _reserve_http_bridge_unanchored_handoff(
    session: "_HTTPBridgeSession",
    *,
    request_scope_id: str,
) -> None:
    current_reservation = getattr(session, "unanchored_reservation_id", None)
    if current_reservation is not None and current_reservation != request_scope_id:
        raise ProxyResponseError(
            409,
            openai_error(
                "bridge_session_reserved",
                "HTTP responses session bridge is reserved by another request",
                error_type="server_error",
            ),
        )
    session.unanchored_reservation_id = request_scope_id


def _release_http_bridge_unanchored_handoff(
    session: "_HTTPBridgeSession",
    *,
    request_scope_id: str,
) -> None:
    if session.unanchored_reservation_id == request_scope_id:
        session.unanchored_reservation_id = None


async def _refresh_reused_http_bridge_session_with_handoff(
    service: "_HTTPBridgeServiceProtocol",
    session: "_HTTPBridgeSession",
    *,
    key: "_HTTPBridgeSessionKey",
    request_scope_id: str,
    reserve_handoff: bool,
) -> None:
    if reserve_handoff:
        _reserve_http_bridge_unanchored_handoff(session, request_scope_id=request_scope_id)
    try:
        await service._refresh_durable_http_bridge_session(session)
        _log_http_bridge_event(
            "reuse",
            key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=service._http_bridge_pending_count_nowait(session, context="reuse_log"),
            cache_key_family=key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
    except BaseException:
        if reserve_handoff:
            _release_http_bridge_unanchored_handoff(session, request_scope_id=request_scope_id)
        raise


def _http_bridge_session_retiring_with_visible_requests(session: "_HTTPBridgeSession") -> bool:
    return session.upstream_control.retire_after_drain and _http_bridge_session_has_visible_requests(session)


def _http_bridge_payload_looks_like_full_resend(payload: ResponsesRequest) -> bool:
    input_value = payload.input
    if isinstance(input_value, str):
        return len(input_value) >= 4096
    if isinstance(input_value, Sequence) and not isinstance(input_value, (str, bytes, bytearray)):
        if len(input_value) > 1:
            return True
        if len(input_value) == 1:
            try:
                return len(json.dumps(input_value[0], ensure_ascii=True, separators=(",", ":"))) >= 4096
            except TypeError:
                return False
    return False


def _preferred_http_bridge_reconnect_turn_state(session: "_HTTPBridgeSession") -> str | None:
    if (
        session.codex_session
        and session.downstream_turn_state is not None
        and session.affinity.kind == StickySessionKind.CODEX_SESSION
        and session.affinity.key == session.downstream_turn_state
    ):
        return session.downstream_turn_state
    return session.upstream_turn_state


def _http_bridge_turn_state_alias_key(turn_state: str, api_key_id: str | None) -> tuple[str, str | None]:
    return (turn_state, api_key_id)


def _http_bridge_turn_state_alias_has_live_owner(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
    turn_state: str,
) -> bool:
    alias_key = _http_bridge_turn_state_alias_key(turn_state, session.key.api_key_id)
    existing_key = service._http_bridge_turn_state_index.get(alias_key)
    if existing_key is None or existing_key == session.key:
        return False
    return not _http_bridge_alias_target_is_stale(service._http_bridge_sessions.get(existing_key))


def _register_http_bridge_turn_state_aliases_locked(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
) -> None:
    for alias in session.downstream_turn_state_aliases:
        service._http_bridge_turn_state_index[_http_bridge_turn_state_alias_key(alias, session.key.api_key_id)] = (
            session.key
        )


def _http_bridge_previous_response_alias_key(response_id: str, api_key_id: str | None) -> tuple[str, str | None]:
    return (response_id.strip(), api_key_id)


def _http_bridge_session_allows_api_key(session: "_HTTPBridgeSession", api_key: ApiKeyData | None) -> bool:
    if api_key is None or not api_key.account_assignment_scope_enabled:
        return True
    return session.account.id in api_key.assigned_account_ids


def _http_bridge_session_account_active(session: "_HTTPBridgeSession") -> bool:
    # Hot-path invariant: this reuse check MUST stay a pure in-memory lookup (no
    # per-request database reads). Cross-replica freshness comes from the
    # `account_routing` cache-invalidation namespace refreshing the routing
    # availability snapshot behind is_account_routing_unavailable().
    return session.account.status == AccountStatus.ACTIVE and not is_account_routing_unavailable(session.account.id)


def _http_bridge_session_reusable_for_request(
    *,
    session: "_HTTPBridgeSession",
    key: "_HTTPBridgeSessionKey",
    incoming_turn_state: str | None,
    previous_response_id: str | None,
) -> bool:
    if session.upstream_control.retire_after_drain:
        return False
    if key.affinity_kind != "prompt_cache":
        return True
    if incoming_turn_state is not None:
        return True
    if previous_response_id is not None:
        return True
    return not session.codex_session


def _http_bridge_session_matches_preferred_account(
    *,
    session: "_HTTPBridgeSession",
    previous_response_id: str | None,
    preferred_account_id: str | None,
    require_preferred_account: bool = False,
) -> bool:
    if preferred_account_id is None:
        return True
    if previous_response_id is None and not require_preferred_account:
        return True
    return session.account.id == preferred_account_id


def _http_bridge_session_reusable_for_lookup(
    *,
    session: "_HTTPBridgeSession",
    key: "_HTTPBridgeSessionKey",
    api_key: ApiKeyData | None,
    incoming_turn_state: str | None,
    previous_response_id: str | None,
    preferred_account_id: str | None,
    require_preferred_account: bool,
    service_tier_supported: bool,
    allow_closed_admission_handoff: bool,
) -> bool:
    live_or_retained = _http_bridge_session_account_active(session) and (
        not session.closed or (allow_closed_admission_handoff and _http_bridge_session_has_admission_waiter(session))
    )
    return (
        live_or_retained
        and _http_bridge_session_allows_api_key(session, api_key)
        and _http_bridge_session_reusable_for_request(
            session=session,
            key=key,
            incoming_turn_state=incoming_turn_state,
            previous_response_id=previous_response_id,
        )
        and _http_bridge_session_matches_preferred_account(
            session=session,
            previous_response_id=previous_response_id,
            preferred_account_id=preferred_account_id,
            require_preferred_account=require_preferred_account,
        )
        and service_tier_supported
    )


def _require_http_bridge_bound_account_not_excluded(
    hard_account_bound: bool,
    account_id: str,
    excluded_account_ids: set[str],
) -> None:
    if hard_account_bound and account_id in excluded_account_ids:
        raise ProxyResponseError(
            502,
            openai_error(
                "upstream_unavailable",
                "HTTP responses session bridge continuity account is excluded",
            ),
        )


def _raise_http_bridge_incompatible_admission_handoff() -> None:
    raise ProxyResponseError(
        503,
        openai_error(
            "upstream_unavailable",
            "HTTP responses session bridge is preserving an incompatible admission handoff",
        ),
    )


def _make_http_bridge_session_key(
    payload: ResponsesRequest,
    *,
    headers: Mapping[str, str],
    affinity: _AffinityPolicy,
    api_key: ApiKeyData | None,
    request_id: str,
    explicit_prompt_cache_key: str | None = None,
    allow_forwarded_affinity_headers: bool = False,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
) -> _HTTPBridgeSessionKey:
    forwarded_key = (
        _forwarded_http_bridge_session_key(
            headers,
            api_key,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )
        if allow_forwarded_affinity_headers
        else None
    )
    if forwarded_key is not None:
        return forwarded_key
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key is not None:
        affinity_key = turn_state_key
        affinity_kind = "turn_state_header"
        strength: Literal["hard", "soft"] = "hard"
    else:
        session_key = _sticky_key_from_session_header(headers)
        if session_key is not None:
            # One Codex process session can host several independent agent
            # threads. Codex keeps the process-level session header shared but
            # gives every thread a stable explicit prompt_cache_key. Keying
            # only by the header makes a later, non-overlapping child reuse the
            # parent's upstream conversation and receive the wrong history.
            session_header_key = _make_http_bridge_session_header_fallback_key(
                headers=headers,
                api_key=api_key,
                explicit_prompt_cache_key=explicit_prompt_cache_key,
            )
            assert session_header_key is not None
            affinity_key = session_header_key.affinity_key
            affinity_kind = "session_header"
            strength = "hard"
        else:
            affinity_key = affinity.key or request_id
            affinity_kind = affinity.kind.value if affinity.kind is not None else "request"
            strength = "soft"
    return _HTTPBridgeSessionKey(
        affinity_kind=affinity_kind,
        affinity_key=affinity_key,
        api_key_id=api_key.id if api_key is not None else None,
        strength=strength,
    )


def _make_http_bridge_session_header_fallback_key(
    *,
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    explicit_prompt_cache_key: str | None,
) -> _HTTPBridgeSessionKey | None:
    session_key = _sticky_key_from_session_header(headers)
    if session_key is None:
        return None
    affinity_key = (
        sha256(f"{session_key}\0{explicit_prompt_cache_key.strip()}".encode()).hexdigest()
        if isinstance(explicit_prompt_cache_key, str) and explicit_prompt_cache_key.strip()
        else session_key
    )
    return _HTTPBridgeSessionKey(
        "session_header",
        affinity_key,
        api_key.id if api_key is not None else None,
    )


async def _http_bridge_should_wait_for_registration(
    self,
    key: _HTTPBridgeSessionKey,
    settings: Settings,
) -> bool:
    import app.core.startup as startup_module

    if startup_module._bridge_registration_complete:
        return False
    if key.strength != "hard":
        return False
    if _http_bridge_requires_cluster_registration(settings):
        return True
    if self._ring_membership is None:
        return False
    try:
        active_members = await self._ring_membership.list_active()
    except Exception:
        logger.debug("Skipping bridge registration gate because active ring lookup failed", exc_info=True)
        return False
    current_instance = settings.http_responses_session_bridge_instance_id
    return any(member != current_instance for member in active_members)


def _durable_bridge_lookup_active_owner(lookup: DurableBridgeLookup | None) -> str | None:
    if lookup is None:
        return None
    if lookup.state == "closed":
        return None
    if lookup.owner_instance_id is None or lookup.lease_expires_at is None:
        return None
    lease_expires_at = to_utc_naive(lookup.lease_expires_at)
    if lease_expires_at <= utcnow():
        return None
    return lookup.owner_instance_id


def _durable_bridge_lookup_allows_local_reuse(
    lookup: DurableBridgeLookup | None,
    *,
    current_instance: str,
) -> bool:
    if lookup is None:
        return True
    owner_instance = _durable_bridge_lookup_active_owner(lookup)
    if owner_instance is None:
        return True
    return owner_instance == current_instance


def _http_bridge_allow_durable_takeover(lookup: DurableBridgeLookup | None) -> bool:
    owner_instance = _durable_bridge_lookup_active_owner(lookup)
    if owner_instance is None:
        return True
    if lookup is None:
        return False
    return lookup.state in {
        HttpBridgeSessionState.DRAINING,
        HttpBridgeSessionState.CLOSED,
    }


def _http_bridge_has_durable_recovery_anchor(
    *,
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    if previous_response_id is not None:
        return True
    if durable_lookup is None or durable_lookup.latest_response_id is None:
        return False
    return durable_lookup.canonical_kind in _HARD_HTTP_BRIDGE_AFFINITY_KINDS


def _http_bridge_can_local_recover_without_ring(
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    if _http_bridge_has_durable_recovery_anchor(
        previous_response_id=previous_response_id,
        durable_lookup=durable_lookup,
    ):
        return True
    return (
        key.affinity_kind == "session_header"
        and previous_response_id is None
        and _sticky_key_from_turn_state_header(headers) is None
    )


def _http_bridge_can_single_instance_owner_takeover_without_anchor(
    *,
    key: _HTTPBridgeSessionKey,
    owner_instance: str | None,
    current_instance: str,
    ring: tuple[str, ...],
) -> bool:
    if key.strength != "hard":
        return False
    if owner_instance is None or owner_instance == current_instance:
        return False
    if len(ring) != 1:
        return False
    if ring[0] != current_instance:
        return False
    return owner_instance not in ring


def _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
    *,
    key: _HTTPBridgeSessionKey,
    owner_instance: str | None,
    current_instance: str,
    ring: tuple[str, ...],
) -> bool:
    if key.affinity_kind != "prompt_cache":
        return False
    if owner_instance is None or owner_instance == current_instance:
        return False
    if len(ring) != 1:
        return False
    if ring[0] != current_instance:
        return False
    return owner_instance not in ring


def _http_bridge_endpoint_matches_current_instance(owner_endpoint: str, settings: Settings) -> bool:
    current_endpoint = settings.http_responses_session_bridge_advertise_base_url
    if current_endpoint is None:
        return False
    return owner_endpoint.strip().rstrip("/") == current_endpoint.strip().rstrip("/")


def _http_bridge_can_recover_during_drain(
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
    durable_lookup: DurableBridgeLookup | None,
) -> bool:
    return _http_bridge_has_durable_recovery_anchor(
        previous_response_id=previous_response_id,
        durable_lookup=durable_lookup,
    )


def _http_bridge_request_stage(
    *,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    durable_lookup: DurableBridgeLookup | None,
) -> str:
    del durable_lookup
    if (
        payload.previous_response_id is not None
        or _sticky_key_from_turn_state_header(headers) is not None
        or _sticky_key_from_session_header(headers) is not None
    ):
        return "follow_up"
    return "first_turn"


def _record_bridge_reattach(*, path: str, outcome: str) -> None:
    if PROMETHEUS_AVAILABLE and bridge_reattach_total is not None:
        bridge_reattach_total.labels(path=path, outcome=outcome).inc()


def _record_bridge_first_turn_timeout() -> None:
    if PROMETHEUS_AVAILABLE and bridge_first_turn_timeout_total is not None:
        bridge_first_turn_timeout_total.inc()


def _record_bridge_drain_recovery_allowed() -> None:
    if PROMETHEUS_AVAILABLE and bridge_drain_recovery_allowed_total is not None:
        bridge_drain_recovery_allowed_total.inc()


def _is_missing_durable_bridge_table_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "http_bridge_sessions" not in message and "http_bridge_session_aliases" not in message:
        return False
    return "no such table" in message or "does not exist" in message or "undefinedtable" in message


def _http_bridge_durable_lease_ttl_seconds() -> float:
    return float(RING_STALE_THRESHOLD_SECONDS)


async def _release_http_bridge_unanchored_handoffs_for_request(
    service: _HTTPBridgeServiceProtocol,
    *,
    request_scope_id: str,
) -> None:
    """Fail-safe cleanup for reservations published before request submission."""

    async with service._http_bridge_lock:
        for session in service._http_bridge_sessions.values():
            _release_http_bridge_unanchored_handoff(session, request_scope_id=request_scope_id)


def _track_alias_registration(session: _HTTPBridgeSession, alias: str, *, turn_state: bool) -> int:
    session.alias_registration_generation += 1
    generation = session.alias_registration_generation
    registrations = (
        session.turn_state_alias_registration_generations
        if turn_state
        else session.previous_response_alias_registration_generations
    )
    registrations[alias] = generation
    return generation


async def _persist_http_bridge_turn_state_alias(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
    *,
    turn_state: str,
    registration_generation: int,
    instance_id: str,
    lease_ttl_seconds: float,
) -> None:
    owner_epoch = session.durable_owner_epoch
    try:
        registered = await service._durable_bridge.register_turn_state(
            session_id=session.durable_session_id,
            api_key_id=session.key.api_key_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            turn_state=turn_state,
            lease_ttl_seconds=lease_ttl_seconds,
        )
    except Exception:
        logger.warning("Failed to persist durable HTTP bridge turn-state alias", exc_info=True)
        return
    if registered is not False:
        return

    fenced_out_session: _HTTPBridgeSession | None = None
    async with service._http_bridge_lock:
        if session.turn_state_alias_registration_generations.get(turn_state) != registration_generation:
            return
        session.turn_state_alias_registration_generations.pop(turn_state, None)
        session.downstream_turn_state_aliases.discard(turn_state)
        if session.downstream_turn_state == turn_state:
            session.downstream_turn_state = None
        alias_key = _http_bridge_turn_state_alias_key(turn_state, session.key.api_key_id)
        current_session = service._http_bridge_sessions.get(session.key)
        current_generation_owns_alias = (
            current_session is not None
            and current_session is not session
            and turn_state in current_session.downstream_turn_state_aliases
        )
        if not current_generation_owns_alias and service._http_bridge_turn_state_index.get(alias_key) == session.key:
            service._http_bridge_turn_state_index.pop(alias_key, None)
        fenced_out_session = _evict_fenced_out_http_bridge_session_locked(
            service,
            session,
            owner_epoch_at_write=owner_epoch,
        )
    _schedule_fenced_out_http_bridge_session_close(service, fenced_out_session, detail="turn_state_alias_fenced_out")


async def _persist_http_bridge_previous_response_alias(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
    *,
    response_id: str,
    registration_generation: int,
    input_item_count: int | None,
    input_full_fingerprint: str | None,
    instance_id: str,
    lease_ttl_seconds: float,
) -> None:
    owner_epoch = session.durable_owner_epoch
    try:
        registered = await service._durable_bridge.register_previous_response_id(
            session_id=session.durable_session_id,
            api_key_id=session.key.api_key_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            response_id=response_id,
            lease_ttl_seconds=lease_ttl_seconds,
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
        )
    except Exception:
        logger.warning("Failed to persist durable HTTP bridge previous_response_id alias", exc_info=True)
        return
    if registered is not False:
        return

    fenced_out_session: _HTTPBridgeSession | None = None
    async with service._http_bridge_lock:
        if session.previous_response_alias_registration_generations.get(response_id) != registration_generation:
            return
        session.previous_response_alias_registration_generations.pop(response_id, None)
        session.previous_response_ids.discard(response_id)
        alias_key = _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id)
        current_session = service._http_bridge_sessions.get(session.key)
        current_generation_owns_alias = (
            current_session is not None
            and current_session is not session
            and response_id in current_session.previous_response_ids
        )
        if (
            not current_generation_owns_alias
            and service._http_bridge_previous_response_index.get(alias_key) == session.key
        ):
            service._http_bridge_previous_response_index.pop(alias_key, None)
        fenced_out_session = _evict_fenced_out_http_bridge_session_locked(
            service,
            session,
            owner_epoch_at_write=owner_epoch,
        )
    _schedule_fenced_out_http_bridge_session_close(
        service, fenced_out_session, detail="previous_response_alias_fenced_out"
    )


def _evict_fenced_out_http_bridge_session_locked(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
    *,
    owner_epoch_at_write: int | None,
) -> _HTTPBridgeSession | None:
    """Detach a session whose fenced durable write proved lost ownership.

    Callers must hold ``service._http_bridge_lock``. When the session's local
    epoch advanced concurrently (same-session re-claim), the fenced rejection
    is stale rather than a lost-ownership signal and no eviction happens.
    """

    if session.closed:
        return None
    if owner_epoch_at_write is None or session.durable_owner_epoch != owner_epoch_at_write:
        return None
    detached = service._detach_http_bridge_session_locked(session.key, expected_session=session)
    if detached is None:
        session.closed = True
    return detached or session


def _schedule_fenced_out_http_bridge_session_close(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession | None,
    *,
    detail: str,
) -> None:
    if session is None:
        return
    _log_http_bridge_event(
        "fenced_out_evict",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=f"outcome={detail}",
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
        owner_check_applied=True,
    )
    service._schedule_http_bridge_session_closes([session], reason="durable_fenced_out")


async def _renew_durable_http_bridge_lease(
    service: _HTTPBridgeServiceProtocol,
    session: _HTTPBridgeSession,
) -> None:
    """Renew the durable lease; callers must hold ``service._http_bridge_lock``.

    Raises the retryable instance-mismatch error after evicting the local
    session when the renewal reveals the durable row is owned by another
    instance/epoch (fenced out).
    """

    if session.durable_session_id is None or session.durable_owner_epoch is None:
        return
    current_instance = _service_get_settings().http_responses_session_bridge_instance_id
    try:
        lookup = await service._durable_bridge.renew_live_session(
            session_id=session.durable_session_id,
            api_key_id=session.key.api_key_id,
            instance_id=current_instance,
            owner_epoch=session.durable_owner_epoch,
            lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
            latest_turn_state=session.downstream_turn_state,
            latest_response_id=None,
        )
    except Exception:
        logger.warning("Failed to renew durable HTTP bridge session lease", exc_info=True)
        return
    if lookup is None:
        return
    if lookup.owner_instance_id == current_instance and lookup.owner_epoch == session.durable_owner_epoch:
        return
    # Fenced out: another instance/epoch owns the durable session. Never adopt
    # the foreign epoch — evict the local session so its upstream websocket and
    # account lease are released, and fail the request with the retryable
    # instance-mismatch contract used by rejected claims.
    detached = service._detach_http_bridge_session_locked(session.key, expected_session=session)
    session.closed = True
    service._schedule_http_bridge_session_closes([detached or session], reason="durable_fenced_out")
    _log_http_bridge_event(
        "fenced_out_evict",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=(
            f"owner_instance={lookup.owner_instance_id}, owner_epoch={lookup.owner_epoch}, "
            f"current_instance={current_instance}, local_epoch={session.durable_owner_epoch}, "
            "outcome=renew_fenced_out"
        ),
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
        owner_check_applied=True,
    )
    if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
        bridge_instance_mismatch_total.labels(outcome="renew_fenced_out").inc()
    raise ProxyResponseError(
        409,
        openai_error(
            "bridge_instance_mismatch",
            "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
            error_type="server_error",
        ),
    )


async def _reconcile_durable_http_bridge_ownership(service: _HTTPBridgeServiceProtocol) -> int:
    """Close local sessions whose durable row is owned by another instance/epoch.

    Piggybacked on the ring-heartbeat cadence so a replica that lost
    ownership releases its orphaned upstream websocket and account lease
    within roughly the durable lease TTL instead of the idle TTL.
    """

    current_instance = _service_get_settings().http_responses_session_bridge_instance_id
    lease_ttl_seconds = _http_bridge_durable_lease_ttl_seconds()
    now = _service_time().monotonic()
    async with service._http_bridge_lock:
        candidates = [
            (key, session)
            for key, session in service._http_bridge_sessions.items()
            if not session.closed
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
            and now - session.last_used_at >= lease_ttl_seconds
        ]
    if not candidates:
        return 0
    lookups = await service._durable_bridge.lookup_sessions(
        session_ids=[session.durable_session_id for _, session in candidates if session.durable_session_id]
    )
    lookup_by_id = {lookup.session_id: lookup for lookup in lookups}
    fenced_sessions: list[_HTTPBridgeSession] = []
    async with service._http_bridge_lock:
        for key, session in candidates:
            lookup = lookup_by_id.get(session.durable_session_id or "")
            if lookup is None or lookup.owner_instance_id is None:
                continue
            if lookup.owner_instance_id == current_instance and lookup.owner_epoch == session.durable_owner_epoch:
                continue
            detached = service._detach_http_bridge_session_locked(key, expected_session=session)
            if detached is None:
                continue
            fenced_sessions.append(detached)
            _log_http_bridge_event(
                "fenced_out_evict",
                key,
                account_id=session.account.id,
                model=session.request_model,
                detail=(
                    f"owner_instance={lookup.owner_instance_id}, owner_epoch={lookup.owner_epoch}, "
                    f"current_instance={current_instance}, local_epoch={session.durable_owner_epoch}, "
                    "outcome=reconcile_sweep"
                ),
                cache_key_family=key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
                owner_check_applied=True,
            )
    if fenced_sessions:
        service._schedule_http_bridge_session_closes(fenced_sessions, reason="durable_fenced_out")
    return len(fenced_sessions)


def _http_bridge_turn_state_anchor_for_owner_failure(
    exc: ProxyResponseError,
    *,
    headers: Mapping[str, str],
    previous_response_id: str | None,
) -> str | None:
    """Return the turn-state anchor when an owner forward failed unreachable."""

    if previous_response_id is not None:
        return None
    turn_state = _sticky_key_from_turn_state_header(headers)
    if turn_state is None:
        return None
    payload = exc.payload
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    if error.get("code") != "bridge_owner_unreachable":
        return None
    return turn_state


def _http_bridge_durable_lookup_allows_turn_state_takeover(lookup: DurableBridgeLookup | None) -> bool:
    """Allow local takeover only when the durable lease is not actively held.

    Released (owner ``None``), expired-lease, CLOSED, and missing rows allow
    takeover. A live lease keeps failing closed even for DRAINING rows:
    shutdown marks rows DRAINING before releasing them, so a draining owner
    may still be finishing an in-flight turn until its lease is released or
    lapses.
    """

    return _durable_bridge_lookup_active_owner(lookup) is None


def _forwarded_http_bridge_session_key(
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
    *,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
) -> _HTTPBridgeSessionKey | None:
    affinity_kind = forwarded_affinity_kind or _header_value_case_insensitive(headers, "x-codex-bridge-affinity-kind")
    affinity_key = forwarded_affinity_key or _header_value_case_insensitive(headers, "x-codex-bridge-affinity-key")
    if affinity_kind is None or affinity_key is None:
        return None
    strength: Literal["hard", "soft"]
    if affinity_kind in _HARD_HTTP_BRIDGE_AFFINITY_KINDS:
        strength = "hard"
    else:
        strength = "soft"
    return _HTTPBridgeSessionKey(
        affinity_kind=affinity_kind,
        affinity_key=affinity_key,
        api_key_id=api_key.id if api_key is not None else None,
        strength=strength,
    )


def _http_bridge_requires_cluster_registration(settings: Settings) -> bool:
    if len(settings.http_responses_session_bridge_instance_ring) > 1:
        return True
    advertise_base_url = settings.http_responses_session_bridge_advertise_base_url
    if advertise_base_url is None:
        return False
    hostname = urlparse(advertise_base_url).hostname
    if hostname is None:
        return False
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        return True
    return not parsed_ip.is_loopback


def _effective_http_bridge_idle_ttl_seconds(
    *,
    affinity: _AffinityPolicy,
    idle_ttl_seconds: float,
    codex_idle_ttl_seconds: float,
    prompt_cache_idle_ttl_seconds: float | None = None,
) -> float:
    if affinity.kind == StickySessionKind.CODEX_SESSION:
        return max(idle_ttl_seconds, codex_idle_ttl_seconds)
    if affinity.kind == StickySessionKind.PROMPT_CACHE and prompt_cache_idle_ttl_seconds is not None:
        return prompt_cache_idle_ttl_seconds
    return idle_ttl_seconds


def _http_bridge_eviction_priority(session: _HTTPBridgeSession) -> tuple[int, float]:
    return (0 if not session.codex_session else 1, session.last_used_at)


def _build_http_bridge_prewarm_text(text_data: str) -> str | None:
    try:
        payload = json.loads(text_data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("generate") is False:
        return None
    previous_response_id = payload.get("previous_response_id")
    if isinstance(previous_response_id, str) and previous_response_id.strip():
        return None
    warmup_payload = dict(payload)
    warmup_payload["generate"] = False
    return json.dumps(warmup_payload, ensure_ascii=True, separators=(",", ":"))


def _http_bridge_prewarm_enabled(settings: Any) -> bool:
    """Prewarm eligibility is the ``prewarm_enabled`` flag alone.

    The canary percent and allow/deny cohort scaffolding was one-time
    rollout tooling retired by ``reduce-settings-surface-phase-4``.
    """
    return bool(getattr(settings, "http_responses_session_bridge_codex_prewarm_enabled", False))


def _record_http_bridge_prewarm_outcome(*, outcome: str) -> None:
    if not PROMETHEUS_AVAILABLE or http_bridge_prewarm_total is None:
        return
    http_bridge_prewarm_total.labels(outcome=outcome).inc()


def _record_http_bridge_stuck_retire(
    *,
    reason: str,
    session: _HTTPBridgeSession,
) -> None:
    if not PROMETHEUS_AVAILABLE or http_bridge_stuck_retire_total is None:
        return
    http_bridge_stuck_retire_total.labels(
        reason=reason,
        affinity_kind=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else "unknown",
    ).inc()


def _http_bridge_payload_without_previous_response_id(payload: ResponsesRequest) -> ResponsesRequest:
    if payload.previous_response_id is None:
        return payload
    return payload.model_copy(update={"previous_response_id": None})


def _http_bridge_previous_response_error_envelope(
    previous_response_id: str,
    detail: str,
) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "previous_response_not_found",
        f"Previous response with id '{previous_response_id}' not found. {detail}",
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "previous_response_id"
    return payload


def _http_bridge_continuity_lost_error_envelope() -> OpenAIErrorEnvelope:
    return previous_response_stream_incomplete_error()


def _http_bridge_owner_lookup_unavailable_error_envelope() -> OpenAIErrorEnvelope:
    return openai_error(
        "upstream_unavailable",
        "HTTP bridge owner metadata unavailable; retry later.",
        error_type="server_error",
    )


def _http_bridge_previous_response_owner_unavailable_error() -> ProxyResponseError:
    return ProxyResponseError(
        502,
        openai_error(
            "previous_response_owner_unavailable",
            "Previous response owner account is unavailable; retry later.",
            error_type="server_error",
        ),
    )


def _http_bridge_should_attempt_local_previous_response_recovery(exc: ProxyResponseError) -> bool:
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if code in {
        "bridge_owner_unreachable",
        "bridge_previous_response_not_found",
        "previous_response_not_found",
        "bridge_instance_mismatch",
    }:
        return True
    param_value = error.get("param")
    param = param_value.strip() if isinstance(param_value, str) and param_value.strip() else None
    message_value = error.get("message")
    message = message_value.strip() if isinstance(message_value, str) and message_value.strip() else None
    return _is_previous_response_not_found_error(code=code, param=param, message=message)


def _http_bridge_is_previous_response_owner_unavailable(exc: ProxyResponseError) -> bool:
    if exc.status_code != 502:
        return False
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    return (
        error.get("code")
        in {
            "previous_response_owner_unavailable",
            "upstream_unavailable",
        }
        and error.get("message") == "Previous response owner account is unavailable; retry later."
    )


def _http_bridge_should_attempt_soft_affinity_reroute(
    exc: ProxyResponseError,
    *,
    key: "_HTTPBridgeSessionKey",
    previous_response_id: str | None,
) -> bool:
    if exc.status_code != 429:
        return False
    if key.strength == "hard" or previous_response_id is not None:
        return False
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    return error.get("code") in {
        "bridge_queue_full",
        "response_create_gate_timeout",
        "account_response_create_cap",
        "account_stream_cap",
    }


def _http_bridge_is_context_overflow_error(exc: ProxyResponseError) -> bool:
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code_value = error.get("code")
    code = code_value.strip() if isinstance(code_value, str) and code_value.strip() else None
    type_value = error.get("type")
    error_type = type_value.strip() if isinstance(type_value, str) and type_value.strip() else None
    normalized_code = _normalize_error_code(code, error_type)
    return normalized_code == "context_length_exceeded"


def _http_bridge_should_rollover_after_context_overflow(
    exc: ProxyResponseError,
    *,
    key: _HTTPBridgeSessionKey | None = None,
) -> bool:
    if not _http_bridge_is_context_overflow_error(exc):
        return False
    if key is not None and key.strength == "hard":
        return False
    return True


def _http_bridge_should_attempt_local_bootstrap_rebind(
    exc: ProxyResponseError,
    *,
    key: _HTTPBridgeSessionKey,
    headers: Mapping[str, str],
    previous_response_id: str | None,
) -> bool:
    if key.affinity_kind != "session_header":
        return False
    if previous_response_id is not None:
        return False
    if _sticky_key_from_turn_state_header(headers) is not None:
        return False
    payload = exc.payload
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    return code in {
        "bridge_owner_unreachable",
        "bridge_instance_mismatch",
    }


def _normalized_http_bridge_instance_ring(settings: Settings) -> tuple[str, tuple[str, ...]]:
    instance_id = settings.http_responses_session_bridge_instance_id.strip()
    if not instance_id:
        instance_id = "codex-lb"
    ring_entries: list[str] = []
    for entry in settings.http_responses_session_bridge_instance_ring:
        stripped = entry.strip()
        if stripped:
            ring_entries.append(stripped)
    if not ring_entries:
        ring_entries.append(instance_id)
    return instance_id, tuple(sorted(set(ring_entries)))


async def _active_http_bridge_instance_ring(
    settings: Settings,
    ring_membership: RingMembershipService | None,
) -> tuple[str, tuple[str, ...]]:
    instance_id, static_ring = _normalized_http_bridge_instance_ring(settings)
    if ring_membership is None:
        return instance_id, static_ring
    try:
        active_members = await ring_membership.list_active(require_endpoint=True)
    except Exception:
        logger.warning("Bridge ring lookup failed — refusing to fall back to static ring", exc_info=True)
        raise
    if not active_members:
        return instance_id, (instance_id,)
    normalized_members = tuple(
        sorted({member.strip() for member in active_members if isinstance(member, str) and member.strip()})
    )
    if not normalized_members:
        return instance_id, static_ring
    return instance_id, normalized_members


async def _http_bridge_owner_instance(
    key: _HTTPBridgeSessionKey,
    settings: Settings,
    ring_membership: RingMembershipService | None = None,
) -> str | None:
    instance_id, ring = await _active_http_bridge_instance_ring(settings, ring_membership)
    if len(ring) <= 1:
        return instance_id
    hash_input = f"{key.affinity_kind}:{key.affinity_key}:{key.api_key_id or ''}"
    return select_node(hash_input, ring)


def _http_bridge_runtime_config(
    dashboard_settings: DashboardSettings,
    app_settings: Settings,
) -> _HTTPBridgeRuntimeConfig:
    return _HTTPBridgeRuntimeConfig(
        enabled=app_settings.http_responses_session_bridge_enabled,
        idle_ttl_seconds=app_settings.http_responses_session_bridge_idle_ttl_seconds,
        codex_idle_ttl_seconds=app_settings.http_responses_session_bridge_codex_idle_ttl_seconds,
        max_sessions=app_settings.http_responses_session_bridge_max_sessions,
        queue_limit=app_settings.http_responses_session_bridge_queue_limit,
        prompt_cache_idle_ttl_seconds=float(
            dashboard_settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        ),
        gateway_safe_mode=dashboard_settings.http_responses_session_bridge_gateway_safe_mode,
    )


def _http_bridge_request_budget_seconds(settings: object) -> float:
    return float(
        getattr(
            settings,
            "http_responses_session_bridge_request_budget_seconds",
            getattr(settings, "proxy_request_budget_seconds", 600.0),
        )
    )


def _http_bridge_admission_timeout_seconds(
    request_state: _WebSocketRequestState,
    admission_timeout_seconds: float,
    settings: object,
) -> float:
    # Bridged requests may retry response-create gate acquisition within one
    # bridge request budget, so every wait must be clamped to the remaining
    # time. Re-prepared retry states reset started_at but deliberately retain
    # the original deadline; using started_at alone would extend the budget.
    deadline = request_state.bridge_request_deadline
    if deadline is None:
        deadline = request_state.started_at + _http_bridge_request_budget_seconds(settings)
    remaining_budget_seconds = deadline - time.monotonic()
    return max(0.0, min(admission_timeout_seconds, remaining_budget_seconds))


def _http_bridge_owner_check_required(
    key: _HTTPBridgeSessionKey,
    *,
    gateway_safe_mode: bool,
) -> bool:
    if key.strength == "hard":
        return True
    return gateway_safe_mode and key.affinity_kind == "sticky_thread"


def _http_bridge_key_strength(key: _HTTPBridgeSessionKey) -> str:
    return key.strength or "soft"


def _log_http_bridge_event(
    event: str,
    key: _HTTPBridgeSessionKey,
    *,
    account_id: str | None,
    model: str | None,
    pending_count: int | None = None,
    detail: str | None = None,
    cache_key_family: str | None = None,
    model_class: str | None = None,
    owner_check_applied: bool | None = None,
) -> None:
    level = logging.INFO
    if event in {
        "queue_full",
        "submit_on_closed",
        "send_failure",
        "retry_fresh_upstream",
        "retry_precreated",
        "reconnect",
        "terminal_error",
        "capacity_exhausted_active_sessions",
        "owner_mismatch",
        "owner_forward_fail",
        "prompt_cache_locality_miss",
        "reallocation_orphan",
        "context_overflow_rollover",
        "missing_response_created_timeout",
    }:
        level = logging.WARNING
    logger.log(
        level,
        "http_bridge_event event=%s bridge_kind=%s bridge_key=%s account_id=%s"
        " model=%s pending=%s detail=%s cache_key_family=%s model_class=%s"
        " key_strength=%s owner_check_applied=%s",
        event,
        key.affinity_kind,
        _hash_identifier(key.affinity_key),
        account_id,
        model,
        pending_count,
        detail,
        cache_key_family,
        model_class,
        _http_bridge_key_strength(key),
        owner_check_applied,
    )


def _patchable_helper(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        service_module = sys.modules.get("app.modules.proxy.service")
        target = getattr(service_module, name, original) if service_module is not None else original
        if target is _wrapper:
            target = original
        return target(*args, **kwargs)

    return _wrapper


for _helper_name in (
    "_http_bridge_startup_wait_timeout_error",
    "_log_http_bridge_startup_wait_timeout",
    "_http_bridge_precreated_retry_failure_error",
    "_trim_http_bridge_previous_response_input_items",
    "_is_http_bridge_previous_response_output_item",
    "_has_http_bridge_response_output_marker",
    "_http_bridge_input_item_type",
    "_normalize_http_bridge_error_event",
    "_http_bridge_request_counts_against_queue",
    "_http_bridge_session_has_visible_requests",
    "_http_bridge_session_retiring_with_visible_requests",
    "_http_bridge_payload_looks_like_full_resend",
    "_preferred_http_bridge_reconnect_turn_state",
    "_http_bridge_turn_state_alias_key",
    "_http_bridge_previous_response_alias_key",
    "_http_bridge_session_allows_api_key",
    "_http_bridge_session_account_active",
    "_http_bridge_session_reusable_for_request",
    "_http_bridge_session_matches_preferred_account",
    "_make_http_bridge_session_key",
    "_http_bridge_should_wait_for_registration",
    "_durable_bridge_lookup_active_owner",
    "_durable_bridge_lookup_allows_local_reuse",
    "_http_bridge_allow_durable_takeover",
    "_http_bridge_has_durable_recovery_anchor",
    "_http_bridge_can_local_recover_without_ring",
    "_http_bridge_can_single_instance_owner_takeover_without_anchor",
    "_http_bridge_can_single_instance_prompt_cache_takeover_without_anchor",
    "_http_bridge_endpoint_matches_current_instance",
    "_http_bridge_can_recover_during_drain",
    "_http_bridge_request_stage",
    "_record_bridge_reattach",
    "_record_bridge_first_turn_timeout",
    "_record_bridge_drain_recovery_allowed",
    "_is_missing_durable_bridge_table_error",
    "_http_bridge_durable_lease_ttl_seconds",
    "_forwarded_http_bridge_session_key",
    "_http_bridge_requires_cluster_registration",
    "_effective_http_bridge_idle_ttl_seconds",
    "_http_bridge_eviction_priority",
    "_build_http_bridge_prewarm_text",
    "_http_bridge_prewarm_enabled",
    "_record_http_bridge_prewarm_outcome",
    "_record_http_bridge_stuck_retire",
    "_http_bridge_payload_without_previous_response_id",
    "_http_bridge_previous_response_error_envelope",
    "_http_bridge_continuity_lost_error_envelope",
    "_http_bridge_owner_lookup_unavailable_error_envelope",
    "_http_bridge_should_attempt_local_previous_response_recovery",
    "_http_bridge_is_previous_response_owner_unavailable",
    "_http_bridge_should_attempt_soft_affinity_reroute",
    "_http_bridge_is_context_overflow_error",
    "_http_bridge_should_rollover_after_context_overflow",
    "_http_bridge_should_attempt_local_bootstrap_rebind",
    "_http_bridge_turn_state_anchor_for_owner_failure",
    "_http_bridge_durable_lookup_allows_turn_state_takeover",
    "_normalized_http_bridge_instance_ring",
    "_active_http_bridge_instance_ring",
    "_http_bridge_owner_instance",
    "_http_bridge_runtime_config",
    "_http_bridge_owner_check_required",
    "_http_bridge_key_strength",
    "_log_http_bridge_event",
):
    globals()[_helper_name] = _patchable_helper(_helper_name, globals()[_helper_name])

del _helper_name
