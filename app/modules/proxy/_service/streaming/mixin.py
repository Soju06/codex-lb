# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

import asyncio
import inspect
import sys
import time
from typing import Any, AsyncIterator, Mapping, cast

import aiohttp

from app.core.balancer import (
    PERMANENT_FAILURE_CODES,
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
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_CODE as PREVIOUS_RESPONSE_STALE_CODE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_MESSAGE as PREVIOUS_RESPONSE_STALE_MESSAGE,
)
from app.core.errors import (
    response_failed_event,
)
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME as CODEX_KEEPALIVE_FRAME  # noqa: F401
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow as utcnow
from app.db.models import (
    Account,
    AccountStatus,  # noqa: F401
)
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyUsageReservationData,
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
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
from app.modules.proxy._service.streaming.retry import _StreamingRetryMixin
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_WEBSOCKET,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _ApiKeyReservationTouchState,
    _event_type_from_payload,
    _RequestLogFailureMetadata,
    _RetryableStreamError,
    _StreamSettlement,
    _TerminalStreamError,
    _TransientStreamError,
    _WebSocketUpstreamControl,
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
    _owner_lookup_session_id_from_headers,
    _sticky_key_from_session_header,  # noqa: F401
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _header_account_id,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
    classify_upstream_failure,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext as HTTPBridgeForwardContext,
)
from app.modules.proxy.http_bridge_forwarding import (
    OwnerForwardRelayFailure as OwnerForwardRelayFailure,
)
from app.modules.proxy.load_balancer import AccountLease, AccountSelection
from app.modules.proxy.tool_call_dedupe import (
    mark_duplicate_tool_call_downstream_event,
    rewrite_parallel_tool_call_sse_line,
)
from app.modules.proxy.tool_call_dedupe import (
    response_id_from_payload as tool_call_response_id_from_payload,
)
from app.modules.proxy.work_admission import AdmissionLease


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


_REQUEST_TRANSPORT_HTTP = "http"


