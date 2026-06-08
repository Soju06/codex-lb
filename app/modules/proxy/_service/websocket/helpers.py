from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import deque
from collections.abc import Sequence
from typing import Any, cast

import anyio

from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.http import lease_http_session as lease_http_session  # noqa: F401
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
from app.core.clients.proxy_websocket import (
    UpstreamWebSocketMessage,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_CODE,
    PREVIOUS_RESPONSE_STALE_MESSAGE,
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorEnvelope,
    openai_error,
    previous_response_stream_incomplete_error,
    response_failed_event,
)
from app.core.exceptions import AppError
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.types import JsonValue
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME as CODEX_KEEPALIVE_FRAME  # noqa: F401
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow as utcnow
from app.db.models import (
    AccountStatus,  # noqa: F401
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
from app.modules.proxy._service.http_bridge.helpers import (
    _active_http_bridge_instance_ring as _active_http_bridge_instance_ring,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _build_http_bridge_prewarm_text as _build_http_bridge_prewarm_text,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _durable_bridge_lookup_active_owner as _durable_bridge_lookup_active_owner,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _durable_bridge_lookup_allows_local_reuse as _durable_bridge_lookup_allows_local_reuse,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _effective_http_bridge_idle_ttl_seconds as _effective_http_bridge_idle_ttl_seconds,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _forwarded_http_bridge_session_key as _forwarded_http_bridge_session_key,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _has_http_bridge_response_output_marker as _has_http_bridge_response_output_marker,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_allow_durable_takeover as _http_bridge_allow_durable_takeover,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_can_local_recover_without_ring as _http_bridge_can_local_recover_without_ring,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_can_recover_during_drain as _http_bridge_can_recover_during_drain,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_continuity_lost_error_envelope as _http_bridge_continuity_lost_error_envelope,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_durable_lease_ttl_seconds as _http_bridge_durable_lease_ttl_seconds,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_endpoint_matches_current_instance as _http_bridge_endpoint_matches_current_instance,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_eviction_priority as _http_bridge_eviction_priority,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_has_durable_recovery_anchor as _http_bridge_has_durable_recovery_anchor,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_input_item_type as _http_bridge_input_item_type,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_is_context_overflow_error as _http_bridge_is_context_overflow_error,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_is_previous_response_owner_unavailable as _http_bridge_is_previous_response_owner_unavailable,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_key_strength as _http_bridge_key_strength,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_owner_check_required as _http_bridge_owner_check_required,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_owner_instance as _http_bridge_owner_instance,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_owner_lookup_unavailable_error_envelope as _http_bridge_owner_lookup_unavailable_error_envelope,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_payload_looks_like_full_resend as _http_bridge_payload_looks_like_full_resend,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_payload_without_previous_response_id as _http_bridge_payload_without_previous_response_id,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_precreated_retry_failure_error as _http_bridge_precreated_retry_failure_error,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_previous_response_alias_key as _http_bridge_previous_response_alias_key,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_previous_response_error_envelope as _http_bridge_previous_response_error_envelope,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_request_counts_against_queue as _http_bridge_request_counts_against_queue,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_request_stage as _http_bridge_request_stage,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_requires_cluster_registration as _http_bridge_requires_cluster_registration,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_runtime_config as _http_bridge_runtime_config,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_session_allows_api_key as _http_bridge_session_allows_api_key,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_session_has_visible_requests as _http_bridge_session_has_visible_requests,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_session_matches_preferred_account as _http_bridge_session_matches_preferred_account,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_session_retiring_with_visible_requests as _http_bridge_session_retiring_with_visible_requests,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_session_reusable_for_request as _http_bridge_session_reusable_for_request,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_should_attempt_local_bootstrap_rebind as _http_bridge_should_attempt_local_bootstrap_rebind,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_should_attempt_local_previous_response_recovery,  # noqa: F401
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_should_attempt_soft_affinity_reroute as _http_bridge_should_attempt_soft_affinity_reroute,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_should_rollover_after_context_overflow as _http_bridge_should_rollover_after_context_overflow,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_should_wait_for_registration as _http_bridge_should_wait_for_registration,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_startup_wait_timeout_error as _http_bridge_startup_wait_timeout_error,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_turn_state_alias_key as _http_bridge_turn_state_alias_key,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _is_http_bridge_previous_response_output_item as _is_http_bridge_previous_response_output_item,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _is_missing_durable_bridge_table_error as _is_missing_durable_bridge_table_error,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _log_http_bridge_event as _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _log_http_bridge_startup_wait_timeout as _log_http_bridge_startup_wait_timeout,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _make_http_bridge_session_key as _make_http_bridge_session_key,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _normalize_http_bridge_error_event as _normalize_http_bridge_error_event,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _normalized_http_bridge_instance_ring as _normalized_http_bridge_instance_ring,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _preferred_http_bridge_reconnect_turn_state as _preferred_http_bridge_reconnect_turn_state,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _record_bridge_drain_recovery_allowed as _record_bridge_drain_recovery_allowed,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _record_bridge_first_turn_timeout as _record_bridge_first_turn_timeout,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _record_bridge_reattach as _record_bridge_reattach,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _trim_http_bridge_previous_response_input_items as _trim_http_bridge_previous_response_input_items,
)
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
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_request_error_overrides,
    _event_type_from_payload,
    _websocket_request_can_replay_before_visible_output,
    _WebSocketContinuityAnchor,
    _WebSocketContinuityState,
    _WebSocketReceiveTimeout,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeOwnerForward as _HTTPBridgeOwnerForward,
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
from app.modules.proxy.affinity import (
    _sticky_key_from_session_header,  # noqa: F401
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
    _parse_openai_error,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext as HTTPBridgeForwardContext,
)
from app.modules.proxy.http_bridge_forwarding import (
    OwnerForwardRelayFailure as OwnerForwardRelayFailure,
)


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


def _prepare_websocket_request_state_for_visible_output_replay(
    request_state: "_WebSocketRequestState",
) -> str | None:
    downstream_response_id = None
    if request_state.response_id is not None and not request_state.awaiting_response_created:
        downstream_response_id = request_state.response_id
    if request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text:
        request_state.request_text = request_state.fresh_upstream_request_text
        request_state.previous_response_id = None
        request_state.proxy_injected_previous_response_id = False
        request_state.fresh_upstream_request_is_retry_safe = False
        _refresh_websocket_request_input_fingerprint_from_text(request_state)
    request_text = request_state.request_text
    if not isinstance(request_text, str):
        return None
    request_state.replay_count += 1
    request_state.awaiting_response_created = True
    request_state.response_id = None
    request_state.response_event_count = 0
    request_state.replay_downstream_response_id = downstream_response_id
    request_state.suppress_next_created_downstream = downstream_response_id is not None
    _clear_websocket_request_error_overrides(request_state)
    return request_text


def _websocket_continuity_anchor_for_payload(
    continuity_state: _WebSocketContinuityState | None,
    *,
    responses_payload: ResponsesRequest,
    codex_session_affinity: bool,
) -> _WebSocketContinuityAnchor | None:
    if not codex_session_affinity or continuity_state is None:
        return None
    if responses_payload.previous_response_id is not None:
        return None
    previous_response_id = continuity_state.last_completed_response_id
    stored_count = continuity_state.last_completed_input_count
    stored_fingerprint = continuity_state.last_completed_input_prefix_fingerprint
    incoming_input = responses_payload.input
    if (
        previous_response_id is None
        or stored_count <= 0
        or stored_fingerprint is None
        or not isinstance(incoming_input, list)
        or len(incoming_input) <= stored_count
    ):
        return None
    incoming_input_list = cast(list[JsonValue], incoming_input)
    incoming_prefix_fingerprint = _facade()._fingerprint_input_items(incoming_input_list[:stored_count])
    if incoming_prefix_fingerprint != stored_fingerprint:
        return None
    return _WebSocketContinuityAnchor(
        previous_response_id=previous_response_id,
        stored_input_item_count=stored_count,
    )


def _websocket_client_previous_response_full_resend_is_retry_safe(
    *,
    previous_response_id: str | None,
    input_value: JsonValue,
    continuity_state: _WebSocketContinuityState | None,
) -> bool:
    if previous_response_id is None or not isinstance(input_value, list):
        return False
    input_items = cast(list[JsonValue], input_value)
    if len(input_items) <= 1:
        return False
    if (
        continuity_state is not None
        and continuity_state.last_completed_response_id == previous_response_id
        and (
            continuity_state.last_completed_input_count > 0
            or continuity_state.last_completed_input_prefix_fingerprint is not None
        )
    ):
        return _facade()._input_prefix_matches_stored_context(
            input_value,
            stored_count=continuity_state.last_completed_input_count,
            stored_fingerprint=continuity_state.last_completed_input_prefix_fingerprint,
        )
    return True


def _record_websocket_continuity_completion(
    continuity_state: _WebSocketContinuityState,
    *,
    request_state: _WebSocketRequestState,
    response_id: str | None,
) -> None:
    if response_id is None or request_state.input_item_count <= 0 or request_state.input_full_fingerprint is None:
        continuity_state.last_completed_response_id = None
        continuity_state.last_completed_input_count = 0
        continuity_state.last_completed_input_prefix_fingerprint = None
        continuity_state.last_pending_function_call_ids = []
        return
    continuity_state.last_completed_response_id = response_id
    continuity_state.last_completed_input_count = request_state.input_item_count
    continuity_state.last_completed_input_prefix_fingerprint = request_state.input_full_fingerprint
    continuity_state.last_pending_function_call_ids = list(request_state.pending_function_call_ids)


def _websocket_response_id(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None and event.response is not None and event.response.id:
        return event.response.id
    if not isinstance(payload, dict):
        return None
    direct_response_id = payload.get("response_id")
    if isinstance(direct_response_id, str):
        stripped_direct_response_id = direct_response_id.strip()
        if stripped_direct_response_id:
            return stripped_direct_response_id
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    response_id = response.get("id")
    if not isinstance(response_id, str):
        return None
    stripped = response_id.strip()
    return stripped or None


def _websocket_downstream_response_id(request_state: "_WebSocketRequestState") -> str:
    return request_state.replay_downstream_response_id or request_state.response_id or request_state.request_id


def _websocket_continuity_response_ids(
    request_state: "_WebSocketRequestState",
    upstream_response_id: str | None,
) -> tuple[str, ...]:
    response_ids: list[str] = []
    for response_id in (upstream_response_id, request_state.replay_downstream_response_id):
        if not isinstance(response_id, str):
            continue
        stripped = response_id.strip()
        if stripped and stripped not in response_ids:
            response_ids.append(stripped)
    return tuple(response_ids)


def _rewrite_websocket_downstream_response_id(
    payload: dict[str, JsonValue],
    request_state: "_WebSocketRequestState",
) -> dict[str, JsonValue]:
    downstream_response_id = request_state.replay_downstream_response_id
    if downstream_response_id is None:
        return payload

    rewritten = dict(payload)
    if isinstance(rewritten.get("response_id"), str):
        rewritten["response_id"] = downstream_response_id
    response = rewritten.get("response")
    if isinstance(response, dict) and isinstance(response.get("id"), str):
        rewritten["response"] = {**response, "id": downstream_response_id}
    return rewritten


def _websocket_event_error_code(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    code_value = error.get("code")
    if not isinstance(code_value, str):
        return None
    stripped = code_value.strip()
    return stripped or None


def _websocket_event_error_type(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    type_value = error.get("type")
    if not isinstance(type_value, str):
        return None
    stripped = type_value.strip()
    return stripped or None


def _websocket_event_error_param(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    param_value = error.get("param")
    if not isinstance(param_value, str):
        return None
    stripped = param_value.strip()
    return stripped or None


def _websocket_event_error_message(event_type: str | None, payload: dict[str, JsonValue] | None) -> str | None:
    error = _websocket_event_error_payload(event_type, payload)
    if not isinstance(error, dict):
        return None
    message_value = error.get("message")
    if not isinstance(message_value, str):
        return None
    stripped = message_value.strip()
    return stripped or None


def _websocket_precreated_retry_error_code(
    request_state: _WebSocketRequestState | None,
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    has_other_pending_requests: bool,
) -> str | None:
    if request_state is None:
        return None
    if has_other_pending_requests:
        return None
    if request_state.response_id is not None:
        return None
    if request_state.response_event_count > 0:
        return None
    if not request_state.awaiting_response_created:
        return None
    if not request_state.request_text:
        return None
    if request_state.replay_count >= 1:
        return None
    if event_type not in {"error", "response.failed"}:
        return None

    error_code = _normalize_error_code(
        _websocket_event_error_code(event_type, payload),
        _websocket_event_error_type(event_type, payload),
    )
    error_param = _websocket_event_error_param(event_type, payload)
    error_message = _websocket_event_error_message(event_type, payload)
    if _facade()._is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        return "stream_incomplete"
    if _facade()._is_missing_tool_output_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        return None
    if error_code not in _facade()._WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES:
        return None
    return error_code


def _websocket_precreated_auth_error_code(
    request_state: _WebSocketRequestState | None,
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    has_other_pending_requests: bool,
) -> str | None:
    if request_state is None:
        return None
    if has_other_pending_requests:
        return None
    if request_state.response_id is not None:
        return None
    if request_state.response_event_count > 0:
        return None
    if not request_state.awaiting_response_created:
        return None
    if not request_state.request_text:
        return None
    if request_state.downstream_visible:
        return None
    if event_type not in {"error", "response.failed"}:
        return None

    error_code = _normalize_error_code(
        _websocket_event_error_code(event_type, payload),
        _websocket_event_error_type(event_type, payload),
    )
    error_type = _websocket_event_error_type(event_type, payload)
    if error_code in _facade()._WEBSOCKET_AUTH_FAILURE_CODES or error_type == "authentication_error":
        return "invalid_api_key"
    return None


def _websocket_auth_failure_requires_reauth(message: str | None) -> bool:
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _facade()._WEBSOCKET_REAUTH_REQUIRED_MESSAGE_MARKERS)


def _websocket_auth_failure_permanent_code(message: str | None) -> str:
    if _websocket_auth_failure_requires_reauth(message):
        return _facade()._WEBSOCKET_SESSION_EXPIRED_FAILURE_CODE
    return _facade()._WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE


def _websocket_auth_request_can_switch_account(request_state: _WebSocketRequestState) -> bool:
    if request_state.previous_response_id is None or request_state.preferred_account_id is None:
        return True
    return bool(
        request_state.proxy_injected_previous_response_id
        and request_state.fresh_upstream_request_is_retry_safe
        and request_state.fresh_upstream_request_text
    )


def _prepare_websocket_request_state_for_auth_replay(
    request_state: _WebSocketRequestState,
) -> str | None:
    if not _websocket_auth_request_can_switch_account(request_state):
        return None
    if (
        request_state.proxy_injected_previous_response_id
        and request_state.fresh_upstream_request_is_retry_safe
        and request_state.fresh_upstream_request_text
    ):
        request_state.request_text = request_state.fresh_upstream_request_text
        request_state.previous_response_id = None
        request_state.preferred_account_id = None
        request_state.proxy_injected_previous_response_id = False
        request_state.fresh_upstream_request_is_retry_safe = False
        _refresh_websocket_request_input_fingerprint_from_text(request_state)
    request_text = request_state.request_text
    if not isinstance(request_text, str):
        return None
    request_state.replay_count += 1
    request_state.auth_replay_count += 1
    request_state.awaiting_response_created = True
    request_state.response_id = None
    request_state.response_event_count = 0
    _clear_websocket_request_error_overrides(request_state)
    return request_text


def _websocket_owner_pinned_quota_error_code(
    request_state: _WebSocketRequestState | None,
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
) -> str | None:
    if request_state is None:
        return None
    if request_state.previous_response_id is None or request_state.preferred_account_id is None:
        return None
    if request_state.response_id is not None:
        return None
    if not request_state.awaiting_response_created:
        return None
    if not request_state.request_text:
        return None
    if event_type not in {"error", "response.failed"}:
        return None

    error_code = _normalize_error_code(
        _websocket_event_error_code(event_type, payload),
        _websocket_event_error_type(event_type, payload),
    )
    if error_code not in _facade()._WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES:
        return None
    return error_code


async def _pop_replayable_precreated_websocket_request_state(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
) -> _WebSocketRequestState | None:
    async with pending_lock:
        if len(pending_requests) != 1:
            return None
        request_state = pending_requests[0]
        if not _websocket_request_can_replay_before_visible_output(request_state):
            return None
        pending_requests.popleft()
    if _prepare_websocket_request_state_for_visible_output_replay(request_state) is None:
        return None
    return request_state


async def _websocket_full_resend_conflicts_with_visible_pending(
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
        return any(pending is not request_state and pending.downstream_visible for pending in pending_requests)


def _refresh_websocket_request_input_fingerprint_from_text(request_state: _WebSocketRequestState) -> None:
    if not request_state.request_text:
        request_state.input_item_count = 0
        request_state.input_full_fingerprint = None
        return
    try:
        payload = json.loads(request_state.request_text)
    except json.JSONDecodeError:
        request_state.input_item_count = 0
        request_state.input_full_fingerprint = None
        return
    if not isinstance(payload, dict):
        request_state.input_item_count = 0
        request_state.input_full_fingerprint = None
        return
    input_items = payload.get("input")
    if not isinstance(input_items, list):
        request_state.input_item_count = 0
        request_state.input_full_fingerprint = None
        return
    request_state.input_item_count = len(input_items)
    request_state.input_full_fingerprint = _facade()._fingerprint_input_items(cast(list[JsonValue], input_items))


def _websocket_top_level_error_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    if payload.get("type") != "error":
        return None
    error: dict[str, JsonValue] = {}
    for error_field in ("code", "message", "param"):
        value = payload.get(error_field)
        if isinstance(value, str) and value.strip():
            error[error_field] = value.strip()
    error_type = payload.get("error_type")
    if isinstance(error_type, str) and error_type.strip():
        error["type"] = error_type.strip()
    for metadata_field in ("plan_type", "resets_at", "resets_in_seconds"):
        value = payload.get(metadata_field)
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            error[metadata_field] = value
    return error or None


def _websocket_event_error_payload(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    if not isinstance(payload, dict):
        return None
    if event_type == "error":
        error = payload.get("error")
        if isinstance(error, dict):
            return cast(dict[str, JsonValue], error)
        # The ChatGPT-backed Codex websocket can emit OpenAI-style error
        # details directly on the event frame instead of under an ``error``
        # envelope. Normalize those fields into an error-detail object so the
        # event discriminator ``type: error`` is not mistaken for the upstream
        # error type.
        return _websocket_top_level_error_payload(payload)
    if event_type == "response.failed":
        response = payload.get("response")
        error = response.get("error") if isinstance(response, dict) else None
        return cast(dict[str, JsonValue], error) if isinstance(error, dict) else None
    return None


def _maybe_rewrite_websocket_previous_response_not_found_event(
    *,
    request_state: _WebSocketRequestState,
    event: OpenAIEvent | None,
    payload: dict[str, JsonValue] | None,
    event_type: str | None,
    upstream_control: _WebSocketUpstreamControl,
    original_text: str,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    error_code = _websocket_event_error_code(event_type, payload)
    error_param = _websocket_event_error_param(event_type, payload)
    error_message = _websocket_event_error_message(event_type, payload)
    should_rewrite = _facade()._is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    )
    reason = "previous_response_not_found"
    if not should_rewrite:
        if request_state.previous_response_id is None:
            return event, payload, event_type, original_text
        should_rewrite = _facade()._is_missing_tool_output_error(
            code=error_code,
            param=error_param,
            message=error_message,
        )
        reason = "missing_tool_output"
    if not should_rewrite:
        return event, payload, event_type, original_text

    reconnect_requested = reason == "missing_tool_output" or request_state.preferred_account_id is not None
    return _rewrite_websocket_continuity_corruption_event(
        request_state=request_state,
        upstream_control=upstream_control,
        reason=reason,
        reconnect_requested=reconnect_requested,
        original_text=original_text,
    )


def _websocket_continuity_error_fields(
    *,
    reason: str,
    expose_stale_previous_response_classifier: bool,
) -> tuple[str, str]:
    if reason == "previous_response_not_found" and expose_stale_previous_response_classifier:
        return PREVIOUS_RESPONSE_STALE_CODE, PREVIOUS_RESPONSE_STALE_MESSAGE
    return "stream_incomplete", PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE


def _rewrite_websocket_continuity_corruption_event(
    *,
    request_state: _WebSocketRequestState,
    upstream_control: _WebSocketUpstreamControl,
    reason: str,
    reconnect_requested: bool,
    original_text: str,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    del original_text
    if reconnect_requested:
        upstream_control.reconnect_requested = True
    _record_continuity_fail_closed(
        surface="websocket_stream",
        reason=reason,
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
    )
    rewritten_code, rewritten_message = _websocket_continuity_error_fields(
        reason=reason,
        expose_stale_previous_response_classifier=request_state.expose_stale_previous_response_classifier,
    )
    rewritten_event_payload = response_failed_event(
        rewritten_code,
        rewritten_message,
        error_type="server_error",
        response_id=_websocket_downstream_response_id(request_state),
    )
    rewritten_text = json.dumps(rewritten_event_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event, rewritten_payload, rewritten_event_type, rewritten_text


def _rewrite_websocket_previous_response_owner_unavailable_event(
    *,
    request_state: _WebSocketRequestState,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    _record_continuity_fail_closed(
        surface="websocket_stream",
        reason="owner_account_unavailable",
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
    )
    rewritten_event_payload = response_failed_event(
        "upstream_unavailable",
        "Previous response owner account is unavailable; retry later.",
        error_type="server_error",
        response_id=_websocket_downstream_response_id(request_state),
    )
    rewritten_text = json.dumps(rewritten_event_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event, rewritten_payload, rewritten_event_type, rewritten_text


def _rewrite_websocket_suppressed_duplicate_tool_call_completion_event(
    *,
    request_state: _WebSocketRequestState,
) -> tuple[OpenAIEvent | None, dict[str, JsonValue] | None, str | None, str]:
    rewritten_event_payload = response_failed_event(
        "stream_incomplete",
        _facade()._SUPPRESSED_DUPLICATE_TOOL_CALL_MESSAGE,
        error_type="server_error",
        response_id=_websocket_downstream_response_id(request_state),
    )
    rewritten_text = json.dumps(rewritten_event_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event, rewritten_payload, rewritten_event_type, rewritten_text


def _sanitize_websocket_connect_failure(
    *,
    request_state: _WebSocketRequestState,
    status_code: int,
    payload: OpenAIErrorEnvelope,
    error_code: str,
    error_message: str,
) -> tuple[int, OpenAIErrorEnvelope, str, str]:
    return _sanitize_websocket_previous_response_error(
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
        status_code=status_code,
        payload=payload,
        error_code=error_code,
        error_message=error_message,
        surface="websocket_connect",
        expose_stale_previous_response_classifier=request_state.expose_stale_previous_response_classifier,
    )


def _sanitize_websocket_previous_response_error(
    *,
    previous_response_id: str | None,
    session_id: str | None,
    status_code: int,
    payload: OpenAIErrorEnvelope,
    error_code: str,
    error_message: str,
    surface: str,
    expose_stale_previous_response_classifier: bool = False,
) -> tuple[int, OpenAIErrorEnvelope, str, str]:
    if previous_response_id is None:
        return status_code, payload, error_code, error_message
    parsed_error = _parse_openai_error(payload)
    normalized_code = _normalize_error_code(
        parsed_error.code if parsed_error else error_code,
        parsed_error.type if parsed_error else None,
    )
    normalized_message = parsed_error.message if parsed_error and parsed_error.message else error_message
    reason = "previous_response_not_found"
    should_rewrite = _facade()._is_previous_response_not_found_error(
        code=normalized_code,
        param=parsed_error.param if parsed_error else None,
        message=normalized_message,
    )
    if not should_rewrite:
        should_rewrite = _facade()._is_missing_tool_output_error(
            code=normalized_code,
            param=parsed_error.param if parsed_error else None,
            message=normalized_message,
        )
        reason = "missing_tool_output"
    if not should_rewrite:
        return status_code, payload, error_code, error_message

    rewritten_code, rewritten_message = _websocket_continuity_error_fields(
        reason=reason,
        expose_stale_previous_response_classifier=expose_stale_previous_response_classifier,
    )
    _record_continuity_fail_closed(
        surface=surface,
        reason=reason,
        previous_response_id=previous_response_id,
        session_id=session_id,
        upstream_error_code=normalized_code,
    )
    return (
        502,
        openai_error(
            rewritten_code,
            rewritten_message,
            error_type="server_error",
        ),
        rewritten_code,
        rewritten_message,
    )


def _sanitize_websocket_terminal_error_fields(
    *,
    request_state: _WebSocketRequestState,
    error_code: str,
    error_message: str,
    error_type: str,
    error_param: str | None,
) -> tuple[str, str, str, str | None]:
    normalized_code = _normalize_error_code(error_code, error_type)
    if not _facade()._is_previous_response_not_found_error(
        code=normalized_code,
        param=error_param,
        message=error_message,
    ):
        return error_code, error_message, error_type, error_param
    _record_continuity_fail_closed(
        surface="websocket_terminal",
        reason="previous_response_not_found",
        previous_response_id=request_state.previous_response_id,
        session_id=request_state.session_id,
        upstream_error_code=normalized_code,
    )
    rewritten_code, rewritten_message = _websocket_continuity_error_fields(
        reason="previous_response_not_found",
        expose_stale_previous_response_classifier=request_state.expose_stale_previous_response_classifier,
    )
    return (
        rewritten_code,
        rewritten_message,
        "server_error",
        None,
    )


def _find_websocket_request_state_by_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str,
) -> _WebSocketRequestState | None:
    for request_state in pending_requests:
        if request_state.response_id == response_id:
            return request_state
    return None


def _assign_websocket_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str | None,
) -> _WebSocketRequestState | None:
    if response_id is None:
        return None
    existing = _find_websocket_request_state_by_response_id(pending_requests, response_id)
    if existing is not None:
        return existing
    for request_state in pending_requests:
        if request_state.response_id is None and _http_bridge_request_counts_against_queue(request_state):
            request_state.response_id = response_id
            return request_state
    for request_state in pending_requests:
        if request_state.response_id is None and request_state.draining_until_terminal:
            request_state.response_id = response_id
            return request_state
    for request_state in pending_requests:
        if request_state.response_id is None:
            request_state.response_id = response_id
            return request_state
    return None


def _draining_websocket_request_states(
    pending_requests: deque[_WebSocketRequestState],
) -> list[_WebSocketRequestState]:
    return [request_state for request_state in pending_requests if request_state.draining_until_terminal]


def _match_websocket_request_state_for_anonymous_event(
    pending_requests: deque[_WebSocketRequestState],
    *,
    prefer_previous_response_not_found: bool,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
    allow_unanchored_previous_response_error: bool = False,
    prefer_draining_requests: bool = True,
) -> _WebSocketRequestState | None:
    if prefer_previous_response_not_found:
        return _match_websocket_request_state_for_previous_response_error(
            pending_requests,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
            allow_unanchored_previous_response_error=allow_unanchored_previous_response_error,
        )

    visible_requests = [
        request_state for request_state in pending_requests if _http_bridge_request_counts_against_queue(request_state)
    ]
    draining_requests = _draining_websocket_request_states(pending_requests)
    if prefer_draining_requests and draining_requests:
        unresolved_draining_requests = [
            request_state for request_state in draining_requests if request_state.response_id is None
        ]
        if len(unresolved_draining_requests) == 1:
            return unresolved_draining_requests[0]
        if not visible_requests:
            return draining_requests[0]

    if len(visible_requests) == 1:
        return visible_requests[0]

    unresolved_visible_requests = [
        request_state for request_state in visible_requests if request_state.response_id is None
    ]
    if len(unresolved_visible_requests) == 1:
        return unresolved_visible_requests[0]

    if not visible_requests and draining_requests:
        unresolved_draining_requests = [
            request_state for request_state in draining_requests if request_state.response_id is None
        ]
        if len(unresolved_draining_requests) == 1:
            return unresolved_draining_requests[0]
        return draining_requests[0]

    return None


def _match_websocket_request_state_for_precreated_terminal_event(
    pending_requests: deque[_WebSocketRequestState],
) -> _WebSocketRequestState | None:
    unresolved_requests = [
        request_state
        for request_state in pending_requests
        if request_state.response_id is None and request_state.awaiting_response_created
    ]
    if len(unresolved_requests) == 1:
        return unresolved_requests[0]
    return None


def _match_websocket_request_state_for_previous_response_error(
    pending_requests: deque[_WebSocketRequestState],
    *,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
    allow_unanchored_previous_response_error: bool = False,
) -> _WebSocketRequestState | None:
    matching_requests = _matching_websocket_request_states_for_previous_response_error(
        pending_requests,
        previous_response_id_hint=previous_response_id_hint,
        error_message=error_message,
        allow_unanchored_previous_response_error=allow_unanchored_previous_response_error,
    )
    if len(matching_requests) == 1:
        return matching_requests[0]
    return None


def _matching_websocket_request_states_for_previous_response_error(
    pending_requests: deque[_WebSocketRequestState],
    *,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
    allow_unanchored_previous_response_error: bool = False,
) -> list[_WebSocketRequestState]:
    followup_requests = [
        request_state for request_state in pending_requests if request_state.previous_response_id is not None
    ]
    if not followup_requests:
        if allow_unanchored_previous_response_error and len(pending_requests) == 1:
            return [pending_requests[0]]
        return []
    if previous_response_id_hint is not None:
        matching_requests = [
            request_state
            for request_state in followup_requests
            if request_state.previous_response_id == previous_response_id_hint
        ]
        if matching_requests:
            return matching_requests
    if error_message is not None:
        matching_requests = [
            request_state
            for request_state in followup_requests
            if _facade()._message_mentions_previous_response_id(error_message, request_state.previous_response_id)
        ]
        if matching_requests:
            return matching_requests
    unresolved_followups = [request_state for request_state in followup_requests if request_state.response_id is None]
    if len(unresolved_followups) == 1:
        return unresolved_followups
    if len(unresolved_followups) > 1:
        unique_previous_response_ids = {
            request_state.previous_response_id
            for request_state in unresolved_followups
            if request_state.previous_response_id
        }
        if len(unique_previous_response_ids) == 1:
            return unresolved_followups
    return []


def _matching_websocket_request_states_for_missing_tool_output_error(
    pending_requests: deque[_WebSocketRequestState],
) -> list[_WebSocketRequestState]:
    unresolved_followups = [
        request_state
        for request_state in pending_requests
        if request_state.response_id is None and request_state.previous_response_id is not None
    ]
    if len(unresolved_followups) <= 1:
        return unresolved_followups
    unique_previous_response_ids = {
        request_state.previous_response_id
        for request_state in unresolved_followups
        if request_state.previous_response_id
    }
    if len(unique_previous_response_ids) == 1:
        return unresolved_followups
    return []


def _pop_matching_websocket_request_states(
    pending_requests: deque[_WebSocketRequestState],
    matching_requests: list[_WebSocketRequestState],
) -> list[_WebSocketRequestState]:
    popped_requests: list[_WebSocketRequestState] = []
    for request_state in matching_requests:
        try:
            pending_requests.remove(request_state)
        except ValueError:
            continue
        popped_requests.append(request_state)
    return popped_requests


async def _release_websocket_response_create_gate(
    request_state: _WebSocketRequestState,
    response_create_gate: asyncio.Semaphore,
) -> None:
    account_response_create_lease = request_state.account_response_create_lease
    account_response_create_release = request_state.account_response_create_release
    request_state.account_response_create_lease = None
    request_state.account_response_create_release = None
    if request_state.response_create_admission is not None:
        request_state.response_create_admission.release()
        request_state.response_create_admission = None
    if account_response_create_lease is not None and account_response_create_release is not None:
        await account_response_create_release(account_response_create_lease)
    request_state.awaiting_response_created = False
    request_state.response_create_gate = None
    if not request_state.response_create_gate_acquired:
        return
    request_state.response_create_gate_acquired = False
    response_create_gate.release()


def _pop_terminal_websocket_request_state(
    pending_requests: deque[_WebSocketRequestState],
    *,
    response_id: str | None,
    fallback_request_state: _WebSocketRequestState | None,
    prefer_previous_response_not_found: bool = False,
    previous_response_id_hint: str | None = None,
    error_message: str | None = None,
    allow_unanchored_previous_response_error: bool = False,
    allow_precreated_terminal_fallback: bool = False,
    prefer_draining_requests: bool = True,
) -> _WebSocketRequestState | None:
    if response_id is not None:
        request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
        if request_state is not None:
            pending_requests.remove(request_state)
            return request_state
    if fallback_request_state is not None and fallback_request_state in pending_requests:
        pending_requests.remove(fallback_request_state)
        return fallback_request_state
    if response_id is not None and allow_precreated_terminal_fallback:
        request_state = _match_websocket_request_state_for_precreated_terminal_event(pending_requests)
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    if response_id is not None and prefer_previous_response_not_found:
        request_state = _match_websocket_request_state_for_previous_response_error(
            pending_requests,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
            allow_unanchored_previous_response_error=allow_unanchored_previous_response_error,
        )
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    if response_id is None:
        request_state = _match_websocket_request_state_for_anonymous_event(
            pending_requests,
            prefer_previous_response_not_found=prefer_previous_response_not_found,
            previous_response_id_hint=previous_response_id_hint,
            error_message=error_message,
            allow_unanchored_previous_response_error=allow_unanchored_previous_response_error,
            prefer_draining_requests=prefer_draining_requests,
        )
        if request_state is not None and request_state in pending_requests:
            pending_requests.remove(request_state)
            return request_state
    return None


def _upstream_websocket_disconnect_message(message: UpstreamWebSocketMessage) -> str:
    if message.kind == "error" and message.error:
        return f"Upstream websocket closed before response.completed: {message.error}"
    if message.close_code is not None:
        return f"Upstream websocket closed before response.completed (close_code={message.close_code})"
    return "Upstream websocket closed before response.completed"


def _websocket_receive_timeout_for_pending_requests(
    started_ats: Sequence[float],
    *,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
) -> _WebSocketReceiveTimeout | None:
    if not started_ats:
        return None

    idle_timeout_seconds = max(0.001, stream_idle_timeout_seconds)
    oldest_started_at = min(started_ats)
    budget_deadline = oldest_started_at + proxy_request_budget_seconds
    remaining_budget = _facade()._remaining_budget_seconds(budget_deadline)
    idle_timeout_matches_request_budget = idle_timeout_seconds == max(0.001, proxy_request_budget_seconds)

    if remaining_budget <= 0 and idle_timeout_matches_request_budget:
        return _WebSocketReceiveTimeout(
            timeout_seconds=0.0,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
        )
    if idle_timeout_matches_request_budget and remaining_budget >= idle_timeout_seconds:
        return _WebSocketReceiveTimeout(
            timeout_seconds=remaining_budget,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
        )
    if remaining_budget <= 0:
        return _WebSocketReceiveTimeout(
            timeout_seconds=0.0,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )
    if idle_timeout_seconds < remaining_budget:
        return _WebSocketReceiveTimeout(
            timeout_seconds=idle_timeout_seconds,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
            fail_all_pending=True,
        )
    return _WebSocketReceiveTimeout(
        timeout_seconds=remaining_budget,
        error_code="upstream_request_timeout",
        error_message="Proxy request budget exhausted",
    )


def _parse_websocket_payload(text: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _is_websocket_response_create(payload: dict[str, JsonValue]) -> bool:
    payload_type = payload.get("type")
    return isinstance(payload_type, str) and payload_type == "response.create"


def _app_error_to_websocket_event(exc: AppError) -> dict[str, JsonValue]:
    return _wrapped_websocket_error_event(
        exc.status_code,
        openai_error(exc.code, exc.message, error_type=getattr(exc, "error_type", "server_error")),
    )


def _wrapped_websocket_error_event(
    status_code: int,
    payload: OpenAIErrorEnvelope,
) -> dict[str, JsonValue]:
    error = payload["error"]
    error_code = _normalize_error_code(
        error.get("code"),
        error.get("type"),
    )
    error_param = error.get("param")
    error_message = error.get("message")
    if _facade()._is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        status_code = 502
        payload = previous_response_stream_incomplete_error()
    error_payload = cast(JsonValue, dict(payload["error"]))
    event: dict[str, JsonValue] = {
        "type": "error",
        "status": status_code,
        "error": error_payload,
    }
    return event


def _serialize_websocket_error_event(payload: dict[str, JsonValue]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _trim_websocket_previous_response_input_items(input_items: list[JsonValue]) -> list[JsonValue]:
    first_output_index = next(
        (
            index
            for index, item in enumerate(input_items)
            if _websocket_input_item_type(item)
            in {"function_call_output", "custom_tool_call_output", "apply_patch_call_output"}
        ),
        None,
    )
    if first_output_index is None or first_output_index == 0:
        return input_items
    prefix = input_items[:first_output_index]
    if not all(_is_websocket_previous_response_output_item(item) for item in prefix):
        return input_items
    return input_items[first_output_index:]


def _is_websocket_previous_response_output_item(item: JsonValue) -> bool:
    if isinstance(item, dict) and _websocket_input_item_type(item) is None and item.get("role") == "assistant":
        return True
    item_type = _websocket_input_item_type(item)
    if item_type in {"reasoning", "function_call", "custom_tool_call", "apply_patch_call"}:
        return True
    if item_type != "message" or not isinstance(item, dict):
        return False
    return item.get("role") == "assistant"


def _websocket_input_item_type(item: JsonValue) -> str | None:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    return item_type if isinstance(item_type, str) else None


def _websocket_connect_deadline(request_state: _WebSocketRequestState, budget_seconds: float) -> float:
    started_at = request_state.started_at if request_state.started_at > 0 else time.monotonic()
    return started_at + budget_seconds
