from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Mapping, NoReturn, TypeVar, cast

import aiohttp
import anyio

from app.core.auth.refresh import (
    RefreshError,
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
)
from app.core.balancer import (
    PERMANENT_FAILURE_CODES,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
    ResetPreferenceWindow,
    RoutingStrategy,
    TrafficClass,
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
from app.core.clients.proxy import stream_responses as core_stream_responses  # noqa: F401
from app.core.clients.proxy import thread_goal_request as core_thread_goal_request
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio  # noqa: F401
from app.core.clients.proxy_websocket import (
    UpstreamResponsesWebSocket as UpstreamResponsesWebSocket,
)
from app.core.clients.proxy_websocket import (
    connect_responses_websocket as connect_responses_websocket,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_CODE as PREVIOUS_RESPONSE_STALE_CODE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_STALE_MESSAGE as PREVIOUS_RESPONSE_STALE_MESSAGE,
)
from app.core.errors import (
    OpenAIErrorEnvelope,
    ResponseFailedEvent,
    is_previous_response_not_found_error,
    is_previous_response_not_found_message,
    openai_error,
    previous_response_id_from_not_found_message,
    previous_response_stream_incomplete_error,
    response_failed_event,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_same_account_takeover_total,
)
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
)
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.upstream_proxy.resolver import (
    resolve_upstream_route as resolve_upstream_route,
)
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME as CODEX_KEEPALIVE_FRAME  # noqa: F401
from app.core.utils.sse import format_sse_event, parse_sse_data_json  # noqa: F401
from app.core.utils.time import utcnow as utcnow
from app.db.models import (
    Account,
    AccountStatus,  # noqa: F401
    DashboardSettings,
    StickySessionKind,
)
from app.db.session import SessionLocal as SessionLocal
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,  # noqa: F401
)
from app.modules.api_keys.service import (
    ApiKeysService as ApiKeysService,
)
from app.modules.proxy._service.api_key_usage import (
    _API_KEY_RESERVATION_HEARTBEAT_SECONDS as _API_KEY_RESERVATION_HEARTBEAT_SECONDS,
)
from app.modules.proxy._service.api_key_usage import (
    _ApiKeyUsageMixin,
)
from app.modules.proxy._service.codex_control import (
    _CodexControlMixin,
)
from app.modules.proxy._service.compact import (
    _CompactMixin,
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
from app.modules.proxy._service.file_ops import (
    _FileOpsMixin,
)
from app.modules.proxy._service.http_bridge import (
    _HTTPBridgeMixin,
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
from app.modules.proxy._service.observability import (
    continuity_fail_closed_total as continuity_fail_closed_total,
)
from app.modules.proxy._service.observability import (
    continuity_owner_resolution_total as continuity_owner_resolution_total,
)
from app.modules.proxy._service.rate_limit import (
    _RateLimitMixin,
)
from app.modules.proxy._service.request_log import (
    _RequestLogMixin,
)
from app.modules.proxy._service.response_create import (
    _OVERSIZED_RESPONSE_CREATE_DUMP_DIR as _OVERSIZED_RESPONSE_CREATE_DUMP_DIR,
)
from app.modules.proxy._service.response_create import (
    _OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS as _OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS,
)
from app.modules.proxy._service.response_create import (
    _RESPONSE_CREATE_HISTORY_OMISSION_NOTICE as _RESPONSE_CREATE_HISTORY_OMISSION_NOTICE,
)
from app.modules.proxy._service.response_create import (
    _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE as _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE,
)
from app.modules.proxy._service.response_create import (
    _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE as _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE,
)
from app.modules.proxy._service.response_create import (
    _UPSTREAM_RESPONSE_CREATE_MAX_BYTES as _UPSTREAM_RESPONSE_CREATE_MAX_BYTES,
)
from app.modules.proxy._service.response_create import (
    _UPSTREAM_RESPONSE_CREATE_WARN_BYTES as _UPSTREAM_RESPONSE_CREATE_WARN_BYTES,
)
from app.modules.proxy._service.response_create import (
    _count_external_image_urls as _count_external_image_urls,
)
from app.modules.proxy._service.response_create import (
    _enforce_response_create_size_limit as _enforce_response_create_size_limit,
)
from app.modules.proxy._service.response_create import (
    _fingerprint_input_items as _fingerprint_input_items,
)
from app.modules.proxy._service.response_create import (
    _function_call_output_call_ids as _function_call_output_call_ids,
)
from app.modules.proxy._service.response_create import (
    _inject_missing_interrupted_function_call_outputs as _inject_missing_interrupted_function_call_outputs,
)
from app.modules.proxy._service.response_create import (
    _inline_top_level_input_image_urls as _inline_top_level_input_image_urls,
)
from app.modules.proxy._service.response_create import (
    _input_part_is_image as _input_part_is_image,
)
from app.modules.proxy._service.response_create import (
    _is_inline_image_reference as _is_inline_image_reference,
)
from app.modules.proxy._service.response_create import (
    _json_size_bytes as _json_size_bytes,
)
from app.modules.proxy._service.response_create import (
    _json_value_contains_input_image_part as _json_value_contains_input_image_part,
)
from app.modules.proxy._service.response_create import (
    _maybe_dump_oversized_response_create_request as _maybe_dump_oversized_response_create_request,
)
from app.modules.proxy._service.response_create import (
    _missing_function_call_outputs_for_previous_response as _missing_function_call_outputs_for_previous_response,
)
from app.modules.proxy._service.response_create import (
    _oversized_response_create_dump_dir as _oversized_response_create_dump_dir,
)
from app.modules.proxy._service.response_create import (
    _response_create_client_metadata as _response_create_client_metadata,
)
from app.modules.proxy._service.response_create import (
    _response_create_history_omission_notice_item as _response_create_history_omission_notice_item,
)
from app.modules.proxy._service.response_create import (
    _response_create_inline_image_notice_item as _response_create_inline_image_notice_item,
)
from app.modules.proxy._service.response_create import (
    _response_create_inline_image_notice_part as _response_create_inline_image_notice_part,
)
from app.modules.proxy._service.response_create import (
    _response_create_recent_suffix_start as _response_create_recent_suffix_start,
)
from app.modules.proxy._service.response_create import (
    _response_create_text as _response_create_text,
)
from app.modules.proxy._service.response_create import (
    _response_create_text_with_account_installation_id as _response_create_text_with_account_installation_id,
)
from app.modules.proxy._service.response_create import (
    _response_create_text_with_size_guard as _response_create_text_with_size_guard,
)
from app.modules.proxy._service.response_create import (
    _response_create_too_large_error_envelope as _response_create_too_large_error_envelope,
)
from app.modules.proxy._service.response_create import (
    _response_output_item_done_function_call_id as _response_output_item_done_function_call_id,
)
from app.modules.proxy._service.response_create import (
    _responses_request_contains_input_image as _responses_request_contains_input_image,
)
from app.modules.proxy._service.response_create import (
    _responses_request_uses_image_generation as _responses_request_uses_image_generation,
)
from app.modules.proxy._service.response_create import (
    _safe_dump_slug as _safe_dump_slug,
)
from app.modules.proxy._service.response_create import (
    _should_dump_oversized_response_create as _should_dump_oversized_response_create,
)
from app.modules.proxy._service.response_create import (
    _should_slim_historical_tool_output as _should_slim_historical_tool_output,
)
from app.modules.proxy._service.response_create import (
    _slim_historical_response_content as _slim_historical_response_content,
)
from app.modules.proxy._service.response_create import (
    _slim_historical_response_content_part as _slim_historical_response_content_part,
)
from app.modules.proxy._service.response_create import (
    _slim_historical_response_input_item as _slim_historical_response_input_item,
)
from app.modules.proxy._service.response_create import (
    _slim_response_create_payload_for_upstream as _slim_response_create_payload_for_upstream,
)
from app.modules.proxy._service.response_create import (
    _summarize_response_create_input as _summarize_response_create_input,
)
from app.modules.proxy._service.response_create import (
    _summarize_response_create_payload as _summarize_response_create_payload,
)
from app.modules.proxy._service.response_create import (
    _synthetic_interrupted_function_call_output as _synthetic_interrupted_function_call_output,
)
from app.modules.proxy._service.response_create import (
    _write_response_create_dump as _write_response_create_dump,
)
from app.modules.proxy._service.streaming import (
    _StreamingMixin,
)
from app.modules.proxy._service.streaming.helpers import (
    _build_rewritten_stream_response_failed_event as _build_rewritten_stream_response_failed_event,
)
from app.modules.proxy._service.streaming.helpers import (
    _build_stream_incomplete_terminal_event_for_request as _build_stream_incomplete_terminal_event_for_request,
)
from app.modules.proxy._service.streaming.helpers import (
    _call_stream_with_supported_optional_kwargs as _call_stream_with_supported_optional_kwargs,
)
from app.modules.proxy._service.streaming.helpers import (
    _classify_upstream_close as _classify_upstream_close,
)
from app.modules.proxy._service.streaming.helpers import (
    _push_stream_attempt_timeout_overrides as _push_stream_attempt_timeout_overrides,
)
from app.modules.proxy._service.streaming.helpers import (
    _refresh_upstream_proxy_fail_closed_reason as _refresh_upstream_proxy_fail_closed_reason,
)
from app.modules.proxy._service.streaming.helpers import (
    _resolve_upstream_stream_transport as _resolve_upstream_stream_transport,
)
from app.modules.proxy._service.streaming.helpers import (
    _rewrite_previous_response_stream_error as _rewrite_previous_response_stream_error,
)
from app.modules.proxy._service.streaming.helpers import (
    _should_infer_upstream_status_from_proxy_error as _should_infer_upstream_status_from_proxy_error,
)
from app.modules.proxy._service.streaming.helpers import (
    _should_penalize_stream_error as _should_penalize_stream_error,
)
from app.modules.proxy._service.streaming.helpers import (
    _should_retry_stream_error as _should_retry_stream_error,
)
from app.modules.proxy._service.streaming.helpers import (
    _should_retry_transient_stream_error as _should_retry_transient_stream_error,
)
from app.modules.proxy._service.streaming.helpers import (
    _stream_request_budget_seconds as _stream_request_budget_seconds,
)
from app.modules.proxy._service.streaming.helpers import (
    _upstream_turn_state_from_socket as _upstream_turn_state_from_socket,
)
from app.modules.proxy._service.streaming.retry import (
    _http_downstream_request_is_sticky as _http_downstream_request_is_sticky,
)
from app.modules.proxy._service.streaming.retry import (
    _resolve_http_downstream_transport as _resolve_http_downstream_transport,
)
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_WEBSOCKET,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _ApiKeyReservationTouchState,  # noqa: F401
    _clear_websocket_request_error_overrides,  # noqa: F401
    _DownstreamWebSocketActivity,  # noqa: F401
    _event_type_from_payload,  # noqa: F401
    _FilePinEntry,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _PreparedWebSocketRequest,  # noqa: F401
    _record_response_event,  # noqa: F401
    _record_websocket_route_metadata,  # noqa: F401
    _request_log_useragent_fields,
    _RequestLogFailureMetadata,
    _RetryableStreamError,  # noqa: F401
    _stream_settlement_error_payload,  # noqa: F401
    _StreamSettlement,  # noqa: F401
    _TerminalStreamError,  # noqa: F401
    _TransientStreamError,  # noqa: F401
    _wait_for_websocket_continuity_gap,  # noqa: F401
    _websocket_full_replay_should_wait_for_continuity,  # noqa: F401
    _websocket_request_can_replay_before_visible_output,  # noqa: F401
    _WebSocketConnectFailureEmitted,  # noqa: F401
    _WebSocketContinuityAnchor,  # noqa: F401
    _WebSocketContinuityState,
    _WebSocketReceiveTimeout,  # noqa: F401
    _WebSocketRequestState,
    _WebSocketUpstreamControl,  # noqa: F401
)
from app.modules.proxy._service.support import (
    _call_with_supported_optional_kwargs as _support_call_with_supported_optional_kwargs,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeOwnerForward as _HTTPBridgeOwnerForward,
)
from app.modules.proxy._service.support import (
    _supported_optional_kwargs as _support_supported_optional_kwargs,
)
from app.modules.proxy._service.support import (
    _websocket_route_log_kwargs as _websocket_route_log_kwargs,
)
from app.modules.proxy._service.transcribe import (
    _TranscribeMixin,
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
    _WarmupMixin,
)
from app.modules.proxy._service.warmup import (
    _WarmupSubmitResult as _WarmupSubmitResult,
)
from app.modules.proxy._service.warmup import (
    _WarmupUsageSnapshot as _WarmupUsageSnapshot,
)
from app.modules.proxy._service.websocket import (
    _WebSocketMixin,
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
    _AffinityPolicy,
    _sticky_key_for_codex_control_request,
    _sticky_key_from_session_header,  # noqa: F401
)
from app.modules.proxy.affinity import (
    _owner_lookup_session_id_from_headers as _owner_lookup_session_id_from_headers,
)
from app.modules.proxy.affinity import (
    _sticky_key_for_responses_request as _sticky_key_for_responses_request,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup as DurableBridgeLookup,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeSessionCoordinator,
)
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _header_account_id,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext as HTTPBridgeForwardContext,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeOwnerClient,
)
from app.modules.proxy.http_bridge_forwarding import (
    OwnerForwardRelayFailure as OwnerForwardRelayFailure,
)
from app.modules.proxy.load_balancer import AccountLease, AccountLeaseKind, AccountSelection, LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.ring_membership import (
    RingMembershipService,
)
from app.modules.proxy.work_admission import WorkAdmissionController

