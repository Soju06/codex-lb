from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
import math
import sys
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, AsyncIterator, Literal, Mapping, NoReturn, TypeVar, cast, overload
from uuid import uuid4

import aiohttp
import anyio

from app.core import shutdown as shutdown_state
from app.core.auth.refresh import (
    RefreshError,
)
from app.core.balancer import (
    ResetPreferenceWindow,
    RoutingStrategy,
)
from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.http import lease_http_session
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
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.clients.proxy import thread_goal_request as core_thread_goal_request
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio  # noqa: F401
from app.core.config.settings import Settings, get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import (
    openai_error,
    response_failed_event,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_forward_latency_seconds,
    bridge_instance_mismatch_total,
    bridge_local_rebind_total,
    bridge_owner_forward_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.overload import is_local_overload_error_code, local_overload_error
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME, format_sse_event, parse_sse_data_json
from app.db.models import (
    Account,
    AccountStatus,
    DashboardSettings,
    StickySessionKind,
)
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.proxy._service.api_key_usage import (
    _API_KEY_RESERVATION_HEARTBEAT_SECONDS as _API_KEY_RESERVATION_HEARTBEAT_SECONDS,
)
from app.modules.proxy._service.compact import (
    _sticky_key_for_compact_request as _sticky_key_for_compact_request,
)
from app.modules.proxy._service.compact import (
    _sticky_key_from_compact_payload as _sticky_key_from_compact_payload,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _active_http_bridge_instance_ring,
    _build_http_bridge_prewarm_text,
    _durable_bridge_lookup_active_owner,
    _durable_bridge_lookup_allows_local_reuse,
    _effective_http_bridge_idle_ttl_seconds,
    _forwarded_http_bridge_session_key,
    _http_bridge_allow_durable_takeover,
    _http_bridge_can_local_recover_without_ring,
    _http_bridge_can_recover_during_drain,
    _http_bridge_can_single_instance_owner_takeover_without_anchor,
    _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor,
    _http_bridge_continuity_lost_error_envelope,
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_endpoint_matches_current_instance,
    _http_bridge_eviction_priority,
    _http_bridge_has_durable_recovery_anchor,
    _http_bridge_is_context_overflow_error,
    _http_bridge_is_previous_response_owner_unavailable,
    _http_bridge_key_strength,
    _http_bridge_owner_check_required,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_payload_looks_like_full_resend,
    _http_bridge_payload_without_previous_response_id,
    _http_bridge_precreated_retry_failure_error,
    _http_bridge_previous_response_alias_key,
    _http_bridge_request_counts_against_queue,
    _http_bridge_request_stage,
    _http_bridge_runtime_config,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_should_attempt_local_bootstrap_rebind,
    _http_bridge_should_attempt_local_previous_response_recovery,
    _http_bridge_should_attempt_soft_affinity_reroute,
    _http_bridge_should_rollover_after_context_overflow,
    _http_bridge_should_wait_for_registration,
    _http_bridge_startup_wait_timeout_error,
    _http_bridge_turn_state_alias_key,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _log_http_bridge_startup_wait_timeout,
    _make_http_bridge_session_key,
    _normalize_http_bridge_error_event,
    _normalized_http_bridge_instance_ring,
    _preferred_http_bridge_reconnect_turn_state,
    _record_bridge_drain_recovery_allowed,
    _record_bridge_first_turn_timeout,
    _record_bridge_reattach,
    _trim_http_bridge_previous_response_input_items,
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
    _tools_hash as _tools_hash,
)
from app.modules.proxy._service.observability import (
    _truncate_identifier as _truncate_identifier,
)
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_request_error_overrides,
    _copy_websocket_route_metadata_from_session,
    _copy_websocket_route_metadata_to_session,
    _event_type_from_payload,
    _HTTPBridgeOwnerForward,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _record_response_event,
    _request_log_useragent_fields,
    _RequestLogFailureMetadata,
    _websocket_request_can_replay_before_visible_output,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
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
    _AffinityPolicy,
    _extract_model_class,
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
    _parse_openai_error,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext,
    OwnerForwardRelayFailure,
)
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.tool_call_dedupe import (
    dedupe_replayed_side_effect_input_items,
    mark_duplicate_tool_call_downstream_event,
    rewrite_parallel_tool_call_text,
)
from app.modules.proxy.tool_call_dedupe import (
    response_id_from_payload as tool_call_response_id_from_payload,
)

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_REQUEST_TRANSPORT_HTTP = "http"
_UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY = frozenset({1011})
_WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE = "account_auth_invalidated"
_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"
_SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "codex-lb is retrying on an account marked as authorized for security work."
)
_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. codex-lb is continuing with normal account selection; the upstream request may still fail until "
    "an account with Trusted Access for Cyber is marked as security-work-authorized."
)
_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS = 0.5
_PREWARM_RESPONSE_TIMEOUT_SECONDS = 2.0
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100
_STREAM_KEEPALIVE_MAX_COUNT = 6
_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = get_settings().upstream_response_create_max_bytes


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


def _service_lease_http_session() -> Any:
    return _service_global_or("lease_http_session", lease_http_session)


def _service_as_image_fetch_session() -> Any:
    return _service_global_or("_as_image_fetch_session", _as_image_fetch_session)


def _service_inline_input_image_urls() -> Any:
    return _service_global_or("_inline_input_image_urls", _inline_input_image_urls)


def _stream_keepalive_max_count() -> int:
    return int(_service_global_or("_STREAM_KEEPALIVE_MAX_COUNT", _STREAM_KEEPALIVE_MAX_COUNT))


def _prewarm_response_timeout_seconds() -> float:
    return float(_service_global_or("_PREWARM_RESPONSE_TIMEOUT_SECONDS", _PREWARM_RESPONSE_TIMEOUT_SECONDS))


def _codex_keepalive_frame() -> str:
    return str(_service_global_or("CODEX_KEEPALIVE_FRAME", CODEX_KEEPALIVE_FRAME))


def _upstream_response_create_max_bytes() -> int:
    return int(_service_global_or("_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", _UPSTREAM_RESPONSE_CREATE_MAX_BYTES))


def _http_bridge_startup_keepalive_grace_seconds() -> float:
    return float(
        _service_global_or(
            "_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS",
            _HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS,
        )
    )


def _service_core_stream_responses() -> Any:
    return _service_global_or("core_stream_responses", core_stream_responses)


def _service_core_thread_goal_request() -> Any:
    return _service_global_or("core_thread_goal_request", core_thread_goal_request)


def _service_push_stream_timeout_overrides(**kwargs: float) -> object:
    return _service_global_or("push_stream_timeout_overrides", push_stream_timeout_overrides)(**kwargs)


def _service_pop_stream_timeout_overrides(token: object) -> None:
    _service_global_or("pop_stream_timeout_overrides", pop_stream_timeout_overrides)(cast(Any, token))


def _remaining_budget_seconds(deadline: float) -> float:
    return cast(Callable[[float], float], _service_global("_remaining_budget_seconds"))(deadline)


def _request_log_failure_metadata(exc: ProxyResponseError) -> _RequestLogFailureMetadata:
    return cast(
        Callable[[ProxyResponseError], _RequestLogFailureMetadata],
        _service_global("_request_log_failure_metadata"),
    )(exc)


def _prefer_earlier_reset_window(settings: DashboardSettings) -> ResetPreferenceWindow:
    return cast(
        Callable[[DashboardSettings], ResetPreferenceWindow],
        _service_global("_prefer_earlier_reset_window"),
    )(settings)


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    return cast(Callable[[DashboardSettings], RoutingStrategy], _service_global("_routing_strategy"))(settings)


def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    *args: object,
    optional_kwargs: Mapping[str, object],
    **required_kwargs: object,
) -> Awaitable[Any]:
    return cast(
        Callable[..., Awaitable[Any]],
        _service_global("_call_with_supported_optional_kwargs"),
    )(func, *args, optional_kwargs=optional_kwargs, **required_kwargs)


def _raise_proxy_budget_exhausted() -> NoReturn:
    cast(Callable[[], NoReturn], _service_global("_raise_proxy_budget_exhausted"))()


def _raise_proxy_unavailable(message: str) -> NoReturn:
    cast(Callable[[str], NoReturn], _service_global("_raise_proxy_unavailable"))(message)


def _proxy_response_failed_account(exc: ProxyResponseError, fallback: Account) -> Account:
    return cast(
        Callable[[ProxyResponseError, Account], Account],
        _service_global("_proxy_response_failed_account"),
    )(exc, fallback)


def _refresh_error_failed_account(exc: RefreshError, fallback: Account) -> Account:
    return cast(
        Callable[[RefreshError, Account], Account],
        _service_global("_refresh_error_failed_account"),
    )(exc, fallback)


def _normalize_responses_request_payload_for_bridge(payload: ResponsesRequest) -> ResponsesRequest:
    return cast(
        Callable[[ResponsesRequest], ResponsesRequest],
        _service_global("_normalize_responses_request_payload_for_bridge"),
    )(payload)


def _proxy_admission_wait_timeout_seconds(settings: Any | None = None) -> float:
    return cast(Callable[[Any | None], float], _service_global("_proxy_admission_wait_timeout_seconds"))(settings)


def _maybe_log_proxy_request_payload(kind: str, payload: ResponsesRequest, headers: Mapping[str, str]) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_request_payload"))(kind, payload, headers)


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    **kwargs: object,
) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_request_shape"))(kind, payload, headers, **kwargs)


def _maybe_log_proxy_service_tier_trace(
    kind: str,
    *,
    requested_service_tier: str | None,
    actual_service_tier: str | None,
) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_service_tier_trace"))(
        kind, requested_service_tier=requested_service_tier, actual_service_tier=actual_service_tier
    )


def _summarize_input(input_value: JsonValue | None) -> dict[str, JsonValue] | None:
    return cast(
        Callable[[JsonValue | None], dict[str, JsonValue] | None],
        _service_global("_summarize_input"),
    )(input_value)


def _record_continuity_owner_resolution(**kwargs: object) -> None:
    cast(Callable[..., None], _service_global("_record_continuity_owner_resolution"))(**kwargs)


def _record_continuity_fail_closed(**kwargs: object) -> None:
    cast(Callable[..., None], _service_global("_record_continuity_fail_closed"))(**kwargs)


def _service_tier_from_compact_payload(payload: Any) -> str | None:
    return cast(Callable[[Any], str | None], _service_global("_service_tier_from_compact_payload"))(payload)


