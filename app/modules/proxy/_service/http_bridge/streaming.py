from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
from collections.abc import AsyncGenerator, Callable
from typing import Any, AsyncIterator, Mapping, TypeVar, cast
from uuid import uuid4

import anyio

from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.proxy import CodexControlResponse as CodexControlResponse
from app.core.clients.proxy import (  # noqa: F401
    ImageFetchSession,
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    _as_image_fetch_session,
    _client_metadata_uses_responses_lite,
    _inline_content_images,
    _inline_input_image_urls,
    _ws_transport_payload_budget_bytes,
    filter_inbound_headers,
    is_confirmed_pre_dispatch_transport_error,
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
from app.core.clients.proxy_websocket import UpstreamWebSocketTransportError
from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorEnvelope,
    openai_error,
    response_failed_event,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    http_bridge_retry_circuit_total,
    stream_idle_timeout_total,
    stream_keepalive_sent_total,
)
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id, ensure_request_scope_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.core.utils.time import utcnow
from app.db.models import (
    HttpBridgeSessionState,
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
    _sticky_key_for_compact_request as _sticky_key_for_compact_request,
)
from app.modules.proxy._service.compact import (
    _sticky_key_from_compact_payload as _sticky_key_from_compact_payload,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _HTTP_BRIDGE_COOLDOWN_SUPPRESSION_ATTR,
    _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
    _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
    _HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
    _HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR,
    _HTTP_BRIDGE_PREPARED_ANCHOR_ATTR,
    _bind_http_bridge_proxy_injected_anchor,
    _capture_http_bridge_denied_anchor_fence,
    _effective_http_bridge_idle_ttl_seconds,
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_durable_lookup_allows_turn_state_takeover,
    _http_bridge_eventless_budget_seconds,
    _http_bridge_eventless_max_keepalive_count,
    _http_bridge_eventless_timeout_message,
    _http_bridge_is_context_overflow_error,
    _http_bridge_is_explicit_previous_response_rejection,
    _http_bridge_is_previous_response_owner_unavailable,
    _http_bridge_models_compatible,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_payload_looks_like_full_resend,
    _http_bridge_payload_without_previous_response_id,
    _http_bridge_previous_response_error_envelope,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_needs_unanchored_handoff,
    _http_bridge_request_stage,
    _http_bridge_requires_cluster_registration,
    _http_bridge_retry_circuit_attempt_selection_for_pending_requests,
    _http_bridge_runtime_config,
    _http_bridge_should_attempt_local_bootstrap_rebind,
    _http_bridge_should_attempt_local_previous_response_recovery,
    _http_bridge_should_attempt_soft_affinity_reroute,
    _http_bridge_should_rollover_after_context_overflow,
    _http_bridge_turn_state_anchor_for_owner_failure,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _make_http_bridge_session_header_fallback_key,
    _make_http_bridge_session_key,
    _proxy_admission_wait_timeout_seconds,
    _record_bridge_reattach,
    _record_continuity_fail_closed,
    _release_http_bridge_denied_anchor_fences,
    _release_http_bridge_unanchored_handoff,
    _release_http_bridge_unanchored_handoffs_for_request,
    _reserve_http_bridge_unanchored_handoff,
    _trim_http_bridge_previous_response_input_items,
)
from app.modules.proxy._service.http_bridge.owner_forwarding import (
    _owner_forward_failure_allows_local_recovery,
)
from app.modules.proxy._service.http_bridge.quarantine import (
    _http_bridge_quarantine_generation,
    _http_bridge_session_key_quarantined,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _build_rewritten_stream_response_failed_event,
    _codex_keepalive_frame,
    _fingerprint_input_items,
    _header_value_case_insensitive,
    _http_bridge_startup_keepalive_grace_seconds,
    _inject_missing_interrupted_function_call_outputs,
    _input_prefix_matches_stored_context,
    _is_previous_response_not_found_error,
    _maybe_log_proxy_request_payload,
    _maybe_log_proxy_request_shape,
    _missing_function_call_outputs_for_previous_response,
    _normalize_service_tier_value,
    _normalize_session_id,
    _openai_error_envelope_from_response_failed_payload,
    _partial_output_proxy_error_event_block,
    _response_create_client_metadata,
    _responses_request_contains_input_image,
    _responses_request_uses_image_generation,
    _service_get_settings,
    _service_get_settings_cache,
    _service_time,
    _stream_keepalive_max_count,
    _websocket_downstream_response_id,
    _websocket_event_error_code,
    _websocket_event_error_message,
    _websocket_event_error_param,
    _websocket_event_error_type,
)
from app.modules.proxy._service.http_bridge.upstream_events import (
    _abandon_durable_http_bridge_continuity,
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
    _tools_hash as _tools_hash,
)
from app.modules.proxy._service.observability import (
    _truncate_identifier as _truncate_identifier,
)
from app.modules.proxy._service.support import (
    _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _account_capacity_wait_payload,
    _account_selection_recovery_sleep_seconds_from_message,
    _DeferredAccountBackoffLifecycle,
    _DeferredAccountBackoffTracker,
    _event_type_from_payload,
    _HTTPBridgeOwnerForward,
    _HTTPBridgeRetryCircuitAttemptSelection,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _is_local_account_cap_code,
    _signal_propagated_capacity_startup_ready,
    _signal_propagated_capacity_startup_wait,
    _signal_propagated_responses_service_cleanup_ready,
    _ttft_event_visible_at,
    _WebSocketRequestState,
    mark_upstream_websocket_transport_failure,
    upstream_websocket_transport_recently_failed,
    websocket_connect_transport_failure_code,
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
    _codex_backend_identity,
    _extract_model_class,
    _prompt_cache_key_from_request_model,
    _request_allows_bare_session_cap_spillover,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
    resolve_required_account_id,
    without_http_bridge_session_affinity_headers,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup
from app.modules.proxy.durable_bridge_repository import durable_bridge_hash
from app.modules.proxy.durable_bridge_runtime import http_bridge_owner_process_epoch
from app.modules.proxy.helpers import (
    _normalize_error_code,
)
from app.modules.proxy.replay_safety import (
    AccountNeutralReplayProjection,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
)

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_REQUEST_TRANSPORT_HTTP = "http"
_RESPONSE_CREATE_GATE_RETRY_SLEEP_SECONDS = 10.0


def _http_bridge_continuity_bound_without_safe_replay(request_state: _WebSocketRequestState) -> bool:
    """Return whether retrying would require replaying an unsafe continuation."""
    if request_state.previous_response_id is not None:
        return not (request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text)
    return request_state.hard_continuity_anchor and not (
        request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text
    )


def _http_bridge_durable_recovery_predecessor_proven(request_state: _WebSocketRequestState) -> bool:
    """Return whether the operation has a durable predecessor anchor."""
    return request_state.previous_response_id is not None or request_state.operation_parent_response_id is not None


def _http_bridge_verified_stale_anchor_replay_is_operation_fenced(
    session: _HTTPBridgeSession,
    request_state: _WebSocketRequestState,
) -> bool:
    """Return whether anchor removal can preserve durable single settlement."""
    return bool(
        request_state.operation_registered
        and request_state.operation_id is not None
        and session.durable_session_id is not None
        and session.durable_owner_epoch is not None
    )


class _VerifiedDurableFullResend:
    """Immutable proof that one payload contains a durable turn's complete context."""

    _durable_session_id: str
    _full_input_fingerprint: str
    _latest_response_id: str
    _owner_account_id: str
    _pending_tool_calls: tuple[tuple[str, str], ...] | None
    _stored_input_fingerprint: str
    _stored_input_item_count: int
    __slots__ = (
        "_durable_session_id",
        "_full_input_fingerprint",
        "_latest_response_id",
        "_owner_account_id",
        "_pending_tool_calls",
        "_stored_input_fingerprint",
        "_stored_input_item_count",
    )
    __construction_token = object()

    def __init__(
        self,
        *,
        _token: object,
        durable_session_id: str,
        owner_account_id: str,
        latest_response_id: str,
        stored_input_item_count: int,
        stored_input_fingerprint: str,
        full_input_fingerprint: str,
        pending_tool_calls: tuple[tuple[str, str], ...] | None,
    ) -> None:
        if _token is not self.__construction_token:
            raise TypeError("verified durable full resend proofs are created only by the verifier")
        object.__setattr__(self, "_durable_session_id", durable_session_id)
        object.__setattr__(self, "_owner_account_id", owner_account_id)
        object.__setattr__(self, "_latest_response_id", latest_response_id)
        object.__setattr__(self, "_stored_input_item_count", stored_input_item_count)
        object.__setattr__(self, "_stored_input_fingerprint", stored_input_fingerprint)
        object.__setattr__(self, "_full_input_fingerprint", full_input_fingerprint)
        object.__setattr__(self, "_pending_tool_calls", pending_tool_calls)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("verified durable full resend proofs are immutable")

    def __copy__(self) -> "_VerifiedDurableFullResend":
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> "_VerifiedDurableFullResend":
        return self

    def __reduce_ex__(self, _protocol: object) -> str | tuple[Any, ...]:
        raise TypeError("verified durable full resend proofs cannot be serialized")

    @property
    def stored_input_item_count(self) -> int:
        return self._stored_input_item_count

    def matches(
        self,
        payload: ResponsesRequest,
        durable_lookup: DurableBridgeLookup | None,
    ) -> bool:
        input_items = payload.input
        return (
            isinstance(input_items, list)
            and durable_lookup is not None
            and durable_lookup.session_id == self._durable_session_id
            and durable_lookup.account_id == self._owner_account_id
            and durable_lookup.latest_response_id == self._latest_response_id
            and durable_lookup.latest_input_item_count == self._stored_input_item_count
            and durable_lookup.latest_input_full_fingerprint == self._stored_input_fingerprint
            and _pending_tool_calls_identity(durable_lookup.latest_pending_tool_calls) == self._pending_tool_calls
            and _fingerprint_input_items(cast(list[JsonValue], input_items)) == self._full_input_fingerprint
        )

    @classmethod
    def _verify(
        cls,
        payload: ResponsesRequest,
        durable_lookup: DurableBridgeLookup,
    ) -> "_VerifiedDurableFullResend | None":
        owner_account_id = durable_lookup.account_id
        latest_response_id = durable_lookup.latest_response_id
        stored_count = durable_lookup.latest_input_item_count
        stored_fingerprint = durable_lookup.latest_input_full_fingerprint
        if (
            owner_account_id is None
            or latest_response_id is None
            or stored_count is None
            or stored_fingerprint is None
            or not _http_bridge_payload_looks_like_full_resend(payload)
            or not isinstance(payload.input, list)
            or not _input_prefix_matches_stored_context(
                payload.input,
                stored_count=stored_count,
                stored_fingerprint=stored_fingerprint,
            )
        ):
            return None
        input_items = cast(list[JsonValue], payload.input)
        replay_projection = project_responses_input_for_account_neutral_fresh_replay(
            input_items,
            stored_count=stored_count,
            # Classification only, mirroring classify_durable_full_resend:
            # inline Responses-Lite developer IDs must remain visible until
            # the exact-manifest check rejects response-owned messages.
            preserve_developer_message_ids=True,
        )
        pending_tool_calls = durable_lookup.latest_pending_tool_calls
        if replay_projection is None:
            return None
        safe_fresh_context = responses_input_suffix_retains_prior_output(
            replay_projection.input_items,
            stored_count=replay_projection.stored_prefix_count,
            canonical_lite_developer_index=replay_projection.canonical_lite_developer_index,
        ) or (
            pending_tool_calls is not None
            and responses_input_suffix_matches_pending_tool_calls(
                replay_projection.input_items,
                stored_count=replay_projection.stored_prefix_count,
                pending_tool_calls=pending_tool_calls,
                canonical_lite_developer_index=replay_projection.canonical_lite_developer_index,
            )
        )
        if not safe_fresh_context:
            return None
        return cls(
            _token=cls.__construction_token,
            durable_session_id=durable_lookup.session_id,
            owner_account_id=owner_account_id,
            latest_response_id=latest_response_id,
            stored_input_item_count=stored_count,
            stored_input_fingerprint=stored_fingerprint,
            full_input_fingerprint=_fingerprint_input_items(input_items),
            pending_tool_calls=_pending_tool_calls_identity(pending_tool_calls),
        )


def _pending_tool_calls_identity(
    pending_tool_calls: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...] | None:
    return None if pending_tool_calls is None else tuple(sorted(pending_tool_calls.items()))


def _verify_durable_full_resend(
    payload: ResponsesRequest,
    durable_lookup: DurableBridgeLookup | None,
) -> _VerifiedDurableFullResend | None:
    if durable_lookup is None or durable_lookup.account_id is None or durable_lookup.latest_response_id is None:
        return None
    return _VerifiedDurableFullResend._verify(payload, durable_lookup)


def _http_bridge_client_full_history_recovery_enabled(request_state: _WebSocketRequestState) -> bool:
    """Return whether an ambiguous anchored turn may fall back to client replay.

    The client can recover an unknown upstream handoff by dropping
    ``previous_response_id`` and resending its full local history. This is
    intentionally opt-in: upstream acceptance is still ambiguous and the
    fallback therefore has at-least-once (possible duplicate) semantics.
    """
    settings = _service_get_settings()
    return (
        getattr(settings, "http_responses_session_bridge_ambiguous_continuation_recovery_mode", "fail_closed")
        == "client_full_history_once"
        and request_state.previous_response_id is not None
        and request_state.response_id is None
        and request_state.response_event_count == 0
        and not request_state.fresh_upstream_request_is_retry_safe
    )


def _http_bridge_server_anchored_replay_enabled(request_state: _WebSocketRequestState) -> bool:
    """Return whether the one permitted server-side anchored replay is unused."""
    settings = _service_get_settings()
    return (
        getattr(settings, "http_responses_session_bridge_ambiguous_continuation_recovery_mode", "fail_closed")
        in {"server_anchored_replay_once", "server_indefinite_recovery"}
        and request_state.previous_response_id is not None
        and request_state.response_id is None
        and request_state.response_event_count == 0
        and (
            request_state.replay_count == 0
            or getattr(settings, "http_responses_session_bridge_ambiguous_continuation_recovery_mode", "")
            == "server_indefinite_recovery"
        )
    )


def _http_bridge_client_full_history_recovery_error() -> OpenAIErrorEnvelope:
    payload = openai_error(
        "previous_response_not_found",
        "Previous response was not found; retry without previous_response_id.",
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "previous_response_id"
    return payload


_HTTP_BRIDGE_DEAD_OWNER_NOT_FOUND_DETAIL = "The previous bridge owner is no longer available."


def _http_bridge_dead_owner_previous_response_not_found_terminal(
    *,
    previous_response_id: str,
    response_id: str,
) -> dict[str, JsonValue]:
    """Return the standard not-found terminal for an unreplayable dead owner."""

    error = cast(
        dict[str, JsonValue],
        _http_bridge_previous_response_error_envelope(
            previous_response_id,
            _HTTP_BRIDGE_DEAD_OWNER_NOT_FOUND_DETAIL,
        )["error"],
    )
    return cast(
        dict[str, JsonValue],
        response_failed_event(
            cast(str, error["code"]),
            cast(str, error["message"]),
            error_type=cast(str, error["type"]),
            response_id=response_id,
            error_param=cast(str | None, error["param"]),
        ),
    )


def _http_bridge_dead_owner_previous_response_not_found_proxy_error(
    *,
    previous_response_id: str,
) -> ProxyResponseError:
    return ProxyResponseError(
        400,
        _http_bridge_previous_response_error_envelope(
            previous_response_id,
            _HTTP_BRIDGE_DEAD_OWNER_NOT_FOUND_DETAIL,
        ),
    )


def _http_bridge_dead_owner_previous_response_id(request_state: _WebSocketRequestState) -> str:
    return request_state.previous_response_id or _websocket_downstream_response_id(request_state)


def _http_bridge_durable_owner_is_dead(
    lookup: DurableBridgeLookup,
    *,
    current_instance: str,
    current_process_epoch: str,
) -> bool:
    """Classify an anchored durable lookup whose owner cannot be live now."""

    previous_process_epoch = lookup.owner_process_epoch
    owner_instance_is_dead = lookup.owner_instance_id is not None and lookup.owner_instance_id != current_instance
    process_epoch_is_dead = previous_process_epoch is not None and previous_process_epoch != current_process_epoch
    return owner_instance_is_dead or process_epoch_is_dead or not lookup.lease_is_active(now=utcnow())


def _http_bridge_payload_is_account_neutral_fresh_replay(payload: ResponsesRequest) -> bool:
    return responses_payload_is_account_neutral_fresh_replay(payload.to_replay_safety_payload())


def _apply_http_bridge_downstream_turn_state(
    request_state: _WebSocketRequestState,
    *,
    downstream_turn_state: str | None,
    incoming_turn_state_header: str | None,
) -> None:
    if downstream_turn_state is None:
        return
    request_state.session_id = _normalize_session_id(downstream_turn_state)
    if incoming_turn_state_header is not None or request_state.previous_response_id is not None:
        request_state.hard_continuity_anchor = True


def _proxy_error_code_message(exc: ProxyResponseError) -> tuple[str | None, str | None]:
    error = exc.payload.get("error") if isinstance(exc.payload, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return (str(code) if code is not None else None, str(message) if message is not None else None)


_HTTP_BRIDGE_AMBIGUOUS_RECOVERY_ERROR_CODES = frozenset(
    {
        "stream_incomplete",
        "stream_idle_timeout",
        "upstream_request_timeout",
        # Pre-response bridge timeouts replaced the upstream_request_timeout
        # code that startup cooldown fail-closed used to raise; they leave
        # upstream acceptance exactly as unknown as it was before.
        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
    }
)


def _http_bridge_error_is_ambiguous_transport(exc: ProxyResponseError) -> bool:
    """Return whether an error leaves upstream acceptance genuinely unknown."""

    code, _message = _proxy_error_code_message(exc)
    return code in _HTTP_BRIDGE_AMBIGUOUS_RECOVERY_ERROR_CODES


def _http_bridge_account_capacity_wait_seconds(exc: ProxyResponseError) -> float | None:
    code, message = _proxy_error_code_message(exc)
    if code == "capacity_exhausted_active_sessions":
        return None
    if code == "response_create_gate_timeout":
        # Per-session response-create gate contention is recoverable: the
        # in-flight turn releases the gate when it completes, so queued
        # same-session work must wait within the bridge request budget
        # instead of failing at the first admission-timeout expiry. Each
        # retry re-attempts acquisition for proxy_admission_wait_timeout
        # seconds, so the sleep only covers the window between attempts.
        return _RESPONSE_CREATE_GATE_RETRY_SLEEP_SECONDS
    return _account_selection_recovery_sleep_seconds_from_message(
        message,
        error_code=code,
    )


def _http_bridge_unanchored_fork_can_spill_on_cap(
    *,
    error_code: str | None,
    affinity_kind: str,
    original_request_unanchored: bool,
    payload: ResponsesRequest,
    preferred_account_id: str | None,
) -> bool:
    return (
        _is_local_account_cap_code(error_code)
        and affinity_kind == "internal_unanchored_parallel"
        and original_request_unanchored
        and preferred_account_id is not None
        and payload.previous_response_id is None
        and _request_allows_bare_session_cap_spillover(payload)
    )


def _http_bridge_capacity_wait_plan(
    exc: ProxyResponseError,
    *,
    request_deadline: float,
) -> tuple[float, float, str | None] | None:
    account_capacity_wait_seconds = _http_bridge_account_capacity_wait_seconds(exc)
    if account_capacity_wait_seconds is None:
        return None
    remaining_budget_seconds = max(0.0, request_deadline - _service_time().monotonic())
    if remaining_budget_seconds <= 0:
        return None
    code, message = _proxy_error_code_message(exc)
    bounded_wait_seconds = min(account_capacity_wait_seconds, remaining_budget_seconds)
    if code == "response_create_gate_timeout":
        # Reserve the tail of the request budget for one final gate
        # acquisition attempt instead of sleeping it away: a same-session
        # turn may release the gate during those last seconds.
        attempt_reserve_seconds = _proxy_admission_wait_timeout_seconds()
        bounded_wait_seconds = min(
            bounded_wait_seconds,
            max(0.0, remaining_budget_seconds - attempt_reserve_seconds),
        )
    return bounded_wait_seconds, account_capacity_wait_seconds, message


def _http_bridge_can_replace_retired_gate_session(
    exc: ProxyResponseError,
    *,
    session: "_HTTPBridgeSession",
    request_state: _WebSocketRequestState,
    request_was_enqueued: bool,
) -> bool:
    # A gate timeout happens before this waiter is appended or sent.  Once the
    # stale owner has retired the session, only that fully cleaned pre-submit
    # state is safe to carry to a replacement; any response/downstream marker
    # makes the upstream acceptance boundary ambiguous. replay_count is
    # incremented at proxy-side resubmission points too, not only on client
    # reconnects, so it says nothing about upstream progress on *this* bridge
    # attempt either way; it's the other, definitively-unsubmitted markers
    # below that establish the boundary is unambiguous, so replay_count must
    # not disqualify replacement on its own.
    code, _message = _proxy_error_code_message(exc)
    return (
        code == "response_create_gate_timeout"
        and session.closed
        and session.key.strength == "hard"
        and not request_was_enqueued
        and request_state.request_text is not None
        and request_state.event_queue is not None
        and request_state.response_id is None
        and request_state.response_event_count == 0
        and request_state.last_downstream_sequence_number is None
        and not request_state.downstream_visible
        and not request_state.awaiting_response_created
        and request_state.response_create_gate is None
        and not request_state.response_create_gate_acquired
    )


async def _iter_account_capacity_wait_sse(
    *,
    request_id: str,
    reason: str | None,
    sleep_seconds: float,
    emit_keepalives: bool,
    request_state: _WebSocketRequestState | None = None,
) -> AsyncIterator[str]:
    if not emit_keepalives:
        if request_state is not None and request_state.capacity_startup_ready_event is not None:
            request_state.capacity_startup_ready_event.clear()
        if request_state is not None and request_state.capacity_startup_wait_event is not None:
            request_state.capacity_startup_wait_event.set()
        _signal_propagated_capacity_startup_wait()
    wait_started_at = _service_time().monotonic()
    remaining_sleep_seconds = sleep_seconds
    while remaining_sleep_seconds > 0:
        if emit_keepalives:
            yield format_sse_event(
                cast(
                    Mapping[str, JsonValue],
                    _account_capacity_wait_payload(
                        None,
                        request_id=request_id,
                        reason=reason,
                        retry_after_seconds=remaining_sleep_seconds,
                        started_at=wait_started_at,
                    ),
                )
            )
        chunk_seconds = min(
            remaining_sleep_seconds,
            _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS,
        )
        await asyncio.sleep(chunk_seconds)
        remaining_sleep_seconds -= chunk_seconds


def _http_bridge_interrupted_tool_outputs_input(
    session: _HTTPBridgeSession,
    *,
    payload: ResponsesRequest,
    request_id: str,
) -> list[JsonValue] | None:
    """Return ``payload.input`` with synthetic interrupted tool outputs prepended.

    When the session's last completed response left tool-call items pending
    (the turn was interrupted before their outputs were sent) and the outgoing
    request anchors on that response id without supplying those outputs,
    upstream rejects it with ``No tool output found for ... call_``. Mirror
    the direct WebSocket route by injecting synthetic outputs of the matching
    item type into the payload *before* it is prepared, so the slim/size
    guard, the stored input context, and the usage budget all observe the
    upstream-shaped input. Returns ``None`` when no injection is needed.
    """
    if not session.last_pending_tool_calls or session.last_completed_response_id is None:
        return None
    if payload.previous_response_id != session.last_completed_response_id:
        return None
    input_items = payload.input
    if not isinstance(input_items, list):
        return None
    input_item_list = cast(list[JsonValue], input_items)
    missing_call_ids = _missing_function_call_outputs_for_previous_response(
        input_item_list,
        pending_call_ids=list(session.last_pending_tool_calls),
    )
    if not missing_call_ids:
        return None
    logger.warning(
        "http_bridge_interrupted_tool_outputs_injected request_id=%s previous_response_id=%s missing_call_count=%s",
        request_id,
        session.last_completed_response_id,
        len(missing_call_ids),
    )
    return cast(
        list[JsonValue],
        _inject_missing_interrupted_function_call_outputs(
            input_item_list,
            missing_call_ids=missing_call_ids,
            pending_call_types=session.last_pending_tool_calls,
        ),
    )


def _legacy_forward_upgrade_required_error() -> ProxyResponseError:
    return ProxyResponseError(
        409,
        openai_error(
            "bridge_forward_upgrade_required",
            "Legacy owner forwarding requires a registered turn-state continuity anchor",
            error_type="server_error",
        ),
    )


async def _legacy_forward_anchor_lookup(
    *,
    durable_bridge: Any,
    bridge_session_key: _HTTPBridgeSessionKey,
    turn_state: str | None,
    api_key: ApiKeyData | None,
    previous_response_id: str | None,
    forwarded_request: bool,
    forwarded_legacy_signature: bool,
) -> DurableBridgeLookup | None:
    if not (
        forwarded_request
        and forwarded_legacy_signature
        and bridge_session_key.affinity_kind == "session_header"
        and previous_response_id is None
    ):
        return None

    return await _registered_turn_state_anchor_lookup(
        durable_bridge=durable_bridge,
        bridge_session_key=bridge_session_key,
        turn_state=turn_state,
        api_key=api_key,
    )


async def _current_origin_legacy_owner_anchor_lookup(
    *,
    durable_bridge: Any,
    bridge_session_key: _HTTPBridgeSessionKey,
    turn_state: str | None,
    api_key: ApiKeyData | None,
    previous_response_id: str | None,
    forwarded_request: bool,
) -> DurableBridgeLookup | None:
    """Prove a current-origin turn state before using legacy owner forwarding."""

    if (
        forwarded_request
        or bridge_session_key.affinity_kind != "session_header"
        or turn_state is None
        or previous_response_id is not None
    ):
        return None
    return await _registered_turn_state_anchor_lookup(
        durable_bridge=durable_bridge,
        bridge_session_key=bridge_session_key,
        turn_state=turn_state,
        api_key=api_key,
    )


async def _registered_turn_state_anchor_lookup(
    *,
    durable_bridge: Any,
    bridge_session_key: _HTTPBridgeSessionKey,
    turn_state: str | None,
    api_key: ApiKeyData | None,
) -> DurableBridgeLookup:
    if turn_state is None:
        raise _legacy_forward_upgrade_required_error()
    try:
        lookup = await durable_bridge.lookup_turn_state_target(
            turn_state=turn_state,
            api_key_id=api_key.id if api_key is not None else None,
        )
    except Exception as exc:
        logger.warning("Legacy owner-forward turn-state proof lookup failed", exc_info=True)
        raise _legacy_forward_upgrade_required_error() from exc
    if (
        lookup is None
        or lookup.canonical_kind != bridge_session_key.affinity_kind
        or lookup.canonical_key != bridge_session_key.affinity_key
    ):
        raise _legacy_forward_upgrade_required_error()
    return lookup


class _HTTPBridgeStreamingMixin:
    async def validate_http_bridge_legacy_forward_anchor(
        self: Any,
        *,
        original_affinity_kind: str | None,
        original_affinity_key: str | None,
        downstream_turn_state: str | None,
        previous_response_id: str | None,
        api_key: ApiKeyData | None,
    ) -> DurableBridgeLookup | None:
        """Prove a legacy forwarded anchor before any compact or fallback branch."""

        return await _legacy_forward_anchor_lookup(
            durable_bridge=self._durable_bridge,
            bridge_session_key=_HTTPBridgeSessionKey(
                original_affinity_kind or "",
                original_affinity_key or "",
                api_key.id if api_key is not None else None,
            ),
            turn_state=downstream_turn_state,
            api_key=api_key,
            previous_response_id=previous_response_id,
            forwarded_request=True,
            forwarded_legacy_signature=True,
        )

    def stream_http_responses(
        self: Any,
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_legacy_signature: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        forwarded_file_owner_account_id: str | None = None,
        client_ip: str | None = None,
        enforce_openai_sdk_contract: bool = True,
        capacity_startup_wait_event: asyncio.Event | None = None,
        capacity_startup_ready_event: asyncio.Event | None = None,
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
            forwarded_original_request_unanchored=forwarded_original_request_unanchored,
            forwarded_legacy_signature=forwarded_legacy_signature,
            proxy_api_authorization=proxy_api_authorization,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            forwarded_file_owner_account_id=forwarded_file_owner_account_id,
            client_ip=client_ip,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            capacity_startup_wait_event=capacity_startup_wait_event,
            capacity_startup_ready_event=capacity_startup_ready_event,
        )

    async def _stream_http_bridge_or_retry(
        self: Any,
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_legacy_signature: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        forwarded_file_owner_account_id: str | None = None,
        client_ip: str | None = None,
        enforce_openai_sdk_contract: bool = True,
        capacity_startup_wait_event: asyncio.Event | None = None,
        capacity_startup_ready_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        dashboard_settings = await _service_get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, _service_get_settings())
        request_id = ensure_request_id()
        self._raise_for_unsupported_input_image_references(payload)
        payload_size_estimate_bytes = len(
            json.dumps(payload.to_payload(), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )
        rewritten_file_account_id = await self._resolve_forwarded_file_account_for_responses(
            payload,
            headers,
            forwarded_file_owner_account_id=forwarded_file_owner_account_id,
            require_forwarded_file_owner=forwarded_request,
        )
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
        # The bridge exists to hold upstream websocket sessions, so a pinned
        # "http" upstream transport must bypass it; without this gate the
        # dashboard pin is silently ignored for bridged follow-up turns.
        configured_upstream_transport = getattr(dashboard_settings, "upstream_stream_transport", "default")
        if configured_upstream_transport == "default":
            configured_upstream_transport = getattr(_service_get_settings(), "upstream_stream_transport", "auto")
        if runtime_config.enabled and configured_upstream_transport == "http":
            logger.info(
                "stream_responses bypassing http bridge for pinned http upstream transport request_id=%s",
                request_id,
            )
            force_upstream_stream_transport = "http"
            runtime_config = dataclasses.replace(runtime_config, enabled=False)
        # While the websocket transport-failure marker is armed, every
        # responses path must degrade to the HTTP upstream: the bridge would
        # stall on its websocket session creation, and the raw path's "smart"
        # policy can resolve a sticky follow-up straight back to the
        # unavailable websocket upstream.
        if force_upstream_stream_transport is None and upstream_websocket_transport_recently_failed():
            if runtime_config.enabled:
                logger.info(
                    "stream_responses bypassing http bridge for recent upstream websocket transport failure "
                    "request_id=%s",
                    request_id,
                )
                runtime_config = dataclasses.replace(runtime_config, enabled=False)
            force_upstream_stream_transport = "http"
        if not runtime_config.enabled:
            stream_with_retry = cast(Callable[..., AsyncIterator[str]], self._stream_with_retry)
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
                file_account_resolution_complete=True,
                upstream_stream_transport_override=force_upstream_stream_transport,
                client_ip=client_ip,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            ):
                yield line
            return

        request_scope_id = ensure_request_scope_id()
        deferred_account_backoff_tracker = _DeferredAccountBackoffTracker()
        bridge_yielded_any = False
        bridge_transport_unavailable = False
        try:
            try:
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
                    forwarded_original_request_unanchored=forwarded_original_request_unanchored,
                    forwarded_legacy_signature=forwarded_legacy_signature,
                    proxy_api_authorization=proxy_api_authorization,
                    forwarded_affinity_kind=forwarded_affinity_kind,
                    forwarded_affinity_key=forwarded_affinity_key,
                    rewritten_file_account_id=rewritten_file_account_id,
                    client_ip=client_ip,
                    enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                    capacity_startup_wait_event=capacity_startup_wait_event,
                    capacity_startup_ready_event=capacity_startup_ready_event,
                    deferred_account_backoff_tracker=deferred_account_backoff_tracker,
                ):
                    bridge_yielded_any = True
                    yield line
            except ProxyResponseError as exc:
                # A transient failure to establish the bridge's upstream
                # websocket session must not fail the turn while the plain
                # HTTP upstream path is healthy. The replay is safe only for
                # failures carrying pre-submit session-creation provenance —
                # a post-dispatch ``upstream_unavailable`` (for example a
                # submit whose request already holds upstream response
                # events) must propagate, or the raw-HTTP retry dispatches
                # the same turn twice. It is also confined to the
                # subscription path: API-key reservations are settled by the
                # finally block below, so re-entering streaming with the
                # same reservation would double-settle it. The
                # ``failure_phase == "connect"`` requirement confines the
                # replay to websocket-open transport evidence: an exhausted
                # token-refresh loop surfaces the same 502
                # ``upstream_unavailable`` envelope from this pre-submit
                # stage, but it is account evidence — retrying it over raw
                # HTTP re-runs the same failing refresh and buries the
                # actionable error under ``no_accounts``.
                # Classify by the connect-site transport provenance, never by
                # the sanitized code: the responses policy preserves the
                # upstream handshake body, so a direct 5xx bridge connect
                # surfaces as ``upstream_error`` or whatever the edge
                # returned. The provenance also subsumes the phase and status
                # gates — refresh transport errors, routed handshakes and
                # credential-scoped rejections never carry it, so they keep
                # their own handling instead of being replayed over raw HTTP.
                transport_failure_code = websocket_connect_transport_failure_code(
                    exc,
                    confirmed_pre_dispatch=is_confirmed_pre_dispatch_transport_error(exc),
                )
                # The bridge retry circuit's own cooldown suppression is
                # generated locally, so it carries no connect provenance. It
                # is admitted on the marker the pre-dispatch submission gate
                # attaches once the request is proved replay-safe, and it is
                # bridge-scoped rather than websocket transport evidence.
                # The marker, never the error code: an ordinary pre-submit
                # budget exhaustion emits the same ``upstream_request_timeout``
                # from ``_raise_proxy_budget_exhausted`` and collects the same
                # pre-submit provenance, but it is admission-queue or
                # host-network evidence. Replaying it would double every
                # request exactly when the instance is saturated, and would
                # feed a doomed raw-HTTP attempt into its own recovery wait.
                cooldown_suppression = getattr(exc, _HTTP_BRIDGE_COOLDOWN_SUPPRESSION_ATTR, False)
                if (
                    bridge_yielded_any
                    or api_key_reservation is not None
                    or not getattr(exc, _HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, False)
                    or getattr(exc, _HTTP_BRIDGE_PREPARED_ANCHOR_ATTR, False)
                    or (transport_failure_code is None and not cooldown_suppression)
                ):
                    raise
                if transport_failure_code is not None:
                    # Bridge session creation runs its own pre-dispatch
                    # failover, which never reaches the websocket failover
                    # decision, so this is the only place bridge-only traffic
                    # can arm the marker. Without it every later HTTP request
                    # re-attempts the dead websocket bridge before falling
                    # back, and the next downstream handshake is accepted
                    # instead of denied with 426.
                    mark_upstream_websocket_transport_failure()
                bridge_transport_unavailable = True
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    lifecycle = deferred_account_backoff_tracker.current_lifecycle
                    if lifecycle is not None:
                        pending_backoffs = lifecycle.pending_backoffs
                        if lifecycle.settlement_confirmed:
                            await self._drain_deferred_account_error_backoffs(pending_backoffs)
                        elif not lifecycle.settlement_owned and (
                            pending_backoffs or lifecycle.reservation != api_key_reservation
                        ):
                            # Session creation can fail before the request is
                            # submitted. Until submit returns, this wrapper owns
                            # the current lifecycle and may release exactly that
                            # reservation. Once ownership transfers, the request
                            # finalizer is the only safe settlement owner.
                            try:
                                await self._release_websocket_reservation(lifecycle.reservation)
                            except Exception:
                                logger.warning(
                                    "Failed to release HTTP bridge API key reservation before deferred backoff",
                                    exc_info=True,
                                )
                            else:
                                lifecycle.settlement_confirmed = True
                                await self._drain_deferred_account_error_backoffs(pending_backoffs)
                finally:
                    await _release_http_bridge_unanchored_handoffs_for_request(
                        self,
                        request_scope_id=request_scope_id,
                    )
        if not bridge_transport_unavailable:
            return
        logger.warning(
            "stream_responses http bridge upstream unavailable; retrying over http upstream transport request_id=%s",
            request_id,
        )
        stream_with_retry = cast(Callable[..., AsyncIterator[str]], self._stream_with_retry)
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
            file_account_resolution_complete=True,
            upstream_stream_transport_override="http",
            client_ip=client_ip,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        ):
            yield line

    async def _stream_via_http_bridge_impl(
        self: Any,
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_legacy_signature: bool = False,
        proxy_api_authorization: str | None = None,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        rewritten_file_account_id: str | None = None,
        client_ip: str | None = None,
        enforce_openai_sdk_contract: bool = True,
        capacity_startup_wait_event: asyncio.Event | None = None,
        capacity_startup_ready_event: asyncio.Event | None = None,
        deferred_account_backoff_tracker: _DeferredAccountBackoffTracker | None = None,
        _denied_anchor_request_id: str | None = None,
    ) -> AsyncIterator[str]:
        del suppress_text_done_events
        dead_owner_anchor = False
        dead_owner_process_epoch_mismatch = False
        request_id = _denied_anchor_request_id or ensure_request_id()
        dashboard_settings = await _service_get_settings_cache().get()
        runtime_config = _http_bridge_runtime_config(dashboard_settings, _service_get_settings())
        if deferred_account_backoff_tracker is None:
            deferred_account_backoff_tracker = _DeferredAccountBackoffTracker()
        bridge_payload = payload.to_payload()
        bridge_client_metadata = _response_create_client_metadata(
            bridge_payload,
            headers=headers,
            preserve_existing_responses_lite=forwarded_request,
        )
        bridge_uses_responses_lite = bridge_client_metadata is not None and _client_metadata_uses_responses_lite(
            bridge_client_metadata
        )
        if bridge_client_metadata is not None or "client_metadata" in bridge_payload:
            payload = payload.model_copy(update={"client_metadata": bridge_client_metadata})

        def begin_bridge_lifecycle(
            reservation: ApiKeyUsageReservationData | None,
        ) -> _DeferredAccountBackoffLifecycle:
            previous_lifecycle = deferred_account_backoff_tracker.current_lifecycle
            same_reservation = bool(
                previous_lifecycle is not None
                and (
                    previous_lifecycle.reservation is reservation
                    or (
                        previous_lifecycle.reservation is not None
                        and reservation is not None
                        and previous_lifecycle.reservation.reservation_id == reservation.reservation_id
                    )
                )
            )
            pending_backoffs = (
                previous_lifecycle.pending_backoffs
                if previous_lifecycle is not None and not previous_lifecycle.settlement_owned and same_reservation
                else {}
            )
            lifecycle = _DeferredAccountBackoffLifecycle(
                reservation=reservation,
                pending_backoffs=pending_backoffs,
            )
            deferred_account_backoff_tracker.current_lifecycle = lifecycle
            return lifecycle

        def prepare_bridge_request(
            request_payload: ResponsesRequest,
            *,
            reservation: ApiKeyUsageReservationData | None = api_key_reservation,
        ) -> tuple[_WebSocketRequestState, str]:
            if bridge_uses_responses_lite:
                request_state, text_data = self._prepare_http_bridge_request(
                    request_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=reservation,
                    request_id=request_id,
                    client_ip=client_ip,
                    preserve_responses_lite_client_metadata=True,
                )
            else:
                request_state, text_data = self._prepare_http_bridge_request(
                    request_payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=reservation,
                    request_id=request_id,
                    client_ip=client_ip,
                )
            request_state.capacity_startup_wait_event = capacity_startup_wait_event
            request_state.capacity_startup_ready_event = capacity_startup_ready_event
            lifecycle = begin_bridge_lifecycle(request_state.api_key_reservation)
            request_state.deferred_account_error_backoffs = lifecycle.pending_backoffs
            request_state.deferred_account_backoff_tracker = deferred_account_backoff_tracker
            request_state.deferred_account_backoff_lifecycle = lifecycle
            request_state.durable_owner_dead = dead_owner_anchor
            return request_state, text_data

        async def release_unowned_bridge_lifecycle(
            lifecycle: _DeferredAccountBackoffLifecycle | None,
            request_state: _WebSocketRequestState | None,
        ) -> None:
            if lifecycle is None or lifecycle.settlement_owned:
                return
            if request_state is not None:
                await self._release_websocket_request_state_reservation(request_state)
                return
            await self._release_websocket_reservation(lifecycle.reservation)
            lifecycle.settlement_confirmed = True
            await self._drain_deferred_account_error_backoffs(lifecycle.pending_backoffs)

        incoming_turn_state_header = _sticky_key_from_turn_state_header(headers) if not forwarded_request else None
        incoming_session_header = _sticky_key_from_session_header(headers) if not forwarded_request else None
        explicit_prompt_cache_key = _prompt_cache_key_from_request_model(payload)
        had_prompt_cache_key = explicit_prompt_cache_key is not None
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
        if affinity.codex_session_source == "thread_header":
            sticky_key_source = "thread_header"
        elif affinity.kind == StickySessionKind.CODEX_SESSION:
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
            explicit_prompt_cache_key=explicit_prompt_cache_key,
            allow_forwarded_affinity_headers=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
        )
        durable_lookup_turn_state = (
            downstream_turn_state
            if forwarded_request
            and is_http_bridge_account_neutral_replay(
                kind=bridge_session_key.affinity_kind,
                key=bridge_session_key.affinity_key,
            )
            else incoming_turn_state_header
        )
        session_header_fallback_key = (
            _make_http_bridge_session_header_fallback_key(
                headers=headers,
                api_key=api_key,
                explicit_prompt_cache_key=explicit_prompt_cache_key,
            )
            if not forwarded_request
            else None
        )
        durable_session_header_alias = (
            None
            if _codex_backend_identity(headers).thread_id is not None
            else session_header_fallback_key.affinity_key
            if explicit_prompt_cache_key is not None and session_header_fallback_key is not None
            else incoming_session_header
        )
        legacy_anchor_lookup = await _legacy_forward_anchor_lookup(
            durable_bridge=self._durable_bridge,
            bridge_session_key=bridge_session_key,
            turn_state=_sticky_key_from_turn_state_header(headers),
            api_key=api_key,
            previous_response_id=payload.previous_response_id,
            forwarded_request=forwarded_request,
            forwarded_legacy_signature=forwarded_legacy_signature,
        )
        if legacy_anchor_lookup is not None:
            incoming_turn_state_header = _sticky_key_from_turn_state_header(headers)
        original_request_unanchored = _http_bridge_request_needs_unanchored_handoff(
            bridge_session_key,
            _sticky_key_from_turn_state_header(headers),
            payload.previous_response_id,
            forwarded_request,
            forwarded_original_request_unanchored,
        )
        if legacy_anchor_lookup is not None:
            durable_lookup = legacy_anchor_lookup
        else:
            try:
                durable_lookup = await self._durable_bridge.lookup_request_targets(
                    session_key_kind=bridge_session_key.affinity_kind,
                    session_key_value=bridge_session_key.affinity_key,
                    api_key_id=bridge_session_key.api_key_id,
                    turn_state=durable_lookup_turn_state,
                    # A raw process alias is ambiguous when thread-id exists.
                    # Exact turn/response aliases remain independent inputs.
                    session_header=durable_session_header_alias,
                    previous_response_id=payload.previous_response_id,
                )
            except ProxyResponseError:
                # Conflicting durable aliases are a continuity decision, not a
                # metadata outage. Never soften that fail-closed result into a
                # non-durable first-match fallback.
                raise
            except Exception as exc:
                missing_durable_tables = _is_missing_durable_bridge_table_error(exc)
                hard_continuity_lookup = (
                    bridge_session_key.strength == "hard"
                    or incoming_turn_state_header is not None
                    or payload.previous_response_id is not None
                )
                if hard_continuity_lookup:
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="owner_metadata_unavailable",
                        previous_response_id=payload.previous_response_id,
                        session_id=incoming_turn_state_header or incoming_session_header,
                        upstream_error_code="durable_lookup_failed",
                    )
                    logger.warning("Durable bridge continuity lookup failed; failing closed", exc_info=True)
                    raise ProxyResponseError(
                        502,
                        _http_bridge_owner_lookup_unavailable_error_envelope(),
                    ) from exc
                if missing_durable_tables:
                    logger.warning(
                        "Durable bridge tables missing; using ordinary in-memory bridge fallback",
                        exc_info=True,
                    )
                else:
                    logger.warning(
                        "Durable bridge lookup failed; falling back to non-durable request handling",
                        exc_info=True,
                    )
                durable_lookup = None
        if affinity.abandon_unavailable_legacy_owner:
            # A verified goal restart deliberately resends all portable state.
            # The old bridge row is therefore not additional ownership proof:
            # promoting it to preferred_account_id below would bypass the only
            # selection path allowed to atomically retire the raw sticky owner.
            durable_lookup = None
        if durable_lookup is not None and durable_lookup.latest_response_id is not None:
            current_instance = _service_get_settings().http_responses_session_bridge_instance_id
            current_process_epoch = http_bridge_owner_process_epoch()
            dead_owner_process_epoch_mismatch = (
                durable_lookup.owner_instance_id == current_instance
                and durable_lookup.owner_process_epoch is not None
                and durable_lookup.owner_process_epoch != current_process_epoch
            )
            dead_owner_anchor = _http_bridge_durable_owner_is_dead(
                durable_lookup,
                current_instance=current_instance,
                current_process_epoch=current_process_epoch,
            )
        effective_payload = payload
        untrimmed_effective_payload = payload
        proxy_injected_previous_response_id = False
        fresh_upstream_request_text: str | None = None
        client_full_resend_fresh_upstream_request_text: str | None = None
        previous_response_trimmed_input_count: int | None = None
        previous_response_trimmed_input_fingerprint: str | None = None
        durable_full_resend_anchor_count: int | None = None
        durable_full_resend_anchor_fingerprint: str | None = None
        durable_full_resend_fresh_payload: ResponsesRequest | None = None
        durable_full_resend_is_account_neutral: bool | None = None
        durable_full_resend_has_safe_fresh_context = False
        durable_full_resend_retains_required_context_cache: bool | None = None
        durable_full_resend_proof = _verify_durable_full_resend(payload, durable_lookup)
        durable_full_resend_fresh_bridge_proof: _VerifiedDurableFullResend | None = None
        force_local_recovery_creation = False
        payload_looks_like_full_resend = _http_bridge_payload_looks_like_full_resend(payload)
        # Set when the quarantine check below suppresses the durable-anchor
        # injection for a full-resend payload; the session hydration and the
        # session-level anchor injection further down must honor it so the
        # dispatch genuinely goes unanchored instead of rebuilding the same
        # wedged reattach through the session-state side door.
        fresh_reattach_anchor_suppressed_quarantined = False

        def replay_projection_retains_required_context(
            replay_projection: AccountNeutralReplayProjection,
            lookup: DurableBridgeLookup,
        ) -> bool:
            return responses_input_suffix_retains_prior_output(
                replay_projection.input_items,
                stored_count=replay_projection.stored_prefix_count,
                canonical_lite_developer_index=replay_projection.canonical_lite_developer_index,
            ) or (
                lookup.latest_pending_tool_calls is not None
                and responses_input_suffix_matches_pending_tool_calls(
                    replay_projection.input_items,
                    stored_count=replay_projection.stored_prefix_count,
                    pending_tool_calls=lookup.latest_pending_tool_calls,
                    canonical_lite_developer_index=replay_projection.canonical_lite_developer_index,
                )
            )

        def classify_durable_full_resend(
            lookup: DurableBridgeLookup,
        ) -> tuple[int | None, str | None, bool]:
            stored_count = lookup.latest_input_item_count
            if (
                not payload_looks_like_full_resend
                or stored_count is None
                or not _input_prefix_matches_stored_context(
                    payload.input,
                    stored_count=stored_count,
                    stored_fingerprint=lookup.latest_input_full_fingerprint,
                )
                or not isinstance(payload.input, list)
            ):
                return None, None, False
            replay_projection = project_responses_input_for_account_neutral_fresh_replay(
                cast(list[JsonValue], payload.input),
                stored_count=stored_count,
                # Classification only: inline Responses-Lite developer IDs
                # must remain visible until the exact-manifest check rejects
                # response-owned messages. Cross-account replay uses the
                # default ID-stripping projection below.
                preserve_developer_message_ids=True,
            )
            safe_fresh_context = False
            if replay_projection is not None:
                safe_fresh_context = replay_projection_retains_required_context(replay_projection, lookup)
            return stored_count, lookup.latest_input_full_fingerprint, safe_fresh_context

        if durable_lookup is not None:
            (
                durable_full_resend_anchor_count,
                durable_full_resend_anchor_fingerprint,
                durable_full_resend_has_safe_fresh_context,
            ) = classify_durable_full_resend(durable_lookup)
        if (
            durable_full_resend_has_safe_fresh_context
            and durable_full_resend_anchor_count is not None
            and isinstance(payload.input, list)
        ):
            durable_full_resend_retains_required_context_cache = durable_full_resend_has_safe_fresh_context
            replay_projection = project_responses_input_for_account_neutral_fresh_replay(
                cast(list[JsonValue], payload.input),
                stored_count=durable_full_resend_anchor_count,
            )
            if replay_projection is not None:
                durable_full_resend_fresh_payload = _http_bridge_payload_without_previous_response_id(
                    payload
                ).model_copy(update={"input": replay_projection.input_items})
                durable_full_resend_is_account_neutral = _http_bridge_payload_is_account_neutral_fresh_replay(
                    durable_full_resend_fresh_payload
                )
                if durable_lookup is not None:
                    try:
                        _fresh_state, fresh_replay_text = prepare_bridge_request(
                            _http_bridge_payload_without_previous_response_id(payload)
                        )
                        del _fresh_state
                        durable_recovery_attempt_fingerprint = durable_bridge_hash(fresh_replay_text)
                        existing_attempt = await self._durable_bridge.lookup_recovery_attempt(
                            session_id=durable_lookup.session_id,
                            request_fingerprint=durable_recovery_attempt_fingerprint,
                        )
                        if existing_attempt is not None and (
                            durable_lookup.state != HttpBridgeSessionState.ACTIVE
                            or not durable_lookup.lease_is_active(now=utcnow())
                        ):
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "A prior HTTP response outcome is ambiguous; retry after its recovery "
                                    "state settles.",
                                ),
                            )
                    except ProxyResponseError:
                        raise
                    except Exception:
                        logger.warning("Failed to inspect HTTP bridge recovery attempt", exc_info=True)
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "HTTP responses recovery state could not be claimed; retry the request.",
                            ),
                        )
        durable_anchor_trimmable = durable_full_resend_anchor_count is not None
        durable_model_transition_lookup = (
            durable_lookup
            if durable_lookup is not None and not _http_bridge_models_compatible(durable_lookup.model, payload.model)
            else None
        )
        durable_model_transition_requires_owner = durable_model_transition_lookup is not None and (
            payload.previous_response_id is not None
            or bridge_session_key.strength == "hard"
            or (
                bridge_session_key.affinity_kind == "prompt_cache"
                and _http_bridge_request_stage(
                    headers=headers,
                    payload=payload,
                    durable_lookup=durable_model_transition_lookup,
                )
                == "follow_up"
                and durable_model_transition_lookup.latest_turn_state is not None
            )
        )
        if durable_model_transition_lookup is not None:
            _log_http_bridge_event(
                "model_transition_isolated",
                bridge_session_key,
                account_id=durable_model_transition_lookup.account_id,
                model=payload.model,
                detail=f"previous_model={durable_model_transition_lookup.model}",
                cache_key_family=bridge_session_key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=durable_model_transition_requires_owner,
            )
            if is_http_bridge_account_neutral_replay(
                kind=durable_model_transition_lookup.canonical_kind,
                key=durable_model_transition_lookup.canonical_key,
            ):
                replay_kind, replay_key = make_http_bridge_account_neutral_replay_key(uuid4().hex)
                bridge_session_key = _HTTPBridgeSessionKey(
                    replay_kind,
                    replay_key,
                    bridge_session_key.api_key_id,
                )
                force_local_recovery_creation = True
            durable_lookup = None
            dead_owner_anchor = False
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
                durable_lookup=durable_lookup,
            )
            forwards_to_active_owner = await self._http_bridge_can_forward_to_active_owner(durable_lookup)
            fresh_reattach_can_use_durable_anchor = (
                not live_local_session_exists
                and not forwards_to_active_owner
                and payload.previous_response_id is None
                and not payload.conversation
                and bridge_session_key.strength == "hard"
                and durable_lookup.latest_response_id is not None
                and (not payload_looks_like_full_resend or durable_anchor_trimmable)
            )
            if payload_looks_like_full_resend and _http_bridge_session_key_quarantined(self, bridge_session_key):
                # The previous attach on this key proved silent/wedged
                # (#1534). The client's own payload already carries the full
                # conversation, so send it unanchored on the fresh path
                # instead of rebuilding the same reattach. Delta-only
                # payloads keep the anchor: it is their only way to convey
                # prior context (same boundary as the fenced anchor clear).
                # Evaluated independently of the fresh-reattach eligibility
                # above: even when that gate is already false (for example a
                # conversation-scoped payload, a live alias session, or an
                # active-owner forward that later falls back to a local
                # rebind), the suppression must still reach the session
                # hydration, session-level injection, and recovery injection
                # paths below.
                fresh_reattach_can_use_durable_anchor = False
                fresh_reattach_anchor_suppressed_quarantined = True
                _log_http_bridge_event(
                    "fresh_reattach_anchor_skipped_quarantined",
                    bridge_session_key,
                    account_id=durable_lookup.account_id,
                    model=payload.model,
                    detail=f"response_id={durable_lookup.latest_response_id}",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(payload.model) if payload.model else None,
                )
            if (
                fresh_reattach_can_use_durable_anchor
                and payload_looks_like_full_resend
                and durable_full_resend_has_safe_fresh_context
            ):
                if durable_full_resend_proof is not None and durable_full_resend_proof.matches(payload, durable_lookup):
                    durable_full_resend_fresh_bridge_proof = durable_full_resend_proof
                    # The client already supplied a proved complete fresh
                    # request. Adding a durable anchor here can strand it on
                    # the new WebSocket.
                    _log_http_bridge_event(
                        "fresh_reattach_full_resend_preserved",
                        bridge_session_key,
                        account_id=durable_lookup.account_id,
                        model=payload.model,
                        detail="outcome=client_unanchored_full_resend",
                        cache_key_family=bridge_session_key.affinity_kind,
                        model_class=_extract_model_class(payload.model) if payload.model else None,
                    )
                else:
                    effective_payload = payload.model_copy(
                        update={"previous_response_id": durable_lookup.latest_response_id}
                    )
                    proxy_injected_previous_response_id = True
                    _fresh_request_state, fresh_upstream_request_text = prepare_bridge_request(payload)
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
            elif fresh_reattach_can_use_durable_anchor:
                effective_payload = payload.model_copy(
                    update={"previous_response_id": durable_lookup.latest_response_id}
                )
                proxy_injected_previous_response_id = True
                _fresh_request_state, fresh_upstream_request_text = prepare_bridge_request(payload)
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
        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=bridge_session_key.affinity_kind,
            key=bridge_session_key.affinity_key,
        )
        if account_neutral_recovery:
            affinity = _AffinityPolicy()
            incoming_turn_state_header = None
            session_header_fallback_key = None
            durable_session_header_alias = None
        owner_bound_full_resend_ignores_broad_session = (
            not forwarded_request
            and durable_full_resend_fresh_bridge_proof is not None
            and durable_full_resend_fresh_bridge_proof.matches(payload, durable_lookup)
            and affinity.codex_session_source == "session_header"
        )
        if owner_bound_full_resend_ignores_broad_session:
            # The durable owner remains required through request_state below.
            # Remove only the broad client alias that can resolve a stale raw
            # compatibility row; keep CODEX_SESSION semantics so the new
            # bridge can anchor later incremental turns to its fresh response.
            affinity = _AffinityPolicy(kind=StickySessionKind.CODEX_SESSION)
            incoming_session_header = None
            session_header_fallback_key = None
            durable_session_header_alias = None
            _log_http_bridge_event(
                "fresh_reattach_broad_session_owner_ignored",
                bridge_session_key,
                account_id=durable_lookup.account_id if durable_lookup is not None else None,
                model=payload.model,
                detail=(f"stored_items={durable_full_resend_fresh_bridge_proof.stored_input_item_count}"),
                cache_key_family=bridge_session_key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
        if effective_payload.previous_response_id is not None and isinstance(effective_payload.input, list):
            previous_response_input_items = cast(list[JsonValue], effective_payload.input)
            trimmed_input_items = _trim_http_bridge_previous_response_input_items(previous_response_input_items)
            if len(trimmed_input_items) != len(previous_response_input_items):
                previous_response_trimmed_input_count = len(previous_response_input_items)
                previous_response_trimmed_input_fingerprint = _fingerprint_input_items(previous_response_input_items)
                effective_payload = effective_payload.model_copy(update={"input": trimmed_input_items})
        request_state, text_data = prepare_bridge_request(effective_payload)
        if proxy_injected_previous_response_id and request_state.previous_response_id is not None:
            # Capture denial provenance before owner lookups below can suspend
            # while a detached predecessor rejects the same durable anchor.
            _bind_http_bridge_proxy_injected_anchor(
                self,
                request_state,
                response_id=request_state.previous_response_id,
                fence_request_id=request_id,
            )
        request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
        request_state.affinity_policy = affinity
        _apply_http_bridge_downstream_turn_state(
            request_state,
            downstream_turn_state=downstream_turn_state,
            incoming_turn_state_header=incoming_turn_state_header,
        )
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
        if (
            request_state.preferred_account_id is None
            and durable_model_transition_lookup is not None
            and durable_model_transition_requires_owner
        ):
            request_state.preferred_account_id = durable_model_transition_lookup.account_id
        local_previous_response_owner: str | None = None
        indexed_previous_response_owner: str | None = None
        if request_state.previous_response_id is not None:
            local_previous_response_owner = await self._http_bridge_local_owner_account_id(
                key=bridge_session_key,
                incoming_turn_state=incoming_turn_state_header,
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
                durable_lookup=durable_lookup,
            )
            indexed_previous_response_owner = await self._resolve_websocket_previous_response_owner(
                previous_response_id=request_state.previous_response_id,
                api_key=api_key,
                session_id=request_state.session_id,
                surface="http_bridge",
            )
            try:
                request_state.preferred_account_id = resolve_required_account_id(
                    ("durable bridge", request_state.preferred_account_id),
                    ("live bridge", local_previous_response_owner),
                    ("previous-response index", indexed_previous_response_owner),
                )
            except ProxyResponseError:
                # The request-log owner cache is intentionally only a fast
                # path. If it conflicts with a durable anchor, re-read the
                # authoritative request-log row once before failing closed;
                # this repairs stale in-process pins without adding a DB read
                # to the normal continuation path.
                if durable_lookup is None or indexed_previous_response_owner is None:
                    raise
                indexed_previous_response_owner = await self._resolve_websocket_previous_response_owner(
                    previous_response_id=request_state.previous_response_id,
                    api_key=api_key,
                    session_id=request_state.session_id,
                    surface="http_bridge",
                    force_request_log_lookup=True,
                )
                request_state.preferred_account_id = resolve_required_account_id(
                    ("durable bridge", request_state.preferred_account_id),
                    ("live bridge", local_previous_response_owner),
                    ("previous-response index", indexed_previous_response_owner),
                )
        durable_lookup_requires_owner = durable_lookup is not None and (
            request_state.previous_response_id is not None
            or bridge_session_key.strength == "hard"
            or (
                bridge_session_key.affinity_kind == "prompt_cache"
                and request_state.request_stage == "follow_up"
                and durable_lookup.latest_turn_state is not None
            )
        )
        durable_owner_missing = (
            durable_lookup is not None and durable_lookup_requires_owner and durable_lookup.account_id is None
        )
        model_transition_owner_missing = (
            durable_model_transition_lookup is not None
            and durable_model_transition_requires_owner
            and durable_model_transition_lookup.account_id is None
        )
        required_continuity_owner_missing = (
            (request_state.previous_response_id is not None and request_state.preferred_account_id is None)
            or durable_owner_missing
            or model_transition_owner_missing
        )
        continuity_preferred_account_id = request_state.preferred_account_id
        # Existing bridge/response ownership and file ownership are equally
        # hard. Merge them before transport creation; source ordering must not
        # turn a conflict into an implicit account switch.
        request_state.preferred_account_id = resolve_required_account_id(
            ("previous response or bridge", request_state.preferred_account_id),
            ("input file", rewritten_file_account_id),
        )
        preferred_account_has_continuity_provenance = (
            continuity_preferred_account_id is not None
            and request_state.preferred_account_id == continuity_preferred_account_id
        )
        file_required_preferred_account = rewritten_file_account_id is not None
        if proxy_injected_previous_response_id:
            request_state.proxy_injected_previous_response_id = True
            request_state.proxy_injected_anchor_had_full_resend_payload = payload_looks_like_full_resend
            request_state.fresh_upstream_request_text = fresh_upstream_request_text or text_data
            # Durable-anchor injection runs when the incoming payload is
            # *not* a full resend, or when a full resend failed the sealed
            # owner-bound proof (missing owner metadata or fingerprint), so
            # the captured unanchored text cannot be assumed to carry the
            # complete conversational context the anchor was pointing at.
            # Only the trim branch below (which verifies the stored prefix
            # fingerprint) is allowed to flip this flag to ``True``.
            request_state.fresh_upstream_request_is_retry_safe = False
        elif (
            effective_payload.previous_response_id is not None
            and payload_looks_like_full_resend
            and durable_full_resend_anchor_count is not None
            and durable_full_resend_has_safe_fresh_context
        ):
            # A client-provided full resend carries the same proof as a
            # proxy-injected anchor: the stored prefix matches and the fresh
            # suffix retains the prior output/tool context. Capture the
            # verified anchor-free body so a retry can use it without sending
            # previous_response_id again.
            client_full_resend_payload = _http_bridge_payload_without_previous_response_id(untrimmed_effective_payload)
            _fresh_state, client_full_resend_fresh_upstream_request_text = prepare_bridge_request(
                client_full_resend_payload
            )
            del _fresh_state
            request_state.fresh_upstream_request_text = client_full_resend_fresh_upstream_request_text
            request_state.fresh_upstream_request_is_retry_safe = True
        settings = _service_get_settings()
        request_deadline = request_state.started_at + _http_bridge_request_budget_seconds(settings)
        session_creation_headers = (
            without_http_bridge_session_affinity_headers(headers)
            if account_neutral_recovery or owner_bound_full_resend_ignores_broad_session
            else dict(headers)
        )
        fresh_replay_excluded_account_ids: set[str] = set()
        unanchored_fork_spill_attempted = False
        verified_stale_anchor_generation_captured = False
        verified_stale_anchor_circuit_key: _HTTPBridgeSessionKey | None = None
        verified_stale_anchor_generation: tuple[int, float, int, float, int, float, float] | None = None
        verified_stale_anchor_quarantine_generation: int | None = None

        def durable_full_resend_retains_required_context() -> bool:
            nonlocal durable_full_resend_retains_required_context_cache

            if (
                durable_full_resend_anchor_count is None
                or durable_full_resend_anchor_fingerprint is None
                or not isinstance(payload.input, list)
            ):
                return False
            if durable_full_resend_retains_required_context_cache is None:
                eligibility_projection = project_responses_input_for_account_neutral_fresh_replay(
                    cast(list[JsonValue], payload.input),
                    stored_count=durable_full_resend_anchor_count,
                    preserve_developer_message_ids=True,
                )
                if eligibility_projection is None or durable_lookup is None:
                    return False
                durable_full_resend_retains_required_context_cache = replay_projection_retains_required_context(
                    eligibility_projection,
                    durable_lookup,
                )
            return bool(durable_full_resend_retains_required_context_cache)

        def durable_full_resend_allows_account_neutral_replay() -> bool:
            nonlocal durable_full_resend_fresh_payload
            nonlocal durable_full_resend_is_account_neutral

            if rewritten_file_account_id is not None or not durable_full_resend_retains_required_context():
                return False
            if durable_full_resend_fresh_payload is None:
                assert isinstance(payload.input, list)
                assert durable_full_resend_anchor_count is not None
                replay_projection = project_responses_input_for_account_neutral_fresh_replay(
                    cast(list[JsonValue], payload.input),
                    stored_count=durable_full_resend_anchor_count,
                )
                if replay_projection is None:
                    return False
                durable_full_resend_fresh_payload = _http_bridge_payload_without_previous_response_id(
                    payload
                ).model_copy(update={"input": replay_projection.input_items})
            if durable_full_resend_is_account_neutral is None:
                durable_full_resend_is_account_neutral = _http_bridge_payload_is_account_neutral_fresh_replay(
                    durable_full_resend_fresh_payload
                )
            return durable_full_resend_is_account_neutral

        def owner_unavailable_allows_account_neutral_replay(exc: ProxyResponseError) -> bool:
            return (
                _http_bridge_is_previous_response_owner_unavailable(exc)
                and durable_full_resend_allows_account_neutral_replay()
            )

        def switch_to_account_neutral_replay(
            *,
            event: str = "owner_unavailable_fresh_resend",
            detail: str = "outcome=projected_plaintext_full_resend_without_anchor",
            preserve_operation: bool = False,
        ) -> None:
            nonlocal account_neutral_recovery
            nonlocal affinity
            nonlocal bridge_session_key
            nonlocal dead_owner_anchor
            nonlocal dead_owner_process_epoch_mismatch
            nonlocal durable_full_resend_anchor_count
            nonlocal durable_full_resend_anchor_fingerprint
            nonlocal durable_full_resend_fresh_payload
            nonlocal durable_full_resend_is_account_neutral
            nonlocal durable_lookup
            nonlocal effective_payload
            nonlocal file_required_preferred_account
            nonlocal force_local_recovery_creation
            nonlocal client_full_resend_fresh_upstream_request_text
            nonlocal fresh_upstream_request_text
            nonlocal incoming_turn_state_header
            nonlocal previous_response_trimmed_input_count
            nonlocal previous_response_trimmed_input_fingerprint
            nonlocal proxy_injected_previous_response_id
            nonlocal request_state
            nonlocal session_creation_headers
            nonlocal session_header_fallback_key
            nonlocal text_data
            nonlocal untrimmed_effective_payload

            preserve_operation_identity = preserve_operation
            prior_operation_id = request_state.operation_id if preserve_operation_identity else None
            prior_operation_fingerprint = request_state.operation_fingerprint if preserve_operation_identity else None
            prior_operation_parent_response_id = (
                request_state.operation_parent_response_id or request_state.previous_response_id
                if preserve_operation_identity
                else None
            )
            prior_operation_registered = request_state.operation_registered if preserve_operation_identity else False
            prior_operation_attempt_generation = (
                request_state.operation_attempt_generation if preserve_operation_identity else 0
            )
            prior_operation_persisted_response_id = (
                request_state.operation_persisted_response_id if preserve_operation_identity else None
            )
            failed_owner_id = request_state.preferred_account_id
            _log_http_bridge_event(
                event,
                bridge_session_key,
                account_id=failed_owner_id,
                model=payload.model,
                detail=detail,
                cache_key_family=bridge_session_key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
            )
            if failed_owner_id is not None:
                fresh_replay_excluded_account_ids.add(failed_owner_id)
            session_creation_headers = without_http_bridge_session_affinity_headers(session_creation_headers)
            incoming_turn_state_header = None
            session_header_fallback_key = None
            affinity = _AffinityPolicy()
            replay_kind, replay_key = make_http_bridge_account_neutral_replay_key(uuid4().hex)
            bridge_session_key = _HTTPBridgeSessionKey(replay_kind, replay_key, bridge_session_key.api_key_id)
            account_neutral_recovery = True
            force_local_recovery_creation = True
            fresh_payload = durable_full_resend_fresh_payload
            if fresh_payload is None:
                raise RuntimeError("account-neutral replay projection missing after eligibility check")
            request_state, text_data = prepare_bridge_request(fresh_payload)
            if preserve_operation_identity:
                request_state.operation_id = prior_operation_id
                request_state.operation_fingerprint = prior_operation_fingerprint
                request_state.operation_parent_response_id = prior_operation_parent_response_id
                request_state.operation_registered = prior_operation_registered
                request_state.operation_attempt_generation = prior_operation_attempt_generation
                request_state.operation_persisted_response_id = prior_operation_persisted_response_id
                request_state.operation_rebind_required = True
            request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
            request_state.affinity_policy = affinity
            request_state.excluded_account_ids.update(fresh_replay_excluded_account_ids)
            if downstream_turn_state is not None:
                request_state.session_id = _normalize_session_id(downstream_turn_state)
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=fresh_payload,
                durable_lookup=None,
            )
            request_state.preferred_account_id = None
            effective_payload = fresh_payload
            untrimmed_effective_payload = fresh_payload
            proxy_injected_previous_response_id = False
            fresh_upstream_request_text = None
            client_full_resend_fresh_upstream_request_text = None
            previous_response_trimmed_input_count = None
            previous_response_trimmed_input_fingerprint = None
            durable_full_resend_anchor_count = None
            durable_full_resend_anchor_fingerprint = None
            durable_full_resend_fresh_payload = None
            durable_full_resend_is_account_neutral = None
            durable_lookup = None
            dead_owner_anchor = False
            dead_owner_process_epoch_mismatch = False
            file_required_preferred_account = False

        if (
            dead_owner_anchor
            and durable_lookup is not None
            and durable_lookup.state == HttpBridgeSessionState.ACTIVE
            and dead_owner_process_epoch_mismatch
            and durable_full_resend_allows_account_neutral_replay()
        ):
            _log_http_bridge_event(
                "dead_owner_fresh_resend",
                bridge_session_key,
                account_id=request_state.preferred_account_id,
                model=payload.model,
                detail="outcome=projected_plaintext_full_resend_without_anchor",
                cache_key_family=bridge_session_key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            switch_to_account_neutral_replay()

        if required_continuity_owner_missing:
            owner_unavailable = ProxyResponseError(
                502,
                openai_error(
                    "previous_response_owner_unavailable",
                    "Previous response owner account is unavailable; retry later.",
                ),
            )
            _record_continuity_fail_closed(
                surface="http_bridge",
                reason="owner_account_unavailable",
                previous_response_id=request_state.previous_response_id,
                session_id=request_state.session_id,
                upstream_error_code="owner_lookup_miss",
            )
            raise owner_unavailable

        while True:
            try:
                session_or_forward = await self._get_or_create_http_bridge_session(
                    bridge_session_key,
                    headers=dict(session_creation_headers),
                    affinity=affinity,
                    api_key=api_key,
                    request_model=effective_payload.model,
                    request_service_tier=request_state.requested_service_tier,
                    idle_ttl_seconds=_effective_http_bridge_idle_ttl_seconds(
                        affinity=affinity,
                        idle_ttl_seconds=idle_ttl_seconds,
                        codex_idle_ttl_seconds=codex_idle_ttl_seconds,
                        prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
                    ),
                    max_sessions=max_sessions,
                    previous_response_id=request_state.previous_response_id,
                    gateway_safe_mode=runtime_config.gateway_safe_mode,
                    allow_forward_to_owner=(
                        not fresh_replay_excluded_account_ids
                        and not force_local_recovery_creation
                        and not affinity.abandon_unavailable_legacy_owner
                    ),
                    # A single-instance restart can leave an anchored durable
                    # row owned by the previous process epoch. Once the old
                    # owner is proven dead, let the initial continuation
                    # rebind locally; clustered deployments still route or
                    # fail closed through the normal owner path.
                    allow_previous_response_recovery_rebind=(
                        request_state.previous_response_id is not None
                        and dead_owner_anchor
                        and not _http_bridge_requires_cluster_registration(settings)
                    ),
                    forwarded_request=forwarded_request,
                    forwarded_original_request_unanchored=original_request_unanchored,
                    forwarded_affinity_kind=forwarded_affinity_kind,
                    forwarded_affinity_key=forwarded_affinity_key,
                    durable_lookup=durable_lookup,
                    request_stage=request_state.request_stage,
                    preferred_account_id=request_state.preferred_account_id,
                    preferred_account_has_continuity_provenance=preferred_account_has_continuity_provenance,
                    fallback_on_preferred_account_unavailable=not file_required_preferred_account,
                    request_usage_budget=request_state.request_usage_budget,
                    request_deadline=request_deadline,
                    session_header_fallback_key=session_header_fallback_key,
                    exclude_account_ids=fresh_replay_excluded_account_ids or None,
                    deferred_account_backoff_lifecycle=request_state.deferred_account_backoff_lifecycle,
                    defer_account_health_writes=request_state.api_key_reservation is not None,
                )
            except ProxyResponseError as exc:
                # Session creation failed before the turn was submitted
                # upstream; record that provenance so the outer wrapper knows
                # a raw-HTTP replay cannot dispatch the turn twice.
                setattr(exc, _HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
                # A bridge-injected anchor lives only on ``effective_payload``:
                # the raw path recovers the owner from the turn-state header
                # but never injects a response anchor, so replaying the
                # incoming payload would send the new turn alone and silently
                # drop the prior conversation. Only a payload that already
                # carries its own continuity is a safe fresh raw-HTTP replay.
                if effective_payload.previous_response_id != payload.previous_response_id:
                    setattr(exc, _HTTP_BRIDGE_PREPARED_ANCHOR_ATTR, True)
                if not owner_unavailable_allows_account_neutral_replay(exc):
                    exc_code, _exc_message = _proxy_error_code_message(exc)
                    if not unanchored_fork_spill_attempted and _http_bridge_unanchored_fork_can_spill_on_cap(
                        error_code=exc_code,
                        affinity_kind=bridge_session_key.affinity_kind,
                        original_request_unanchored=original_request_unanchored,
                        payload=effective_payload,
                        preferred_account_id=request_state.preferred_account_id,
                    ):
                        unanchored_fork_spill_attempted = True
                        _log_http_bridge_event(
                            "unanchored_fork_cap_spill",
                            bridge_session_key,
                            account_id=request_state.preferred_account_id,
                            model=effective_payload.model,
                            detail=f"reason={exc_code}, outcome=retry_without_preferred_account",
                            cache_key_family=bridge_session_key.affinity_kind,
                            model_class=_extract_model_class(effective_payload.model)
                            if effective_payload.model
                            else None,
                        )
                        request_state.preferred_account_id = None
                        preferred_account_has_continuity_provenance = False
                        continue
                    wait_plan = _http_bridge_capacity_wait_plan(exc, request_deadline=request_deadline)
                    if wait_plan is not None:
                        bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                        logger.info(
                            "Waiting for an account to recover before retrying HTTP bridge session creation "
                            "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
                            request_id,
                            effective_payload.model,
                            bounded_wait_seconds,
                            account_capacity_wait_seconds,
                            message,
                        )
                        async for line in _iter_account_capacity_wait_sse(
                            request_id=request_id,
                            reason=message,
                            sleep_seconds=bounded_wait_seconds,
                            emit_keepalives=not propagate_http_errors,
                            request_state=request_state,
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
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
                switch_to_account_neutral_replay()
                continue
            break
        if isinstance(session_or_forward, _HTTPBridgeOwnerForward):
            await _current_origin_legacy_owner_anchor_lookup(
                durable_bridge=self._durable_bridge,
                bridge_session_key=session_or_forward.key,
                turn_state=incoming_turn_state_header,
                api_key=api_key,
                previous_response_id=effective_payload.previous_response_id,
                forwarded_request=forwarded_request,
            )
            forwarded_any = False
            try:
                async for line in self._forward_http_bridge_request_to_owner(
                    owner_forward=session_or_forward,
                    payload=effective_payload,
                    headers=session_creation_headers,
                    api_key_reservation=api_key_reservation,
                    codex_session_affinity=codex_session_affinity,
                    downstream_turn_state=downstream_turn_state,
                    file_owner_account_id=rewritten_file_account_id,
                    request_started_at=request_state.started_at,
                    proxy_api_authorization=proxy_api_authorization,
                    client_ip=client_ip,
                ):
                    forwarded_any = True
                    yield line
                return
            except ProxyResponseError as exc:
                if not _owner_forward_failure_allows_local_recovery(exc):
                    raise
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
                owner_forward_fresh_replay = owner_unavailable_allows_account_neutral_replay(exc)
                if owner_forward_fresh_replay:
                    switch_to_account_neutral_replay()
                should_attempt_previous_response_recovery = not owner_forward_fresh_replay and (
                    effective_payload.previous_response_id is not None
                    and _http_bridge_should_attempt_local_previous_response_recovery(exc)
                )
                should_attempt_bootstrap_rebind = (
                    not owner_forward_fresh_replay
                    and _http_bridge_should_attempt_local_bootstrap_rebind(
                        exc,
                        key=bridge_session_key,
                        headers=headers,
                        previous_response_id=effective_payload.previous_response_id,
                    )
                )
                should_attempt_turn_state_takeover = False
                if (
                    not owner_forward_fresh_replay
                    and not should_attempt_previous_response_recovery
                    and not should_attempt_bootstrap_rebind
                ):
                    takeover_turn_state = _http_bridge_turn_state_anchor_for_owner_failure(
                        exc,
                        headers=headers,
                        previous_response_id=effective_payload.previous_response_id,
                    )
                    if takeover_turn_state is not None:
                        # Reuse the routing lookup semantics (alias resolution
                        # plus the latest-turn-state fallback) so a row that was
                        # originally found without a registered alias remains
                        # takeover-eligible; an alias-only lookup would return
                        # None and lose the durable anchor for the local retry.
                        try:
                            fresh_turn_state_lookup = await self._durable_bridge.lookup_request_targets(
                                session_key_kind=bridge_session_key.affinity_kind,
                                session_key_value=bridge_session_key.affinity_key,
                                api_key_id=bridge_session_key.api_key_id,
                                turn_state=takeover_turn_state,
                                session_header=durable_session_header_alias,
                                previous_response_id=effective_payload.previous_response_id,
                            )
                        except Exception:
                            logger.warning(
                                "Turn-state takeover lookup failed after owner forward failure; failing closed",
                                exc_info=True,
                            )
                        else:
                            if _http_bridge_durable_lookup_allows_turn_state_takeover(fresh_turn_state_lookup):
                                durable_lookup = fresh_turn_state_lookup
                                if fresh_turn_state_lookup is None:
                                    durable_full_resend_anchor_count = None
                                    durable_full_resend_anchor_fingerprint = None
                                    durable_full_resend_has_safe_fresh_context = False
                                else:
                                    (
                                        durable_full_resend_anchor_count,
                                        durable_full_resend_anchor_fingerprint,
                                        durable_full_resend_has_safe_fresh_context,
                                    ) = classify_durable_full_resend(fresh_turn_state_lookup)
                                    continuity_preferred_account_id = fresh_turn_state_lookup.account_id
                                    request_state.preferred_account_id = resolve_required_account_id(
                                        (
                                            "refreshed previous response or bridge",
                                            continuity_preferred_account_id,
                                        ),
                                        ("input file", rewritten_file_account_id),
                                    )
                                    preferred_account_has_continuity_provenance = (
                                        continuity_preferred_account_id is not None
                                        and request_state.preferred_account_id == continuity_preferred_account_id
                                    )
                                refreshed_continuity_routable = (
                                    fresh_turn_state_lookup is None
                                    or fresh_turn_state_lookup.latest_response_id is None
                                    or fresh_turn_state_lookup.account_id is not None
                                )
                                should_attempt_turn_state_takeover = refreshed_continuity_routable and (
                                    not payload_looks_like_full_resend
                                    or (
                                        fresh_turn_state_lookup is not None
                                        and fresh_turn_state_lookup.account_id is not None
                                        and durable_full_resend_anchor_count is not None
                                        and (
                                            durable_full_resend_has_safe_fresh_context
                                            or fresh_turn_state_lookup.latest_response_id is not None
                                        )
                                    )
                                )
                if (
                    not owner_forward_fresh_replay
                    and not should_attempt_previous_response_recovery
                    and not should_attempt_bootstrap_rebind
                    and not should_attempt_turn_state_takeover
                ):
                    raise
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    if owner_forward_fresh_replay:
                        recover_path = "owner_forward_fresh_replay"
                    elif should_attempt_previous_response_recovery:
                        recover_path = "owner_forward_fail"
                    elif should_attempt_turn_state_takeover:
                        recover_path = "owner_forward_turn_state"
                    else:
                        recover_path = "owner_forward_bootstrap"
                    bridge_durable_recover_total.labels(path=recover_path).inc()
                if owner_forward_fresh_replay:
                    recover_event = "owner_unavailable_fresh_resend"
                    recover_detail = "outcome=local_fresh_replay_after_forward_failure"
                elif should_attempt_previous_response_recovery:
                    recover_event = "previous_response_recover_local"
                    recover_detail = "outcome=local_rebind_after_forward_failure"
                elif should_attempt_turn_state_takeover:
                    recover_event = "turn_state_takeover_local"
                    recover_detail = "outcome=local_takeover_after_forward_failure"
                else:
                    recover_event = "bootstrap_rebind_local"
                    recover_detail = "outcome=local_bootstrap_after_forward_failure"
                _log_http_bridge_event(
                    recover_event,
                    bridge_session_key,
                    account_id=None,
                    model=effective_payload.model,
                    detail=recover_detail,
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                while True:
                    try:
                        session = await self._get_or_create_http_bridge_session(
                            bridge_session_key,
                            headers=dict(session_creation_headers),
                            affinity=affinity,
                            api_key=api_key,
                            request_model=effective_payload.model,
                            request_service_tier=request_state.requested_service_tier,
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
                            allow_previous_response_recovery_rebind=(
                                should_attempt_previous_response_recovery and not owner_forward_fresh_replay
                            ),
                            allow_bootstrap_owner_rebind=(
                                (should_attempt_bootstrap_rebind or should_attempt_turn_state_takeover)
                                and not owner_forward_fresh_replay
                            ),
                            durable_lookup=durable_lookup,
                            request_stage=(
                                request_state.request_stage
                                if owner_forward_fresh_replay
                                else (
                                    "reattach"
                                    if should_attempt_previous_response_recovery or should_attempt_turn_state_takeover
                                    else "bootstrap_rebind"
                                )
                            ),
                            preferred_account_id=request_state.preferred_account_id,
                            preferred_account_has_continuity_provenance=preferred_account_has_continuity_provenance,
                            request_usage_budget=request_state.request_usage_budget,
                            session_header_fallback_key=session_header_fallback_key,
                            request_deadline=request_deadline,
                            exclude_account_ids=request_state.excluded_account_ids or None,
                            deferred_account_backoff_lifecycle=request_state.deferred_account_backoff_lifecycle,
                            defer_account_health_writes=request_state.api_key_reservation is not None,
                        )
                    except ProxyResponseError as capacity_exc:
                        if owner_unavailable_allows_account_neutral_replay(capacity_exc):
                            switch_to_account_neutral_replay()
                            owner_forward_fresh_replay = True
                            continue
                        wait_plan = _http_bridge_capacity_wait_plan(capacity_exc, request_deadline=request_deadline)
                        if wait_plan is None:
                            raise
                        bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                        logger.info(
                            "Waiting for an account to recover before retrying HTTP bridge recovery session creation "
                            "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f path=%s error=%s",
                            request_id,
                            effective_payload.model,
                            bounded_wait_seconds,
                            account_capacity_wait_seconds,
                            "owner_forward_fail"
                            if should_attempt_previous_response_recovery
                            else "owner_forward_bootstrap",
                            message,
                        )
                        async for line in _iter_account_capacity_wait_sse(
                            request_id=request_id,
                            reason=message,
                            sleep_seconds=bounded_wait_seconds,
                            emit_keepalives=not propagate_http_errors,
                            request_state=request_state,
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
                    break
                _record_bridge_reattach(
                    path=(
                        "owner_forward_fresh_replay"
                        if owner_forward_fresh_replay
                        else (
                            "owner_forward_fail"
                            if should_attempt_previous_response_recovery
                            else "owner_forward_bootstrap"
                        )
                    ),
                    outcome="success",
                )
                # Best-effort synthetic interrupted-output injection for the
                # local recovery request. The pending tool-call metadata lives
                # in the owning instance's in-memory session state, so after an
                # owner-forward failure it is only available when the rebound
                # local session still carries it (for example when ownership
                # flapped back to this instance). A fresh local rebind cannot
                # know the interrupted call ids; in that case the anchored
                # request is resubmitted unmodified (matching pre-injection
                # behavior) and an upstream missing-tool-output error is
                # classified and masked as a retryable continuity failure.
                recovery_payload = effective_payload
                recovery_anchor_input_count: int | None = None
                recovery_anchor_input_fingerprint: str | None = None
                recovery_anchor_fence_generation: int | None = None
                recovery_anchor_fence_was_already_denied = False
                recovery_anchor_fence_response_id: str | None = None
                if (
                    not owner_forward_fresh_replay
                    # The quarantine skip (#1534) applies to the local recovery
                    # rebind as well: re-injecting the suppressed anchor here
                    # would rebuild the same wedged reattach.
                    and not fresh_reattach_anchor_suppressed_quarantined
                    and not durable_full_resend_has_safe_fresh_context
                    and recovery_payload.previous_response_id is None
                    and durable_lookup is not None
                    and durable_lookup.latest_response_id is not None
                    and durable_full_resend_anchor_count is not None
                    and durable_full_resend_anchor_fingerprint is not None
                    and isinstance(recovery_payload.input, list)
                    and len(recovery_payload.input) > durable_full_resend_anchor_count
                ):
                    # The recovery rebind above is allowed to bind this session to
                    # an account other than the durable owner. A previous_response_id
                    # is account-scoped upstream, so replaying the durable anchor on
                    # a different account sends an anchor upstream cannot resolve
                    # with the history trimmed away: no response.created arrives and
                    # the per-bridge response-create gate wedges. Resend the full
                    # history on the serving account instead.
                    if durable_lookup.account_id != session.account.id:
                        _log_http_bridge_event(
                            "cross_account_anchor_declined",
                            bridge_session_key,
                            account_id=session.account.id,
                            model=recovery_payload.model,
                            detail=(
                                "site=owner_forward_recovery, "
                                f"response_id={durable_lookup.latest_response_id}, "
                                f"anchor_account_id={durable_lookup.account_id}, "
                                "outcome=full_history_resend"
                            ),
                            cache_key_family=bridge_session_key.affinity_kind,
                            model_class=_extract_model_class(recovery_payload.model)
                            if recovery_payload.model
                            else None,
                            owner_check_applied=True,
                        )
                    else:
                        recovery_input = cast(list[JsonValue], recovery_payload.input)
                        recovery_anchor_input_count = len(recovery_input)
                        recovery_anchor_input_fingerprint = _fingerprint_input_items(recovery_input)
                        recovery_payload = recovery_payload.model_copy(
                            update={
                                "previous_response_id": durable_lookup.latest_response_id,
                                "input": recovery_input[durable_full_resend_anchor_count:],
                            }
                        )
                        recovery_anchor_fence_response_id = durable_lookup.latest_response_id
                        assert recovery_anchor_fence_response_id is not None
                        (
                            recovery_anchor_fence_generation,
                            recovery_anchor_fence_was_already_denied,
                        ) = _capture_http_bridge_denied_anchor_fence(
                            self,
                            recovery_anchor_fence_response_id,
                            request_id,
                        )
                        if durable_lookup.latest_response_id != session.last_completed_response_id:
                            session.last_pending_tool_calls = {}
                        session.last_completed_response_id = durable_lookup.latest_response_id
                        session.last_completed_response_account_id = durable_lookup.account_id
                        session.last_completed_input_count = durable_full_resend_anchor_count
                        session.last_completed_input_prefix_fingerprint = durable_full_resend_anchor_fingerprint
                        _log_http_bridge_event(
                            "owner_forward_recovery_anchor_injected",
                            bridge_session_key,
                            account_id=durable_lookup.account_id,
                            model=recovery_payload.model,
                            detail=f"response_id={durable_lookup.latest_response_id}",
                            cache_key_family=bridge_session_key.affinity_kind,
                            model_class=_extract_model_class(recovery_payload.model)
                            if recovery_payload.model
                            else None,
                        )
                recovery_injected_input = _http_bridge_interrupted_tool_outputs_input(
                    session,
                    payload=recovery_payload,
                    request_id=request_id,
                )
                if recovery_injected_input is not None:
                    recovery_payload = recovery_payload.model_copy(update={"input": recovery_injected_input})
                owner_recovery_scope_id = ensure_request_scope_id() if original_request_unanchored else None
                if owner_recovery_scope_id is not None:
                    _reserve_http_bridge_unanchored_handoff(
                        session,
                        request_scope_id=owner_recovery_scope_id,
                    )
                retry_request_state: _WebSocketRequestState | None = None
                retry_unowned_lifecycle: _DeferredAccountBackoffLifecycle | None = None
                try:
                    retry_api_key_reservation = api_key_reservation
                    retry_reservation_reacquired = False
                    if api_key is not None and api_key_reservation is not None:
                        retry_api_key_reservation = await self._reserve_websocket_api_key_usage(
                            api_key,
                            request_model=recovery_payload.model,
                            request_service_tier=_normalize_service_tier_value(
                                dict(recovery_payload.to_payload()).get("service_tier"),
                            ),
                            request_usage_budget=estimate_api_key_request_usage(recovery_payload),
                        )
                        retry_reservation_reacquired = True
                        retry_unowned_lifecycle = begin_bridge_lifecycle(retry_api_key_reservation)

                    retry_request_state, retry_text_data = prepare_bridge_request(
                        recovery_payload,
                        reservation=retry_api_key_reservation,
                    )
                    retry_request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
                    retry_request_state.affinity_policy = affinity
                    _apply_http_bridge_downstream_turn_state(
                        retry_request_state,
                        downstream_turn_state=downstream_turn_state,
                        incoming_turn_state_header=incoming_turn_state_header,
                    )
                    retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                    retry_request_state.request_stage = (
                        request_state.request_stage if owner_forward_fresh_replay else "reattach"
                    )
                    retry_request_state.preferred_account_id = request_state.preferred_account_id
                    retry_request_state.excluded_account_ids.update(request_state.excluded_account_ids)
                    if recovery_anchor_input_count is not None:
                        retry_request_state.input_item_count = recovery_anchor_input_count
                        retry_request_state.input_full_fingerprint = recovery_anchor_input_fingerprint
                        retry_request_state.proxy_injected_previous_response_id = True
                        retry_request_state.denied_proxy_injected_anchor_fence_generation_at_prepare = (
                            recovery_anchor_fence_generation
                            if recovery_anchor_fence_generation is not None
                            else request_state.denied_proxy_injected_anchor_fence_generation_at_prepare
                        )
                        retry_request_state.denied_proxy_injected_anchor_fence_was_already_denied = (
                            recovery_anchor_fence_was_already_denied
                            or request_state.denied_proxy_injected_anchor_fence_was_already_denied
                        )
                        retry_request_state.denied_proxy_injected_anchor_fence_response_id = (
                            recovery_anchor_fence_response_id
                            if recovery_anchor_fence_response_id is not None
                            else request_state.denied_proxy_injected_anchor_fence_response_id
                        )
                        retry_request_state.denied_proxy_injected_anchor_fence_request_id = (
                            request_id
                            if recovery_anchor_fence_response_id is not None
                            else request_state.denied_proxy_injected_anchor_fence_request_id
                        )
                        # ``recovery_anchor_input_count`` is only set when
                        # ``durable_full_resend_anchor_count`` is not None,
                        # which itself requires the incoming payload to have
                        # classified as a full resend (see
                        # ``classify_durable_full_resend`` above).
                        retry_request_state.proxy_injected_anchor_had_full_resend_payload = True
                        retry_request_state.fresh_upstream_request_is_retry_safe = False

                    async for event_block in self._stream_http_bridge_session_events(
                        session,
                        request_state=retry_request_state,
                        text_data=retry_text_data,
                        queue_limit=queue_limit,
                        propagate_http_errors=propagate_http_errors,
                        downstream_turn_state=downstream_turn_state,
                        request_deadline=request_deadline,
                    ):
                        yield event_block
                except BaseException:
                    if retry_reservation_reacquired and retry_api_key_reservation is not None:
                        retry_lifecycle = (
                            retry_request_state.deferred_account_backoff_lifecycle
                            if retry_request_state is not None
                            else retry_unowned_lifecycle
                        )
                        try:
                            await release_unowned_bridge_lifecycle(retry_lifecycle, retry_request_state)
                        except Exception:
                            logger.warning(
                                "Failed to release owner-recovery HTTP bridge reservation",
                                exc_info=True,
                            )
                    raise
                finally:
                    if owner_recovery_scope_id is not None:
                        _release_http_bridge_unanchored_handoff(
                            session,
                            request_scope_id=owner_recovery_scope_id,
                        )
                    if retry_request_state is not None:
                        with anyio.CancelScope(shield=True):
                            await self._detach_http_bridge_request(session, request_state=retry_request_state)
                            session.last_used_at = _service_time().monotonic()
                return
        session = session_or_forward
        if (
            # A quarantine-suppressed anchor (#1534) must not be rehydrated
            # into the session either: doing so would let the session-level
            # injection below re-add the exact anchor the quarantine skipped
            # and trim the prefix, rebuilding the wedged reattach.
            not fresh_reattach_anchor_suppressed_quarantined
            and not durable_full_resend_has_safe_fresh_context
            and durable_full_resend_anchor_count is not None
            and durable_full_resend_anchor_fingerprint is not None
            and durable_lookup is not None
            and durable_lookup.latest_response_id is not None
        ):
            if durable_lookup.latest_response_id != session.last_completed_response_id:
                # The pending tool calls were recorded for the session's own
                # last completed response; a durable anchor pointing elsewhere
                # must not trigger interrupted-output injection.
                session.last_pending_tool_calls = {}
            session.last_completed_response_id = durable_lookup.latest_response_id
            # The durable anchor is owned by the durable session's account, which
            # may differ from this session's account after a failover. Record the
            # owner so the session-anchor injection below can refuse to replay a
            # cross-account previous_response_id (upstream cannot resolve it and
            # would stall with no response.created — a wedged response-create gate).
            session.last_completed_response_account_id = durable_lookup.account_id
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
        # A previous_response_id is account-scoped upstream: only the account that
        # created the response can resume it. If this session's serving account is
        # not the anchor's owner (e.g. the session failed over after the durable
        # owner became unavailable), injecting the anchor sends an unresolvable
        # previous_response_id upstream with the history trimmed away — upstream
        # then never emits response.created and the response-create gate wedges
        # ("idle timeout waiting for SSE"). Fall through to a full-history resend.
        session_anchor_account_owned = (
            session.last_completed_response_account_id is not None
            and session.last_completed_response_account_id == session.account.id
        )
        recovery_session_can_anchor = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        ) and (not _http_bridge_payload_looks_like_full_resend(effective_payload) or session_anchor_trimmable)
        session_anchor_candidate = (
            session.codex_session
            # Honor the quarantine decision end to end: the durable anchor
            # skipped above must not come back as a session-level injection.
            and not fresh_reattach_anchor_suppressed_quarantined
            and not proxy_injected_previous_response_id
            and effective_payload.previous_response_id is None
            and session.last_completed_response_id is not None
            and (session_anchor_trimmable or recovery_session_can_anchor)
        )
        if session_anchor_candidate and not session_anchor_account_owned:
            _log_http_bridge_event(
                "cross_account_anchor_declined",
                session.key,
                account_id=session.account.id,
                model=effective_payload.model,
                detail=(
                    "site=session_anchor, "
                    f"response_id={session.last_completed_response_id}, "
                    f"anchor_account_id={session.last_completed_response_account_id}, "
                    "outcome=full_history_resend"
                ),
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                owner_check_applied=True,
            )
        if session_anchor_candidate and session_anchor_account_owned:
            fresh_upstream_request_text = text_data
            session_level_payload_looks_like_full_resend = _http_bridge_payload_looks_like_full_resend(
                effective_payload
            )
            effective_payload = effective_payload.model_copy(
                update={"previous_response_id": session.last_completed_response_id}
            )
            proxy_injected_previous_response_id = True
            request_state, text_data = prepare_bridge_request(effective_payload)
            request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
            request_state.affinity_policy = affinity
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=effective_payload,
                durable_lookup=durable_lookup,
            )
            request_state.preferred_account_id = durable_lookup.account_id if durable_lookup is not None else None
            request_state.excluded_account_ids.update(fresh_replay_excluded_account_ids)
            request_state.proxy_injected_previous_response_id = True
            request_state.proxy_injected_anchor_had_full_resend_payload = session_level_payload_looks_like_full_resend
            request_state.fresh_upstream_request_text = fresh_upstream_request_text
            if request_state.previous_response_id is not None:
                _bind_http_bridge_proxy_injected_anchor(
                    self,
                    request_state,
                    response_id=request_state.previous_response_id,
                    fence_request_id=request_id,
                )
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
        submit_payload = effective_payload
        store_context_trim_applied = False
        store_context_original_count = 0
        store_context_original_fingerprint: str | None = None
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
                store_context_trim_applied = True
                store_context_original_count = len(incoming_input_list)
                store_context_original_fingerprint = _fingerprint_input_items(incoming_input_list)
                submit_payload = effective_payload.model_copy(update={"input": incoming_input_list[stored_count:]})
                logger.info(
                    "store_context_input_trimmed request_id=%s original_items=%s trimmed_to=%s previous_response_id=%s",
                    request_id,
                    store_context_original_count,
                    store_context_original_count - stored_count,
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
        injected_input_items = _http_bridge_interrupted_tool_outputs_input(
            session,
            payload=submit_payload,
            request_id=request_id,
        )
        if injected_input_items is not None:
            submit_payload = submit_payload.model_copy(update={"input": injected_input_items})
        if store_context_trim_applied or injected_input_items is not None:
            # Re-prepare from the final upstream-shaped payload (post-trim,
            # post-injection) so the serialized request text, the slim/size
            # guard, the stored input context, and the usage budget all
            # observe the input actually sent upstream.
            previous_request_state = request_state
            request_state, text_data = prepare_bridge_request(submit_payload)
            request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
            request_state.affinity_policy = affinity
            _apply_http_bridge_downstream_turn_state(
                request_state,
                downstream_turn_state=downstream_turn_state,
                incoming_turn_state_header=incoming_turn_state_header,
            )
            request_state.transport = _REQUEST_TRANSPORT_HTTP
            request_state.request_stage = _http_bridge_request_stage(
                headers=headers,
                payload=submit_payload,
                durable_lookup=durable_lookup,
            )
            request_state.preferred_account_id = previous_request_state.preferred_account_id
            request_state.excluded_account_ids.update(previous_request_state.excluded_account_ids)
            if store_context_trim_applied:
                # Store the full incoming client input as the session context
                # so the client's next full resend can prefix-match it.
                request_state.input_item_count = store_context_original_count
                request_state.input_full_fingerprint = store_context_original_fingerprint
            elif previous_response_trimmed_input_count is not None:
                request_state.input_item_count = previous_response_trimmed_input_count
                request_state.input_full_fingerprint = previous_response_trimmed_input_fingerprint
            if proxy_injected_previous_response_id:
                request_state.proxy_injected_previous_response_id = True
                request_state.denied_proxy_injected_anchor_fence_response_id = (
                    previous_request_state.denied_proxy_injected_anchor_fence_response_id
                )
                request_state.denied_proxy_injected_anchor_fence_request_id = (
                    previous_request_state.denied_proxy_injected_anchor_fence_request_id
                )
                request_state.denied_proxy_injected_anchor_fence_generation_at_prepare = (
                    previous_request_state.denied_proxy_injected_anchor_fence_generation_at_prepare
                )
                request_state.denied_proxy_injected_anchor_fence_was_already_denied = (
                    previous_request_state.denied_proxy_injected_anchor_fence_was_already_denied
                )
                # Unlike ``fresh_upstream_request_is_retry_safe`` below, this
                # flag only asks whether the client's payload looked like a
                # full resend, which cannot change between the original
                # injection and this re-prepare: ``store_context_trim_applied``
                # requires ``len(incoming_input) > stored_count > 0``, which
                # already implies a full-resend-shaped payload, so a plain
                # carry-forward is sufficient.
                request_state.proxy_injected_anchor_had_full_resend_payload = (
                    previous_request_state.proxy_injected_anchor_had_full_resend_payload
                )
                request_state.fresh_upstream_request_text = fresh_upstream_request_text
                # The trim branch only fires when the untrimmed payload
                # is a true full resend whose prefix exactly matches the
                # already-stored context, so the unanchored request text
                # is a safe fresh-turn replay target regardless of
                # whether the anchor came from the durable or
                # session-level injection path. Injection-only re-prepares
                # keep the replay-safety decision made when the anchor was
                # injected.
                request_state.fresh_upstream_request_is_retry_safe = (
                    (durable_full_resend_anchor_count is None or durable_full_resend_has_safe_fresh_context)
                    if store_context_trim_applied
                    else previous_request_state.fresh_upstream_request_is_retry_safe
                )
            elif client_full_resend_fresh_upstream_request_text is not None:
                request_state.fresh_upstream_request_text = client_full_resend_fresh_upstream_request_text
                request_state.fresh_upstream_request_is_retry_safe = True
        initial_handoff_session = session
        initial_handoff_scope_id = ensure_request_scope_id() if original_request_unanchored else None
        if initial_handoff_scope_id is not None:
            _reserve_http_bridge_unanchored_handoff(
                initial_handoff_session,
                request_scope_id=initial_handoff_scope_id,
            )
        session_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=queue_limit,
            propagate_http_errors=propagate_http_errors,
            downstream_turn_state=downstream_turn_state,
            request_deadline=request_deadline,
        )
        request_state.file_required_preferred_account = file_required_preferred_account
        request_state.bridge_soft_capacity_reroute_allowed = (
            bridge_session_key.strength == "soft"
            and request_state.previous_response_id is None
            and not file_required_preferred_account
        )
        try:
            yielded_any = False

            async def reset_previous_response_recovery_operation_spool(
                recovery_session: "_HTTPBridgeSession",
                recovery_request_state: _WebSocketRequestState,
                *,
                required: bool = True,
            ) -> None:
                if not _http_bridge_verified_stale_anchor_replay_is_operation_fenced(
                    recovery_session,
                    recovery_request_state,
                ):
                    if not required:
                        return
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "HTTP response recovery operation fence is unavailable; retry the request.",
                        ),
                    )
                reset_operation_event_spool = getattr(self._durable_bridge, "reset_operation_event_spool", None)
                if not callable(reset_operation_event_spool):
                    if not required:
                        return
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "HTTP response recovery spool reset is unavailable; retry the request.",
                        ),
                    )
                assert recovery_request_state.operation_id is not None
                assert recovery_session.durable_session_id is not None
                assert recovery_session.durable_owner_epoch is not None
                reset_ok = await reset_operation_event_spool(
                    operation_id=recovery_request_state.operation_id,
                    session_id=recovery_session.durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=recovery_session.durable_owner_epoch,
                )
                if not reset_ok:
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "HTTP response recovery spool could not be reset; retry the request.",
                        ),
                    )

            async def capture_verified_stale_anchor_circuit_generation(
                recovery_session: "_HTTPBridgeSession",
            ) -> None:
                nonlocal verified_stale_anchor_generation
                nonlocal verified_stale_anchor_generation_captured
                nonlocal verified_stale_anchor_circuit_key

                load_succeeded, generation = await self._http_bridge_retry_circuit_generation(recovery_session)
                if not load_succeeded:
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "HTTP response recovery circuit generation is unavailable; retry the request.",
                        ),
                    )
                verified_stale_anchor_generation = generation
                verified_stale_anchor_generation_captured = True
                verified_stale_anchor_circuit_key = recovery_session.key

            def capture_verified_stale_anchor_quarantine_generation(
                recovery_session: "_HTTPBridgeSession",
            ) -> None:
                nonlocal verified_stale_anchor_quarantine_generation

                verified_stale_anchor_quarantine_generation = _http_bridge_quarantine_generation(
                    self,
                    recovery_session.key,
                )

            async for event_block in session_events:
                yield event_block
                yielded_any = True
        except ProxyResponseError as exc:
            if (
                request_state.operation_registered
                and request_state.operation_id is not None
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
                and _http_bridge_durable_recovery_predecessor_proven(request_state)
            ):
                # The API-level recovery loop must only run when this request
                # has an actual durable operation fence. Settings alone are
                # insufficient during a rolling migration where the durable
                # tables may be unavailable and the bridge falls back to an
                # in-memory session.
                setattr(exc, "http_bridge_durable_recovery_eligible", True)
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
            async with session.pending_lock:
                request_was_enqueued = request_state in session.pending_requests
            if _http_bridge_can_replace_retired_gate_session(
                exc,
                session=session,
                request_state=request_state,
                request_was_enqueued=request_was_enqueued,
            ):
                _log_http_bridge_event(
                    "replace_retired_gate",
                    session.key,
                    account_id=session.account.id,
                    model=effective_payload.model,
                    detail="reason=response_create_gate_timeout_stuck_pending",
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                replacement_preferred_account_id = request_state.preferred_account_id
                if request_state.previous_response_id is not None and replacement_preferred_account_id is None:
                    replacement_preferred_account_id = session.account.id
                elif replacement_preferred_account_id is None:
                    # The retired owner already proved stuck; a "replacement"
                    # that could legally reselect that same account isn't a
                    # replacement at all. An already-pinned waiter (continuity
                    # owner resolved above, or a file-pinned account) must
                    # stay pinned instead — excluding its required account
                    # here would make that account's own replacement
                    # impossible (fallback_on_preferred_account_unavailable is
                    # False for exactly this pinned case below) and would
                    # keep poisoning every later recovery call on this
                    # request, since excluded_account_ids persists on
                    # request_state.
                    request_state.excluded_account_ids.add(session.account.id)
                while True:
                    try:
                        replacement_session = await self._get_or_create_http_bridge_session(
                            bridge_session_key,
                            headers=dict(session_creation_headers),
                            affinity=affinity,
                            api_key=api_key,
                            request_model=effective_payload.model,
                            request_service_tier=request_state.requested_service_tier,
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
                            forwarded_request=forwarded_request,
                            forwarded_original_request_unanchored=original_request_unanchored,
                            durable_lookup=durable_lookup,
                            request_stage=request_state.request_stage,
                            preferred_account_id=replacement_preferred_account_id,
                            preferred_account_has_continuity_provenance=preferred_account_has_continuity_provenance,
                            fallback_on_preferred_account_unavailable=not (
                                file_required_preferred_account or request_state.previous_response_id is not None
                            ),
                            allow_previous_response_recovery_rebind=request_state.previous_response_id is not None,
                            request_usage_budget=request_state.request_usage_budget,
                            request_deadline=request_deadline,
                            session_header_fallback_key=session_header_fallback_key,
                            exclude_account_ids=request_state.excluded_account_ids or None,
                            deferred_account_backoff_lifecycle=request_state.deferred_account_backoff_lifecycle,
                            defer_account_health_writes=request_state.api_key_reservation is not None,
                        )
                    except ProxyResponseError as capacity_exc:
                        wait_plan = _http_bridge_capacity_wait_plan(capacity_exc, request_deadline=request_deadline)
                        if wait_plan is None:
                            raise
                        bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                        logger.info(
                            "Waiting for an account to recover before replacing retired HTTP bridge gate "
                            "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
                            request_id,
                            effective_payload.model,
                            bounded_wait_seconds,
                            account_capacity_wait_seconds,
                            message,
                        )
                        async for line in _iter_account_capacity_wait_sse(
                            request_id=request_id,
                            reason=message,
                            sleep_seconds=bounded_wait_seconds,
                            emit_keepalives=not propagate_http_errors,
                            request_state=request_state,
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
                    break
                if initial_handoff_scope_id is not None:
                    _release_http_bridge_unanchored_handoff(
                        initial_handoff_session,
                        request_scope_id=initial_handoff_scope_id,
                    )
                    _reserve_http_bridge_unanchored_handoff(
                        replacement_session,
                        request_scope_id=initial_handoff_scope_id,
                    )
                    initial_handoff_session = replacement_session
                replacement_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    replacement_session,
                    request_state=request_state,
                    text_data=text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                    request_deadline=request_deadline,
                )
                try:
                    async for event_block in replacement_events:
                        yield event_block
                finally:
                    try:
                        await replacement_events.aclose()
                    except Exception:
                        pass
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
                while True:
                    try:
                        reroute_session = await self._get_or_create_http_bridge_session(
                            reroute_key,
                            headers=dict(session_creation_headers),
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
                            request_deadline=request_deadline,
                            exclude_account_ids=request_state.excluded_account_ids or None,
                            deferred_account_backoff_lifecycle=request_state.deferred_account_backoff_lifecycle,
                            defer_account_health_writes=request_state.api_key_reservation is not None,
                        )
                    except ProxyResponseError as capacity_exc:
                        wait_plan = _http_bridge_capacity_wait_plan(capacity_exc, request_deadline=request_deadline)
                        if wait_plan is None:
                            raise
                        bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                        logger.info(
                            "Waiting for an account to recover before retrying HTTP bridge soft reroute session "
                            "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
                            request_id,
                            effective_payload.model,
                            bounded_wait_seconds,
                            account_capacity_wait_seconds,
                            message,
                        )
                        async for line in _iter_account_capacity_wait_sse(
                            request_id=request_id,
                            reason=message,
                            sleep_seconds=bounded_wait_seconds,
                            emit_keepalives=not propagate_http_errors,
                            request_state=request_state,
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
                    break
                retry_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    reroute_session,
                    request_state=request_state,
                    text_data=text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                    request_deadline=request_deadline,
                )
                request_state.bridge_soft_capacity_reroute_allowed = False
                try:
                    async for event_block in retry_events:
                        yield event_block
                finally:
                    try:
                        await retry_events.aclose()
                    except Exception:
                        pass
                return
            explicit_previous_response_rejection = (
                effective_payload.previous_response_id is not None
                and _http_bridge_is_explicit_previous_response_rejection(exc)
            )
            if explicit_previous_response_rejection and (
                request_state.replay_count > 0
                or request_state.response_event_count > 0
                or request_state.downstream_visible
            ):
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "A prior HTTP response replay has an ambiguous outcome; retry later.",
                    ),
                ) from exc
            verified_stale_anchor_operation_fenced = _http_bridge_verified_stale_anchor_replay_is_operation_fenced(
                session, request_state
            )
            is_context_overflow = _http_bridge_is_context_overflow_error(exc)
            should_rollover_after_context_overflow = _http_bridge_should_rollover_after_context_overflow(
                exc,
                key=bridge_session_key,
            )
            should_attempt_previous_response_recovery = (
                effective_payload.previous_response_id is not None
                and _http_bridge_should_attempt_local_previous_response_recovery(exc)
            )
            previous_response_rejected_same_owner_full_resend = bool(
                explicit_previous_response_rejection
                and verified_stale_anchor_operation_fenced
                and request_state.response_event_count == 0
                and request_state.replay_count == 0
                and durable_full_resend_retains_required_context()
            )
            previous_response_rejected_full_resend = (
                explicit_previous_response_rejection
                and verified_stale_anchor_operation_fenced
                and request_state.response_event_count == 0
                and request_state.replay_count == 0
                and durable_full_resend_allows_account_neutral_replay()
            )
            previous_response_rejected_full_resend_without_operation_fence = bool(
                explicit_previous_response_rejection
                and not verified_stale_anchor_operation_fenced
                and request_state.response_event_count == 0
                and request_state.replay_count == 0
                and durable_full_resend_retains_required_context()
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

            if previous_response_rejected_full_resend_without_operation_fence:
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "HTTP response recovery operation fence is unavailable; retry the request.",
                    ),
                ) from exc
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
                    error_message=_HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
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
                    error_message=_HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
                )
                raise
            elif previous_response_rejected_full_resend:
                await capture_verified_stale_anchor_circuit_generation(session)
                capture_verified_stale_anchor_quarantine_generation(session)
                await reset_previous_response_recovery_operation_spool(session, request_state)
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message=_HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
                )
                switch_to_account_neutral_replay(
                    event="previous_response_recover_fresh_resend",
                    detail="outcome=stale_anchor_rejected_account_neutral_replay",
                    preserve_operation=True,
                )
                recovery_path = "local_previous_response_fresh_replay"
                retry_payload = effective_payload
                retry_previous_response_id = None
                retry_request_stage = "stale_anchor_recover"
                retry_preferred_account_id = None
                allow_previous_response_recovery_rebind = False
            elif previous_response_rejected_same_owner_full_resend:
                # The owner-pinned replacement does not bypass the source
                # circuit: its unique internal key avoids that admission path.
                # Retain the origin key only for independent quarantine cleanup.
                verified_stale_anchor_circuit_key = session.key
                capture_verified_stale_anchor_quarantine_generation(session)
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="local_previous_response_same_owner_fresh_replay").inc()
                _log_http_bridge_event(
                    "previous_response_recover_same_owner_fresh_resend",
                    bridge_session_key,
                    account_id=session.account.id,
                    model=effective_payload.model,
                    detail="outcome=stale_anchor_rejected_same_owner_unanchored_replay",
                    cache_key_family=bridge_session_key.affinity_kind,
                    model_class=_extract_model_class(effective_payload.model) if effective_payload.model else None,
                    owner_check_applied=True,
                )
                retry_payload = _http_bridge_payload_without_previous_response_id(untrimmed_effective_payload)
                retry_injected_input = _http_bridge_interrupted_tool_outputs_input(
                    session,
                    payload=retry_payload,
                    request_id=request_id,
                )
                if retry_injected_input is not None:
                    retry_payload = retry_payload.model_copy(update={"input": retry_injected_input})
                retry_preferred_account_id = request_state.preferred_account_id or session.account.id
                bridge_session_key = _HTTPBridgeSessionKey(
                    "internal_request_parallel",
                    f"stale-owner-replay:{uuid4().hex}",
                    bridge_session_key.api_key_id,
                )
                await reset_previous_response_recovery_operation_spool(session, request_state)
                await self._reset_http_bridge_session_after_local_terminal_error(
                    session,
                    error_code="stream_incomplete",
                    error_message=_HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
                )
                recovery_path = "local_previous_response_same_owner_fresh_replay"
                retry_previous_response_id = None
                retry_request_stage = "stale_anchor_recover"
                allow_previous_response_recovery_rebind = False
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
                    error_message=_HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
                )
                recovery_path = "local_previous_response_error"
                retry_payload = effective_payload
                # The failed session object still carries the pending
                # tool-call state recorded for its last completed response,
                # so re-run synthetic interrupted-output injection for the
                # anchored recovery payload; ``effective_payload`` alone
                # would drop the outputs injected into ``submit_payload``
                # and reintroduce the upstream missing-tool-output failure.
                retry_injected_input = _http_bridge_interrupted_tool_outputs_input(
                    session,
                    payload=retry_payload,
                    request_id=request_id,
                )
                if retry_injected_input is not None:
                    retry_payload = retry_payload.model_copy(update={"input": retry_injected_input})
                retry_previous_response_id = request_state.previous_response_id
                retry_request_stage = "reattach"
                retry_preferred_account_id = request_state.preferred_account_id
                allow_previous_response_recovery_rebind = True

            try:
                while True:
                    try:
                        session = await self._get_or_create_http_bridge_session(
                            bridge_session_key,
                            headers=dict(session_creation_headers),
                            affinity=affinity,
                            api_key=api_key,
                            request_model=retry_payload.model,
                            request_service_tier=request_state.requested_service_tier,
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
                            session_header_fallback_key=session_header_fallback_key,
                            durable_lookup=durable_lookup,
                            request_stage=retry_request_stage,
                            preferred_account_id=retry_preferred_account_id,
                            preferred_account_has_continuity_provenance=preferred_account_has_continuity_provenance,
                            fallback_on_preferred_account_unavailable=not (
                                file_required_preferred_account
                                or (
                                    recovery_path == "local_previous_response_same_owner_fresh_replay"
                                    and retry_preferred_account_id is not None
                                )
                            ),
                            request_usage_budget=estimate_api_key_request_usage(retry_payload),
                            request_deadline=request_deadline,
                            exclude_account_ids=request_state.excluded_account_ids or None,
                            deferred_account_backoff_lifecycle=request_state.deferred_account_backoff_lifecycle,
                            defer_account_health_writes=request_state.api_key_reservation is not None,
                        )
                    except ProxyResponseError as capacity_exc:
                        wait_plan = _http_bridge_capacity_wait_plan(capacity_exc, request_deadline=request_deadline)
                        if wait_plan is None:
                            raise
                        bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                        logger.info(
                            "Waiting for an account to recover before retrying HTTP bridge local recovery session "
                            "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f path=%s error=%s",
                            request_id,
                            retry_payload.model,
                            bounded_wait_seconds,
                            account_capacity_wait_seconds,
                            recovery_path,
                            message,
                        )
                        async for line in _iter_account_capacity_wait_sse(
                            request_id=request_id,
                            reason=message,
                            sleep_seconds=bounded_wait_seconds,
                            emit_keepalives=not propagate_http_errors,
                            request_state=request_state,
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
                    break
            except BaseException:
                raise
            _record_bridge_reattach(path=recovery_path, outcome="success")

            local_recovery_scope_id = ensure_request_scope_id() if original_request_unanchored else None
            if local_recovery_scope_id is not None:
                _reserve_http_bridge_unanchored_handoff(
                    session,
                    request_scope_id=local_recovery_scope_id,
                )
            retry_request_state: _WebSocketRequestState | None = None
            retry_unowned_lifecycle: _DeferredAccountBackoffLifecycle | None = None
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
                    retry_unowned_lifecycle = begin_bridge_lifecycle(retry_api_key_reservation)

                retry_request_state, retry_text_data = prepare_bridge_request(
                    retry_payload,
                    reservation=retry_api_key_reservation,
                )
                local_previous_response_recovery = recovery_path in {
                    "local_previous_response_error",
                    "local_previous_response_fresh_replay",
                    "local_previous_response_same_owner_fresh_replay",
                }
                if recovery_path == "local_previous_response_error":
                    await reset_previous_response_recovery_operation_spool(
                        session,
                        request_state,
                        required=False,
                    )
                # A recovery request is the one bounded server-side replay;
                # prevent a second cooldown bypass if this fresh socket also
                # fails before response.created.
                if local_previous_response_recovery:
                    retry_request_state.replay_count = max(1, request_state.replay_count + 1)
                retry_request_state.verified_stale_anchor_replay = recovery_path in {
                    "local_previous_response_fresh_replay",
                    "local_previous_response_same_owner_fresh_replay",
                }
                if retry_request_state.verified_stale_anchor_replay:
                    retry_request_state.verified_stale_anchor_retry_circuit_generation_captured = (
                        verified_stale_anchor_generation_captured
                    )
                    retry_request_state.verified_stale_anchor_retry_circuit_key = verified_stale_anchor_circuit_key
                    retry_request_state.verified_stale_anchor_retry_circuit_generation = (
                        verified_stale_anchor_generation
                    )
                    retry_request_state.verified_stale_anchor_quarantine_generation = (
                        verified_stale_anchor_quarantine_generation
                    )
                # Keep the durable operation identity attached to the
                # server-owned recovery attempt. Re-registering the same
                # fingerprint would be interpreted as an already-dispatched
                # unknown operation and suppress the intended one-shot replay.
                retry_request_state.operation_id = request_state.operation_id
                retry_request_state.operation_fingerprint = request_state.operation_fingerprint
                retry_request_state.operation_parent_response_id = request_state.operation_parent_response_id
                retry_request_state.operation_registered = request_state.operation_registered
                retry_request_state.operation_attempt_generation = request_state.operation_attempt_generation
                retry_request_state.operation_persisted_response_id = request_state.operation_persisted_response_id
                retry_request_state.operation_rebind_required = request_state.operation_rebind_required
                # An anchored recovery replays the proxy's own anchor, so the
                # retry inherits its provenance. Without this the retry looks
                # client-anchored: diagnostics report
                # ``previous_response_source=client_supplied`` for an id no
                # client sent, ``_http_bridge_request_state_wedged_reattach``
                # cannot recognise the reattach, and an upstream denial of the
                # anchor is not attributable to this proxy. The anchor-free
                # recovery paths carry no anchor at all, so the flag stays
                # false there and cannot describe an id the retry never sends.
                retry_request_state.proxy_injected_previous_response_id = (
                    request_state.proxy_injected_previous_response_id and retry_previous_response_id is not None
                )
                retry_request_state.denied_proxy_injected_anchor_fence_generation_at_prepare = (
                    request_state.denied_proxy_injected_anchor_fence_generation_at_prepare
                    if retry_request_state.proxy_injected_previous_response_id
                    else None
                )
                retry_request_state.denied_proxy_injected_anchor_fence_was_already_denied = (
                    request_state.denied_proxy_injected_anchor_fence_was_already_denied
                    if retry_request_state.proxy_injected_previous_response_id
                    else False
                )
                retry_request_state.denied_proxy_injected_anchor_fence_response_id = (
                    request_state.denied_proxy_injected_anchor_fence_response_id
                    if retry_request_state.proxy_injected_previous_response_id
                    else None
                )
                retry_request_state.denied_proxy_injected_anchor_fence_request_id = (
                    request_state.denied_proxy_injected_anchor_fence_request_id
                    if retry_request_state.proxy_injected_previous_response_id
                    else None
                )
                # Carried with the flag above, not separately: the anchor's
                # retirability depends on whether the payload it was injected
                # onto was full-resend shaped, and a retry that replays the
                # anchor replays that shape too.
                retry_request_state.proxy_injected_anchor_had_full_resend_payload = (
                    request_state.proxy_injected_anchor_had_full_resend_payload
                    and retry_request_state.proxy_injected_previous_response_id
                )
                if local_previous_response_recovery:
                    # The prior response.failed/error made the operation
                    # terminal. Re-enter record_operation so its owner fence
                    # atomically moves it back to submitted before send.
                    retry_request_state.operation_rebind_required = True
                retry_request_state.enforce_openai_sdk_contract = enforce_openai_sdk_contract
                _apply_http_bridge_downstream_turn_state(
                    retry_request_state,
                    downstream_turn_state=downstream_turn_state,
                    incoming_turn_state_header=incoming_turn_state_header,
                )
                retry_request_state.transport = _REQUEST_TRANSPORT_HTTP
                retry_request_state.request_stage = retry_request_stage
                retry_request_state.preferred_account_id = retry_preferred_account_id
                retry_request_state.excluded_account_ids.update(request_state.excluded_account_ids)

                retry_events: AsyncGenerator[str, None] = self._stream_http_bridge_session_events(
                    session,
                    request_state=retry_request_state,
                    text_data=retry_text_data,
                    queue_limit=queue_limit,
                    propagate_http_errors=propagate_http_errors,
                    downstream_turn_state=downstream_turn_state,
                    request_deadline=request_deadline,
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
                    retry_lifecycle = (
                        retry_request_state.deferred_account_backoff_lifecycle
                        if retry_request_state is not None
                        else retry_unowned_lifecycle
                    )
                    try:
                        await release_unowned_bridge_lifecycle(retry_lifecycle, retry_request_state)
                    except Exception:
                        logger.warning(
                            "Failed to release local-recovery HTTP bridge reservation",
                            exc_info=True,
                        )
                raise
            finally:
                if local_recovery_scope_id is not None:
                    _release_http_bridge_unanchored_handoff(
                        session,
                        request_scope_id=local_recovery_scope_id,
                    )
        finally:
            if initial_handoff_scope_id is not None:
                _release_http_bridge_unanchored_handoff(
                    initial_handoff_session,
                    request_scope_id=initial_handoff_scope_id,
                )
            try:
                await session_events.aclose()
            except Exception:
                pass

    async def _stream_via_http_bridge(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Run one bridge request and release its process-level anchor pins."""
        request_id = ensure_request_id()
        inner_stream = self._stream_via_http_bridge_impl(
            *args,
            _denied_anchor_request_id=request_id,
            **kwargs,
        )
        try:
            async for event_block in inner_stream:
                yield event_block
        finally:
            try:
                await inner_stream.aclose()
            finally:
                _release_http_bridge_denied_anchor_fences(self, request_id)

    async def _reset_http_bridge_session_after_local_terminal_error(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        error_code: str,
        error_message: str,
        preserve_durable_lease: bool = False,
    ) -> None:
        async with self._http_bridge_lock:
            # Pending settlement below may block or fail before resource close
            # starts. Transfer canonical routing into detached lifecycle
            # ownership first so capacity, invalidation, and shutdown continue
            # to see the live socket and leases throughout that interval.
            self._detach_http_bridge_session_locked(session.key, expected_session=session)
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
            penalize_account=False,
        )
        await self._close_http_bridge_session(session, release_durable_session=not preserve_durable_lease)

    async def _stream_http_bridge_session_events(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        propagate_http_errors: bool,
        downstream_turn_state: str | None,
        request_deadline: float | None = None,
    ) -> AsyncGenerator[str, None]:
        if request_deadline is None:
            request_deadline = request_state.started_at + _http_bridge_request_budget_seconds(_service_get_settings())
        request_state.bridge_request_deadline = request_deadline
        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
        request_state.propagate_http_errors = propagate_http_errors

        async def retry_precreated_for_idle_recovery(
            *,
            downstream_response_id: str,
            after_circuit_cooldown: bool = False,
        ) -> tuple[bool, str | None]:
            # The reader may already be performing the bounded additional
            # clean-close replay (including its jitter). Wait for that result
            # instead of interpreting the in-progress flag as a terminal idle
            # failure and detaching the request underneath the replay.
            if request_state.clean_close_retry_in_progress:
                while request_state.clean_close_retry_in_progress:
                    if _service_time().monotonic() >= request_deadline:
                        return (
                            False,
                            format_sse_event(
                                cast(
                                    Mapping[str, JsonValue],
                                    response_failed_event(
                                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                                        "HTTP responses session bridge clean-close recovery "
                                        "exceeded the request budget",
                                        response_id=downstream_response_id,
                                    ),
                                )
                            ),
                        )
                    await asyncio.sleep(0.01)
                if request_state.clean_close_retry_result is True:
                    return True, None
            try:
                return (
                    await self._retry_http_bridge_precreated_request(
                        session,
                        restart_reader=True,
                    ),
                    None,
                )
            except UpstreamWebSocketTransportError as exc:
                if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                    stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
                logger.info(
                    "HTTP bridge eventless recovery retry%s failed with transport error request_id=%s error_code=%s",
                    " after circuit cooldown" if after_circuit_cooldown else "",
                    request_state.request_id,
                    exc.error_code,
                )
                mark_bridge_eventless_failure()
                await record_bridge_eventless_timeout_failure()
                eventless_message = _http_bridge_eventless_timeout_message(session.unmatched_upstream_liveness_count)
                if getattr(
                    _service_get_settings(),
                    "http_responses_session_bridge_ambiguous_continuation_recovery_mode",
                    "fail_closed",
                ) == "server_indefinite_recovery" and _http_bridge_server_anchored_replay_enabled(request_state):
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                            eventless_message,
                            error_type="server_error",
                        ),
                    ) from exc
                return (
                    False,
                    format_sse_event(
                        cast(
                            Mapping[str, JsonValue],
                            response_failed_event(
                                _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                                eventless_message,
                                response_id=downstream_response_id,
                            ),
                        )
                    ),
                )

        def operation_fenced_cooldown_wait_enabled() -> bool:
            """Allow a hard turn to wait until its durable fence can arbitrate recovery."""
            return (
                getattr(
                    _service_get_settings(),
                    "http_responses_session_bridge_ambiguous_continuation_recovery_mode",
                    "fail_closed",
                )
                in {"server_anchored_replay_once", "server_indefinite_recovery"}
                and getattr(_service_get_settings(), "http_responses_session_bridge_operation_ledger_enabled", True)
                and request_state.hard_continuity_anchor
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
                and request_state.previous_response_id is None
                and request_state.response_id is None
                and request_state.response_event_count == 0
            )

        def continuity_bound_without_safe_replay() -> bool:
            """Do not hold a client stream through a cooldown we cannot use."""
            return _http_bridge_continuity_bound_without_safe_replay(request_state) and not (
                _http_bridge_server_anchored_replay_enabled(request_state) or operation_fenced_cooldown_wait_enabled()
            )

        async def wait_through_operation_fenced_startup_cooldown() -> bool:
            if session.key.strength != "hard" or not operation_fenced_cooldown_wait_enabled():
                return False
            retry_cooldown_seconds = await self._http_bridge_precreated_retry_cooldown_seconds(session)
            if retry_cooldown_seconds <= 0:
                return False
            remaining_budget_seconds = request_deadline - _service_time().monotonic()
            if remaining_budget_seconds <= 0:
                return False
            wait_seconds = min(retry_cooldown_seconds, remaining_budget_seconds)
            async with session.pending_lock:
                if session.queued_request_count >= queue_limit:
                    raise ProxyResponseError(
                        429,
                        openai_error(
                            "bridge_queue_full",
                            "HTTP responses session bridge queue is full",
                            error_type="rate_limit_error",
                        ),
                    )
                session.queued_request_count += 1
            _log_http_bridge_event(
                "wait_operation_fenced_cooldown",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail="hard_turn_operation_fence",
                cache_key_family=session.key.affinity_kind,
            )
            logger.info(
                "HTTP bridge waiting through retry-circuit cooldown before durable hard-turn arbitration "
                "request_id=%s wait_seconds=%.1f remaining_budget_seconds=%.1f",
                request_state.request_id,
                wait_seconds,
                remaining_budget_seconds,
            )
            # No upstream request has been dispatched on this path.  After the
            # cooldown, normal submission still has to create or claim the
            # durable operation fence before response.create can be sent.
            try:
                current_instance = _service_get_settings().http_responses_session_bridge_instance_id
                lease_refresh_interval_seconds = max(
                    1.0,
                    min(
                        _http_bridge_durable_lease_ttl_seconds() / 3.0,
                        wait_seconds,
                    ),
                )
                remaining_wait_seconds = wait_seconds
                while remaining_wait_seconds > 0:
                    sleep_seconds = min(remaining_wait_seconds, lease_refresh_interval_seconds)
                    await asyncio.sleep(sleep_seconds)
                    remaining_wait_seconds = max(0.0, remaining_wait_seconds - sleep_seconds)
                    if remaining_wait_seconds <= 0:
                        break
                    try:
                        owner_lookup = await self._durable_bridge.renew_live_session(
                            session_id=session.durable_session_id,
                            api_key_id=session.key.api_key_id,
                            instance_id=current_instance,
                            owner_epoch=session.durable_owner_epoch,
                            lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                            latest_turn_state=session.downstream_turn_state,
                            latest_response_id=None,
                        )
                    except Exception as exc:
                        session.closed = True
                        session.upstream_control.reconnect_requested = True
                        session.upstream_control.retire_after_drain = True
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "HTTP responses session ownership could not be renewed; retry the request.",
                            ),
                        ) from exc
                    if (
                        owner_lookup is None
                        or owner_lookup.owner_instance_id != current_instance
                        or owner_lookup.owner_epoch != session.durable_owner_epoch
                    ):
                        session.closed = True
                        session.upstream_control.reconnect_requested = True
                        session.upstream_control.retire_after_drain = True
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "HTTP responses session ownership changed during cooldown; retry the request.",
                            ),
                        )
            finally:
                async with session.pending_lock:
                    session.queued_request_count = max(0, session.queued_request_count - 1)
            return True

        async def operation_fenced_request_budget_terminal_event() -> str | None:
            if not operation_fenced_cooldown_wait_enabled() or _service_time().monotonic() < request_deadline:
                return None
            await self._release_websocket_request_state_reservation(request_state)
            request_state.api_key_reservation = None
            mark_bridge_eventless_failure()
            eventless_message = _http_bridge_eventless_timeout_message(session.unmatched_upstream_liveness_count)
            if propagate_http_errors:
                raise ProxyResponseError(
                    503,
                    openai_error(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        eventless_message,
                        error_type="server_error",
                    ),
                )
            return format_sse_event(
                cast(
                    Mapping[str, JsonValue],
                    response_failed_event(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        eventless_message,
                        response_id=_websocket_downstream_response_id(request_state),
                    ),
                )
            )

        def mark_bridge_eventless_failure() -> None:
            """Record the pre-response-start classification on the request log.

            ``stream_idle_timeout`` describes a stream that started and then
            went quiet for ``stream_idle_timeout_seconds``. Nothing about this
            phase matches that, and stamping request logs with it hid every
            eventless bridge handoff wedge behind an upstream-shaped label.
            """

            if request_state.failure_phase_override is None:
                request_state.failure_phase_override = "bridge"
            if request_state.failure_detail_override is None:
                request_state.failure_detail_override = _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL

        async def record_bridge_eventless_timeout_failure(
            selection: _HTTPBridgeRetryCircuitAttemptSelection | None = None,
        ) -> None:
            if selection is None:
                consecutive_failures = await self._record_http_bridge_retry_circuit_failure(
                    session,
                    detail=_HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                )
            else:
                consecutive_failures = await self._record_http_bridge_retry_circuit_failure_for_attempt_selection(
                    session,
                    detail=_HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                    selection=selection,
                )
            observed_response_events = max(
                request_state.response_event_count,
                int(
                    request_state.response_id is not None
                    or request_state.latency_response_created_ms is not None
                    or request_state.downstream_visible
                ),
            )
            if (
                observed_response_events > 0
                or consecutive_failures is None
                or consecutive_failures
                < _service_get_settings().http_responses_session_bridge_anchor_poison_failure_threshold
            ):
                return
            if await _abandon_durable_http_bridge_continuity(self, session):
                await self._retire_stale_pending_http_bridge_session(
                    session,
                    detail="repeated_zero_event_idle_timeout",
                    response_events_seen=0,
                    retired_request_count=0,
                )

        async def startup_continuity_cooldown_terminal_event() -> str | None:
            if (
                session.key.strength != "hard"
                or not continuity_bound_without_safe_replay()
                or request_state.verified_stale_anchor_replay
                or request_state.response_id is not None
                or request_state.response_event_count > 0
            ):
                return None
            retry_cooldown_seconds = await self._http_bridge_precreated_retry_cooldown_seconds(session)
            if retry_cooldown_seconds <= 0:
                return None
            if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
            _record_continuity_fail_closed(
                surface="http_bridge",
                reason="retry_circuit_cooldown_continuity_bound",
                previous_response_id=request_state.previous_response_id,
                session_id=downstream_turn_state or request_state.session_id,
            )
            mark_bridge_eventless_failure()
            logger.info(
                "HTTP bridge eventless timeout fail-closed before submit without safe replay "
                "request_id=%s retry_after_seconds=%.1f",
                request_state.request_id,
                retry_cooldown_seconds,
            )
            # This path returns before the request is submitted, so the normal
            # detach/finally cleanup cannot settle an API-key reservation.
            # Release it before handing the synthetic terminal event to the
            # non-streaming collector.
            await self._release_websocket_request_state_reservation(request_state)
            request_state.api_key_reservation = None
            if propagate_http_errors and _http_bridge_client_full_history_recovery_enabled(request_state):
                raise ProxyResponseError(
                    400,
                    _http_bridge_client_full_history_recovery_error(),
                )
            if propagate_http_errors:
                if request_state.durable_owner_dead:
                    raise _http_bridge_dead_owner_previous_response_not_found_proxy_error(
                        previous_response_id=_http_bridge_dead_owner_previous_response_id(request_state),
                    )
                raise ProxyResponseError(
                    503,
                    openai_error(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
                        error_type="server_error",
                    ),
                    retry_after_seconds=max(1, math.ceil(retry_cooldown_seconds)),
                )
            return format_sse_event(
                cast(
                    Mapping[str, JsonValue],
                    _http_bridge_dead_owner_previous_response_not_found_terminal(
                        previous_response_id=_http_bridge_dead_owner_previous_response_id(request_state),
                        response_id=_websocket_downstream_response_id(request_state),
                    )
                    if request_state.durable_owner_dead
                    else response_failed_event(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
                        response_id=_websocket_downstream_response_id(request_state),
                    ),
                )
            )

        while True:
            budget_terminal_event = await operation_fenced_request_budget_terminal_event()
            if budget_terminal_event is not None:
                yield budget_terminal_event
                return
            if await wait_through_operation_fenced_startup_cooldown():
                continue
            startup_terminal_event = await startup_continuity_cooldown_terminal_event()
            if startup_terminal_event is not None:
                yield startup_terminal_event
                return
            try:
                if account_neutral_recovery:
                    await self._submit_http_bridge_request(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                        queue_limit=queue_limit,
                        recovery_turn_state=downstream_turn_state,
                    )
                else:
                    await self._submit_http_bridge_request(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                        queue_limit=queue_limit,
                    )
                lifecycle = request_state.deferred_account_backoff_lifecycle
                if lifecycle is not None:
                    lifecycle.settlement_owned = True
                _signal_propagated_responses_service_cleanup_ready()
            except ProxyResponseError as exc:
                if request_state.bridge_soft_capacity_reroute_allowed:
                    raise
                wait_plan = _http_bridge_capacity_wait_plan(exc, request_deadline=request_deadline)
                if wait_plan is None:
                    raise
                bounded_wait_seconds, account_capacity_wait_seconds, message = wait_plan
                exc_code, _exc_message = _proxy_error_code_message(exc)
                gate_contention = exc_code == "response_create_gate_timeout"
                if gate_contention and session.closed:
                    # The timed-out attempt retired the session (stuck
                    # pending work); retrying a closed session would commit
                    # a stream and then surface upstream_unavailable. Fail
                    # the startup cleanly instead.
                    raise
                if gate_contention:
                    # A sleeping gate waiter keeps occupying its bridge
                    # queue slot so per-session pending work stays bounded
                    # by the queue limit across retries.
                    async with session.pending_lock:
                        if session.queued_request_count >= queue_limit:
                            raise ProxyResponseError(
                                429,
                                openai_error(
                                    "bridge_queue_full",
                                    "HTTP responses session bridge queue is full",
                                    error_type="rate_limit_error",
                                ),
                            )
                        session.queued_request_count += 1
                logger.info(
                    "Waiting for account capacity before retrying HTTP bridge submit request_id=%s model=%s "
                    "account_id=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
                    request_state.request_id,
                    request_state.model,
                    session.account.id,
                    bounded_wait_seconds,
                    account_capacity_wait_seconds,
                    message,
                )
                try:
                    async for line in _iter_account_capacity_wait_sse(
                        request_id=request_state.request_id,
                        reason=message,
                        sleep_seconds=bounded_wait_seconds,
                        emit_keepalives=not propagate_http_errors,
                        request_state=request_state,
                    ):
                        yield line
                finally:
                    if gate_contention:
                        async with session.pending_lock:
                            session.queued_request_count = max(0, session.queued_request_count - 1)
                if _service_time().monotonic() >= request_deadline:
                    raise
                if gate_contention and session.closed:
                    raise
                # Durable hard-turn admission may rewrite the request state
                # with a completed predecessor before a pre-dispatch capacity
                # failure.  Retry the exact rewritten body rather than the
                # stale outer-loop payload, or the next attempt could lose the
                # injected previous_response_id and its continuity anchor.
                text_data = request_state.request_text or text_data
                continue
            break
        event_queue = request_state.event_queue
        assert event_queue is not None
        initial_retry_cooldown_seconds = await self._http_bridge_precreated_retry_cooldown_seconds(session)
        if (
            initial_retry_cooldown_seconds > 0
            and session.key.strength == "hard"
            and continuity_bound_without_safe_replay()
            and not request_state.verified_stale_anchor_replay
            and request_state.response_id is None
            and request_state.response_event_count == 0
            and event_queue.empty()
        ):
            if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
            _record_continuity_fail_closed(
                surface="http_bridge",
                reason="retry_circuit_cooldown_continuity_bound",
                previous_response_id=request_state.previous_response_id,
                session_id=downstream_turn_state or request_state.session_id,
            )
            mark_bridge_eventless_failure()
            logger.info(
                "HTTP bridge eventless timeout fail-closed at startup without safe replay "
                "request_id=%s retry_after_seconds=%.1f",
                request_state.request_id,
                initial_retry_cooldown_seconds,
            )
            terminal_event = format_sse_event(
                cast(
                    Mapping[str, JsonValue],
                    _http_bridge_dead_owner_previous_response_not_found_terminal(
                        previous_response_id=_http_bridge_dead_owner_previous_response_id(request_state),
                        response_id=_websocket_downstream_response_id(request_state),
                    )
                    if request_state.durable_owner_dead
                    else response_failed_event(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
                        response_id=_websocket_downstream_response_id(request_state),
                    ),
                )
            )
            # The request was submitted before the durable cooldown refresh,
            # so detach it before returning. This releases the response-create
            # gate, reservation, and pending queue entry while marking the
            # upstream handoff for retirement.
            await self._detach_http_bridge_request(session, request_state=request_state)
            if propagate_http_errors and _http_bridge_client_full_history_recovery_enabled(request_state):
                raise ProxyResponseError(
                    400,
                    _http_bridge_client_full_history_recovery_error(),
                )
            if propagate_http_errors:
                if request_state.durable_owner_dead:
                    raise _http_bridge_dead_owner_previous_response_not_found_proxy_error(
                        previous_response_id=_http_bridge_dead_owner_previous_response_id(request_state),
                    )
                raise ProxyResponseError(
                    503,
                    openai_error(
                        _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                        _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
                        error_type="server_error",
                    ),
                    retry_after_seconds=max(1, math.ceil(initial_retry_cooldown_seconds)),
                )
            yield terminal_event
            return
        try:
            if downstream_turn_state is not None and not account_neutral_recovery:
                await self._register_http_bridge_turn_state(session, downstream_turn_state)
            _signal_propagated_capacity_startup_ready()
            if request_state.capacity_startup_wait_event is not None:
                request_state.capacity_startup_wait_event.clear()
            if request_state.capacity_startup_ready_event is not None:
                request_state.capacity_startup_ready_event.set()
            event_queue = request_state.event_queue
            assert event_queue is not None
            yielded_any = False
            keepalive_sent = False
            keepalive_count = 0
            completed_delivery_suppression_logged = False
            circuit_keepalive_waiting = False
            circuit_keepalive_until: float | None = None

            async def completed_delivery_suppresses_idle_timeout(
                *,
                downstream_response_id: str,
                revoke_queue_if_inactive: bool,
            ) -> bool:
                nonlocal completed_delivery_suppression_logged, keepalive_count

                async with session.pending_lock:
                    completed_delivery_scope = request_state.completed_delivery_scope
                    completed_delivery_owns_queue = completed_delivery_scope is not None and (
                        completed_delivery_scope.active or completed_delivery_scope.terminal_enqueued
                    )
                    if completed_delivery_owns_queue:
                        keepalive_count = 0
                    elif revoke_queue_if_inactive:
                        # The terminal idle timeout and completed queue claim
                        # share this lock. Revoking the mutable queue here
                        # prevents a later completion from claiming an
                        # orphaned downstream consumer.
                        request_state.event_queue = None

                if completed_delivery_owns_queue and not completed_delivery_suppression_logged:
                    logger.info(
                        "HTTP bridge stream idle timeout suppressed during completed delivery "
                        "request_id=%s response_id=%s elapsed_seconds=%.2f",
                        request_state.request_id,
                        downstream_response_id,
                        max(0.0, _service_time().monotonic() - request_state.started_at),
                    )
                    completed_delivery_suppression_logged = True
                return completed_delivery_owns_queue

            def stream_idle_keepalive(*, downstream_response_id: str) -> str | None:
                nonlocal keepalive_sent, yielded_any

                if propagate_http_errors and request_state.response_id is None and not circuit_keepalive_waiting:
                    return None
                keepalive_sent = True
                yielded_any = True
                if PROMETHEUS_AVAILABLE and stream_keepalive_sent_total is not None:
                    stream_keepalive_sent_total.labels(surface="http_bridge").inc()
                if request_state.response_id or request_state.replay_downstream_response_id:
                    return format_sse_event(
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
                return _codex_keepalive_frame()

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
                    response_started = bool(
                        request_state.response_id
                        or request_state.replay_downstream_response_id
                        or request_state.response_event_count > 0
                        or request_state.latency_response_created_ms is not None
                    )
                    # Before response.created the downstream queue is silent by
                    # design, so stream_idle_timeout_seconds does not describe
                    # this phase. Use the named settings-derived pre-response
                    # budget (aligned with the owner-side stuck gate) instead of
                    # the implicit _STREAM_KEEPALIVE_MAX_COUNT * interval
                    # product that used to expire at ~60s under any settings.
                    eventless_budget_seconds = _http_bridge_eventless_budget_seconds(
                        settings,
                        fallback_seconds=keepalive_interval * stream_keepalive_max_count,
                    )
                    max_keepalive_count = (
                        max(
                            stream_keepalive_max_count,
                            math.ceil(max(0.001, stream_idle_timeout_seconds) / keepalive_interval),
                        )
                        if response_started
                        else _http_bridge_eventless_max_keepalive_count(
                            settings,
                            keepalive_interval_seconds=keepalive_interval,
                            floor_count=stream_keepalive_max_count,
                        )
                    )
                    wait_timeout = keepalive_interval
                    if circuit_keepalive_waiting:
                        # Once the circuit is cooling down, wake at the actual
                        # expiry instead of waiting through another full
                        # keepalive interval before checking it again.
                        max_keepalive_count = 1
                        if circuit_keepalive_until is not None:
                            wait_timeout = min(
                                wait_timeout,
                                max(0.001, circuit_keepalive_until - _service_time().monotonic()),
                            )
                    if not yielded_any and not keepalive_sent:
                        wait_timeout = max(wait_timeout, _http_bridge_startup_keepalive_grace_seconds())
                    try:
                        event_block = await asyncio.wait_for(event_queue.get(), timeout=wait_timeout)
                    except asyncio.TimeoutError:
                        if request_state.account_capacity_waiting:
                            keepalive_count = 0
                            if request_state.account_capacity_wait_suppress_keepalive:
                                continue
                            keepalive_sent = True
                            yielded_any = True
                            downstream_response_id = _websocket_downstream_response_id(request_state)
                            yield format_sse_event(
                                cast(
                                    Mapping[str, JsonValue],
                                    _account_capacity_wait_payload(
                                        request_state,
                                        request_id=request_state.request_id,
                                        reason=request_state.account_capacity_wait_reason,
                                        retry_after_seconds=request_state.account_capacity_wait_retry_after_seconds,
                                    ),
                                )
                            )
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
                            continue
                        downstream_response_id = _websocket_downstream_response_id(request_state)
                        completed_delivery_in_progress = await completed_delivery_suppresses_idle_timeout(
                            downstream_response_id=downstream_response_id,
                            revoke_queue_if_inactive=False,
                        )
                        if not completed_delivery_in_progress:
                            keepalive_count += 1
                        if not completed_delivery_in_progress and keepalive_count >= max_keepalive_count:
                            timed_out_retry_circuit_attempt_selection = (
                                _http_bridge_retry_circuit_attempt_selection_for_pending_requests((request_state,))
                            )
                            if not response_started:
                                retried = False
                                if not circuit_keepalive_waiting:
                                    retried, terminal_event = await retry_precreated_for_idle_recovery(
                                        downstream_response_id=downstream_response_id,
                                    )
                                    if terminal_event is not None:
                                        if await completed_delivery_suppresses_idle_timeout(
                                            downstream_response_id=downstream_response_id,
                                            revoke_queue_if_inactive=True,
                                        ):
                                            keepalive_event = stream_idle_keepalive(
                                                downstream_response_id=downstream_response_id,
                                            )
                                            if keepalive_event is not None:
                                                yield keepalive_event
                                            continue
                                        yield terminal_event
                                        break
                                    if retried:
                                        logger.info(
                                            "HTTP bridge stream idle recovery retried pre-response request_id=%s",
                                            request_state.request_id,
                                        )
                                        keepalive_count = 0
                                        keepalive_sent = False
                                        yielded_any = False
                                        continue
                                retry_cooldown_seconds = await self._http_bridge_precreated_retry_cooldown_seconds(
                                    session
                                )
                                fresh_replay_is_safe = bool(
                                    request_state.fresh_upstream_request_is_retry_safe
                                    and request_state.fresh_upstream_request_text
                                )
                                continuity_bound = continuity_bound_without_safe_replay()
                                if retry_cooldown_seconds > 0 and (
                                    continuity_bound or (session.key.strength == "hard" and not fresh_replay_is_safe)
                                ):
                                    if await completed_delivery_suppresses_idle_timeout(
                                        downstream_response_id=downstream_response_id,
                                        revoke_queue_if_inactive=True,
                                    ):
                                        keepalive_event = stream_idle_keepalive(
                                            downstream_response_id=downstream_response_id,
                                        )
                                        if keepalive_event is not None:
                                            yield keepalive_event
                                        continue
                                    if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                                        stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
                                    _record_continuity_fail_closed(
                                        surface="http_bridge",
                                        reason=(
                                            "retry_circuit_cooldown_continuity_bound"
                                            if continuity_bound
                                            else "retry_circuit_cooldown_no_safe_replay"
                                        ),
                                        previous_response_id=request_state.previous_response_id,
                                        session_id=downstream_turn_state or request_state.session_id,
                                    )
                                    mark_bridge_eventless_failure()
                                    logger.info(
                                        "HTTP bridge eventless timeout fail-closed without safe replay "
                                        "request_id=%s retry_after_seconds=%.1f continuity_bound=%s",
                                        request_state.request_id,
                                        retry_cooldown_seconds,
                                        continuity_bound,
                                    )
                                    if propagate_http_errors and _http_bridge_client_full_history_recovery_enabled(
                                        request_state
                                    ):
                                        raise ProxyResponseError(
                                            400,
                                            _http_bridge_client_full_history_recovery_error(),
                                        )
                                    yield format_sse_event(
                                        cast(
                                            Mapping[str, JsonValue],
                                            _http_bridge_dead_owner_previous_response_not_found_terminal(
                                                previous_response_id=_http_bridge_dead_owner_previous_response_id(
                                                    request_state
                                                ),
                                                response_id=downstream_response_id,
                                            )
                                            if request_state.durable_owner_dead
                                            else response_failed_event(
                                                _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                                                _HTTP_BRIDGE_EVENTLESS_COOLDOWN_MESSAGE,
                                                response_id=downstream_response_id,
                                            ),
                                        )
                                    )
                                    break
                                if retry_cooldown_seconds > 0:
                                    retry_cooldown_remaining_budget = max(
                                        0.0,
                                        request_deadline - _service_time().monotonic(),
                                    )
                                    if retry_cooldown_seconds >= retry_cooldown_remaining_budget:
                                        if await completed_delivery_suppresses_idle_timeout(
                                            downstream_response_id=downstream_response_id,
                                            revoke_queue_if_inactive=True,
                                        ):
                                            keepalive_event = stream_idle_keepalive(
                                                downstream_response_id=downstream_response_id,
                                            )
                                            if keepalive_event is not None:
                                                yield keepalive_event
                                            continue
                                        if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                                            stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
                                        mark_bridge_eventless_failure()
                                        logger.info(
                                            "HTTP bridge eventless timeout during retry circuit cooldown "
                                            "request_id=%s retry_after_seconds=%.1f remaining_budget_seconds=%.1f",
                                            request_state.request_id,
                                            retry_cooldown_seconds,
                                            retry_cooldown_remaining_budget,
                                        )
                                        if propagate_http_errors and _http_bridge_client_full_history_recovery_enabled(
                                            request_state
                                        ):
                                            raise ProxyResponseError(
                                                400,
                                                _http_bridge_client_full_history_recovery_error(),
                                            )
                                        yield format_sse_event(
                                            cast(
                                                Mapping[str, JsonValue],
                                                response_failed_event(
                                                    _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                                                    "HTTP responses session bridge retry cooldown "
                                                    "exceeds the request budget",
                                                    response_id=downstream_response_id,
                                                ),
                                            )
                                        )
                                        break
                                    circuit_keepalive_waiting = True
                                    keepalive_count = 0
                                    circuit_keepalive_until = _service_time().monotonic() + retry_cooldown_seconds
                                    if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                                        http_bridge_retry_circuit_total.labels(outcome="keepalive").inc()
                                    logger.info(
                                        "HTTP bridge stream waiting during retry circuit cooldown "
                                        "request_id=%s retry_after_seconds=%.1f",
                                        request_state.request_id,
                                        retry_cooldown_seconds,
                                    )
                                else:
                                    was_circuit_keepalive_waiting = circuit_keepalive_waiting
                                    circuit_keepalive_waiting = False
                                    circuit_keepalive_until = None
                                    if was_circuit_keepalive_waiting and not retried:
                                        retried, terminal_event = await retry_precreated_for_idle_recovery(
                                            downstream_response_id=downstream_response_id,
                                            after_circuit_cooldown=True,
                                        )
                                        if terminal_event is not None:
                                            if await completed_delivery_suppresses_idle_timeout(
                                                downstream_response_id=downstream_response_id,
                                                revoke_queue_if_inactive=True,
                                            ):
                                                keepalive_event = stream_idle_keepalive(
                                                    downstream_response_id=downstream_response_id,
                                                )
                                                if keepalive_event is not None:
                                                    yield keepalive_event
                                                continue
                                            yield terminal_event
                                            break
                                    if retried:
                                        logger.info(
                                            "HTTP bridge stream idle recovery retried after circuit cooldown "
                                            "request_id=%s",
                                            request_state.request_id,
                                        )
                                        keepalive_count = 0
                                        keepalive_sent = False
                                        yielded_any = False
                                        continue
                                    if not retried:
                                        if await completed_delivery_suppresses_idle_timeout(
                                            downstream_response_id=downstream_response_id,
                                            revoke_queue_if_inactive=True,
                                        ):
                                            keepalive_event = stream_idle_keepalive(
                                                downstream_response_id=downstream_response_id,
                                            )
                                            if keepalive_event is not None:
                                                yield keepalive_event
                                            continue
                                        await record_bridge_eventless_timeout_failure(
                                            timed_out_retry_circuit_attempt_selection
                                        )
                                        if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                                            stream_idle_timeout_total.labels(surface="http_bridge_eventless").inc()
                                        mark_bridge_eventless_failure()
                                        logger.info(
                                            "HTTP bridge eventless timeout request_id=%s keepalive_count=%s "
                                            "max_keepalive_count=%s eventless_budget_seconds=%.1f "
                                            "unmatched_upstream_liveness=%s",
                                            request_state.request_id,
                                            keepalive_count,
                                            max_keepalive_count,
                                            eventless_budget_seconds,
                                            session.unmatched_upstream_liveness_count,
                                        )
                                        eventless_message = _http_bridge_eventless_timeout_message(
                                            session.unmatched_upstream_liveness_count
                                        )
                                        yield format_sse_event(
                                            cast(
                                                Mapping[str, JsonValue],
                                                response_failed_event(
                                                    _HTTP_BRIDGE_EVENTLESS_TIMEOUT_DETAIL,
                                                    eventless_message,
                                                    response_id=downstream_response_id,
                                                ),
                                            )
                                        )
                                        break
                            elif response_started:
                                if await completed_delivery_suppresses_idle_timeout(
                                    downstream_response_id=downstream_response_id,
                                    revoke_queue_if_inactive=True,
                                ):
                                    keepalive_event = stream_idle_keepalive(
                                        downstream_response_id=downstream_response_id,
                                    )
                                    if keepalive_event is not None:
                                        yield keepalive_event
                                    continue
                                if PROMETHEUS_AVAILABLE and stream_idle_timeout_total is not None:
                                    stream_idle_timeout_total.labels(surface="http_bridge").inc()
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
                        keepalive_event = stream_idle_keepalive(
                            downstream_response_id=downstream_response_id,
                        )
                        if keepalive_event is not None:
                            yield keepalive_event
                        continue
                else:
                    event_block = await event_queue.get()
                if event_block is None:
                    break
                keepalive_count = 0
                # A real upstream event means the stream is active again; do
                # not carry the pre-response retry-circuit wake mode into the
                # normal response-started idle timeout policy.
                circuit_keepalive_waiting = False
                circuit_keepalive_until = None
                block_payload = parse_sse_data_json(event_block)
                block_event_type = _event_type_from_payload(None, block_payload)
                if request_state.latency_first_token_ms is None:
                    ttft_visible_at = _ttft_event_visible_at(
                        block_event_type, block_payload, request_state.ttft_reasoning_deltas
                    )
                    if ttft_visible_at is not None:
                        request_state.latency_first_token_ms = max(
                            0, int((ttft_visible_at - request_state.started_at) * 1000)
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
                        # Canonical public masking for an upstream
                        # previous_response_not_found rejection (see
                        # app.modules.proxy.api._mask_previous_response_not_found_error).
                        error_message=PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
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
                                PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
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
                await self._maybe_release_idle_http_bridge_session_lease(session)
