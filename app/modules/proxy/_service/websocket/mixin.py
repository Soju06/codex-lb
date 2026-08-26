from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterator, Mapping, NoReturn, cast

import aiohttp
import anyio
from fastapi import WebSocket
from pydantic import ValidationError

from app.core import shutdown as shutdown_state
from app.core.auth.refresh import (
    RefreshError,
    is_transient_refresh_contention,
    refresh_contention_kind,
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
from app.core.clients.proxy import (  # noqa: F401  # noqa: F401
    CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY,
    ImageFetchSession,
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    _as_image_fetch_session,
    _inline_content_images,
    _inline_input_image_urls,
    _payload_has_responses_lite_websocket_marker,
    _payload_uses_responses_lite,
    _ws_transport_payload_budget_bytes,
    apply_codex_installation_headers,
    apply_codex_installation_metadata,
    filter_inbound_headers,
    is_confirmed_pre_dispatch_transport_error,
    pop_compact_timeout_overrides,
    pop_stream_timeout_overrides,
    pop_transcribe_timeout_overrides,
    push_compact_timeout_overrides,
    push_stream_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy import CodexControlResponse as CodexControlResponse
from app.core.clients.proxy import codex_control_request as core_codex_control_request  # noqa: F401
from app.core.clients.proxy import compact_responses as core_compact_responses  # noqa: F401
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio  # noqa: F401
from app.core.clients.proxy_websocket import (
    UpstreamWebSocket,
    UpstreamWebSocketTransportError,
    filter_inbound_websocket_headers,
    is_account_neutral_websocket_error_code,
)
from app.core.errors import (
    OpenAIErrorEnvelope,
    openai_error,
    response_failed_event,
)
from app.core.exceptions import AppError, ProxyAuthError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import (
    _LIFECYCLE_EVENT_TYPES,
    classify_event_type,
    parse_sse_event_payload,
)
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.network_recovery import (
    NetworkRecoveryDecision,
    ProcessNetworkRecovery,
    process_network_error_code,
)
from app.core.types import JsonValue
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.request_id import get_request_id, reset_request_id, set_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME as CODEX_KEEPALIVE_FRAME  # noqa: F401
from app.core.utils.sse import format_sse_event
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
from app.modules.model_sources.selection import (
    effective_model_for_api_key,
    responses_model_is_source_owned,
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
    _record_upstream_transport_decision as _record_upstream_transport_decision,
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
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_HTTP,
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _account_capacity_wait_payload,
    _clear_websocket_precreated_replay_fallback,
    _clear_websocket_request_error_overrides,
    _DownstreamWebSocketActivity,
    _finalize_ttft_reasoning_deltas,
    _PreparedWebSocketRequest,
    _record_response_event,
    _record_websocket_route_metadata,
    _request_log_client_fields,
    _sleep_for_account_selection_recovery,
    _stream_settlement_error_payload,
    _StreamSettlement,
    _wait_for_websocket_continuity_gap,
    _websocket_full_replay_should_wait_for_continuity,
    _WebSocketConnectFailureEmitted,
    _WebSocketContinuityState,
    _WebSocketReceiveTimeout,
    _WebSocketRequestState,
    _WebSocketTransientRefreshFailover,
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
    _bind_websocket_request_dispatch_owner,
    _find_websocket_request_state_by_response_id,
    _forget_websocket_stale_previous_response,
    _install_verified_fresh_replay,
    _is_websocket_response_create,
    _is_websocket_stale_previous_response,
    _match_websocket_request_state_for_anonymous_event,
    _matching_websocket_request_states_for_missing_tool_output_error,
    _matching_websocket_request_states_for_previous_response_error,
    _maybe_rewrite_websocket_previous_response_not_found_event,
    _parse_websocket_payload,
    _pop_matching_websocket_request_states,
    _pop_replayable_precreated_websocket_request_state,
    _pop_terminal_websocket_request_state,
    _prepare_websocket_request_state_for_account_switch,
    _prepare_websocket_request_state_for_auth_replay,
    _record_websocket_continuity_completion,
    _record_websocket_responses_lite_acceptance,
    _record_websocket_stale_anchor_failure,
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
    _websocket_capability_metadata_values,
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
    _websocket_event_incomplete_reason,
    _websocket_full_resend_conflicts_with_visible_pending,
    _websocket_input_items_are_self_contained_fresh_replay,
    _websocket_owner_switch_has_other_pending_requests,
    _websocket_precreated_auth_error_code,
    _websocket_precreated_replay_fallback_error,
    _websocket_precreated_retry_error_code,
    _websocket_receive_timeout_for_pending_requests,
    _websocket_response_id,
    _wrapped_websocket_error_event,
)
from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _is_synthesized_turn_state,
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _request_allows_unavailable_legacy_owner_abandonment,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,  # noqa: F401
    _sticky_key_from_turn_state_header,
    _websocket_continuity_aliases_from_headers,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.capability_routing import (
    CAPABILITY_ROUTING_UNAVAILABLE_CODE,
    CAPABILITY_ROUTING_UNAVAILABLE_MESSAGE,
    RoutingCapability,
    RoutingIntent,
    _capability_lineage_unavailable_error,
    capability_lineage_aliases,
    parse_routing_intent,
    reject_capability_signal_outside_response_create,
    strip_capability_metadata,
)
from app.modules.proxy.continuity import resolve_required_account_id
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
from app.modules.proxy.load_balancer import AccountLease, effective_account_concurrency_caps
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    apply_enforced_service_tier_model_fallback,
    model_alias_requests_fast_mode,
    normalize_responses_request_payload,
    openai_client_payload_error,
    openai_invalid_payload_error,
    openai_validation_error,
    responses_source_route_excluded,
    validate_model_access,
    validate_top_level_compaction_trigger_input_shape,
)
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED, selection_failure_response
from app.modules.proxy.tool_call_dedupe import (
    mark_duplicate_tool_call_downstream_event,
    rewrite_parallel_tool_call_text,
)
from app.modules.proxy.tool_call_dedupe import (
    response_id_from_payload as tool_call_response_id_from_payload,
)


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


logger = logging.getLogger(__name__)

_WEBSOCKET_PINNED_REFRESH_UNAVAILABLE_MESSAGE = "Account refresh is temporarily unavailable; retry later."
# Scope teardown coordinates several request/lease finalizers; keep its normal
# observation budget separate from the short generic child-task cancel bound.
_WEBSOCKET_SCOPE_CLEANUP_TIMEOUT_SECONDS = 5.0
_CAPABILITY_REQUIRED_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "This request requires Trusted Access for Cyber, but no eligible account is marked as "
    "security-work-authorized. codex-lb did not fall back to an ordinary account."
)
_CAPABILITY_REQUIRED_NO_AUTHORIZED_ACCOUNTS_ACTION = "fail_closed_capability_routing"


class _WebSocketReplaySequenceRegression(Exception):
    pass


class _CapabilityLineagePropagationError(Exception):
    def __init__(self, error: ProxyResponseError) -> None:
        super().__init__("Capability lineage propagation failed")
        self.error = error


def _log_websocket_persist_conflict(context: str, exc: RefreshError, account_id: str) -> None:
    """Surface a post-exchange persist/status CAS conflict distinctly in logs.

    Benign claim contention is expected and unlogged here; a post-exchange
    persist conflict is a rarer, more-serious internal race worth surfacing.
    """
    if refresh_contention_kind(exc) == "persist_conflict":
        logger.warning(
            "WebSocket %s refresh post-exchange persist conflict code=%s account_id=%s",
            context,
            exc.code,
            account_id,
        )


async def _reject_websocket_owner_switch_blocked(
    proxy: Any,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    request_state: _WebSocketRequestState,
    account: Account,
    api_key: ApiKeyData | None,
    response_create_gate: asyncio.Semaphore,
    downstream_activity: _DownstreamWebSocketActivity,
    error_code: str = "previous_response_owner_unavailable",
    error_message: str = (
        "Previous response owner differs while another response is still streaming; retry after the terminal frame."
    ),
) -> None:
    await proxy._release_websocket_request_state_reservation(request_state)
    await proxy._write_websocket_connect_failure(
        account_id=account.id,
        api_key=api_key,
        request_state=request_state,
        error_code=error_code,
        error_message=error_message,
    )
    await proxy._emit_websocket_terminal_error(
        websocket,
        client_send_lock=client_send_lock,
        request_state=request_state,
        error_code=error_code,
        error_message=error_message,
        downstream_activity=downstream_activity,
    )
    await _release_websocket_response_create_gate(request_state, response_create_gate)


async def _reject_websocket_capability_switch_blocked(
    proxy: Any,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    request_state: _WebSocketRequestState,
    account: Account,
    api_key: ApiKeyData | None,
    response_create_gate: asyncio.Semaphore,
    downstream_activity: _DownstreamWebSocketActivity,
) -> None:
    error_message = (
        "Required capability cannot switch accounts while another response is still streaming; "
        "retry after the terminal frame."
    )
    await proxy._release_websocket_request_state_reservation(request_state)
    await proxy._write_websocket_connect_failure(
        account_id=account.id,
        api_key=api_key,
        request_state=request_state,
        error_code="continuity_owner_conflict",
        error_message=error_message,
    )
    await proxy._emit_websocket_terminal_error(
        websocket,
        client_send_lock=client_send_lock,
        request_state=request_state,
        error_code="continuity_owner_conflict",
        error_message=error_message,
        downstream_activity=downstream_activity,
    )
    await _release_websocket_response_create_gate(request_state, response_create_gate)


async def _close_downstream_after_sequenced_replay_refusal(
    websocket: WebSocket,
    downstream_activity: _DownstreamWebSocketActivity,
) -> None:
    # Once a sequence-numbered frame is visible, a synthetic terminal frame
    # would violate the upstream sequence. Closing is therefore the only
    # client-visible terminal signal whenever replay is refused.
    downstream_activity.mark_disconnected()
    try:
        await websocket.close(code=1011, reason="upstream replay requires a fresh request")
    except Exception:
        _facade().logger.debug(
            "Failed to close downstream websocket after sequenced replay refusal",
            exc_info=True,
        )


@contextmanager
def _websocket_archive_request_context(request_id: str | None) -> Iterator[None]:
    token = set_request_id(request_id)
    try:
        yield
    finally:
        reset_request_id(token)


def _archive_received_websocket_message(
    upstream: UpstreamWebSocket,
    message: Any,
    *,
    archive_request_id: str | None,
) -> None:
    archive_received = getattr(upstream, "archive_received", None)
    if not callable(archive_received):
        return
    with _websocket_archive_request_context(archive_request_id):
        archive_received(message)


def _websocket_archive_request_state_for_payload(
    pending_requests: deque[_WebSocketRequestState],
    *,
    event: OpenAIEvent | None,
    payload: dict[str, JsonValue] | None,
    event_type: str | None,
) -> _WebSocketRequestState | None:
    response_id = _websocket_response_id(event, payload)
    if event_type == "response.created":
        if response_id is not None:
            existing = _find_websocket_request_state_by_response_id(pending_requests, response_id)
            if existing is not None:
                return existing
        for request_state in pending_requests:
            if request_state.response_id is None and _http_bridge_request_counts_against_queue(request_state):
                return request_state
        for request_state in pending_requests:
            if request_state.response_id is None and request_state.draining_until_terminal:
                return request_state
        for request_state in pending_requests:
            if request_state.response_id is None:
                return request_state
        return None
    if response_id is not None:
        return _find_websocket_request_state_by_response_id(pending_requests, response_id)
    error_message = _websocket_event_error_message(event_type, payload)
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
    return _match_websocket_request_state_for_anonymous_event(
        pending_requests,
        prefer_previous_response_not_found=is_previous_response_not_found_event or is_missing_tool_output_event,
        previous_response_id_hint=_facade()._previous_response_id_from_not_found_message(error_message),
        error_message=error_message,
        allow_unanchored_previous_response_error=is_previous_response_not_found_event,
    )


@dataclass(frozen=True, slots=True)
class _ParsedUpstreamWebSocketFrame:
    payload: dict[str, JsonValue] | None
    event_type: str | None
    event: OpenAIEvent | None


def _parse_upstream_websocket_text_frame(text: str) -> _ParsedUpstreamWebSocketFrame:
    """Decode an upstream websocket text frame exactly once.

    The payload is json-decoded a single time, the event type is classified
    from the parsed dict, and pydantic validation runs only for lifecycle
    frames (the only events whose validated model fields the proxy consumes).
    """
    try:
        raw_payload = json.loads(text)
    except json.JSONDecodeError:
        raw_payload = None
    payload = cast(dict[str, JsonValue], raw_payload) if isinstance(raw_payload, dict) else None
    event_type = classify_event_type(payload)
    event = parse_sse_event_payload(payload) if event_type in _LIFECYCLE_EVENT_TYPES else None
    return _ParsedUpstreamWebSocketFrame(payload=payload, event_type=event_type, event=event)


async def _websocket_archive_request_id_for_message(
    message: Any,
    *,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
    parsed_frame: _ParsedUpstreamWebSocketFrame | None = None,
) -> str | None:
    if message.kind != "text" or message.text is None:
        async with pending_lock:
            if len(pending_requests) == 1:
                return pending_requests[0].archive_request_id
            return None
    # Archive attribution only needs the payload dict (response ids and error
    # fields are read from it directly), so reuse the caller's parsed frame
    # when provided and never re-validate non-lifecycle deltas.
    frame = parsed_frame if parsed_frame is not None else _parse_upstream_websocket_text_frame(message.text)
    async with pending_lock:
        request_state = _websocket_archive_request_state_for_payload(
            pending_requests,
            event=frame.event,
            payload=frame.payload,
            event_type=frame.event_type,
        )
        return None if request_state is None else request_state.archive_request_id


def _raise_proxy_budget_exhausted() -> NoReturn:
    _facade()._raise_proxy_budget_exhausted()
    raise AssertionError("proxy budget exhaustion helper returned")


async def _wait_for_process_network_recovery(
    recovery: ProcessNetworkRecovery,
    exc: ProxyResponseError,
    *,
    deadline: float,
) -> NetworkRecoveryDecision:
    error = _parse_openai_error(exc.payload)
    return await recovery.wait(
        error_code=_normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        ),
        retryable_same_contract=exc.retryable_same_contract,
        deadline=deadline,
        rotate_shared_client=True,
        failed_session=exc.failed_session,
    )


def _websocket_text_with_account_installation_id(text_data: str, account: Account) -> str:
    payload = json.loads(text_data)
    if not isinstance(payload, dict):
        return text_data
    codex_installation_id = getattr(account, "codex_installation_id", None)
    apply_codex_installation_metadata(cast(dict[str, JsonValue], payload), codex_installation_id)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _websocket_enforce_response_create_text_size(
    request_state: _WebSocketRequestState,
    text_data: str,
) -> None:
    original_request_text = request_state.request_text
    request_state.request_text = text_data
    try:
        _facade()._enforce_response_create_size_limit(request_state)
    finally:
        request_state.request_text = original_request_text


def _refine_websocket_request_kind_from_completion(
    request_state: _WebSocketRequestState,
    *,
    event_type: str | None,
    output_tokens: int | None,
) -> None:
    if event_type == "response.completed" and request_state.connection_request_kind == "prewarm" and output_tokens == 0:
        request_state.request_kind = "prewarm"


def _track_websocket_owned_task(
    proxy: _WebSocketServiceProtocol,
    task: asyncio.Task[Any],
) -> None:
    tracked_task = cast(asyncio.Task[None], task)
    proxy._background_cleanup_tasks.add(tracked_task)

    def _discard_owned_task(_done_task: asyncio.Task[Any]) -> None:
        proxy._background_cleanup_tasks.discard(tracked_task)
        if not _done_task.cancelled():
            # The scope normally observes the result. Retrieving it here also
            # prevents an abandoned post-deadline task from producing an
            # unhandled-task warning after shutdown has moved on.
            _done_task.exception()

    task.add_done_callback(_discard_owned_task)


_WEBSOCKET_UPSTREAM_CLOSE_CLEANUP_TIMEOUT_SECONDS = 0.25


async def _close_websocket_upstream_for_cleanup(
    proxy: _WebSocketServiceProtocol,
    upstream: UpstreamWebSocket,
    *,
    timeout_seconds: float,
) -> None:
    """Close an upstream socket without letting a stuck close block cleanup.

    Some websocket implementations can wait for a close handshake after the
    peer has already disappeared. The close operation remains tracked so it
    can finish asynchronously, while scope finalization continues releasing
    request ownership and leases within its bounded cleanup budget.
    """

    close_task = asyncio.create_task(
        upstream.close(),
        name="proxy-websocket-upstream-close",
    )
    _track_websocket_owned_task(proxy, close_task)
    effective_timeout = min(
        max(float(timeout_seconds), 0.0),
        _WEBSOCKET_UPSTREAM_CLOSE_CLEANUP_TIMEOUT_SECONDS,
    )

    async def cancel_close_task() -> None:
        try:
            await _facade()._await_cancelled_task(
                close_task,
                timeout_seconds=effective_timeout,
                label="proxy websocket upstream close",
                cleanup_tasks=proxy._background_cleanup_tasks,
            )
        except Exception:
            _facade().logger.debug("Failed to cancel upstream websocket close task", exc_info=True)

    if effective_timeout <= 0:
        await cancel_close_task()
        return
    try:
        await asyncio.wait_for(asyncio.shield(close_task), timeout=effective_timeout)
    except TimeoutError:
        _facade().logger.debug(
            "Upstream websocket close continued after cleanup budget timeout_seconds=%.3f",
            effective_timeout,
        )
        await cancel_close_task()
    except Exception:
        _facade().logger.debug("Failed to close upstream websocket during scope cleanup", exc_info=True)