def _header_value_case_insensitive(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_header_value_case_insensitive")(*args, **kwargs)


def _responses_request_contains_input_image(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_responses_request_contains_input_image")(*args, **kwargs)


def _responses_request_uses_image_generation(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_responses_request_uses_image_generation")(*args, **kwargs)


def _input_prefix_matches_stored_context(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_input_prefix_matches_stored_context")(*args, **kwargs)


def _fingerprint_input_items(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_fingerprint_input_items")(*args, **kwargs)


def _normalize_session_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_normalize_session_id")(*args, **kwargs)


def _partial_output_proxy_error_event_block(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_partial_output_proxy_error_event_block")(*args, **kwargs)


def _normalize_service_tier_value(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_normalize_service_tier_value")(*args, **kwargs)


def _websocket_downstream_response_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_downstream_response_id")(*args, **kwargs)


def _is_previous_response_not_found_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_is_previous_response_not_found_error")(*args, **kwargs)


def _websocket_event_error_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_event_error_code")(*args, **kwargs)


def _websocket_event_error_type(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_event_error_type")(*args, **kwargs)


def _websocket_event_error_param(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_event_error_param")(*args, **kwargs)


def _websocket_event_error_message(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_event_error_message")(*args, **kwargs)


def _build_rewritten_stream_response_failed_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_build_rewritten_stream_response_failed_event")(*args, **kwargs)


def _openai_error_envelope_from_response_failed_payload(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_openai_error_envelope_from_response_failed_payload")(*args, **kwargs)


def _headers_with_turn_state(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_headers_with_turn_state")(*args, **kwargs)


def _headers_with_authorization(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_headers_with_authorization")(*args, **kwargs)


def _response_create_client_metadata(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_response_create_client_metadata")(*args, **kwargs)


def _count_external_image_urls(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_count_external_image_urls")(*args, **kwargs)


def _inline_top_level_input_image_urls(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_inline_top_level_input_image_urls")(*args, **kwargs)


def _slim_response_create_payload_for_upstream(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_slim_response_create_payload_for_upstream")(*args, **kwargs)


def _enforce_response_create_size_limit(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_enforce_response_create_size_limit")(*args, **kwargs)


def _estimated_lease_tokens_from_request_usage_budget(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_estimated_lease_tokens_from_request_usage_budget")(*args, **kwargs)


def _websocket_response_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_response_id")(*args, **kwargs)


def _websocket_connect_deadline(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_connect_deadline")(*args, **kwargs)


def _upstream_turn_state_from_socket(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_upstream_turn_state_from_socket")(*args, **kwargs)


def _record_same_account_takeover(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_record_same_account_takeover")(*args, **kwargs)


def _prepare_websocket_request_state_for_visible_output_replay(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_prepare_websocket_request_state_for_visible_output_replay")(*args, **kwargs)


def _prepare_websocket_request_state_for_auth_replay(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_prepare_websocket_request_state_for_auth_replay")(*args, **kwargs)


def _classify_upstream_close(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_classify_upstream_close")(*args, **kwargs)


def _websocket_auth_failure_permanent_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_auth_failure_permanent_code")(*args, **kwargs)


def _websocket_auth_failure_requires_reauth(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_auth_failure_requires_reauth")(*args, **kwargs)


def _is_local_account_cap_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_is_local_account_cap_code")(*args, **kwargs)


def _upstream_websocket_disconnect_message(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_upstream_websocket_disconnect_message")(*args, **kwargs)


def _await_cancelled_task(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_await_cancelled_task")(*args, **kwargs)


def _is_missing_tool_output_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_is_missing_tool_output_error")(*args, **kwargs)


def _previous_response_id_from_not_found_message(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_previous_response_id_from_not_found_message")(*args, **kwargs)


def _assign_websocket_response_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_assign_websocket_response_id")(*args, **kwargs)


def _find_websocket_request_state_by_response_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_find_websocket_request_state_by_response_id")(*args, **kwargs)


def _match_websocket_request_state_for_anonymous_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_match_websocket_request_state_for_anonymous_event")(*args, **kwargs)


def _service_tier_from_event_payload(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_service_tier_from_event_payload")(*args, **kwargs)


def _response_output_item_done_function_call_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_response_output_item_done_function_call_id")(*args, **kwargs)


def _rewrite_websocket_downstream_response_id(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_rewrite_websocket_downstream_response_id")(*args, **kwargs)


def _pop_terminal_websocket_request_state(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_pop_terminal_websocket_request_state")(*args, **kwargs)


def _pop_matching_websocket_request_states(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_pop_matching_websocket_request_states")(*args, **kwargs)


def _matching_websocket_request_states_for_previous_response_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_matching_websocket_request_states_for_previous_response_error")(*args, **kwargs)


def _matching_websocket_request_states_for_missing_tool_output_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_matching_websocket_request_states_for_missing_tool_output_error")(*args, **kwargs)


def _build_stream_incomplete_terminal_event_for_request(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_build_stream_incomplete_terminal_event_for_request")(*args, **kwargs)


def _rewrite_websocket_suppressed_duplicate_tool_call_completion_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_rewrite_websocket_suppressed_duplicate_tool_call_completion_event")(*args, **kwargs)


def _rewrite_websocket_continuity_corruption_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_rewrite_websocket_continuity_corruption_event")(*args, **kwargs)


def _maybe_rewrite_websocket_previous_response_not_found_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_maybe_rewrite_websocket_previous_response_not_found_event")(*args, **kwargs)


def _websocket_precreated_retry_error_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_precreated_retry_error_code")(*args, **kwargs)


def _websocket_precreated_auth_error_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_precreated_auth_error_code")(*args, **kwargs)


def _websocket_owner_pinned_quota_error_code(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_websocket_owner_pinned_quota_error_code")(*args, **kwargs)


def _rewrite_websocket_previous_response_owner_unavailable_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_rewrite_websocket_previous_response_owner_unavailable_event")(*args, **kwargs)


def _http_error_status_from_payload(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_http_error_status_from_payload")(*args, **kwargs)


def _release_websocket_response_create_gate(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_release_websocket_response_create_gate")(*args, **kwargs)


def _is_security_work_authorization_required_error(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_is_security_work_authorization_required_error")(*args, **kwargs)


def _security_work_advisory_event(*args: Any, **kwargs: Any) -> Any:
    return _service_global("_security_work_advisory_event")(*args, **kwargs)


class _HTTPBridgeMixin(_HTTPBridgeServiceProtocol):
    def stream_http_responses(
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
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream_http", payload, headers)
        proxy_api_authorization = _header_value_case_insensitive(headers, "authorization")
        filtered = filter_inbound_headers(headers)
        return self._stream_http_bridge_or_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            proxy_api_authorization=proxy_api_authorization,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )

    async def _stream_http_bridge_or_retry(
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
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
    ) -> AsyncIterator[str]:
        dashboard_settings = await _service_get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, _service_get_settings())
        request_id = ensure_request_id()
        self._raise_for_unsupported_input_image_references(payload)
        payload_size_estimate_bytes = len(
            json.dumps(payload.to_payload(), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )
        rewritten_file_account_id = await self._resolve_file_account_for_responses(payload, headers)
        ws_payload_budget_bytes = _ws_transport_payload_budget_bytes(_service_get_settings())
        if runtime_config.enabled and payload_size_estimate_bytes > ws_payload_budget_bytes:
            logger.info(
                "stream_responses bypassing http bridge for large payload size=%s budget=%s request_id=%s",
                payload_size_estimate_bytes,
                ws_payload_budget_bytes,
                request_id,
            )
            runtime_config = dataclasses.replace(runtime_config, enabled=False)
        image_request = _responses_request_contains_input_image(payload)
        image_generation_request = _responses_request_uses_image_generation(payload)
        force_upstream_stream_transport = "http" if image_request else None
        if runtime_config.enabled and (image_request or image_generation_request):
            logger.info(
                "stream_responses bypassing http bridge for image-capable request input_image=%s "
                "image_generation=%s request_id=%s",
                image_request,
                image_generation_request,
                request_id,
            )
            runtime_config = dataclasses.replace(runtime_config, enabled=False)
        if not runtime_config.enabled:
            stream_with_retry = cast(Callable[..., AsyncIterator[str]], cast(Any, self)._stream_with_retry)
            async for line in stream_with_retry(
                payload,
                headers,
                codex_session_affinity=codex_session_affinity,
                propagate_http_errors=propagate_http_errors,
                openai_cache_affinity=openai_cache_affinity,
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                suppress_text_done_events=suppress_text_done_events,
                request_transport=_REQUEST_TRANSPORT_HTTP,
                rewritten_file_account_id=rewritten_file_account_id,
                upstream_stream_transport_override=force_upstream_stream_transport,
            ):
                yield line
            return

        async for line in self._stream_via_http_bridge(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            idle_ttl_seconds=runtime_config.idle_ttl_seconds,
            codex_idle_ttl_seconds=runtime_config.codex_idle_ttl_seconds,
            max_sessions=runtime_config.max_sessions,
            queue_limit=runtime_config.queue_limit,
            prompt_cache_idle_ttl_seconds=runtime_config.prompt_cache_idle_ttl_seconds,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            proxy_api_authorization=proxy_api_authorization,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            rewritten_file_account_id=rewritten_file_account_id,
        ):
            yield line

    async def _stream_via_http_bridge(
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
        idle_ttl_seconds: float,
        codex_idle_ttl_seconds: float,
        max_sessions: int,
        queue_limit: int,
        prompt_cache_idle_ttl_seconds: float | None = None,
        downstream_turn_state: str | None = None,
        forwarded_request: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        rewritten_file_account_id: str | None = None,
    ) -> AsyncIterator[str]:
        del suppress_text_done_events
        request_id = ensure_request_id()
        dashboard_settings = await _service_get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, _service_get_settings())
        incoming_turn_state_header = _sticky_key_from_turn_state_header(headers) if not forwarded_request else None
        incoming_session_header = _sticky_key_from_session_header(headers) if not forwarded_request else None
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=dashboard_settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=dashboard_settings.sticky_threads_enabled,
            api_key=api_key,
        )
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = (
                "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
            )
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream_http_bridge",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )

        bridge_session_key = _make_http_bridge_session_key(
            payload,
            headers=headers,
            affinity=affinity,
            api_key=api_key,
            request_id=request_id,
            allow_forwarded_affinity_headers=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )
        try:
            durable_lookup = await self._durable_bridge.lookup_request_targets(
                session_key_kind=bridge_session_key.affinity_kind,
                session_key_value=bridge_session_key.affinity_key,
                api_key_id=bridge_session_key.api_key_id,
                turn_state=incoming_turn_state_header,
                session_header=incoming_session_header,
                previous_response_id=payload.previous_response_id,
            )
        except Exception:
            logger.warning("Durable bridge lookup failed; falling back to non-durable request handling", exc_info=True)
            durable_lookup = None
        effective_payload = payload
        untrimmed_effective_payload = payload
        proxy_injected_previous_response_id = False
        fresh_upstream_request_text: str | None = None
        previous_response_trimmed_input_count: int | None = None
        previous_response_trimmed_input_fingerprint: str | None = None
        durable_full_resend_anchor_count: int | None = None
        durable_full_resend_anchor_fingerprint: str | None = None
        if durable_lookup is not None:
            bridge_session_key = _HTTPBridgeSessionKey(
                durable_lookup.canonical_kind,
                durable_lookup.canonical_key,
                bridge_session_key.api_key_id,
            )
            live_local_session_exists = await self._http_bridge_has_live_local_session(
                key=bridge_session_key,
                incoming_turn_state=incoming_turn_state_header,
                api_key=api_key,
            )
            forwards_to_active_owner = await self._http_bridge_can_forward_to_active_owner(durable_lookup)
            durable_anchor_trimmable = _input_prefix_matches_stored_context(
                payload.input,
                stored_count=durable_lookup.latest_input_item_count or 0,
                stored_fingerprint=durable_lookup.latest_input_full_fingerprint,
            )
            if (
                not live_local_session_exists
                and not forwards_to_active_owner
                and payload.previous_response_id is None
                and bridge_session_key.strength == "hard"
                and durable_lookup.latest_response_id is not None
                and (not _http_bridge_payload_looks_like_full_resend(payload) or durable_anchor_trimmable)
            ):
                effective_payload = payload.model_copy(
                    update={"previous_response_id": durable_lookup.latest_response_id}
                )
                proxy_injected_previous_response_id = True
                _fresh_request_state, fresh_upstream_request_text = self._prepare_http_bridge_request(
                    payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                del _fresh_request_state
                _log_http_bridge_event(
                    "fresh_reattach_anchor_injected",
                    bridge_session_key,
                    account_id=None,
                    model=payload.model,
                    detail=f"response_id={durable_lookup.latest_response_id}",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(payload.model) if payload.model else None,
                )
                if _http_bridge_payload_looks_like_full_resend(payload):
                    durable_full_resend_anchor_count = durable_lookup.latest_input_item_count
                    durable_full_resend_anchor_fingerprint = durable_lookup.latest_input_full_fingerprint
                    _log_http_bridge_event(
                        "durable_full_resend_anchor_injected",
                        bridge_session_key,
                        account_id=None,
                        model=payload.model,
                        detail=(
                            f"response_id={durable_lookup.latest_response_id} "
                            f"stored_items={durable_full_resend_anchor_count}"
                        ),
                        cache_key_family=bridge_session_key.affinity_kind,
                        model_class=_extract_model_class(payload.model) if payload.model else None,
                    )
        if effective_payload.previous_response_id is not None and isinstance(effective_payload.input, list):
            previous_response_input_items = cast(list[JsonValue], effective_payload.input)
            trimmed_input_items = _trim_http_bridge_previous_response_input_items(previous_response_input_items)
            if len(trimmed_input_items) != len(previous_response_input_items):
                previous_response_trimmed_input_count = len(previous_response_input_items)
                previous_response_trimmed_input_fingerprint = _fingerprint_input_items(previous_response_input_items)
                effective_payload = effective_payload.model_copy(update={"input": trimmed_input_items})
        request_state, text_data = self._prepare_http_bridge_request(
            effective_payload,
            headers,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            request_id=request_id,
        )
        if downstream_turn_state is not None:
            request_state.session_id = _normalize_session_id(downstream_turn_state)
        if previous_response_trimmed_input_count is not None:
            request_state.input_item_count = previous_response_trimmed_input_count
            request_state.input_full_fingerprint = previous_response_trimmed_input_fingerprint
            logger.info(
                "http_bridge_previous_response_input_trimmed request_id=%s original_items=%s trimmed_to=%s "
                "previous_response_id=%s",
                request_state.request_id,
                previous_response_trimmed_input_count,
                len(cast(list[JsonValue], effective_payload.input))
                if isinstance(effective_payload.input, list)
                else None,
                effective_payload.previous_response_id,
            )
        request_state.transport = _REQUEST_TRANSPORT_HTTP
        request_state.request_stage = _http_bridge_request_stage(
            headers=headers,
            payload=effective_payload,
            durable_lookup=durable_lookup,
        )
        request_state.preferred_account_id = (
            durable_lookup.account_id
            if (
                durable_lookup is not None
                and (
                    request_state.previous_response_id is not None
                    or bridge_session_key.strength == "hard"
                    or (
                        bridge_session_key.affinity_kind == "prompt_cache"
                        and request_state.request_stage == "follow_up"
                        and durable_lookup.latest_turn_state is not None
                    )
                )
            )
            else request_state.preferred_account_id
        )
        if request_state.previous_response_id is not None and request_state.preferred_account_id is None:
            request_state.preferred_account_id = await self._http_bridge_local_owner_account_id(
                key=bridge_session_key,
                incoming_turn_state=incoming_turn_state_header,
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
            )
        if request_state.previous_response_id is not None and request_state.preferred_account_id is None:
            request_state.preferred_account_id = await self._resolve_websocket_previous_response_owner(
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
                session_id=request_state.session_id,
                surface="http_bridge",
            )
        file_required_preferred_account = False
        if request_state.preferred_account_id is None:
            # ``input_file.file_id`` references must land on the account
            # that registered the upload (chatgpt-account-id-scoped).
            # The helper returns ``None`` when stronger affinity signals
            # are present, so this never overrides existing routing.
            if rewritten_file_account_id is not None:
                request_state.preferred_account_id = rewritten_file_account_id
                file_required_preferred_account = True
        if request_state.preferred_account_id is None:
            resolved_file_account_id = await self._resolve_file_account_for_responses(effective_payload, headers)
            if resolved_file_account_id is not None:
                request_state.preferred_account_id = resolved_file_account_id
                file_required_preferred_account = True
        if proxy_injected_previous_response_id:
            request_state.proxy_injected_previous_response_id = True
            request_state.fresh_upstream_request_text = fresh_upstream_request_text or text_data
            # Durable-anchor injection actually runs when the incoming
            # payload is *not* a full resend (see the
            # ``not _http_bridge_payload_looks_like_full_resend(payload)``
            # guard above), so the captured unanchored text is typically
            # just a short follow-up. Replaying it as a fresh turn would
            # drop the conversational context the anchor was pointing at.
            # Only the trim branch below (which verifies the stored prefix
            # fingerprint) is allowed to flip this flag to ``True``.
            request_state.fresh_upstream_request_is_retry_safe = False
        try:
            session_or_forward = await self._get_or_create_http_bridge_session(
                bridge_session_key,
                headers=dict(headers),
                affinity=affinity,
                api_key=api_key,
                request_model=effective_payload.model,
                idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                    affinity=affinity,
                    idle_ttl_seconds=idle_ttl_seconds,
                    codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                    prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                ),
                max_sessions=max_sessions,
                previous_response_id=request_state.previous_response_id,
                gateway_safe_mode=runtime_config.gateway_safe_mode,
                allow_forward_to_owner=True,
                forwarded_request=forwarded_request,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
                durable_lookup=durable_lookup,
                request_stage=request_state.request_stage,
                preferred_account_id=request_state.preferred_account_id,
                fallback_on_preferred_account_unavailable=not file_required_preferred_account,
                request_usage_budget=request_state.request_usage_budget,
            )
        except ProxyResponseError as exc:
            if not (
                _http_bridge_is_previous_response_owner_unavailable(exc)
                and proxy_injected_previous_response_id
                and fresh_upstream_request_text is not None
                and durable_full_resend_anchor_count is not None
                and durable_full_resend_anchor_fingerprint is not None
            ):
                raise
            _log_http_bridge_event(
                "owner_unavailable_fresh_resend",
                bridge_session_key,
                account_id=request_state.preferred_account_id,
                model=payload.model,
                detail="outcome=fresh_full_resend_without_anchor",
                cache_key_family=bridge_session_key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
            )
            request_state, text_data = self._prepare_http_bridge_request(
                payload,
                headers,
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                request_id=request_id,
            )
            if downstream_turn_state is not None:
                request_state.session_id = _normalize_session_id(downstream_turn_state)
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=payload,
                durable_lookup=None,
            )
            file_required_preferred_account = False
            if rewritten_file_account_id is not None:
                request_state.preferred_account_id = rewritten_file_account_id
                file_required_preferred_account = True
            if request_state.preferred_account_id is None:
                resolved_file_account_id = await self._resolve_file_account_for_responses(payload, headers)
                if resolved_file_account_id is not None:
                    request_state.preferred_account_id = resolved_file_account_id
                    file_required_preferred_account = True
            effective_payload = payload
            untrimmed_effective_payload = payload
            proxy_injected_previous_response_id = False
            previous_response_trimmed_input_count = None
            previous_response_trimmed_input_fingerprint = None
            durable_full_resend_anchor_count = None
            durable_full_resend_anchor_fingerprint = None
            session_or_forward = await self._get_or_create_http_bridge_session(
                bridge_session_key,
                headers=dict(headers),
                affinity=affinity,
                api_key=api_key,
                request_model=payload.model,
                idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                    affinity=affinity,
                    idle_ttl_seconds=idle_ttl_seconds,
                    codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                    prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                ),
                max_sessions=max_sessions,
                previous_response_id=None,
                gateway_safe_mode=runtime_config.gateway_safe_mode,
                allow_forward_to_owner=True,
                forwarded_request=forwarded_request,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
                durable_lookup=None,
                request_stage=request_state.request_stage,
                preferred_account_id=request_state.preferred_account_id,
                fallback_on_preferred_account_unavailable=not file_required_preferred_account,
                request_usage_budget=request_state.request_usage_budget,
            )
        if isinstance(session_or_forward, _HTTPBridgeOwnerForward):
            forwarded_any = False
            try:
                async for line in self._forward_http_bridge_request_to_owner(
                    owner_forward=session_or_forward,
                    payload=effective_payload,
                    headers=headers,
                    api_key_reservation=api_key_reservation,
                    codex_session_affinity=codex_session_affinity,
                    downstream_turn_state=downstream_turn_state,
                    request_started_at=request_state.started_at,
                    proxy_api_authorization=proxy_api_authorization,
                ):
                    forwarded_any = True
                    yield line
                return
            except ProxyResponseError as exc:
                if forwarded_any:
                    yield _partial_output_proxy_error_event_block(
                        exc,
                        response_id=request_state.response_id or request_id,
                        previous_response_id=request_state.previous_response_id,
                        preferred_account_id=request_state.preferred_account_id,
                        default_code="bridge_owner_unreachable",
                        default_message="HTTP bridge owner request failed",
                    )
                    return
                should_attempt_previous_response_recovery = (
                    effective_payload.previous_response_id is not None
                    and _http_bridge_should_attempt_local_previous_response_recovery(exc)
                )
                should_attempt_bootstrap_rebind = _http_bridge_should_attempt_local_bootstrap_rebind(
                    exc,
                    key=bridge_session_key,
                    headers=headers,
                    previous_response_id=effective_payload.previous_response_id,
                )
                if not should_attempt_previous_response_recovery and not should_attempt_bootstrap_rebind:
                    raise
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(
                        path="owner_forward_fail"
                        if should_attempt_previous_response_recovery
                        else "owner_forward_bootstrap"
                    ).inc()
                _log_http_bridge_event(
                    "previous_response_recover_local"
                    if should_attempt_previous_response_recovery
                    else "bootstrap_rebind_local",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail=(
                        "outcome=local_rebind_after_forward_failure"
                        if should_attempt_previous_response_recovery
                        else "outcome=local_bootstrap_after_forward_failure"
                    ),
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                session = await self._get_or_create_http_bridge_session(
                    bridge_session_key,
                    headers=dict(headers),
                    affinity=affinity,
                    api_key=api_key,
                    request_model=effective_payload.model,
                    idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                        affinity=affinity,
                        idle_ttl_seconds=idle_ttl_seconds,
                        codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                        prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                    ),
                    max_sessions=max_sessions,
                    previous_response_id=request_state.previous_response_id,
                    gateway_safe_mode=runtime_config.gateway_safe_mode,
                    allow_forward_to_owner=False,
                    forwarded_request=False,
                    allow_previous_response_recovery_rebind=should_attempt_previous_response_recovery,
                    allow_bootstrap_owner_rebind=should_attempt_bootstrap_rebind,
                    durable_lookup=durable_lookup,
                    request_stage="reattach",
                    preferred_account_id=request_state.preferred_account_id,
                    request_usage_budget=request_state.request_usage_budget,
                )
                _record_bridge_reattach(
                    path="owner_forward_fail"
                    if should_attempt_previous_response_recovery
                    else "owner_forward_bootstrap",
                    outcome="success",
                )
                retry_request_state: _WebSocketRequestState | None = None
                try:
                    retry_api_key_reservation = api_key_reservation
                    retry_reservation_reacquired = False
                    if api_key is not None and api_key_reservation is not None:
                        retry_api_key_reservation = await self._reserve_websocket_api_key_usage(
                            api_key,
                            request_model=effective_payload.model,
                            request_service_tier=_normalize_service_tier_value(
                                dict(effective_payload.to_payload()).get("service_tier"),
                            ),
                            request_usage_budget=estimate_api_key_request_usage(effective_payload),
                        )
                        retry_reservation_reacquired = True

                    retry_request_state, retry_text_data = self._prepare_http_bridge_request(
                        effective_payload,
                        headers,
                        api_key=api_key,
                        api_key_reservation=retry_api_key_reservation,
                        request_id=request_id,
                    )
                    if downstream_turn_state is not None:
                        retry_request_state.session_id = _normalize_session_id(downstream_turn_state)
                    retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                    retry_request_state.request_stage = "reattach"
                    retry_request_state.preferred_account_id = request_state.preferred_account_id

                    await self._submit_http_bridge_request(
                        session,
                        request_state=retry_request_state,
                        text_data=retry_text_data,
                        queue_limit=queue_limit,
                    )
                    if downstream_turn_state is not None:
                        await self._register_http_bridge_turn_state(session, downstream_turn_state)
                    event_queue = retry_request_state.event_queue
                    assert event_queue is not None
                    while True:
                        event_block = await event_queue.get()
                        if event_block is None:
                            break
                        if retry_request_state.latency_first_token_ms is None:
                            block_payload = parse_sse_data_json(event_block)
                            block_event_type = _event_type_from_payload(None, block_payload)
                            if block_event_type in _TEXT_DELTA_EVENT_TYPES:
                                retry_request_state.latency_first_token_ms = int(
                                    (_service_time().monotonic() - retry_request_state.started_at) * 1000
                                )
                        yield event_block
                except BaseException:
                    if retry_reservation_reacquired and retry_api_key_reservation is not None:
                        await self._release_websocket_reservation(retry_api_key_reservation)
                    raise
                finally:
                    if retry_request_state is not None:
                        with anyio.CancelScope(shield=True):
                            await self._detach_http_bridge_request(session, request_state=retry_request_state)
                            session.last_used_at = _service_time().monotonic()
                return
        session = session_or_forward
        if (
            durable_full_resend_anchor_count is not None
            and durable_full_resend_anchor_fingerprint is not None
            and durable_lookup is not None
            and durable_lookup.latest_response_id is not None
        ):
            session.last_completed_response_id = durable_lookup.latest_response_id
            session.last_completed_input_count = durable_full_resend_anchor_count
            session.last_completed_input_prefix_fingerprint = durable_full_resend_anchor_fingerprint
        # --- Session-level previous_response_id injection ---
        # If the client didn't send previous_response_id and the durable
        # lookup didn't inject one, but this bridge session is carrying
        # Codex-style conversational continuity and has already completed a
        # request on this logical conversation, inject the session's last
        # completed response ID so the trim branch below can strip the
        # already-stored prefix.
        #
        # Correctness guards:
        # - Soft affinity reuse (for example prompt cache / sticky-thread
        #   sharing) must stay self-contained, so only true Codex
        #   continuity sessions opt in.
        # - Injecting an anchor when the incoming payload is a full-resend
        #   whose prefix cannot be safely trimmed (non-list input, prefix
        #   mismatch, or shorter-than-stored history) would send both the
        #   full history *and* the anchor upstream, which duplicates
        #   context and distorts output/cost. Gate injection so it only
        #   fires when the trim branch below would actually succeed.
        incoming_input_preview = effective_payload.input
        stored_count_preview = session.last_completed_input_count
        stored_fingerprint_preview = session.last_completed_input_prefix_fingerprint
        session_anchor_trimmable = _input_prefix_matches_stored_context(
            incoming_input_preview,
            stored_count=stored_count_preview,
            stored_fingerprint=stored_fingerprint_preview,
        )
        if (
            session.codex_session
            and not proxy_injected_previous_response_id
            and effective_payload.previous_response_id is None
            and session.last_completed_response_id is not None
            and session_anchor_trimmable
        ):
            fresh_upstream_request_text = text_data
            effective_payload = effective_payload.model_copy(
                update={"previous_response_id": session.last_completed_response_id}
            )
            proxy_injected_previous_response_id = True
            request_state, text_data = self._prepare_http_bridge_request(
                effective_payload,
                headers,
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                request_id=request_id,
            )
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=effective_payload,
                durable_lookup=durable_lookup,
            )
            request_state.preferred_account_id = durable_lookup.account_id if durable_lookup is not None else None
            request_state.proxy_injected_previous_response_id = True
            request_state.fresh_upstream_request_text = fresh_upstream_request_text
            # Session-level anchor injection may be attached to a payload
            # that relied on the anchor for context (for example a
            # single-item follow-up turn whose prior history is only
            # represented by ``previous_response_id``). Replaying without
            # the anchor would silently turn it into a fresh turn and drop
            # conversational context, so opt this path out of fresh-upstream
            # fresh-turn replay.
            request_state.fresh_upstream_request_is_retry_safe = False
            logger.info(
                "session_anchor_injected request_id=%s response_id=%s",
                request_id,
                session.last_completed_response_id,
            )
        # Trim already-stored prefix when previous_response_id anchors context.
        has_previous_response_id = (
            proxy_injected_previous_response_id or effective_payload.previous_response_id is not None
        )
        incoming_input = effective_payload.input
        stored_count = session.last_completed_input_count
        stored_fingerprint = session.last_completed_input_prefix_fingerprint
        if (
            has_previous_response_id
            and stored_count > 0
            and stored_fingerprint is not None
            and isinstance(incoming_input, list)
            and len(incoming_input) > stored_count
        ):
            incoming_input_list = cast(list[JsonValue], incoming_input)
            incoming_prefix_fingerprint = _fingerprint_input_items(incoming_input_list[:stored_count])
            if incoming_prefix_fingerprint == stored_fingerprint:
                original_count = len(incoming_input_list)
                trimmed_input = incoming_input_list[stored_count:]
                trimmed_payload = effective_payload.model_copy(update={"input": trimmed_input})
                previous_preferred_account_id = request_state.preferred_account_id
                request_state, text_data = self._prepare_http_bridge_request(
                    trimmed_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                if downstream_turn_state is not None:
                    request_state.session_id = _normalize_session_id(downstream_turn_state)
                request_state.transport = _REQUEST_TRANSPORT_HTTP
                request_state.request_stage = _http_bridge_request_stage(
                    headers=headers,
                    payload=trimmed_payload,
                    durable_lookup=durable_lookup,
                )
                request_state.preferred_account_id = previous_preferred_account_id
                request_state.input_item_count = original_count
                request_state.input_full_fingerprint = _fingerprint_input_items(incoming_input_list)
                if proxy_injected_previous_response_id:
                    request_state.proxy_injected_previous_response_id = True
                    request_state.fresh_upstream_request_text = fresh_upstream_request_text
                    # The trim branch only fires when the untrimmed payload
                    # is a true full resend whose prefix exactly matches the
                    # already-stored context, so the unanchored request text
                    # is a safe fresh-turn replay target regardless of
                    # whether the anchor came from the durable or
                    # session-level injection path.
                    request_state.fresh_upstream_request_is_retry_safe = True
                logger.info(
                    "store_context_input_trimmed request_id=%s original_items=%s trimmed_to=%s previous_response_id=%s",
                    request_id,
                    original_count,
                    len(trimmed_input),
                    effective_payload.previous_response_id,
                )
            else:
                logger.warning(
                    "store_context_input_trim_skipped_prefix_mismatch request_id=%s incoming_items=%s "
                    "stored_items=%s previous_response_id=%s",
                    request_id,
                    len(incoming_input_list),
                    stored_count,
                    effective_payload.previous_response_id,
                )
        session_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=queue_limit,
            propagate_http_errors=propagate_http_errors,
            downstream_turn_state=downstream_turn_state,
        )
        try:
            yielded_any = False
            async for event_block in session_events:
                yield event_block
                yielded_any = True
        except ProxyResponseError as exc:
            if yielded_any:
                yield _partial_output_proxy_error_event_block(
                    exc,
                    response_id=request_state.response_id or request_id,
                    previous_response_id=request_state.previous_response_id,
                    preferred_account_id=request_state.preferred_account_id,
                    default_code="upstream_error",
                    default_message="Upstream error",
                )
                return
            if (
                _http_bridge_should_attempt_soft_affinity_reroute(
                    exc,
                    key=bridge_session_key,
                    previous_response_id=effective_payload.previous_response_id,
                )
                and not file_required_preferred_account
            ):
                _log_http_bridge_event(
                    "internal_soft_affinity_reroute",
                    bridge_session_key,
                    account_id=session.account.id,
                    model=effective_payload.model,
                    detail="reason=bridge_local_pressure",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=False,
                )
                reroute_key = _HTTPBridgeSessionKey(
                    "internal_soft_affinity_reroute",
                    f"{bridge_session_key.affinity_kind}:{uuid4().hex}",
                    bridge_session_key.api_key_id,
                    strength="soft",
                )
                reroute_session = await self._get_or_create_http_bridge_session(
                    reroute_key,
                    headers=dict(headers),
                    affinity=_AffinityPolicy(),
                    api_key=api_key,
                    request_model=effective_payload.model,
                    idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                        affinity=_AffinityPolicy(),
                        idle_ttl_seconds=idle_ttl_seconds,
                        codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                        prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                    ),
                    max_sessions=max_sessions,
                    previous_response_id=None,
                    gateway_safe_mode=runtime_config.gateway_safe_mode,
                    allow_forward_to_owner=False,
                    forwarded_request=forwarded_request,
                    durable_lookup=None,
                    request_stage=request_state.request_stage,
                    preferred_account_id=None,
                    request_usage_budget=request_state.request_usage_budget,
                )
                retry_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    reroute_session,
                    request_state=request_state,
                    text_data=text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                )
                try:
                    async for event_block in retry_events:
                        yield event_block
                finally:
                    try:
                        await retry_events.aclose()
                    except Exception:
                        pass
                return
            is_context_overflow = _http_bridge_is_context_overflow_error(exc)
            should_rollover_after_context_overflow = _http_bridge_should_rollover_after_context_overflow(
                exc,
                key=bridge_session_key,
            )
            should_attempt_previous_response_recovery = (
                effective_payload.previous_response_id is not None
                and _http_bridge_should_attempt_local_previous_response_recovery(exc)
            )
            should_attempt_context_overflow_fresh_turn_recovery = (
                is_context_overflow
                and effective_payload.previous_response_id is not None
                and bridge_session_key.strength != "hard"
            )
            if (
                not should_attempt_previous_response_recovery
                and not should_rollover_after_context_overflow
                and not should_attempt_context_overflow_fresh_turn_recovery
            ):
                if is_context_overflow:
                    _log_http_bridge_event(
                        "context_overflow_no_rollover",
                        bridge_session_key,
                        account_id=None,
                        model=effective_payload.model,
                        detail="outcome=preserve_hard_affinity_session",
                        cache_key_family=bridge_session_key.affinity_kind,
                        model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                        owner_check_applied=True,
                    )
                raise

            if should_attempt_context_overflow_fresh_turn_recovery:
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="context_overflow_fresh_turn").inc()
                _log_http_bridge_event(
                    "context_overflow_fresh_turn_recover",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=retry_without_previous_response_id",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                recovery_path = "context_overflow_fresh_turn"
                retry_payload = _http_bridge_payload_without_previous_response_id(untrimmed_effective_payload)
                retry_previous_response_id = None
                retry_request_stage = "context_overflow_recover"
                retry_preferred_account_id = None
                allow_previous_response_recovery_rebind = False
            elif should_rollover_after_context_overflow:
                _log_http_bridge_event(
                    "context_overflow_rollover",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=close_session_after_context_length_exceeded",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                raise
            else:
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="local_previous_response_error").inc()
                _log_http_bridge_event(
                    "previous_response_recover_local",
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail="outcome=local_rebind_after_local_error",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message="Upstream websocket closed before response.completed",
                )
                recovery_path = "local_previous_response_error"
                retry_payload = effective_payload
                retry_previous_response_id = request_state.previous_response_id
                retry_request_stage = "reattach"
                retry_preferred_account_id = request_state.preferred_account_id
                allow_previous_response_recovery_rebind = True

            session = await self._get_or_create_http_bridge_session(
                bridge_session_key,
                headers=dict(headers),
                affinity=affinity,
                api_key=api_key,
                request_model=retry_payload.model,
                idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                    affinity=affinity,
                    idle_ttl_seconds=idle_ttl_seconds,
                    codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                    prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                ),
                max_sessions=max_sessions,
                previous_response_id=retry_previous_response_id,
                gateway_safe_mode=runtime_config.gateway_safe_mode,
                allow_forward_to_owner=False,
                forwarded_request=False,
                allow_previous_response_recovery_rebind=allow_previous_response_recovery_rebind,
                durable_lookup=durable_lookup,
                request_stage=retry_request_stage,
                preferred_account_id=retry_preferred_account_id,
                fallback_on_preferred_account_unavailable=not (
                    file_required_preferred_account and retry_preferred_account_id is not None
                ),
                request_usage_budget=estimate_api_key_request_usage(retry_payload),
            )
            _record_bridge_reattach(path=recovery_path, outcome="success")

            try:
                retry_api_key_reservation = api_key_reservation
                retry_reservation_reacquired = False
                if api_key is not None and api_key_reservation is not None:
                    retry_api_key_reservation = await self._reserve_websocket_api_key_usage(
                        api_key,
                        request_model=retry_payload.model,
                        request_service_tier=_normalize_service_tier_value(
                            dict(retry_payload.to_payload()).get("service_tier"),
                        ),
                        request_usage_budget=estimate_api_key_request_usage(retry_payload),
                    )
                    retry_reservation_reacquired = True

                retry_request_state, retry_text_data = self._prepare_http_bridge_request(
                    retry_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=retry_api_key_reservation,
                    request_id=request_id,
                )
                if downstream_turn_state is not None:
                    retry_request_state.session_id = _normalize_session_id(downstream_turn_state)
                retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                retry_request_state.request_stage = retry_request_stage
                retry_request_state.preferred_account_id = retry_preferred_account_id

                retry_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    session,
                    request_state=retry_request_state,
                    text_data=retry_text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                )
                try:
                    async for event_block in retry_events:
                        yield event_block
                finally:
                    try:
                        await retry_events.aclose()
                    except Exception:
                        pass
            except BaseException:
                if retry_reservation_reacquired and retry_api_key_reservation is not None:
                    await self._release_websocket_reservation(retry_api_key_reservation)
                raise
        finally:
            try:
                await session_events.aclose()
            except Exception:
                pass

    async def _reset_http_bridge_session_after_local_terminal_error(
        self,
        session: "_HTTPBridgeSession",
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self._http_bridge_lock:
            if self._http_bridge_sessions.get(session.key) is session:
                self._http_bridge_sessions.pop(session.key, None)
        async with session.pending_lock:
            session.queued_request_count = 0
        await self._fail_pending_websocket_requests(
            account=session.account,
            account_id_value=session.account.id,
            pending_requests=session.pending_requests,
            pending_lock=session.pending_lock,
            error_code=error_code,
            error_message=error_message,
            api_key=None,
            response_create_gate=session.response_create_gate,
        )
        await self._close_http_bridge_session(session)

    async def _stream_http_bridge_session_events(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
    ) -> AsyncGenerator[str, None]:
        await self._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=queue_limit,
        )
        if downstream_turn_state is not None:
            await self._register_http_bridge_turn_state(session, downstream_turn_state)

        try:
            event_queue = request_state.event_queue
            assert event_queue is not None
            yielded_any = False
            keepalive_sent = False
            keepalive_count = 0
            while True:
                keepalive_interval = getattr(_service_get_settings(), "sse_keepalive_interval_seconds", 10.0)
                if keepalive_interval > 0:
                    settings = _service_get_settings()
                    stream_keepalive_max_count = _stream_keepalive_max_count()
                    stream_idle_timeout_seconds = getattr(
                        settings,
                        "stream_idle_timeout_seconds",
                        keepalive_interval * stream_keepalive_max_count,
                    )
                    max_keepalive_count = max(
                        stream_keepalive_max_count,
                        math.ceil(max(0.001, stream_idle_timeout_seconds) / keepalive_interval),
                    )
                    wait_timeout = keepalive_interval
                    if not yielded_any and not keepalive_sent:
                        wait_timeout = max(wait_timeout, _http_bridge_startup_keepalive_grace_seconds())
                    try:
                        event_block = await asyncio.wait_for(event_queue.get(), timeout=wait_timeout)
                    except asyncio.TimeoutError:
                        keepalive_count += 1
                        downstream_response_id = _websocket_downstream_response_id(request_state)
                        if keepalive_count > max_keepalive_count:
                            logger.info(
                                "HTTP bridge stream idle timeout request_id=%s keepalive_count=%s "
                                "max_keepalive_count=%s",
                                request_state.request_id,
                                keepalive_count,
                                max_keepalive_count,
                            )
                            yield format_sse_event(
                                cast(
                                    Mapping[str, JsonValue],
                                    response_failed_event(
                                        "stream_idle_timeout",
                                        "Upstream did not respond within the keepalive window",
                                        response_id=downstream_response_id,
                                    ),
                                )
                            )
                            break
                        if propagate_http_errors and request_state.response_id is None:
                            continue
                        keepalive_sent = True
                        yielded_any = True
                        if request_state.response_id or request_state.replay_downstream_response_id:
                            yield format_sse_event(
                                cast(
                                    Mapping[str, JsonValue],
                                    {
                                        "type": "response.in_progress",
                                        "response": {
                                            "id": downstream_response_id,
                                            "status": "in_progress",
                                        },
                                    },
                                )
                            )
                        else:
                            yield _codex_keepalive_frame()
                        continue
                else:
                    event_block = await event_queue.get()
                if event_block is None:
                    break
                keepalive_count = 0
                block_payload = parse_sse_data_json(event_block)
                block_event_type = _event_type_from_payload(None, block_payload)
                if request_state.latency_first_token_ms is None and block_event_type in _TEXT_DELTA_EVENT_TYPES:
                    request_state.latency_first_token_ms = int(
                        (_service_time().monotonic() - request_state.started_at) * 1000
                    )
                if not propagate_http_errors and _is_previous_response_not_found_error(
                    code=_normalize_error_code(
                        _websocket_event_error_code(block_event_type, block_payload),
                        _websocket_event_error_type(block_event_type, block_payload),
                    ),
                    param=_websocket_event_error_param(block_event_type, block_payload),
                    message=_websocket_event_error_message(block_event_type, block_payload),
                ):
                    session.upstream_control.reconnect_requested = True
                    request_state.error_http_status_override = 502
                    (
                        event_block,
                        _event,
                        block_payload,
                        block_event_type,
                    ) = _build_rewritten_stream_response_failed_event(
                        response_id=_websocket_downstream_response_id(request_state),
                        error_code="stream_incomplete",
                        error_message="Upstream websocket closed before response.completed",
                    )
                if (
                    not yielded_any
                    and propagate_http_errors
                    and block_event_type == "response.failed"
                    and request_state.error_http_status_override is not None
                    and request_state.error_http_status_override >= 400
                ):
                    if request_state.previous_response_not_found_rewritten:
                        raise ProxyResponseError(
                            request_state.error_http_status_override,
                            openai_error(
                                "bridge_previous_response_not_found",
                                "Upstream websocket closed before response.completed",
                            ),
                        )
                    raise ProxyResponseError(
                        request_state.error_http_status_override,
                        _openai_error_envelope_from_response_failed_payload(block_payload),
                    )
                yield event_block
                yielded_any = True
        finally:
            with anyio.CancelScope(shield=True):
                await self._detach_http_bridge_request(session, request_state=request_state)
                session.last_used_at = _service_time().monotonic()

    async def _http_bridge_has_live_local_session(
        self,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        api_key: ApiKeyData | None,
    ) -> bool:
        api_key_id = api_key.id if api_key is not None else None
        async with self._http_bridge_lock:
            candidate_keys = [key]
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                if not _http_bridge_session_reusable_for_request(
                    session=session,
                    key=candidate_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=None,
                ) and not _http_bridge_session_retiring_with_visible_requests(session):
                    continue
                return True
        return False

    async def _http_bridge_local_owner_account_id(
        self,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        previous_response_id: str,
        api_key: ApiKeyData | None,
    ) -> str | None:
        api_key_id = api_key.id if api_key is not None else None
        candidate_keys: list[_HTTPBridgeSessionKey] = [key]
        async with self._http_bridge_lock:
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
            previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
            if previous_key is not None and previous_key not in candidate_keys:
                candidate_keys.append(previous_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                if not _http_bridge_session_reusable_for_request(
                    session=session,
                    key=candidate_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                ):
                    continue
                _record_continuity_owner_resolution(
                    surface="http_bridge",
                    source="local_bridge_session",
                    outcome="hit",
                    previous_response_id=previous_response_id,
                    session_id=incoming_turn_state,
                )
                return session.account.id
        _record_continuity_owner_resolution(
            surface="http_bridge",
            source="local_bridge_session",
            outcome="miss",
            previous_response_id=previous_response_id,
            session_id=incoming_turn_state,
        )
        return None

    async def _http_bridge_can_forward_to_active_owner(
        self,
        durable_lookup: DurableBridgeLookup,
    ) -> bool:
        owner_instance = _durable_bridge_lookup_active_owner(durable_lookup)
        if owner_instance is None:
            return False
        if owner_instance == _service_get_settings().http_responses_session_bridge_instance_id:
            return False
        if self._ring_membership is None:
            return False
        try:
            owner_endpoint = await self._ring_membership.resolve_endpoint(owner_instance)
        except Exception:
            logger.debug("Failed to resolve HTTP bridge owner endpoint during anchor injection decision", exc_info=True)
            return False
        return owner_endpoint is not None

    async def _forward_http_bridge_request_to_owner(
        self,
        *,
        owner_forward: _HTTPBridgeOwnerForward,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        api_key_reservation: ApiKeyUsageReservationData | None,
        codex_session_affinity: bool,
        downstream_turn_state: str | None,
        request_started_at: float,
        proxy_api_authorization: str | None,
    ) -> AsyncIterator[str]:
        current_instance, _ = _normalized_http_bridge_instance_ring(_service_get_settings())
        forwarded_turn_state = _header_value_case_insensitive(headers, "x-codex-turn-state") or downstream_turn_state
        forward_context = HTTPBridgeForwardContext(
            origin_instance=current_instance,
            target_instance=owner_forward.owner_instance,
            reservation=api_key_reservation,
            codex_session_affinity=codex_session_affinity,
            downstream_turn_state=forwarded_turn_state,
            original_affinity_kind=owner_forward.key.affinity_kind,
            original_affinity_key=owner_forward.key.affinity_key,
        )
        forward_headers = _headers_with_authorization(headers, proxy_api_authorization)
        start = _service_time().monotonic()
        _log_http_bridge_event(
            "owner_forward_start",
            owner_forward.key,
            account_id=None,
            model=payload.model,
            detail=(
                f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                f"owner_endpoint={owner_forward.owner_endpoint}"
            ),
            cache_key_family=owner_forward.key.affinity_kind,
            model_class=_extract_model_class(payload.model) if payload.model else None,
            owner_check_applied=True,
        )

        forwarded_any = False
        forwarded_response_id: str | None = None
        try:
            async for event_block in self._http_bridge_owner_client.stream_responses(
                owner_endpoint=owner_forward.owner_endpoint,
                payload=payload,
                headers=forward_headers,
                context=forward_context,
                request_started_at=request_started_at,
            ):
                forwarded_any = True
                event_payload = parse_sse_data_json(event_block)
                event_type = _event_type_from_payload(None, event_payload)
                forwarded_response_id = _websocket_response_id(None, event_payload) or forwarded_response_id
                if event_type == "response.failed" and forwarded_response_id is None:
                    forwarded_response_id = get_request_id()
                yield event_block
        except OwnerForwardRelayFailure as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                    "error=relay_failure"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                yield exc.event_block
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner relay timed out",
                    error_type="server_error",
                ),
                failure_phase="owner_forward",
                failure_detail="relay_timeout",
                failure_exception_type=type(exc).__name__,
            ) from exc
        except ProxyResponseError as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                terminal_response_id = forwarded_response_id or get_request_id() or "unknown"
                yield _partial_output_proxy_error_event_block(
                    exc,
                    response_id=terminal_response_id,
                    previous_response_id=payload.previous_response_id,
                    preferred_account_id=None,
                    default_code="bridge_owner_unreachable",
                    default_message="HTTP bridge owner request failed",
                )
                return
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, error={exc}"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                terminal_response_id = forwarded_response_id or get_request_id() or "unknown"
                yield format_sse_event(
                    response_failed_event(
                        "bridge_owner_unreachable",
                        "HTTP bridge owner request failed",
                        response_id=terminal_response_id,
                    )
                )
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner request failed",
                    error_type="server_error",
                ),
                failure_phase="owner_forward",
                failure_detail=str(exc) or "owner_forward_request_failed",
                failure_exception_type=type(exc).__name__,
            ) from exc
        else:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="success").inc()
            _log_http_bridge_event(
                "owner_forward_success",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
        finally:
            if PROMETHEUS_AVAILABLE and bridge_forward_latency_seconds is not None:
                bridge_forward_latency_seconds.observe(max(_service_time().monotonic() - start, 0.0))

    def _prepare_http_bridge_request(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        request_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        request_state, text_data = self._prepare_response_bridge_request_state(
            payload,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            include_type_field=True,
            attach_event_queue=True,
            transport=_REQUEST_TRANSPORT_HTTP,
            client_metadata=_response_create_client_metadata(payload.to_payload(), headers=headers),
            session_id=_owner_lookup_session_id_from_headers(headers),
            request_log_id=request_id or get_request_id() or ensure_request_id(None),
        )
        request_state.useragent, request_state.useragent_group = _request_log_useragent_fields(headers)
        return request_state, text_data

    def _prepare_response_bridge_request_state(
        self,
        payload: ResponsesRequest,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        include_type_field: bool,
        attach_event_queue: bool,
        transport: str,
        client_metadata: Mapping[str, JsonValue] | None,
        session_id: str | None = None,
        request_id: str | None = None,
        request_log_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        deduped_replayed_input_count: int | None = None
        deduped_replayed_input_fingerprint: str | None = None
        deduped_replayed_tool_call_count = 0
        if payload.previous_response_id is not None and isinstance(payload.input, list):
            replayed_input_items = cast(list[JsonValue], payload.input)
            deduped_input_items, deduped_replayed_tool_call_count = dedupe_replayed_side_effect_input_items(
                replayed_input_items,
                sanitize_missing_outputs=False,
            )
            if deduped_replayed_tool_call_count > 0:
                deduped_replayed_input_count = len(replayed_input_items)
                deduped_replayed_input_fingerprint = _fingerprint_input_items(replayed_input_items)
                payload = payload.model_copy(update={"input": deduped_input_items})
        upstream_payload = dict(payload.to_payload())
        upstream_payload.pop("stream", None)
        upstream_payload.pop("background", None)
        if include_type_field:
            upstream_payload["type"] = "response.create"
        if client_metadata:
            upstream_payload["client_metadata"] = client_metadata
        forwarded_service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        input_item_count = 0
        input_full_fingerprint: str | None = None
        payload_input = payload.input
        if isinstance(payload_input, list):
            payload_input_list = cast(list[JsonValue], payload_input)
            input_item_count = len(payload_input_list)
            if input_item_count > 0:
                input_full_fingerprint = _fingerprint_input_items(payload_input_list)

        request_state = _WebSocketRequestState(
            request_id=request_id or f"ws_{uuid4().hex}",
            request_log_id=request_log_id,
            model=payload.model,
            service_tier=forwarded_service_tier,
            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
            api_key_reservation=api_key_reservation,
            started_at=_service_time().monotonic(),
            requested_service_tier=forwarded_service_tier,
            awaiting_response_created=True,
            event_queue=asyncio.Queue() if attach_event_queue else None,
            transport=transport,
            api_key=api_key,
            request_usage_budget=estimate_api_key_request_usage(payload),
            previous_response_id=payload.previous_response_id,
            session_id=_normalize_session_id(session_id),
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
        )
        if deduped_replayed_input_count is not None:
            request_state.input_item_count = deduped_replayed_input_count
            request_state.input_full_fingerprint = deduped_replayed_input_fingerprint
            logger.warning(
                "%s_replayed_tool_call_input_deduped request_id=%s original_items=%s deduped_to=%s "
                "removed_tool_calls=%s previous_response_id=%s",
                transport,
                request_state.request_id,
                deduped_replayed_input_count,
                input_item_count,
                deduped_replayed_tool_call_count,
                payload.previous_response_id,
            )
        text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
        payload_size = len(text_data.encode("utf-8"))
        max_bytes = _upstream_response_create_max_bytes()
        if payload_size > max_bytes:
            slimmed_payload, slim_summary = _slim_response_create_payload_for_upstream(
                upstream_payload,
                max_bytes=max_bytes,
            )
            if slim_summary is not None:
                upstream_payload = slimmed_payload
                text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
                logger.warning(
                    (
                        "Slimmed response.create request_id=%s request_log_id=%s transport=%s "
                        "original_bytes=%s slimmed_bytes=%s "
                        "historical_tool_outputs_slimmed=%s historical_images_slimmed=%s"
                    ),
                    request_state.request_id,
                    request_state.request_log_id,
                    transport,
                    payload_size,
                    len(text_data.encode("utf-8")),
                    slim_summary["historical_tool_outputs_slimmed"],
                    slim_summary["historical_images_slimmed"],
                )
        request_state.request_text = text_data
        _enforce_response_create_size_limit(request_state)
        return request_state, text_data

    async def _inline_http_bridge_image_urls(
        self,
        text_data: str,
        request_state: _WebSocketRequestState,
    ) -> str:
        """Inline external ``input_image`` URLs into ``data:`` URLs.

        The HTTP direct-stream path already does this via
        ``_inline_input_image_urls`` in :mod:`app.core.clients.proxy`, but the
        HTTP bridge (WebSocket pool) path was missing the conversion.  The
        upstream ChatGPT WebSocket only accepts ``data:image/…`` payloads; an
        external ``https://`` image URL causes it to silently reject or hang
        the request.

        This method applies the same transformation to the already-serialised
        ``text_data`` JSON that will be sent on the upstream WebSocket.
        If any external image URLs survive inlining (because the fetch failed),
        the request is rejected immediately with a 400 error rather than
        allowing the upstream to hang.
        """
        settings = _service_get_settings()
        if not settings.image_inline_fetch_enabled:
            return text_data
        # Quick string-level pre-check: skip the parse/fetch cycle when the
        # payload contains no ``input_image`` items with an ``http`` URL.
        if "input_image" not in text_data:
            return text_data
        try:
            payload_dict: dict[str, JsonValue] = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            return text_data
        connect_timeout = getattr(settings, "upstream_connect_timeout_seconds", 5.0)
        async with _service_lease_http_session()() as http_session:
            image_fetch_session = _service_as_image_fetch_session()(http_session)
            inlined = await _service_inline_input_image_urls()(
                payload_dict,
                image_fetch_session,
                connect_timeout,
            )
            inlined = await _inline_top_level_input_image_urls(inlined, image_fetch_session, connect_timeout)
        # After inlining, check if any external URLs survived (i.e. fetch
        # failed).  The upstream WS only accepts data: URLs so sending an
        # external URL would just cause a silent hang.
        remaining_external = _count_external_image_urls(inlined)
        if remaining_external > 0:
            raise ProxyResponseError(
                400,
                openai_error(
                    "image_download_failed",
                    (
                        f"Failed to download {remaining_external} external image(s). "
                        "The upstream API only accepts inline data: URLs. "
                        "Send images as base64 data URLs (data:image/png;base64,...) "
                        "or ensure the image URLs are publicly accessible."
                    ),
                ),
            )
        updated_text = json.dumps(inlined, ensure_ascii=True, separators=(",", ":"))
        if updated_text == text_data:
            return text_data
        request_state.request_text = updated_text
        _enforce_response_create_size_limit(request_state)
        return updated_text

    async def _http_bridge_pending_count(self, session: "_HTTPBridgeSession") -> int:
        async with session.pending_lock:
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)

    def _http_bridge_pending_count_nowait(
        self,
        session: "_HTTPBridgeSession",
        *,
        context: str,
    ) -> int | None:
        try:
            session.pending_lock.acquire_nowait()
        except (anyio.WouldBlock, RuntimeError):
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
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)
        finally:
            session.pending_lock.release()

    async def _close_http_bridge_session_bounded(
        self,
        session: "_HTTPBridgeSession",
        *,
        reason: str,
    ) -> None:
        close_task = asyncio.create_task(
            self._close_http_bridge_session(session),
            name=f"http-bridge-close-{_hash_identifier(session.key.affinity_key)}",
        )

        def _track_close_task_after_interruption(*, interruption: str) -> None:
            if close_task.done():
                return
            self._background_cleanup_tasks.add(close_task)

            def _close_done(done_task: asyncio.Task[None]) -> None:
                self._background_cleanup_tasks.discard(done_task)
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

            close_task.add_done_callback(_close_done)

        try:
            await asyncio.wait_for(
                asyncio.shield(close_task),
                timeout=_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _track_close_task_after_interruption(interruption="timeout")
            logger.warning(
                "http_bridge_session_close_timeout reason=%s bridge_kind=%s bridge_key=%s "
                "account_id=%s model=%s timeout_seconds=%.1f background_cleanup_tasks=%d",
                reason,
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                session.account.id,
                session.request_model,
                _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
                len(self._background_cleanup_tasks),
            )
        except asyncio.CancelledError:
            _track_close_task_after_interruption(interruption="cancellation")
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

    def _schedule_http_bridge_session_closes(
        self,
        sessions: list["_HTTPBridgeSession"],
        *,
        reason: str,
    ) -> None:
        for session in sessions:
            if len(self._background_cleanup_tasks) >= _HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD:
                logger.warning(
                    "http_bridge_background_cleanup_backlog action=session_close count=%d threshold=%d reason=%s",
                    len(self._background_cleanup_tasks),
                    _HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD,
                    reason,
                )
            self._schedule_cancel_safe_cleanup(
                self._close_http_bridge_session_bounded(session, reason=reason),
                action="http_bridge_session_close",
                request_id=_hash_identifier(session.key.affinity_key),
            )

    async def _drain_http_bridge_background_cleanup_tasks(self, *, reason: str) -> None:
        tasks = [
            task
            for task in self._background_cleanup_tasks
            if not task.done()
            and (
                task.get_name().startswith("proxy-http_bridge_session_close-")
                or task.get_name().startswith("http-bridge-close-")
            )
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True),
                timeout=_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "http_bridge_background_cleanup_drain_timeout reason=%s count=%d timeout_seconds=%.1f",
                reason,
                len(tasks),
                _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )

    async def _fail_http_bridge_inflight_session_creation(
        self,
        key: "_HTTPBridgeSessionKey",
        inflight_future: asyncio.Future["_HTTPBridgeSession"] | None,
        exc: BaseException,
    ) -> bool:
        if inflight_future is None:
            return False
        async with self._http_bridge_lock:
            current_future = self._http_bridge_inflight_sessions.get(key)
            if current_future is not inflight_future:
                return False
            self._http_bridge_inflight_sessions.pop(key, None)
            if inflight_future.done():
                return True
            if isinstance(exc, asyncio.CancelledError):
                inflight_future.cancel()
            else:
                inflight_future.set_exception(exc)
                inflight_future.exception()
            return True

    async def _evict_http_bridge_inflight_waiter(
        self,
        inflight_future: asyncio.Future["_HTTPBridgeSession"],
        exc: BaseException,
    ) -> "_HTTPBridgeSessionKey | None":
        async with self._http_bridge_lock:
            stale_key = None
            for candidate_key, candidate_future in self._http_bridge_inflight_sessions.items():
                if candidate_future is inflight_future:
                    stale_key = candidate_key
                    break
            if stale_key is None:
                return None
            self._http_bridge_inflight_sessions.pop(stale_key, None)
            if not inflight_future.done():
                inflight_future.set_exception(exc)
                inflight_future.exception()
            return stale_key

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[False] = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession": ...

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[True],
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward": ...

    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: bool = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        settings = _service_get_settings()
        api_key_id = api_key.id if api_key is not None else None
        incoming_turn_state = _sticky_key_from_turn_state_header(headers)
        incoming_session_key = _sticky_key_from_session_header(headers)
        if await _http_bridge_should_wait_for_registration(self, key, settings):
            skip_registration_gate = False
            async with self._http_bridge_lock:
                existing = self._http_bridge_sessions.get(key)
                if existing is not None:
                    skip_registration_gate = True
                elif incoming_turn_state is not None:
                    alias_index_key = _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                    alias_key = self._http_bridge_turn_state_index.get(alias_index_key)
                    if alias_key is not None and alias_key in self._http_bridge_sessions:
                        skip_registration_gate = True
            if not skip_registration_gate:
                import app.core.startup as startup_module

                registered = await startup_module.wait_for_bridge_registration(
                    timeout_seconds=settings.upstream_connect_timeout_seconds,
                )
                if not registered:
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_owner_unreachable",
                            "HTTP bridge registration is not ready",
                            error_type="server_error",
                        ),
                    )
        effective_idle_ttl_seconds = idle_ttl_seconds
        forwarded_affinity = (
            _forwarded_http_bridge_session_key(
                headers,
                api_key,
                forwarded_affinity_kind=forwarded_affinity_kind,
                forwarded_affinity_key=forwarded_affinity_key,
            )
            if forwarded_request
            else None
        )
        old_account_id: str | None = None
        force_durable_takeover_after_detach = False
        while True:
            inflight_future: asyncio.Future[_HTTPBridgeSession] | None = None
            capacity_wait_future: asyncio.Future[_HTTPBridgeSession] | None = None
            owns_creation = False
            continuity_error: ProxyResponseError | None = None
            owner_mismatch_error: ProxyResponseError | None = None
            owner_forward: _HTTPBridgeOwnerForward | None = None
            force_durable_takeover = force_durable_takeover_after_detach
            missing_turn_state_alias = False
            used_session_header_fallback = False
            sessions_to_close_before_create: list[_HTTPBridgeSession] = []
            session_to_return_after_close: _HTTPBridgeSession | None = None
            preserve_durable_canonical_key = (
                incoming_turn_state is not None
                and forwarded_affinity is None
                and durable_lookup is not None
                and key.affinity_kind == durable_lookup.canonical_kind
                and key.affinity_key == durable_lookup.canonical_key
                and key.affinity_kind != "turn_state_header"
            )

            async with self._http_bridge_lock:
                if (
                    incoming_turn_state is not None
                    and forwarded_affinity is None
                    and not preserve_durable_canonical_key
                ):
                    alias_index_key = _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                    alias_key = self._http_bridge_turn_state_index.get(alias_index_key)
                    if alias_key is not None:
                        key = alias_key
                        alias_session = self._http_bridge_sessions.get(alias_key)
                        if (
                            alias_session is None
                            or alias_session.closed
                            or alias_session.account.status != AccountStatus.ACTIVE
                            or not _http_bridge_session_matches_preferred_account(
                                session=alias_session,
                                previous_response_id=previous_response_id,
                                preferred_account_id=preferred_account_id,
                                require_preferred_account=not fallback_on_preferred_account_unavailable,
                            )
                        ):
                            self._http_bridge_turn_state_index.pop(alias_index_key, None)
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        else:
                            self._promote_http_bridge_session_to_codex_affinity(
                                alias_session,
                                turn_state=incoming_turn_state,
                                settings=settings,
                            )
                            for alias in alias_session.downstream_turn_state_aliases:
                                self._http_bridge_turn_state_index[
                                    _http_bridge_turn_state_alias_key(alias, alias_session.key.api_key_id)
                                ] = alias_session.key
                            key = alias_session.key
                    elif incoming_turn_state.startswith("http_turn_"):
                        if previous_response_id is not None:
                            previous_alias_key = _http_bridge_previous_response_alias_key(
                                previous_response_id,
                                api_key_id,
                            )
                            previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
                            previous_session = None
                            if previous_key is not None:
                                previous_session = self._http_bridge_sessions.get(previous_key)
                            if (
                                previous_session is not None
                                and not previous_session.closed
                                and previous_session.account.status == AccountStatus.ACTIVE
                                and _http_bridge_session_matches_preferred_account(
                                    session=previous_session,
                                    previous_response_id=previous_response_id,
                                    preferred_account_id=preferred_account_id,
                                    require_preferred_account=not fallback_on_preferred_account_unavailable,
                                )
                            ):
                                key = previous_session.key
                                self._promote_http_bridge_session_to_codex_affinity(
                                    previous_session,
                                    turn_state=incoming_turn_state,
                                    settings=settings,
                                )
                                previous_session.downstream_turn_state_aliases.add(incoming_turn_state)
                                for alias in previous_session.downstream_turn_state_aliases:
                                    self._http_bridge_turn_state_index[
                                        _http_bridge_turn_state_alias_key(
                                            alias,
                                            previous_session.key.api_key_id,
                                        )
                                    ] = previous_session.key
                                continue
                            if previous_key is not None:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                        if incoming_session_key is not None:
                            key = _HTTPBridgeSessionKey("session_header", incoming_session_key, api_key_id)
                            used_session_header_fallback = True
                        else:
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                            missing_turn_state_alias = True

                pruned_sessions = self._prune_http_bridge_sessions_locked()
                if pruned_sessions:
                    if any(session.key == key for session in pruned_sessions):
                        force_durable_takeover = True
                    self._schedule_http_bridge_session_closes(
                        pruned_sessions,
                        reason="registry_detach",
                    )

                existing = self._http_bridge_sessions.get(key)
                if (
                    existing is not None
                    and not existing.closed
                    and existing.account.status == AccountStatus.ACTIVE
                    and _http_bridge_session_allows_api_key(existing, api_key)
                    and _http_bridge_session_reusable_for_request(
                        session=existing,
                        key=key,
                        incoming_turn_state=incoming_turn_state,
                        previous_response_id=previous_response_id,
                    )
                    and _http_bridge_session_matches_preferred_account(
                        session=existing,
                        previous_response_id=previous_response_id,
                        preferred_account_id=preferred_account_id,
                        require_preferred_account=not fallback_on_preferred_account_unavailable,
                    )
                ):
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        existing.api_key = api_key
                        existing.request_model = request_model
                        existing.last_used_at = _service_time().monotonic()
                        await self._refresh_durable_http_bridge_session(existing)
                        _log_http_bridge_event(
                            "reuse",
                            key,
                            account_id=existing.account.id,
                            model=existing.request_model,
                            pending_count=self._http_bridge_pending_count_nowait(
                                existing,
                                context="reuse_log",
                            ),
                            cache_key_family=key.affinity_kind,
                            model_class=_extract_model_class(existing.request_model)
                            if existing.request_model
                            else None,
                        )
                        return existing
                    old_account_id = existing.account.id
                    detached = self._detach_http_bridge_session_locked(key, expected_session=existing)
                    if detached is not None:
                        force_durable_takeover = True
                        self._schedule_http_bridge_session_closes([detached], reason="registry_detach")
                    existing = None
                if existing is not None and not existing.closed and existing.account.status == AccountStatus.ACTIVE:
                    old_account_id = existing.account.id
                    retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(existing)
                    detached = self._detach_http_bridge_session_locked(
                        key,
                        expected_session=existing,
                        mark_closed=not retiring_with_visible_requests,
                    )
                    if detached is not None:
                        force_durable_takeover = True
                        if not retiring_with_visible_requests:
                            self._schedule_http_bridge_session_closes([detached], reason="registry_detach")
                    existing = None

                if shutdown_state.is_bridge_drain_active() and not _http_bridge_can_recover_during_drain(
                    key=key,
                    headers=headers,
                    previous_response_id=previous_response_id,
                    durable_lookup=durable_lookup,
                ):
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_drain_active",
                            "HTTP bridge is draining — new sessions not accepted during shutdown",
                            error_type="server_error",
                        ),
                    )
                if shutdown_state.is_bridge_drain_active():
                    _record_bridge_drain_recovery_allowed()

                owner_check_required = _http_bridge_owner_check_required(
                    key,
                    gateway_safe_mode=gateway_safe_mode,
                )
                if owner_check_required or key.affinity_kind == "prompt_cache":
                    owner_instance = _durable_bridge_lookup_active_owner(durable_lookup)
                    hard_continuity_lookup = owner_check_required or previous_response_id is not None
                    ring_lookup_failed = False
                    if owner_instance is None:
                        try:
                            owner_instance = await _http_bridge_owner_instance(key, settings, self._ring_membership)
                        except Exception as exc:
                            ring_lookup_failed = True
                            if hard_continuity_lookup:
                                _record_continuity_fail_closed(
                                    surface="http_bridge",
                                    reason="owner_metadata_unavailable",
                                    previous_response_id=previous_response_id,
                                    session_id=incoming_turn_state or incoming_session_key,
                                    upstream_error_code="owner_lookup_failed",
                                )
                                raise ProxyResponseError(
                                    502,
                                    _http_bridge_owner_lookup_unavailable_error_envelope(),
                                ) from exc
                            if _http_bridge_can_local_recover_without_ring(
                                key=key,
                                headers=headers,
                                previous_response_id=previous_response_id,
                                durable_lookup=durable_lookup,
                            ):
                                logger.warning(
                                    "Bridge owner lookup failed; allowing local recovery path",
                                    exc_info=True,
                                )
                                owner_instance = settings.http_responses_session_bridge_instance_id
                            else:
                                raise
                    try:
                        current_instance, ring = await _active_http_bridge_instance_ring(
                            settings, self._ring_membership
                        )
                    except Exception as exc:
                        if hard_continuity_lookup:
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="owner_metadata_unavailable",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state or incoming_session_key,
                                upstream_error_code="ring_lookup_failed",
                            )
                            raise ProxyResponseError(
                                502,
                                _http_bridge_owner_lookup_unavailable_error_envelope(),
                            ) from exc
                        if ring_lookup_failed or _http_bridge_can_local_recover_without_ring(
                            key=key,
                            headers=headers,
                            previous_response_id=previous_response_id,
                            durable_lookup=durable_lookup,
                        ):
                            logger.warning(
                                "Bridge ring lookup failed; falling back to local recovery ring", exc_info=True
                            )
                            current_instance = settings.http_responses_session_bridge_instance_id
                            ring = (current_instance,)
                        else:
                            raise
                    owner_mismatch = owner_instance is not None and owner_instance != current_instance
                    if owner_mismatch and (len(ring) > 1 or durable_lookup is not None):
                        if PROMETHEUS_AVAILABLE and bridge_owner_mismatch_total is not None:
                            bridge_owner_mismatch_total.labels(strength=_http_bridge_key_strength(key)).inc()
                        if (
                            owner_check_required
                            and not (previous_response_id is not None and allow_previous_response_recovery_rebind)
                            and not allow_bootstrap_owner_rebind
                        ):
                            _log_http_bridge_event(
                                "owner_mismatch",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, outcome=forward"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=True,
                            )
                            if allow_forward_to_owner:
                                if forwarded_request:
                                    _log_http_bridge_event(
                                        "owner_mismatch_forward_loop",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=forward_loop_prevented"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    raise ProxyResponseError(
                                        503,
                                        openai_error(
                                            "bridge_forward_loop_prevented",
                                            (
                                                "HTTP bridge request was forwarded back to a non-owner instance; "
                                                "refusing takeover to avoid a forward loop"
                                            ),
                                            error_type="server_error",
                                        ),
                                    )
                                elif self._ring_membership is None:
                                    if _http_bridge_has_durable_recovery_anchor(
                                        previous_response_id=previous_response_id,
                                        durable_lookup=durable_lookup,
                                    ):
                                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                            bridge_durable_recover_total.labels(path="owner_missing").inc()
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=local_recover_no_ring"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                    elif _http_bridge_can_single_instance_owner_takeover_without_anchor(
                                        key=key,
                                        owner_instance=owner_instance,
                                        current_instance=current_instance,
                                        ring=ring,
                                    ):
                                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                            bridge_durable_recover_total.labels(path="restart_takeover").inc()
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=single_instance_takeover_no_anchor"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                    else:
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=local_recover_no_ring"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                else:
                                    assert owner_instance is not None
                                    owner_endpoint = await self._ring_membership.resolve_endpoint(owner_instance)
                                    if owner_endpoint is None:
                                        if _http_bridge_has_durable_recovery_anchor(
                                            previous_response_id=previous_response_id,
                                            durable_lookup=durable_lookup,
                                        ):
                                            if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                                bridge_durable_recover_total.labels(path="owner_missing").inc()
                                            _log_http_bridge_event(
                                                "owner_endpoint_missing_local_recover",
                                                key,
                                                account_id=None,
                                                model=request_model,
                                                detail=(
                                                    "expected_instance="
                                                    f"{owner_instance}, current_instance={current_instance}, "
                                                    "outcome=local_recover"
                                                ),
                                                cache_key_family=key.affinity_kind,
                                                model_class=_extract_model_class(request_model)
                                                if request_model
                                                else None,
                                                owner_check_applied=True,
                                            )
                                            force_durable_takeover = True
                                        else:
                                            _log_http_bridge_event(
                                                "owner_mismatch_local_recover",
                                                key,
                                                account_id=None,
                                                model=request_model,
                                                detail=(
                                                    "expected_instance="
                                                    f"{owner_instance}, current_instance={current_instance}, "
                                                    "outcome=local_recover_no_endpoint"
                                                ),
                                                cache_key_family=key.affinity_kind,
                                                model_class=_extract_model_class(request_model)
                                                if request_model
                                                else None,
                                                owner_check_applied=True,
                                            )
                                            force_durable_takeover = True
                                    elif _http_bridge_endpoint_matches_current_instance(owner_endpoint, settings):
                                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                            bridge_durable_recover_total.labels(path="restart_takeover").inc()
                                        _log_http_bridge_event(
                                            "owner_mismatch_local_recover",
                                            key,
                                            account_id=None,
                                            model=request_model,
                                            detail=(
                                                "expected_instance="
                                                f"{owner_instance}, current_instance={current_instance}, "
                                                "outcome=local_recover_same_endpoint"
                                            ),
                                            cache_key_family=key.affinity_kind,
                                            model_class=_extract_model_class(request_model) if request_model else None,
                                            owner_check_applied=True,
                                        )
                                        force_durable_takeover = True
                                    else:
                                        owner_forward = _HTTPBridgeOwnerForward(
                                            owner_instance=owner_instance,
                                            owner_endpoint=owner_endpoint,
                                            key=key,
                                        )
                            else:
                                if _http_bridge_has_durable_recovery_anchor(
                                    previous_response_id=previous_response_id,
                                    durable_lookup=durable_lookup,
                                ):
                                    if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                                        bridge_durable_recover_total.labels(path="owner_missing").inc()
                                    _log_http_bridge_event(
                                        "owner_mismatch_local_recover",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=local_recover"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    force_durable_takeover = True
                                else:
                                    _log_http_bridge_event(
                                        "owner_mismatch_local_recover",
                                        key,
                                        account_id=None,
                                        model=request_model,
                                        detail=(
                                            "expected_instance="
                                            f"{owner_instance}, current_instance={current_instance}, "
                                            "outcome=local_recover_no_forward"
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(request_model) if request_model else None,
                                        owner_check_applied=True,
                                    )
                                    force_durable_takeover = True
                        else:
                            _log_http_bridge_event(
                                "prompt_cache_locality_miss",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, "
                                    "outcome=local_rebind"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=False,
                            )
                            if _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
                                key=key,
                                owner_instance=owner_instance,
                                current_instance=current_instance,
                                ring=ring,
                            ):
                                force_durable_takeover = True
                            elif allow_previous_response_recovery_rebind or allow_bootstrap_owner_rebind:
                                force_durable_takeover = True
                            _log_http_bridge_event(
                                "soft_locality_rebind",
                                key,
                                account_id=None,
                                model=request_model,
                                detail=(
                                    "expected_instance="
                                    f"{owner_instance}, current_instance={current_instance}, outcome=local_rebind"
                                ),
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=False,
                            )
                            if PROMETHEUS_AVAILABLE:
                                if bridge_prompt_cache_locality_miss_total is not None:
                                    bridge_prompt_cache_locality_miss_total.inc()
                                if bridge_soft_local_rebind_total is not None:
                                    bridge_soft_local_rebind_total.inc()
                                if bridge_local_rebind_total is not None:
                                    bridge_local_rebind_total.labels(reason="prompt_cache_locality_miss").inc()

                if existing is not None:
                    old_account_id = existing.account.id
                    _log_http_bridge_event(
                        "discard_stale",
                        key,
                        account_id=existing.account.id,
                        model=existing.request_model,
                        cache_key_family=key.affinity_kind,
                        model_class=_extract_model_class(existing.request_model) if existing.request_model else None,
                    )
                    detached = self._detach_http_bridge_session_locked(key, expected_session=existing)
                    if detached is not None:
                        force_durable_takeover = True
                        self._schedule_http_bridge_session_closes([detached], reason="registry_detach")

                if owner_mismatch_error is None:
                    inflight_future = self._http_bridge_inflight_sessions.get(key)
                    if (
                        previous_response_id is not None
                        and inflight_future is None
                        and (existing is None or existing.closed or existing.account.status != AccountStatus.ACTIVE)
                    ):
                        previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
                        previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
                        if previous_key is not None:
                            previous_session = self._http_bridge_sessions.get(previous_key)
                            if (
                                previous_session is not None
                                and not previous_session.closed
                                and previous_session.account.status == AccountStatus.ACTIVE
                            ):
                                key = previous_session.key
                                existing = previous_session
                                inflight_future = self._http_bridge_inflight_sessions.get(previous_key)
                                if incoming_turn_state:
                                    self._promote_http_bridge_session_to_codex_affinity(
                                        previous_session,
                                        turn_state=incoming_turn_state,
                                        settings=settings,
                                    )
                                    previous_session.downstream_turn_state_aliases.add(incoming_turn_state)
                                    for alias in previous_session.downstream_turn_state_aliases:
                                        self._http_bridge_turn_state_index[
                                            _http_bridge_turn_state_alias_key(
                                                alias,
                                                previous_session.key.api_key_id,
                                            )
                                        ] = previous_session.key
                                if inflight_future is None:
                                    previous_session.request_model = request_model
                                    previous_session.last_used_at = _service_time().monotonic()
                                    await self._refresh_durable_http_bridge_session(previous_session)
                                    _log_http_bridge_event(
                                        "reuse",
                                        key,
                                        account_id=previous_session.account.id,
                                        model=previous_session.request_model,
                                        pending_count=self._http_bridge_pending_count_nowait(
                                            previous_session,
                                            context="previous_response_reuse_log",
                                        ),
                                        cache_key_family=key.affinity_kind,
                                        model_class=_extract_model_class(previous_session.request_model)
                                        if previous_session.request_model
                                        else None,
                                    )
                                    session_to_return_after_close = previous_session
                            else:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                    if (
                        session_to_return_after_close is None
                        and previous_response_id is not None
                        and not used_session_header_fallback
                        and not allow_previous_response_recovery_rebind
                        and durable_lookup is None
                    ):
                        _record_continuity_fail_closed(
                            surface="http_bridge",
                            reason="continuity_lost",
                            previous_response_id=previous_response_id,
                            session_id=incoming_turn_state or incoming_session_key,
                        )
                        continuity_error = ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                    elif missing_turn_state_alias and inflight_future is None and durable_lookup is None:
                        turn_state_scope_conflict = incoming_turn_state is not None and any(
                            alias == incoming_turn_state and alias_api_key != api_key_id
                            for alias, alias_api_key in self._http_bridge_turn_state_index
                        )
                        if turn_state_scope_conflict:
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="turn_state_scope_conflict",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state,
                            )
                            continuity_error = ProxyResponseError(
                                409,
                                openai_error(
                                    "bridge_instance_mismatch",
                                    "HTTP bridge turn-state is bound to a different API key scope",
                                    error_type="server_error",
                                ),
                            )
                        elif (
                            incoming_turn_state is not None
                            and incoming_turn_state.startswith("http_turn_")
                            and not allow_forward_to_owner
                        ):
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="generated_turn_state_continuity_lost",
                                previous_response_id=previous_response_id,
                                session_id=incoming_turn_state,
                            )
                            continuity_error = ProxyResponseError(
                                409,
                                openai_error(
                                    "bridge_instance_mismatch",
                                    "HTTP bridge continuity was lost for generated turn-state",
                                    error_type="server_error",
                                ),
                            )
                        else:
                            _log_http_bridge_event(
                                "turn_state_alias_miss_local_rebind",
                                key,
                                account_id=None,
                                model=request_model,
                                detail="outcome=local_rebind_without_alias",
                                cache_key_family=key.affinity_kind,
                                model_class=_extract_model_class(request_model) if request_model else None,
                                owner_check_applied=owner_check_required,
                            )
                    elif inflight_future is None:
                        while (
                            len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= max_sessions
                            and self._http_bridge_sessions
                        ):
                            evictable_sessions: list[tuple[_HTTPBridgeSessionKey, _HTTPBridgeSession]] = []
                            for candidate_key, candidate_session in self._http_bridge_sessions.items():
                                pending_count = self._http_bridge_pending_count_nowait(
                                    candidate_session,
                                    context="capacity_evict_scan",
                                )
                                if pending_count is None:
                                    continue
                                if pending_count:
                                    continue
                                evictable_sessions.append((candidate_key, candidate_session))
                            if not evictable_sessions:
                                break
                            lru_key, lru_session = min(
                                evictable_sessions,
                                key=lambda item: _http_bridge_eviction_priority(item[1]),
                            )
                            _log_http_bridge_event(
                                "evict_lru",
                                lru_key,
                                account_id=lru_session.account.id,
                                model=lru_session.request_model,
                                cache_key_family=lru_key.affinity_kind,
                                model_class=_extract_model_class(lru_session.request_model)
                                if lru_session.request_model
                                else None,
                            )
                            detached = self._detach_http_bridge_session_locked(lru_key, expected_session=lru_session)
                            if detached is not None:
                                sessions_to_close_before_create.append(detached)
                        if len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= max_sessions:
                            if self._http_bridge_inflight_sessions:
                                capacity_wait_future = next(iter(self._http_bridge_inflight_sessions.values()))
                            else:
                                _log_http_bridge_event(
                                    "capacity_exhausted_active_sessions",
                                    key,
                                    account_id=None,
                                    model=request_model,
                                    pending_count=(
                                        len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions)
                                    ),
                                    cache_key_family=key.affinity_kind,
                                    model_class=_extract_model_class(request_model) if request_model else None,
                                )
                                raise ProxyResponseError(
                                    429,
                                    local_overload_error(
                                        "HTTP responses session bridge has no idle capacity",
                                        code="capacity_exhausted_active_sessions",
                                    ),
                                )
                        else:
                            inflight_future = asyncio.get_running_loop().create_future()
                            self._http_bridge_inflight_sessions[key] = inflight_future
                            owns_creation = True

            try:
                for session_to_close in sessions_to_close_before_create:
                    await self._close_http_bridge_session_bounded(session_to_close, reason="registry_detach")
            except BaseException as exc:
                if owns_creation:
                    await self._fail_http_bridge_inflight_session_creation(key, inflight_future, exc)
                raise

            if session_to_return_after_close is not None:
                return session_to_return_after_close

            if owner_forward is not None:
                return owner_forward

            if owner_mismatch_error is not None:
                raise owner_mismatch_error

            if continuity_error is not None:
                raise continuity_error

            if capacity_wait_future is not None:
                wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(settings)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(capacity_wait_future),
                        timeout=wait_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    if capacity_wait_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_capacity",
                        code="capacity_exhausted_active_sessions",
                    )
                    stale_key = await self._evict_http_bridge_inflight_waiter(capacity_wait_future, timeout_error)
                    _log_http_bridge_startup_wait_timeout(
                        stage="capacity",
                        timeout_seconds=wait_timeout_seconds,
                        key=stale_key or key,
                        request_model=request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise timeout_error from exc
                except ProxyResponseError:
                    raise
                except Exception:
                    pass
                continue

            if inflight_future is not None and not owns_creation:
                wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(settings)
                try:
                    session = await asyncio.wait_for(
                        asyncio.shield(inflight_future),
                        timeout=wait_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    if inflight_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_inflight_session",
                        code="capacity_exhausted_active_sessions",
                    )
                    await self._fail_http_bridge_inflight_session_creation(key, inflight_future, timeout_error)
                    _log_http_bridge_startup_wait_timeout(
                        stage="inflight_session",
                        timeout_seconds=wait_timeout_seconds,
                        key=key,
                        request_model=request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise timeout_error from exc
                except Exception:
                    raise
                if session is None:
                    continue
                if (
                    not session.closed
                    and session.account.status == AccountStatus.ACTIVE
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
                        require_preferred_account=not fallback_on_preferred_account_unavailable,
                    )
                ):
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        session.api_key = api_key
                        session.request_model = request_model
                        session.last_used_at = _service_time().monotonic()
                        return session
                if not session.closed and session.account.status == AccountStatus.ACTIVE:
                    old_account_id = session.account.id
                    retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(session)
                    async with self._http_bridge_lock:
                        detached = self._detach_http_bridge_session_locked(
                            key,
                            expected_session=session,
                            mark_closed=not retiring_with_visible_requests,
                        )
                    if detached is not None:
                        force_durable_takeover_after_detach = True
                    if detached is not None and not retiring_with_visible_requests:
                        self._schedule_http_bridge_session_closes(
                            [detached],
                            reason="registry_detach",
                        )
                continue

            created_session: _HTTPBridgeSession | None = None
            session_registered = False
            require_preferred_account = (previous_response_id is not None and preferred_account_id is not None) or (
                preferred_account_id is not None and not fallback_on_preferred_account_unavailable
            )
            try:
                create_session = self._create_http_bridge_session
                create_kwargs: dict[str, Any] = {
                    "headers": headers,
                    "affinity": affinity,
                    "api_key": api_key,
                    "request_model": request_model,
                    "idle_ttl_seconds": effective_idle_ttl_seconds,
                    "request_stage": request_stage,
                    "preferred_account_id": preferred_account_id,
                    "require_preferred_account": require_preferred_account,
                    "fallback_on_preferred_account_unavailable": fallback_on_preferred_account_unavailable,
                    "request_usage_budget": request_usage_budget,
                }
                try:
                    create_signature = inspect.signature(create_session)
                except (TypeError, ValueError):
                    create_signature = None
                create_accepts_var_keyword = create_signature is not None and any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in create_signature.parameters.values()
                )
                if (
                    create_signature is not None
                    and not create_accepts_var_keyword
                    and "request_usage_budget" not in create_signature.parameters
                ):
                    create_kwargs.pop("request_usage_budget", None)
                created_session = await create_session(key, **create_kwargs)
                await self._claim_durable_http_bridge_session(
                    created_session,
                    allow_takeover=force_durable_takeover or _http_bridge_allow_durable_takeover(durable_lookup),
                    force_owner_epoch_advance=force_durable_takeover,
                )
                async with self._http_bridge_lock:
                    current_future = self._http_bridge_inflight_sessions.get(key)
                    if current_future is inflight_future:
                        self._http_bridge_inflight_sessions.pop(key, None)
                        self._http_bridge_sessions[key] = created_session
                        session_registered = True
                        if inflight_future is not None and not inflight_future.done():
                            inflight_future.set_result(created_session)
                if not session_registered:
                    raise _http_bridge_startup_wait_timeout_error(
                        "http_bridge_session_registration",
                        code="capacity_exhausted_active_sessions",
                    )
            except BaseException as exc:
                async with self._http_bridge_lock:
                    current_future = self._http_bridge_inflight_sessions.get(key)
                    if current_future is inflight_future:
                        self._http_bridge_inflight_sessions.pop(key, None)
                        if inflight_future is not None and not inflight_future.done():
                            if isinstance(exc, asyncio.CancelledError):
                                inflight_future.cancel()
                            else:
                                inflight_future.set_exception(exc)
                                inflight_future.exception()
                if created_session is not None and not session_registered:
                    await self._close_http_bridge_session(created_session)
                raise
            assert created_session is not None
            _log_http_bridge_event(
                "create",
                key,
                account_id=created_session.account.id,
                model=created_session.request_model,
                detail=(
                    f"request_stage={request_stage}, preferred_account_id={preferred_account_id}, "
                    f"selected_account_id={created_session.account.id}, "
                    f"durable_session_id={created_session.durable_session_id}"
                ),
                cache_key_family=key.affinity_kind,
                model_class=_extract_model_class(created_session.request_model)
                if created_session.request_model
                else None,
            )
            if old_account_id is not None and old_account_id != created_session.account.id:
                _log_http_bridge_event(
                    "reallocation_orphan",
                    key,
                    account_id=created_session.account.id,
                    model=created_session.request_model,
                    detail=f"old_account={old_account_id}",
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(created_session.request_model)
                    if created_session.request_model
                    else None,
                )
            return created_session

    async def close_all_http_bridge_sessions(self) -> None:
        async with self._http_bridge_lock:
            sessions_to_close = list(self._http_bridge_sessions.values())
            inflight_futures = list(self._http_bridge_inflight_sessions.values())
            self._http_bridge_sessions.clear()
            self._http_bridge_inflight_sessions.clear()
            self._http_bridge_previous_response_index.clear()

        shutdown_error = ProxyResponseError(
            503,
            openai_error(
                "upstream_unavailable",
                "HTTP responses session bridge is shutting down",
                error_type="server_error",
            ),
        )
        for inflight_future in inflight_futures:
            if inflight_future.done():
                continue
            inflight_future.set_exception(shutdown_error)
            inflight_future.exception()

        for session in sessions_to_close:
            await self._close_http_bridge_session(session)
        await self._drain_http_bridge_background_cleanup_tasks(reason="shutdown")

    async def mark_http_bridge_draining(self) -> None:
        try:
            await self._durable_bridge.mark_instance_draining(
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            )
        except Exception:
            logger.warning("Failed to mark durable HTTP bridge sessions draining", exc_info=True)

    def _prune_http_bridge_sessions_locked(self) -> list["_HTTPBridgeSession"]:
        now = _service_time().monotonic()
        stale_keys: list[_HTTPBridgeSessionKey] = []
        for key, session in self._http_bridge_sessions.items():
            if session.closed:
                stale_keys.append(key)
                continue
            pending_count = self._http_bridge_pending_count_nowait(
                session,
                context="idle_prune",
            )
            if pending_count is None:
                continue
            if pending_count:
                continue
            if now - session.last_used_at < session.idle_ttl_seconds:
                continue
            stale_keys.append(key)
        sessions_to_close: list[_HTTPBridgeSession] = []
        for key in stale_keys:
            session = self._detach_http_bridge_session_locked(key)
            if session is not None:
                _log_http_bridge_event(
                    "evict_idle",
                    key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                sessions_to_close.append(session)
        return sessions_to_close

    async def _close_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state_lock_held: bool = False,
    ) -> None:
        session.closed = True
        if turn_state_lock_held:
            self._unregister_http_bridge_turn_states_locked(session)
            self._unregister_http_bridge_previous_response_ids_locked(session)
        else:
            await self._unregister_http_bridge_turn_states(session)
            await self._unregister_http_bridge_previous_response_ids(session)
        account_lease = getattr(session, "account_lease", None)
        try:
            await self._load_balancer.release_account_lease(account_lease)
        except Exception:
            logger.warning("Failed to release HTTP bridge account lease during close", exc_info=True)
        finally:
            session.account_lease = None
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.release_live_session(
                    session_id=session.durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    draining=shutdown_state.is_bridge_drain_active(),
                )
            except Exception:
                logger.warning("Failed to release durable HTTP bridge session", exc_info=True)
        upstream_reader = session.upstream_reader
        if upstream_reader is not None:
            if upstream_reader is asyncio.current_task():
                session.upstream_reader = None
            else:
                await _await_cancelled_task(upstream_reader, label="http bridge upstream reader")
                if session.upstream_reader is upstream_reader:
                    session.upstream_reader = None
        try:
            await session.upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket", exc_info=True)
        pending_requests = getattr(session, "pending_requests", None)
        pending_lock = getattr(session, "pending_lock", None)
        response_create_gate = getattr(session, "response_create_gate", None)
        if pending_requests is not None and pending_lock is not None:
            async with pending_lock:
                session.queued_request_count = 0
            await self._fail_pending_websocket_requests(
                account=session.account,
                account_id_value=session.account.id,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="HTTP bridge session closed before response.completed",
                api_key=None,
                response_create_gate=response_create_gate,
            )
        _log_http_bridge_event(
            "close",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    async def _register_http_bridge_turn_state(self, session: "_HTTPBridgeSession", turn_state: str) -> None:
        async with self._http_bridge_lock:
            if session.closed:
                return
            session.downstream_turn_state_aliases.add(turn_state)
            if session.downstream_turn_state is None:
                session.downstream_turn_state = turn_state
            for alias in session.downstream_turn_state_aliases:
                self._http_bridge_turn_state_index[_http_bridge_turn_state_alias_key(alias, session.key.api_key_id)] = (
                    session.key
                )
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_turn_state(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    turn_state=turn_state,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge turn-state alias", exc_info=True)

    async def _register_http_bridge_previous_response_id(
        self,
        session: "_HTTPBridgeSession",
        response_id: str,
        *,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
    ) -> None:
        stripped_response_id = response_id.strip()
        if not stripped_response_id:
            return
        async with self._http_bridge_lock:
            if session.closed:
                return
            if (
                session.upstream_control.retire_after_drain
                and self._http_bridge_sessions.get(session.key) is not session
            ):
                return
            alias_key = _http_bridge_previous_response_alias_key(stripped_response_id, session.key.api_key_id)
            self._http_bridge_previous_response_index[alias_key] = session.key
            session.previous_response_ids.add(stripped_response_id)
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_previous_response_id(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    response_id=stripped_response_id,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    input_item_count=input_item_count,
                    input_full_fingerprint=input_full_fingerprint,
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge previous_response_id alias", exc_info=True)

    async def _unregister_http_bridge_turn_states(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_turn_states_locked(session)

    async def _unregister_http_bridge_previous_response_ids(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_previous_response_ids_locked(session)

    def _detach_http_bridge_session_locked(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        expected_session: "_HTTPBridgeSession | None" = None,
        mark_closed: bool = True,
    ) -> "_HTTPBridgeSession | None":
        session = self._http_bridge_sessions.get(key)
        if session is None:
            return None
        if expected_session is not None and session is not expected_session:
            return None
        self._http_bridge_sessions.pop(key, None)
        if mark_closed:
            session.closed = True
        self._unregister_http_bridge_turn_states_locked(session)
        self._unregister_http_bridge_previous_response_ids_locked(session)
        return session

    def _unregister_http_bridge_turn_states_locked(self, session: "_HTTPBridgeSession") -> None:
        aliases = tuple(session.downstream_turn_state_aliases)
        current_session = self._http_bridge_sessions.get(session.key)
        for alias in aliases:
            alias_key = _http_bridge_turn_state_alias_key(alias, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and alias in current_session.downstream_turn_state_aliases
            ):
                continue
            if self._http_bridge_turn_state_index.get(alias_key) == session.key:
                self._http_bridge_turn_state_index.pop(alias_key, None)
        session.downstream_turn_state_aliases.clear()

    def _unregister_http_bridge_previous_response_ids_locked(self, session: "_HTTPBridgeSession") -> None:
        response_ids = tuple(session.previous_response_ids)
        current_session = self._http_bridge_sessions.get(session.key)
        for response_id in response_ids:
            alias_key = _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and response_id in current_session.previous_response_ids
            ):
                continue
            if self._http_bridge_previous_response_index.get(alias_key) == session.key:
                self._http_bridge_previous_response_index.pop(alias_key, None)
        session.previous_response_ids.clear()

    def _promote_http_bridge_session_to_codex_affinity(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state: str,
        settings: Settings,
    ) -> None:
        session.affinity = _AffinityPolicy(key=turn_state, kind=StickySessionKind.CODEX_SESSION)
        session.codex_session = True
        session.downstream_turn_state = turn_state
        session.downstream_turn_state_aliases.add(turn_state)
        session.idle_ttl_seconds = max(
            session.idle_ttl_seconds,
            float(settings.http_responses_session_bridge_codex_idle_ttl_seconds),
        )
        session.headers = _headers_with_turn_state(session.headers, turn_state)

    async def _claim_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
    ) -> None:
        current_instance = _service_get_settings().http_responses_session_bridge_instance_id
        try:
            lookup: DurableBridgeLookup | None = None
            for claim_attempt in range(2):
                lookup = await self._durable_bridge.claim_live_session(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    instance_id=current_instance,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    account_id=session.account.id,
                    model=session.request_model,
                    service_tier=None,
                    latest_turn_state=session.downstream_turn_state,
                    latest_response_id=None,
                    allow_takeover=allow_takeover,
                    force_owner_epoch_advance=force_owner_epoch_advance or claim_attempt > 0,
                )
                if lookup.owner_instance_id == current_instance:
                    break
                if not allow_takeover or claim_attempt > 0:
                    break
                await asyncio.sleep(0)
            assert lookup is not None
            if lookup.owner_instance_id != current_instance:
                _log_http_bridge_event(
                    "owner_mismatch_retry",
                    session.key,
                    account_id=None,
                    model=session.request_model,
                    detail=(
                        "expected_instance="
                        f"{lookup.owner_instance_id}, current_instance={current_instance}, outcome=claim_rejected"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    owner_check_applied=True,
                )
                if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
                    bridge_instance_mismatch_total.labels(outcome="retry").inc()
                raise ProxyResponseError(
                    409,
                    openai_error(
                        "bridge_instance_mismatch",
                        "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                        error_type="server_error",
                    ),
                )
            session.durable_session_id = lookup.session_id
            session.durable_owner_epoch = lookup.owner_epoch
            session.headers = _headers_with_turn_state(session.headers, session.downstream_turn_state)
            if (
                PROMETHEUS_AVAILABLE
                and bridge_durable_recover_total is not None
                and allow_takeover
                and lookup.owner_epoch > 1
            ):
                bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _record_bridge_reattach(path="restart_takeover", outcome="success")
            if session.key.affinity_kind == "session_header":
                await self._durable_bridge.register_session_header(
                    session_id=lookup.session_id,
                    api_key_id=session.key.api_key_id,
                    session_header=session.key.affinity_key,
                )
        except Exception as exc:
            if _is_missing_durable_bridge_table_error(exc):
                logger.warning("Durable bridge tables missing; using in-memory bridge session fallback", exc_info=True)
                return
            raise

    async def _refresh_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
    ) -> None:
        if session.durable_session_id is None or session.durable_owner_epoch is None:
            return
        try:
            lookup = await self._durable_bridge.renew_live_session(
                session_id=session.durable_session_id,
                api_key_id=session.key.api_key_id,
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                owner_epoch=session.durable_owner_epoch,
                lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                latest_turn_state=session.downstream_turn_state,
                latest_response_id=None,
            )
            if lookup is not None:
                session.durable_owner_epoch = lookup.owner_epoch
        except Exception:
            logger.warning("Failed to renew durable HTTP bridge session lease", exc_info=True)

    async def _create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession":
        request_state = _WebSocketRequestState(
            request_id=f"http_bridge_connect_{uuid4().hex}",
            model=request_model,
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=_service_time().monotonic(),
            transport=_REQUEST_TRANSPORT_HTTP,
        )
        deadline = _websocket_connect_deadline(request_state, _service_get_settings().proxy_request_budget_seconds)
        settings = await _service_get_settings_cache().get()
        excluded_account_ids: set[str] = set()
        retry_same_account_once = preferred_account_id is not None
        preferred_candidate_id = preferred_account_id
        selected_account_lease: AccountLease | None = None
        while True:
            select_kwargs = {
                "request_id": request_state.request_log_id or request_state.request_id,
                "kind": "http_bridge",
                "request_stage": request_stage,
                "api_key": api_key,
                "sticky_key": affinity.key,
                "sticky_kind": affinity.kind,
                "reallocate_sticky": affinity.reallocate_sticky,
                "sticky_max_age_seconds": affinity.max_age_seconds,
                "prefer_earlier_reset_accounts": settings.prefer_earlier_reset_accounts,
                "prefer_earlier_reset_window": _prefer_earlier_reset_window(settings),
                "routing_strategy": _routing_strategy(settings),
                "model": request_model,
                "exclude_account_ids": excluded_account_ids,
                "preferred_account_id": preferred_candidate_id,
                "lease_kind": "stream",
                "estimated_lease_tokens": _estimated_lease_tokens_from_request_usage_budget(request_usage_budget),
                "fallback_on_preferred_account_unavailable": fallback_on_preferred_account_unavailable,
            }
            selection = await self._select_account_with_budget_for_stream(deadline, **select_kwargs)
            selected_account_lease = selection.lease
            account = selection.account
            if account is None:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=None,
                )
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                error_type = "rate_limit_error" if status_code == 429 else "server_error"
                raise ProxyResponseError(
                    status_code,
                    openai_error(
                        selection.error_code or "no_accounts",
                        selection.error_message or "No active accounts available",
                        error_type=error_type,
                    ),
                )
            if require_preferred_account and preferred_account_id is not None and account.id != preferred_account_id:
                message = "Previous response owner account is unavailable; retry later."
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "previous_response_owner_unavailable",
                        message,
                        error_type="server_error",
                    ),
                )
            selected_is_preferred = preferred_account_id is not None and account.id == preferred_account_id
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                upstream = await _call_with_supported_optional_kwargs(
                    self._open_upstream_websocket_with_budget,
                    account,
                    connect_headers,
                    optional_kwargs={"request_state": request_state},
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                break
            except ProxyResponseError as exc:
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    raise
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                    upstream = await self._open_upstream_websocket_with_budget(
                        account,
                        connect_headers,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                    _record_same_account_takeover(
                        preferred_account_id=preferred_account_id,
                        selected_account_id=account.id,
                    )
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    await self._handle_proxy_error(account, retry_exc)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if exc.is_permanent:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    raise ProxyResponseError(
                        401,
                        openai_error(
                            "invalid_api_key",
                            exc.message,
                            error_type="authentication_error",
                        ),
                    ) from exc
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(exc.message or "Temporary upstream refresh failure")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
            except BaseException:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                raise
        session = _HTTPBridgeSession(
            key=key,
            headers=connect_headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            account=account,
            upstream=upstream,
            upstream_control=_WebSocketUpstreamControl(),
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            response_create_gate=asyncio.Semaphore(1),
            queued_request_count=0,
            lifecycle_lock=anyio.Lock(),
            last_used_at=_service_time().monotonic(),
            idle_ttl_seconds=idle_ttl_seconds,
            codex_session=affinity.kind == StickySessionKind.CODEX_SESSION,
            prewarm_lock=anyio.Lock(),
            upstream_turn_state=_upstream_turn_state_from_socket(upstream),
            downstream_turn_state=None,
            account_lease=selected_account_lease,
        )
        _copy_websocket_route_metadata_to_session(session, request_state)
        session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        return session

    async def _submit_http_bridge_request(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        if request_state.response_id is not None or request_state.response_event_count > 0:
            _log_http_bridge_event(
                "submit_after_response_event",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=(
                    f"response_id={request_state.response_id}, "
                    f"response_events_seen={request_state.response_event_count}"
                ),
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            raise ProxyResponseError(
                502,
                openai_error(
                    "upstream_unavailable",
                    "HTTP responses session bridge request already has upstream response events",
                    error_type="server_error",
                ),
            )
        if session.closed:
            async with session.lifecycle_lock:
                if session.closed:
                    current_session = session
                    http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                    bridge_lock = getattr(self, "_http_bridge_lock", None)
                    if bridge_lock is not None:
                        async with bridge_lock:
                            if http_bridge_sessions is not None:
                                current_session = http_bridge_sessions.get(session.key)
                    elif http_bridge_sessions is not None:
                        current_session = http_bridge_sessions.get(session.key)
                    if current_session is None and _http_bridge_key_strength(session.key) == "hard":
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail="session_unregistered_before_reconnect",
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                        )
                    if current_session is not session:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail="session_replaced_before_reconnect",
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                    # Try reconnecting the upstream websocket first.  For requests
                    # carrying previous_response_id we only reconnect (send_request=
                    # False) because the fresh upstream won't recognise the old
                    # response id.  If reconnection itself fails, raise 502 so the
                    # client retries with previous_response_id intact rather than
                    # receiving 400 previous_response_not_found (which causes the
                    # CLI to drop previous_response_id and resend the full
                    # conversation history, inflating per-turn context by ~20x).
                    recovered = await self._retry_http_bridge_request_on_fresh_upstream(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                        send_request=False,
                    )
                    if recovered:
                        session.closed = False
                    else:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                        )
        if session.upstream_control.retire_after_drain:
            await self._retire_http_bridge_after_drain_if_ready(session)
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "HTTP responses session bridge is retiring"),
            )
        await self._maybe_prewarm_http_bridge_session(
            session,
            request_state=request_state,
            text_data=text_data,
        )
        gate_acquired = False
        request_enqueued = False
        async with session.pending_lock:
            if session.queued_request_count >= queue_limit:
                _log_http_bridge_event(
                    "bridge_queue_full",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    pending_count=session.queued_request_count,
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                raise ProxyResponseError(
                    429,
                    openai_error(
                        "bridge_queue_full",
                        "HTTP responses session bridge queue is full",
                        error_type="rate_limit_error",
                    ),
                )
            session.queued_request_count += 1
        try:
            text_data = await self._inline_http_bridge_image_urls(text_data, request_state)
            self._start_request_state_api_key_reservation_heartbeat(
                request_state,
                api_key=request_state.api_key,
                surface="http_bridge",
            )
            _copy_websocket_route_metadata_from_session(request_state, session)
            await self._acquire_request_state_response_create_admission(
                request_state,
                response_create_gate=session.response_create_gate,
                account_id=session.account.id,
                surface="http_bridge",
                bridge_session=session,
            )
            gate_acquired = True
            async with session.lifecycle_lock:
                current_session = session
                http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                bridge_lock = getattr(self, "_http_bridge_lock", None)
                if bridge_lock is not None:
                    async with bridge_lock:
                        if http_bridge_sessions is not None:
                            current_session = http_bridge_sessions.get(session.key)
                elif http_bridge_sessions is not None:
                    current_session = http_bridge_sessions.get(session.key)
                session_unregistered = current_session is None and _http_bridge_key_strength(session.key) == "hard"
                session_replaced = current_session is not None and current_session is not session
                if session.closed or session_unregistered or session_replaced:
                    _log_http_bridge_event(
                        "submit_on_closed",
                        session.key,
                        account_id=session.account.id,
                        model=session.request_model,
                        detail=(
                            "session_retired_after_admission"
                            if session.closed
                            else (
                                "session_unregistered_after_admission"
                                if session_unregistered
                                else "session_replaced_after_admission"
                            )
                        ),
                        cache_key_family=session.key.affinity_kind,
                        model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    )
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                    )
                async with session.pending_lock:
                    session.pending_requests.append(request_state)
                request_enqueued = True
                await session.upstream.send_text(text_data)
                session.last_used_at = _service_time().monotonic()
        except ProxyResponseError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            raise
        except asyncio.CancelledError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            raise
        except Exception as exc:
            _log_http_bridge_event(
                "send_failure",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=str(exc) or None,
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            retried = await self._retry_http_bridge_request_on_fresh_upstream(
                session,
                request_state=request_state,
                text_data=text_data,
            )
            if retried:
                return
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            await self._fail_pending_websocket_requests(
                account=session.account,
                account_id_value=session.account.id,
                pending_requests=deque([request_state]),
                pending_lock=anyio.Lock(),
                error_code="stream_incomplete",
                error_message="Upstream websocket closed before response.completed",
                api_key=None,
                response_create_gate=session.response_create_gate,
            )
            session.closed = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close HTTP bridge upstream websocket after send failure", exc_info=True)
            # Always raise 502 so the client can retry with
            # previous_response_id intact.  Returning 400
            # previous_response_not_found causes the client to drop
            # previous_response_id and resend the full conversation
            # history, inflating per-turn context by ~20x.
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", str(exc) or "Upstream websocket closed"),
            ) from exc

    async def _maybe_prewarm_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> None:
        if (
            not session.codex_session
            or session.prewarmed
            or request_state.previous_response_id is not None
            or not getattr(_service_get_settings(), "http_responses_session_bridge_codex_prewarm_enabled", False)
        ):
            return
        prewarm_lock = session.prewarm_lock
        if prewarm_lock is None:
            return
        async with prewarm_lock:
            if session.prewarmed:
                return
            warmup_text = _build_http_bridge_prewarm_text(text_data)
            session.prewarmed = True
            if warmup_text is None:
                return

            warmup_state = _WebSocketRequestState(
                request_id=f"http_prewarm_{uuid4().hex}",
                model=request_state.model,
                service_tier=request_state.service_tier,
                reasoning_effort=request_state.reasoning_effort,
                api_key_reservation=None,
                started_at=_service_time().monotonic(),
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                awaiting_response_created=True,
                event_queue=asyncio.Queue(),
                transport=_REQUEST_TRANSPORT_HTTP,
                request_text=warmup_text,
                skip_request_log=True,
            )
            gate_acquired = False
            request_enqueued = False
            try:
                event_queue = warmup_state.event_queue
                assert event_queue is not None
                await self._acquire_request_state_response_create_admission(
                    warmup_state,
                    response_create_gate=session.response_create_gate,
                    account_id=session.account.id,
                    surface="http_bridge_prewarm",
                    bridge_session=session,
                )
                gate_acquired = True
                async with session.lifecycle_lock:
                    current_session = session
                    http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                    bridge_lock = getattr(self, "_http_bridge_lock", None)
                    if bridge_lock is not None:
                        async with bridge_lock:
                            if http_bridge_sessions is not None:
                                current_session = http_bridge_sessions.get(session.key)
                    elif http_bridge_sessions is not None:
                        current_session = http_bridge_sessions.get(session.key)
                    session_replaced = current_session is not session
                    if session.closed or session_replaced:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail=(
                                "prewarm_session_retired_after_admission"
                                if session.closed
                                else "prewarm_session_replaced_after_admission"
                            ),
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        session.prewarmed = False
                        await self._cleanup_http_bridge_submit_interruption(
                            session,
                            request_state=warmup_state,
                            gate_acquired=gate_acquired,
                            request_enqueued=request_enqueued,
                            counted_in_queue=False,
                        )
                        gate_acquired = False
                        return
                    async with session.pending_lock:
                        session.pending_requests.append(warmup_state)
                    request_enqueued = True
                    await session.upstream.send_text(warmup_text)
                while True:
                    try:
                        event_block = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=_prewarm_response_timeout_seconds(),
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "HTTP bridge prewarm timed out request_id=%s model=%s",
                            request_state.request_id,
                            request_state.model,
                        )
                        session.prewarmed = False
                        try:
                            # The warmup request has already been sent upstream.  Close/reconnect the
                            # socket while the warmup state is still attached so any late warmup
                            # response cannot be assigned to the next visible request on this session.
                            await self._reconnect_http_bridge_session(
                                session,
                                request_state=request_state,
                                restart_reader=True,
                            )
                        except Exception:
                            session.closed = True
                            raise
                        finally:
                            async with session.pending_lock:
                                if warmup_state in session.pending_requests:
                                    session.pending_requests.remove(warmup_state)
                            self._cancel_request_state_api_key_reservation_heartbeat(warmup_state)
                            if gate_acquired:
                                await _release_websocket_response_create_gate(
                                    warmup_state,
                                    session.response_create_gate,
                                )
                        return
                    if event_block is None:
                        break
                    payload = parse_sse_data_json(event_block)
                    event = parse_sse_event(event_block)
                    event_type = _event_type_from_payload(event, payload)
                    if event_type in {"response.failed", "response.incomplete", "error"}:
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "upstream_unavailable",
                                "HTTP responses session bridge prewarm failed",
                            ),
                        )
                session.last_used_at = _service_time().monotonic()
            except ProxyResponseError as exc:
                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(error.code if error else None, error.type if error else None)
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=False,
                )
                if is_local_overload_error_code(code):
                    session.prewarmed = False
                    return
                session.prewarmed = False
                raise
            except BaseException:
                session.prewarmed = False
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=False,
                )
                raise

    async def _cleanup_http_bridge_submit_interruption(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        gate_acquired: bool,
        request_enqueued: bool,
        counted_in_queue: bool,
    ) -> None:
        async with session.pending_lock:
            if request_enqueued and request_state in session.pending_requests:
                session.pending_requests.remove(request_state)
            if counted_in_queue:
                session.queued_request_count = max(0, session.queued_request_count - 1)
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        if gate_acquired:
            await _release_websocket_response_create_gate(request_state, session.response_create_gate)

    async def _detach_http_bridge_request(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
    ) -> bool:
        detached = False
        async with session.pending_lock:
            if request_state in session.pending_requests and not request_state.draining_until_terminal:
                request_state.draining_until_terminal = True
                request_state.downstream_visible = False
                session.queued_request_count = max(0, session.queued_request_count - 1)
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                detached = True
        request_state.event_queue = None
        # event_queue is nulled unconditionally because by the time
        # _detach is called from the finally block in
        # _stream_http_bridge_session_events, the terminal event has
        # already been delivered via _pop_terminal_websocket_request_state.
        # A late-arriving event on a nulled queue is a no-op.
        await _release_websocket_response_create_gate(request_state, session.response_create_gate)
        if not detached:
            return False
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        await self._release_websocket_request_state_reservation(request_state)
        request_state.api_key_reservation = None
        await self._retire_http_bridge_after_drain_if_ready(session)
        return True

    async def _retire_http_bridge_after_drain_if_ready(self, session: "_HTTPBridgeSession") -> bool:
        if not (session.upstream_control.reconnect_requested and session.upstream_control.retire_after_drain):
            return False
        async with session.pending_lock:
            has_visible_pending = any(
                _http_bridge_request_counts_against_queue(request_state) for request_state in session.pending_requests
            )
            should_reconnect = not has_visible_pending and session.queued_request_count == 0
            if should_reconnect:
                session.pending_requests.clear()
        if not should_reconnect:
            return False

        await self._close_http_bridge_session(session)
        return True

    async def _retire_stale_pending_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        detail: str,
    ) -> None:
        session.closed = True
        async with self._http_bridge_lock:
            if self._http_bridge_sessions.get(session.key) is session:
                self._http_bridge_sessions.pop(session.key, None)
                self._unregister_http_bridge_turn_states_locked(session)
                self._unregister_http_bridge_previous_response_ids_locked(session)
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            durable_session_id = session.durable_session_id
            durable_owner_epoch = session.durable_owner_epoch
            session.durable_session_id = None
            session.durable_owner_epoch = None
            try:
                await self._durable_bridge.release_live_session(
                    session_id=durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=durable_owner_epoch,
                    draining=shutdown_state.is_bridge_drain_active(),
                )
            except Exception:
                session.durable_session_id = durable_session_id
                session.durable_owner_epoch = durable_owner_epoch
                logger.warning("Failed to release stale pending HTTP bridge session lease", exc_info=True)
        await self._load_balancer.release_account_lease(session.account_lease)
        session.account_lease = None
        if not session.upstream_close_attempted:
            session.upstream_close_attempted = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close stale pending HTTP bridge upstream websocket", exc_info=True)
        _log_http_bridge_event(
            "retire_stale_pending",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            detail=detail,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    async def _relay_http_bridge_upstream_messages(
        self,
        session: "_HTTPBridgeSession",
    ) -> None:
        runtime_settings = _service_get_settings()
        try:
            while True:
                receive_timeout = await self._next_websocket_receive_timeout(
                    session.pending_requests,
                    pending_lock=session.pending_lock,
                    proxy_request_budget_seconds=runtime_settings.http_responses_session_bridge_request_budget_seconds,
                    stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                )
                try:
                    if receive_timeout is None:
                        message = await session.upstream.receive()
                    elif receive_timeout.timeout_seconds <= 0:
                        raise asyncio.TimeoutError()
                    else:
                        message = await asyncio.wait_for(
                            session.upstream.receive(),
                            timeout=receive_timeout.timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    if receive_timeout is None:
                        raise
                    retried = await self._retry_http_bridge_precreated_request(session)
                    if retried:
                        continue
                    async with session.lifecycle_lock:
                        try:
                            session.closed = True
                            async with session.pending_lock:
                                session.queued_request_count = 0
                            await self._fail_pending_websocket_requests(
                                account=session.account,
                                account_id_value=session.account.id,
                                pending_requests=session.pending_requests,
                                pending_lock=session.pending_lock,
                                error_code=receive_timeout.error_code,
                                error_message=receive_timeout.error_message,
                                api_key=None,
                                response_create_gate=session.response_create_gate,
                            )
                        finally:
                            await self._retire_stale_pending_http_bridge_session(
                                session,
                                detail=receive_timeout.error_code,
                            )
                    break

                if message.kind == "text" and message.text is not None:
                    session.last_upstream_close_code = None
                    await self._process_http_bridge_upstream_text(session, message.text)
                    if await self._retire_http_bridge_after_drain_if_ready(session):
                        break
                    continue

                session.last_upstream_close_code = message.close_code
                retried = await self._retry_http_bridge_precreated_request(session)
                if retried:
                    continue
                async with session.lifecycle_lock:
                    try:
                        session.closed = True
                        async with session.pending_lock:
                            session.queued_request_count = 0
                        await self._fail_pending_websocket_requests(
                            account=session.account,
                            account_id_value=session.account.id,
                            pending_requests=session.pending_requests,
                            pending_lock=session.pending_lock,
                            error_code="stream_incomplete",
                            error_message=_upstream_websocket_disconnect_message(message),
                            api_key=None,
                            response_create_gate=session.response_create_gate,
                        )
                    finally:
                        await self._retire_stale_pending_http_bridge_session(
                            session,
                            detail="stream_incomplete",
                        )
                break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "HTTP bridge upstream reader crashed account_id=%s bridge_kind=%s",
                session.account.id,
                session.key.affinity_kind,
                exc_info=True,
            )
            async with session.lifecycle_lock:
                try:
                    session.closed = True
                    async with session.pending_lock:
                        session.queued_request_count = 0
                    await self._fail_pending_websocket_requests(
                        account=session.account,
                        account_id_value=session.account.id,
                        pending_requests=session.pending_requests,
                        pending_lock=session.pending_lock,
                        error_code="stream_incomplete",
                        error_message="HTTP bridge upstream reader crashed before response.completed",
                        api_key=None,
                        response_create_gate=session.response_create_gate,
                    )
                finally:
                    await self._retire_stale_pending_http_bridge_session(
                        session,
                        detail="reader_crash",
                    )
        finally:
            session.closed = True

    async def _retry_http_bridge_request_on_fresh_upstream(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        send_request: bool = True,
    ) -> bool:
        retry_text_data = text_data
        if request_state.previous_response_id is not None and send_request:
            # After an ambiguous websocket send failure we cannot prove whether
            # upstream already accepted the continuation. Re-sending the same
            # previous_response_id request can fork continuity with duplicate
            # child responses, so only reconnect-without-resend is allowed.
            # The single exception is proxy-injected anchors on trim-safe
            # full-resend payloads: dropping the anchor and replaying the
            # original unanchored request is equivalent to the client's own
            # retry. Session-level injections do not opt in because their
            # payload may depend on the anchor for context preservation.
            if (
                not request_state.proxy_injected_previous_response_id
                or not request_state.fresh_upstream_request_text
                or not request_state.fresh_upstream_request_is_retry_safe
            ):
                return False
            retry_text_data = request_state.fresh_upstream_request_text
        if request_state.replay_count >= 1:
            return False
        if request_state.response_event_count > 0:
            return False
        request_state.replay_count += 1
        _log_http_bridge_event(
            "retry_fresh_upstream",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                restart_reader=True,
            )
            if send_request:
                if retry_text_data != text_data:
                    request_state.previous_response_id = None
                    request_state.proxy_injected_previous_response_id = False
                    request_state.request_text = retry_text_data
                await session.upstream.send_text(retry_text_data)
            _clear_websocket_request_error_overrides(request_state)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception:
            logger.warning("HTTP bridge retry on fresh upstream failed", exc_info=True)
            return False

    async def _retry_http_bridge_precreated_request(self, session: "_HTTPBridgeSession") -> bool:
        async with session.pending_lock:
            retryable_requests = [
                request_state
                for request_state in session.pending_requests
                if not request_state.draining_until_terminal
                and _websocket_request_can_replay_before_visible_output(request_state)
            ]
            if len(retryable_requests) != 1:
                return False
            request_state = retryable_requests[0]
            if request_state.previous_response_id is not None and not (
                request_state.proxy_injected_previous_response_id
                and request_state.fresh_upstream_request_is_retry_safe
                and request_state.fresh_upstream_request_text
            ):
                # Once a continuation is pending upstream, reconnecting without
                # replay cannot complete the current request, while replaying it
                # is unsafe without upstream idempotency guarantees. Proxy-
                # injected retry-safe anchors are equivalent to the client's own
                # full resend once the anchor is stripped.
                return False
            close_classification = _classify_upstream_close(
                session.last_upstream_close_code,
                response_events_seen=request_state.response_event_count,
            )
            if close_classification == "rejected":
                request_state.error_code_override = "upstream_rejected_input"
                request_state.error_http_status_override = 502
                request_state.error_message_override = (
                    "Upstream rejected the request before response.created "
                    f"(close_code={session.last_upstream_close_code})"
                )
                return False
            request_text = _prepare_websocket_request_state_for_visible_output_replay(request_state)
            if request_text is None:
                return False
        _log_http_bridge_event(
            "retry_precreated",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(session, request_state=request_state)
            await session.upstream.send_text(request_text)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception as exc:
            request_state.error_code_override, request_state.error_message_override = (
                _http_bridge_precreated_retry_failure_error(exc)
            )
            if isinstance(exc, ProxyResponseError):
                logger.info(
                    "HTTP bridge pre-created retry failed with terminal proxy error code=%s message=%s",
                    request_state.error_code_override,
                    request_state.error_message_override,
                )
            else:
                logger.warning("HTTP bridge pre-created retry failed", exc_info=True)
            return False

    async def _retry_http_bridge_precreated_auth_request(
        self,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
        *,
        error_message: str | None,
    ) -> Literal["not_replayable", "retried", "failed"]:
        permanent_failure_code = _websocket_auth_failure_permanent_code(error_message)
        request_text = _prepare_websocket_request_state_for_auth_replay(request_state)
        if request_text is None:
            await self._load_balancer.mark_permanent_failure(session.account, permanent_failure_code)
            setattr(request_state, "account_health_error_handled", True)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(session.account.id)
            return "not_replayable"

        if _websocket_auth_failure_requires_reauth(error_message):
            failure_code = permanent_failure_code
        elif request_state.auth_replay_counts_by_account.get(session.account.id, 0) == 0:
            failure_code = None
            request_state.auth_replay_counts_by_account[session.account.id] = 1
            request_state.force_refresh_account_id = session.account.id
            request_state.preferred_account_id = session.account.id
        else:
            failure_code = _WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE

        if failure_code is not None:
            await self._load_balancer.mark_permanent_failure(session.account, failure_code)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(session.account.id)

        async with session.pending_lock:
            if request_state not in session.pending_requests:
                session.pending_requests.appendleft(request_state)
                session.queued_request_count += 1

        _log_http_bridge_event(
            "retry_precreated_auth",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(session, request_state=request_state)
            await session.upstream.send_text(request_text)
            session.last_used_at = _service_time().monotonic()
            return "retried"
        except Exception as exc:
            request_state.error_code_override, request_state.error_message_override = (
                _http_bridge_precreated_retry_failure_error(exc)
            )
            if isinstance(exc, ProxyResponseError):
                logger.info(
                    "HTTP bridge pre-created auth retry failed with terminal proxy error code=%s message=%s",
                    request_state.error_code_override,
                    request_state.error_message_override,
                )
            else:
                logger.warning("HTTP bridge pre-created auth retry failed", exc_info=True)
            return "failed"

    async def _retry_http_bridge_security_work_request(
        self,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
    ) -> bool:
        if session.account.security_work_authorized:
            return False
        if request_state.response_id is not None:
            return False
        if request_state.replay_count >= 1:
            return False
        retry_text = request_state.request_text
        if not retry_text:
            return False
        if request_state.previous_response_id is not None:
            if not (
                request_state.fresh_upstream_request_text is not None
                and request_state.fresh_upstream_request_is_retry_safe
            ):
                return False
            retry_text = request_state.fresh_upstream_request_text

        request_state.replay_count += 1
        request_state.response_id = None
        request_state.awaiting_response_created = True
        if retry_text != request_state.request_text:
            request_state.previous_response_id = None
            request_state.proxy_injected_previous_response_id = False
            request_state.request_text = retry_text

        async with session.pending_lock:
            if request_state not in session.pending_requests:
                session.pending_requests.append(request_state)
                session.queued_request_count += 1

        _log_http_bridge_event(
            "retry_security_work_authorized",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                require_security_work_authorized=True,
            )
            await session.upstream.send_text(retry_text)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception as exc:
            logger.warning("HTTP bridge security-work retry failed", exc_info=True)
            if isinstance(exc, ProxyResponseError):
                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(error.code if error else None, error.type if error else None)
                if code == _NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE and request_state.event_queue is not None:
                    await request_state.event_queue.put(
                        format_sse_event(
                            _security_work_advisory_event(
                                code=_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                                message=_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                                request_id=request_state.request_log_id or request_state.request_id,
                                action="forward_original_security_work_error",
                            )
                        )
                    )
            async with session.pending_lock:
                if request_state in session.pending_requests:
                    session.pending_requests.remove(request_state)
                    session.queued_request_count = max(0, session.queued_request_count - 1)
            return False

    async def _reconnect_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        restart_reader: bool = False,
        require_security_work_authorized: bool = False,
    ) -> None:
        old_account_id = session.account.id
        old_upstream = session.upstream
        old_reader = session.upstream_reader if restart_reader else None
        if old_reader is not None:
            if old_reader is not asyncio.current_task():
                cancelled = await _await_cancelled_task(old_reader, label="http bridge upstream reader")
                if not cancelled:
                    session.closed = True
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "upstream_unavailable",
                            "HTTP responses session bridge reader did not shut down cleanly",
                        ),
                    )
        deadline = _websocket_connect_deadline(request_state, _service_get_settings().proxy_request_budget_seconds)
        settings = await _service_get_settings_cache().get()
        session.api_key = request_state.api_key
        skip_same_account = session.last_upstream_close_code in _UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY
        forced_refresh_account_id = request_state.force_refresh_account_id
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        if skip_same_account:
            excluded_account_ids.add(session.account.id)
        retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
        if skip_same_account:
            preferred_candidate_id: str | None = None
        elif forced_refresh_account_id is not None:
            preferred_candidate_id = forced_refresh_account_id
        elif request_state.preferred_account_id is not None:
            preferred_candidate_id = request_state.preferred_account_id
        elif session.account.id not in excluded_account_ids:
            preferred_candidate_id = session.account.id
        else:
            preferred_candidate_id = None
        selected_account_lease: AccountLease | None = None

        async def release_selected_account_lease() -> None:
            nonlocal selected_account_lease
            lease = selected_account_lease
            selected_account_lease = None
            if lease is None:
                return
            if lease is session.account_lease:
                session.account_lease = None
            await self._load_balancer.release_account_lease(lease)

        while True:
            reuse_current_account_lease = (
                preferred_candidate_id == session.account.id and session.account_lease is not None
            )
            selection = await self._select_account_with_budget_for_stream(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="http_bridge",
                request_stage="reattach",
                api_key=session.api_key,
                sticky_key=session.affinity.key,
                sticky_kind=session.affinity.kind,
                reallocate_sticky=session.affinity.reallocate_sticky,
                sticky_max_age_seconds=session.affinity.max_age_seconds,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                routing_strategy=_routing_strategy(settings),
                model=session.request_model,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
                require_security_work_authorized=require_security_work_authorized,
                lease_kind=None if reuse_current_account_lease else "stream",
                estimated_lease_tokens=_estimated_lease_tokens_from_request_usage_budget(
                    request_state.request_usage_budget
                ),
                fallback_on_preferred_account_unavailable=not reuse_current_account_lease,
            )
            account = selection.account
            if account is None:
                await release_selected_account_lease()
                if reuse_current_account_lease and _remaining_budget_seconds(deadline) > 0:
                    preferred_candidate_id = None
                    continue
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=None,
                )
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                raise ProxyResponseError(
                    status_code,
                    openai_error(
                        selection.error_code or "no_accounts",
                        selection.error_message or "No active accounts available",
                        error_type="rate_limit_error" if status_code == 429 else "server_error",
                    ),
                )
            selected_account_lease = (
                session.account_lease
                if reuse_current_account_lease and account.id == session.account.id
                else selection.lease
            )
            selected_is_preferred = account.id == session.account.id
            force_refresh = forced_refresh_account_id == account.id
            if forced_refresh_account_id is not None and account.id != forced_refresh_account_id:
                request_state.force_refresh_account_id = None
                if request_state.preferred_account_id == forced_refresh_account_id:
                    request_state.preferred_account_id = None
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    force=force_refresh,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                if force_refresh and request_state.force_refresh_account_id == account.id:
                    request_state.force_refresh_account_id = None
                connect_headers = _headers_with_turn_state(
                    session.headers,
                    _preferred_http_bridge_reconnect_turn_state(session),
                )
                upstream = await self._open_upstream_websocket_with_budget(
                    account,
                    connect_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                    request_state=request_state,
                )
                _copy_websocket_route_metadata_to_session(session, request_state)
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=account.id,
                )
                break
            except ProxyResponseError as exc:
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await release_selected_account_lease()
                    raise
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _headers_with_turn_state(
                        session.headers,
                        _preferred_http_bridge_reconnect_turn_state(session),
                    )
                    upstream = await self._open_upstream_websocket_with_budget(
                        account,
                        connect_headers,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                    _copy_websocket_route_metadata_to_session(session, request_state)
                    _record_same_account_takeover(
                        preferred_account_id=session.account.id,
                        selected_account_id=account.id,
                    )
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await release_selected_account_lease()
                        raise
                    await self._handle_proxy_error(account, retry_exc)
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                await release_selected_account_lease()
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                await release_selected_account_lease()
                raise
        try:
            await old_upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket before reconnect", exc_info=True)
        if selected_account_lease is not session.account_lease:
            await self._load_balancer.release_account_lease(session.account_lease)
        session.account_lease = selected_account_lease
        session.account = account
        session.headers = connect_headers
        session.upstream = upstream
        session.upstream_control = _WebSocketUpstreamControl()
        session.closed = False
        session.last_upstream_close_code = None
        session.upstream_turn_state = _upstream_turn_state_from_socket(upstream) or session.upstream_turn_state
        if restart_reader:
            session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        _log_http_bridge_event(
            "reconnect",
            session.key,
            account_id=account.id,
            model=session.request_model,
            detail=(
                f"request_stage=reattach, previous_account={old_account_id}, "
                f"preferred_account_id={old_account_id}, selected_account_id={account.id}, "
                f"durable_session_id={session.durable_session_id}"
            ),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    async def _process_http_bridge_upstream_text(
        self,
        session: "_HTTPBridgeSession",
        text: str,
    ) -> None:
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json(event_block)
        event = parse_sse_event(event_block)
        event_type = _event_type_from_payload(event, payload)
        response_id = _websocket_response_id(event, payload)
        error_message = _websocket_event_error_message(event_type, payload)
        is_typeless_error_event = (
            isinstance(payload, dict)
            and not isinstance(payload.get("type"), str)
            and isinstance(payload.get("error"), dict)
        )
        is_previous_response_not_found_event = _is_previous_response_not_found_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        is_missing_tool_output_event = _is_missing_tool_output_error(
            code=_normalize_error_code(
                _websocket_event_error_code(event_type, payload),
                _websocket_event_error_type(event_type, payload),
            ),
            param=_websocket_event_error_param(event_type, payload),
            message=error_message,
        )
        previous_response_id_hint = _previous_response_id_from_not_found_message(error_message)
        text, payload, event, event_type, event_block = rewrite_parallel_tool_call_text(
            text,
            payload,
            event_block=event_block,
        )

        async with session.pending_lock:
            matched_request_state = None
            created_request_state = None
            suppress_downstream_event = False
            has_other_pending_requests = False
            grouped_previous_response_request_states: list[_WebSocketRequestState] = []
            anonymous_event_prefers_draining = event_type not in {"response.failed", "response.incomplete", "error"}
            if event_type == "response.created":
                matched_request_state = _assign_websocket_response_id(session.pending_requests, response_id)
                created_request_state = matched_request_state
                release_create_gate = matched_request_state is not None
            elif response_id is not None:
                matched_request_state = _find_websocket_request_state_by_response_id(
                    session.pending_requests,
                    response_id,
                )
                release_create_gate = False
            elif response_id is None:
                matched_request_state = _match_websocket_request_state_for_anonymous_event(
                    session.pending_requests,
                    prefer_previous_response_not_found=is_previous_response_not_found_event
                    or is_missing_tool_output_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                    prefer_draining_requests=anonymous_event_prefers_draining,
                )
                release_create_gate = False
            else:
                release_create_gate = False

            if matched_request_state is not None:
                actual_service_tier = _service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    matched_request_state.actual_service_tier = actual_service_tier
                    matched_request_state.service_tier = actual_service_tier
                completed_function_call_id = _response_output_item_done_function_call_id(payload)
                if (
                    completed_function_call_id is not None
                    and completed_function_call_id not in matched_request_state.pending_function_call_ids
                ):
                    matched_request_state.pending_function_call_ids.append(completed_function_call_id)
                if mark_duplicate_tool_call_downstream_event(
                    payload,
                    seen_tool_call_keys=matched_request_state.seen_tool_call_keys,
                    response_id=tool_call_response_id_from_payload(payload) or matched_request_state.request_id,
                    scope_side_effects_by_response_id=False,
                ):
                    matched_request_state.suppressed_duplicate_tool_call = True
                    return
                if event_type in _TEXT_DELTA_EVENT_TYPES:
                    matched_request_state.downstream_visible = True
                if event_type == "response.created" and matched_request_state.suppress_next_created_downstream:
                    matched_request_state.suppress_next_created_downstream = False
                    suppress_downstream_event = True
                if payload is not None:
                    payload = _rewrite_websocket_downstream_response_id(payload, matched_request_state)
                    event_block = format_sse_event(payload)

            terminal_request_state = None
            if event_type in {"response.completed", "response.failed", "response.incomplete", "error"}:
                terminal_request_state = _pop_terminal_websocket_request_state(
                    session.pending_requests,
                    response_id=response_id,
                    fallback_request_state=matched_request_state,
                    prefer_previous_response_not_found=is_previous_response_not_found_event
                    or is_missing_tool_output_event,
                    previous_response_id_hint=previous_response_id_hint,
                    error_message=error_message,
                    allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                    allow_precreated_terminal_fallback=event_type
                    in {
                        "response.completed",
                        "response.failed",
                        "response.incomplete",
                        "error",
                    },
                    prefer_draining_requests=anonymous_event_prefers_draining,
                )
                if (
                    matched_request_state is None
                    and terminal_request_state is not None
                    and response_id is not None
                    and event_type == "response.completed"
                    and terminal_request_state.response_id is None
                ):
                    terminal_request_state.response_id = response_id
                    matched_request_state = terminal_request_state
                elif (
                    matched_request_state is None
                    and terminal_request_state is not None
                    and response_id is not None
                    and terminal_request_state.response_id == response_id
                ):
                    matched_request_state = terminal_request_state
                if terminal_request_state is not None and _http_bridge_request_counts_against_queue(
                    terminal_request_state
                ):
                    session.queued_request_count = max(0, session.queued_request_count - 1)
                elif is_previous_response_not_found_event or is_missing_tool_output_event:
                    grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                        session.pending_requests,
                        _matching_websocket_request_states_for_previous_response_error(
                            session.pending_requests,
                            previous_response_id_hint=previous_response_id_hint,
                            error_message=error_message,
                            allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                        ),
                    )
                    if not grouped_previous_response_request_states and is_missing_tool_output_event:
                        grouped_previous_response_request_states = _pop_matching_websocket_request_states(
                            session.pending_requests,
                            _matching_websocket_request_states_for_missing_tool_output_error(
                                session.pending_requests,
                            ),
                        )
                    if grouped_previous_response_request_states:
                        grouped_counted_requests = sum(
                            1
                            for grouped_request_state in grouped_previous_response_request_states
                            if _http_bridge_request_counts_against_queue(grouped_request_state)
                        )
                        session.queued_request_count = max(
                            0,
                            session.queued_request_count - grouped_counted_requests,
                        )
                if (
                    terminal_request_state is None
                    and event_type == "error"
                    and is_typeless_error_event
                    and not grouped_previous_response_request_states
                ):
                    grouped_previous_response_request_states = list(session.pending_requests)
                    session.pending_requests.clear()
                    if grouped_previous_response_request_states:
                        grouped_counted_requests = sum(
                            1
                            for grouped_request_state in grouped_previous_response_request_states
                            if _http_bridge_request_counts_against_queue(grouped_request_state)
                        )
                        session.queued_request_count = max(
                            0,
                            session.queued_request_count - grouped_counted_requests,
                        )
                has_other_pending_requests = bool(session.pending_requests)

        if len(grouped_previous_response_request_states) > 1:
            session.upstream_control.reconnect_requested = True
            grouped_error_reason = (
                "previous_response_not_found"
                if is_previous_response_not_found_event
                else "missing_tool_output"
                if is_missing_tool_output_event
                else "stream_incomplete"
            )
            for grouped_request_state in grouped_previous_response_request_states:
                grouped_request_state.error_http_status_override = 502
                (
                    _grouped_downstream_text,
                    grouped_event_block,
                    grouped_event,
                    grouped_payload,
                    grouped_event_type,
                ) = _build_stream_incomplete_terminal_event_for_request(
                    grouped_request_state,
                    reason=grouped_error_reason,
                )
                if grouped_request_state.event_queue is not None:
                    await grouped_request_state.event_queue.put(grouped_event_block)
                    await grouped_request_state.event_queue.put(None)
                await self._finalize_websocket_request_state(
                    grouped_request_state,
                    account=session.account,
                    account_id_value=session.account.id,
                    event=grouped_event,
                    event_type=grouped_event_type,
                    payload=grouped_payload,
                    api_key=grouped_request_state.api_key,
                    upstream_control=session.upstream_control,
                    response_create_gate=session.response_create_gate,
                )
            return

        if len(grouped_previous_response_request_states) == 1 and terminal_request_state is None:
            terminal_request_state = grouped_previous_response_request_states[0]

        if matched_request_state is terminal_request_state:
            _record_response_event(matched_request_state, event_type)
        else:
            _record_response_event(matched_request_state, event_type)
            _record_response_event(terminal_request_state, event_type)

        status_request_state = terminal_request_state or matched_request_state
        if status_request_state is None and is_previous_response_not_found_event:
            session.upstream_control.reconnect_requested = True
            return

        if status_request_state is not None and event_type not in {
            "response.completed",
            "response.failed",
            "response.incomplete",
            "error",
        }:
            await self._maybe_touch_request_state_api_key_reservation(
                status_request_state,
                api_key=status_request_state.api_key,
                surface="http_bridge",
            )

        if (
            event_type == "response.completed"
            and terminal_request_state is not None
            and terminal_request_state.suppressed_duplicate_tool_call
        ):
            session.upstream_control.reconnect_requested = True
            session.closed = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close HTTP bridge upstream after suppressed duplicate tool call", exc_info=True)
            terminal_request_state.error_http_status_override = 502
            (
                event,
                payload,
                event_type,
                rewritten_text,
            ) = _rewrite_websocket_suppressed_duplicate_tool_call_completion_event(
                request_state=terminal_request_state,
            )
            event_block = f"data: {rewritten_text}\n\n"

        if (
            status_request_state is not None
            and status_request_state.previous_response_id is not None
            and is_missing_tool_output_event
        ):
            status_request_state.error_http_status_override = 502
            event, payload, event_type, rewritten_text = _rewrite_websocket_continuity_corruption_event(
                request_state=status_request_state,
                upstream_control=session.upstream_control,
                reason="missing_tool_output",
                reconnect_requested=True,
                original_text=text,
            )
            event_block = f"data: {rewritten_text}\n\n"

        if status_request_state is not None and is_previous_response_not_found_event:
            status_request_state.error_http_status_override = 502
            status_request_state.previous_response_not_found_rewritten = (
                response_id is None and not has_other_pending_requests
            )
            event, payload, event_type, rewritten_text = _maybe_rewrite_websocket_previous_response_not_found_event(
                request_state=status_request_state,
                event=event,
                payload=payload,
                event_type=event_type,
                upstream_control=session.upstream_control,
                original_text=text,
            )
            event_block = f"data: {rewritten_text}\n\n"

        retry_error_code = _websocket_precreated_retry_error_code(
            status_request_state,
            event_type=event_type,
            payload=payload,
            has_other_pending_requests=has_other_pending_requests,
        )
        auth_error_code = _websocket_precreated_auth_error_code(
            status_request_state,
            event_type=event_type,
            payload=payload,
            has_other_pending_requests=has_other_pending_requests,
        )
        owner_pinned_quota_error = _websocket_owner_pinned_quota_error_code(
            status_request_state,
            event_type=event_type,
            payload=payload,
        )
        if (
            auth_error_code is not None
            and not is_previous_response_not_found_event
            and status_request_state is not None
        ):
            auth_retry_result = await self._retry_http_bridge_precreated_auth_request(
                session,
                status_request_state,
                error_message=_websocket_event_error_message(event_type, payload),
            )
            if auth_retry_result == "retried":
                return
            if auth_retry_result == "failed":
                async with session.pending_lock:
                    if status_request_state in session.pending_requests:
                        session.pending_requests.remove(status_request_state)
                        session.queued_request_count = max(0, session.queued_request_count - 1)
                status_request_state.error_http_status_override = 502
                (
                    _downstream_text,
                    event_block,
                    event,
                    payload,
                    event_type,
                ) = _build_stream_incomplete_terminal_event_for_request(status_request_state)
        elif owner_pinned_quota_error is not None and not is_previous_response_not_found_event:
            await self._handle_stream_error(
                session.account,
                {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                owner_pinned_quota_error,
            )
            if status_request_state is not None:
                setattr(status_request_state, "account_health_error_handled", True)
            if (
                status_request_state is not None
                and status_request_state.previous_response_id is not None
                and status_request_state.preferred_account_id is not None
            ):
                status_request_state.error_http_status_override = 502
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                event, payload, event_type, rewritten_text = (
                    _rewrite_websocket_previous_response_owner_unavailable_event(
                        request_state=status_request_state,
                    )
                )
                event_block = f"data: {rewritten_text}\n\n"
        elif retry_error_code is not None and not is_previous_response_not_found_event:
            await self._handle_stream_error(
                session.account,
                {"message": _websocket_event_error_message(event_type, payload) or "Upstream error"},
                retry_error_code,
            )
            if status_request_state is not None:
                setattr(status_request_state, "account_health_error_handled", True)
            if status_request_state is not None and status_request_state.previous_response_id is None:
                async with session.pending_lock:
                    if status_request_state not in session.pending_requests:
                        session.pending_requests.appendleft(status_request_state)
                        session.queued_request_count += 1
                    status_request_state.awaiting_response_created = True
                    status_request_state.response_id = None
                retried = await self._retry_http_bridge_precreated_request(session)
                if retried:
                    return
                async with session.pending_lock:
                    if status_request_state in session.pending_requests:
                        session.pending_requests.remove(status_request_state)
                        session.queued_request_count = max(0, session.queued_request_count - 1)
                status_request_state.error_http_status_override = 502
                (
                    _downstream_text,
                    event_block,
                    event,
                    payload,
                    event_type,
                ) = _build_stream_incomplete_terminal_event_for_request(status_request_state)

        if event_type == "response.completed" and terminal_request_state is not None:
            # Record the completed response id regardless of input shape so
            # subsequent turns (including ones that never populated
            # input_item_count, e.g. string inputs) can still reuse this
            # anchor for continuity lookups.
            if response_id is not None:
                session.last_completed_response_id = response_id
            # Prefix trimming is only meaningful for list-shaped inputs, so
            # keep the input-count / fingerprint update scoped to that path.
            if terminal_request_state.input_item_count > 0:
                session.last_completed_input_count = terminal_request_state.input_item_count
                session.last_completed_input_prefix_fingerprint = terminal_request_state.input_full_fingerprint

        if event_type == "error":
            http_status = _http_error_status_from_payload(payload)
            if status_request_state is not None:
                status_request_state.error_http_status_override = http_status
            (
                event_block,
                payload,
                event,
                event_type,
            ) = _normalize_http_bridge_error_event(
                event=event,
                payload=payload,
                request_state=terminal_request_state or matched_request_state,
            )

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            await _release_websocket_response_create_gate(created_request_state, session.response_create_gate)

        if response_id is not None and matched_request_state is not None and event_type == "response.completed":
            await self._register_http_bridge_previous_response_id(
                session,
                response_id,
                input_item_count=(
                    matched_request_state.input_item_count
                    if event_type == "response.completed" and matched_request_state.input_item_count > 0
                    else None
                ),
                input_full_fingerprint=(
                    matched_request_state.input_full_fingerprint
                    if event_type == "response.completed" and matched_request_state.input_item_count > 0
                    else None
                ),
            )

        if terminal_request_state is not None and event_type in {"response.failed", "error"}:
            if event_type == "error":
                error = event.error if event else None
            else:
                error = event.response.error if event and event.response else None
            terminal_error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            terminal_error_message = error.message if error else None
            if _is_security_work_authorization_required_error(terminal_error_code, terminal_error_message):
                can_retry_security_work = (
                    not session.account.security_work_authorized
                    and not has_other_pending_requests
                    and terminal_request_state.response_id is None
                    and terminal_request_state.replay_count < 1
                    and bool(terminal_request_state.request_text)
                    and terminal_request_state.preferred_account_id != session.account.id
                    and (
                        terminal_request_state.previous_response_id is None
                        or (
                            terminal_request_state.fresh_upstream_request_text is not None
                            and terminal_request_state.fresh_upstream_request_is_retry_safe
                        )
                    )
                )
                if terminal_request_state.event_queue is not None:
                    await terminal_request_state.event_queue.put(
                        format_sse_event(
                            _security_work_advisory_event(
                                code=_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                message=(
                                    _SECURITY_WORK_RETRY_MESSAGE
                                    if can_retry_security_work
                                    else "Upstream flagged this request as possible cybersecurity work. "
                                    "codex-lb cannot safely switch accounts after this response has already started, "
                                    "so the original upstream error is being forwarded."
                                ),
                                request_id=terminal_request_state.request_log_id or terminal_request_state.request_id,
                                action=(
                                    "retry_security_work_authorized"
                                    if can_retry_security_work
                                    else "forward_original_security_work_error"
                                ),
                                account_id=session.account.id,
                            )
                        )
                    )
                if can_retry_security_work:
                    retried = await self._retry_http_bridge_security_work_request(session, terminal_request_state)
                    if retried:
                        return

        if (
            matched_request_state is not None
            and matched_request_state.event_queue is not None
            and not suppress_downstream_event
        ):
            await matched_request_state.event_queue.put(event_block)

        if terminal_request_state is None:
            return

        if terminal_request_state is not matched_request_state and terminal_request_state.event_queue is not None:
            await terminal_request_state.event_queue.put(event_block)
        if terminal_request_state.event_queue is not None:
            await terminal_request_state.event_queue.put(None)

        if event_type in {"response.failed", "response.incomplete", "error"}:
            error_code = None
            if event_type == "error":
                error = event.error if event else None
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            elif event and event.response:
                error = event.response.error
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            _log_http_bridge_event(
                "terminal_error",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=error_code,
                pending_count=await self._http_bridge_pending_count(session),
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )

        await self._finalize_websocket_request_state(
            terminal_request_state,
            account=session.account,
            account_id_value=session.account.id,
            event=event,
            event_type=event_type,
            payload=payload,
            api_key=terminal_request_state.api_key,
            upstream_control=session.upstream_control,
            response_create_gate=session.response_create_gate,
        )
