from __future__ import annotations

import json
import sys
from collections.abc import Callable
from copy import deepcopy
from typing import Any, AsyncIterator, Literal, Mapping, cast

from app.core.auth.refresh import (
    RefreshError,
)
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
    UpstreamResponsesWebSocket,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_CODE as PREVIOUS_RESPONSE_STALE_CODE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_MESSAGE as PREVIOUS_RESPONSE_STALE_MESSAGE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    response_failed_event,
)
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
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
    _REQUEST_TRANSPORT_WEBSOCKET,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _event_type_from_payload,
    _WebSocketRequestState,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeOwnerForward as _HTTPBridgeOwnerForward,
)
from app.modules.proxy._service.support import (
    _record_websocket_route_metadata as _record_websocket_route_metadata,
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
from app.modules.proxy._service.websocket.helpers import (
    _app_error_to_websocket_event,  # noqa: F401
    _assign_websocket_response_id,  # noqa: F401
    _draining_websocket_request_states,  # noqa: F401
    _find_websocket_request_state_by_response_id,  # noqa: F401
    _is_websocket_previous_response_output_item,  # noqa: F401
    _is_websocket_response_create,  # noqa: F401
    _match_websocket_request_state_for_anonymous_event,  # noqa: F401
    _match_websocket_request_state_for_precreated_terminal_event,  # noqa: F401
    _match_websocket_request_state_for_previous_response_error,  # noqa: F401
    _matching_websocket_request_states_for_missing_tool_output_error,  # noqa: F401
    _matching_websocket_request_states_for_previous_response_error,  # noqa: F401
    _maybe_rewrite_websocket_previous_response_not_found_event,  # noqa: F401
    _parse_websocket_payload,  # noqa: F401
    _pop_matching_websocket_request_states,  # noqa: F401
    _pop_replayable_precreated_websocket_request_state,  # noqa: F401
    _pop_terminal_websocket_request_state,  # noqa: F401
    _prepare_websocket_request_state_for_auth_replay,  # noqa: F401
    _prepare_websocket_request_state_for_visible_output_replay,  # noqa: F401
    _record_websocket_continuity_completion,  # noqa: F401
    _refresh_websocket_request_input_fingerprint_from_text,  # noqa: F401
    _release_websocket_response_create_gate,  # noqa: F401
    _rewrite_websocket_continuity_corruption_event,  # noqa: F401
    _rewrite_websocket_downstream_response_id,  # noqa: F401
    _rewrite_websocket_previous_response_owner_unavailable_event,  # noqa: F401
    _rewrite_websocket_suppressed_duplicate_tool_call_completion_event,  # noqa: F401
    _sanitize_websocket_connect_failure,  # noqa: F401
    _sanitize_websocket_previous_response_error,  # noqa: F401
    _sanitize_websocket_terminal_error_fields,  # noqa: F401
    _serialize_websocket_error_event,  # noqa: F401
    _trim_websocket_previous_response_input_items,  # noqa: F401
    _upstream_websocket_disconnect_message,  # noqa: F401
    _websocket_auth_failure_permanent_code,  # noqa: F401
    _websocket_auth_failure_requires_reauth,  # noqa: F401
    _websocket_auth_request_can_switch_account,  # noqa: F401
    _websocket_client_previous_response_full_resend_is_retry_safe,  # noqa: F401
    _websocket_connect_deadline,  # noqa: F401
    _websocket_continuity_anchor_for_payload,  # noqa: F401
    _websocket_continuity_error_fields,  # noqa: F401
    _websocket_continuity_response_ids,  # noqa: F401
    _websocket_downstream_response_id,  # noqa: F401
    _websocket_event_error_code,  # noqa: F401
    _websocket_event_error_message,  # noqa: F401
    _websocket_event_error_param,  # noqa: F401
    _websocket_event_error_payload,  # noqa: F401
    _websocket_event_error_type,  # noqa: F401
    _websocket_full_resend_conflicts_with_visible_pending,  # noqa: F401
    _websocket_input_item_type,  # noqa: F401
    _websocket_owner_pinned_quota_error_code,  # noqa: F401
    _websocket_precreated_auth_error_code,  # noqa: F401
    _websocket_precreated_retry_error_code,  # noqa: F401
    _websocket_receive_timeout_for_pending_requests,  # noqa: F401
    _websocket_response_id,  # noqa: F401
    _websocket_top_level_error_payload,  # noqa: F401
    _wrapped_websocket_error_event,  # noqa: F401
)
from app.modules.proxy.affinity import (
    _sticky_key_from_session_header,  # noqa: F401
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext as HTTPBridgeForwardContext,
)
from app.modules.proxy.http_bridge_forwarding import (
    OwnerForwardRelayFailure as OwnerForwardRelayFailure,
)


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


_REQUEST_TRANSPORT_HTTP = "http"


def _resolve_upstream_stream_transport(upstream_stream_transport: str) -> str | None:
    if upstream_stream_transport == "default":
        return None
    return upstream_stream_transport


def _should_penalize_stream_error(code: str | None) -> bool:
    if code is None:
        return False
    return code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES or code in _facade()._TRANSIENT_RETRY_CODES


def _should_retry_transient_stream_error(code: str | None, message: str | None) -> bool:
    if code is None or code == "stream_idle_timeout":
        return False
    if code in _facade()._TRANSIENT_RETRY_CODES:
        return True
    if code != "upstream_unavailable" or not message:
        return False
    normalized_message = message.lower()
    if any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_NON_TRANSIENT_MESSAGE_MARKERS):
        return False
    return any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_TRANSIENT_MESSAGE_MARKERS)


