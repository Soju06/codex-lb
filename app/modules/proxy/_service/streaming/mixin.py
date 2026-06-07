# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

import asyncio
import inspect
import sys
import time
from typing import Any, AsyncIterator, Mapping, cast

import aiohttp

from app.core.auth.refresh import (
    RefreshError,
)
from app.core.balancer import (
    PERMANENT_FAILURE_CODES,
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
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_CODE as PREVIOUS_RESPONSE_STALE_CODE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_MESSAGE as PREVIOUS_RESPONSE_STALE_MESSAGE,
)
from app.core.errors import (
    openai_error,
    response_failed_event,
)
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.retry import backoff_seconds
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
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_WEBSOCKET,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _ApiKeyReservationTouchState,
    _event_type_from_payload,
    _request_log_useragent_fields,
    _RequestLogFailureMetadata,
    _RetryableStreamError,
    _stream_settlement_error_payload,
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
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,  # noqa: F401
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _apply_error_metadata,
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


class _StreamingMixin:
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

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        request_transport: str,
        rewritten_file_account_id: str | None = None,
        upstream_stream_transport_override: str | None = None,
    ) -> AsyncIterator[str]:
        proxy = cast(_StreamingServiceProtocol, self)
        useragent, useragent_group = _request_log_useragent_fields(headers)
        request_id = ensure_request_id()
        start = time.monotonic()
        base_settings = _facade().get_settings()
        settings = await _facade().get_settings_cache().get()
        deadline = start + _facade()._stream_request_budget_seconds(
            base_settings,
            request_transport=request_transport,
        )
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        upstream_stream_transport = upstream_stream_transport_override
        if upstream_stream_transport is None:
            upstream_stream_transport = _facade()._resolve_upstream_stream_transport(settings.upstream_stream_transport)
        if request_transport == _REQUEST_TRANSPORT_HTTP and upstream_stream_transport == "websocket":
            # HTTP/SSE clients can retry a half-rendered turn after an upstream
            # websocket close, making the same visible message restart. Keep
            # native websocket clients on their dedicated path, but use upstream
            # HTTP/SSE for downstream HTTP streams.
            upstream_stream_transport = "http"
        if rewritten_file_account_id is None:
            proxy._raise_for_unsupported_input_image_references(payload)
            rewritten_file_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
            api_key=api_key,
        )
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = "session_header"
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )
        routing_strategy = _facade()._routing_strategy(settings)
        max_attempts = _facade()._STREAM_MAX_ACCOUNT_ATTEMPTS
        settled = False
        any_attempt_logged = False
        settlement = _StreamSettlement()
        last_transient_exc: ProxyResponseError | None = None
        last_security_work_retry_error: _RetryableStreamError | None = None
        excluded_account_ids: set[str] = set()
        preferred_account_id: str | None = None
        file_preferred_account_id: str | None = rewritten_file_account_id
        require_preferred_account = False
        last_retryable_stream_error: _RetryableStreamError | None = None
        require_security_work_authorized = False
        account_leases: list[AccountLease] = []
        estimated_lease_tokens = _facade()._estimated_lease_tokens_from_request_usage_budget(
            estimate_api_key_request_usage(payload)
        )

        async def _release_tracked_stream_lease(lease: AccountLease | None) -> None:
            if lease is None:
                return
            try:
                account_leases.remove(lease)
            except ValueError:
                pass
            await proxy._load_balancer.release_account_lease(lease)

        try:
            if payload.previous_response_id is not None:
                previous_response_lookup_session_id = _owner_lookup_session_id_from_headers(headers)
                preferred_account_id = await proxy._resolve_websocket_previous_response_owner(
                    previous_response_id=payload.previous_response_id,
                    api_key=api_key,
                    session_id=previous_response_lookup_session_id,
                    surface="http_stream",
                )
                require_preferred_account = preferred_account_id is not None
                # `previous_response_id` is a stored-object continuation, so it
                # remains hard owner-bound even when the request also carries a
                # soft prompt-cache affinity key. A different account may have a
                # warmer cache, but it cannot safely resolve the stored response.
                if preferred_account_id is None:
                    selection_inputs = await proxy._load_balancer._load_selection_inputs(
                        model=payload.model,
                        additional_limit_name=None,
                        account_ids=None,
                    )
                    if len(selection_inputs.accounts) != 1:
                        message = "Previous response owner account is unavailable; retry later."
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason="owner_account_unavailable",
                            previous_response_id=payload.previous_response_id,
                            session_id=previous_response_lookup_session_id,
                            upstream_error_code="owner_lookup_miss",
                        )
                        event = response_failed_event(
                            "previous_response_owner_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code="previous_response_owner_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        return
            file_required_preferred_account = False
            if preferred_account_id is None:
                # ``input_file.file_id`` references must land on the account
                # that registered the upload; otherwise upstream rejects the
                # request with not-found / 401. The helper itself enforces
                # priority -- it returns ``None`` when stronger affinity
                # signals (prompt_cache_key / session header / turn_state
                # header) are present, so this never overrides them.
                if rewritten_file_account_id is not None:
                    preferred_account_id = rewritten_file_account_id
                    file_required_preferred_account = True
            if preferred_account_id is None:
                resolved_file_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
                if resolved_file_account_id is not None:
                    file_preferred_account_id = resolved_file_account_id
                    preferred_account_id = resolved_file_account_id
                    file_required_preferred_account = True
            for attempt in range(max_attempts):
                remaining_budget = _facade()._remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    _facade().logger.warning(
                        "Proxy request budget exhausted before retry request_id=%s attempt=%s",
                        request_id,
                        attempt + 1,
                    )
                    await proxy._write_stream_preflight_error(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        start=start,
                        error_code="upstream_request_timeout",
                        error_message="Proxy request budget exhausted",
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        service_tier=payload.service_tier,
                        transport=request_transport,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
                    yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                    return
                while True:
                    try:
                        selection = await proxy._select_account_with_budget_compatible(
                            deadline,
                            request_id=request_id,
                            kind="stream",
                            api_key=api_key,
                            sticky_key=affinity.key,
                            sticky_kind=affinity.kind,
                            reallocate_sticky=affinity.reallocate_sticky,
                            sticky_max_age_seconds=affinity.max_age_seconds,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            prefer_earlier_reset_window=_facade()._prefer_earlier_reset_window(settings),
                            routing_strategy=routing_strategy,
                            model=payload.model,
                            exclude_account_ids=excluded_account_ids,
                            preferred_account_id=preferred_account_id,
                            require_security_work_authorized=require_security_work_authorized,
                            lease_kind="stream",
                            estimated_lease_tokens=estimated_lease_tokens,
                            fallback_on_preferred_account_unavailable=not file_required_preferred_account,
                        )
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                        error_message = error.message if error else None
                        if _facade()._is_proxy_budget_exhausted_error(exc):
                            await proxy._write_stream_preflight_error(
                                account_id=None,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                            )
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        event = response_failed_event(
                            error_code,
                            error_message or "Upstream unavailable",
                            error_type=(error.type or "server_error") if error else "server_error",
                            response_id=request_id,
                        )
                        _apply_error_metadata(event["response"]["error"], error)
                        yield format_sse_event(event)
                        return
                    account = selection.account
                    current_account_lease = selection.lease
                    if selection.lease is not None:
                        account_leases.append(selection.lease)
                    if (
                        not account
                        and require_security_work_authorized
                        and selection.error_code == _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
                    ):
                        _facade().logger.info(
                            "No security-work-authorized account available for stream retry; "
                            "continuing normal account failover request_id=%s",
                            request_id,
                        )
                        yield format_sse_event(
                            _facade()._security_work_advisory_event(
                                code=_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                                message=_facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                                request_id=request_id,
                                action="continue_normal_selection",
                            )
                        )
                        require_security_work_authorized = False
                        continue
                    break
                if not account:
                    if _facade()._is_local_account_cap_code(selection.error_code):
                        raise ProxyResponseError(
                            429,
                            openai_error(
                                selection.error_code or "account_stream_cap",
                                selection.error_message or "Account stream capacity is exhausted",
                                error_type="rate_limit_error",
                            ),
                        )
                    if require_preferred_account and preferred_account_id is not None:
                        message = "Previous response owner account is unavailable; retry later."
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason="owner_account_unavailable",
                            previous_response_id=payload.previous_response_id,
                            session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                            upstream_error_code="no_accounts",
                        )
                        event = response_failed_event(
                            "previous_response_owner_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=preferred_account_id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code="previous_response_owner_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        return
                    # If a prior attempt stored a transient 500 and the caller
                    # expects HTTP error propagation, re-raise the original error
                    # instead of returning a generic no_accounts event.
                    if propagate_http_errors and last_transient_exc is not None:
                        raise last_transient_exc
                    if last_retryable_stream_error is not None:
                        error_message = str(last_retryable_stream_error.error.get("message") or "Upstream error")
                        event = response_failed_event(
                            last_retryable_stream_error.code,
                            error_message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=last_retryable_stream_error.code,
                            error_message=error_message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        return
                    if last_security_work_retry_error is not None:
                        message = (
                            last_security_work_retry_error.error.get("message")
                            or "Security work authorization is required"
                        )
                        event = response_failed_event(
                            last_security_work_retry_error.code,
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=last_security_work_retry_error.code,
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        return
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    error_code = selection.error_code or "no_accounts"
                    event = response_failed_event(
                        error_code,
                        no_accounts_msg,
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await proxy._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code=error_code,
                        error_message=no_accounts_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
                    return

                account_id_value = account.id
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and account.id != preferred_account_id
                ):
                    message = "Previous response owner account is unavailable; retry later."
                    _record_continuity_fail_closed(
                        surface="http_stream",
                        reason="owner_account_unavailable",
                        previous_response_id=payload.previous_response_id,
                        session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                        upstream_error_code="upstream_unavailable",
                    )
                    event = response_failed_event(
                        "previous_response_owner_unavailable",
                        message,
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await proxy._write_request_log(
                        account_id=preferred_account_id,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code="previous_response_owner_unavailable",
                        error_message=message,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
                    return
                try:
                    remaining_budget = _facade()._remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        _facade().logger.warning(
                            "Proxy request budget exhausted before freshness check "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                        return
                    try:
                        account = await proxy._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                    except UpstreamProxyRouteError as exc:
                        message = f"Upstream proxy route unavailable: {exc.reason}"
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_proxy_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            upstream_proxy_fail_closed_reason=exc.reason,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        event = response_failed_event(
                            "upstream_proxy_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        _facade().logger.warning(
                            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                            exc_info=True,
                        )
                        message = str(exc) or "Request to upstream timed out"
                        if (
                            not require_preferred_account
                            and preferred_account_id is None
                            and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                            and attempt + 1 < max_attempts
                        ):
                            await proxy._handle_stream_error(
                                account,
                                {"message": message},
                                "upstream_unavailable",
                            )
                            last_retryable_stream_error = _RetryableStreamError(
                                "upstream_unavailable",
                                {"message": message},
                                exclude_account=True,
                            )
                            excluded_account_ids.add(account.id)
                            continue
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        event = response_failed_event(
                            "upstream_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    any_attempt_logged = True
                    settlement = _StreamSettlement()
                    tool_call_dedupe = _WebSocketUpstreamControl()
                    effective_attempt_timeout = _facade()._remaining_budget_seconds(deadline)
                    if effective_attempt_timeout <= 0:
                        _facade().logger.warning(
                            "Proxy request budget exhausted before stream attempt "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                        return
                    transient_retries = 0
                    allow_retry_flag = attempt < max_attempts - 1
                    while True:
                        stream_timeout_tokens = _facade()._push_stream_attempt_timeout_overrides(
                            _facade()._remaining_budget_seconds(deadline),
                        )
                        try:
                            settlement = _StreamSettlement()
                            async for line in proxy._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                allow_retry_flag,
                                request_started_at=start,
                                allow_transient_retry=(
                                    transient_retries < _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES - 1
                                    or allow_retry_flag
                                ),
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                preferred_account_id=preferred_account_id,
                                tool_call_dedupe=tool_call_dedupe,
                            ):
                                yield line
                        except (_TransientStreamError, ProxyResponseError) as tex:
                            if settlement.downstream_visible:
                                failed_response_id = settlement.response_id or request_id
                                if isinstance(tex, ProxyResponseError):
                                    error = _parse_openai_error(tex.payload)
                                    error_code = _normalize_error_code(
                                        error.code if error else None,
                                        error.type if error else None,
                                    )
                                    error_message = error.message if error else "Upstream error"
                                    error_type = error.type if error else None
                                    error_param = error.param if error else None
                                    event = response_failed_event(
                                        error_code or "upstream_error",
                                        error_message or "Upstream error",
                                        error_type=error_type or "server_error",
                                        response_id=failed_response_id,
                                        error_param=error_param,
                                    )
                                    _apply_error_metadata(event["response"]["error"], error)
                                else:
                                    error_code = tex.code
                                    error_message = str(tex.error.get("message") or "Upstream error")
                                    event = response_failed_event(
                                        error_code or "upstream_error",
                                        error_message,
                                        response_id=failed_response_id,
                                    )
                                _facade().logger.warning(
                                    "Surfacing mid-stream upstream failure without replay "
                                    "request_id=%s account_id=%s code=%s",
                                    request_id,
                                    account.id,
                                    error_code,
                                )
                                yield format_sse_event(event)
                                settlement.record_success = False
                                settlement.error_code = error_code
                                settlement.error_message = error_message
                                if isinstance(tex, ProxyResponseError):
                                    settlement.error = _upstream_error_from_openai(error)
                                else:
                                    settlement.error = tex.error
                                settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
                                if settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                    )
                                settled = await proxy._settle_stream_api_key_usage(
                                    api_key,
                                    api_key_reservation,
                                    settlement,
                                    request_id,
                                )
                                return
                            if isinstance(tex, ProxyResponseError) and tex.status_code != 500:
                                error = _parse_openai_error(tex.payload)
                                code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error else None
                                if _facade()._is_security_work_authorization_required_error(code, error_message):
                                    if (
                                        account.security_work_authorized
                                        or account.id == file_preferred_account_id
                                        or require_preferred_account
                                        or attempt >= max_attempts - 1
                                    ):
                                        raise
                                    _facade().logger.info(
                                        "Retrying on security-work-authorized account request_id=%s account_id=%s",
                                        request_id,
                                        account.id,
                                    )
                                    yield format_sse_event(
                                        _facade()._security_work_advisory_event(
                                            code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                            message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                            request_id=request_id,
                                            action="retry_security_work_authorized",
                                            account_id=account.id,
                                        )
                                    )
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    require_security_work_authorized = True
                                    last_security_work_retry_error = _RetryableStreamError(
                                        _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                        _upstream_error_from_openai(error),
                                    )
                                    last_transient_exc = tex
                                    break
                                if code == "account_response_create_cap":
                                    last_transient_exc = tex
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    break
                                if _facade()._is_account_neutral_error_code(code):
                                    raise
                                classified = await proxy._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    code,
                                    http_status=tex.status_code,
                                )
                                if getattr(base_settings, "deterministic_failover_enabled", True):
                                    action = failover_decision(
                                        failure_class=classified["failure_class"],
                                        downstream_visible=settlement.downstream_visible,
                                        candidates_remaining=max_attempts - attempt - 1,
                                    )
                                else:
                                    action = "surface"
                                _facade().logger.info(
                                    "Failover decision request_id=%s transport=stream account_id=%s "
                                    "attempt=%d failure_class=%s action=%s",
                                    request_id,
                                    account.id,
                                    attempt + 1,
                                    classified["failure_class"],
                                    action,
                                )
                                if action == "failover_next":
                                    last_transient_exc = tex
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    break
                                raise
                            transient_retries += 1
                            error_code = tex.code if isinstance(tex, _TransientStreamError) else "server_error"
                            error_payload: UpstreamError = (
                                tex.error
                                if isinstance(tex, _TransientStreamError)
                                else _upstream_error_from_openai(_parse_openai_error(tex.payload))
                            )
                            if (
                                transient_retries < _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                                and _facade()._remaining_budget_seconds(deadline) > 0
                                and not settlement.downstream_visible
                            ):
                                delay = backoff_seconds(transient_retries)
                                _facade().logger.info(
                                    "Transient stream error, retrying same account "
                                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                                    request_id,
                                    account.id,
                                    transient_retries,
                                    _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                                    delay,
                                    error_code,
                                )
                                await asyncio.sleep(delay)
                                continue  # inner loop: retry same account
                            # Exhausted same-account retries — penalize and failover
                            _facade().logger.warning(
                                "Transient retries exhausted for account "
                                "request_id=%s account_id=%s retries=%s code=%s",
                                request_id,
                                account.id,
                                transient_retries,
                                error_code,
                            )
                            await proxy._handle_stream_error(account, error_payload, error_code)
                            # Record remaining errors so total equals transient_retries,
                            # meeting the load balancer backoff threshold (error_count >= 3).
                            await proxy._load_balancer.record_errors(account, transient_retries - 1)
                            # Preserve last ProxyResponseError for propagate_http_errors path.
                            if isinstance(tex, ProxyResponseError):
                                last_transient_exc = tex
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            break  # outer loop: select different account
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await proxy._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await proxy._load_balancer.record_success(account)
                        settled = await proxy._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
                        return
                    continue  # outer loop: account failover after transient exhaustion
                except _RetryableStreamError as exc:
                    if _facade()._is_security_work_authorization_required_error(exc.code, exc.error.get("message")):
                        if (
                            account.security_work_authorized
                            or account.id == file_preferred_account_id
                            or require_preferred_account
                            or attempt >= max_attempts - 1
                        ):
                            event = response_failed_event(
                                exc.code,
                                exc.error.get("message") or "Security work authorization is required",
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        _facade().logger.info(
                            "Retrying on security-work-authorized account request_id=%s account_id=%s",
                            request_id,
                            account.id,
                        )
                        yield format_sse_event(
                            _facade()._security_work_advisory_event(
                                code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                request_id=request_id,
                                action="retry_security_work_authorized",
                                account_id=account.id,
                            )
                        )
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                        require_security_work_authorized = True
                        last_security_work_retry_error = exc
                        continue
                    await proxy._handle_stream_error(account, exc.error, exc.code)
                    last_retryable_stream_error = exc
                    if exc.exclude_account:
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                    continue
                except _TerminalStreamError as exc:
                    if _facade()._should_penalize_stream_error(exc.code):
                        await proxy._handle_stream_error(account, exc.error, exc.code)
                    return
                except ProxyResponseError as exc:
                    if exc.status_code == 401:
                        remaining_budget = _facade()._remaining_budget_seconds(deadline)
                        if remaining_budget <= 0:
                            _facade().logger.warning(
                                "Proxy request budget exhausted before forced refresh retry "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                            )
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        try:
                            account = await proxy._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
                        except RefreshError as refresh_exc:
                            if refresh_exc.is_permanent:
                                await proxy._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                            continue
                        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                            _facade().logger.warning(
                                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                                exc_info=True,
                            )
                            message = str(exc) or "Request to upstream timed out"
                            if (
                                not require_preferred_account
                                and preferred_account_id is None
                                and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                                and attempt + 1 < max_attempts
                            ):
                                await proxy._handle_stream_error(
                                    account,
                                    {"message": message},
                                    "upstream_unavailable",
                                )
                                last_retryable_stream_error = _RetryableStreamError(
                                    "upstream_unavailable",
                                    {"message": message},
                                    exclude_account=True,
                                )
                                excluded_account_ids.add(account.id)
                                continue
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_unavailable",
                                error_message=message,
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                            )
                            event = response_failed_event(
                                "upstream_unavailable",
                                message,
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        settlement = _StreamSettlement()
                        effective_attempt_timeout = _facade()._remaining_budget_seconds(deadline)
                        if effective_attempt_timeout <= 0:
                            _facade().logger.warning(
                                "Proxy request budget exhausted before post-refresh stream attempt "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                            )
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        stream_timeout_tokens = _facade()._push_stream_attempt_timeout_overrides(
                            effective_attempt_timeout
                        )
                        try:
                            async for line in proxy._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                False,
                                request_started_at=start,
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                tool_call_dedupe=tool_call_dedupe,
                            ):
                                yield line
                        except ProxyResponseError as retry_exc:
                            if settlement.downstream_visible:
                                failed_response_id = settlement.response_id or request_id
                                error = _parse_openai_error(retry_exc.payload)
                                error_code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error else "Upstream error"
                                event = response_failed_event(
                                    error_code or "upstream_error",
                                    error_message or "Upstream error",
                                    error_type=(error.type if error else None) or "server_error",
                                    response_id=failed_response_id,
                                    error_param=error.param if error else None,
                                )
                                _apply_error_metadata(event["response"]["error"], error)
                                _facade().logger.warning(
                                    "Surfacing post-refresh stream failure without replay "
                                    "request_id=%s account_id=%s code=%s",
                                    request_id,
                                    account.id,
                                    error_code,
                                )
                                yield format_sse_event(event)
                                settlement.record_success = False
                                settlement.error_code = error_code
                                settlement.error_message = error_message
                                settlement.error = _upstream_error_from_openai(error)
                                settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
                                if settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                        http_status=retry_exc.status_code,
                                    )
                                settled = await proxy._settle_stream_api_key_usage(
                                    api_key,
                                    api_key_reservation,
                                    settlement,
                                    request_id,
                                )
                                return
                            error = _parse_openai_error(retry_exc.payload)
                            error_code = _normalize_error_code(
                                error.code if error else None,
                                error.type if error else None,
                            )
                            if error_code == "account_response_create_cap":
                                last_transient_exc = retry_exc
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            if _facade()._is_account_neutral_error_code(error_code):
                                raise
                            classified = await proxy._handle_stream_error(
                                account,
                                _upstream_error_from_openai(error),
                                error_code,
                                http_status=retry_exc.status_code,
                            )
                            candidates_remaining = max_attempts - attempt - 1
                            if retry_exc.status_code == 401 and candidates_remaining > 0:
                                action = "failover_next"
                            elif getattr(base_settings, "deterministic_failover_enabled", True):
                                action = failover_decision(
                                    failure_class=classified["failure_class"],
                                    downstream_visible=False,
                                    candidates_remaining=candidates_remaining,
                                )
                            else:
                                action = "surface"
                            _facade().logger.info(
                                "Failover decision request_id=%s transport=stream account_id=%s "
                                "attempt=%d phase=post_refresh failure_class=%s action=%s",
                                request_id,
                                account.id,
                                attempt + 1,
                                classified["failure_class"],
                                action,
                            )
                            if action == "failover_next":
                                last_transient_exc = retry_exc
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            if propagate_http_errors:
                                raise
                            error_message = error.message if error else None
                            event = response_failed_event(
                                error_code or "upstream_error",
                                error_message or "Upstream error",
                                error_type=(error.type if error else None) or "server_error",
                                response_id=request_id,
                                error_param=error.param if error else None,
                            )
                            _apply_error_metadata(event["response"]["error"], error)
                            yield format_sse_event(event)
                            return
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await proxy._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await proxy._load_balancer.record_success(account)
                        settled = await proxy._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
                        return
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    error_type = error.type if error else None
                    error_param = error.param if error else None
                    if _facade()._is_security_work_authorization_required_error(error_code, error_message):
                        if (
                            not account.security_work_authorized
                            and account.id != file_preferred_account_id
                            and not require_preferred_account
                            and attempt < max_attempts - 1
                        ):
                            _facade().logger.info(
                                "Retrying on security-work-authorized account request_id=%s account_id=%s",
                                request_id,
                                account.id,
                            )
                            yield format_sse_event(
                                _facade()._security_work_advisory_event(
                                    code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                    message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                    request_id=request_id,
                                    action="retry_security_work_authorized",
                                    account_id=account.id,
                                )
                            )
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            require_security_work_authorized = True
                            continue
                    if _facade()._should_penalize_stream_error(error_code):
                        await proxy._handle_stream_error(
                            account,
                            _upstream_error_from_openai(error),
                            error_code,
                        )
                    if propagate_http_errors:
                        raise
                    event = response_failed_event(
                        error_code,
                        error_message or "Upstream error",
                        error_type=error_type or "server_error",
                        response_id=request_id,
                        error_param=error_param,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return
                except RefreshError as exc:
                    if exc.is_permanent:
                        await proxy._load_balancer.mark_permanent_failure(account, exc.code)
                    continue
                except Exception:
                    _facade().logger.warning(
                        "Proxy streaming failed without retry account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
            # When HTTP error propagation is enabled and the last failure was
            # a transient 500, re-raise to preserve the upstream status/payload.
            if propagate_http_errors and last_transient_exc is not None:
                raise last_transient_exc
            if last_retryable_stream_error is not None:
                retries_exhausted_msg = str(last_retryable_stream_error.error.get("message") or "Upstream error")
                event = response_failed_event(
                    last_retryable_stream_error.code,
                    retries_exhausted_msg,
                    response_id=request_id,
                )
                yield format_sse_event(event)
                if not any_attempt_logged:
                    await proxy._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code=last_retryable_stream_error.code,
                        error_message=retries_exhausted_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
                return
            retries_exhausted_msg = "No available accounts after retries"
            _facade().logger.warning(
                "Proxy streaming exhausted accounts request_id=%s model=%s transport=%s attempts=%s "
                "excluded_count=%s preferred_account_id=%s api_key_present=%s",
                request_id,
                payload.model,
                request_transport,
                attempt,
                len(excluded_account_ids),
                preferred_account_id,
                api_key is not None,
            )
            event = response_failed_event(
                "no_accounts",
                retries_exhausted_msg,
                response_id=request_id,
            )
            yield format_sse_event(event)
            if not any_attempt_logged:
                await proxy._write_request_log(
                    account_id=None,
                    api_key=api_key,
                    request_id=request_id,
                    model=payload.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code="no_accounts",
                    error_message=retries_exhausted_msg,
                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                    transport=request_transport,
                    service_tier=payload.service_tier,
                    requested_service_tier=payload.service_tier,
                    useragent=useragent,
                    useragent_group=useragent_group,
                )
        finally:
            for account_lease in account_leases:
                await proxy._load_balancer.release_account_lease(account_lease)
            if not settled and api_key is not None and api_key_reservation is not None:
                release_coro = proxy._release_unsettled_stream_api_key_usage(
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    proxy._schedule_cancel_safe_cleanup(
                        release_coro,
                        action="release_stream_api_key_reservation",
                        request_id=request_id,
                    )
                else:
                    await release_coro

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
                    },
                    raise_for_status=True,
                    upstream_stream_transport_override=upstream_stream_transport,
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