async def _await_owned_websocket_task_after_reader_cancellation(
    task: asyncio.Task[Any],
    *,
    failure_message: str,
) -> None:
    """Observe owned child completion without replacing reader cancellation."""

    remaining = shutdown_state.remaining_drain_timeout_seconds()
    timeout_seconds = _facade()._TASK_CANCEL_TIMEOUT_SECONDS if remaining is None else max(float(remaining), 0.0)

    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        raise
    if not done:
        return
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        _facade().logger.warning(failure_message, exc_info=True)


async def _release_websocket_response_create_ownership_for_cleanup(
    request_state: _WebSocketRequestState,
    response_create_gate: asyncio.Semaphore,
) -> None:
    """Release every create owner even when one release side effect fails."""

    response_create_admission = request_state.response_create_admission
    account_response_create_lease = request_state.account_response_create_lease
    account_response_create_release = request_state.account_response_create_release
    response_create_gate_acquired = request_state.response_create_gate_acquired
    request_state.response_create_admission = None
    request_state.account_response_create_lease = None
    request_state.account_response_create_release = None
    request_state.awaiting_response_created = False
    request_state.response_create_gate = None
    request_state.response_create_gate_acquired = False

    try:
        if response_create_admission is not None:
            try:
                response_create_admission.release()
            except Exception:
                _facade().logger.warning(
                    "Failed to release websocket work admission during terminal cleanup request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )
        if account_response_create_lease is not None and account_response_create_release is not None:
            try:
                await account_response_create_release(account_response_create_lease)
            except Exception:
                _facade().logger.warning(
                    "Failed to release websocket account create lease during terminal cleanup request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )
    finally:
        if response_create_gate_acquired:
            response_create_gate.release()


async def _process_and_forward_upstream_websocket_text(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    upstream: UpstreamWebSocket,
    *,
    message: Any,
    text: str,
    account: Account,
    account_id_value: str,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    upstream_control: _WebSocketUpstreamControl,
    response_create_gate: asyncio.Semaphore,
    downstream_activity: _DownstreamWebSocketActivity,
    continuity_state: _WebSocketContinuityState | None,
    codex_session_affinity: bool,
) -> bool:
    parsed_frame = _parse_upstream_websocket_text_frame(text)
    archive_request_id = await _websocket_archive_request_id_for_message(
        message,
        pending_requests=pending_requests,
        pending_lock=pending_lock,
        parsed_frame=parsed_frame,
    )
    _archive_received_websocket_message(
        upstream,
        message,
        archive_request_id=archive_request_id,
    )
    downstream_text = await proxy._process_upstream_websocket_text(
        text,
        parsed_frame=parsed_frame,
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
    downstream_sequence_request_state = upstream_control.downstream_sequence_request_state
    downstream_sequence_number = upstream_control.downstream_sequence_number
    upstream_control.suppress_downstream_event = False
    upstream_control.downstream_texts = None
    upstream_control.downstream_sequence_request_state = None
    upstream_control.downstream_sequence_number = None
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
                    "Downstream websocket disconnected during upstream relay",
                    exc_info=True,
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
            return True
    elif not suppress_downstream_event:
        try:
            await proxy._send_downstream_websocket_text(
                websocket,
                client_send_lock=client_send_lock,
                text=downstream_text,
                downstream_activity=downstream_activity,
            )
            if downstream_sequence_request_state is not None and downstream_sequence_number is not None:
                downstream_sequence_request_state.last_downstream_sequence_number = downstream_sequence_number
        except Exception:
            downstream_activity.mark_disconnected()
            _facade().logger.debug(
                "Downstream websocket disconnected during upstream relay",
                exc_info=True,
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
            return True
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
                    "Failed to close upstream websocket for reconnect",
                    exc_info=True,
                )
            return True
    return False


async def _websocket_has_active_drain_work(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
    upstream_control: _WebSocketUpstreamControl | None,
) -> bool:
    if upstream_control is not None and upstream_control.replay_request_state is not None:
        return True
    terminal_task = upstream_control.terminal_message_task if upstream_control is not None else None
    if terminal_task is not None and not terminal_task.done():
        return True
    async with pending_lock:
        return bool(pending_requests)


async def _claim_unsent_websocket_request_for_reconnect(
    request_state: _WebSocketRequestState,
    *,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
    upstream_control: _WebSocketUpstreamControl,
) -> tuple[_WebSocketRequestState | None, _WebSocketRequestState | None]:
    """Transfer one unsent request off a transport retired before send."""

    await pending_lock.acquire()
    try:
        reader_replay = upstream_control.replay_request_state
        current_is_pending = request_state in pending_requests
        if current_is_pending:
            pending_requests.remove(request_state)
        upstream_control.replay_request_state = None

        if reader_replay is request_state:
            return request_state, None
        if reader_replay is not None:
            # A reader-owned replay is older. Preserve that single owner and let
            # the caller terminally finalize a separately registered current turn.
            return reader_replay, request_state if current_is_pending else None
        if current_is_pending:
            # This frame has not crossed send_text(), so moving it to a fresh
            # transport is not a replay and must not consume or rewrite replay
            # state.
            return request_state, None
        # The reader already finalized the state while the caller was awaiting
        # admission/account ownership. Never resurrect it.
        return None, None
    finally:
        pending_lock.release()


async def _claim_sent_websocket_requests_for_reader(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
) -> deque[_WebSocketRequestState]:
    """Atomically leave unsent requests sender-owned at transport end."""

    await pending_lock.acquire()
    try:
        reader_owned = deque(
            request_state for request_state in pending_requests if request_state.response_create_sent_at is not None
        )
        if reader_owned:
            sender_owned = [
                request_state for request_state in pending_requests if request_state.response_create_sent_at is None
            ]
            pending_requests.clear()
            pending_requests.extend(sender_owned)
        return reader_owned
    finally:
        pending_lock.release()


async def _process_upstream_websocket_transport_end(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    upstream: UpstreamWebSocket,
    *,
    message: Any,
    account: Account,
    account_id_value: str,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    upstream_control: _WebSocketUpstreamControl,
    response_create_gate: asyncio.Semaphore,
    downstream_activity: _DownstreamWebSocketActivity,
) -> bool:
    """Finalize only requests that crossed this transport's send boundary."""

    archive_request_id = await _websocket_archive_request_id_for_message(
        message,
        pending_requests=pending_requests,
        pending_lock=pending_lock,
    )
    _archive_received_websocket_message(
        upstream,
        message,
        archive_request_id=archive_request_id,
    )
    reader_owned = await _claim_sent_websocket_requests_for_reader(
        pending_requests,
        pending_lock=pending_lock,
    )
    replay_refusal_reasons: list[str] = []
    replay_request_state = None
    message_error_code = getattr(message, "error_code", None)
    # A classified local transport failure says nothing about whether an
    # already-sent response.create was accepted. Keep it account-neutral and
    # terminal: replay here could duplicate work, billing, or tool side effects.
    account_neutral = is_account_neutral_websocket_error_code(message_error_code)
    if account_neutral:
        if any(state.last_downstream_sequence_number is not None for state in reader_owned):
            replay_refusal_reasons.append("sequenced_downstream_frame")
    else:
        replay_request_state = await _pop_replayable_precreated_websocket_request_state(
            reader_owned,
            pending_lock=anyio.Lock(),
            replay_refusal_reasons=replay_refusal_reasons,
        )
    if replay_request_state is not None:
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
        return True

    sequenced_downstream_replay_refused = "sequenced_downstream_frame" in replay_refusal_reasons
    await proxy._fail_pending_websocket_requests(
        account=account,
        account_id_value=account_id_value,
        pending_requests=reader_owned,
        pending_lock=anyio.Lock(),
        error_code=message_error_code or "stream_incomplete",
        error_message=_upstream_websocket_disconnect_message(message),
        api_key=api_key,
        websocket=websocket,
        client_send_lock=client_send_lock,
        response_create_gate=response_create_gate,
        downstream_activity=downstream_activity,
        penalize_account=not account_neutral,
        suppress_sequenced_downstream_errors=sequenced_downstream_replay_refused,
    )
    # A terminal receive can race the outer session cleanup, especially when
    # the downstream closes as soon as it receives the failure event. Retire
    # the transport before this reader-owned finalization task exits.
    try:
        await upstream.close()
    except Exception:
        _facade().logger.debug("Failed to close upstream websocket after terminal receive", exc_info=True)
    if sequenced_downstream_replay_refused:
        await _close_downstream_after_sequenced_replay_refusal(
            websocket,
            downstream_activity,
        )
    return True


class _WebSocketMixin:
    async def _touch_active_websocket_thread_affinity(
        self,
        request_state: _WebSocketRequestState,
        account: Account,
    ) -> None:
        """Refresh bounded thread locality without turning it into ownership."""

        proxy = cast(_WebSocketServiceProtocol, self)
        policy = request_state.affinity_policy
        if (
            policy.codex_session_source != "thread_header"
            or policy.selection_key is None
            or policy.kind != StickySessionKind.PROMPT_CACHE
            or policy.max_age_seconds is None
        ):
            return
        now = time.monotonic()
        touch_interval = max(1.0, min(float(policy.max_age_seconds) / 2.0, 60.0))
        if now - request_state.thread_affinity_last_touch_at < touch_interval:
            return
        try:
            # A response can outlive the selection TTL. Throttled event-time
            # touches keep reconnect locality current, while exact response or
            # bridge ownership remains the hard authority for this turn.
            async with proxy._repo_factory() as repos:
                await repos.sticky_sessions.upsert(
                    policy.selection_key,
                    account.id,
                    kind=policy.kind,
                )
        except Exception:
            _facade().logger.warning(
                "Failed to refresh active Codex thread affinity account_id=%s",
                account.id,
                exc_info=True,
            )
            return
        request_state.thread_affinity_last_touch_at = now

    def _websocket_continuity_state_for_request(
        self,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        codex_session_affinity: bool,
        synthesized_turn_state: str | None = None,
    ) -> "_WebSocketContinuityState":
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if not codex_session_affinity:
            return _WebSocketContinuityState()
        api_key_id = api_key.id if api_key is not None else None
        cache_keys = [
            (continuity_key, api_key_id)
            for continuity_key in _websocket_continuity_aliases_from_headers(
                headers,
                synthesized_turn_state=synthesized_turn_state,
            )
        ]
        if not cache_keys:
            return _WebSocketContinuityState()
        explicit_turn_state = _sticky_key_from_turn_state_header(headers)
        exact_client_turn = explicit_turn_state is not None and explicit_turn_state != synthesized_turn_state
        # An exact client turn state is hard continuity. If its alias is
        # unknown, do not borrow retained response/tool state from the broader
        # thread key; the turn may have a different owner. Once the exact alias
        # resolves, publishing that same state under the thread key is safe and
        # keeps a later unanchored reconnect thread-local.
        lookup_keys = cache_keys[:1] if exact_client_turn else cache_keys
        continuity_state = next(
            (
                existing_state
                for key in lookup_keys
                if (existing_state := proxy._websocket_continuity_index.get(key)) is not None
            ),
            None,
        )
        exact_alias_resolved = continuity_state is not None
        if continuity_state is None:
            continuity_state = _WebSocketContinuityState()
        publish_keys = cache_keys if not exact_client_turn or exact_alias_resolved else lookup_keys
        for key in publish_keys:
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
        client_ip: str | None = None,
        synthesized_turn_state: str | None = None,
        capability_header_values: tuple[str, ...] | None = None,
    ) -> None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        filtered_headers = filter_inbound_websocket_headers(dict(headers))
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        runtime_settings = _facade().get_settings()
        settings = await _facade().get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = settings.sticky_threads_enabled
        openai_cache_affinity_max_age_seconds = settings.openai_cache_affinity_max_age_seconds
        prohibit_fast_mode = bool(getattr(settings, "prohibit_fast_mode", False))
        routing_strategy = _facade()._routing_strategy(settings)
        pending_requests: deque[_WebSocketRequestState] = deque()
        pending_lock = anyio.Lock()
        client_send_lock = anyio.Lock()
        response_create_gate = asyncio.Semaphore(1)
        upstream: UpstreamWebSocket | None = None
        upstream_reader: asyncio.Task[None] | None = None
        upstream_control: _WebSocketUpstreamControl | None = None
        continuity_state = proxy._websocket_continuity_state_for_request(
            headers,
            api_key=api_key,
            codex_session_affinity=codex_session_affinity,
            synthesized_turn_state=synthesized_turn_state,
        )
        account: Account | None = None
        account_lease: AccountLease | None = None
        upstream_requires_security_work_authorized: bool | None = None
        upstream_turn_state: str | None = _sticky_key_from_turn_state_header(headers)
        # The API inserts its generated downstream turn state into ``headers``
        # before entering this service. Preserve a turn-state header as
        # client-owned only when no synthesized value accompanied it; otherwise
        # account-switch cleanup must remain able to remove the old account's
        # generated token from ``filtered_headers``.
        client_turn_state_header: str | None = (
            _sticky_key_from_turn_state_header(filtered_headers) if synthesized_turn_state is None else None
        )
        upstream_account_id: str | None = None
        downstream_activity = _DownstreamWebSocketActivity()
        replay_request_state: _WebSocketRequestState | None = None
        request_state_to_fail: _WebSocketRequestState | None = None
        request_state_failure_task: asyncio.Task[None] | None = None
        account_lease_release_task: asyncio.Task[None] | None = None
        retired_create_lease_release_task: asyncio.Task[None] | None = None
        upstream_reader_cancel_requested = False
        scope_cancelled = False

        async def release_current_account_lease() -> None:
            nonlocal account_lease, account_lease_release_task
            if account_lease_release_task is None:
                lease_to_release = account_lease
                account_lease = None
                if lease_to_release is None:
                    return
                account_lease_release_task = asyncio.create_task(
                    proxy._load_balancer.release_account_lease(lease_to_release),
                    name="proxy-websocket-finalization-connection-lease",
                )
                _track_websocket_owned_task(proxy, account_lease_release_task)
            release_task = account_lease_release_task
            try:
                await asyncio.shield(release_task)
            finally:
                if release_task.done():
                    account_lease_release_task = None

        async def retire_current_upstream() -> None:
            nonlocal account, upstream, upstream_control, upstream_reader
            nonlocal upstream_requires_security_work_authorized
            if upstream_control is not None:
                upstream_control.reconnect_requested = True
            if upstream_reader is not None:
                await _facade()._await_cancelled_task(
                    upstream_reader,
                    label="proxy websocket upstream reader",
                    cleanup_tasks=proxy._background_cleanup_tasks,
                )
                upstream_reader = None
            upstream_control = None
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    _facade().logger.debug("Failed to retire upstream websocket", exc_info=True)
            upstream = None
            await release_current_account_lease()
            account = None
            upstream_requires_security_work_authorized = None

        async def quiesce_current_upstream_reader_after_send_failure() -> bool:
            """Stop one reader before its control owner can be retired."""

            nonlocal upstream, upstream_reader, upstream_reader_cancel_requested
            if upstream_control is not None:
                # Reader cleanup must not close the downstream socket before
                # this sender has harvested replay state or emitted the exact
                # terminal failure for an ambiguous send.
                upstream_control.reconnect_requested = True
            reader_to_await = upstream_reader
            if reader_to_await is not None and not reader_to_await.done():
                reader_to_await.cancel()
                upstream_reader_cancel_requested = True
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    _facade().logger.debug(
                        "Failed to close upstream websocket after send failure",
                        exc_info=True,
                    )
                else:
                    upstream = None
            if reader_to_await is None:
                return True
            try:
                completed = await _facade()._await_cancelled_task(
                    reader_to_await,
                    label="proxy websocket upstream reader",
                    cancel=False,
                )
            except Exception:
                # A completed reader failure must not hide an ownership
                # transfer already published on upstream_control.
                _facade().logger.warning(
                    "Upstream websocket reader failed while handling send failure",
                    exc_info=True,
                )
                completed = reader_to_await.done()
            if completed:
                upstream_reader = None
                upstream_reader_cancel_requested = False
            return completed

        def take_reader_replay_request_state() -> _WebSocketRequestState | None:
            if upstream_control is None:
                return None
            reader_replay = upstream_control.replay_request_state
            upstream_control.replay_request_state = None
            return reader_replay

        try:
            while True:
                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
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
                    # This state now belongs to a fresh transport attempt. The
                    # next reader may classify a close as post-send replayable
                    # only after this attempt reaches its own send boundary.
                    request_state.response_create_sent_at = None
                    request_state.request_stage = "reattach"
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
                    if request_state.response_create_gate_acquired:
                        # Ordinary pre-created replay retains its create gate.
                        # Re-register it without trying to acquire the same
                        # non-reentrant semaphore a second time.
                        async with pending_lock:
                            pending_requests.append(request_state)
                        proxy._start_request_state_api_key_reservation_heartbeat(
                            request_state,
                            api_key=request_state.api_key or api_key,
                            surface="websocket",
                        )
                        request_state_registered = True
                    # A terminal security event released the create gate and
                    # account admission.  Leave that replay unregistered so the
                    # normal block below reacquires both before queue and send.
                else:
                    downstream_idle_timeout_seconds = runtime_settings.proxy_downstream_websocket_idle_timeout_seconds
                    if shutdown_state.is_draining() and not await _websocket_has_active_drain_work(
                        pending_requests,
                        pending_lock=pending_lock,
                        upstream_control=upstream_control,
                    ):
                        async with client_send_lock:
                            if not await _websocket_has_active_drain_work(
                                pending_requests,
                                pending_lock=pending_lock,
                                upstream_control=upstream_control,
                            ):
                                try:
                                    await websocket.close(code=1012, reason="Server is draining")
                                except Exception:
                                    _facade().logger.debug(
                                        "Failed to close drained downstream websocket",
                                        exc_info=True,
                                    )
                                break
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
                            upstream_control=upstream_control,
                            downstream_activity=downstream_activity,
                            idle_timeout_seconds=downstream_idle_timeout_seconds,
                        ):
                            continue
                        idle_close = False
                        async with client_send_lock:
                            if await proxy._downstream_websocket_is_idle(
                                pending_requests,
                                pending_lock=pending_lock,
                                upstream_control=upstream_control,
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

                    if bytes_data is not None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(400, openai_invalid_payload_error())
                                )
                            )
                        continue

                    if text_data is not None:
                        payload = _parse_websocket_payload(text_data)
                        if payload is None:
                            async with client_send_lock:
                                await websocket.send_text(
                                    _serialize_websocket_error_event(
                                        _wrapped_websocket_error_event(400, openai_invalid_payload_error())
                                    )
                                )
                            continue
                        if _is_websocket_response_create(payload):
                            if shutdown_state.is_draining():
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(
                                                503,
                                                openai_error(
                                                    "service_unavailable",
                                                    "Server is draining",
                                                ),
                                            )
                                        )
                                    )
                                continue
                            try:
                                prepared_request = await proxy._prepare_websocket_response_create_request(
                                    payload,
                                    headers=headers,
                                    codex_session_affinity=codex_session_affinity,
                                    openai_cache_affinity=openai_cache_affinity,
                                    sticky_threads_enabled=sticky_threads_enabled,
                                    openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                                    prohibit_fast_mode=prohibit_fast_mode,
                                    api_key=api_key,
                                    continuity_state=continuity_state,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                    conversation_id=conversation_id,
                                    client_ip=client_ip,
                                    synthesized_turn_state=synthesized_turn_state,
                                    capability_header_values=capability_header_values,
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
                                        prohibit_fast_mode=prohibit_fast_mode,
                                        api_key=api_key,
                                        continuity_state=continuity_state,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                        conversation_id=conversation_id,
                                        client_ip=client_ip,
                                        synthesized_turn_state=synthesized_turn_state,
                                        capability_header_values=capability_header_values,
                                    )
                                request_state = prepared_request.request_state
                                request_affinity = prepared_request.affinity_policy
                                text_data = prepared_request.text_data
                                if request_state.previous_response_id is not None:
                                    request_state.previous_response_owner_account_id = (
                                        await proxy._resolve_websocket_previous_response_owner(
                                            previous_response_id=request_state.previous_response_id,
                                            api_key=request_state.api_key or api_key,
                                            session_id=request_state.session_id,
                                            surface="websocket_source_route",
                                            request_state=request_state,
                                        )
                                    )
                                    request_state.preferred_account_id = resolve_required_account_id(
                                        ("existing bridge or file", request_state.preferred_account_id),
                                        (
                                            "previous response",
                                            request_state.previous_response_owner_account_id,
                                        ),
                                    )
                                if (
                                    upstream is not None
                                    and account is not None
                                    # A reader that has already finished means the upstream is
                                    # gone but the cleanup that nulls it runs further below, so
                                    # without this the turn would take the reuse path (terminal
                                    # error) when it should reconnect and take the connect path
                                    # (503, which the client transparently falls back from).
                                    and upstream_reader is not None
                                    and not upstream_reader.done()
                                    # Requests the HTTP route excludes from
                                    # source routing (a terminal compaction
                                    # trigger, ``input_file`` references)
                                    # must stay on subscription accounts even
                                    # when their model is also source-owned;
                                    # the owner-routing below dispatches them
                                    # to the pinned account instead of this
                                    # guard failing the turn.
                                    and not request_state.source_route_excluded
                                    # A model source may emit the same canonical
                                    # response-id shape as the subscription
                                    # backend. Only recorded account ownership,
                                    # resolved above, may bypass this guard.
                                    and request_state.previous_response_owner_account_id is None
                                    and await responses_model_is_source_owned(
                                        request_state.model,
                                        request_state.api_key or api_key,
                                        # The raw client model, before enforcement
                                        # normalized aliases: an alias-only source
                                        # (``gpt-5-high``) is invisible in the
                                        # normalized ``request_state.model``.
                                        raw_model=request_state.raw_source_model,
                                    )
                                ):
                                    # Socket reuse bypasses connect-time selection, so a later
                                    # response.create that switches to a source-owned model
                                    # would otherwise be forwarded to the subscription account
                                    # already attached to the open upstream. Model sources are
                                    # only reachable from the HTTP request path.
                                    #
                                    # Gated on an existing upstream on purpose: a first turn has
                                    # no socket yet and must fall through to the connect guard,
                                    # which fails with a service-level 503 so the client falls
                                    # back to HTTP. Emitting a terminal error here would preempt
                                    # that fallback and make source models unreachable.
                                    source_model = request_state.raw_source_model or request_state.model
                                    source_message = (
                                        f"Model {source_model!r} is served by an "
                                        "OpenAI-compatible model source, which is only reachable "
                                        "over the HTTP transport; retry the request over HTTPS."
                                    )
                                    _facade().logger.info(
                                        "Websocket model source requires http transport "
                                        "request_id=%s model=%s raw_model=%s stage=response_create",
                                        request_state.request_log_id or request_state.request_id,
                                        request_state.model,
                                        request_state.raw_source_model,
                                    )
                                    await proxy._release_websocket_request_state_reservation(request_state)
                                    # The prepared request already owns a request-log row; without
                                    # this the row is never finalized, so the same logical failure
                                    # is only visible in request logs when it happens on the first
                                    # turn (where the connect path writes it).
                                    await proxy._write_websocket_connect_failure(
                                        account_id=account.id,
                                        api_key=request_state.api_key or api_key,
                                        request_state=request_state,
                                        error_code="model_source_requires_http_transport",
                                        error_message=source_message,
                                    )
                                    await proxy._emit_websocket_terminal_error(
                                        websocket,
                                        client_send_lock=client_send_lock,
                                        request_state=request_state,
                                        error_code="model_source_requires_http_transport",
                                        error_message=source_message,
                                        error_type="invalid_request_error",
                                        downstream_activity=downstream_activity,
                                    )
                                    continue
                            except ProxyResponseError as exc:
                                error = _parse_openai_error(exc.payload)
                                error_code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error and error.message else "Upstream error"
                                (
                                    status_code,
                                    error_payload,
                                    error_code,
                                    error_message,
                                ) = _sanitize_websocket_previous_response_error(
                                    previous_response_id=_facade()._previous_response_id_from_payload(payload),
                                    session_id=_owner_lookup_session_id_from_headers(
                                        headers,
                                        synthesized_turn_state=synthesized_turn_state,
                                    ),
                                    status_code=exc.status_code,
                                    payload=exc.payload,
                                    error_code=error_code or "upstream_error",
                                    error_message=error_message,
                                    surface="websocket_connect",
                                    expose_stale_previous_response_classifier=codex_session_affinity,
                                    request_state=request_state,
                                )
                                if request_state is not None:
                                    sanitized_error = _parse_openai_error(error_payload)
                                    error_type = (
                                        sanitized_error.type
                                        if sanitized_error and sanitized_error.type
                                        else "server_error"
                                    )
                                    error_param = sanitized_error.param if sanitized_error else None
                                    await proxy._release_websocket_request_state_reservation(request_state)
                                    await proxy._write_websocket_connect_failure(
                                        account_id=None,
                                        api_key=request_state.api_key or api_key,
                                        request_state=request_state,
                                        error_code=error_code,
                                        error_message=error_message,
                                    )
                                    await proxy._emit_websocket_terminal_error(
                                        websocket,
                                        client_send_lock=client_send_lock,
                                        request_state=request_state,
                                        error_code=error_code,
                                        error_message=error_message,
                                        error_type=error_type,
                                        error_param=error_param,
                                        downstream_activity=downstream_activity,
                                    )
                                    continue
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(
                                                status_code,
                                                error_payload,
                                                expose_stale_previous_response_classifier=codex_session_affinity,
                                            )
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
                        elif payload is not None:
                            try:
                                reject_capability_signal_outside_response_create(
                                    api_key=api_key,
                                    client_metadata=payload.get("client_metadata"),
                                    client_metadata_values=_websocket_capability_metadata_values(payload),
                                )
                            except ProxyResponseError as exc:
                                async with client_send_lock:
                                    await websocket.send_text(
                                        _serialize_websocket_error_event(
                                            _wrapped_websocket_error_event(exc.status_code, exc.payload)
                                        )
                                    )
                                continue

                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
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

                if request_state is not None and (
                    request_state.previous_response_id is not None
                    or request_state.affinity_policy.codex_session_source == "turn_state"
                ):
                    try:
                        # Preparation can discover file/bridge ownership, but
                        # response and turn-state indexes are independent hard
                        # evidence. Resolve every source before socket reuse or
                        # connection so source ordering cannot hide conflicts.
                        turn_state = (
                            request_state.affinity_policy.key
                            if request_state.affinity_policy.codex_session_source == "turn_state"
                            else None
                        )
                        turn_state_owner_account_id = (
                            await proxy._resolve_compact_turn_state_owner(
                                turn_state=turn_state,
                                api_key=request_state.api_key or api_key,
                                fail_on_missing=not _is_synthesized_turn_state(turn_state),
                            )
                            if turn_state is not None
                            else None
                        )
                        previous_response_owner_account_id = request_state.previous_response_owner_account_id
                        if (
                            request_state.previous_response_id is not None
                            and request_state.previous_response_owner_lookup_outcome is None
                        ):
                            previous_response_owner_account_id = await proxy._resolve_websocket_previous_response_owner(
                                previous_response_id=request_state.previous_response_id,
                                api_key=request_state.api_key or api_key,
                                session_id=request_state.session_id,
                                surface="websocket",
                                request_state=request_state,
                            )
                            request_state.previous_response_owner_account_id = previous_response_owner_account_id
                        request_state.preferred_account_id = resolve_required_account_id(
                            ("existing bridge or file", request_state.preferred_account_id),
                            ("turn state", turn_state_owner_account_id),
                            ("previous response", previous_response_owner_account_id),
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

                if (
                    request_state is not None
                    and upstream is not None
                    and account is not None
                    and request_state.affinity_policy.abandon_unavailable_legacy_owner
                ):
                    # Reusing the existing socket would bypass sticky
                    # selection, so the unavailable raw owner would never be
                    # compared, tombstoned, or replaced. A restart is movable
                    # only before dispatch and cannot retire a socket that
                    # still owns another response.
                    async with pending_lock:
                        restart_switch_blocked = _websocket_owner_switch_has_other_pending_requests(
                            request_state,
                            pending_requests,
                        )
                    if restart_switch_blocked:
                        await _reject_websocket_owner_switch_blocked(
                            proxy,
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            account=account,
                            api_key=api_key,
                            response_create_gate=response_create_gate,
                            downstream_activity=downstream_activity,
                            error_code="stream_incomplete",
                            error_message=(
                                "Goal restart cannot switch accounts while another response is still streaming; "
                                "retry after the terminal frame."
                            ),
                        )
                        request_state = None
                        text_data = None
                        payload = None
                        continue
                    await retire_current_upstream()
                    upstream_turn_state = None
                    if client_turn_state_header is None:
                        # Provenance was captured before this switch: absence
                        # here means the API synthesized the forwarded token.
                        # Such account-local state must die with its upstream;
                        # an actual client anchor remains fail-closed instead.
                        filtered_headers = {
                            key: value for key, value in filtered_headers.items() if key.lower() != "x-codex-turn-state"
                        }

                if (
                    request_state is not None
                    and upstream is not None
                    and account is not None
                    and request_state.require_security_work_authorized
                ):
                    capability_account_reusable = False
                    if upstream_requires_security_work_authorized:
                        try:
                            (
                                revalidated_account,
                                _error_code,
                                _error_message,
                            ) = await proxy._revalidate_open_websocket_account(
                                account,
                                request_state=request_state,
                                api_key=request_state.api_key or api_key,
                            )
                        except ProxyResponseError as exc:
                            error = _parse_openai_error(exc.payload)
                            error_code = _normalize_error_code(
                                error.code if error else None,
                                error.type if error else None,
                            )
                            error_message = error.message if error and error.message else "Upstream error"
                            await proxy._release_websocket_request_state_reservation(request_state)
                            await proxy._write_websocket_connect_failure(
                                account_id=account.id,
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
                                error_type=error.type if error and error.type else "server_error",
                                error_param=error.param if error else None,
                                downstream_activity=downstream_activity,
                            )
                            request_state = None
                            text_data = None
                            payload = None
                            continue
                        except BaseException as exc:
                            await proxy._release_websocket_request_state_reservation(request_state)
                            if not isinstance(exc, Exception):
                                raise
                            _facade().logger.exception(
                                "Capability account revalidation failed request_id=%s account_id=%s",
                                request_state.request_log_id or request_state.request_id,
                                account.id,
                            )
                            await proxy._write_websocket_connect_failure(
                                account_id=account.id,
                                api_key=api_key,
                                request_state=request_state,
                                error_code=CAPABILITY_ROUTING_UNAVAILABLE_CODE,
                                error_message=CAPABILITY_ROUTING_UNAVAILABLE_MESSAGE,
                            )
                            await proxy._emit_websocket_terminal_error(
                                websocket,
                                client_send_lock=client_send_lock,
                                request_state=request_state,
                                error_code=CAPABILITY_ROUTING_UNAVAILABLE_CODE,
                                error_message=CAPABILITY_ROUTING_UNAVAILABLE_MESSAGE,
                                error_type="server_error",
                                downstream_activity=downstream_activity,
                            )
                            request_state = None
                            text_data = None
                            payload = None
                            continue
                        if revalidated_account is not None:
                            account = revalidated_account
                            capability_account_reusable = True

                    if not capability_account_reusable:
                        async with pending_lock:
                            capability_switch_blocked = any(
                                pending_request is not request_state for pending_request in pending_requests
                            )
                            if capability_switch_blocked and request_state in pending_requests:
                                pending_requests.remove(request_state)
                        if capability_switch_blocked:
                            await _reject_websocket_capability_switch_blocked(
                                proxy,
                                websocket,
                                client_send_lock=client_send_lock,
                                request_state=request_state,
                                account=account,
                                api_key=api_key,
                                response_create_gate=response_create_gate,
                                downstream_activity=downstream_activity,
                            )
                            request_state = None
                            text_data = None
                            payload = None
                            continue
                        await retire_current_upstream()
                        upstream_turn_state = None
                        if synthesized_turn_state is not None:
                            filtered_headers = {
                                key: value
                                for key, value in filtered_headers.items()
                                if key.lower() != "x-codex-turn-state"
                            }

                if (
                    request_state is not None
                    and upstream is not None
                    and account is not None
                    and request_state.affinity_policy.require_unambiguous_account
                ):
                    # Socket reuse bypasses connect-time selection. Re-run the
                    # ownership-only check for every conversation frame; the
                    # existing socket account is a route, not owner proof.
                    ownership_selection = await proxy._select_account_with_budget_compatible(
                        request_state.started_at + runtime_settings.proxy_request_budget_seconds,
                        request_id=request_state.request_log_id or request_state.request_id,
                        kind="websocket",
                        request_stage=request_state.request_stage,
                        api_key=request_state.api_key or api_key,
                        affinity_policy=request_state.affinity_policy,
                        model=request_state.model,
                        preferred_account_id=account.id,
                        require_security_work_authorized=request_state.require_security_work_authorized,
                        fallback_on_preferred_account_unavailable=False,
                    )
                    if ownership_selection.account is None:
                        await proxy._release_websocket_request_state_reservation(request_state)
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=request_state,
                            error_code=ownership_selection.error_code or "conversation_owner_unavailable",
                            error_message=ownership_selection.error_message
                            or "Conversation owner account is unavailable",
                            error_type="server_error",
                            downstream_activity=downstream_activity,
                        )
                        request_state = None
                        text_data = None
                        payload = None
                        continue

                if request_state is not None and not request_state_registered:
                    response_create_request_state = request_state
                    try:
                        proxy._start_request_state_api_key_reservation_heartbeat(
                            response_create_request_state,
                            api_key=response_create_request_state.api_key or api_key,
                            surface="websocket",
                        )
                        await proxy._acquire_request_state_response_create_admission(
                            response_create_request_state,
                            response_create_gate=response_create_gate,
                        )
                        async with pending_lock:
                            pending_requests.append(response_create_request_state)
                            if shutdown_state.is_draining():
                                # Register-first makes this barrier fail-closed
                                # against a synchronous shutdown signal between
                                # the check and the queue mutation. A turn is
                                # either removed and rejected here or was
                                # already visible as active when drain began.
                                pending_requests.remove(response_create_request_state)
                            else:
                                request_state_registered = True
                        if not request_state_registered:
                            await proxy._release_websocket_request_state_reservation(response_create_request_state)
                            await proxy._emit_websocket_terminal_error(
                                websocket,
                                client_send_lock=client_send_lock,
                                request_state=response_create_request_state,
                                error_code="service_unavailable",
                                error_message="Server is draining",
                                downstream_activity=downstream_activity,
                            )
                            request_state = None
                            text_data = None
                            payload = None
                            continue
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message else "Upstream error"
                        error_type = error.type if error and error.type else "server_error"
                        error_param = error.param if error else None
                        await proxy._release_websocket_request_state_reservation(response_create_request_state)
                        await proxy._write_websocket_connect_failure(
                            account_id=account.id if account else None,
                            api_key=api_key,
                            request_state=response_create_request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                        )
                        await proxy._emit_websocket_terminal_error(
                            websocket,
                            client_send_lock=client_send_lock,
                            request_state=response_create_request_state,
                            error_code=error_code or "upstream_error",
                            error_message=error_message,
                            error_type=error_type,
                            error_param=error_param,
                            downstream_activity=downstream_activity,
                        )
                        await _release_websocket_response_create_gate(
                            response_create_request_state,
                            response_create_gate,
                        )
                        continue
                    except asyncio.CancelledError:
                        await proxy._release_websocket_request_state_reservation(response_create_request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if response_create_request_state in pending_requests:
                                    pending_requests.remove(response_create_request_state)
                        await _release_websocket_response_create_gate(
                            response_create_request_state,
                            response_create_gate,
                        )
                        raise
                    except Exception:
                        await proxy._release_websocket_request_state_reservation(response_create_request_state)
                        if request_state_registered:
                            async with pending_lock:
                                if response_create_request_state in pending_requests:
                                    pending_requests.remove(response_create_request_state)
                        await _release_websocket_response_create_gate(
                            response_create_request_state,
                            response_create_gate,
                        )
                        raise

                if (
                    request_state is not None
                    and request_state_registered
                    and text_data is not None
                    and payload is not None
                    and _is_websocket_response_create(payload)
                    and upstream is not None
                    and account is not None
                ):
                    required_owner_id = request_state.preferred_account_id
                    if required_owner_id is not None and required_owner_id != account.id:
                        async with pending_lock:
                            owner_switch_blocked = _websocket_owner_switch_has_other_pending_requests(
                                request_state, pending_requests
                            )
                            if owner_switch_blocked and request_state in pending_requests:
                                pending_requests.remove(request_state)
                        if owner_switch_blocked:
                            await _reject_websocket_owner_switch_blocked(
                                proxy,
                                websocket,
                                client_send_lock=client_send_lock,
                                request_state=request_state,
                                account=account,
                                api_key=api_key,
                                response_create_gate=response_create_gate,
                                downstream_activity=downstream_activity,
                            )
                            request_state = None
                            text_data = None
                            payload = None
                            continue
                        # The anchor remains unchanged. The normal connect path
                        # below must select the resolved owner or fail closed.
                        await retire_current_upstream()
                        # Turn-state is learned from the retired account's
                        # socket and must never cross the account boundary. A
                        # client-provided turn-state header is the continuity
                        # anchor that forced this owner switch and must still
                        # reach the resolved owner.
                        upstream_turn_state = None
                        if client_turn_state_header is None:
                            filtered_headers = {
                                key: value
                                for key, value in filtered_headers.items()
                                if key.lower() != "x-codex-turn-state"
                            }

                if upstream is None:
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
                    if request_state is not None and request_state.request_stage == "reattach":
                        # A replay can select a different owner.  Do not send
                        # the previous socket's account-scoped turn token while
                        # choosing and opening that replacement connection. If
                        # the client supplied the turn-state header, keep that
                        # logical-turn anchor across the reconnect.
                        upstream_turn_state = None
                        if client_turn_state_header is None:
                            filtered_headers = {
                                key: value
                                for key, value in filtered_headers.items()
                                if key.lower() != "x-codex-turn-state"
                            }
                    elif (
                        upstream_turn_state is not None
                        and upstream_account_id is not None
                        and request_state.preferred_account_id is None
                    ):
                        # The token came from a closed account-owned socket. A
                        # movable bare-session reconnect may spill, but only
                        # after dropping that stale transport-owned token.
                        upstream_turn_state = None
                        filtered_headers = {
                            key: value for key, value in filtered_headers.items() if key.lower() != "x-codex-turn-state"
                        }
                    connect_headers = _facade()._headers_with_turn_state(filtered_headers, upstream_turn_state)
                    account, upstream = await proxy._connect_proxy_websocket(
                        connect_headers,
                        sticky_key=request_affinity.selection_key,
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
                    await release_current_account_lease()
                    account_lease = request_state.websocket_stream_lease
                    request_state.websocket_stream_lease = None
                    if upstream_account_id is not None and account.id != upstream_account_id:
                        # An upstream turn-state token belongs to the account
                        # that issued it.  Never offer it to a replacement
                        # owner when a transparent replay reconnects.
                        upstream_turn_state = None
                    upstream_account_id = account.id
                    upstream_requires_security_work_authorized = request_state.require_security_work_authorized
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
                            proxy_request_budget_seconds=_facade()._stream_request_budget_seconds(
                                runtime_settings,
                                request_transport="websocket",
                            ),
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
                        # Account-cap spillover belongs to connect selection.
                        # Once this shared socket exists, a late create-cap race
                        # rejects only this frame; switching/retiring the socket
                        # could interrupt unrelated in-flight responses.
                        current_settings = await _facade().get_settings_cache().get()
                        request_state.account_response_create_lease = (
                            await proxy._acquire_account_response_create_lease_or_overload(
                                account_id=account.id,
                                request_id=request_state.request_log_id or request_state.request_id,
                                surface="websocket",
                                concurrency_caps=effective_account_concurrency_caps(current_settings),
                            )
                        )
                        request_state.account_response_create_release = proxy._load_balancer.release_account_lease
                    if (
                        text_data is not None
                        and request_state is not None
                        and payload is not None
                        and account is not None
                        and _is_websocket_response_create(payload)
                    ):
                        text_data = _websocket_text_with_account_installation_id(text_data, account)
                        if request_state.fresh_upstream_request_text is not None:
                            fresh_upstream_request_text = _websocket_text_with_account_installation_id(
                                request_state.fresh_upstream_request_text,
                                account,
                            )
                            _websocket_enforce_response_create_text_size(request_state, fresh_upstream_request_text)
                            request_state.fresh_upstream_request_text = fresh_upstream_request_text
                        request_state.request_text = text_data
                        _facade()._enforce_response_create_size_limit(request_state)
                    if (
                        text_data is not None
                        and request_state is not None
                        and payload is not None
                        and upstream_control is not None
                        and upstream_control.reconnect_requested
                        and _is_websocket_response_create(payload)
                    ):
                        # Admission and account-cap waits can outlive a clean
                        # close observed by the upstream reader. Re-check at
                        # the final send boundary and transfer the exact unsent
                        # state without consuming the post-send replay budget.
                        retiring_control = upstream_control
                        if upstream_reader is not None:
                            try:
                                await upstream_reader
                            except asyncio.CancelledError:
                                current_task = asyncio.current_task()
                                if current_task is not None and current_task.cancelling():
                                    raise
                                pass
                        claimed_replay, unsent_request_to_fail = await _claim_unsent_websocket_request_for_reconnect(
                            request_state,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            upstream_control=retiring_control,
                        )
                        # Publish every detached request into an outer cleanup
                        # owner before the next await. Scope cancellation can
                        # then neither lose the reader replay nor the current
                        # unsent state.
                        replay_request_state = claimed_replay
                        request_state_to_fail = unsent_request_to_fail
                        # The account-local slot belongs to the retired
                        # transport owner. A fresh connection re-acquires it
                        # for its selected account; the global turn admission
                        # remains attached to a claimed request state.
                        retired_create_lease_release_task = asyncio.create_task(
                            proxy._release_request_state_account_response_create_lease(request_state),
                            name="proxy-websocket-finalization-retired-create-lease",
                        )
                        _track_websocket_owned_task(proxy, retired_create_lease_release_task)
                        await asyncio.shield(retired_create_lease_release_task)
                        retired_create_lease_release_task = None
                        if request_state_to_fail is not None:
                            owned_request_state = request_state_to_fail
                            request_state_failure_task = asyncio.create_task(
                                proxy._fail_pending_websocket_requests(
                                    account=None,
                                    account_id_value=account.id if account is not None else upstream_account_id,
                                    pending_requests=deque([owned_request_state]),
                                    pending_lock=anyio.Lock(),
                                    error_code="stream_incomplete",
                                    error_message="Upstream websocket closed before request could be sent",
                                    api_key=api_key,
                                    websocket=websocket,
                                    client_send_lock=client_send_lock,
                                    response_create_gate=response_create_gate,
                                    downstream_activity=downstream_activity,
                                    penalize_account=False,
                                ),
                                name="proxy-websocket-finalization-unsent-request",
                            )
                            _track_websocket_owned_task(proxy, request_state_failure_task)
                            request_state_to_fail = None
                            await asyncio.shield(request_state_failure_task)
                            request_state_failure_task = None
                        upstream_reader = None
                        upstream_control = None
                        if upstream is not None:
                            try:
                                await upstream.close()
                            except Exception:
                                _facade().logger.debug(
                                    "Failed to close retired upstream websocket before send",
                                    exc_info=True,
                                )
                        upstream = None
                        await release_current_account_lease()
                        account = None
                        continue
                    if text_data is not None:
                        archive_request_id = None if request_state is None else request_state.archive_request_id
                        if request_state is not None and payload is not None and _is_websocket_response_create(payload):
                            if account is None or not _bind_websocket_request_dispatch_owner(
                                request_state,
                                account_id=account.id,
                                exact_request_text=text_data,
                            ):
                                raise ProxyResponseError(
                                    502,
                                    openai_error(
                                        "previous_response_owner_unavailable",
                                        "Request payload owner account is unavailable; retry later.",
                                        error_type="server_error",
                                    ),
                                )
                            request_state.response_create_sent_at = time.monotonic()
                        with _websocket_archive_request_context(archive_request_id):
                            await upstream.send_text(text_data)
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
                except UpstreamWebSocketTransportError as exc:
                    # send_str/send_bytes may fail after handing bytes to the
                    # kernel. Delivery is uncertain, so replay could duplicate
                    # a response.create even when no output is visible yet.
                    if not await quiesce_current_upstream_reader_after_send_failure():
                        break
                    reader_replay = take_reader_replay_request_state()
                    if reader_replay is not None:
                        # Publish ownership outside upstream_control before the
                        # first finalization await. Scope cancellation can then
                        # find either this slot or the registered child task.
                        request_state_to_fail = reader_replay
                        owned_request_state = request_state_to_fail
                        request_state_failure_task = asyncio.create_task(
                            proxy._fail_pending_websocket_requests(
                                account=account,
                                account_id_value=account.id if account else None,
                                pending_requests=deque([owned_request_state]),
                                pending_lock=anyio.Lock(),
                                error_code=exc.error_code,
                                error_message=str(exc),
                                api_key=api_key,
                                websocket=websocket,
                                client_send_lock=client_send_lock,
                                response_create_gate=response_create_gate,
                                downstream_activity=downstream_activity,
                                penalize_account=not is_account_neutral_websocket_error_code(exc.error_code),
                            ),
                            name="proxy-websocket-finalization-transport-send-failure",
                        )
                        _track_websocket_owned_task(proxy, request_state_failure_task)
                        request_state_to_fail = None
                        await asyncio.shield(request_state_failure_task)
                        request_state_failure_task = None
                    async with pending_lock:
                        sequenced_downstream_replay_refused = any(
                            state.last_downstream_sequence_number is not None for state in pending_requests
                        )
                    await proxy._fail_pending_websocket_requests(
                        account=account,
                        account_id_value=account.id if account else None,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        error_code=exc.error_code,
                        error_message=str(exc),
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                        downstream_activity=downstream_activity,
                        penalize_account=not is_account_neutral_websocket_error_code(exc.error_code),
                        suppress_sequenced_downstream_errors=sequenced_downstream_replay_refused,
                    )
                    if sequenced_downstream_replay_refused:
                        await _close_downstream_after_sequenced_replay_refusal(
                            websocket,
                            downstream_activity,
                        )
                    upstream_control = None
                    upstream = None
                    await release_current_account_lease()
                    account = None
                    continue
                except Exception:
                    if not await quiesce_current_upstream_reader_after_send_failure():
                        break
                    reader_replay = take_reader_replay_request_state()
                    replay_refusal_reasons: list[str] = []
                    replay_candidate = reader_replay
                    if replay_candidate is None:
                        replay_candidate = await _pop_replayable_precreated_websocket_request_state(
                            pending_requests,
                            pending_lock=pending_lock,
                            replay_refusal_reasons=replay_refusal_reasons,
                        )
                    if replay_candidate is not None:
                        replay_request_state = replay_candidate
                        _facade().logger.info(
                            "Transparent websocket replay after upstream send failure request_id=%s",
                            replay_candidate.request_log_id or replay_candidate.request_id,
                        )
                        retired_create_lease_release_task = asyncio.create_task(
                            proxy._release_request_state_account_response_create_lease(replay_candidate),
                            name="proxy-websocket-finalization-retired-create-lease",
                        )
                        _track_websocket_owned_task(proxy, retired_create_lease_release_task)
                        await asyncio.shield(retired_create_lease_release_task)
                        retired_create_lease_release_task = None
                        upstream_control = None
                        upstream = None
                        await release_current_account_lease()
                        account = None
                        continue
                    sequenced_downstream_replay_refused = "sequenced_downstream_frame" in replay_refusal_reasons
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
                        suppress_sequenced_downstream_errors=sequenced_downstream_replay_refused,
                    )
                    if sequenced_downstream_replay_refused:
                        await _close_downstream_after_sequenced_replay_refusal(
                            websocket,
                            downstream_activity,
                        )
                    upstream_control = None
                    upstream = None
                    await release_current_account_lease()
                    account = None
                    continue
        except asyncio.CancelledError:
            scope_cancelled = True
            raise
        finally:
            remaining_drain_timeout = shutdown_state.remaining_drain_timeout_seconds()
            cleanup_timeout = (
                _WEBSOCKET_SCOPE_CLEANUP_TIMEOUT_SECONDS
                if remaining_drain_timeout is None
                else max(float(remaining_drain_timeout), 0.0)
            )
            task_cleanup_timeout = (
                _facade()._TASK_CANCEL_TIMEOUT_SECONDS if remaining_drain_timeout is None else cleanup_timeout
            )
            cleanup_phase = "not_started"

            async def finalize_websocket_scope() -> None:
                nonlocal cleanup_phase
                nonlocal replay_request_state
                nonlocal request_state_failure_task
                nonlocal request_state_to_fail
                nonlocal retired_create_lease_release_task
                nonlocal upstream_reader
                reader_to_await = upstream_reader
                if reader_to_await is not None and not upstream_reader_cancel_requested:
                    # Cancel exactly once before close. Some transports defer
                    # cancellation while receive() unwinds and need close() to
                    # release that wait.
                    reader_to_await.cancel()
                if upstream is not None:
                    cleanup_phase = "upstream_close"
                    await _close_websocket_upstream_for_cleanup(
                        proxy,
                        upstream,
                        timeout_seconds=task_cleanup_timeout,
                    )
                if reader_to_await is not None:
                    try:
                        cleanup_phase = "upstream_reader"
                        await _facade()._await_cancelled_task(
                            reader_to_await,
                            label="proxy websocket upstream reader",
                            cancel=False,
                            cleanup_tasks=proxy._background_cleanup_tasks,
                        )
                    except Exception:
                        # Reader failure must not skip lease release or the
                        # one terminal cleanup of still-owned request state.
                        _facade().logger.warning(
                            "Upstream websocket reader failed during scope cleanup",
                            exc_info=True,
                        )
                    upstream_reader = None
                if retired_create_lease_release_task is not None:
                    try:
                        cleanup_phase = "retired_create_lease"
                        await _facade()._await_cancelled_task(
                            retired_create_lease_release_task,
                            timeout_seconds=task_cleanup_timeout,
                            label="proxy websocket retired create lease release",
                            cancel=False,
                        )
                    except Exception:
                        _facade().logger.warning(
                            "Retired websocket create lease release failed during scope cleanup",
                            exc_info=True,
                        )
                    retired_create_lease_release_task = None
                if request_state_failure_task is not None:
                    try:
                        cleanup_phase = "unsent_request"
                        await _facade()._await_cancelled_task(
                            request_state_failure_task,
                            timeout_seconds=task_cleanup_timeout,
                            label="proxy websocket unsent request finalization",
                            cancel=False,
                        )
                    except Exception:
                        _facade().logger.warning(
                            "Unsent websocket request finalization failed during scope cleanup",
                            exc_info=True,
                        )
                    request_state_failure_task = None
                if replay_request_state is None and upstream_control is not None:
                    replay_request_state = upstream_control.replay_request_state
                    upstream_control.replay_request_state = None
                if request_state_to_fail is not None:
                    cleanup_phase = "unsent_request"
                    await proxy._fail_pending_websocket_requests(
                        account=None,
                        account_id_value=account.id if account is not None else upstream_account_id,
                        pending_requests=deque([request_state_to_fail]),
                        pending_lock=anyio.Lock(),
                        error_code="client_disconnected" if downstream_activity.disconnected else "stream_incomplete",
                        error_message=(
                            "Downstream websocket disconnected before response.completed"
                            if downstream_activity.disconnected
                            else "Websocket scope cancelled before response.completed"
                        ),
                        api_key=api_key,
                        response_create_gate=response_create_gate,
                        status="cancelled",
                        penalize_account=False,
                    )
                    request_state_to_fail = None
                if replay_request_state is not None:
                    cleanup_phase = "replay_request"
                    await proxy._fail_pending_websocket_requests(
                        account=None,
                        account_id_value=account.id if account is not None else upstream_account_id,
                        pending_requests=deque([replay_request_state]),
                        pending_lock=anyio.Lock(),
                        error_code="client_disconnected" if downstream_activity.disconnected else "stream_incomplete",
                        error_message=(
                            "Downstream websocket disconnected before response.completed"
                            if downstream_activity.disconnected
                            else "Websocket scope cancelled before response.completed"
                        ),
                        api_key=api_key,
                        response_create_gate=response_create_gate,
                        status="cancelled",
                        penalize_account=False,
                    )
                client_disconnected = downstream_activity.disconnected
                cleanup_phase = "pending_requests"
                await proxy._fail_pending_websocket_requests(
                    account=None if client_disconnected or scope_cancelled else account,
                    account_id_value=account.id if account is not None else upstream_account_id,
                    pending_requests=pending_requests,
                    pending_lock=pending_lock,
                    error_code="client_disconnected" if client_disconnected else "stream_incomplete",
                    error_message=(
                        "Downstream websocket disconnected before response.completed"
                        if client_disconnected
                        else "Websocket scope cancelled before response.completed"
                        if scope_cancelled
                        else "Upstream websocket closed before response.completed"
                    ),
                    api_key=api_key,
                    websocket=None if client_disconnected or scope_cancelled else websocket,
                    client_send_lock=None if client_disconnected or scope_cancelled else client_send_lock,
                    response_create_gate=response_create_gate,
                    downstream_activity=downstream_activity,
                    status="cancelled" if client_disconnected or scope_cancelled else "error",
                    penalize_account=not (client_disconnected or scope_cancelled),
                )
                try:
                    cleanup_phase = "connection_lease"
                    await release_current_account_lease()
                except Exception:
                    # Connection-lease cleanup must never replace cancellation
                    # or skip the already-published request finalization work.
                    _facade().logger.warning(
                        "Failed to release websocket connection lease during scope cleanup",
                        exc_info=True,
                    )
                cleanup_phase = "complete"

            cleanup_task = asyncio.create_task(
                finalize_websocket_scope(),
                name="proxy-websocket-finalization-scope-cleanup",
            )
            _track_websocket_owned_task(proxy, cleanup_task)

            def log_scope_cleanup_failure(done_task: asyncio.Task[None]) -> None:
                if done_task.cancelled():
                    return
                exception = done_task.exception()
                if exception is not None:
                    _facade().logger.warning(
                        "Websocket scope cleanup failed",
                        exc_info=(type(exception), exception, exception.__traceback__),
                    )

            cleanup_task.add_done_callback(log_scope_cleanup_failure)
            done, _ = await asyncio.wait(
                {cleanup_task},
                timeout=max(float(cleanup_timeout), 0.0),
            )
            if not done:
                _facade().logger.warning(
                    "Websocket scope cleanup exceeded its cleanup budget "
                    "timeout_seconds=%.3f cleanup_phase=%s background_cleanup_tasks=%d",
                    max(float(cleanup_timeout), 0.0),
                    cleanup_phase,
                    sum(1 for task in proxy._background_cleanup_tasks if not task.done()),
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
        prohibit_fast_mode: bool = False,
        continuity_state: "_WebSocketContinuityState | None" = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        conversation_id: str | None = None,
        client_ip: str | None = None,
        synthesized_turn_state: str | None = None,
        capability_header_values: tuple[str, ...] | None = None,
    ) -> _PreparedWebSocketRequest:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        refreshed_api_key = await proxy._refresh_websocket_api_key_policy(api_key)
        raw_client_metadata = payload.get("client_metadata")
        capability_intent = parse_routing_intent(
            headers,
            api_key=refreshed_api_key,
            client_metadata=raw_client_metadata,
            header_values=capability_header_values,
            client_metadata_values=_websocket_capability_metadata_values(payload),
        )
        validate_top_level_compaction_trigger_input_shape(payload)
        responses_payload = normalize_responses_request_payload(
            payload,
            openai_compat=openai_cache_affinity,
        )
        # The client's raw model, captured before enforcement normalizes
        # aliases (``gpt-5-high`` -> ``gpt-5``). The source-ownership guards
        # must judge the raw alias too, or an alias-only model source is
        # missed on the WebSocket paths while the HTTP path routes the same
        # request via ``raw_source_model``. Mirrors ``api.py::responses``
        # exactly, including the enforced-model substitution here and the
        # fast-mode correction after enforcement below.
        raw_source_model = effective_model_for_api_key(refreshed_api_key, responses_payload.model)
        # The effort the normalizer replaced is discarded here on purpose: the
        # WebSocket transport never reaches a model source, so the rewrite that
        # works around the backend hang must stick.
        service_tier_was_enforced = apply_api_key_enforcement(
            responses_payload,
            refreshed_api_key,
            prohibit_fast_mode=prohibit_fast_mode,
        ).service_tier_was_enforced
        if prohibit_fast_mode and model_alias_requests_fast_mode(raw_source_model):
            raw_source_model = responses_payload.model
        apply_enforced_service_tier_model_fallback(
            responses_payload,
            service_tier_was_enforced=service_tier_was_enforced,
        )
        # Judged on the full client input, before the websocket-specific
        # trimming and anchor injection below rewrite it — the same payload
        # the HTTP route evaluates for its source-selection gate.
        try:
            source_route_excluded = responses_source_route_excluded(responses_payload)
        except ClientPayloadError:
            # HTTP rejects a malformed compaction trigger with a 400; the
            # WebSocket path has always forwarded such frames verbatim, so a
            # parse failure keeps the source guards active instead of
            # changing that behavior here.
            source_route_excluded = False
        normalized_payload = responses_payload.to_payload()
        stripped_client_metadata = strip_capability_metadata(normalized_payload.get("client_metadata"))
        if stripped_client_metadata is not normalized_payload.get("client_metadata"):
            responses_payload = responses_payload.model_copy(update={"client_metadata": stripped_client_metadata})
            normalized_payload = responses_payload.to_payload()
        body_uses_responses_lite = _payload_uses_responses_lite(normalized_payload)
        trusted_incremental_responses_lite = bool(
            not body_uses_responses_lite
            and continuity_state is not None
            and continuity_state.responses_lite_model == responses_payload.model
            and continuity_state.responses_lite_response_id is not None
            and responses_payload.previous_response_id == continuity_state.responses_lite_response_id
            and _payload_has_responses_lite_websocket_marker(normalized_payload)
        )
        client_metadata = _facade()._response_create_client_metadata(
            normalized_payload,
            headers=headers,
            preserve_existing_responses_lite=trusted_incremental_responses_lite,
        )
        next_responses_lite_model = (
            responses_payload.model if body_uses_responses_lite or trusted_incremental_responses_lite else None
        )
        if client_metadata is not None or "client_metadata" in normalized_payload:
            responses_payload = responses_payload.model_copy(update={"client_metadata": client_metadata})
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
        full_resend_client_metadata = client_metadata
        if client_full_resend_retry_safe and client_full_resend_input_items is not None:
            if trusted_incremental_responses_lite and client_metadata is not None:
                # The transparent fresh replay clears ``previous_response_id``
                # and this input carries no ``additional_tools`` prefix, so the
                # replay loses the linkage that justified the trusted marker.
                # Strip it so the replay does not advertise Responses Lite.
                stripped_metadata = {
                    key: value
                    for key, value in client_metadata.items()
                    if key.lower() != CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY
                }
                full_resend_client_metadata = stripped_metadata or None
            client_full_resend_payload = responses_payload.model_copy(
                update={
                    "previous_response_id": None,
                    "input": client_full_resend_input_items,
                    "client_metadata": full_resend_client_metadata,
                }
            )
        validate_model_access(refreshed_api_key, responses_payload.model)
        proxy._raise_for_unsupported_input_image_references(responses_payload)
        rewritten_file_account_id = await proxy._resolve_file_account_for_responses(responses_payload, headers)
        original_full_resend_payload: ResponsesRequest | None = None
        original_input_item_count: int | None = None
        original_input_fingerprint: str | None = None
        # Classify restart authority from the complete normalized client body,
        # before ordinary direct-WebSocket continuity injects a
        # ``previous_response_id`` and trims historical input. That injected
        # anchor is account-owned and would both erase the restart capability
        # and make the payload unsafe for the replacement account. A proven
        # goal restart must retain the complete resend through selection.
        goal_restart_full_resend = _request_allows_unavailable_legacy_owner_abandonment(responses_payload)
        restart_affinity_payload = responses_payload
        session_anchor = None
        if not goal_restart_full_resend:
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
                            pending_call_types=continuity_state.last_pending_tool_call_types,
                        )
                    }
                )
                _facade().logger.warning(
                    "websocket_interrupted_tool_outputs_injected previous_response_id=%s missing_call_count=%s",
                    responses_payload.previous_response_id,
                    len(missing_call_ids),
                )
        session_id = _owner_lookup_session_id_from_headers(
            headers,
            synthesized_turn_state=synthesized_turn_state,
        )
        capability_route = await proxy._capability_router.route(
            capability_intent,
            api_key_id=refreshed_api_key.id if refreshed_api_key is not None else None,
            aliases=capability_lineage_aliases(
                headers,
                session_id=_sticky_key_from_session_header(headers),
                turn_state=_sticky_key_from_turn_state_header(headers) or synthesized_turn_state,
                previous_response_ids=(responses_payload.previous_response_id,),
                client_metadata=client_metadata,
            ),
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
            request_state, text_data = proxy._prepare_response_bridge_request_state(
                responses_payload,
                api_key=refreshed_api_key,
                api_key_reservation=reservation,
                include_type_field=True,
                attach_event_queue=False,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
                client_metadata=client_metadata,
                headers=headers,
                session_id=session_id,
            )
        except ProxyResponseError:
            await proxy._release_websocket_reservation(reservation)
            raise
        request_state.useragent = useragent
        request_state.useragent_group = useragent_group
        request_state.conversation_id = conversation_id
        request_state.client_ip = client_ip
        request_state.raw_source_model = raw_source_model
        request_state.source_route_excluded = source_route_excluded
        request_state.responses_lite_model = next_responses_lite_model
        request_state.expose_stale_previous_response_classifier = codex_session_affinity
        request_state.require_security_work_authorized = capability_route.require_security_work_authorized
        request_state.durable_capability_lineage_required = capability_route.require_security_work_authorized
        original_full_resend_input: JsonValue | None = None
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
                original_full_resend_input = (
                    original_full_resend_payload.get("input")
                    if isinstance(original_full_resend_payload, dict)
                    else original_full_resend_payload.input
                )
            request_state.fresh_upstream_request_is_retry_safe = bool(
                request_state.fresh_upstream_request_text is not None
                and isinstance(original_full_resend_input, list)
                and _websocket_input_items_are_self_contained_fresh_replay(
                    cast(list[JsonValue], original_full_resend_input)
                )
            )
            if not request_state.fresh_upstream_request_is_retry_safe:
                request_state.fresh_upstream_request_text = None
            request_state.fresh_upstream_request_responses_lite_model = (
                responses_payload.model if body_uses_responses_lite else None
            )
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
                client_metadata=full_resend_client_metadata,
                request_state=request_state,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
            )
            request_state.fresh_upstream_request_is_retry_safe = request_state.fresh_upstream_request_text is not None
            # A marker-only trusted incremental frame yields a fresh body with
            # the reserved marker stripped, so the replay must not be treated
            # as a Lite request when it is accepted upstream.
            request_state.fresh_upstream_request_responses_lite_model = (
                responses_payload.model if body_uses_responses_lite else None
            )
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
            # Only the proven restart uses the pre-injection body. Ordinary
            # full resends must be classified after anchor injection so they
            # cannot accidentally gain soft-session mobility.
            restart_affinity_payload if goal_restart_full_resend else responses_payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=sticky_threads_enabled,
            api_key=api_key,
            synthesized_turn_state=synthesized_turn_state,
        )
        sticky_key_source = "none"
        if affinity_policy.codex_session_source == "thread_header":
            sticky_key_source = "thread_header"
        elif affinity_policy.kind == StickySessionKind.CODEX_SESSION:
            turn_state_key = _sticky_key_from_turn_state_header(headers)
            if turn_state_key is not None and turn_state_key == synthesized_turn_state:
                sticky_key_source = "generated_turn_state"
            elif turn_state_key is not None:
                sticky_key_source = "turn_state_header"
            else:
                sticky_key_source = "session_header"
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
        # turn often references a file_id alongside process-session locality.
        # The file pin is a hard owner and overrides session/cache hints; only
        # resolved turn-state or previous-response ownership may take
        # precedence, with conflicting hard signals failing closed.
        request_state.preferred_account_id = resolve_required_account_id(
            ("previous response or bridge", request_state.preferred_account_id),
            ("input file", rewritten_file_account_id),
        )
        request_state.file_required_preferred_account = rewritten_file_account_id is not None

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
            request_state.fresh_upstream_request_responses_lite_model = next_responses_lite_model

        return _PreparedWebSocketRequest(
            text_data=text_data,
            request_state=request_state,
            affinity_policy=affinity_policy,
        )

    async def _revalidate_open_websocket_account(
        self,
        account: Account,
        *,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
    ) -> tuple[Account | None, str | None, str | None]:
        """Check whether an open socket may serve the next movable turn."""
        proxy = cast(_WebSocketServiceProtocol, self)
        deadline = _websocket_connect_deadline(
            request_state,
            _facade().get_settings().proxy_request_budget_seconds,
        )
        selection = await proxy._select_account_with_budget_compatible(
            deadline,
            request_id=request_state.request_log_id or request_state.request_id,
            kind="websocket_revalidate",
            request_stage=request_state.request_stage,
            api_key=api_key,
            model=request_state.model,
            service_tier=request_state.requested_service_tier,
            preferred_account_id=account.id,
            require_security_work_authorized=request_state.require_security_work_authorized,
            fallback_on_preferred_account_unavailable=False,
        )
        await proxy._load_balancer.release_account_lease(selection.lease)
        selected_account = selection.account
        if selected_account is None or selected_account.id != account.id:
            return None, selection.error_code, selection.error_message
        return selected_account, None, None

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
        downstream_activity: _DownstreamWebSocketActivity | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
    ) -> tuple[Account | None, UpstreamWebSocket | None]:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy

        async def _record_or_defer_confirmed_route_backoff(account: Account) -> None:
            if request_state.api_key_reservation is not None:
                request_state.deferred_account_error_backoffs.setdefault(account.id, account)
                return
            await proxy._load_balancer.record_error_backoff(account)

        if (
            request_state.useragent is None
            and request_state.useragent_group is None
            and request_state.conversation_id is None
        ):
            (
                request_state.useragent,
                request_state.useragent_group,
                request_state.conversation_id,
            ) = _request_log_client_fields(headers)
        base_settings = _facade().get_settings()
        deadline = _websocket_connect_deadline(
            request_state,
            _facade()._stream_request_budget_seconds(
                base_settings,
                request_transport="websocket",
            ),
        )
        # Model sources are only reachable from the HTTP request path. Fail the
        # WebSocket connect instead of dispatching a source-owned model to a
        # subscription account, which the upstream rejects with "The '<model>'
        # model is not supported when using Codex with a ChatGPT account."
        # Codex clients fall back to the HTTP transport when a WebSocket
        # connect fails, and that path routes to the source correctly.
        #
        # Evaluated once per connect series rather than inside the failover
        # loop below: source ownership is a property of the requested model, so
        # re-resolving it per attempt would only repeat the same lookup. The
        # per-request api key is used (rather than the session key) so a policy
        # refresh mid-session cannot make this disagree with the equivalent
        # check on the prepared-request path.
        #
        # Structural HTTP exclusions and a recorded previous-response account
        # owner skip the guard. Response-id syntax is provider-opaque, so a
        # configured source with no recorded subscription owner still falls
        # back to the HTTP source path.
        if (
            not request_state.source_route_excluded
            and request_state.previous_response_owner_account_id is None
            and await responses_model_is_source_owned(
                model,
                request_state.api_key or api_key,
                # ``model`` is the session loop's post-enforcement
                # ``request_state.model``; the raw client alias captured at
                # preparation is what an alias-only source is registered under.
                raw_model=request_state.raw_source_model,
            )
        ):
            source_model = request_state.raw_source_model or model
            message = (
                f"Model {source_model!r} is served by an OpenAI-compatible model source, which is only "
                "reachable over the HTTP transport; retry the request over HTTPS."
            )
            _facade().logger.info(
                "Websocket model source requires http transport request_id=%s model=%s raw_model=%s api_key_present=%s",
                request_state.request_log_id or request_state.request_id,
                model,
                request_state.raw_source_model,
                (request_state.api_key or api_key) is not None,
            )
            await proxy._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=None,
                api_key=request_state.api_key or api_key,
                request_state=request_state,
                # 503 (not 4xx) is deliberate: Codex clients only fall back to
                # the HTTP transport when a WebSocket connect fails at the
                # service level. A 4xx is treated as terminal and surfaces to
                # the user instead of retrying over HTTPS.
                status_code=503,
                payload=openai_error(
                    "model_source_requires_http_transport",
                    message,
                    error_type="server_error",
                ),
                error_code="model_source_requires_http_transport",
                error_message=message,
            )
            return None, None
        max_attempts = _facade()._WEBSOCKET_MAX_ACCOUNT_ATTEMPTS
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        last_failover_exc: ProxyResponseError | None = None
        last_failover_account: Account | None = None
        for attempt in range(max_attempts):
            is_retry = attempt > 0
            forced_refresh_account_id = request_state.force_refresh_account_id
            preferred_account_id = (
                request_state.replay_required_account_id
                or forced_refresh_account_id
                or request_state.preferred_account_id
            )
            turn_state_owner_required = (
                request_state.affinity_policy.codex_session_source == "turn_state"
                and request_state.preferred_account_id is not None
            )
            require_preferred_account = (
                (request_state.previous_response_id is not None and request_state.preferred_account_id is not None)
                or request_state.replay_required_account_id is not None
                or request_state.file_required_preferred_account
                or turn_state_owner_required
            )
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
                    downstream_activity=downstream_activity,
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

            selected_account_model_replacement = (
                request_state.precreated_replay_reason == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
                and account.id != request_state.precreated_replay_account_id
            )
            if selected_account_model_replacement:
                # Preserve the rejected account's 400 only when selection
                # cannot find a replacement. Once this replacement attempt
                # starts, a connection/open failure belongs to the replacement.
                _clear_websocket_precreated_replay_fallback(request_state)

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
                    # Gate transient transport-level refresh failover on whether
                    # the request is *genuinely pinned* (hard-required account:
                    # session continuity or file pin), NOT merely on a preferred
                    # account being set. A forced-refresh reconnect auth replay
                    # sets both force_refresh_account_id and preferred_account_id
                    # to the stale account even for movable requests, so keying
                    # off preferred_account_id would wrongly strand a movable
                    # request on the stale account when its refresh claim is held
                    # by another replica. Hard-pinned requests
                    # (require_preferred_account) still stay on their account.
                    can_transient_failover=not require_preferred_account,
                )
            except _WebSocketTransientRefreshFailover as failover:
                # A transient, transport-level refresh failure (e.g. the
                # account's refresh claim is held by another replica) reached
                # the connect path. Release the skipped account's already-
                # acquired stream lease so it does not keep consuming a
                # stream-concurrency slot for a connection that never opens,
                # exclude it, and reselect a healthy account.
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                selected_stream_lease = None
                # Record a capacity-style failure so that if every account
                # attempt hits a transient refresh-claim failover, the loop
                # still surfaces a proper terminal error after exhaustion
                # instead of returning (None, None) silently. The account
                # credentials are fine (its refresh claim is just held by
                # another replica), so this must be a 503/capacity-style
                # upstream error, NOT a bogus 401 invalid_api_key.
                refresh_failure = ProxyResponseError(
                    503,
                    openai_error(
                        "upstream_unavailable",
                        "Account refresh is temporarily unavailable; no healthy account could be reached.",
                        error_type="server_error",
                    ),
                )
                if selected_account_model_replacement:
                    await proxy._emit_websocket_connect_failure(
                        websocket,
                        client_send_lock=client_send_lock,
                        account_id=account.id,
                        api_key=api_key,
                        request_state=request_state,
                        status_code=refresh_failure.status_code,
                        payload=refresh_failure.payload,
                        error_code="upstream_unavailable",
                        error_message=(
                            "Account refresh is temporarily unavailable; no healthy account could be reached."
                        ),
                    )
                    return None, None
                excluded_account_ids.add(failover.account_id)
                last_failover_exc = refresh_failure
                last_failover_account = account
                continue
            except ProxyResponseError as exc:
                confirmed_pre_dispatch = is_confirmed_pre_dispatch_transport_error(exc)
                if selected_account_model_replacement:
                    # The account/model retry budget selected this replacement;
                    # its connection failure must be surfaced rather than
                    # consuming another account through generic failover.
                    action = "surface"
                else:
                    action = await proxy._decide_websocket_failover_action(
                        account=account,
                        exc=exc,
                        request_state=request_state,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        deterministic_failover_enabled=getattr(base_settings, "deterministic_failover_enabled", True),
                        require_preferred_account=require_preferred_account,
                    )
                if action == "failover_next":
                    # Release the dead route's stream lease before recording
                    # the backoff so its concurrency slot never outlives the
                    # failed connection attempt.
                    await proxy._load_balancer.release_account_lease(selected_stream_lease)
                    selected_stream_lease = None
                    if confirmed_pre_dispatch:
                        await _record_or_defer_confirmed_route_backoff(account)
                    last_failover_exc = exc
                    last_failover_account = account
                    excluded_account_ids.add(account.id)
                    continue
                error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                error_message = error.message if error else None
                await proxy._load_balancer.release_account_lease(selected_stream_lease)
                selected_stream_lease = None
                if confirmed_pre_dispatch:
                    await _record_or_defer_confirmed_route_backoff(account)
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
            _clear_websocket_precreated_replay_fallback(request_state)
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
        downstream_activity: _DownstreamWebSocketActivity | None = None,
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
        while True:
            try:
                selection = await proxy._select_account_with_budget_compatible(
                    deadline,
                    request_id=request_state.request_log_id or request_state.request_id,
                    kind="websocket",
                    api_key=api_key,
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    reallocate_sticky=reallocate_sticky,
                    sticky_source=request_state.affinity_policy.codex_session_source,
                    legacy_sticky_key=request_state.affinity_policy.legacy_selection_key,
                    legacy_continuity_source=request_state.affinity_policy.legacy_continuity_source,
                    sticky_seed_key=request_state.affinity_policy.seed_selection_key,
                    sticky_seed_kind=request_state.affinity_policy.seed_selection_kind,
                    spill_bare_session_on_account_cap=request_state.affinity_policy.spill_on_account_cap,
                    abandon_unavailable_legacy_owner=(request_state.affinity_policy.abandon_unavailable_legacy_owner),
                    require_unambiguous_account=request_state.affinity_policy.require_unambiguous_account,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    model=model,
                    service_tier=request_state.requested_service_tier,
                    exclude_account_ids=exclude_account_ids,
                    preferred_account_id=preferred_account_id,
                    require_security_work_authorized=require_security_work_authorized,
                    lease_kind="stream",
                    request_stage=request_state.request_stage,
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
            if account is not None:
                break
            if selection.error_code == USAGE_LIMIT_REACHED:
                break

            async def _heartbeat(remaining_seconds: float) -> None:
                event = _account_capacity_wait_payload(
                    request_state,
                    request_id=request_state.request_log_id or request_state.request_id,
                    reason=selection.error_message,
                    retry_after_seconds=remaining_seconds,
                )
                await proxy._send_downstream_websocket_text(
                    websocket,
                    client_send_lock=client_send_lock,
                    text=json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                    downstream_activity=downstream_activity,
                )

            if not await _sleep_for_account_selection_recovery(
                selection,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="websocket",
                request_stage=request_state.request_stage,
                model=model,
                max_sleep_seconds=_facade()._remaining_budget_seconds(deadline),
                request_state=request_state,
                heartbeat=_heartbeat,
            ):
                break
            # A wait clipped to the remaining request budget is still a
            # completed wait. Preserve the selection error that caused it
            # instead of performing one more selection that can only replace
            # the original local-cap 429 with upstream_request_timeout.
            if _facade()._remaining_budget_seconds(deadline) <= 0:
                break

        account = selection.account
        if (
            account is not None
            and request_state.replay_required_account_id is None
            and request_state.request_text is not None
            and not _facade()._websocket_request_text_is_account_neutral_fresh_replay(request_state.request_text)
        ):
            request_state.preferred_account_id = account.id
            request_state.replay_required_account_id = account.id
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
        durable_capability_pool_missing = bool(
            require_security_work_authorized
            and request_state.durable_capability_lineage_required
            and not require_preferred_account
            and not _facade()._is_local_account_cap_code(selection.error_code)
        )
        if (
            defer_no_account_error
            and not durable_capability_pool_missing
            and not _facade()._is_local_account_cap_code(selection.error_code)
        ):
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
        if durable_capability_pool_missing or (
            require_security_work_authorized and error_code == _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
        ):
            await proxy._emit_websocket_security_work_missing_pool(
                websocket,
                client_send_lock=client_send_lock,
                account_id=preferred_account_id,
                api_key=api_key,
                request_state=request_state,
            )
            return None
        if require_preferred_account and preferred_account_id is not None:
            if _facade()._is_local_account_cap_code(error_code):
                status_code, error_payload = selection_failure_response(selection)
                await proxy._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=preferred_account_id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=status_code,
                    payload=error_payload,
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
        status_code, error_payload = selection_failure_response(selection)
        await proxy._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=None,
            api_key=api_key,
            request_state=request_state,
            status_code=status_code,
            payload=error_payload,
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
        if request_state.durable_capability_lineage_required:
            _clear_websocket_precreated_replay_fallback(request_state)
            error_code = _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
            error_message = _CAPABILITY_REQUIRED_NO_AUTHORIZED_ACCOUNTS_MESSAGE
            error_type = "server_error"
            status_code = 503
            advisory_message = _CAPABILITY_REQUIRED_NO_AUTHORIZED_ACCOUNTS_MESSAGE
            advisory_action = _CAPABILITY_REQUIRED_NO_AUTHORIZED_ACCOUNTS_ACTION
        else:
            error_code = request_state.error_code_override or _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
            error_message = (
                request_state.error_message_override or _facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE
            )
            error_type = request_state.error_type_override or (
                "invalid_request_error" if request_state.error_code_override is not None else "server_error"
            )
            status_code = request_state.error_http_status_override or (
                400 if request_state.error_code_override is not None else 503
            )
            advisory_message = _facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE
            advisory_action = "forward_original_security_work_error"
        async with client_send_lock:
            await websocket.send_text(
                json.dumps(
                    _facade()._security_work_advisory_event(
                        code=_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                        message=advisory_message,
                        request_id=request_state.request_log_id or request_state.request_id,
                        action=advisory_action,
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
            status_code=status_code,
            payload=openai_error(
                error_code,
                error_message,
                error_type=error_type,
            ),
            error_code=error_code,
            error_message=error_message,
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
        can_transient_failover: bool = False,
    ) -> tuple[Account, UpstreamWebSocket] | None:
        recovery = ProcessNetworkRecovery(
            transport="websocket",
            request_id=request_state.request_log_id or request_state.request_id,
            account_id=account.id,
        )
        while True:
            try:
                result = await self._try_open_websocket_connect_attempt_once(
                    account,
                    headers,
                    deadline=deadline,
                    api_key=api_key,
                    request_state=request_state,
                    client_send_lock=client_send_lock,
                    websocket=websocket,
                    force_refresh=force_refresh,
                    can_transient_failover=can_transient_failover,
                )
                recovery.log_recovered()
                return result
            except ProxyResponseError as exc:
                decision = await _wait_for_process_network_recovery(
                    recovery,
                    exc,
                    deadline=deadline,
                )
                if decision == "retry":
                    continue
                if decision == "exhausted":
                    _raise_proxy_budget_exhausted()
                raise

    async def _try_open_websocket_connect_attempt_once(
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
        can_transient_failover: bool = False,
    ) -> tuple[Account, UpstreamWebSocket] | None:
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
                can_transient_failover=can_transient_failover,
            )
        except RefreshError as exc:
            if exc.is_permanent:
                await proxy._load_balancer.mark_permanent_failure(account, exc.code)
            elif can_transient_failover and is_transient_refresh_contention(exc):
                # Transient CROSS-REPLICA refresh contention on the proactive
                # freshness check: benign claim contention (the account's refresh
                # claim is held by another replica) OR a post-exchange persist/status
                # CAS conflict (``token_persist_conflict`` / ``status_downgrade_conflict``).
                # This is NOT a genuine ``transport_error`` OAuth failure — the
                # account's credentials are healthy — so do not surface a bogus 401
                # invalid_api_key. Signal the connect loop to release this account's
                # stream lease, exclude it, and fail over to a healthy account
                # WITHOUT an account-health penalty. Log a post-exchange persist
                # conflict distinctly (rarer, more-serious than benign contention).
                _log_websocket_persist_conflict("freshness-check", exc, account.id)
                raise _WebSocketTransientRefreshFailover(account.id) from exc
            elif is_transient_refresh_contention(exc):
                # PINNED refresh contention (require_preferred_account:
                # previous_response_id session continuity or file ownership).
                # can_transient_failover is False, so the movable failover
                # branch above is correctly skipped -- a pinned request must
                # not cross accounts. The owner account's credentials are
                # healthy; its refresh claim is merely held by a peer replica,
                # so a 401 invalid_api_key would be misleading and terminal.
                # Stay on the owner (no crossing, no permanent mark), release
                # the acquired stream lease (the caller releases it when this
                # returns None), and surface a RETRYABLE upstream_unavailable so
                # the client can retry once the peer replica releases the claim.
                # (Also covers a pinned post-exchange persist/status CAS conflict,
                # logged distinctly.)
                _log_websocket_persist_conflict("pinned freshness-check", exc, account.id)
                message = exc.message or _WEBSOCKET_PINNED_REFRESH_UNAVAILABLE_MESSAGE
                await proxy._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=503,
                    payload=openai_error(
                        "upstream_unavailable",
                        message,
                        error_type="server_error",
                    ),
                    error_code="upstream_unavailable",
                    error_message=message,
                )
                return None
            elif exc.transport_error:
                # A GENUINE OAuth transport failure (``code == "transport_error"``:
                # the refresh request itself timed out / its upstream connection
                # failed). This IS the account/route's fault, so treat it exactly
                # like the aiohttp/connect transport handler below — raise a
                # retryable 502 ``upstream_unavailable`` so the connect loop
                # applies its normal transport-failure failover/health handling —
                # rather than a misleading terminal 401 invalid_api_key or the
                # unpenalized claim-contention failover.
                message = exc.message or str(exc) or "Request to upstream timed out"
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "upstream_unavailable",
                        message,
                        error_type="server_error",
                    ),
                ) from exc
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
            error_code = process_network_error_code(exc, fallback="upstream_unavailable")
            raise ProxyResponseError(
                502,
                openai_error(
                    error_code,
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
        can_transient_failover: bool = False,
    ) -> tuple[Account, UpstreamWebSocket] | None:
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
            elif can_transient_failover and is_transient_refresh_contention(refresh_exc):
                # Transient CROSS-REPLICA refresh contention on the post-401 forced
                # refresh: benign claim contention (the account's refresh claim is
                # held by another replica) OR a post-exchange persist/status CAS
                # conflict. This is NOT a genuine ``transport_error`` OAuth failure,
                # so fail over to a healthy account WITHOUT an account-health penalty
                # instead of surfacing a bogus 401 invalid_api_key.
                _log_websocket_persist_conflict("post-401 forced-refresh", refresh_exc, account.id)
                raise _WebSocketTransientRefreshFailover(account.id) from refresh_exc
            elif is_transient_refresh_contention(refresh_exc):
                # PINNED refresh contention on the post-401 forced refresh:
                # mirror the pre-open freshness branch. The request is hard-pinned
                # (can_transient_failover is False), so it must not cross accounts,
                # but the owner's credentials are healthy (its refresh claim is
                # merely held by a peer replica). Stay on the owner (no crossing,
                # no permanent mark), release the acquired stream lease (the caller
                # releases it when this returns None), and surface a RETRYABLE
                # upstream_unavailable instead of a terminal 401 invalid_api_key.
                # (Also covers a pinned post-exchange persist/status CAS conflict,
                # logged distinctly.)
                _log_websocket_persist_conflict("pinned post-401 forced-refresh", refresh_exc, account.id)
                message = refresh_exc.message or _WEBSOCKET_PINNED_REFRESH_UNAVAILABLE_MESSAGE
                await proxy._emit_websocket_connect_failure(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                    status_code=503,
                    payload=openai_error(
                        "upstream_unavailable",
                        message,
                        error_type="server_error",
                    ),
                    error_code="upstream_unavailable",
                    error_message=message,
                )
                return None
            elif refresh_exc.transport_error:
                # A GENUINE OAuth transport failure (``code == "transport_error"``):
                # the account/route is at fault, so treat it exactly like the
                # aiohttp/connect transport handler below — raise a retryable 502
                # ``upstream_unavailable`` so the connect loop applies its normal
                # transport-failure failover/health handling — rather than a
                # misleading terminal 401 invalid_api_key or the unpenalized
                # claim-contention failover.
                message = refresh_exc.message or str(refresh_exc) or "Request to upstream timed out"
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "upstream_unavailable",
                        message,
                        error_type="server_error",
                    ),
                ) from refresh_exc
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
        require_preferred_account: bool = False,
    ) -> str:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        confirmed_pre_dispatch = is_confirmed_pre_dispatch_transport_error(exc)
        if confirmed_pre_dispatch:
            # A proven pre-dispatch proxy connect failure is account-local
            # transient evidence. The caller applies the bounded transient
            # backoff floor once the failed lease is released, so the generic
            # single-error health write is skipped here. Hard account
            # ownership fails closed on the original sanitized failure.
            failure_class = "retryable_transient"
        else:
            classified = await proxy._handle_websocket_connect_error(account, exc)
            failure_class = classified["failure_class"] if isinstance(classified, dict) else "non_retryable"
        candidates_remaining = max_attempts - attempt
        if confirmed_pre_dispatch:
            action = "surface" if require_preferred_account or candidates_remaining <= 0 else "failover_next"
        elif exc.status_code == 401 and candidates_remaining > 0:
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
    ) -> UpstreamWebSocket:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        recovery = ProcessNetworkRecovery(
            transport="websocket",
            request_id=None if request_state is None else request_state.request_log_id or request_state.request_id,
            account_id=account.id,
        )
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                _raise_proxy_budget_exhausted()
            try:
                with anyio.fail_after(remaining_seconds):
                    upstream = await proxy._open_upstream_websocket(account, headers, request_state=request_state)
                recovery.log_recovered()
                return upstream
            except ProxyResponseError as exc:
                decision = await _wait_for_process_network_recovery(
                    recovery,
                    exc,
                    deadline=deadline,
                )
                if decision == "retry":
                    continue
                if decision == "exhausted":
                    _raise_proxy_budget_exhausted()
                raise
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
    ) -> UpstreamWebSocket:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        access_token = proxy._encryptor.decrypt(account.access_token_encrypted)
        headers = apply_codex_installation_headers(headers, getattr(account, "codex_installation_id", None))
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
        _forget_websocket_stale_previous_response(
            previous_response_id=response_id,
            api_key_id=api_key_id,
        )
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
        request_state: _WebSocketRequestState | None = None,
        force_request_log_lookup: bool = False,
    ) -> str | None:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy

        def _record_lookup_metadata(
            *,
            source: str,
            outcome: str,
            requested_at: datetime | None = None,
            owner_session_id: str | None = None,
        ) -> None:
            if request_state is None:
                return
            request_state.previous_response_owner_lookup_source = source
            request_state.previous_response_owner_lookup_outcome = outcome
            request_state.previous_response_owner_requested_at = requested_at
            request_state.previous_response_owner_session_id = owner_session_id

        def _raise_stale_response_cache_suppression(*, outcome: str) -> NoReturn:
            _record_lookup_metadata(source="stale_response_cache", outcome=outcome)
            _record_continuity_owner_resolution(
                surface=surface,
                source="stale_response_cache",
                outcome=outcome,
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            raise ProxyResponseError(
                502,
                openai_error(
                    "stream_incomplete",
                    "Previous response is temporarily unavailable; retrying is suppressed for the recovery window.",
                    error_type="server_error",
                ),
            )

        if previous_response_id is None:
            return None
        response_id = previous_response_id.strip()
        if not response_id:
            return None
        api_key_id = api_key.id if api_key is not None else None
        session_id_value = _facade()._normalize_session_id(session_id)
        stale_cache_hit = (
            request_state is not None
            and not force_request_log_lookup
            and not request_state.fresh_upstream_request_is_retry_safe
            and _is_websocket_stale_previous_response(
                previous_response_id=response_id,
                api_key_id=api_key_id,
            )
        )
        cache_key = (response_id, api_key_id, session_id_value)
        cached_account_id = (
            None if force_request_log_lookup else proxy._websocket_previous_response_account_index.get(cache_key)
        )
        if cached_account_id is not None:
            _record_lookup_metadata(source="request_cache", outcome="hit")
            _record_continuity_owner_resolution(
                surface=surface,
                source="request_cache",
                outcome="hit",
                previous_response_id=response_id,
                session_id=session_id_value,
            )
            return cached_account_id
        fallback_account_id = (
            None
            if force_request_log_lookup
            else (
                proxy._websocket_previous_response_account_index.get((response_id, api_key_id, None))
                if session_id_value is not None
                else None
            )
        )
        try:
            async with proxy._repo_factory() as repos:
                owner_record = await repos.request_logs.find_latest_owner_record_for_response_id(
                    response_id=response_id,
                    api_key_id=api_key_id,
                    session_id=session_id_value,
                )
        except Exception as exc:
            if stale_cache_hit:
                _raise_stale_response_cache_suppression(outcome="lookup_failed")
            if fallback_account_id is not None:
                _record_lookup_metadata(source="request_cache_fallback", outcome="hit")
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
            _record_lookup_metadata(source="request_logs", outcome="fail_closed")
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
        if owner_record is None:
            if stale_cache_hit:
                _raise_stale_response_cache_suppression(outcome="hit")
            if force_request_log_lookup:
                proxy._websocket_previous_response_account_index.pop(cache_key, None)
                if session_id_value is not None:
                    proxy._websocket_previous_response_account_index.pop((response_id, api_key_id, None), None)
            if fallback_account_id is not None:
                _record_lookup_metadata(source="request_cache_fallback", outcome="hit")
                _record_continuity_owner_resolution(
                    surface=surface,
                    source="request_cache_fallback",
                    outcome="hit",
                    previous_response_id=response_id,
                    session_id=session_id_value,
                )
            else:
                _record_lookup_metadata(source="request_logs", outcome="miss")
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
            account_id=owner_record.account_id,
            session_id=session_id_value,
        )
        _record_lookup_metadata(
            source="request_logs",
            outcome="hit",
            requested_at=owner_record.requested_at,
            owner_session_id=owner_record.session_id,
        )
        _record_continuity_owner_resolution(
            surface=surface,
            source="request_logs",
            outcome="hit",
            previous_response_id=response_id,
            session_id=session_id_value,
        )
        return owner_record.account_id

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
        upstream: UpstreamWebSocket,
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
                        if message.kind not in {"text", "binary"}:
                            # A transport-end frame makes this socket
                            # ineligible for another turn immediately. Set the
                            # latch before the drain-owned transport child can
                            # wait on the pending-request lock.
                            upstream_control.reconnect_requested = True
                        if message.kind == "binary":
                            archive_request_id = await _websocket_archive_request_id_for_message(
                                message,
                                pending_requests=pending_requests,
                                pending_lock=pending_lock,
                            )
                            _archive_received_websocket_message(
                                upstream,
                                message,
                                archive_request_id=archive_request_id,
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
                    terminal_task = asyncio.create_task(
                        _process_and_forward_upstream_websocket_text(
                            proxy,
                            websocket,
                            upstream,
                            message=message,
                            text=message.text,
                            account=account,
                            account_id_value=account_id_value,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            client_send_lock=client_send_lock,
                            api_key=api_key,
                            upstream_control=upstream_control,
                            response_create_gate=response_create_gate,
                            downstream_activity=downstream_activity,
                            continuity_state=continuity_state,
                            codex_session_affinity=codex_session_affinity,
                        ),
                        name=f"proxy-websocket-terminal-{account_id_value}",
                    )
                    upstream_control.terminal_message_task = terminal_task
                    _track_websocket_owned_task(proxy, terminal_task)
                    try:
                        try:
                            should_stop_reader = await asyncio.shield(terminal_task)
                        except asyncio.CancelledError:
                            # The reader owns this message from receive through
                            # final downstream delivery. Cancellation of the
                            # reader must not orphan a terminal state after it
                            # has left pending_requests.
                            await _await_owned_websocket_task_after_reader_cancellation(
                                terminal_task,
                                failure_message="Websocket terminal task failed during reader cancellation",
                            )
                            raise
                    finally:
                        if upstream_control.terminal_message_task is terminal_task:
                            upstream_control.terminal_message_task = None
                    if should_stop_reader:
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
                terminal_task = asyncio.create_task(
                    _process_upstream_websocket_transport_end(
                        proxy,
                        websocket,
                        upstream,
                        message=message,
                        account=account,
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        client_send_lock=client_send_lock,
                        api_key=api_key,
                        upstream_control=upstream_control,
                        response_create_gate=response_create_gate,
                        downstream_activity=downstream_activity,
                    ),
                    name=f"proxy-websocket-transport-end-{account_id_value}",
                )
                upstream_control.terminal_message_task = terminal_task
                _track_websocket_owned_task(proxy, terminal_task)
                try:
                    try:
                        should_stop_reader = await asyncio.shield(terminal_task)
                    except asyncio.CancelledError:
                        # The child owns every sent request claimed from the
                        # shared queue. Reader cancellation must wait for that
                        # ownership transfer to finish within the shared
                        # shutdown deadline.
                        await _await_owned_websocket_task_after_reader_cancellation(
                            terminal_task,
                            failure_message="Websocket transport-end task failed during reader cancellation",
                        )
                        raise
                finally:
                    if upstream_control.terminal_message_task is terminal_task:
                        upstream_control.terminal_message_task = None
                if should_stop_reader:
                    break
        except asyncio.CancelledError:
            raise
        except _CapabilityLineagePropagationError as exc:
            error = _parse_openai_error(exc.error.payload)
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error and error.message else "Required capability lineage is unavailable"
            await proxy._fail_pending_websocket_requests(
                account=account,
                account_id_value=account_id_value,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code=error_code or "capability_lineage_unavailable",
                error_message=error_message,
                api_key=api_key,
                websocket=websocket,
                client_send_lock=client_send_lock,
                response_create_gate=response_create_gate,
                downstream_activity=downstream_activity,
                penalize_account=False,
            )
            upstream_control.reconnect_requested = True
            try:
                await upstream.close()
            except Exception:
                _facade().logger.debug(
                    "Failed to retire upstream websocket after capability lineage propagation failure",
                    exc_info=True,
                )
        except _WebSocketReplaySequenceRegression as exc:
            _facade().logger.warning(
                "Refusing websocket replay after non-advancing sequence account_id=%s detail=%s",
                account_id_value,
                exc,
            )
            await proxy._fail_pending_websocket_requests(
                account=account,
                account_id_value=account_id_value,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="Replayed upstream websocket sequence did not advance",
                api_key=api_key,
                response_create_gate=response_create_gate,
                suppress_sequenced_downstream_errors=True,
            )
            await _close_downstream_after_sequenced_replay_refusal(
                websocket,
                downstream_activity,
            )
            try:
                await upstream.close()
            except Exception:
                _facade().logger.debug(
                    "Failed to close upstream websocket after replay sequence refusal",
                    exc_info=True,
                )
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
        parsed_frame: _ParsedUpstreamWebSocketFrame | None = None,
    ) -> str:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if parsed_frame is None:
            parsed_frame = _parse_upstream_websocket_text_frame(text)
        payload = parsed_frame.payload
        event_type = parsed_frame.event_type
        event = parsed_frame.event
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
        # The returned event block is unused here; the rewrite helper rebuilds
        # its own canonical block on the (rare) changed path, so avoid the
        # per-frame ``format_sse_event`` re-encode and pass the raw framing.
        text, payload, event, event_type, _event_block = rewrite_parallel_tool_call_text(
            text,
            payload,
            event_block=f"data: {text}\n\n",
            event=event,
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
                replay_created_will_be_suppressed = (
                    event_type == "response.created" and request_state.suppress_next_created_downstream
                )
                sequence_number = payload.get("sequence_number") if payload is not None else None
                if (
                    request_state.replay_downstream_response_id is not None
                    and request_state.last_downstream_sequence_number is not None
                    and isinstance(sequence_number, int)
                    and not isinstance(sequence_number, bool)
                    and sequence_number <= request_state.last_downstream_sequence_number
                    and not replay_created_will_be_suppressed
                ):
                    raise _WebSocketReplaySequenceRegression(
                        f"request_id={request_state.request_log_id or request_state.request_id} "
                        f"watermark={request_state.last_downstream_sequence_number} replay={sequence_number}"
                    )
                if event_type not in {"response.completed", "response.failed", "response.incomplete", "error"}:
                    _record_response_event(request_state, event_type)
                elapsed_ms = int((time.monotonic() - request_state.started_at) * 1000)
                if request_state.latency_first_upstream_event_ms is None:
                    request_state.latency_first_upstream_event_ms = elapsed_ms
                if event_type == "response.created" and request_state.latency_response_created_ms is None:
                    request_state.latency_response_created_ms = elapsed_ms
                if request_state.latency_first_token_ms is None:
                    ttft_visible_at = _facade()._ttft_event_visible_at(
                        event_type, payload, request_state.ttft_reasoning_deltas
                    )
                    if ttft_visible_at is not None:
                        request_state.latency_first_token_ms = max(
                            0, int((ttft_visible_at - request_state.started_at) * 1000)
                        )
                actual_service_tier = _facade()._service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    request_state.actual_service_tier = actual_service_tier
                    request_state.service_tier = actual_service_tier
                completed_tool_call = _facade()._response_output_item_done_tool_call(payload)
                if completed_tool_call is not None:
                    completed_call_id, completed_call_type = completed_tool_call
                    if completed_call_id not in request_state.pending_function_call_ids:
                        request_state.pending_function_call_ids.append(completed_call_id)
                    request_state.pending_tool_call_types[completed_call_id] = completed_call_type
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
                    rewritten_payload = _rewrite_websocket_downstream_response_id(payload, request_state)
                    if rewritten_payload is not payload:
                        payload = rewritten_payload
                        text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                    sequence_number = payload.get("sequence_number")
                    if isinstance(sequence_number, int) and not isinstance(sequence_number, bool):
                        upstream_control.downstream_sequence_request_state = request_state
                        upstream_control.downstream_sequence_number = sequence_number
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

        if (
            event_type == "response.created"
            and response_id is not None
            and created_request_state is not None
            and created_request_state.durable_capability_lineage_required
        ):
            capability_api_key = created_request_state.api_key
            try:
                if capability_api_key is None:
                    raise _capability_lineage_unavailable_error()
                await proxy._capability_router.route(
                    RoutingIntent.requiring(RoutingCapability.TRUSTED_CYBER),
                    api_key_id=capability_api_key.id,
                    aliases=capability_lineage_aliases(
                        {},
                        previous_response_ids=_websocket_continuity_response_ids(
                            created_request_state,
                            response_id,
                        ),
                    ),
                )
            except ProxyResponseError as exc:
                async with pending_lock:
                    created_request_state.response_id = None
                raise _CapabilityLineagePropagationError(exc) from exc

        if event_type == "response.created" and created_request_state is not None and continuity_state is not None:
            _record_websocket_responses_lite_acceptance(
                continuity_state,
                request_state=created_request_state,
            )

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            await _release_websocket_response_create_gate(created_request_state, response_create_gate)

        if request_state is not None:
            await proxy._touch_active_websocket_thread_affinity(request_state, account)

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
                if grouped_error_reason == "previous_response_not_found":
                    _record_websocket_stale_anchor_failure(
                        grouped_request_state,
                        surface="websocket_stream",
                        upstream_error_code="previous_response_not_found",
                    )
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
                    isolate_account_health_failure=True,
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
        retry_safe_previous_response_not_found = (
            retry_is_previous_response_not_found
            and request_state.fresh_upstream_request_is_retry_safe
            and request_state.fresh_upstream_request_text
            and retry_error_code is not None
        )
        if retry_safe_previous_response_not_found:
            downstream_text = text
        else:
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
        retry_safe_owner_replay = bool(
            retry_error_code in _facade()._WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES
            and request_state.previous_response_id is not None
            and request_state.preferred_account_id is not None
            and request_state.proxy_injected_previous_response_id
            and request_state.fresh_upstream_request_is_retry_safe
            and request_state.fresh_upstream_request_text
        )
        if (
            retry_error_code in _facade()._WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES
            and request_state.previous_response_id is not None
            and request_state.preferred_account_id is not None
            and not retry_safe_previous_response_not_found
            and not retry_safe_owner_replay
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
        if retry_safe_owner_replay and not retry_safe_previous_response_not_found:
            safe_request_text = _prepare_websocket_request_state_for_account_switch(request_state)
            if safe_request_text is None:
                await proxy._handle_stream_error(
                    account,
                    {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                    retry_error_code,
                )
                event, payload, event_type, downstream_text = (
                    _rewrite_websocket_previous_response_owner_unavailable_event(
                        request_state=request_state,
                    )
                )
                retry_error_code = None
            else:
                # Keep the global response-create gate/admission while dropping
                # the old owner's per-account create lease. The replay may move
                # to a replacement account, and the reconnect/send path
                # re-acquires the account-local slot only when this field is
                # clear.
                await proxy._release_request_state_account_response_create_lease(request_state)
                request_state.excluded_account_ids.add(account.id)
                request_state.affinity_policy = replace(
                    request_state.affinity_policy,
                    reallocate_sticky=True,
                )
                request_state.request_text = safe_request_text
        if retry_error_code == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE:
            retry_text = None
            if not request_state.file_required_preferred_account:
                retry_text = _prepare_websocket_request_state_for_account_switch(request_state)
            if retry_text is not None:
                request_state.precreated_replay_reason = _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
                request_state.precreated_replay_account_id = account.id
                request_state.error_code_override = (
                    _normalize_error_code(
                        _websocket_event_error_code(event_type, payload),
                        _websocket_event_error_type(event_type, payload),
                    )
                    or "invalid_request_error"
                )
                request_state.error_message_override = (
                    _websocket_event_error_message(event_type, payload) or "Upstream rejected the requested model"
                )
                request_state.error_type_override = (
                    _websocket_event_error_type(event_type, payload) or "invalid_request_error"
                )
                request_state.error_param_override = _websocket_event_error_param(event_type, payload)
                request_state.error_http_status_override = _facade()._http_error_status_from_payload(payload) or 400
                await proxy._release_request_state_account_response_create_lease(request_state)
                request_state.excluded_account_ids.add(account.id)
                request_state.affinity_policy = replace(
                    request_state.affinity_policy,
                    reallocate_sticky=True,
                )
                request_state.request_text = retry_text
                request_state.replay_count += 1
                request_state.awaiting_response_created = True
                request_state.response_id = None
                request_state.response_event_count = 0
                upstream_control.reconnect_requested = True
                upstream_control.suppress_downstream_event = True
                upstream_control.replay_request_state = request_state
                _facade().logger.info(
                    "Retrying pre-created request after account/model rejection request_id=%s account_id=%s model=%s",
                    request_state.request_log_id or request_state.request_id,
                    account.id,
                    request_state.model,
                )
                return downstream_text
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
                    replay_text = _install_verified_fresh_replay(
                        request_state,
                        require_proxy_injected_previous_response_id=False,
                        require_account_neutral=False,
                    )
                    if replay_text is None:
                        retry_error_code = None
                    else:
                        upstream_control.reconnect_requested = True
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

        completed_usage = (
            event.response.usage if event_type == "response.completed" and event and event.response else None
        )
        _refine_websocket_request_kind_from_completion(
            request_state,
            event_type=event_type,
            output_tokens=completed_usage.output_tokens if completed_usage is not None else None,
        )
        completed_empty_prewarm = (
            event_type == "response.completed"
            and request_state.request_kind == "prewarm"
            and completed_usage is not None
            and completed_usage.output_tokens == 0
        )
        if event_type == "response.completed" and continuity_state is not None and not completed_empty_prewarm:
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
                    and request_state.last_downstream_sequence_number is None
                    and request_state.response_id is None
                    and request_state.replay_count < 1
                    and bool(request_state.request_text)
                    and request_state.preferred_account_id != account.id
                    and not request_state.file_required_preferred_account
                    and (
                        request_state.previous_response_id is None
                        or (
                            request_state.proxy_injected_previous_response_id
                            and request_state.fresh_upstream_request_text is not None
                            and request_state.fresh_upstream_request_is_retry_safe
                        )
                    )
                )
                if can_retry_security_work:
                    retry_text = request_state.request_text
                    if request_state.previous_response_id is not None:
                        retry_text = _prepare_websocket_request_state_for_account_switch(request_state)
                    if retry_text:
                        request_state.replay_count += 1
                        request_state.response_id = None
                        request_state.awaiting_response_created = True
                        request_state.require_security_work_authorized = True
                        request_state.error_code_override = _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE
                        request_state.error_message_override = terminal_error_message
                        request_state.error_type_override = error.type if error else None
                        request_state.error_param_override = error.param if error else None
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
            isolate_account_health_failure=True,
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
        bound_to_current_account = request_state.replay_required_account_id == account.id
        requires_reauth = _websocket_auth_failure_requires_reauth(error_message)
        if bound_to_current_account and (
            requires_reauth or request_state.auth_replay_counts_by_account.get(account.id, 0) > 0
        ):
            failure_code = (
                _facade()._WEBSOCKET_SESSION_EXPIRED_FAILURE_CODE
                if requires_reauth
                else _facade()._WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE
            )
            await proxy._load_balancer.mark_permanent_failure(account, failure_code)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(account.id)
            return False
        if (
            _prepare_websocket_request_state_for_auth_replay(
                request_state,
                current_account_id=account.id,
            )
            is None
        ):
            return False

        if requires_reauth:
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
        upstream_control: _WebSocketUpstreamControl | None = None,
        downstream_activity: _DownstreamWebSocketActivity,
        idle_timeout_seconds: float,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        if upstream_control is not None:
            terminal_task = upstream_control.terminal_message_task
            if terminal_task is not None and not terminal_task.done():
                return False
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
        isolate_account_health_failure: bool = False,
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
            await proxy._release_websocket_request_state_reservation(request_state)
            # The reservation is settled; clear any terminal-bookkeeping
            # settlement claim so abort handling does not settle it again.
            request_state.terminal_settlement_phase = None
            return

        if request_state.latency_first_token_ms is None:
            ttft_visible_at = _finalize_ttft_reasoning_deltas(request_state.ttft_reasoning_deltas)
            if ttft_visible_at is not None:
                request_state.latency_first_token_ms = max(0, int((ttft_visible_at - request_state.started_at) * 1000))

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
            incomplete_reason = _websocket_event_incomplete_reason(event_type, payload)
            if incomplete_reason is not None:
                error_code = incomplete_reason
                error_message = incomplete_reason
            if event_type == "response.failed":
                error_payload = _upstream_error_from_openai(error)
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id
        elif event_type == "response.completed":
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id

        _refine_websocket_request_kind_from_completion(
            request_state,
            event_type=event_type,
            output_tokens=usage.output_tokens if usage is not None else None,
        )

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
        completed_empty_prewarm = (
            event_type == "response.completed"
            and request_state.request_kind == "prewarm"
            and usage is not None
            and usage.output_tokens == 0
        )
        if event_type in {"response.failed", "response.incomplete", "error"}:
            settlement.record_success = False
        if completed_empty_prewarm:
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
        if settlement.account_health_error:
            # Connection safety must not wait on settlement or health
            # persistence. The health write remains ordered below.
            upstream_control.reconnect_requested = True
            upstream_control.retire_after_drain = True
        lifecycle = request_state.deferred_account_backoff_lifecycle
        settlement_committed = await proxy._settle_stream_api_key_usage(
            api_key,
            request_state.api_key_reservation,
            settlement,
            response_id,
            # The reservation must be settled before the load-balancer
            # health write below (settlement-ordering invariant).
            wait_for_settlement=(
                lifecycle is not None
                or settlement.account_health_error
                or settlement.record_success
                or bool(request_state.deferred_account_error_backoffs)
            ),
        )
        # Settlement responsibility has transferred (the settle path tracks
        # its own failure/cancellation fallback release). Clear any terminal-
        # bookkeeping settlement claim so a later abort of the surrounding
        # continuation does not race a duplicate release against it.
        request_state.terminal_settlement_phase = None
        if settlement_committed:
            request_state.api_key_reservation = None
            if lifecycle is not None:
                lifecycle.settlement_confirmed = True
            pending_backoffs = (
                lifecycle.pending_backoffs if lifecycle is not None else request_state.deferred_account_error_backoffs
            )
            if pending_backoffs:
                await proxy._drain_deferred_account_error_backoffs(pending_backoffs)
        latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
        )
        request_log_handoff_succeeded = True
        if not request_state.skip_request_log:
            request_log_response_id = (
                _websocket_downstream_response_id(request_state) if settlement.record_success else response_id
            )
            try:
                await proxy._write_request_log(
                    account_id=account_id_value,
                    api_key=api_key,
                    request_id=request_log_response_id,
                    archive_request_id=request_state.archive_request_id,
                    model=request_state.model or "",
                    latency_ms=latency_ms,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    failure_phase=request_state.failure_phase_override,
                    failure_detail=request_state.failure_detail_override,
                    upstream_error_code=request_state.upstream_error_code_override,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    cached_input_tokens=cached_input_tokens,
                    reasoning_tokens=reasoning_tokens,
                    reasoning_effort=request_state.reasoning_effort,
                    transport=request_state.transport,
                    upstream_transport=request_state.upstream_transport,
                    service_tier=response_service_tier,
                    requested_service_tier=request_state.requested_service_tier,
                    actual_service_tier=request_state.actual_service_tier,
                    latency_first_token_ms=request_state.latency_first_token_ms,
                    latency_response_created_ms=request_state.latency_response_created_ms,
                    latency_first_upstream_event_ms=request_state.latency_first_upstream_event_ms,
                    latency_response_create_gate_wait_ms=request_state.latency_response_create_gate_wait_ms,
                    latency_bridge_queue_wait_ms=request_state.latency_bridge_queue_wait_ms,
                    prewarm_status=request_state.prewarm_status,
                    prewarm_latency_ms=request_state.prewarm_latency_ms,
                    session_previous_gap_ms=request_state.session_previous_gap_ms,
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
                    conversation_id=request_state.conversation_id,
                    client_ip=request_state.client_ip,
                    request_kind=request_state.request_kind,
                    connection_request_kind=request_state.connection_request_kind,
                )
            except Exception:
                request_log_handoff_succeeded = False
                _facade().logger.warning(
                    "Failed to hand off websocket terminal request log request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )
            else:
                _record_upstream_transport_decision(
                    downstream_transport=request_state.transport,
                    upstream_transport=request_state.upstream_transport,
                    policy=(
                        "bridge"
                        if request_state.transport == _REQUEST_TRANSPORT_HTTP
                        and request_state.upstream_transport == _REQUEST_TRANSPORT_WEBSOCKET
                        else "direct"
                    ),
                    sticky=request_state.affinity_policy.key is not None
                    or request_state.previous_response_id is not None,
                    status=status,
                )

        if settlement.account_health_error:
            upstream_control.reconnect_requested = True
            upstream_control.retire_after_drain = True
            if not settlement_committed:
                _facade().logger.warning(
                    "Skipped websocket account-health error write because API-key settlement did not commit "
                    "request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                )
            elif not request_log_handoff_succeeded:
                _facade().logger.warning(
                    "Skipped websocket account-health error write because terminal request-log handoff failed "
                    "request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                )
            else:
                try:
                    await proxy._handle_stream_error(
                        account,
                        _stream_settlement_error_payload(settlement),
                        settlement.error_code or "upstream_error",
                    )
                except Exception:
                    _facade().logger.warning(
                        "Failed to record websocket account-health error request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )
                    if not isolate_account_health_failure:
                        raise
        elif settlement.record_success:
            if not settlement_committed:
                _facade().logger.warning(
                    "Skipped websocket account-health success write because API-key settlement did not commit "
                    "request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                )
            elif not request_log_handoff_succeeded:
                _facade().logger.warning(
                    "Skipped websocket account-health success write because terminal request-log handoff failed "
                    "request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                )
            else:
                try:
                    await proxy._load_balancer.record_success(account)
                except Exception:
                    _facade().logger.warning(
                        "Failed to record websocket account-health success request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )
                    if not isolate_account_health_failure:
                        raise
            for remembered_response_id in _websocket_continuity_response_ids(request_state, response_id):
                proxy._remember_websocket_previous_response_owner(
                    previous_response_id=remembered_response_id,
                    api_key_id=api_key.id if api_key is not None else None,
                    account_id=account_id_value,
                    session_id=request_state.session_id,
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
            archive_request_id=request_state.archive_request_id,
            model=request_state.model or "",
            latency_ms=int((time.monotonic() - request_state.started_at) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            failure_phase=request_state.failure_phase_override,
            failure_detail=request_state.failure_detail_override,
            upstream_error_code=request_state.upstream_error_code_override,
            reasoning_effort=request_state.reasoning_effort,
            transport=request_state.transport,
            upstream_transport=request_state.upstream_transport,
            service_tier=request_state.service_tier,
            requested_service_tier=request_state.requested_service_tier,
            actual_service_tier=request_state.actual_service_tier,
            latency_first_token_ms=request_state.latency_first_token_ms,
            latency_response_created_ms=request_state.latency_response_created_ms,
            latency_first_upstream_event_ms=request_state.latency_first_upstream_event_ms,
            latency_response_create_gate_wait_ms=request_state.latency_response_create_gate_wait_ms,
            latency_bridge_queue_wait_ms=request_state.latency_bridge_queue_wait_ms,
            prewarm_status=request_state.prewarm_status,
            prewarm_latency_ms=request_state.prewarm_latency_ms,
            session_previous_gap_ms=request_state.session_previous_gap_ms,
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
            conversation_id=request_state.conversation_id,
            client_ip=request_state.client_ip,
            request_kind=request_state.request_kind,
            connection_request_kind=request_state.connection_request_kind,
        )
        _record_upstream_transport_decision(
            downstream_transport=request_state.transport,
            upstream_transport=request_state.upstream_transport,
            policy=(
                "bridge"
                if request_state.transport == _REQUEST_TRANSPORT_HTTP
                and request_state.upstream_transport == _REQUEST_TRANSPORT_WEBSOCKET
                else "direct"
            ),
            sticky=request_state.affinity_policy.key is not None or request_state.previous_response_id is not None,
            status="error",
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
        replay_fallback = _websocket_precreated_replay_fallback_error(request_state)
        if replay_fallback is not None:
            status_code, payload, error_code, error_message, rejected_account_id = replay_fallback
            account_id = rejected_account_id
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
                _serialize_websocket_error_event(
                    _wrapped_websocket_error_event(
                        status_code,
                        payload,
                        expose_stale_previous_response_classifier=(
                            request_state.expose_stale_previous_response_classifier
                        ),
                    )
                )
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
        suppress_sequenced_downstream_errors: bool = False,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy
        finalization_task: asyncio.Task[bool] | None = None
        await pending_lock.acquire()
        try:
            remaining = list(pending_requests)
            pending_requests.clear()
            if remaining:
                finalization_task = asyncio.create_task(
                    self._finalize_claimed_websocket_requests(
                        account=account,
                        account_id_value=account_id_value,
                        remaining=remaining,
                        error_code=error_code,
                        error_message=error_message,
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                        downstream_activity=downstream_activity,
                        status=status,
                        penalize_account=penalize_account,
                        suppress_sequenced_downstream_errors=suppress_sequenced_downstream_errors,
                    ),
                    name="proxy-websocket-finalization-pending-requests",
                )
                _track_websocket_owned_task(proxy, finalization_task)
        finally:
            # anyio.Lock.release() is synchronous: once the shared deque is
            # emptied, the registered child is published before another
            # cancellation point can strand the claimed request states.
            pending_lock.release()

        if finalization_task is None:
            return True

        try:
            settlement_succeeded = await asyncio.shield(finalization_task)
        except asyncio.CancelledError:
            remaining_timeout = shutdown_state.remaining_drain_timeout_seconds()
            timeout_seconds = (
                _facade()._TASK_CANCEL_TIMEOUT_SECONDS
                if remaining_timeout is None
                else max(float(remaining_timeout), 0.0)
            )
            if not finalization_task.done() and timeout_seconds > 0:
                # Do not cancel the child at the bound: it is the sole owner of
                # the claimed states and remains visible to lifespan draining.
                await asyncio.wait({finalization_task}, timeout=timeout_seconds)
            raise
        return settlement_succeeded

    async def _finalize_claimed_websocket_requests(
        self,
        *,
        account: Account | None,
        account_id_value: str | None,
        remaining: list[_WebSocketRequestState],
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None,
        client_send_lock: anyio.Lock | None,
        response_create_gate: asyncio.Semaphore | None,
        downstream_activity: _DownstreamWebSocketActivity | None,
        status: str,
        penalize_account: bool,
        suppress_sequenced_downstream_errors: bool,
    ) -> bool:
        proxy = cast(_WebSocketServiceProtocol, self)
        _ = proxy

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

        reservation_release_succeeded = True
        for request_state in remaining:
            try:
                proxy._cancel_request_state_api_key_reservation_heartbeat(request_state)
            except Exception:
                _facade().logger.warning(
                    "Failed to stop websocket reservation heartbeat during terminal cleanup request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )
            try:
                await proxy._release_websocket_request_state_reservation(request_state)
            except Exception:
                reservation_release_succeeded = False
                _facade().logger.warning(
                    "Failed to release websocket reservation during terminal cleanup request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )

        request_log_handoff_succeeded = True
        last_index = len(remaining) - 1
        for index, request_state in enumerate(remaining):
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
                try:
                    _facade()._maybe_dump_oversized_response_create_request(
                        request_state,
                        account_id_value=account_id_value,
                        error_code=request_error_code,
                        error_message=request_error_message,
                    )
                except Exception:
                    _facade().logger.warning(
                        "Failed to dump oversized websocket request during terminal cleanup request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )

            if response_create_gate is not None:
                await _release_websocket_response_create_ownership_for_cleanup(
                    request_state,
                    response_create_gate,
                )
            if request_state.websocket_stream_lease is not None:
                websocket_stream_lease = request_state.websocket_stream_lease
                request_state.websocket_stream_lease = None
                try:
                    await proxy._load_balancer.release_account_lease(websocket_stream_lease)
                except Exception:
                    _facade().logger.warning(
                        "Failed to release websocket stream lease during terminal cleanup request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )
            if request_state.event_queue is not None:
                try:
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
                except Exception:
                    _facade().logger.warning(
                        "Failed to publish websocket terminal queue event during cleanup request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )
            if (
                websocket is not None
                and client_send_lock is not None
                and not (
                    suppress_sequenced_downstream_errors and request_state.last_downstream_sequence_number is not None
                )
            ):
                try:
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
                except Exception:
                    if downstream_activity is not None:
                        downstream_activity.mark_disconnected()
                    _facade().logger.warning(
                        "Failed to emit websocket terminal event during cleanup request_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        exc_info=True,
                    )
            if account_id_value is None or request_state.skip_request_log:
                continue
            latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
            if request_state.latency_first_token_ms is None:
                ttft_visible_at = _finalize_ttft_reasoning_deltas(request_state.ttft_reasoning_deltas)
                if ttft_visible_at is not None:
                    request_state.latency_first_token_ms = max(
                        0, int((ttft_visible_at - request_state.started_at) * 1000)
                    )
            try:
                await proxy._write_request_log(
                    account_id=account_id_value,
                    # HTTP-bridge callers fan a shared session failure out to
                    # requests from multiple API keys, so they pass api_key=None;
                    # each request_state carries its own authenticated key.
                    api_key=request_state.api_key or api_key,
                    request_id=request_state.response_id or request_state.request_log_id or request_state.request_id,
                    archive_request_id=request_state.archive_request_id,
                    model=request_state.model or "",
                    latency_ms=latency_ms,
                    status=status,
                    error_code=request_error_code,
                    error_message=request_error_message,
                    failure_phase=request_state.failure_phase_override,
                    failure_detail=request_state.failure_detail_override,
                    upstream_error_code=request_state.upstream_error_code_override,
                    reasoning_effort=request_state.reasoning_effort,
                    transport=request_state.transport,
                    upstream_transport=request_state.upstream_transport,
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
                    conversation_id=request_state.conversation_id,
                    client_ip=request_state.client_ip,
                    request_kind=request_state.request_kind,
                    connection_request_kind=request_state.connection_request_kind,
                )
            except Exception:
                request_log_handoff_succeeded = False
                _facade().logger.warning(
                    "Failed to persist websocket terminal request log during cleanup request_id=%s",
                    request_state.request_log_id or request_state.request_id,
                    exc_info=True,
                )
            _record_upstream_transport_decision(
                downstream_transport=request_state.transport,
                upstream_transport=request_state.upstream_transport,
                policy=(
                    "bridge"
                    if request_state.transport == _REQUEST_TRANSPORT_HTTP
                    and request_state.upstream_transport == _REQUEST_TRANSPORT_WEBSOCKET
                    else "direct"
                ),
                sticky=request_state.affinity_policy.key is not None or request_state.previous_response_id is not None,
                status=status,
            )

        if (
            remaining
            and penalize_account
            and account is not None
            and isinstance(account, Account)
            and penalty_code is not None
        ):
            if not reservation_release_succeeded:
                _facade().logger.warning(
                    "Skipped websocket pending-request health penalty because reservation release did not commit "
                    "account_id=%s error_code=%s",
                    account_id_value,
                    penalty_code,
                )
            elif not request_log_handoff_succeeded:
                _facade().logger.warning(
                    "Skipped websocket pending-request health penalty because terminal request-log handoff failed "
                    "account_id=%s error_code=%s",
                    account_id_value,
                    penalty_code,
                )
            else:
                try:
                    await proxy._handle_stream_error(
                        account,
                        {"message": penalty_message or error_message},
                        penalty_code,
                    )
                except Exception:
                    _facade().logger.warning(
                        "Failed to record websocket pending-request health penalty account_id=%s error_code=%s",
                        account_id_value,
                        penalty_code,
                        exc_info=True,
                    )

        return reservation_release_succeeded

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
            await _release_websocket_response_create_ownership_for_cleanup(
                request_state,
                response_create_gate,
            )
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