logger = logging.getLogger(__name__)


_TASK_CANCEL_TIMEOUT_SECONDS = 1.0
_TaskResultT = TypeVar("_TaskResultT")
_ResponsesPayloadT = TypeVar("_ResponsesPayloadT", ResponsesRequest, ResponsesCompactRequest)
_DOWNSTREAM_WEBSOCKET_IDLE_CLOSE_REASON = "Idle downstream websocket timeout"
_DOWNSTREAM_WEBSOCKET_RECEIVE_POLL_SECONDS = 1.0
# Keep the first HTTP bridge liveness frame behind the API layer's startup
# error probe window. If a keepalive becomes the first yielded chunk, the HTTP
# status is committed as 200 and startup ProxyResponseError handling is masked.
_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS = 0.5
_DEFAULT_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS = 10.0


def _proxy_admission_wait_timeout_seconds(settings: Any | None = None) -> float:
    settings = settings or get_settings()
    raw_timeout = getattr(
        settings,
        "proxy_admission_wait_timeout_seconds",
        _DEFAULT_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS,
    )
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = _DEFAULT_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS
    return max(0.001, timeout)


# Maximum time (seconds) to wait for a prewarm upstream response before
# giving up and letting the actual request proceed without prewarming.
# A blocked prewarm holds the response_create_gate semaphore and prevents
# the real request from being sent, leading to an indefinite :keepalive hang.
_PREWARM_RESPONSE_TIMEOUT_SECONDS = 2.0
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100
# Maximum consecutive keepalive frames sent before terminating the stream.
# 6 × 10s (default interval) = 60s.  Combined with the 0.5s startup-probe
# window this ensures the client sees a terminal event within ≈70s when the
# upstream silently stops responding.
_STREAM_KEEPALIVE_MAX_COUNT = 6


async def _await_cancelled_task(
    task: asyncio.Task[_TaskResultT],
    *,
    timeout_seconds: float = _TASK_CANCEL_TIMEOUT_SECONDS,
    label: str,
) -> bool:
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout_seconds)
    except asyncio.CancelledError:
        return True
    except TimeoutError:
        logger.warning("Timed out waiting for %s cancellation", label)
        return False
    return True


_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_TEXT_DONE_CONTENT_PART_TYPES = frozenset({"output_text", "refusal"})
_REQUEST_TRANSPORT_HTTP = "http"
_COMPACT_SAME_CONTRACT_RETRY_BUDGET = 1
_ACCOUNT_RECOVERY_RETRY_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
        *PERMANENT_FAILURE_CODES.keys(),
    }
)
_TRANSIENT_RETRY_CODES = frozenset(
    {
        "server_error",
        "stream_incomplete",
        "stream_idle_timeout",
        "upstream_request_timeout",
    }
)
_UPSTREAM_UNAVAILABLE_TRANSIENT_MESSAGE_MARKERS = (
    "broken pipe",
    "cannot connect",
    "connection aborted",
    "connection closed",
    "connection reset",
    "keepalive ping timeout",
    "no close frame",
    "server disconnected",
    "timed out",
    "timeout",
    "upstream closed",
)
_UPSTREAM_UNAVAILABLE_NON_TRANSIENT_MESSAGE_MARKERS = (
    "certificate verify failed",
    "clientconnectorcertificateerror",
    "sslcertverificationerror",
)
_UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY = frozenset({1011})
_MAX_TRANSIENT_SAME_ACCOUNT_RETRIES = 3
_COMPACT_MAX_ACCOUNT_ATTEMPTS = 2
_STREAM_MAX_ACCOUNT_ATTEMPTS = 3
_WEBSOCKET_MAX_ACCOUNT_ATTEMPTS = 3
_WEBSOCKET_TRANSPARENT_REPLAY_ERROR_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
    }
)
_WEBSOCKET_AUTH_FAILURE_CODES = frozenset({"invalid_api_key", "invalid_authentication", "token_invalidated"})
_WEBSOCKET_REAUTH_REQUIRED_MESSAGE_MARKERS = (
    "session has ended",
    "session expired",
    "log in again",
    "login again",
    "reauth",
    "re-auth",
)
_WEBSOCKET_SESSION_EXPIRED_FAILURE_CODE = "account_session_expired"
_WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE = "account_auth_invalidated"
_SUPPRESSED_DUPLICATE_TOOL_CALL_MESSAGE = (
    "Suppressed duplicate side-effect tool call; upstream response cannot be continued safely."
)
_WEBSOCKET_PREVIOUS_RESPONSE_ACCOUNT_CACHE_LIMIT = 4096
_WEBSOCKET_CONTINUITY_CACHE_LIMIT = 4096
_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"
_SECURITY_WORK_AUTHORIZATION_REQUIRED_HINTS = (
    "flagged for possible cybersecurity risk",
    "authorized for security work",
    "chatgpt.com/cyber",
)
_SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "codex-lb is retrying on an account marked as authorized for security work."
)
_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. codex-lb is continuing with normal account selection; the upstream request may still fail until "
    "an account with Trusted Access for Cyber is marked as security-work-authorized."
)