def _refresh_upstream_proxy_fail_closed_reason(exc: RefreshError) -> str | None:
    if exc.code != "upstream_proxy_unavailable":
        return None
    reason = exc.upstream_proxy_fail_closed_reason
    if reason:
        return reason
    marker = "Upstream proxy route unavailable:"
    if exc.message.startswith(marker):
        parsed = exc.message.removeprefix(marker).strip()
        return parsed or "unavailable"
    return "unavailable"


def _classify_upstream_close(
    close_code: int | None,
    *,
    response_events_seen: int,
) -> Literal["transient", "rejected"]:
    if close_code == 1000 and response_events_seen == 0:
        return "rejected"
    return "transient"


def _should_infer_upstream_status_from_proxy_error(exc: ProxyResponseError, upstream_error_code: str | None) -> bool:
    if exc.failure_phase == "status":
        return True
    if exc.failure_phase is not None:
        return False
    return upstream_error_code not in _facade()._LOCAL_PROXY_ERROR_CODES


def _rewrite_previous_response_stream_error(
    *,
    previous_response_id: str | None,
    preferred_account_id: str | None,
    error_code: str | None,
    error_type: str | None,
    error_message: str | None,
    error_param: str | None,
) -> tuple[str, str, str | None] | None:
    if previous_response_id is None:
        return None
    if _facade()._is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="previous_response_not_found",
            previous_response_id=previous_response_id,
            upstream_error_code=error_code,
        )
        return (
            "stream_incomplete",
            PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
            None,
        )
    if _facade()._is_missing_tool_output_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="missing_tool_output",
            previous_response_id=previous_response_id,
            upstream_error_code=error_code,
        )
        return (
            "stream_incomplete",
            PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
            None,
        )
    normalized_code = _normalize_error_code(error_code, error_type)
    if preferred_account_id is not None and normalized_code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES:
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="owner_account_unavailable",
            previous_response_id=previous_response_id,
            upstream_error_code=normalized_code,
        )
        return (
            "previous_response_owner_unavailable",
            "Previous response owner account is unavailable; retry later.",
            normalized_code,
        )
    return None


def _build_rewritten_stream_response_failed_event(
    *,
    response_id: str,
    error_code: str,
    error_message: str,
) -> tuple[str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    rewritten_event_payload = response_failed_event(
        error_code,
        error_message,
        error_type="server_error",
        response_id=response_id,
    )
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event_block, rewritten_event, rewritten_payload, rewritten_event_type


def _build_stream_incomplete_terminal_event_for_request(
    request_state: _WebSocketRequestState,
    *,
    reason: str = "stream_incomplete",
) -> tuple[str, str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    error_code, error_message = _websocket_continuity_error_fields(
        reason=reason,
        expose_stale_previous_response_classifier=request_state.expose_stale_previous_response_classifier,
    )
    event_block, event, payload, event_type = _build_rewritten_stream_response_failed_event(
        response_id=_websocket_downstream_response_id(request_state),
        error_code=error_code,
        error_message=error_message,
    )
    downstream_text = json.dumps(
        cast(
            dict[str, JsonValue],
            response_failed_event(
                error_code,
                error_message,
                error_type="server_error",
                response_id=_websocket_downstream_response_id(request_state),
            ),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return downstream_text, event_block, event, payload, event_type


def _slim_response_create_payload_for_upstream(
    payload: dict[str, JsonValue],
    *,
    max_bytes: int,
) -> tuple[dict[str, JsonValue], dict[str, int] | None]:
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _facade()._response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        (
            slimmed_item,
            item_tool_outputs_slimmed,
            item_images_slimmed,
        ) = _facade()._slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _call_stream_with_supported_optional_kwargs(
    func: Callable[..., AsyncIterator[str]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> AsyncIterator[str]:
    return func(*args, **_facade()._supported_optional_kwargs(func, optional_kwargs, required_kwargs))


def _stream_request_budget_seconds(settings: object, *, request_transport: str) -> float:
    if request_transport == _REQUEST_TRANSPORT_HTTP:
        budget = getattr(settings, "http_responses_stream_request_budget_seconds", None)
        if budget is not None:
            return float(budget)
    return float(getattr(settings, "proxy_request_budget_seconds"))


def _push_stream_attempt_timeout_overrides(
    timeout_seconds: float,
) -> tuple[float | None, float | None, float | None]:
    return _facade().push_stream_timeout_overrides(
        connect_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=timeout_seconds,
        total_timeout_seconds=timeout_seconds,
    )


def _should_retry_stream_error(code: str) -> bool:
    return code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES


def _upstream_turn_state_from_socket(upstream: UpstreamResponsesWebSocket | None) -> str | None:
    if upstream is None:
        return None
    getter = getattr(upstream, "response_header", None)
    if not callable(getter):
        return None
    value = getter("x-codex-turn-state")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
