from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import deque
from typing import Any, Mapping, NoReturn, cast

import aiohttp
import anyio
from fastapi import WebSocket
from pydantic import ValidationError

from app.core.auth.refresh import (
    RefreshError,
)
from app.core.balancer import (
    ResetPreferenceWindow,
    RoutingStrategy,
    failover_decision,
)
from app.core.balancer.types import ClassifiedFailure, UpstreamError
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
    filter_inbound_websocket_headers,
)
from app.core.errors import (
    OpenAIErrorEnvelope,
    openai_error,
    response_failed_event,
)
from app.core.exceptions import AppError, ProxyAuthError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME as CODEX_KEEPALIVE_FRAME  # noqa: F401
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow as utcnow
from app.db.models import (
    Account,
    AccountStatus,  # noqa: F401
    StickySessionKind,
)
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeysService,
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
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_request_error_overrides,
    _DownstreamWebSocketActivity,
    _event_type_from_payload,
    _PreparedWebSocketRequest,
    _record_response_event,
    _record_websocket_route_metadata,
    _request_log_useragent_fields,
    _stream_settlement_error_payload,
    _StreamSettlement,
    _wait_for_websocket_continuity_gap,
    _websocket_full_replay_should_wait_for_continuity,
    _WebSocketConnectFailureEmitted,
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
from app.modules.proxy._service.websocket.helpers import (
    _app_error_to_websocket_event,
    _assign_websocket_response_id,
    _find_websocket_request_state_by_response_id,
    _is_websocket_response_create,
    _match_websocket_request_state_for_anonymous_event,
    _matching_websocket_request_states_for_missing_tool_output_error,
    _matching_websocket_request_states_for_previous_response_error,
    _maybe_rewrite_websocket_previous_response_not_found_event,
    _parse_websocket_payload,
    _pop_matching_websocket_request_states,
    _pop_replayable_precreated_websocket_request_state,
    _pop_terminal_websocket_request_state,
    _prepare_websocket_request_state_for_auth_replay,
    _record_websocket_continuity_completion,
    _release_websocket_response_create_gate,
    _rewrite_websocket_continuity_corruption_event,
    _rewrite_websocket_downstream_response_id,
    _rewrite_websocket_previous_response_owner_unavailable_event,
    _rewrite_websocket_suppressed_duplicate_tool_call_completion_event,
    _sanitize_websocket_connect_failure,
    _sanitize_websocket_previous_response_error,
    _sanitize_websocket_terminal_error_fields,
    _serialize_websocket_error_event,
    _trim_websocket_previous_response_input_items,
    _upstream_websocket_disconnect_message,
    _websocket_auth_failure_requires_reauth,
    _websocket_client_previous_response_full_resend_is_retry_safe,
    _websocket_connect_deadline,
    _websocket_continuity_anchor_for_payload,
    _websocket_continuity_error_fields,
    _websocket_continuity_response_ids,
    _websocket_downstream_response_id,
    _websocket_event_error_code,
    _websocket_event_error_message,
    _websocket_event_error_param,
    _websocket_event_error_type,
    _websocket_full_resend_conflicts_with_visible_pending,
    _websocket_precreated_auth_error_code,
    _websocket_precreated_retry_error_code,
    _websocket_receive_timeout_for_pending_requests,
    _websocket_response_id,
    _wrapped_websocket_error_event,
)
from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,  # noqa: F401
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _header_account_id,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext as HTTPBridgeForwardContext,
)
from app.modules.proxy.http_bridge_forwarding import (
    OwnerForwardRelayFailure as OwnerForwardRelayFailure,
)
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    normalize_responses_request_payload,
    openai_client_payload_error,
    openai_invalid_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.tool_call_dedupe import (
    mark_duplicate_tool_call_downstream_event,
    rewrite_parallel_tool_call_text,
)
from app.modules.proxy.tool_call_dedupe import (
    response_id_from_payload as tool_call_response_id_from_payload,
)


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


def _raise_proxy_budget_exhausted() -> NoReturn:
    _facade()._raise_proxy_budget_exhausted()
    raise AssertionError("proxy budget exhaustion helper returned")