@dataclass(frozen=True, slots=True)
class _HTTPBridgeRuntimeConfig:
    enabled: bool
    idle_ttl_seconds: float
    codex_idle_ttl_seconds: float
    max_sessions: int
    queue_limit: int
    prompt_cache_idle_ttl_seconds: float
    gateway_safe_mode: bool


def _estimated_lease_tokens_from_request_usage_budget(budget: ApiKeyRequestUsageBudget | None) -> float:
    if budget is None:
        return 0.0
    input_tokens = _bounded_lease_token_estimate(
        budget.input_tokens,
        default=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    )
    output_tokens = _bounded_lease_token_estimate(
        budget.output_tokens,
        default=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    )
    return float(input_tokens + output_tokens)


def _bounded_lease_token_estimate(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    return max(0, min(value, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET))


class ProxyService(
    _ApiKeyUsageMixin,
    _RequestLogMixin,
    _RateLimitMixin,
    _WarmupMixin,
    _FileOpsMixin,
    _TranscribeMixin,
    _CodexControlMixin,
    _CompactMixin,
    _StreamingMixin,
    _WebSocketMixin,
    _HTTPBridgeMixin,
):
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._encryptor = TokenEncryptor()
        self._load_balancer = LoadBalancer(repo_factory)
        self._ring_membership = RingMembershipService(SessionLocal)
        self._durable_bridge = DurableBridgeSessionCoordinator(SessionLocal)
        self._http_bridge_owner_client = HTTPBridgeOwnerClient()
        self._http_bridge_sessions: dict[_HTTPBridgeSessionKey, _HTTPBridgeSession] = {}
        self._http_bridge_inflight_sessions: dict[_HTTPBridgeSessionKey, asyncio.Future[_HTTPBridgeSession]] = {}
        self._http_bridge_turn_state_index: dict[tuple[str, str | None], _HTTPBridgeSessionKey] = {}
        self._http_bridge_previous_response_index: dict[tuple[str, str | None], _HTTPBridgeSessionKey] = {}
        self._websocket_previous_response_account_index: dict[tuple[str, str | None, str | None], str] = {}
        self._websocket_continuity_index: dict[tuple[str, str | None], _WebSocketContinuityState] = {}
        self._background_cleanup_tasks: set[asyncio.Task[None]] = set()
        # In-memory pin from upstream-issued file_id -> codex-lb account_id.
        # Used so ``finalize_file`` for a given ``file_id`` is routed to
        # the same account that handled ``create_file``. Cross-instance
        # routing is best-effort: if the finalize request lands on a
        # different replica with no pin, we fall back to a fresh load-
        # balancer selection. The TTL is short enough (5 min) that we
        # never hold stale pins after the upstream upload window closes.
        self._file_account_pins: dict[str, _FilePinEntry] = {}
        self._file_account_pin_lock = asyncio.Lock()
        self._http_bridge_lock = anyio.Lock()
        self._work_admission: WorkAdmissionController | None = None
        self._request_log_tasks: set[asyncio.Task[None]] = set()

    def _get_work_admission(self) -> WorkAdmissionController:
        if self._work_admission is None:
            settings = get_settings()
            self._work_admission = WorkAdmissionController(
                token_refresh_limit=settings.proxy_token_refresh_limit,
                websocket_connect_limit=settings.proxy_upstream_websocket_connect_limit,
                response_create_limit=settings.proxy_response_create_limit,
                compact_response_create_limit=settings.proxy_compact_response_create_limit,
                admission_wait_timeout_seconds=getattr(
                    settings,
                    "proxy_admission_wait_timeout_seconds",
                    10.0,
                ),
            )
        return self._work_admission

    async def thread_goal_request(
        self,
        operation: str,
        payload: Mapping[str, JsonValue],
        headers: Mapping[str, str],
        *,
        method: str = "POST",
        codex_session_affinity: bool = True,
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        filtered = filter_inbound_headers(headers)
        useragent, useragent_group = _request_log_useragent_fields(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        base_settings = get_settings()
        deadline = start + base_settings.proxy_request_budget_seconds
        settings = await get_settings_cache().get()
        affinity = _sticky_key_for_codex_control_request(
            headers,
            codex_session_affinity=codex_session_affinity,
        )
        selection_model = api_key.enforced_model if api_key is not None else None
        routing_strategy = _routing_strategy(settings)
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        failure_metadata = _RequestLogFailureMetadata()
        route_mode: str | None = None
        route_pool_id: str | None = None
        route_endpoint_id: str | None = None
        route_fallback_used: bool | None = None
        route_fail_closed_reason: str | None = None
        request_kind = f"thread_goal_{operation}"

        try:
            selection = await self._select_account_with_budget_compatible(
                deadline,
                request_id=request_id,
                kind=request_kind,
                api_key=api_key,
                sticky_key=affinity.key,
                sticky_kind=affinity.kind,
                reallocate_sticky=affinity.reallocate_sticky,
                sticky_max_age_seconds=affinity.max_age_seconds,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                routing_strategy=routing_strategy,
                model=selection_model,
            )
            account = selection.account
            if not account:
                account = await self._select_codex_control_account_without_budget(
                    affinity=affinity,
                    api_key=api_key,
                    traffic_class=TRAFFIC_CLASS_OPPORTUNISTIC
                    if api_key is not None and api_key.traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC
                    else TRAFFIC_CLASS_FOREGROUND,
                    prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                )
                if account is None:
                    log_error_code = selection.error_code or "no_accounts"
                    log_error_message = selection.error_message or "No active accounts available"
                    raise ProxyResponseError(
                        503,
                        openai_error(log_error_code, log_error_message),
                    )
            account_id_value = account.id

            async def _call_goal(target: Account) -> dict[str, JsonValue]:
                nonlocal route_fallback_used, route_mode, route_pool_id, route_endpoint_id
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                upstream_account_id = _header_account_id(target.chatgpt_account_id)
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Thread goal request budget exhausted before upstream call request_id=%s operation=%s "
                        "account_id=%s",
                        request_id,
                        operation,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                route = await self._resolve_upstream_route_for_account(target, operation=request_kind)
                if route is not None:
                    route_mode = route.mode
                    route_pool_id = route.pool_id
                    route_endpoint_id = route.endpoint_id
                route_trace = UpstreamProxyRouteTrace()
                try:
                    return await core_thread_goal_request(
                        operation,
                        payload,
                        filtered,
                        access_token,
                        upstream_account_id,
                        method=method,
                        timeout_seconds=remaining_budget,
                        route=route,
                        allow_direct_egress=route is None,
                        route_trace=route_trace,
                    )
                finally:
                    if route_trace.mode is not None:
                        route_mode = route_trace.mode
                        route_pool_id = route_trace.pool_id
                        route_endpoint_id = route_trace.endpoint_id
                        route_fallback_used = route_trace.fallback_used

            async def _select_goal_failover(excluded_account_ids: set[str]) -> AccountSelection:
                return await self._select_account_with_budget(
                    deadline,
                    request_id=request_id,
                    kind=request_kind,
                    api_key=api_key,
                    sticky_key=affinity.key,
                    sticky_kind=affinity.kind,
                    reallocate_sticky=affinity.reallocate_sticky,
                    sticky_max_age_seconds=affinity.max_age_seconds,
                    prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    model=selection_model,
                    exclude_account_ids=excluded_account_ids,
                )

            try:
                account = await self._ensure_previsible_unary_fresh_with_failover(
                    account,
                    deadline=deadline,
                    request_id=request_id,
                    kind=request_kind,
                    select_next_account=_select_goal_failover,
                )
                account_id_value = account.id
                response = await _call_goal(account)
                await self._load_balancer.record_success(account)
                log_status = "success"
                return response
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    failed_account = _refresh_error_failed_account(refresh_exc, account)
                    account_id_value = failed_account.id
                    await self._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                raise ProxyResponseError(
                    401,
                    openai_error(
                        "invalid_api_key",
                        refresh_exc.message,
                        error_type="invalid_request_error",
                    ),
                ) from refresh_exc
            except ProxyResponseError as exc:
                if exc.status_code != 401:
                    failover = await self._retry_previsible_unary_call_failover(
                        exc,
                        account,
                        deadline=deadline,
                        select_next_account=_select_goal_failover,
                        call_next=_call_goal,
                    )
                    if failover is not None:
                        account, response = failover
                        account_id_value = account.id
                        log_status = "success"
                        return response
                if exc.status_code == 401:
                    try:
                        remaining_budget = _remaining_budget_seconds(deadline)
                        if remaining_budget <= 0:
                            logger.warning(
                                "Thread goal request budget exhausted before forced refresh retry request_id=%s "
                                "operation=%s account_id=%s",
                                request_id,
                                operation,
                                account.id,
                            )
                            _raise_proxy_budget_exhausted()
                        try:
                            account = await self._ensure_previsible_unary_fresh_with_failover(
                                account,
                                deadline=deadline,
                                request_id=request_id,
                                kind=request_kind,
                                select_next_account=_select_goal_failover,
                                force=True,
                            )
                        except ProxyResponseError as refresh_failover_exc:
                            failed_account = _proxy_response_failed_account(refresh_failover_exc, account)
                            account_id_value = failed_account.id
                            await self._handle_proxy_error(failed_account, refresh_failover_exc)
                            raise
                        account_id_value = account.id
                        try:
                            response = await _call_goal(account)
                            await self._load_balancer.record_success(account)
                            log_status = "success"
                            return response
                        except ProxyResponseError as retry_exc:
                            await self._handle_proxy_error(account, retry_exc)
                            if retry_exc.status_code == 401:
                                selection = await self._select_account_with_budget_compatible(
                                    deadline,
                                    request_id=request_id,
                                    kind=request_kind,
                                    api_key=api_key,
                                    sticky_key=affinity.key,
                                    sticky_kind=affinity.kind,
                                    reallocate_sticky=affinity.reallocate_sticky,
                                    sticky_max_age_seconds=affinity.max_age_seconds,
                                    prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                                    prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                                    routing_strategy=routing_strategy,
                                    model=selection_model,
                                    exclude_account_ids={account.id},
                                )
                                if selection.account is not None:
                                    account = selection.account
                                    account_id_value = account.id
                                    account = await self._ensure_fresh_with_budget_or_auth_error(
                                        account,
                                        timeout_seconds=_remaining_budget_seconds(deadline),
                                    )
                                    try:
                                        response = await _call_goal(account)
                                        await self._load_balancer.record_success(account)
                                        log_status = "success"
                                        return response
                                    except ProxyResponseError as failover_exc:
                                        await self._handle_proxy_error(account, failover_exc)
                                        raise
                            raise
                    except RefreshError as refresh_exc:
                        if refresh_exc.is_permanent:
                            failed_account = _refresh_error_failed_account(refresh_exc, account)
                            account_id_value = failed_account.id
                            await self._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                        raise exc
                    except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                        logger.warning(
                            "Thread goal forced refresh/connect failed request_id=%s operation=%s account_id=%s",
                            request_id,
                            operation,
                            account.id,
                            exc_info=True,
                        )
                        _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                if operation == "get" and _is_missing_thread_goal_protocol_error(exc):
                    log_status = "success"
                    return {"goal": None}
                failed_account = _proxy_response_failed_account(exc, account)
                account_id_value = failed_account.id
                await self._handle_proxy_error(failed_account, exc)
                raise
        except ProxyResponseError as exc:
            failed_account = getattr(exc, _FAILED_ACCOUNT_ATTR, None)
            if isinstance(failed_account, Account):
                account_id_value = failed_account.id
            failure_metadata = _request_log_failure_metadata(exc)
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        except UpstreamProxyRouteError as exc:
            route_fail_closed_reason = exc.reason
            log_error_code = "upstream_proxy_unavailable"
            log_error_message = exc.reason
            raise ProxyResponseError(
                502,
                openai_error("upstream_proxy_unavailable", f"Upstream proxy route unavailable: {exc.reason}"),
            ) from exc
        finally:
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=None,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                transport=_REQUEST_TRANSPORT_HTTP,
                failure_phase=failure_metadata.failure_phase,
                failure_detail=failure_metadata.failure_detail,
                failure_exception_type=failure_metadata.failure_exception_type,
                upstream_status_code=failure_metadata.upstream_status_code,
                upstream_error_code=failure_metadata.upstream_error_code,
                bridge_stage=failure_metadata.bridge_stage,
                upstream_proxy_route_mode=route_mode,
                upstream_proxy_pool_id=route_pool_id,
                upstream_proxy_endpoint_id=route_endpoint_id,
                upstream_proxy_fallback_used=route_fallback_used if route_endpoint_id else None,
                upstream_proxy_fail_closed_reason=route_fail_closed_reason,
                useragent=useragent,
                useragent_group=useragent_group,
            )

    async def _acquire_request_state_response_create_admission(
        self,
        request_state: _WebSocketRequestState,
        *,
        response_create_gate: asyncio.Semaphore,
        bridge_session: "_HTTPBridgeSession | None" = None,
        compact: bool = False,
        account_id: str | None = None,
        surface: str = "websocket",
    ) -> None:
        timeout_seconds = _proxy_admission_wait_timeout_seconds()
        request_state.response_create_gate = response_create_gate
        if account_id is not None:
            request_state.account_response_create_lease = await self._acquire_account_response_create_lease_or_overload(
                account_id=account_id,
                request_id=request_state.request_id,
                surface=surface,
            )
            request_state.account_response_create_release = self._load_balancer.release_account_lease
        try:
            await asyncio.wait_for(response_create_gate.acquire(), timeout=timeout_seconds)
        except TimeoutError as exc:
            await self._release_request_state_account_response_create_lease(request_state)
            request_state.response_create_gate = None
            request_state.response_create_gate_acquired = False
            request_state.awaiting_response_created = False
            pending_count = None
            queued_count = None
            pending_request_ids: list[str] | None = None
            pending_request_ages_seconds: list[float] | None = None
            if bridge_session is not None:
                now = time.monotonic()
                async with bridge_session.pending_lock:
                    pending_states = list(bridge_session.pending_requests)
                    pending_count = len(pending_states)
                    queued_count = bridge_session.queued_request_count
                pending_request_ids = [state.request_log_id or state.request_id for state in pending_states]
                pending_request_ages_seconds = [max(0.0, now - state.started_at) for state in pending_states]
            _log_http_bridge_startup_wait_timeout(
                stage="response_create_gate",
                timeout_seconds=timeout_seconds,
                key=bridge_session.key if bridge_session is not None else None,
                request_id=request_state.request_id,
                request_model=request_state.model,
                pending_count=pending_count,
                queued_count=queued_count,
                available=getattr(response_create_gate, "_value", None),
                pending_request_ids=pending_request_ids,
                pending_request_ages_seconds=pending_request_ages_seconds,
            )
            raise _http_bridge_startup_wait_timeout_error(
                "http_bridge_response_create_gate",
                code="response_create_gate_timeout",
            ) from exc
        except BaseException:
            await self._release_request_state_account_response_create_lease(request_state)
            request_state.response_create_gate = None
            request_state.response_create_gate_acquired = False
            request_state.awaiting_response_created = False
            raise
        request_state.response_create_gate_acquired = True
        request_state.awaiting_response_created = True
        try:
            request_state.response_create_admission = await self._get_work_admission().acquire_response_create(
                compact=compact
            )
        except BaseException:
            await self._release_request_state_account_response_create_lease(request_state)
            await _release_websocket_response_create_gate(request_state, response_create_gate)
            raise

    async def _release_request_state_account_response_create_lease(
        self,
        request_state: "_WebSocketRequestState",
    ) -> None:
        lease = request_state.account_response_create_lease
        request_state.account_response_create_lease = None
        request_state.account_response_create_release = None
        await self._load_balancer.release_account_lease(lease)

    async def _select_account_with_budget_compatible(
        self,
        deadline: float,
        **kwargs: object,
    ) -> AccountSelection:
        select_account = self._select_account_with_budget
        select_account_any = cast(Any, select_account)
        try:
            signature = inspect.signature(select_account)
        except (TypeError, ValueError):
            return await select_account_any(deadline, **kwargs)

        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return await select_account_any(deadline, **kwargs)

        supported_kwargs = {name: value for name, value in kwargs.items() if name in signature.parameters}
        return await select_account_any(deadline, **supported_kwargs)

    async def _select_codex_control_account_without_budget(
        self,
        *,
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    ) -> Account | None:
        scoped_account_ids = (
            set(api_key.assigned_account_ids)
            if api_key is not None and api_key.account_assignment_scope_enabled
            else None
        )
        settings = await get_settings_cache().get()
        if _routing_strategy(settings) == "single_account":
            selected_account_id = (settings.single_account_id or "").strip()
            if not selected_account_id:
                return None
            if scoped_account_ids is not None and selected_account_id not in scoped_account_ids:
                return None
            scoped_account_ids = {selected_account_id}
        selection = await self._load_balancer.select_account(
            sticky_key=affinity.key,
            sticky_kind=affinity.kind,
            reallocate_sticky=affinity.reallocate_sticky,
            sticky_max_age_seconds=affinity.max_age_seconds,
            account_ids=scoped_account_ids,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=_routing_strategy(settings),
            budget_threshold_pct=_sticky_reallocation_primary_budget_threshold_pct(settings),
            secondary_budget_threshold_pct=_sticky_reallocation_secondary_budget_threshold_pct(settings),
            traffic_class=traffic_class,
        )
        if selection.account is None:
            return None
        return _detached_account_copy(selection.account)

    @asynccontextmanager
    async def _accounts_refresh_scope(self) -> AsyncIterator[AccountsRepositoryPort]:
        # Fresh, self-contained accounts repo (own DB session) for AuthManager's
        # detached/shielded token-refresh task. A client disconnect cancels the
        # request and closes the request-scoped session below; without this the
        # still-running refresh task would touch that closed session and strand
        # a background-pool connection (the codex-lb pool-exhaustion leak).
        async with self._repo_factory() as repos:
            yield repos.accounts

    async def _ensure_fresh(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        token = push_token_refresh_timeout_override(timeout_seconds)
        try:
            async with self._repo_factory() as repos:
                auth_manager = AuthManager(
                    repos.accounts,
                    acquire_refresh_admission=self._get_work_admission().acquire_token_refresh,
                    refresh_repo_factory=self._accounts_refresh_scope,
                )
                refresh = auth_manager.ensure_fresh(account, force=force)
                if timeout_seconds is None:
                    return await refresh
                return await asyncio.wait_for(refresh, timeout=max(0.001, timeout_seconds))
        finally:
            pop_token_refresh_timeout_override(token)

    async def _ensure_fresh_with_budget(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        try:
            return await self._ensure_fresh(account, force=force, timeout_seconds=timeout_seconds)
        except RefreshError as exc:
            reason = _refresh_upstream_proxy_fail_closed_reason(exc)
            if reason is not None:
                raise UpstreamProxyRouteError(reason, account_id=account.id) from exc
            raise

    async def _ensure_previsible_unary_fresh_with_failover(
        self,
        account: Account,
        *,
        deadline: float,
        request_id: str,
        kind: str,
        select_next_account: Callable[[set[str]], Awaitable[AccountSelection]],
        strict_account_id: str | None = None,
        force: bool = False,
        max_account_attempts: int = 2,
    ) -> Account:
        current: Account = account
        excluded_account_ids: set[str] = set()
        attempt = 0
        force_current = force
        while True:
            attempt += 1
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                logger.warning(
                    "%s request budget exhausted before freshness check request_id=%s account_id=%s",
                    kind,
                    request_id,
                    current.id,
                )
                _raise_proxy_budget_exhausted()
            try:
                return await self._ensure_fresh_with_budget(
                    current,
                    force=force_current,
                    timeout_seconds=remaining_budget,
                )
            except RefreshError as exc:
                if exc.transport_error:
                    message = exc.message or str(exc) or "Request to upstream timed out"
                    logger.warning(
                        "%s refresh transport failed request_id=%s account_id=%s",
                        kind,
                        request_id,
                        current.id,
                        exc_info=True,
                    )
                    if not _should_retry_transient_stream_error("upstream_unavailable", message):
                        _raise_proxy_unavailable_for_account(message, current)
                    if (
                        strict_account_id is not None and current.id == strict_account_id
                    ) or attempt >= max_account_attempts:
                        _raise_proxy_unavailable_for_account(message, current)
                    excluded_account_ids.add(current.id)
                    selection = await select_next_account(excluded_account_ids)
                    selected_account = selection.account
                    if selected_account is None:
                        _raise_proxy_unavailable_for_account(message, current)
                    assert selected_account is not None
                    await self._handle_stream_error(
                        current,
                        {"message": message},
                        "upstream_unavailable",
                    )
                    current = selected_account
                    force_current = False
                    continue
                setattr(exc, _FAILED_ACCOUNT_ATTR, current)
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                logger.warning(
                    "%s refresh/connect failed request_id=%s account_id=%s",
                    kind,
                    request_id,
                    current.id,
                    exc_info=True,
                )
                if not _should_retry_transient_stream_error("upstream_unavailable", message):
                    _raise_proxy_unavailable_for_account(message, current)
                if (
                    strict_account_id is not None and current.id == strict_account_id
                ) or attempt >= max_account_attempts:
                    _raise_proxy_unavailable_for_account(message, current)
                excluded_account_ids.add(current.id)
                selection = await select_next_account(excluded_account_ids)
                selected_account = selection.account
                if selected_account is None:
                    _raise_proxy_unavailable_for_account(message, current)
                assert selected_account is not None
                await self._handle_stream_error(
                    current,
                    {"message": message},
                    "upstream_unavailable",
                )
                current = selected_account
                force_current = False

    async def _retry_previsible_unary_call_failover(
        self,
        exc: ProxyResponseError,
        account: Account,
        *,
        deadline: float,
        select_next_account: Callable[[set[str]], Awaitable[AccountSelection]],
        call_next: Callable[[Account], Awaitable[Any]],
        strict_account_id: str | None = None,
    ) -> tuple[Account, Any] | None:
        if hasattr(exc, _FAILED_ACCOUNT_ATTR):
            return None
        if not _should_failover_previsible_unary_proxy_error(exc):
            return None
        failed_account = _proxy_response_failed_account(exc, account)
        if strict_account_id is not None and failed_account.id == strict_account_id:
            return None
        selection = await select_next_account({failed_account.id})
        if selection.account is None:
            return None
        await self._handle_proxy_error(failed_account, exc)
        try:
            next_account = await self._ensure_fresh_with_budget_or_auth_error(
                selection.account,
                timeout_seconds=_remaining_budget_seconds(deadline),
            )
        except ProxyResponseError as failover_exc:
            failover_failed_account = _proxy_response_failed_account(failover_exc, selection.account)
            setattr(failover_exc, _FAILED_ACCOUNT_ATTR, failover_failed_account)
            if failover_exc.status_code != 401:
                await self._handle_proxy_error(failover_failed_account, failover_exc)
            raise
        try:
            result = await call_next(next_account)
        except ProxyResponseError as failover_exc:
            failover_failed_account = _proxy_response_failed_account(failover_exc, next_account)
            setattr(failover_exc, _FAILED_ACCOUNT_ATTR, failover_failed_account)
            if failover_exc.status_code == 401:
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    _raise_proxy_budget_exhausted()
                try:
                    refreshed_account = await self._ensure_fresh_with_budget_or_auth_error(
                        next_account,
                        force=True,
                        timeout_seconds=remaining_budget,
                    )
                except ProxyResponseError as refresh_exc:
                    refresh_failed_account = _proxy_response_failed_account(refresh_exc, next_account)
                    setattr(refresh_exc, _FAILED_ACCOUNT_ATTR, refresh_failed_account)
                    if refresh_exc.status_code != 401:
                        await self._handle_proxy_error(refresh_failed_account, refresh_exc)
                    raise
                try:
                    retry_result = await call_next(refreshed_account)
                except ProxyResponseError as retry_exc:
                    retry_failed_account = _proxy_response_failed_account(retry_exc, refreshed_account)
                    setattr(retry_exc, _FAILED_ACCOUNT_ATTR, retry_failed_account)
                    await self._handle_proxy_error(retry_failed_account, retry_exc)
                    raise
                await self._load_balancer.record_success(refreshed_account)
                return refreshed_account, retry_result
            await self._handle_proxy_error(failover_failed_account, failover_exc)
            raise
        await self._load_balancer.record_success(next_account)
        return next_account, result

    async def _ensure_fresh_with_budget_or_auth_error(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
        error_type: str = "invalid_request_error",
    ) -> Account:
        try:
            return await self._ensure_fresh_with_budget(account, force=force, timeout_seconds=timeout_seconds)
        except RefreshError as refresh_exc:
            failed_account = _refresh_error_failed_account(refresh_exc, account)
            if refresh_exc.transport_error:
                _raise_proxy_unavailable_for_account(
                    refresh_exc.message or str(refresh_exc) or "Request to upstream timed out",
                    failed_account,
                )
            if refresh_exc.is_permanent:
                await self._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
            raise ProxyResponseError(
                401,
                openai_error(
                    "invalid_api_key",
                    refresh_exc.message,
                    error_type=error_type,
                ),
            ) from refresh_exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")

    async def _select_account_with_budget(
        self,
        deadline: float,
        *,
        request_id: str,
        kind: str,
        request_stage: str = "first_turn",
        api_key: ApiKeyData | None = None,
        sticky_key: str | None = None,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
        routing_strategy: RoutingStrategy = "capacity_weighted",
        model: str | None = None,
        service_tier: str | None = None,
        additional_limit_name: str | None = None,
        exclude_account_ids: Collection[str] | None = None,
        preferred_account_id: str | None = None,
        require_security_work_authorized: bool = False,
        lease_kind: Literal["response_create", "stream"] | None = None,
        estimated_lease_tokens: float = 0.0,
        fallback_on_preferred_account_unavailable: bool = True,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ) -> AccountSelection:
        remaining_budget = _remaining_budget_seconds(deadline)
        if remaining_budget <= 0:
            logger.warning(
                "%s request budget exhausted before account selection request_id=%s", kind.title(), request_id
            )
            _raise_proxy_budget_exhausted()
        scoped_account_ids = (
            set(api_key.assigned_account_ids)
            if api_key is not None and api_key.account_assignment_scope_enabled
            else None
        )
        effective_traffic_class = (
            TRAFFIC_CLASS_OPPORTUNISTIC
            if api_key is not None and api_key.traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC
            else traffic_class
        )
        excluded_account_ids_set = set(exclude_account_ids or ())
        logger.info(
            "Proxy account selection start request_id=%s kind=%s request_stage=%s model=%s "
            "additional_limit=%s sticky=%s sticky_kind=%s reallocate_sticky=%s prefer_earlier_reset=%s "
            "routing_strategy=%s api_key_present=%s api_key_scope_enabled=%s scoped_count=%s "
            "excluded_count=%s preferred_account_id=%s remaining_budget=%.2f",
            request_id,
            kind,
            request_stage,
            model,
            additional_limit_name,
            bool(sticky_key),
            sticky_kind.value if sticky_kind is not None else None,
            reallocate_sticky,
            prefer_earlier_reset_accounts,
            routing_strategy,
            api_key is not None,
            bool(api_key is not None and api_key.account_assignment_scope_enabled),
            None if scoped_account_ids is None else len(scoped_account_ids),
            len(excluded_account_ids_set),
            preferred_account_id,
            remaining_budget,
        )
        try:
            with anyio.fail_after(remaining_budget):
                settings = await get_settings_cache().get()
                required_preferred_account = (
                    preferred_account_id is not None and not fallback_on_preferred_account_unavailable
                )
                if _routing_strategy(settings) == "single_account" and not required_preferred_account:
                    selected_account_id = (settings.single_account_id or "").strip()
                    if not selected_account_id:
                        return AccountSelection(
                            account=None,
                            error_message="Single account routing is enabled but no account is selected",
                            error_code="single_account_not_configured",
                        )
                    if selected_account_id in excluded_account_ids_set:
                        return AccountSelection(
                            account=None,
                            error_message="Selected single account is unavailable",
                            error_code="single_account_unavailable",
                        )
                    if scoped_account_ids is not None and selected_account_id not in scoped_account_ids:
                        return AccountSelection(
                            account=None,
                            error_message="Selected single account is outside the API key account scope",
                            error_code="single_account_scope_mismatch",
                        )
                    scoped_account_ids = {selected_account_id}
                    routing_strategy = "single_account"
                preferred_eligible = (
                    preferred_account_id is not None
                    and preferred_account_id not in excluded_account_ids_set
                    and (scoped_account_ids is None or preferred_account_id in scoped_account_ids)
                )
                if preferred_account_id is not None and not preferred_eligible:
                    logger.warning(
                        "Proxy preferred account skipped request_id=%s kind=%s request_stage=%s "
                        "preferred_account_id=%s excluded=%s outside_api_key_scope=%s",
                        request_id,
                        kind,
                        request_stage,
                        preferred_account_id,
                        preferred_account_id in excluded_account_ids_set,
                        scoped_account_ids is not None and preferred_account_id not in scoped_account_ids,
                    )
                    if not fallback_on_preferred_account_unavailable:
                        return AccountSelection(
                            account=None,
                            error_message="Preferred account is not available",
                            error_code="preferred_account_unavailable",
                        )
                if preferred_eligible:
                    preferred_selection = await self._load_balancer.select_account(
                        sticky_key=sticky_key,
                        sticky_kind=sticky_kind,
                        reallocate_sticky=reallocate_sticky,
                        sticky_max_age_seconds=sticky_max_age_seconds,
                        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                        prefer_earlier_reset_window=prefer_earlier_reset_window,
                        routing_strategy=routing_strategy,
                        relative_availability_power=_relative_availability_power(settings),
                        relative_availability_top_k=_relative_availability_top_k(settings),
                        model=model,
                        service_tier=service_tier,
                        additional_limit_name=additional_limit_name,
                        account_ids={preferred_account_id},
                        require_security_work_authorized=require_security_work_authorized,
                        budget_threshold_pct=_sticky_reallocation_primary_budget_threshold_pct(settings),
                        secondary_budget_threshold_pct=_sticky_reallocation_secondary_budget_threshold_pct(settings),
                        lease_kind=lease_kind,
                        estimated_lease_tokens=estimated_lease_tokens,
                        traffic_class=effective_traffic_class,
                    )
                    if preferred_selection.account is not None:
                        logger.info(
                            "Selected preferred account request_id=%s kind=%s request_stage=%s account_id=%s",
                            request_id,
                            kind,
                            request_stage,
                            preferred_account_id,
                        )
                        return preferred_selection
                    if not fallback_on_preferred_account_unavailable:
                        logger.warning(
                            "Proxy preferred account unavailable request_id=%s kind=%s request_stage=%s "
                            "preferred_account_id=%s error_code=%s error=%s",
                            request_id,
                            kind,
                            request_stage,
                            preferred_account_id,
                            preferred_selection.error_code,
                            preferred_selection.error_message,
                        )
                        return preferred_selection
                selection = await self._load_balancer.select_account(
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    reallocate_sticky=reallocate_sticky,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                    prefer_earlier_reset_window=prefer_earlier_reset_window,
                    routing_strategy=routing_strategy,
                    relative_availability_power=_relative_availability_power(settings),
                    relative_availability_top_k=_relative_availability_top_k(settings),
                    model=model,
                    service_tier=service_tier,
                    additional_limit_name=additional_limit_name,
                    account_ids=scoped_account_ids,
                    exclude_account_ids=excluded_account_ids_set,
                    require_security_work_authorized=require_security_work_authorized,
                    budget_threshold_pct=_sticky_reallocation_primary_budget_threshold_pct(settings),
                    secondary_budget_threshold_pct=_sticky_reallocation_secondary_budget_threshold_pct(settings),
                    lease_kind=lease_kind,
                    estimated_lease_tokens=estimated_lease_tokens,
                    traffic_class=effective_traffic_class,
                )
                if selection.account is not None and selection.account.id in excluded_account_ids_set:
                    logger.warning(
                        "Proxy account selection returned excluded account request_id=%s kind=%s request_stage=%s "
                        "account_id=%s excluded_count=%s",
                        request_id,
                        kind,
                        request_stage,
                        selection.account.id,
                        len(excluded_account_ids_set),
                    )
                    return AccountSelection(
                        account=None,
                        error_message="No active accounts available",
                        error_code="no_accounts",
                    )
                logger.info(
                    "Proxy account selection result request_id=%s kind=%s request_stage=%s model=%s "
                    "selected_account_id=%s error_code=%s error=%s scoped_count=%s excluded_count=%s",
                    request_id,
                    kind,
                    request_stage,
                    model,
                    selection.account.id if selection.account is not None else None,
                    selection.error_code,
                    selection.error_message,
                    None if scoped_account_ids is None else len(scoped_account_ids),
                    len(excluded_account_ids_set),
                )
                return selection
        except TimeoutError:
            logger.warning("%s account selection exceeded request budget request_id=%s", kind.title(), request_id)
            _raise_proxy_budget_exhausted()

    async def _acquire_account_response_create_lease_or_overload(
        self,
        *,
        account_id: str,
        request_id: str,
        surface: str,
    ) -> AccountLease:
        lease = await self._load_balancer.acquire_account_lease(
            account_id,
            kind="response_create",
        )
        if lease is not None:
            return lease
        inflight_create, inflight_stream, leased_tokens = await self._load_balancer.account_pressure_snapshot(
            account_id
        )
        logger.warning(
            "Responses account response-create cap reached request_id=%s surface=%s account_id=%s "
            "inflight_create=%s inflight_stream=%s leased_tokens=%.3f",
            request_id,
            surface,
            account_id,
            inflight_create,
            inflight_stream,
            leased_tokens,
        )
        raise ProxyResponseError(
            429,
            openai_error(
                "account_response_create_cap",
                "Account response-create capacity is exhausted",
                error_type="rate_limit_error",
            ),
        )

    async def check_opportunistic_admission(
        self,
        *,
        api_key: ApiKeyData | None,
        model: str | None,
        lease_kind: AccountLeaseKind | None = None,
    ) -> AccountSelection:
        settings = await get_settings_cache().get()
        scoped_account_ids = (
            set(api_key.assigned_account_ids)
            if api_key is not None and api_key.account_assignment_scope_enabled
            else None
        )
        if _routing_strategy(settings) == "single_account":
            selected_account_id = (settings.single_account_id or "").strip()
            if selected_account_id:
                scoped_account_ids = (
                    {selected_account_id}
                    if scoped_account_ids is None or selected_account_id in scoped_account_ids
                    else set()
                )
            else:
                scoped_account_ids = set()
        return await self._load_balancer.check_opportunistic_admission(
            model=model,
            account_ids=scoped_account_ids,
            prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
            routing_strategy=_routing_strategy(settings),
            budget_threshold_pct=_sticky_reallocation_primary_budget_threshold_pct(settings),
            secondary_budget_threshold_pct=_sticky_reallocation_secondary_budget_threshold_pct(settings),
            lease_kind=lease_kind,
        )

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        code = _normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        )
        if _is_account_neutral_error_code(code):
            return
        await self._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            code,
            http_status=exc.status_code,
        )


def _is_account_neutral_error_code(code: str | None) -> bool:
    return is_local_overload_error_code(code) or code == "proxy_unavailable"


def _is_local_account_cap_code(code: str | None) -> bool:
    return code in {"account_response_create_cap", "account_stream_cap"}


def _http_error_status_from_payload(payload: dict[str, JsonValue] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    for status_field in ("status", "status_code"):
        status = payload.get(status_field)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    return None


def _openai_error_envelope_from_response_failed_payload(
    payload: dict[str, JsonValue] | None,
) -> OpenAIErrorEnvelope:
    default_envelope = openai_error("upstream_error", "Upstream error")
    if not isinstance(payload, dict):
        return default_envelope
    response_payload = payload.get("response")
    if not isinstance(response_payload, dict):
        return default_envelope
    error_payload = response_payload.get("error")
    if not isinstance(error_payload, dict):
        return default_envelope

    message_value = error_payload.get("message")
    if isinstance(message_value, str) and message_value.strip():
        message = message_value.strip()
    else:
        message = "Upstream error"

    code_value = error_payload.get("code")
    code = code_value.strip() if isinstance(code_value, str) and code_value.strip() else "upstream_error"

    type_value = error_payload.get("type")
    error_type = type_value.strip() if isinstance(type_value, str) and type_value.strip() else "server_error"

    envelope = openai_error(code, message, error_type)
    param_value = error_payload.get("param")
    if isinstance(param_value, str) and param_value.strip():
        envelope["error"]["param"] = param_value.strip()
    error_detail = envelope["error"]
    plan_type = error_payload.get("plan_type")
    if plan_type is not None:
        error_detail["plan_type"] = str(plan_type)
    resets_at = error_payload.get("resets_at")
    if isinstance(resets_at, int | float):
        error_detail["resets_at"] = resets_at
    resets_in = error_payload.get("resets_in_seconds")
    if isinstance(resets_in, int | float):
        error_detail["resets_in_seconds"] = resets_in
    return envelope


def _is_previous_response_not_found_message(message: str | None) -> bool:
    return is_previous_response_not_found_message(message)


def _previous_response_id_from_not_found_message(message: str | None) -> str | None:
    return previous_response_id_from_not_found_message(message)


def _message_mentions_previous_response_id(message: str | None, previous_response_id: str | None) -> bool:
    if message is None or previous_response_id is None:
        return False
    normalized_message = " ".join(message.split())
    normalized_previous_response_id = previous_response_id.strip()
    if not normalized_previous_response_id:
        return False
    identifier_pattern = re.escape(normalized_previous_response_id)
    return (
        re.search(
            rf"(?<![A-Za-z0-9_-]){identifier_pattern}(?![A-Za-z0-9_-])",
            normalized_message,
        )
        is not None
    )


def _normalize_session_id(session_id: str | None) -> str | None:
    if not isinstance(session_id, str):
        return None
    stripped = session_id.strip()
    return stripped or None


def _is_missing_tool_output_error(
    *,
    code: str | None,
    param: str | None,
    message: str | None,
) -> bool:
    if code != "invalid_request_error" or param != "input" or message is None:
        return False
    normalized = " ".join(message.lower().split())
    return normalized.startswith("no tool output found for function call call_")


def _is_previous_response_not_found_error(
    *,
    code: str | None,
    param: str | None,
    message: str | None,
) -> bool:
    return is_previous_response_not_found_error(code=code, param=param, message=message)


def _compact_previous_response_not_found_error(exc: ProxyResponseError) -> ProxyResponseError | None:
    error = _parse_openai_error(exc.payload)
    if error is None:
        return None
    code = _normalize_error_code(error.code, error.type)
    if not _is_previous_response_not_found_error(
        code=code,
        param=error.param,
        message=error.message,
    ):
        return None
    return ProxyResponseError(
        502,
        previous_response_stream_incomplete_error(),
        failure_phase=exc.failure_phase,
        retryable_same_contract=False,
        failure_detail="previous_response_not_found",
        failure_exception_type=exc.failure_exception_type,
        upstream_status_code=exc.upstream_status_code if exc.upstream_status_code is not None else exc.status_code,
        upstream_error_code=code,
    )


def _proxy_response_error_code(exc: ProxyResponseError) -> str | None:
    error = _parse_openai_error(exc.payload)
    if error is None:
        return None
    return _normalize_error_code(error.code, error.type)


_LOCAL_PROXY_ERROR_CODES = frozenset(
    {
        "bridge_owner_forward_failed",
        "bridge_instance_mismatch",
        "bridge_owner_unreachable",
        "preferred_account_unavailable",
        "previous_response_owner_unavailable",
        "insufficient_image_quota",
        "ip_forbidden",
        "no_accounts",
        "no_plan_support_for_model",
        "additional_quota_data_unavailable",
        "no_additional_quota_eligible_accounts",
        "payload_too_large",
        "proxy_overloaded",
        "upstream_request_timeout",
        "upstream_unavailable",
    }
)


def _request_log_failure_metadata(
    exc: ProxyResponseError,
    *,
    bridge_stage: str | None = None,
) -> _RequestLogFailureMetadata:
    upstream_error_code = exc.upstream_error_code or _proxy_response_error_code(exc)
    resolved_bridge_stage = bridge_stage
    if resolved_bridge_stage is None and (
        exc.failure_phase in {"owner_forward", "owner_forward_status"}
        or upstream_error_code in {"bridge_owner_unreachable", "bridge_owner_forward_failed"}
    ):
        resolved_bridge_stage = "owner_forward"
    upstream_status_code = exc.upstream_status_code
    if upstream_status_code is None and _should_infer_upstream_status_from_proxy_error(exc, upstream_error_code):
        upstream_status_code = exc.status_code
    return _RequestLogFailureMetadata(
        failure_phase=exc.failure_phase,
        failure_detail=exc.failure_detail,
        failure_exception_type=exc.failure_exception_type,
        upstream_status_code=upstream_status_code,
        upstream_error_code=upstream_error_code,
        bridge_stage=resolved_bridge_stage,
    )


def _previous_response_id_from_payload(payload: Mapping[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    previous_response_id = payload.get("previous_response_id")
    if isinstance(previous_response_id, str) and previous_response_id.strip():
        return previous_response_id.strip()
    return None


def _partial_output_proxy_error_event_block(
    exc: ProxyResponseError,
    *,
    response_id: str,
    previous_response_id: str | None,
    preferred_account_id: str | None,
    default_code: str,
    default_message: str,
) -> str:
    error = _parse_openai_error(exc.payload)
    error_code = _normalize_error_code(
        error.code if error else None,
        error.type if error else None,
    )
    error_message = error.message if error else None
    effective_previous_response_id = previous_response_id or _previous_response_id_from_not_found_message(
        error_message,
    )
    rewritten_error = _rewrite_previous_response_stream_error(
        previous_response_id=effective_previous_response_id,
        preferred_account_id=preferred_account_id,
        error_code=error_code,
        error_type=error.type if error else None,
        error_message=error_message,
        error_param=error.param if error else None,
    )
    if rewritten_error is not None:
        rewritten_code, rewritten_message, upstream_error_code = rewritten_error
        if upstream_error_code is None:
            event = response_failed_event(
                rewritten_code,
                rewritten_message,
                error_type="server_error",
                response_id=response_id,
            )
            return format_sse_event(event)
    event = response_failed_event(
        error_code or default_code,
        error_message or default_message,
        error_type=(error.type if error and error.type else "server_error"),
        response_id=response_id,
        error_param=error.param if error else None,
    )
    _apply_error_metadata(event["response"]["error"], error)
    return format_sse_event(event)


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    value = getattr(settings, "routing_strategy", None) or "capacity_weighted"
    if value == "single_account":
        return "single_account"
    if value == "sequential_drain":
        return "sequential_drain"
    if value == "reset_drain":
        return "reset_drain"
    if value == "round_robin":
        return "round_robin"
    if value == "usage_weighted":
        return "usage_weighted"
    if value == "relative_availability":
        return "relative_availability"
    if value == "fill_first":
        return "fill_first"
    return "capacity_weighted"


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    return await _support_call_with_supported_optional_kwargs(
        func,
        *args,
        optional_kwargs=optional_kwargs,
        **required_kwargs,
    )


def _supported_optional_kwargs(
    func: Callable[..., Any],
    optional_kwargs: Mapping[str, Any],
    required_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    return _support_supported_optional_kwargs(func, optional_kwargs, required_kwargs)


def _relative_availability_power(settings: DashboardSettings) -> float:
    raw_value = getattr(settings, "relative_availability_power", None)
    value = float(raw_value) if raw_value is not None else 2.0
    return value if value > 0.0 else 2.0


def _relative_availability_top_k(settings: DashboardSettings) -> int:
    raw_value = getattr(settings, "relative_availability_top_k", None)
    value = int(raw_value) if raw_value is not None else 5
    return min(max(value, 1), 20)


def _prefer_earlier_reset_window(settings: DashboardSettings) -> ResetPreferenceWindow:
    return "primary" if getattr(settings, "prefer_earlier_reset_window", None) == "primary" else "secondary"


def _sticky_reallocation_primary_budget_threshold_pct(settings: DashboardSettings) -> float:
    value = getattr(settings, "sticky_reallocation_primary_budget_threshold_pct", None)
    if value is None:
        value = getattr(settings, "sticky_reallocation_budget_threshold_pct", None)
    return float(value if value is not None else 95.0)


def _sticky_reallocation_secondary_budget_threshold_pct(settings: DashboardSettings) -> float:
    value = getattr(settings, "sticky_reallocation_secondary_budget_threshold_pct", None)
    return float(value if value is not None else 100.0)


def _remaining_budget_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _proxy_request_timeout_event(request_id: str) -> ResponseFailedEvent:
    return response_failed_event(
        "upstream_request_timeout",
        "Proxy request budget exhausted",
        response_id=request_id,
    )


def _security_work_advisory_event(
    *,
    code: str,
    message: str,
    request_id: str | None,
    action: str,
    account_id: str | None = None,
) -> dict[str, JsonValue]:
    warning: dict[str, JsonValue] = {
        "code": code,
        "message": message,
        "category": "security_work_authorization",
        "action": action,
    }
    if request_id:
        warning["request_id"] = request_id
    if account_id:
        warning["account_id"] = account_id
    return {
        "type": "codex_lb.warning",
        "warning": warning,
    }


def _is_security_work_authorization_required_error(code: str | None, message: str | None) -> bool:
    normalized_code = (code or "").strip().lower()
    if normalized_code == _SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE:
        return True
    normalized_message = (message or "").strip().lower()
    if not normalized_message:
        return False
    return all(hint in normalized_message for hint in _SECURITY_WORK_AUTHORIZATION_REQUIRED_HINTS)


def _raise_proxy_budget_exhausted() -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_request_timeout", "Proxy request budget exhausted"),
    )


def _raise_proxy_unavailable(message: str) -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_unavailable", message),
    )


_FAILED_ACCOUNT_ATTR = "_codex_lb_failed_account"


def _raise_proxy_unavailable_for_account(message: str, account: Account) -> NoReturn:
    exc = ProxyResponseError(
        502,
        openai_error("upstream_unavailable", message),
    )
    setattr(exc, _FAILED_ACCOUNT_ATTR, account)
    raise exc


def _proxy_response_failed_account(exc: ProxyResponseError, fallback: Account) -> Account:
    account = getattr(exc, _FAILED_ACCOUNT_ATTR, None)
    return account if isinstance(account, Account) else fallback


def _refresh_error_failed_account(exc: RefreshError, fallback: Account) -> Account:
    account = getattr(exc, _FAILED_ACCOUNT_ATTR, None)
    return account if isinstance(account, Account) else fallback


def _should_failover_previsible_unary_proxy_error(exc: ProxyResponseError) -> bool:
    if exc.failure_phase != "connect":
        return False
    error = _parse_openai_error(exc.payload)
    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
    error_message = error.message if error else None
    return error_code == "upstream_unavailable" and _should_retry_transient_stream_error(
        "upstream_unavailable",
        error_message,
    )


def _is_proxy_budget_exhausted_error(exc: ProxyResponseError) -> bool:
    error = _parse_openai_error(exc.payload)
    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
    error_message = error.message if error else None
    return error_code in {"upstream_request_timeout", "upstream_unavailable"} and (
        error_message == "Proxy request budget exhausted"
    )


def _should_suppress_text_done_event(
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    suppress_text_done_events: bool,
    saw_text_delta: bool,
) -> bool:
    if not suppress_text_done_events or not saw_text_delta or event_type is None:
        return False
    if event_type == "response.output_text.done":
        return True
    if event_type == "response.content_part.done":
        return _is_text_content_part(payload)
    return False


def _is_text_content_part(payload: dict[str, JsonValue] | None) -> bool:
    if payload is None:
        return False
    part = payload.get("part")
    if not isinstance(part, dict):
        return False
    part_type = part.get("type")
    return isinstance(part_type, str) and part_type in _TEXT_DONE_CONTENT_PART_TYPES


def _input_prefix_matches_stored_context(
    input_value: JsonValue,
    *,
    stored_count: int,
    stored_fingerprint: str | None,
) -> bool:
    if stored_count <= 0 or stored_fingerprint is None:
        return False
    if not isinstance(input_value, list):
        return False
    if len(input_value) <= stored_count:
        return False
    return _fingerprint_input_items(cast(list[JsonValue], input_value)[:stored_count]) == stored_fingerprint


def _is_missing_thread_goal_protocol_error(exc: ProxyResponseError) -> bool:
    if exc.status_code not in {404, 405}:
        return False
    error = _parse_openai_error(exc.payload)
    code = _normalize_error_code(
        error.code if error else None,
        error.type if error else None,
    )
    message = (error.message if error and error.message else "").strip().lower()
    if exc.status_code == 404:
        return code == "not_found" and message == "not found"
    return code == "method_not_allowed" and message == "method not allowed"


def _detached_account_copy(account: Account) -> Account:
    data = {column.name: getattr(account, column.name) for column in Account.__table__.columns}
    return Account(**data)


def _headers_with_turn_state(headers: Mapping[str, str], turn_state: str | None) -> dict[str, str]:
    forwarded = dict(headers)
    if turn_state:
        forwarded["x-codex-turn-state"] = turn_state
    return forwarded


def _record_same_account_takeover(*, preferred_account_id: str | None, selected_account_id: str | None) -> None:
    if not PROMETHEUS_AVAILABLE or bridge_same_account_takeover_total is None or preferred_account_id is None:
        return
    if selected_account_id is None:
        bridge_same_account_takeover_total.labels(outcome="fail").inc()
    elif selected_account_id == preferred_account_id:
        bridge_same_account_takeover_total.labels(outcome="success").inc()
    else:
        bridge_same_account_takeover_total.labels(outcome="fallback").inc()


def _previous_response_owner_lookup_failed_error_envelope() -> OpenAIErrorEnvelope:
    return openai_error(
        "upstream_unavailable",
        "Previous response owner lookup failed; retry later.",
        error_type="server_error",
    )


def _mark_request_state_previous_response_not_found(
    request_state: _WebSocketRequestState,
    detail: str,
) -> None:
    previous_response_id = request_state.previous_response_id
    if previous_response_id is None:
        return
    payload = _http_bridge_previous_response_error_envelope(previous_response_id, detail)
    error = payload["error"]
    request_state.error_code_override = error.get("code")
    request_state.error_message_override = error.get("message")
    request_state.error_type_override = error.get("type")
    request_state.error_param_override = error.get("param")


def _header_value_case_insensitive(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _headers_with_authorization(headers: Mapping[str, str], authorization: str | None) -> dict[str, str]:
    merged = dict(headers)
    if authorization is None:
        return merged
    if _header_value_case_insensitive(merged, "authorization") is not None:
        return merged
    merged["Authorization"] = authorization
    return merged


def _service_tier_from_response(
    response: OpenAIResponsePayload | CompactResponsePayload | None,
) -> str | None:
    if response is None:
        return None
    extra = response.model_extra
    if not isinstance(extra, Mapping):
        return None
    return _normalize_service_tier_value(extra.get("service_tier"))


def _service_tier_from_event_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    return _normalize_service_tier_value(response.get("service_tier"))


def _effective_service_tier(requested_service_tier: str | None, actual_service_tier: str | None) -> str | None:
    if isinstance(actual_service_tier, str):
        return actual_service_tier
    if isinstance(requested_service_tier, str):
        return requested_service_tier
    return None


def _normalize_service_tier_value(value: JsonValue) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() == "fast":
        return "priority"
    return stripped