class _StreamingMixin(_StreamingRetryMixin):
    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        propagate_http_errors: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        suppress_text_done_events: bool = False,
        request_transport: str = _REQUEST_TRANSPORT_HTTP,
    ) -> AsyncIterator[str]:
        proxy = cast(_StreamingServiceProtocol, self)
        _maybe_log_proxy_request_payload("stream", payload, headers)
        filtered = _facade().filter_inbound_headers(headers)
        return proxy._stream_with_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            request_transport=request_transport,
        )

    async def _resolve_upstream_route_for_account(
        self,
        account: Account,
        *,
        operation: str,
    ) -> ResolvedUpstreamRoute | None:
        proxy = cast(_StreamingServiceProtocol, self)
        async with _facade().SessionLocal() as session:
            return await _facade().resolve_upstream_route(
                session,
                account_id=account.id,
                operation=operation,
                scope="account",
                encryptor=proxy._encryptor,
            )

    async def _select_account_with_budget_for_stream(self, deadline: float, **kwargs: Any) -> AccountSelection:
        proxy = cast(_StreamingServiceProtocol, self)
        selector = proxy._select_account_with_budget_compatible
        optional_kwargs = (
            "require_security_work_authorized",
            "lease_kind",
            "estimated_lease_tokens",
            "fallback_on_preferred_account_unavailable",
        )
        if any(name in kwargs for name in optional_kwargs):
            try:
                signature = inspect.signature(selector)
            except (TypeError, ValueError):
                signature = None
            accepts_var_keyword = signature is not None and any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
            )
            if signature is not None and not accepts_var_keyword:
                kwargs = dict(kwargs)
                for name in optional_kwargs:
                    if name not in signature.parameters:
                        kwargs.pop(name, None)
        return await selector(deadline, **kwargs)

    async def _stream_once(
        self,
        account: Account,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        request_id: str,
        allow_retry: bool,
        *,
        request_started_at: float,
        allow_transient_retry: bool = False,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: _StreamSettlement,
        suppress_text_done_events: bool,
        upstream_stream_transport: str | None,
        request_transport: str,
        useragent: str | None = None,
        useragent_group: str | None = None,
        preferred_account_id: str | None = None,
        tool_call_dedupe: _WebSocketUpstreamControl | None = None,
    ) -> AsyncIterator[str]:
        proxy = cast(_StreamingServiceProtocol, self)
        account_id_value = account.id
        access_token = proxy._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        model = payload.model
        requested_service_tier = payload.service_tier
        service_tier = requested_service_tier
        actual_service_tier: str | None = None
        reasoning_effort = payload.reasoning.effort if payload.reasoning else None
        session_id = _owner_lookup_session_id_from_headers(headers)
        start = time.monotonic()
        status = "success"
        error_code = None
        error_message = None
        failure_metadata = _RequestLogFailureMetadata()
        response_id = request_id
        usage = None
        route: ResolvedUpstreamRoute | None = None
        route_trace = UpstreamProxyRouteTrace()
        route_fail_closed_reason: str | None = None
        saw_text_delta = False
        latency_first_token_ms: int | None = None
        if tool_call_dedupe is None:
            tool_call_dedupe = _WebSocketUpstreamControl()
        suppressed_duplicate_tool_call = False
        response_create_lease = AdmissionLease(None, stage="response_create", request_id=request_id)
        account_response_create_lease: AccountLease | None = None
        api_key_reservation_touch_state = _ApiKeyReservationTouchState(last_touch_at=start)
        api_key_reservation_heartbeat_stop = asyncio.Event()
        api_key_reservation_heartbeat_task: asyncio.Task[None] | None = None
        if api_key_reservation is not None:
            api_key_reservation_heartbeat_task = asyncio.create_task(
                proxy._run_api_key_reservation_heartbeat(
                    api_key=api_key,
                    reservation=api_key_reservation,
                    touch_state=api_key_reservation_touch_state,
                    request_id=request_id,
                    surface="stream",
                    stop_event=api_key_reservation_heartbeat_stop,
                )
            )

        try:
            route = await proxy._resolve_upstream_route_for_account(account, operation="responses")
            account_response_create_lease = await proxy._acquire_account_response_create_lease_or_overload(
                account_id=account.id,
                request_id=request_id,
                surface="stream",
            )
            response_create_lease = await proxy._get_work_admission().acquire_response_create()
            if upstream_stream_transport is not None:
                stream = _facade()._call_stream_with_supported_optional_kwargs(
                    _facade().core_stream_responses,
                    payload,
                    headers,
                    access_token,
                    account_id,
                    optional_kwargs={
                        "route": route,
                        "allow_direct_egress": route is None,
                        "route_trace": route_trace,
                        "upstream_stream_transport_override": upstream_stream_transport,
                    },
                    raise_for_status=True,
                )
            else:
                stream = _facade()._call_stream_with_supported_optional_kwargs(
                    _facade().core_stream_responses,
                    payload,
                    headers,
                    access_token,
                    account_id,
                    optional_kwargs={
                        "route": route,
                        "allow_direct_egress": route is None,
                        "route_trace": route_trace,
                    },
                    raise_for_status=True,
                )
            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                response_create_lease.release()
                await proxy._load_balancer.release_account_lease(account_response_create_lease)
                account_response_create_lease = None
                status = "error"
                error_code = "stream_incomplete"
                error_message = "Upstream websocket closed before response.completed"
                settlement.record_success = False
                settlement.account_health_error = True
                settlement.error = {"message": error_message}
                yield format_sse_event(
                    response_failed_event(
                        error_code,
                        error_message,
                        response_id=request_id,
                    )
                )
                return
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                response_create_lease.release()
                await proxy._load_balancer.release_account_lease(account_response_create_lease)
                account_response_create_lease = None
                status = "error"
                error_code = "upstream_unavailable"
                error_message = str(exc) or "Request to upstream timed out"
                settlement.record_success = False
                settlement.account_health_error = True
                settlement.error = {"message": error_message}
                if allow_retry:
                    raise _RetryableStreamError(error_code, settlement.error, exclude_account=True)
                yield format_sse_event(
                    response_failed_event(
                        error_code,
                        error_message,
                        response_id=request_id,
                    )
                )
                return
            response_create_lease.release()
            await proxy._load_balancer.release_account_lease(account_response_create_lease)
            account_response_create_lease = None
            first_payload = parse_sse_data_json(first)
            event = parse_sse_event(first)
            event_type = _event_type_from_payload(event, first_payload)
            if event_type not in {"response.completed", "response.failed", "response.incomplete", "error"}:
                api_key_reservation_touch_state.last_touch_at = await proxy._maybe_touch_api_key_reservation(
                    api_key=api_key,
                    reservation=api_key_reservation,
                    last_touch_at=api_key_reservation_touch_state.last_touch_at,
                    request_id=request_id,
                    surface="stream",
                )
            event_service_tier = _facade()._service_tier_from_event_payload(first_payload)
            if event_service_tier is not None:
                actual_service_tier = event_service_tier
                service_tier = event_service_tier
            if event and event.response and event.response.id:
                response_id = event.response.id
                settlement.response_id = response_id
            terminal_stream_error: _TerminalStreamError | None = None
            if event and event.type in ("response.failed", "error"):
                if event.type == "response.failed":
                    response = event.response
                    error = response.error if response else None
                else:
                    error = event.error
                response_id = (
                    event.response.id
                    if event.type == "response.failed" and event.response and event.response.id
                    else request_id
                )
                code = _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                if (
                    event_type == "error"
                    and code == "error"
                    and _websocket_event_error_code(event_type, first_payload) is None
                ):
                    code = "upstream_error"
                rewritten_error = _facade()._rewrite_previous_response_stream_error(
                    previous_response_id=payload.previous_response_id,
                    preferred_account_id=preferred_account_id,
                    error_code=code,
                    error_type=error.type if error else None,
                    error_message=error.message if error else None,
                    error_param=error.param if error else None,
                )
                status = "error"
                settlement.error = _upstream_error_from_openai(error)
                settlement.record_success = False
                if rewritten_error is not None:
                    rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                    if upstream_error_code is not None:
                        await proxy._handle_stream_error(
                            account,
                            settlement.error,
                            upstream_error_code,
                        )
                    first, event, first_payload, event_type = _facade()._build_rewritten_stream_response_failed_event(
                        response_id=response_id,
                        error_code=rewritten_code,
                        error_message=rewritten_message,
                    )
                    error_code = rewritten_code
                    error_message = rewritten_message
                    settlement.account_health_error = False
                else:
                    error_code = code
                    error_message = error.message if error else None
                    settlement.account_health_error = _facade()._should_penalize_stream_error(code)
                    if allow_retry and code == "stream_idle_timeout":
                        raise _RetryableStreamError(code, settlement.error, exclude_account=True)
                    if allow_retry and _facade()._is_security_work_authorization_required_error(code, error_message):
                        error_code = _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE
                        raise _RetryableStreamError(
                            _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                            settlement.error,
                        )
                    if allow_retry and _facade()._should_retry_stream_error(code):
                        raise _RetryableStreamError(code, settlement.error, exclude_account=True)
                    if allow_transient_retry and _facade()._should_retry_transient_stream_error(code, error_message):
                        raise _TransientStreamError(code, settlement.error)
                terminal_stream_error = _TerminalStreamError(
                    error_code or code,
                    settlement.error,
                )
                if allow_retry:
                    _facade().logger.info(
                        "Not retrying non-recoverable stream failure request_id=%s account_id=%s code=%s",
                        request_id,
                        account_id_value,
                        error_code or code,
                    )

            if event and event.type in ("response.completed", "response.incomplete"):
                usage = event.response.usage if event.response else None
                if event.response and event.response.id:
                    response_id = event.response.id
                if event.type == "response.incomplete":
                    status = "error"

            if event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                saw_text_delta = True
            if not _facade()._should_suppress_text_done_event(
                event_type=event_type,
                payload=first_payload,
                suppress_text_done_events=suppress_text_done_events,
                saw_text_delta=saw_text_delta,
            ):
                first, first_payload, event, event_type = rewrite_parallel_tool_call_sse_line(first, first_payload)
                if mark_duplicate_tool_call_downstream_event(
                    first_payload,
                    seen_tool_call_keys=tool_call_dedupe.seen_tool_call_keys,
                    response_id=tool_call_response_id_from_payload(first_payload) or request_id,
                    scope_side_effects_by_response_id=False,
                ):
                    suppressed_duplicate_tool_call = True
                else:
                    if first_payload is not None:
                        first = format_sse_event(first_payload)
                    if latency_first_token_ms is None and event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                        latency_first_token_ms = int((time.monotonic() - request_started_at) * 1000)
                    settlement.downstream_visible = True
                    if event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                        settlement.downstream_text_visible = True
                    yield first
            if terminal_stream_error is not None:
                raise terminal_stream_error

            async for line in iterator:
                event_payload = parse_sse_data_json(line)
                event = parse_sse_event(line)
                event_type = _event_type_from_payload(event, event_payload)
                if event_type == "error" and (event is None or event.error is None) and isinstance(event_payload, dict):
                    message_value = event_payload.get("message")
                    message = (
                        message_value.strip()
                        if isinstance(message_value, str) and message_value.strip()
                        else "Upstream error"
                    )
                    line, event, event_payload, event_type = _facade()._build_rewritten_stream_response_failed_event(
                        response_id=response_id,
                        error_code="upstream_error",
                        error_message=message,
                    )
                if event_type not in {"response.completed", "response.failed", "response.incomplete", "error"}:
                    api_key_reservation_touch_state.last_touch_at = await proxy._maybe_touch_api_key_reservation(
                        api_key=api_key,
                        reservation=api_key_reservation,
                        last_touch_at=api_key_reservation_touch_state.last_touch_at,
                        request_id=request_id,
                        surface="stream",
                    )
                event_service_tier = _facade()._service_tier_from_event_payload(event_payload)
                if event_service_tier is not None:
                    actual_service_tier = event_service_tier
                    service_tier = event_service_tier
                line, event_payload, event, event_type = rewrite_parallel_tool_call_sse_line(line, event_payload)
                if event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                    saw_text_delta = True
                if _facade()._should_suppress_text_done_event(
                    event_type=event_type,
                    payload=event_payload,
                    suppress_text_done_events=suppress_text_done_events,
                    saw_text_delta=saw_text_delta,
                ):
                    continue
                if event:
                    if event_type in ("response.failed", "error"):
                        status = "error"
                        if event_type == "response.failed":
                            response = event.response
                            error = response.error if response else None
                            if response and response.id:
                                response_id = response.id
                                settlement.response_id = response_id
                        else:
                            error = event.error
                        raw_error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        if (
                            event_type == "error"
                            and raw_error_code == "error"
                            and _websocket_event_error_code(event_type, event_payload) is None
                        ):
                            raw_error_code = "upstream_error"
                        rewritten_error = _facade()._rewrite_previous_response_stream_error(
                            previous_response_id=payload.previous_response_id,
                            preferred_account_id=preferred_account_id,
                            error_code=raw_error_code,
                            error_type=error.type if error else None,
                            error_message=error.message if error else None,
                            error_param=error.param if error else None,
                        )
                        if rewritten_error is not None:
                            response_id = (
                                event.response.id
                                if event_type == "response.failed" and event.response and event.response.id
                                else request_id
                            )
                            rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                            if upstream_error_code is not None:
                                await proxy._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    upstream_error_code,
                                )
                            (
                                line,
                                event,
                                event_payload,
                                event_type,
                            ) = _facade()._build_rewritten_stream_response_failed_event(
                                response_id=response_id,
                                error_code=rewritten_code,
                                error_message=rewritten_message,
                            )
                            error_code = rewritten_code
                            error_message = rewritten_message
                            settlement.error = _upstream_error_from_openai(error)
                            settlement.record_success = False
                            settlement.account_health_error = False
                        else:
                            error_code = raw_error_code
                            error_message = error.message if error else None
                            settlement.error = _upstream_error_from_openai(error)
                            settlement.record_success = False
                            settlement.account_health_error = (
                                _facade()._should_penalize_stream_error(error_code) and not saw_text_delta
                            )
                    if event_type in ("response.completed", "response.incomplete"):
                        response = event.response if event is not None else None
                        usage = response.usage if response else None
                        if response and response.id:
                            response_id = response.id
                            settlement.response_id = response_id
                        if event_type == "response.incomplete":
                            status = "error"
                    if event_type == "response.completed" and suppressed_duplicate_tool_call:
                        (
                            line,
                            event,
                            event_payload,
                            event_type,
                        ) = _facade()._build_rewritten_stream_response_failed_event(
                            response_id=response_id,
                            error_code="stream_incomplete",
                            error_message=_facade()._SUPPRESSED_DUPLICATE_TOOL_CALL_MESSAGE,
                        )
                        status = "error"
                        error_code = "stream_incomplete"
                        error_message = _facade()._SUPPRESSED_DUPLICATE_TOOL_CALL_MESSAGE
                        settlement.record_success = False
                        settlement.account_health_error = False
                if latency_first_token_ms is None and event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                    latency_first_token_ms = int((time.monotonic() - request_started_at) * 1000)
                if mark_duplicate_tool_call_downstream_event(
                    event_payload,
                    seen_tool_call_keys=tool_call_dedupe.seen_tool_call_keys,
                    response_id=tool_call_response_id_from_payload(event_payload) or request_id,
                    scope_side_effects_by_response_id=False,
                ):
                    suppressed_duplicate_tool_call = True
                    continue
                if event_payload is not None:
                    line = format_sse_event(event_payload)
                settlement.downstream_visible = True
                if event_type in _facade()._TEXT_DELTA_EVENT_TYPES:
                    settlement.downstream_text_visible = True
                yield line
        except ProxyResponseError as exc:
            response_create_lease.release()
            failure_metadata = _facade()._request_log_failure_metadata(exc)
            error = _parse_openai_error(exc.payload)
            rewritten_error = _facade()._rewrite_previous_response_stream_error(
                previous_response_id=payload.previous_response_id,
                preferred_account_id=preferred_account_id,
                error_code=_normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                ),
                error_type=error.type if error else None,
                error_message=error.message if error else None,
                error_param=error.param if error else None,
            )
            if rewritten_error is not None:
                rewritten_code, rewritten_message, upstream_error_code = rewritten_error
                if upstream_error_code is not None:
                    await proxy._handle_stream_error(
                        account,
                        _upstream_error_from_openai(error),
                        upstream_error_code,
                    )
                status = "error"
                error_code = rewritten_code
                error_message = rewritten_message
                settlement.record_success = False
                settlement.account_health_error = False
                yield _facade()._build_rewritten_stream_response_failed_event(
                    response_id=request_id,
                    error_code=rewritten_code,
                    error_message=rewritten_message,
                )[0]
                return
            status = "error"
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error else None
            settlement.record_success = False
            settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
            raise
        except UpstreamProxyRouteError as exc:
            route_fail_closed_reason = exc.reason
            status = "error"
            error_code = "upstream_proxy_unavailable"
            error_message = f"Upstream proxy route unavailable: {exc.reason}"
            settlement.record_success = False
            settlement.account_health_error = False
            settlement.error = {"message": error_message}
            yield format_sse_event(
                response_failed_event(
                    "upstream_proxy_unavailable",
                    error_message,
                    response_id=request_id,
                )
            )
            return
        finally:
            api_key_reservation_heartbeat_stop.set()
            if api_key_reservation_heartbeat_task is not None:
                proxy._cancel_api_key_reservation_heartbeat_task(api_key_reservation_heartbeat_task)
            response_create_lease.release()
            await proxy._load_balancer.release_account_lease(account_response_create_lease)
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
            settlement.status = status
            settlement.model = model
            settlement.service_tier = service_tier
            settlement.input_tokens = input_tokens
            settlement.output_tokens = output_tokens
            settlement.cached_input_tokens = cached_input_tokens
            settlement.error_code = error_code
            settlement.error_message = error_message
            upstream_proxy_route_mode = route_trace.mode or (route.mode if route is not None else None)
            upstream_proxy_pool_id = route_trace.pool_id or (route.pool_id if route is not None else None)
            upstream_proxy_endpoint_id = route_trace.endpoint_id or (route.endpoint_id if route is not None else None)
            upstream_proxy_fallback_used = (
                route_trace.fallback_used
                if route_trace.endpoint_id is not None
                else (False if route is not None else None)
            )
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=response_id,
                model=model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                transport=request_transport,
                upstream_transport=upstream_stream_transport,
                service_tier=service_tier,
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                latency_first_token_ms=latency_first_token_ms,
                session_id=session_id,
                failure_phase=failure_metadata.failure_phase,
                failure_detail=failure_metadata.failure_detail,
                failure_exception_type=failure_metadata.failure_exception_type,
                upstream_status_code=failure_metadata.upstream_status_code,
                upstream_error_code=failure_metadata.upstream_error_code,
                bridge_stage=failure_metadata.bridge_stage,
                upstream_proxy_route_mode=upstream_proxy_route_mode,
                upstream_proxy_pool_id=upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                upstream_proxy_fail_closed_reason=route_fail_closed_reason,
                useragent=useragent,
                useragent_group=useragent_group,
            )
            _maybe_log_proxy_service_tier_trace(
                "stream",
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
            )

    async def _handle_stream_error(
        self,
        account: Account,
        error: UpstreamError,
        code: str,
        http_status: int | None = None,
    ) -> ClassifiedFailure:
        proxy = cast(_StreamingServiceProtocol, self)
        classified = classify_upstream_failure(
            error_code=code,
            error=error,
            http_status=http_status,
            phase="first_event",
        )
        if _facade()._is_account_neutral_error_code(code):
            return classified
        if classified["failure_class"] == "rate_limit":
            await proxy._load_balancer.mark_rate_limit(account, error)
        elif classified["failure_class"] == "quota":
            await proxy._load_balancer.mark_quota_exceeded(account, error)
        elif code in PERMANENT_FAILURE_CODES:
            await proxy._load_balancer.mark_permanent_failure(account, code)
        else:
            await proxy._load_balancer.record_error(account)
            _facade().logger.info(
                "Recorded transient account error account_id=%s request_id=%s code=%s",
                account.id,
                get_request_id(),
                code,
            )
        return classified