class _WebSocketMixin:
    def _websocket_continuity_state_for_request(
        self,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        codex_session_affinity: bool,
    ) -> "_WebSocketContinuityState":
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if not codex_session_affinity:
            return _WebSocketContinuityState()
        session_id = _owner_lookup_session_id_from_headers(headers)
        if session_id is None:
            return _WebSocketContinuityState()
        key = (session_id, api_key.id if api_key is not None else None)
        continuity_state = proxy._websocket_continuity_index.get(key)
        if continuity_state is None:
            continuity_state = _WebSocketContinuityState()
            proxy._websocket_continuity_index[key] = continuity_state
        else:
            proxy._websocket_continuity_index.pop(key, None)
            proxy._websocket_continuity_index[key] = continuity_state
        while len(proxy._websocket_continuity_index) > _facade()._WEBSOCKET_CONTINUITY_CACHE_LIMIT:
            proxy._websocket_continuity_index.pop(next(iter(proxy._websocket_continuity_index)))
        return continuity_state

    async def proxy_responses_websocket(
        self,
        websocket: WebSocket,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        filtered_headers = filter_inbound_websocket_headers(dict(headers))
        useragent, useragent_group = _request_log_useragent_fields(headers)
        runtime_settings = _facade().get_settings()
        settings = await _facade().get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = settings.sticky_threads_enabled
        openai_cache_affinity_max_age_seconds = settings.openai_cache_affinity_max_age_seconds
        routing_strategy = _facade()._routing_strategy(settings)
        pending_requests: deque[_WebSocketRequestState] = deque()
        pending_lock = anyio.Lock()
        client_send_lock = anyio.Lock()
        response_create_gate = asyncio.Semaphore(1)
        upstream: UpstreamResponsesWebSocket | None = None
        upstream_reader: asyncio.Task[None] | None = None
        upstream_control: _WebSocketUpstreamControl | None = None
        continuity_state = proxy._websocket_continuity_state_for_request(
            headers,
            api_key=api_key,
            codex_session_affinity=codex_session_affinity,
        )
        account: Account | None = None
        account_lease: AccountLease | None = None
        upstream_turn_state: str | None = _sticky_key_from_turn_state_header(headers)
        downstream_activity = _DownstreamWebSocketActivity()
        replay_request_state: _WebSocketRequestState | None = None

        async def release_current_account_lease() -> None:
            nonlocal account_lease
            await proxy._load_balancer.release_account_lease(account_lease)
            account_lease = None

        try:
            while True:
                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        pass
                    if replay_request_state is None and upstream_control is not None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    await release_current_account_lease()
                    account = None

                text_data: str | None = None
                bytes_data: bytes | None = None
                request_state: _WebSocketRequestState | None = None
                request_state_registered = False
                request_affinity = _AffinityPolicy()
                payload: dict[str, JsonValue] | None = None

                if replay_request_state is not None:
                    request_state = replay_request_state
                    replay_request_state = None
                    request_affinity = request_state.affinity_policy
                    text_data = request_state.request_text
                    if text_data is None:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code="stream_incomplete",
                            error_message="Upstream websocket closed before response.completed",
                            error_type="server_error",
                            downstream_activity=downstream_activity,
                        )
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    payload = _parse_websocket_payload(text_data)
                    if payload is None:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code="upstream_error",
                            error_message="Invalid replay request payload",
                            error_type="server_error",
                            downstream_activity=downstream_activity,
                        )
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    async with pending_lock:
                        pending_requests.append(request_state)
                    proxy._start_request_state_api_key_reservation_heartbeat(
                        request_state,
                        api_key=request_state.api_key or api_key,
                        surface="websocket",
                    )
                    request_state_registered = True
                else:
                    downstream_idle_timeout_seconds = runtime_settings.proxy_downstream_websocket_idle_timeout_seconds
                    message: Any | None = None
                    try:
                        message = await asyncio.wait_for(
                            websocket.receive(),
                            timeout=min(
                                downstream_idle_timeout_seconds, _facade()._DOWNSTREAM_WEBSOCKET_RECEIVE_POLL_SECONDS
                            ),
                        )
                    except asyncio.TimeoutError:
                        if not await proxy._downstream_websocket_is_idle(
                            pending_requests,
                            pending_lock=pending_lock,
                            downstream_activity=downstream_activity,
                            idle_timeout_seconds=downstream_idle_timeout_seconds,
                        ):
                            continue
                        idle_close = False
                        async with client_send_lock:
                            if await proxy._downstream_websocket_is_idle(
                                pending_requests,
                                pending_lock=pending_lock,
                                downstream_activity=downstream_activity,
                                idle_timeout_seconds=downstream_idle_timeout_seconds,
                            ):
                                try:
                                    message = await asyncio.wait_for(websocket.receive(), timeout=0.05)
                                except asyncio.TimeoutError:
                                    try:
                                        await websocket.close(
                                            code=1001, reason=_facade()._DOWNSTREAM_WEBSOCKET_IDLE_CLOSE_REASON
                                        )
                                    except Exception:
                                        _facade().logger.debug(
                                            "Failed to close idle downstream websocket", exc_info=True
                                        )
                                    idle_close = True
                        if idle_close:
                            break
                    assert message is not None
                    downstream_activity.mark()
                    message_type = message["type"]

                    if message_type == "websocket.disconnect":
                        downstream_activity.mark_disconnected()
                        break
                    if message_type != "websocket.receive":
                        continue

                    text_data = message.get("text")
                    bytes_data = message.get("bytes")

                    if text_data is not None:
                        payload = _parse_websocket_payload(text_data)
                        if payload is not None and _is_websocket_response_create(payload):
                            try:
                                prepared_request = await proxy._prepare_websocket_response_create_request(
                                    payload,
                                    headers=headers,
                                    codex_session_affinity=codex_session_affinity,
                                    openai_cache_affinity=openai_cache_affinity,
                                    sticky_threads_enabled=sticky_threads_enabled,
                                    openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                                    api_key=api_key,
                                    continuity_state=continuity_state,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                )
                                if await _websocket_full_replay_should_wait_for_continuity(
                                    prepared_request.request_state,
                                    pending_requests,
                                    pending_lock=pending_lock,
                                    codex_session_affinity=codex_session_affinity,
                                ):
                                    await proxy._release_websocket_request_state_reservation(
                                        prepared_request.request_state
                                    )
                                    wait_started_at = time.monotonic()
                                    waited_for_anchor = await _wait_for_websocket_continuity_gap(
                                        pending_requests,
                                        pending_lock=pending_lock,
                                        timeout_seconds=runtime_settings.proxy_request_budget_seconds,
                                    )
                                    _facade().logger.info(
                                        "websocket_full_replay_waited_for_continuity waited=%s elapsed_ms=%s "
                                        "original_items=%s",
                                        waited_for_anchor,
                                        int((time.monotonic() - wait_started_at) * 1000),
                                        prepared_request.request_state.input_item_count,
                                    )
                                    prepared_request = await proxy._prepare_websocket_response_create_request(
                                        payload,
                                        headers=headers,
                                        codex_session_affinity=codex_session_affinity,
                                        openai_cache_affinity=openai_cache_affinity,
                                        sticky_threads_enabled=sticky_threads_enabled,
                                        openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                                        api_key=api_key,
                                        continuity_state=continuity_state,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                    )
                                request_state = prepared_request.request_state
                                request_affinity = prepared_request.affinity_policy
                                text_data = prepared_request.text_data
                            except ProxyResponseError as exc:
                                (
                                    status_code,
                                    error_payload,
                                    _error_code,
                                    _error_message,
                                ) = _sanitize_websocket_previous_response_error(
                                    previous_response_id=_facade()._previous_response_id_from_payload(payload),
                                    session_id=_owner_lookup_session_id_from_headers(headers),
                                    status_code=exc.status_code,
                                    payload=exc.payload,
                                    error_code="upstream_error",
                                    error_message="Upstream error",
                                    surface="websocket_connect",
                                    expose_stale_previous_response_classifier=codex_session_affinity,
                                )
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(status_code, error_payload)
                                        )
                                    )
                                continue
                            except AppError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(_app_error_to_websocket_event(exc))
                                    )
                                continue
                            except ClientPayloadError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(400, openai_client_payload_error(exc))
                                        )
                                    )
                                continue
                            except ValidationError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(400, openai_validation_error(exc))
                                        )
                                    )
                                continue

                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        pass
                    if replay_request_state is None and upstream_control is not None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    await release_current_account_lease()
                    account = None

                if (
                    request_state is not None
                    and upstream_control is not None
                    and upstream_control.reconnect_requested
                    and upstream_reader is not None
                ):
                    await upstream_reader
                    if replay_request_state is None:
                        replay_request_state = upstream_control.replay_request_state
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    await release_current_account_lease()
                    account = None

                if (
                    request_state is not None
                    and request_state.previous_response_id is not None
                    and request_state.preferred_account_id is None
                ):
                    try:
                        request_state.preferred_account_id = await proxy._resolve_websocket_previous_response_owner(
                            previous_response_id=request_state.previous_response_id,
                            api_key=request_state.api_key or api_key,
                            session_id=request_state.session_id,
                            surface="websocket",
                        )
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message else "Upstream error"
                        error_type = error.type if error and error.type else "server_error"
                        error_param = error.param if error else None
                        await proxy._release_websocket_request_state_reservation(request_state)
                        await proxy._write_websocket_connect_failure(
                            account_id=None,
                            api_key=api_key,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                        )
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            error_param=error_param,
                            downstream_activity=downstream_activity,
                        )
                        request_state = None
                        text_data = None
                        payload = None
                        continue

                if request_state is not None and await _websocket_full_resend_conflicts_with_visible_pending(
                    request_state,
                    pending_requests,
                    pending_lock=pending_lock,
                    codex_session_affinity=codex_session_affinity,
                ):
                    _facade().logger.warning(
                        "Rejecting websocket full resend while prior response is visible request_id=%s input_items=%s",
                        request_state.request_log_id or request_state.request_id,
                        request_state.input_item_count,
                    )
                    await proxy._release_websocket_request_state_reservation(request_state)
                    await proxy._emit_websocket_terminal_error(
                        websocket,
                        client_send_lock=client_send_lock,
                        request_state=request_state,
                        error_code="stream_incomplete",
                        error_message="Previous response is still streaming; retry after the terminal frame",
                        error_type="server_error",
                        downstream_activity=downstream_activity,
                    )
                    request_state = None
                    text_data = None
                    payload = None
                    continue

                if request_state is not None and not request_state_registered:
                    try:
                        proxy._start_request_state_api_key_reservation_heartbeat(
                            request_state,
                            api_key=request_state.api_key or api_key,
                            surface="websocket",
                        )
                        await proxy._acquire_request_state_response_create_admission(
                            request_state,
                            response_create_gate=response_create_gate,
                        )
                        async with pending_lock:
                            pending_requests.append(request_state)
                        request_state_registered = True
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message else "Upstream error"
                        error_type = error.type if error and error.type else "server_error"
                        error_param = error.param if error else None
                        await proxy._release_websocket_request_state_reservation(request_state)
                        await proxy._write_websocket_connect_failure(
                            account_id=account.id if account else None,
                            api_key=api_key,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                        )
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            error_param=error_param,
                            downstream_activity=downstream_activity,
                        )
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    except asyncio.CancelledError:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        raise
                    except Exception:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        raise

                if upstream is None:
                    if text_data is not None and payload is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(400, openai_invalid_payload_error())
                                )
                            )
                        continue
                    if request_state is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(
                                        400,
                                        openai_error(
                                            "invalid_request_error",
                                            "WebSocket connection has no active upstream session",
                                            error_type="invalid_request_error",
                                        ),
                                    )
                                )
                            )
                        continue
                    connect_headers = _facade()._headers_with_turn_state(filtered_headers, upstream_turn_state)
                    account, upstream = await proxy._connect_proxy_websocket(
                        connect_headers,
                        sticky_key=request_affinity.key,
                        sticky_kind=request_affinity.kind,
                        reallocate_sticky=request_affinity.reallocate_sticky,
                        sticky_max_age_seconds=request_affinity.max_age_seconds,
                        prefer_earlier_reset=prefer_earlier_reset,
                        prefer_earlier_reset_window=_facade()._prefer_earlier_reset_window(settings),
                        routing_strategy=routing_strategy,
                        model=request_state.model,
                        request_state=request_state,
                        api_key=api_key,
                        client_send_lock=client_send_lock,
                        websocket=websocket,
                    )
                    if upstream is None or account is None:
                        proxy._cancel_request_state_api_key_reservation_heartbeat(request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                            await _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    account_lease = request_state.websocket_stream_lease
                    request_state.websocket_stream_lease = None
                    upstream_turn_state = _facade()._upstream_turn_state_from_socket(upstream) or upstream_turn_state
                    upstream_control = _WebSocketUpstreamControl()
                    upstream_reader = asyncio.create_task(
                        proxy._relay_upstream_websocket_messages(
                            websocket,
                            upstream,
                            account=account,
                            account_id_value=account.id,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            client_send_lock=client_send_lock,
                            api_key=api_key,
                            upstream_control=upstream_control,
                            response_create_gate=response_create_gate,
                            continuity_state=continuity_state,
                            proxy_request_budget_seconds=runtime_settings.proxy_request_budget_seconds,
                            stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                            downstream_activity=downstream_activity,
                            codex_session_affinity=codex_session_affinity,
                        )
                    )

                try:
                    if (
                        text_data is not None
                        and request_state is not None
                        and payload is not None
                        and account is not None
                        and _is_websocket_response_create(payload)
                        and request_state.account_response_create_lease is None
                    ):
                        request_state.account_response_create_lease = (
                            await proxy._acquire_account_response_create_lease_or_overload(
                                account_id=account.id,
                                request_id=request_state.request_log_id or request_state.request_id,
                                surface="websocket",
                            )
                        )
                        request_state.account_response_create_release = proxy._load_balancer.release_account_lease
                    if text_data is not None:
                        await upstream.send_text(text_data)
                    elif bytes_data is not None:
                        await upstream.send_bytes(bytes_data)
                except ProxyResponseError as exc:
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error and error.message else "Upstream error"
                    error_type = error.type if error and error.type else "server_error"
                    if request_state is not None:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                            await _release_websocket_response_create_gate(request_state, response_create_gate)
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            error_param=error.param if error else None,
                            downstream_activity=downstream_activity,
                        )
                    continue
                except Exception:
                    replay_candidate = await _pop_replayable_precreated_websocket_request_state(
                        pending_requests,
                        pending_lock=pending_lock,
                    )
                    if replay_candidate is not None:
                        _facade().logger.info(
                            "Transparent websocket replay after upstream send failure request_id=%s",
                            replay_candidate.request_log_id or replay_candidate.request_id,
                        )
                        replay_request_state = replay_candidate
                        if upstream_reader is not None:
                            await _facade()._await_cancelled_task(
                                upstream_reader, label="proxy websocket upstream reader"
                            )
                            upstream_reader = None
                        upstream_control = None
                        if upstream is not None:
                            try:
                                await upstream.close()
                            except Exception:
                                _facade().logger.debug(
                                    "Failed to close upstream websocket after replayable send failure",
                                    exc_info=True,
                                )
                        upstream = None
                        await release_current_account_lease()
                        account = None
                        continue
                    await proxy._fail_pending_websocket_requests(
                        account=account,
                        account_id_value=account.id if account else None,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        error_code="stream_incomplete",
                        error_message="Upstream websocket closed before response.completed",
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                        downstream_activity=downstream_activity,
                    )
                    if upstream_reader is not None:
                        await _facade()._await_cancelled_task(upstream_reader, label="proxy websocket upstream reader")
                        upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug(
                                "Failed to close upstream websocket after send failure", exc_info=True
                            )
                    upstream = None
                    await release_current_account_lease()
                    account = None
                    continue
        finally:
            if upstream_reader is not None:
                await _facade()._await_cancelled_task(upstream_reader, label="proxy websocket upstream reader")
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    _facade().logger.debug("Failed to close upstream websocket", exc_info=True)
            await release_current_account_lease()
            if replay_request_state is not None:
                await proxy._release_websocket_request_state_reservation(replay_request_state)
                replay_request_state.api_key_reservation = None
                await _release_websocket_response_create_gate(replay_request_state, response_create_gate)
            client_disconnected = downstream_activity.disconnected
            await proxy._fail_pending_websocket_requests(
                account=None if client_disconnected else account,
                account_id_value=account.id if account else None,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="client_disconnected" if client_disconnected else "stream_incomplete",
                error_message=(
                    "Downstream websocket disconnected before response.completed"
                    if client_disconnected
                    else "Upstream websocket closed before response.completed"
                ),
                api_key=api_key,
                websocket=None if client_disconnected else websocket,
                client_send_lock=None if client_disconnected else client_send_lock,
                response_create_gate=response_create_gate,
                downstream_activity=downstream_activity,
                status="cancelled" if client_disconnected else "error",
                penalize_account=not client_disconnected,
            )

    async def _prepare_websocket_response_create_request(
        self,
        payload: dict[str, JsonValue],
        *,
        headers: Mapping[str, str],
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        sticky_threads_enabled: bool,
        openai_cache_affinity_max_age_seconds: int,
        api_key: ApiKeyData | None,
        continuity_state: "_WebSocketContinuityState | None" = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
    ) -> _PreparedWebSocketRequest:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        refreshed_api_key = await proxy._refresh_websocket_api_key_policy(api_key)
        client_metadata = _facade()._response_create_client_metadata(payload, headers=headers)
        responses_payload = normalize_responses_request_payload(
            payload,
            openai_compat=openai_cache_affinity,
        )
        previous_response_trimmed_input_count: int | None = None
        previous_response_trimmed_input_fingerprint: str | None = None
        client_full_resend_payload: ResponsesRequest | None = None
        client_full_resend_input_items: list[JsonValue] | None = None
        client_full_resend_retry_safe = False
        if responses_payload.previous_response_id is not None and isinstance(responses_payload.input, list):
            previous_response_input_items = cast(list[JsonValue], responses_payload.input)
            client_full_resend_input_items = previous_response_input_items
            client_full_resend_retry_safe = _websocket_client_previous_response_full_resend_is_retry_safe(
                previous_response_id=responses_payload.previous_response_id,
                input_value=responses_payload.input,
                continuity_state=continuity_state,
            )
            trimmed_input_items = _trim_websocket_previous_response_input_items(previous_response_input_items)
            if len(trimmed_input_items) != len(previous_response_input_items):
                previous_response_trimmed_input_count = len(previous_response_input_items)
                previous_response_trimmed_input_fingerprint = _facade()._fingerprint_input_items(
                    previous_response_input_items
                )
                responses_payload = responses_payload.model_copy(update={"input": trimmed_input_items})
        apply_api_key_enforcement(responses_payload, refreshed_api_key)
        if client_full_resend_retry_safe and client_full_resend_input_items is not None:
            client_full_resend_payload = responses_payload.model_copy(
                update={
                    "previous_response_id": None,
                    "input": client_full_resend_input_items,
                }
            )
        validate_model_access(refreshed_api_key, responses_payload.model)
        proxy._raise_for_unsupported_input_image_references(responses_payload)
        rewritten_file_account_id = await proxy._resolve_file_account_for_responses(responses_payload, headers)
        original_full_resend_payload: ResponsesRequest | None = None
        original_input_item_count: int | None = None
        original_input_fingerprint: str | None = None
        session_anchor = _websocket_continuity_anchor_for_payload(
            continuity_state,
            responses_payload=responses_payload,
            codex_session_affinity=codex_session_affinity,
        )
        if session_anchor is not None:
            original_input_items = cast(list[JsonValue], responses_payload.input)
            original_input_item_count = len(original_input_items)
            original_input_fingerprint = _facade()._fingerprint_input_items(original_input_items)
            original_full_resend_payload = responses_payload
            responses_payload = responses_payload.model_copy(
                update={
                    "previous_response_id": session_anchor.previous_response_id,
                    "input": original_input_items[session_anchor.stored_input_item_count :],
                }
            )
        if (
            continuity_state is not None
            and responses_payload.previous_response_id is not None
            and responses_payload.previous_response_id == continuity_state.last_completed_response_id
            and continuity_state.last_pending_function_call_ids
            and isinstance(responses_payload.input, list)
        ):
            input_items = cast(list[JsonValue], responses_payload.input)
            missing_call_ids = _facade()._missing_function_call_outputs_for_previous_response(
                input_items,
                pending_call_ids=continuity_state.last_pending_function_call_ids,
            )
            if missing_call_ids:
                responses_payload = responses_payload.model_copy(
                    update={
                        "input": _facade()._inject_missing_interrupted_function_call_outputs(
                            input_items,
                            missing_call_ids=missing_call_ids,
                        )
                    }
                )
                _facade().logger.warning(
                    "websocket_interrupted_tool_outputs_injected previous_response_id=%s missing_call_count=%s",
                    responses_payload.previous_response_id,
                    len(missing_call_ids),
                )
        reservation = await proxy._reserve_websocket_api_key_usage(
            refreshed_api_key,
            request_model=responses_payload.model,
            request_service_tier=_facade()._normalize_service_tier_value(
                dict(responses_payload.to_payload()).get("service_tier")
            ),
            request_usage_budget=estimate_api_key_request_usage(responses_payload),
        )
        try:
            session_id = _owner_lookup_session_id_from_headers(headers)
            request_state, text_data = proxy._prepare_response_bridge_request_state(
                responses_payload,
                api_key=refreshed_api_key,
                api_key_reservation=reservation,
                include_type_field=True,
                attach_event_queue=False,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
                client_metadata=client_metadata,
                session_id=session_id,
            )
        except ProxyResponseError:
            await proxy._release_websocket_reservation(reservation)
            raise
        request_state.useragent = useragent
        request_state.useragent_group = useragent_group
        request_state.expose_stale_previous_response_classifier = codex_session_affinity
        if session_anchor is not None:
            request_state.proxy_injected_previous_response_id = True
            request_state.input_item_count = original_input_item_count or request_state.input_item_count
            request_state.input_full_fingerprint = original_input_fingerprint
            if original_full_resend_payload is not None:
                request_state.fresh_upstream_request_text = _facade()._response_create_text_with_size_guard(
                    original_full_resend_payload,
                    include_type_field=True,
                    client_metadata=client_metadata,
                    request_state=request_state,
                    transport=_REQUEST_TRANSPORT_WEBSOCKET,
                )
            request_state.fresh_upstream_request_is_retry_safe = request_state.fresh_upstream_request_text is not None
            _facade().logger.info(
                "websocket_session_anchor_injected request_id=%s response_id=%s original_items=%s trimmed_to=%s",
                request_state.request_id,
                session_anchor.previous_response_id,
                original_input_item_count,
                len(cast(list[JsonValue], responses_payload.input))
                if isinstance(responses_payload.input, list)
                else None,
            )
        had_prompt_cache_key = _prompt_cache_key_from_request_model(responses_payload) is not None
        if previous_response_trimmed_input_count is not None:
            request_state.input_item_count = previous_response_trimmed_input_count
            request_state.input_full_fingerprint = previous_response_trimmed_input_fingerprint
            _facade().logger.info(
                "websocket_previous_response_input_trimmed request_id=%s original_items=%s trimmed_to=%s "
                "previous_response_id=%s",
                request_state.request_id,
                previous_response_trimmed_input_count,
                len(cast(list[JsonValue], responses_payload.input))
                if isinstance(responses_payload.input, list)
                else None,
                responses_payload.previous_response_id,
            )
        if client_full_resend_payload is not None and not request_state.proxy_injected_previous_response_id:
            request_state.fresh_upstream_request_text = _facade()._response_create_text_with_size_guard(
                client_full_resend_payload,
                include_type_field=True,
                client_metadata=client_metadata,
                request_state=request_state,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
            )
            request_state.fresh_upstream_request_is_retry_safe = request_state.fresh_upstream_request_text is not None
            if request_state.fresh_upstream_request_is_retry_safe:
                _facade().logger.info(
                    (
                        "websocket_client_previous_response_full_resend_retry_prepared request_id=%s "
                        "previous_response_id=%s input_items=%s"
                    ),
                    request_state.request_id,
                    responses_payload.previous_response_id,
                    request_state.input_item_count,
                )
        affinity_policy = _sticky_key_for_responses_request(
            responses_payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=sticky_threads_enabled,
            api_key=api_key,
        )
        sticky_key_source = "none"
        if affinity_policy.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = (
                "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
            )
        elif affinity_policy.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "websocket",
            responses_payload,
            headers,
            sticky_kind=affinity_policy.kind.value if affinity_policy.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(responses_payload) is not None,
        )
        request_state.affinity_policy = affinity_policy

        # First-turn ``input_file.file_id`` references must land on the
        # account that registered the upload (chatgpt-account-id-scoped).
        # Codex CLI's typical flow is upload-then-converse, so a fresh
        # turn often references a file_id with no other affinity signal
        # set. The helper short-circuits to ``None`` when stronger
        # affinity signals (prompt_cache_key / session header /
        # turn_state header / previous_response_id) are present, so this
        # never overrides existing routing.
        if request_state.preferred_account_id is None:
            request_state.preferred_account_id = rewritten_file_account_id
            request_state.file_required_preferred_account = request_state.preferred_account_id is not None
        if request_state.preferred_account_id is None:
            request_state.preferred_account_id = await proxy._resolve_file_account_for_responses(
                responses_payload, headers
            )
            request_state.file_required_preferred_account = request_state.preferred_account_id is not None

        # Direct WebSocket retry-safety classification.
        #
        # The single-previous-response-miss masking path in
        # ``_process_upstream_websocket_text`` only attempts a transparent
        # reconnect-and-replay for a turn marked
        # ``fresh_upstream_request_is_retry_safe`` with a captured
        # ``fresh_upstream_request_text``. Without these flags, even a
        # full-resend turn whose semantic payload does not depend on the
        # upstream anchor (no client-supplied ``previous_response_id`` and no
        # proxy-injected anchor) would fall through to ``stream_incomplete``
        # instead of being recovered. That regresses the recovery behavior
        # this PR is explicitly trying to preserve for full-resend variants.
        #
        # The HTTP-bridge path sets these flags at request prep time; mirror
        # the same classification here for the direct WebSocket path so the
        # mask in the reception path treats both variants identically.
        if responses_payload.previous_response_id is None and not request_state.proxy_injected_previous_response_id:
            request_state.fresh_upstream_request_text = text_data
            request_state.fresh_upstream_request_is_retry_safe = True

        return _PreparedWebSocketRequest(
            text_data=text_data,
            request_state=request_state,
            affinity_policy=affinity_policy,
        )

    async def _connect_proxy_websocket(
        self,
        headers: dict[str, str],
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        prefer_earlier_reset: bool,
        routing_strategy: RoutingStrategy,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
        model: str | None,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
    ) -> tuple[Account | None, UpstreamResponsesWebSocket | None]:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if request_state.useragent is None and request_state.useragent_group is None:
            request_state.useragent, request_state.useragent_group = _request_log_useragent_fields(headers)
        deadline = _websocket_connect_deadline(request_state, _facade().get_settings().proxy_request_budget_seconds)
        base_settings = _facade().get_settings()
        max_attempts = _facade()._WEBSOCKET_MAX_ACCOUNT_ATTEMPTS
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        last_failover_exc: ProxyResponseError | None = None
        last_failover_account: Account | None = None
        for attempt in range(max_attempts):
            is_retry = attempt > 0
            forced_refresh_account_id = request_state.force_refresh_account_id
            preferred_account_id = forced_refresh_account_id or request_state.preferred_account_id
            require_preferred_account = (
                request_state.previous_response_id is not None and request_state.preferred_account_id is not None
            ) or request_state.file_required_preferred_account
            try:
                account = await proxy._select_websocket_connect_account(
                    deadline,
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    prefer_earlier_reset=prefer_earlier_reset,
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    model=model,
                    request_state=request_state,
                    api_key=api_key,
                    client_send_lock=client_send_lock,
                    websocket=websocket,
                    reallocate_sticky=True if is_retry else reallocate_sticky,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    exclude_account_ids=excluded_account_ids,
                    preferred_account_id=preferred_account_id,
                    require_security_work_authorized=request_state.require_security_work_authorized,
                    require_preferred_account=require_preferred_account,
                    defer_no_account_error=last_failover_exc is not None and not require_preferred_account,
                )
            except _WebSocketConnectFailureEmitted:
                return None, None
            selected_stream_lease = request_state.websocket_stream_lease
            request_state.websocket_stream_lease = None
            if account is None:
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                if (
                    last_failover_exc is not None
                    and not require_preferred_account
                    and _facade()._remaining_budget_seconds(deadline) <= 0
                ):
                    await proxy._emit_websocket_connect_timeout(
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        account_id=None,
                        api_key=api_key,
                        request_state=request_state,
                    )
                    return None, None
                if last_failover_exc is not None and not require_preferred_account:
                    break
                return None, None
            if forced_refresh_account_id is not None and account.id != forced_refresh_account_id:
                request_state.force_refresh_account_id = None
                if request_state.preferred_account_id == forced_refresh_account_id:
                    request_state.preferred_account_id = None

            try:
                connect_result = await proxy._try_open_websocket_connect_attempt(
                    account,
                    headers,
                    deadline=deadline,
                    api_key=api_key,
                    request_state=request_state,
                    client_send_lock=client_send_lock,
                    websocket=websocket,
                    force_refresh=forced_refresh_account_id == account.id,
                )
            except ProxyResponseError as exc:
                action = await proxy._decide_websocket_failover_action(
                    account=account,
                    exc=exc,
                    request_state=request_state,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    deterministic_failover_enabled=getattr(base_settings, "deterministic_failover_enabled", True),
                )
                if action == "failover_next":
                    await proxy._load_balancer.release_account_lease(selected_stream_lease)
                    last_failover_exc = exc
                    last_failover_account = account
                    excluded_account_ids.add(account.id)
                    continue
                error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                error_message = error.message if error else None
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                selected_stream_lease = None
                await proxy._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=exc.status_code,
                    payload=exc.payload,
                    error_code=error_code or "upstream_error",
                    error_message=error_message or "Upstream error",
                )
                return None, None
            except BaseException:
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                raise

            if connect_result is None:
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                return None, None
            request_state.websocket_stream_lease = selected_stream_lease
            return connect_result

        if last_failover_exc is not None and last_failover_account is not None:
            error = _parse_openai_error(last_failover_exc.payload)
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=last_failover_account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=last_failover_exc.status_code,
                payload=last_failover_exc.payload,
                error_code=error_code or "upstream_error",
                error_message=error_message or "Upstream error",
            )
        return None, None

    async def _select_websocket_connect_account(
        self,
        deadline: float,
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        prefer_earlier_reset: bool,
        routing_strategy: RoutingStrategy,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
        model: str | None,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        exclude_account_ids: set[str],
        preferred_account_id: str | None,
        require_security_work_authorized: bool = False,
        require_preferred_account: bool = False,
        defer_no_account_error: bool = False,
    ) -> Account | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        try:
            selection = await proxy._select_account_with_budget_compatible(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="websocket",
                api_key=api_key,
                sticky_key=sticky_key,
                sticky_kind=sticky_kind,
                reallocate_sticky=reallocate_sticky,
                sticky_max_age_seconds=sticky_max_age_seconds,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                prefer_earlier_reset_window=prefer_earlier_reset_window,
                routing_strategy=routing_strategy,
                model=model,
                exclude_account_ids=exclude_account_ids,
                preferred_account_id=preferred_account_id,
                require_security_work_authorized=require_security_work_authorized,
                lease_kind="stream",
                estimated_lease_tokens=_facade()._estimated_lease_tokens_from_request_usage_budget(
                    request_state.request_usage_budget
                ),
                fallback_on_preferred_account_unavailable=not require_preferred_account,
            )
        except ProxyResponseError as exc:
            if _facade()._is_proxy_budget_exhausted_error(exc):
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=None,
                    api_key=api_key,
                    request_state=request_state,
                )
                raise _WebSocketConnectFailureEmitted
            raise

        account = selection.account
        if (
            account is not None
            and require_preferred_account
            and preferred_account_id is not None
            and account.id != preferred_account_id
        ):
            await proxy._load_balancer.release_account_lease(selection.lease)
            message = "Previous response owner account is unavailable; retry later."
            _record_continuity_fail_closed(
                surface="websocket_connect",
                reason="owner_account_unavailable",
                previous_response_id=request_state.previous_response_id,
                session_id=request_state.session_id,
                upstream_error_code="previous_response_owner_unavailable",
            )
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "previous_response_owner_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="previous_response_owner_unavailable",
                error_message=message,
            )
            return None
        if account:
            request_state.websocket_stream_lease = selection.lease
            return account
        if defer_no_account_error:
            _facade().logger.warning(
                "Websocket account selection deferred no-account error request_id=%s model=%s "
                "preferred_account_id=%s require_preferred=%s error_code=%s error=%s excluded_count=%s",
                request_state.request_log_id or request_state.request_id,
                model,
                preferred_account_id,
                require_preferred_account,
                selection.error_code,
                selection.error_message,
                len(exclude_account_ids),
            )
            return None
        error_code = selection.error_code or "no_accounts"
        error_message = selection.error_message or "No active accounts available"
        if require_security_work_authorized and error_code == _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE:
            await proxy._emit_websocket_security_work_missing_pool(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
            )
            return None
        if require_preferred_account and preferred_account_id is not None:
            if request_state.file_required_preferred_account and _facade()._is_local_account_cap_code(error_code):
                await proxy._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=preferred_account_id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=429,
                    payload=openai_error(
                        error_code,
                        error_message,
                        error_type="rate_limit_error",
                    ),
                    error_code=error_code,
                    error_message=error_message,
                )
                return None
            message = "Previous response owner account is unavailable; retry later."
            _record_continuity_fail_closed(
                surface="websocket_connect",
                reason="owner_account_unavailable",
                previous_response_id=request_state.previous_response_id,
                session_id=request_state.session_id,
                upstream_error_code=error_code,
            )
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "previous_response_owner_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="previous_response_owner_unavailable",
                error_message=message,
            )
            return None
        _facade().logger.warning(
            "Websocket account selection failed request_id=%s model=%s preferred_account_id=%s "
            "require_preferred=%s error_code=%s error=%s excluded_count=%s api_key_present=%s",
            request_state.request_log_id or request_state.request_id,
            model,
            preferred_account_id,
            require_preferred_account,
            error_code,
            error_message,
            len(exclude_account_ids),
            api_key is not None,
        )
        status_code = 429 if is_local_overload_error_code(error_code) else 503
        await proxy._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=None,
            api_key=api_key,
            request_state=request_state,
            status_code=status_code,
            payload=openai_error(
                error_code,
                error_message,
                error_type="rate_limit_error" if status_code == 429 else "server_error",
            ),
            error_code=error_code,
            error_message=error_message,
        )
        return None

    async def _emit_websocket_security_work_missing_pool(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        async with client_send_lock:
            await websocket.send_text(
                json.dumps(
                    _facade()._security_work_advisory_event(
                        code=_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                        message=_facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                        request_id=request_state.request_log_id or request_state.request_id,
                        action="forward_original_security_work_error",
                    ),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )
        await proxy._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            status_code=400,
            payload=openai_error(
                request_state.error_code_override or _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                request_state.error_message_override or "Security work authorization is required",
                error_type=request_state.error_type_override or "invalid_request_error",
            ),
            error_code=request_state.error_code_override or _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
            error_message=request_state.error_message_override or "Security work authorization is required",
        )

    async def _try_open_websocket_connect_attempt(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        deadline: float,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        force_refresh: bool = False,
    ) -> tuple[Account, UpstreamResponsesWebSocket] | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        try:
            remaining_budget = _facade()._remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            account = await proxy._ensure_fresh_with_budget(
                account,
                force=force_refresh,
                timeout_seconds=remaining_budget,
            )
            if force_refresh and request_state.force_refresh_account_id == account.id:
                request_state.force_refresh_account_id = None

            remaining_budget = _facade()._remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            upstream = await _facade()._call_with_supported_optional_kwargs(
                proxy._open_upstream_websocket_with_budget,
                account,
                headers,
                optional_kwargs={"request_state": request_state},
                timeout_seconds=remaining_budget,
            )
            return account, upstream
        except ProxyResponseError as exc:
            if _facade()._is_proxy_budget_exhausted_error(exc):
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            if exc.status_code != 401 or force_refresh:
                raise
            return await proxy._retry_websocket_connect_after_401(
                account,
                headers,
                deadline=deadline,
                api_key=api_key,
                request_state=request_state,
                client_send_lock=client_send_lock,
                websocket=websocket,
            )
        except RefreshError as exc:
            if exc.is_permanent:
                await proxy._load_balancer.mark_permanent_failure(account, exc.code)
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=401,
                payload=openai_error(
                    "invalid_api_key",
                    exc.message,
                    error_type="authentication_error",
                ),
                error_code="invalid_api_key",
                error_message=exc.message,
            )
            return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            raise ProxyResponseError(
                502,
                openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
            ) from exc

    async def _retry_websocket_connect_after_401(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        deadline: float,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
    ) -> tuple[Account, UpstreamResponsesWebSocket] | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        try:
            remaining_budget = _facade()._remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            account = await proxy._ensure_fresh_with_budget(
                account,
                force=True,
                timeout_seconds=remaining_budget,
            )
        except RefreshError as refresh_exc:
            if refresh_exc.is_permanent:
                await proxy._load_balancer.mark_permanent_failure(account, refresh_exc.code)
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=401,
                payload=openai_error(
                    "invalid_api_key",
                    refresh_exc.message,
                    error_type="authentication_error",
                ),
                error_code="invalid_api_key",
                error_message=refresh_exc.message,
            )
            return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as refresh_transport_exc:
            message = str(refresh_transport_exc) or "Request to upstream timed out"
            raise ProxyResponseError(
                502,
                openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
            ) from refresh_transport_exc

        try:
            remaining_budget = _facade()._remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            return account, await proxy._open_upstream_websocket_with_budget(
                account,
                headers,
                timeout_seconds=remaining_budget,
                request_state=request_state,
            )
        except ProxyResponseError as exc:
            if _facade()._is_proxy_budget_exhausted_error(exc):
                await proxy._emit_websocket_connect_timeout(
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None
            raise

    async def _decide_websocket_failover_action(
        self,
        *,
        account: Account,
        exc: ProxyResponseError,
        request_state: _WebSocketRequestState,
        attempt: int,
        max_attempts: int,
        deterministic_failover_enabled: bool,
    ) -> str:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        classified = await proxy._handle_websocket_connect_error(account, exc)
        failure_class = classified["failure_class"] if isinstance(classified, dict) else "non_retryable"
        candidates_remaining = max_attempts - attempt
        if exc.status_code == 401 and candidates_remaining > 0:
            action = "failover_next"
        elif deterministic_failover_enabled:
            action = failover_decision(
                failure_class=failure_class,
                downstream_visible=False,
                candidates_remaining=candidates_remaining,
            )
        else:
            action = "surface"
        _facade().logger.info(
            "Failover decision request_id=%s transport=websocket account_id=%s attempt=%d failure_class=%s action=%s",
            request_state.request_log_id or request_state.request_id,
            account.id,
            attempt,
            failure_class,
            action,
        )
        return action

    async def _emit_websocket_connect_timeout(
        self,
        *,
        websocket: WebSocket,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        await proxy._emit_websocket_proxy_request_timeout(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
        )

    async def _open_upstream_websocket_with_budget(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        request_state: "_WebSocketRequestState | None" = None,
    ) -> UpstreamResponsesWebSocket:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        started_at = time.monotonic()
        try:
            with anyio.fail_after(timeout_seconds):
                return await proxy._open_upstream_websocket(account, headers, request_state=request_state)
        except TimeoutError:
            if time.monotonic() - started_at < timeout_seconds:
                raise
            _raise_proxy_budget_exhausted()

    async def _open_upstream_websocket(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        request_state: "_WebSocketRequestState | None" = None,
    ) -> UpstreamResponsesWebSocket:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        access_token = proxy._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        connect_lease = await proxy._get_work_admission().acquire_websocket_connect()
        try:
            try:
                route = await proxy._resolve_upstream_route_for_account(account, operation="responses_websocket")
            except UpstreamProxyRouteError as exc:
                if request_state is not None:
                    request_state.upstream_proxy_fail_closed_reason = exc.reason
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "upstream_proxy_unavailable",
                        "Unable to resolve upstream proxy route for websocket request",
                        error_type="server_error",
                    ),
                ) from exc
            upstream = await _facade()._call_with_supported_optional_kwargs(
                _facade().connect_responses_websocket,
                headers,
                access_token,
                account_id,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                },
            )
            if request_state is not None:
                _record_websocket_route_metadata(request_state, upstream=upstream, route=route)
            return upstream
        finally:
            connect_lease.release()

    async def _refresh_websocket_api_key_policy(self, api_key: ApiKeyData | None) -> ApiKeyData | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if api_key is None:
            return None

        with anyio.CancelScope(shield=True):
            async with proxy._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                try:
                    return await service.get_key_by_id(api_key.id)
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    def _remember_websocket_previous_response_owner(
        self,
        *,
        previous_response_id: str | None,
        api_key_id: str | None,
        account_id: str | None,
        session_id: str | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if previous_response_id is None or account_id is None:
            return
        response_id = previous_response_id.strip()
        if not response_id:
            return
        account_id_value = account_id.strip()
        if not account_id_value:
            return
        cache_keys = [(response_id, api_key_id, None)]
        normalized_session_id = _facade()._normalize_session_id(session_id)
        if normalized_session_id is not None:
            cache_keys.append((response_id, api_key_id, normalized_session_id))
        for cache_key in cache_keys:
            proxy._websocket_previous_response_account_index.pop(cache_key, None)
            proxy._websocket_previous_response_account_index[cache_key] = account_id_value
        while (
            len(proxy._websocket_previous_response_account_index)
            > _facade()._WEBSOCKET_PREVIOUS_RESPONSE_ACCOUNT_CACHE_LIMIT
        ):
            proxy._websocket_previous_response_account_index.pop(
                next(iter(proxy._websocket_previous_response_account_index))
            )

    def _remember_websocket_previous_response_owner_miss(
        self,
        *,
        previous_response_id: str | None,
        api_key_id: str | None,
        request_cache_scope: str | None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        del previous_response_id, api_key_id, request_cache_scope
        # Intentionally no-op: negative caching caused stale misses under concurrent sessions.
        return None

    async def _resolve_websocket_previous_response_owner(
        self,
        *,
        previous_response_id: str | None,
        api_key: ApiKeyData | None,
        session_id: str | None = None,
        surface: str,
    ) -> str | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if previous_response_id is None:
            return None
        response_id = previous_response_id.strip()
        if not response_id:
            return None
        api_key_id = api_key.id if api_key is not None else None
        session_id_value = _facade()._normalize_session_id(session_id)
        cache_key = (response_id, api_key_id, session_id_value)
        cached_account_id = proxy._websocket_previous_response_account_index.get(cache_key)
        if cached_account_id is not None:
            _record_continuity_owner_resolution(
                surface=surface,
                source="request_cache",
                outcome="hit",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            return cached_account_id
        fallback_account_id = (
            proxy._websocket_previous_response_account_index.get((response_id, api_key_id, None))
            if session_id_value is not None
            else None
        )
        try:
            async with proxy._repo_factory() as repos:
                account_id = await repos.request_logs.find_latest_account_id_for_response_id(
                    response_id=response_id,
                    api_key_id=api_key_id,
                    session_id=session_id_value,
                )
        except Exception as exc:
            if fallback_account_id is not None:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_cache_fallback",
                    outcome="hit",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
                _facade().logger.warning(
                    "Previous response owner lookup failed; using cached owner pin",
                    exc_info=True,
                )
                return fallback_account_id
            _record_continuity_owner_resolution(
                surface=surface,
                source="request_logs",
                outcome="fail_closed",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            _record_continuity_fail_closed(
                surface=surface,
                reason="owner_lookup_failed",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            _facade().logger.warning("Previous response owner lookup failed; failing closed", exc_info=True)
            raise ProxyResponseError(
                502,
                _facade()._previous_response_owner_lookup_failed_error_envelope(),
            ) from exc
        if account_id is None:
            if fallback_account_id is not None:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_cache_fallback",
                    outcome="hit",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
            else:
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_logs",
                    outcome="miss",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
            return fallback_account_id
        proxy._remember_websocket_previous_response_owner(
            previous_response_id=response_id,
            api_key_id=api_key_id,
            account_id=account_id,
            session_id=session_id_value,
        )
        _record_continuity_owner_resolution(
            surface=surface,
            source="request_logs",
            outcome="hit",
            previous_response_id=response_id,
            session_id=session_id_value,
        )
        return account_id

    async def _handle_websocket_connect_error(self, account: Account, exc: ProxyResponseError) -> ClassifiedFailure:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        error = _parse_openai_error(exc.payload)
        error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
        return await proxy._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            error_code,
            http_status=exc.status_code,
        )

    async def _relay_upstream_websocket_messages(
        self,
        websocket: WebSocket,
        upstream: UpstreamResponsesWebSocket,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        client_send_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
        downstream_activity: _DownstreamWebSocketActivity,
        codex_session_affinity: bool = True,
        continuity_state: "_WebSocketContinuityState | None" = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        try:
            while True:
                receive_timeout = await proxy._next_websocket_receive_timeout(
                    pending_requests,
                    pending_lock=pending_lock,
                    proxy_request_budget_seconds=proxy_request_budget_seconds,
                    stream_idle_timeout_seconds=stream_idle_timeout_seconds,
                )
                receive_deadline = (
                    None if receive_timeout is None else time.monotonic() + receive_timeout.timeout_seconds
                )
                try:
                    while True:
                        wait_timeout = None if receive_deadline is None else receive_deadline - time.monotonic()
                        if wait_timeout is not None and wait_timeout <= 0:
                            raise asyncio.TimeoutError()
                        keepalive_interval = getattr(_facade().get_settings(), "sse_keepalive_interval_seconds", 10.0)
                        if keepalive_interval > 0:
                            wait_timeout = (
                                keepalive_interval if wait_timeout is None else min(wait_timeout, keepalive_interval)
                            )
                        message = await asyncio.wait_for(
                            upstream.receive(),
                            timeout=wait_timeout,
                        )
                        break
                except asyncio.TimeoutError:
                    if receive_deadline is None or time.monotonic() < receive_deadline:
                        try:
                            await proxy._emit_pending_websocket_keepalive(
                                websocket,
                                pending_requests=pending_requests,
                                pending_lock=pending_lock,
                                client_send_lock=client_send_lock,
                                downstream_activity=downstream_activity,
                                codex_session_affinity=codex_session_affinity,
                            )
                        except Exception:
                            downstream_activity.mark_disconnected()
                            _facade().logger.debug("Downstream websocket disconnected during keepalive", exc_info=True)
                            await proxy._fail_pending_websocket_requests(
                                account=None,
                                account_id_value=account_id_value,
                                pending_requests=pending_requests,
                                pending_lock=pending_lock,
                                error_code="client_disconnected",
                                error_message="Downstream websocket disconnected before response.completed",
                                api_key=api_key,
                                response_create_gate=response_create_gate,
                                status="cancelled",
                                penalize_account=False,
                            )
                            try:
                                await upstream.close()
                            except Exception:
                                _facade().logger.debug(
                                    "Failed to close upstream websocket after downstream keepalive failure",
                                    exc_info=True,
                                )
                            break
                        continue
                    if receive_timeout is None:
                        raise
                    if receive_timeout.fail_all_pending:
                        await proxy._fail_pending_websocket_requests(
                            account=account,
                            account_id_value=account_id_value,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            error_code=receive_timeout.error_code,
                            error_message=receive_timeout.error_message,
                            api_key=api_key,
                            websocket=websocket,
                            client_send_lock=client_send_lock,
                            response_create_gate=response_create_gate,
                        )
                        upstream_control.reconnect_requested = True
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug("Failed to close upstream websocket after timeout", exc_info=True)
                        break
                    await proxy._fail_expired_pending_websocket_requests(
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        request_budget_seconds=proxy_request_budget_seconds,
                        error_code=receive_timeout.error_code,
                        error_message=receive_timeout.error_message,
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                    )
                    continue
                if message.kind == "text" and message.text is not None:
                    downstream_activity.mark()
                    downstream_text = await proxy._process_upstream_websocket_text(
                        message.text,
                        account=account,
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        api_key=api_key,
                        upstream_control=upstream_control,
                        response_create_gate=response_create_gate,
                        continuity_state=continuity_state,
                        codex_session_affinity=codex_session_affinity,
                    )
                    suppress_downstream_event = upstream_control.suppress_downstream_event
                    downstream_texts = upstream_control.downstream_texts
                    upstream_control.suppress_downstream_event = False
                    upstream_control.downstream_texts = None
                    if downstream_texts is not None:
                        for emitted_text in downstream_texts:
                            try:
                                await proxy._send_downstream_websocket_text(
                                    websocket,
                                    client_send_lock=client_send_lock,
                                    text=emitted_text,
                                    downstream_activity=downstream_activity,
                                )
                            except Exception:
                                downstream_activity.mark_disconnected()
                                _facade().logger.debug(
                                    "Downstream websocket disconnected during upstream relay", exc_info=True
                                )
                                await proxy._fail_pending_websocket_requests(
                                    account=None,
                                    account_id_value=account_id_value,
                                    pending_requests=pending_requests,
                                    pending_lock=pending_lock,
                                    error_code="client_disconnected",
                                    error_message="Downstream websocket disconnected before response.completed",
                                    api_key=api_key,
                                    response_create_gate=response_create_gate,
                                    status="cancelled",
                                    penalize_account=False,
                                )
                                try:
                                    await upstream.close()
                                except Exception:
                                    _facade().logger.debug(
                                        "Failed to close upstream websocket after downstream disconnect",
                                        exc_info=True,
                                    )
                                break
                        if downstream_activity.disconnected:
                            break
                    elif not suppress_downstream_event:
                        try:
                            await proxy._send_downstream_websocket_text(
                                websocket,
                                client_send_lock=client_send_lock,
                                text=downstream_text,
                                downstream_activity=downstream_activity,
                            )
                        except Exception:
                            downstream_activity.mark_disconnected()
                            _facade().logger.debug(
                                "Downstream websocket disconnected during upstream relay", exc_info=True
                            )
                            await proxy._fail_pending_websocket_requests(
                                account=None,
                                account_id_value=account_id_value,
                                pending_requests=pending_requests,
                                pending_lock=pending_lock,
                                error_code="client_disconnected",
                                error_message="Downstream websocket disconnected before response.completed",
                                api_key=api_key,
                                response_create_gate=response_create_gate,
                                status="cancelled",
                                penalize_account=False,
                            )
                            try:
                                await upstream.close()
                            except Exception:
                                _facade().logger.debug(
                                    "Failed to close upstream websocket after downstream disconnect",
                                    exc_info=True,
                                )
                            break
                    if upstream_control.reconnect_requested:
                        should_reconnect = upstream_control.replay_request_state is not None
                        if not should_reconnect:
                            async with pending_lock:
                                should_reconnect = not pending_requests
                        if should_reconnect:
                            try:
                                await upstream.close()
                            except Exception:
                                _facade().logger.debug(
                                    "Failed to close upstream websocket for reconnect", exc_info=True
                                )
                            break
                    continue
                if message.kind == "binary" and message.data is not None:
                    downstream_activity.mark()
                    try:
                        await proxy._send_downstream_websocket_bytes(
                            websocket,
                            client_send_lock=client_send_lock,
                            data=message.data,
                            downstream_activity=downstream_activity,
                        )
                    except Exception:
                        downstream_activity.mark_disconnected()
                        _facade().logger.debug(
                            "Downstream websocket disconnected during upstream binary relay", exc_info=True
                        )
                        await proxy._fail_pending_websocket_requests(
                            account=None,
                            account_id_value=account_id_value,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            error_code="client_disconnected",
                            error_message="Downstream websocket disconnected before response.completed",
                            api_key=api_key,
                            response_create_gate=response_create_gate,
                            status="cancelled",
                            penalize_account=False,
                        )
                        try:
                            await upstream.close()
                        except Exception:
                            _facade().logger.debug(
                                "Failed to close upstream websocket after downstream disconnect",
                                exc_info=True,
                            )
                        break
                    continue
                replay_request_state = await _pop_replayable_precreated_websocket_request_state(
                    pending_requests,
                    pending_lock=pending_lock,
                )
                if replay_request_state is not None:
                    upstream_control.reconnect_requested = True
                    upstream_control.replay_request_state = replay_request_state
                    _facade().logger.info(
                        "Transparent websocket replay after upstream close request_id=%s close_code=%s",
                        replay_request_state.request_log_id or replay_request_state.request_id,
                        message.close_code,
                    )
                    try:
                        await upstream.close()
                    except Exception:
                        _facade().logger.debug("Failed to close upstream websocket for replay", exc_info=True)
                    break
                await proxy._fail_pending_websocket_requests(
                    account=account,
                    account_id_value=account_id_value,
                    pending_requests=pending_requests,
                    pending_lock=pending_lock,
                    error_code="stream_incomplete",
                    error_message=_upstream_websocket_disconnect_message(message),
                    api_key=api_key,
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    response_create_gate=response_create_gate,
                    downstream_activity=downstream_activity,
                )
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            _facade().logger.warning(
                "Upstream websocket reader crashed account_id=%s",
                account_id_value,
                exc_info=True,
            )
            await proxy._fail_pending_websocket_requests(
                account=account,
                account_id_value=account_id_value,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="Upstream websocket reader crashed before response.completed",
                api_key=api_key,
                websocket=websocket,
                client_send_lock=client_send_lock,
                response_create_gate=response_create_gate,
                downstream_activity=downstream_activity,
            )
        finally:
            async with pending_lock:
                has_pending_requests = bool(pending_requests)
            if not upstream_control.reconnect_requested and has_pending_requests:
                try:
                    await websocket.close()
                except Exception:
                    _facade().logger.debug("Failed to close downstream websocket", exc_info=True)

    async def _process_upstream_websocket_text(
        self,
        text: str,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
        continuity_state: "_WebSocketContinuityState | None" = None,
        codex_session_affinity: bool = False,
    ) -> str:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json(event_block)
        if payload is None:
            try:
                raw_payload = json.loads(text)
            except json.JSONDecodeError:
                raw_payload = None
            if isinstance(raw_payload, dict):
                payload = cast(dict[str, JsonValue], raw_payload)
                event_block = format_sse_event(payload)
        event = parse_sse_event(event_block)
        event_type = _event_type_from_payload(event, payload)
        response_id = _websocket_response_id(event, payload)
        error_message = _websocket_event_error_message(event_type, payload)
        is_typeless_error_event = (
            isinstance(payload, dict)
            and not isinstance(payload.get("type"), str)
            and isinstance(payload.get("error"), dict)
        )
        is_previous_response_not_found_event = _facade()._is_previous_response_not_found_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        is_missing_tool_output_event = _facade()._is_missing_tool_output_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        previous_response_id_hint = _facade()._previous_response_id_from_not_found_message(error_message)
        text, payload, event, event_type, _event_block = rewrite_parallel_tool_call_text(
            text,
            payload,
            event_block=format_sse_event(payload) if payload is not None else f"data: {text}\n\n",
        )

        async with pending_lock:
            request_state = None
            created_request_state = None
            has_other_pending_requests = False
            grouped_previous_response_request_states: list[_WebSocketRequestState] = []
            if event_type == "response.created":
                request_state = _assign_websocket_response_id(pending_requests, response_id)
                created_request_state = request_state
                release_create_gate = request_state is not None
            elif response_id is not None:
                request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
                release_create_gate = False
            elif response_id is None:
                request_state = _match_websocket_request_state_for_anonymous_event(
                    pending_requests,
                    prefer_previous_response_not_found=is_previous_response_not_found_event
                    or is_missing_tool_output_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                )
                release_create_gate = False
            else:
                release_create_gate = False
            if request_state is not None:
                actual_service_tier = _facade()._service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    request_state.actual_service_tier = actual_service_tier
                    request_state.service_tier = actual_service_tier
                completed_function_call_id = _facade()._response_output_item_done_function_call_id(payload)
                if (
                    completed_function_call_id is not None
                    and completed_function_call_id not in request_state.pending_function_call_ids
                ):
                    request_state.pending_function_call_ids.append(completed_function_call_id)
                if mark_duplicate_tool_call_downstream_event(
                    payload,
                    seen_tool_call_keys=request_state.seen_tool_call_keys,
                    response_id=tool_call_response_id_from_payload(payload) or request_state.request_id,
                    scope_side_effects_by_response_id=False,
                ):
                    request_state.suppressed_duplicate_tool_call = True
                    upstream_control.suppress_downstream_event = True
                    return text
                if event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                    request_state.downstream_visible = True
                if event_type == "response.created" and request_state.suppress_next_created_downstream:
                    request_state.suppress_next_created_downstream = False
                    upstream_control.suppress_downstream_event = True
                if payload is not None:
                    payload = _rewrite_websocket_downstream_response_id(payload, request_state)
                    text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
            if (
                event_type in {"response.completed", "response.failed", "response.incomplete", "error"}
                and pending_requests
            ):
                request_state = _pop_terminal_websocket_request_state(
                    pending_requests,
                    response_id=response_id,
                    fallback_request_state=request_state,
                    prefer_previous_response_not_found=is_previous_response_not_found_event
                    or is_missing_tool_output_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                    allow_precreated_terminal_fallback=event_type
                    in {
                        "response.failed",
                        "response.incomplete",
                        "error",
                    },
                )
                if request_state is None and (is_previous_response_not_found_event or is_missing_tool_output_event):
                    grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                        pending_requests,
                        _matching_websocket_request_states_for_previous_response_error(
                            pending_requests,
                            previous_response_id_hint=previous_response_id_hint,
                            error_message=error_message,
                            allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                        ),
                    )
                    if not grouped_previous_response_request_states and is_missing_tool_output_event:
                        grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                            pending_requests,
                            _matching_websocket_request_states_for_missing_tool_output_error(
                                pending_requests,
                            ),
                        )
                if (
                    request_state is None
                    and event_type == "error"
                    and is_typeless_error_event
                    and not grouped_previous_response_request_states
                ):
                    grouped_previous_response_request_states = list(pending_requests)
                    pending_requests.clear()
                if (
                    event_type == "response.completed"
                    and request_state is not None
                    and request_state.suppressed_duplicate_tool_call
                ):
                    upstream_control.reconnect_requested = True
                    request_state.error_http_status_override = 502
                    event, payload, event_type, rewritten_text = (
                        _rewrite_websocket_suppressed_duplicate_tool_call_completion_event(
                            request_state=request_state,
                        )
                    )
                    text = rewritten_text
                if (
                    request_state is not None
                    and request_state.previous_response_id is not None
                    and is_missing_tool_output_event
                ):
                    request_state.error_http_status_override = 502
                    event, payload, event_type, text = _rewrite_websocket_continuity_corruption_event(
                        request_state=request_state,
                        upstream_control=upstream_control,
                        reason="missing_tool_output",
                        reconnect_requested=True,
                        original_text=text,
                    )
                has_other_pending_requests = bool(pending_requests)
            else:
                request_state = None

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            await _release_websocket_response_create_gate(created_request_state, response_create_gate)

        if len(grouped_previous_response_request_states) > 1:
            upstream_control.reconnect_requested = True
            downstream_texts: list[str] = []
            grouped_error_reason = (
                "previous_response_not_found"
                if is_previous_response_not_found_event
                else "missing_tool_output"
                if is_missing_tool_output_event
                else "stream_incomplete"
            )
            for grouped_request_state in grouped_previous_response_request_states:
                (
                    grouped_downstream_text,
                    _grouped_event_block,
                    grouped_event,
                    grouped_payload,
                    grouped_event_type,
                ) = _facade()._build_stream_incomplete_terminal_event_for_request(
                    grouped_request_state,
                    reason=grouped_error_reason,
                )
                downstream_texts.append(grouped_downstream_text)
                await proxy._finalize_websocket_request_state(
                    grouped_request_state,
                    account=account,
                    account_id_value=account_id_value,
                    event=grouped_event,
                    event_type=grouped_event_type,
                    payload=grouped_payload,
                    api_key=api_key,
                    upstream_control=upstream_control,
                    response_create_gate=response_create_gate,
                )
            upstream_control.suppress_downstream_event = True
            upstream_control.downstream_texts = downstream_texts
            return downstream_texts[0]

        if len(grouped_previous_response_request_states) == 1 and request_state is None:
            request_state = grouped_previous_response_request_states[0]

        _record_response_event(request_state, event_type)

        if request_state is None:
            if is_previous_response_not_found_event:
                upstream_control.reconnect_requested = True
                fallback_error_code, fallback_error_message = _websocket_continuity_error_fields(
                    reason="previous_response_not_found",
                    expose_stale_previous_response_classifier=codex_session_affinity,
                )
                downstream_text = json.dumps(
                    cast(
                        dict[str, JsonValue],
                        response_failed_event(
                            fallback_error_code,
                            fallback_error_message,
                            error_type="server_error",
                            response_id=get_request_id(),
                        ),
                    ),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                return downstream_text
            if is_missing_tool_output_event:
                upstream_control.suppress_downstream_event = True
            return text

        if event_type not in {"response.completed", "response.failed", "response.incomplete", "error"}:
            await proxy._maybe_touch_request_state_api_key_reservation(
                request_state,
                api_key=request_state.api_key or api_key,
                surface="websocket",
            )

        retry_is_previous_response_not_found = is_previous_response_not_found_event
        retry_error_code = _websocket_precreated_retry_error_code(
            request_state,
            event_type=event_type,
            payload=payload,
            has_other_pending_requests=has_other_pending_requests,
        )
        auth_error_code = _websocket_precreated_auth_error_code(
            request_state,
            event_type=event_type,
            payload=payload,
            has_other_pending_requests=has_other_pending_requests,
        )
        if auth_error_code is not None:
            handled_auth_failure = await proxy._handle_precreated_websocket_auth_failure(
                account=account,
                request_state=request_state,
                upstream_control=upstream_control,
                error_message=_websocket_event_error_message(event_type, payload),
            )
            if handled_auth_failure:
                return text
        event, payload, event_type, downstream_text = _maybe_rewrite_websocket_previous_response_not_found_event(
            request_state=request_state,
            event=event,
            payload=payload,
            event_type=event_type,
            upstream_control=upstream_control,
            original_text=text,
        )
        if retry_error_code is None:
            retry_error_code = _websocket_precreated_retry_error_code(
                request_state,
                event_type=event_type,
                payload=payload,
                has_other_pending_requests=has_other_pending_requests,
            )
        if (
            retry_error_code in _facade()._WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES
            and request_state.previous_response_id is not None
            and request_state.preferred_account_id is not None
        ):
            await proxy._handle_stream_error(
                account,
                {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                retry_error_code,
            )
            event, payload, event_type, downstream_text = _rewrite_websocket_previous_response_owner_unavailable_event(
                request_state=request_state,
            )
            retry_error_code = None
        if retry_error_code is not None:
            if retry_is_previous_response_not_found:
                if not (
                    request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text
                ):
                    # A short continuation depends entirely on the upstream
                    # anchor. Replaying the same lost previous_response_id on a
                    # new websocket just re-surfaces the raw upstream 400; only
                    # full-resend payloads with a prepared fresh body can be
                    # transparently retried.
                    retry_error_code = None
                else:
                    upstream_control.reconnect_requested = True
                    request_state.request_text = request_state.fresh_upstream_request_text
                    request_state.previous_response_id = None
                    request_state.proxy_injected_previous_response_id = False
                    request_state.fresh_upstream_request_is_retry_safe = False
                    request_state.replay_count += 1
                    request_state.awaiting_response_created = True
                    request_state.response_id = None
                    _clear_websocket_request_error_overrides(request_state)
                    upstream_control.suppress_downstream_event = True
                    upstream_control.replay_request_state = request_state
            else:
                upstream_control.reconnect_requested = True
                request_state.replay_count += 1
                request_state.awaiting_response_created = True
                request_state.response_id = None
                _clear_websocket_request_error_overrides(request_state)
                upstream_control.suppress_downstream_event = True
                upstream_control.replay_request_state = request_state
                await proxy._handle_stream_error(
                    account,
                    {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                    retry_error_code,
                )
            if retry_error_code is not None:
                return downstream_text

        if event_type == "response.completed" and continuity_state is not None:
            _record_websocket_continuity_completion(
                continuity_state,
                request_state=request_state,
                response_id=response_id,
            )

        if request_state is not None and event_type in {"response.failed", "error"}:
            if event_type == "error":
                error = event.error if event else None
            else:
                error = event.response.error if event and event.response else None
            terminal_error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            terminal_error_message = error.message if error else None
            if _facade()._is_security_work_authorization_required_error(terminal_error_code, terminal_error_message):
                can_retry_security_work = (
                    not account.security_work_authorized
                    and not has_other_pending_requests
                    and request_state.response_id is None
                    and request_state.replay_count < 1
                    and bool(request_state.request_text)
                    and request_state.preferred_account_id != account.id
                    and (
                        request_state.previous_response_id is None
                        or (
                            request_state.fresh_upstream_request_text is not None
                            and request_state.fresh_upstream_request_is_retry_safe
                        )
                    )
                )
                if can_retry_security_work:
                    retry_text = request_state.request_text
                    if request_state.previous_response_id is not None:
                        retry_text = request_state.fresh_upstream_request_text
                    if retry_text:
                        request_state.replay_count += 1
                        request_state.response_id = None
                        request_state.awaiting_response_created = True
                        request_state.require_security_work_authorized = True
                        request_state.error_code_override = _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE
                        request_state.error_message_override = terminal_error_message
                        request_state.error_type_override = error.type if error else None
                        request_state.error_param_override = error.param if error else None
                        if retry_text != request_state.request_text:
                            request_state.previous_response_id = None
                            request_state.proxy_injected_previous_response_id = False
                            request_state.request_text = retry_text
                        upstream_control.reconnect_requested = True
                        upstream_control.suppress_downstream_event = True
                        await _release_websocket_response_create_gate(request_state, response_create_gate)
                        upstream_control.downstream_texts = [
                            json.dumps(
                                _facade()._security_work_advisory_event(
                                    code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                    message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                    request_id=request_state.request_log_id or request_state.request_id,
                                    action="retry_security_work_authorized",
                                    account_id=account.id,
                                ),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            )
                        ]
                        upstream_control.replay_request_state = request_state
                        return downstream_text

        await proxy._finalize_websocket_request_state(
            request_state,
            account=account,
            account_id_value=account_id_value,
            event=event,
            event_type=event_type,
            payload=payload,
            api_key=api_key,
            upstream_control=upstream_control,
            response_create_gate=response_create_gate,
        )
        return downstream_text

    async def _handle_precreated_websocket_auth_failure(
        self,
        *,
        account: Account,
        request_state: "_WebSocketRequestState",
        upstream_control: "_WebSocketUpstreamControl",
        error_message: str | None,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if _prepare_websocket_request_state_for_auth_replay(request_state) is None:
            return False

        if _websocket_auth_failure_requires_reauth(error_message):
            failure_code = _facade()._WEBSOCKET_SESSION_EXPIRED_FAILURE_CODE
        elif request_state.auth_replay_counts_by_account.get(account.id, 0) == 0:
            request_state.auth_replay_counts_by_account[account.id] = 1
            request_state.force_refresh_account_id = account.id
            request_state.preferred_account_id = account.id
            upstream_control.reconnect_requested = True
            upstream_control.suppress_downstream_event = True
            upstream_control.replay_request_state = request_state
            return True
        else:
            failure_code = _facade()._WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE

        await proxy._load_balancer.mark_permanent_failure(account, failure_code)
        request_state.force_refresh_account_id = None
        request_state.preferred_account_id = None
        request_state.excluded_account_ids.add(account.id)
        upstream_control.reconnect_requested = True
        upstream_control.suppress_downstream_event = True
        upstream_control.replay_request_state = request_state
        return True

    async def _next_websocket_receive_timeout(
        self,
        pending_requests: deque[_WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
    ) -> _WebSocketReceiveTimeout | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        async with pending_lock:
            started_ats = [
                request_state.started_at
                for request_state in pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            ]
        return _websocket_receive_timeout_for_pending_requests(
            started_ats,
            proxy_request_budget_seconds=proxy_request_budget_seconds,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
        )

    async def _emit_pending_websocket_keepalive(
        self,
        websocket: WebSocket,
        *,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        client_send_lock: anyio.Lock,
        downstream_activity: _DownstreamWebSocketActivity,
        codex_session_affinity: bool,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        async with pending_lock:
            keepalive_ids = [
                request_state.response_id for request_state in pending_requests if request_state.response_id is not None
            ]
            precreated_request_ids = [
                request_state.request_id for request_state in pending_requests if request_state.response_id is None
            ]
        emitted = False
        for response_id in keepalive_ids:
            event = {
                "type": "response.in_progress",
                "response": {"id": response_id, "status": "in_progress"},
            }
            await proxy._send_downstream_websocket_text(
                websocket,
                client_send_lock=client_send_lock,
                text=json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                downstream_activity=downstream_activity,
            )
            emitted = True
        if codex_session_affinity:
            for request_id in precreated_request_ids:
                event = {
                    "type": "codex.keepalive",
                    "request_id": request_id,
                    "status": "pending_response_created",
                }
                await proxy._send_downstream_websocket_text(
                    websocket,
                    client_send_lock=client_send_lock,
                    text=json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                    downstream_activity=downstream_activity,
                )
                emitted = True
        return emitted

    async def _downstream_websocket_is_idle(
        self,
        pending_requests: deque[_WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        downstream_activity: _DownstreamWebSocketActivity,
        idle_timeout_seconds: float,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        async with pending_lock:
            if pending_requests:
                return False
        return (time.monotonic() - downstream_activity.last_activity_at) >= idle_timeout_seconds

    async def _fail_expired_pending_websocket_requests(
        self,
        *,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        request_budget_seconds: float,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        now = time.monotonic()
        async with pending_lock:
            expired_requests = [
                request_state
                for request_state in list(pending_requests)
                if now >= request_state.started_at + request_budget_seconds
            ]
            for request_state in expired_requests:
                pending_requests.remove(request_state)
        if not expired_requests:
            return
        await proxy._fail_pending_websocket_requests(
            account_id_value=account_id_value,
            pending_requests=deque(expired_requests),
            pending_lock=anyio.Lock(),
            error_code=error_code,
            error_message=error_message,
            api_key=api_key,
            websocket=websocket,
            client_send_lock=client_send_lock,
            response_create_gate=response_create_gate,
        )

    async def _finalize_websocket_request_state(
        self,
        request_state: _WebSocketRequestState,
        *,
        account: Account,
        account_id_value: str,
        event: OpenAIEvent | None,
        event_type: str | None,
        payload: dict[str, JsonValue] | None,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        status = "success"
        error_code = None
        error_message = None
        usage = None
        error_payload: UpstreamError | None = None
        response_id = request_state.response_id or request_state.request_id
        response_service_tier = request_state.service_tier

        if request_state.draining_until_terminal:
            await _release_websocket_response_create_gate(request_state, response_create_gate)
            await proxy._release_websocket_reservation(request_state.api_key_reservation)
            request_state.api_key_reservation = None
            return

        if event_type == "error":
            error = event.error if event else None
            status = "error"
            error_code = _normalize_error_code(
                error.code if error else _websocket_event_error_code(event_type, payload),
                error.type if error else _websocket_event_error_type(event_type, payload),
            )
            error_message = error.message if error else _websocket_event_error_message(event_type, payload)
            error_payload = _upstream_error_from_openai(error)
        elif event_type in {"response.failed", "response.incomplete"}:
            status = "error"
            error = event.response.error if event and event.response else None
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            if event_type == "response.failed":
                error_payload = _upstream_error_from_openai(error)
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id
        elif event_type == "response.completed":
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id

        actual_service_tier = _facade()._service_tier_from_event_payload(payload)
        if actual_service_tier is not None:
            request_state.actual_service_tier = actual_service_tier
            response_service_tier = actual_service_tier

        settlement = _StreamSettlement(
            status=status,
            model=request_state.model or "",
            service_tier=response_service_tier,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=(
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            ),
            error_code=error_code,
            error_message=error_message,
            error=error_payload,
        )
        if event_type in {"response.failed", "response.incomplete", "error"}:
            settlement.record_success = False
        if event_type in {"response.failed", "error"}:
            settlement.account_health_error = _facade()._should_penalize_stream_error(error_code) and not getattr(
                request_state,
                "account_health_error_handled",
                False,
            )
        if request_state.suppressed_duplicate_tool_call and error_code == "stream_incomplete":
            settlement.account_health_error = False
        if (
            error_code == "stream_incomplete"
            and request_state.previous_response_id is not None
            and error_message == "Upstream websocket closed before response.completed"
        ):
            settlement.account_health_error = False
        proxy._cancel_request_state_api_key_reservation_heartbeat(request_state)
        await _release_websocket_response_create_gate(request_state, response_create_gate)
        await proxy._settle_stream_api_key_usage(
            api_key,
            request_state.api_key_reservation,
            settlement,
            response_id,
        )
        if settlement.account_health_error:
            await proxy._handle_stream_error(
                account,
                _stream_settlement_error_payload(settlement),
                settlement.error_code or "upstream_error",
            )
            upstream_control.reconnect_requested = True
            upstream_control.retire_after_drain = True
        elif settlement.record_success:
            await proxy._load_balancer.record_success(account)
            for remembered_response_id in _websocket_continuity_response_ids(request_state, response_id):
                proxy._remember_websocket_previous_response_owner(
                    previous_response_id=remembered_response_id,
                    api_key_id=api_key.id if api_key is not None else None,
                    account_id=account_id_value,
                    session_id=request_state.session_id,
                )

        latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
        )
        if not request_state.skip_request_log:
            request_log_response_id = (
                _websocket_downstream_response_id(request_state) if settlement.record_success else response_id
            )
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_log_response_id,
                model=request_state.model or "",
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=request_state.reasoning_effort,
                transport=request_state.transport,
                service_tier=response_service_tier,
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                latency_first_token_ms=request_state.latency_first_token_ms,
                session_id=request_state.session_id,
                upstream_proxy_route_mode=request_state.upstream_proxy_route_mode,
                upstream_proxy_pool_id=request_state.upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=request_state.upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=(
                    request_state.upstream_proxy_fallback_used if request_state.upstream_proxy_endpoint_id else None
                ),
                upstream_proxy_fail_closed_reason=request_state.upstream_proxy_fail_closed_reason,
                useragent=request_state.useragent,
                useragent_group=request_state.useragent_group,
            )

    async def _write_websocket_connect_failure(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if request_state.skip_request_log:
            return
        await proxy._write_request_log(
            account_id=account_id,
            api_key=api_key,
            request_id=request_state.request_log_id or request_state.request_id,
            model=request_state.model or "",
            latency_ms=int((time.monotonic() - request_state.started_at) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            reasoning_effort=request_state.reasoning_effort,
            transport=request_state.transport,
            service_tier=request_state.service_tier,
            requested_service_tier=request_state.requested_service_tier,
            actual_service_tier=request_state.actual_service_tier,
            latency_first_token_ms=request_state.latency_first_token_ms,
            session_id=request_state.session_id,
            upstream_proxy_route_mode=request_state.upstream_proxy_route_mode,
            upstream_proxy_pool_id=request_state.upstream_proxy_pool_id,
            upstream_proxy_endpoint_id=request_state.upstream_proxy_endpoint_id,
            upstream_proxy_fallback_used=(
                request_state.upstream_proxy_fallback_used if request_state.upstream_proxy_endpoint_id else None
            ),
            upstream_proxy_fail_closed_reason=request_state.upstream_proxy_fail_closed_reason,
            useragent=request_state.useragent,
            useragent_group=request_state.useragent_group,
        )

    async def _emit_websocket_connect_failure(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        status_code: int,
        payload: OpenAIErrorEnvelope,
        error_code: str,
        error_message: str,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        status_code, payload, error_code, error_message = _sanitize_websocket_connect_failure(
            request_state=request_state,
            status_code=status_code,
            payload=payload,
            error_code=error_code,
            error_message=error_message,
        )
        await proxy._release_websocket_request_state_reservation(request_state)
        await proxy._write_websocket_connect_failure(
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            error_code=error_code,
            error_message=error_message,
        )
        response_create_gate = request_state.response_create_gate
        if response_create_gate is not None:
            await _release_websocket_response_create_gate(request_state, response_create_gate)
        async with client_send_lock:
            await websocket.send_text(
                _serialize_websocket_error_event(_wrapped_websocket_error_event(status_code, payload))
            )

    async def _emit_websocket_proxy_request_timeout(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        await proxy._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            status_code=502,
            payload=openai_error(
                "upstream_request_timeout",
                "Proxy request budget exhausted",
                error_type="server_error",
            ),
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )

    async def _fail_pending_websocket_requests(
        self,
        *,
        account: Account | None = None,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
        status: str = "error",
        penalize_account: bool = True,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        async with pending_lock:
            remaining = list(pending_requests)
            pending_requests.clear()

        penalty_code: str | None = None
        penalty_message: str | None = None
        if penalize_account:
            for request_state in remaining:
                request_error_code = request_state.error_code_override or error_code
                if request_error_code in _facade()._TRANSIENT_RETRY_CODES or _facade()._should_penalize_stream_error(
                    request_error_code
                ):
                    penalty_code = request_error_code
                    penalty_message = request_state.error_message_override or error_message
                    break

        if (
            remaining
            and penalize_account
            and account is not None
            and isinstance(account, Account)
            and penalty_code is not None
        ):
            try:
                await proxy._handle_stream_error(account, {"message": penalty_message or error_message}, penalty_code)
            except Exception:
                _facade().logger.warning(
                    "Failed to record websocket pending-request health penalty account_id=%s error_code=%s",
                    account_id_value,
                    penalty_code,
                    exc_info=True,
                )

        last_index = len(remaining) - 1
        for index, request_state in enumerate(remaining):
            proxy._cancel_request_state_api_key_reservation_heartbeat(request_state)
            request_error_code = request_state.error_code_override or error_code
            request_error_message = request_state.error_message_override or error_message
            request_error_type = request_state.error_type_override or "server_error"
            request_error_param = request_state.error_param_override
            (
                request_error_code,
                request_error_message,
                request_error_type,
                request_error_param,
            ) = _sanitize_websocket_terminal_error_fields(
                request_state=request_state,
                error_code=request_error_code,
                error_message=request_error_message,
                error_type=request_error_type,
                error_param=request_error_param,
            )
            if index == last_index:
                _facade()._maybe_dump_oversized_response_create_request(
                    request_state,
                    account_id_value=account_id_value,
                    error_code=request_error_code,
                    error_message=request_error_message,
                )
            if response_create_gate is not None:
                await _release_websocket_response_create_gate(request_state, response_create_gate)
            if request_state.event_queue is not None:
                await request_state.event_queue.put(
                    format_sse_event(
                        response_failed_event(
                            request_error_code,
                            request_error_message,
                            error_type=request_error_type,
                            response_id=_websocket_downstream_response_id(request_state),
                            error_param=request_error_param,
                        )
                    )
                )
                await request_state.event_queue.put(None)
            if websocket is not None and client_send_lock is not None:
                await proxy._emit_websocket_terminal_error(
                    websocket,
                    client_send_lock=client_send_lock,
                    request_state=request_state,
                    error_code=request_error_code,
                    error_message=request_error_message,
                    error_type=request_error_type,
                    error_param=request_error_param,
                    downstream_activity=downstream_activity,
                )
            await proxy._release_websocket_request_state_reservation(request_state)
            if account_id_value is None or request_state.skip_request_log:
                continue
            latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_state.response_id or request_state.request_log_id or request_state.request_id,
                model=request_state.model or "",
                latency_ms=latency_ms,
                status=status,
                error_code=request_error_code,
                error_message=request_error_message,
                reasoning_effort=request_state.reasoning_effort,
                transport=request_state.transport,
                service_tier=request_state.service_tier,
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                latency_first_token_ms=request_state.latency_first_token_ms,
                session_id=request_state.session_id,
                upstream_proxy_route_mode=request_state.upstream_proxy_route_mode,
                upstream_proxy_pool_id=request_state.upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=request_state.upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=(
                    request_state.upstream_proxy_fallback_used if request_state.upstream_proxy_endpoint_id else None
                ),
                upstream_proxy_fail_closed_reason=request_state.upstream_proxy_fail_closed_reason,
                useragent=request_state.useragent,
                useragent_group=request_state.useragent_group,
            )

    async def _emit_websocket_terminal_error(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
        error_type: str = "server_error",
        error_param: str | None = None,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        error_code, error_message, error_type, error_param = _sanitize_websocket_terminal_error_fields(
            request_state=request_state,
            error_code=error_code,
            error_message=error_message,
            error_type=error_type,
            error_param=error_param,
        )
        event = response_failed_event(
            error_code,
            error_message,
            error_type=error_type,
            response_id=_websocket_downstream_response_id(request_state),
            error_param=error_param,
        )
        response_create_gate = request_state.response_create_gate
        if response_create_gate is not None:
            await _release_websocket_response_create_gate(request_state, response_create_gate)
        try:
            await proxy._send_downstream_websocket_text(
                websocket,
                client_send_lock=client_send_lock,
                text=json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                downstream_activity=downstream_activity,
            )
        except Exception:
            _facade().logger.debug("Failed to emit websocket terminal error", exc_info=True)

    async def _send_downstream_websocket_text(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        text: str,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if downstream_activity is not None:
            downstream_activity.mark()
        async with client_send_lock:
            if downstream_activity is not None:
                downstream_activity.mark()
            await websocket.send_text(text)
            if downstream_activity is not None:
                downstream_activity.mark()

    async def _send_downstream_websocket_bytes(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        data: bytes,
        downstream_activity: _DownstreamWebSocketActivity | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if downstream_activity is not None:
            downstream_activity.mark()
        async with client_send_lock:
            if downstream_activity is not None:
                downstream_activity.mark()
            await websocket.send_bytes(data)
            if downstream_activity is not None:
                downstream_activity.mark()
