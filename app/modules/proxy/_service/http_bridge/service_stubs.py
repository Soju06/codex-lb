from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any, Mapping, NoReturn, TypeVar, cast

from app.core.auth.refresh import RefreshError
from app.core.balancer import ResetPreferenceWindow, RoutingStrategy
from app.core.clients.http import lease_http_session
from app.core.clients.proxy import (
    ProxyResponseError,
    _as_image_fetch_session,
    _inline_input_image_urls,
    pop_stream_timeout_overrides,
    push_stream_timeout_overrides,
)
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.clients.proxy import thread_goal_request as core_thread_goal_request
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME
from app.db.models import Account, DashboardSettings
from app.modules.proxy._service.support import _RequestLogFailureMetadata

T = TypeVar("T")

_HTTP_BRIDGE_STARTUP_KEEPALIVE_GRACE_SECONDS = 0.5
_PREWARM_RESPONSE_TIMEOUT_SECONDS = 2.0
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
