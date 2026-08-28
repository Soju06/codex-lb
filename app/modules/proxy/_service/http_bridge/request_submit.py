from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, cast
from uuid import uuid4

import anyio

from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.proxy import (  # noqa: F401
    CODEX_INSTALLATION_ID_HEADER,
    CODEX_TURN_METADATA_HEADER,
    ImageFetchSession,
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    _as_image_fetch_session,
    _finalize_responses_lite_reasoning_context,
    _historical_agent_control_output_occurrences,
    _inline_content_images,
    _inline_input_image_urls,
    _payload_has_responses_lite_websocket_marker,
    _payload_uses_responses_lite,
    _ws_transport_payload_budget_bytes,
    apply_codex_installation_metadata,
    filter_inbound_headers,
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
    UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
    UpstreamWebSocketTransportError,
    is_account_neutral_websocket_error_code,
)
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.utils.request_id import (
    ensure_request_id,
    ensure_request_scope_id,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import StickySessionKind
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
    _HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR,
    _await_task_deferring_cancellation,
    _build_http_bridge_prewarm_text,
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_is_previous_response_owner_unavailable,
    _http_bridge_key_strength,
    _http_bridge_precreated_retry_failure_error,
    _http_bridge_prewarm_enabled,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_counts_against_queue,
    _http_bridge_retry_circuit_attempt_selection_for_pending_requests,
    _log_http_bridge_event,
    _record_continuity_fail_closed,
    _record_http_bridge_prewarm_outcome,
    _register_http_bridge_turn_state_aliases_locked,
    _release_http_bridge_unanchored_handoff,
)
from app.modules.proxy._service.http_bridge.quarantine import (
    _record_http_bridge_quarantine_wedged_pending,
)
from app.modules.proxy._service.http_bridge.retry_circuit import (
    _http_bridge_anchor_poison_detail,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _call_with_supported_optional_kwargs,
    _classify_upstream_close,
    _count_external_image_urls,
    _enforce_response_create_size_limit,
    _estimated_lease_tokens_from_request_usage_budget,
    _fingerprint_input_items,
    _inline_top_level_input_image_urls,
    _normalize_service_tier_value,
    _normalize_session_id,
    _prepare_websocket_request_state_for_account_switch,
    _prepare_websocket_request_state_for_auth_replay,
    _prepare_websocket_request_state_for_visible_output_replay,
    _prewarm_response_timeout_seconds,
    _release_websocket_response_create_gate,
    _response_create_client_metadata,
    _security_work_advisory_event,
    _service_as_image_fetch_session,
    _service_get_settings,
    _service_get_settings_cache,
    _service_inline_input_image_urls,
    _service_lease_http_session,
    _service_time,
    _slim_response_create_payload_for_upstream,
    _upstream_response_create_max_bytes,
    _websocket_auth_failure_permanent_code,
    _websocket_auth_failure_requires_reauth,
    _websocket_request_text_is_account_neutral_fresh_replay,
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
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _api_key_fair_share_threshold_pct_from_settings,
    _clear_websocket_request_error_overrides,
    _copy_websocket_route_metadata_from_session,
    _event_type_from_payload,
    _HTTPBridgeResponseCreateAttempt,
    _HTTPBridgeRetryCircuitAttemptSelection,
    _HTTPBridgeSession,
    _request_log_client_fields,
    _websocket_request_can_replay_before_visible_output,
    _WebSocketRequestState,
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
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.continuity import is_http_bridge_account_neutral_replay
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeAliasRegistration,
    DurableBridgeAliasRegistrationReceipt,
    durable_bridge_api_key_scope,
    durable_bridge_hash,
    durable_bridge_operation_fingerprint,
    durable_bridge_operation_id,
)
from app.modules.proxy.fair_share import (
    API_KEY_STREAM_FAIR_SHARE_ERROR_CODE,
    ApiKeyFairShareDenialError,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
    _parse_openai_error,
)
from app.modules.proxy.load_balancer import effective_account_concurrency_caps
from app.modules.proxy.tool_call_dedupe import (
    dedupe_replayed_side_effect_input_items,
)

logger = logging.getLogger("app.modules.proxy.service")

_HTTP_BRIDGE_CLEAN_CLOSE_RETRY_MAX_COUNT = 1
_HTTP_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS = 2.0

_REQUEST_TRANSPORT_HTTP = "http"
_WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE = "account_auth_invalidated"
_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"
_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. codex-lb is continuing with normal account selection; the upstream request may still fail until "
    "an account with Trusted Access for Cyber is marked as security-work-authorized."
)


@dataclass(frozen=True, slots=True)
class _HTTPBridgeStaleGateSnapshot:
    pending_states: list[_WebSocketRequestState]
    queued_count: int
    threshold_seconds: float
    stale_request_states: list[_WebSocketRequestState]
    should_retire: bool
    retry_circuit_attempt_selection: _HTTPBridgeRetryCircuitAttemptSelection


def _http_bridge_cooldown_suppression_is_replay_safe(request_state: _WebSocketRequestState) -> bool:
    """Return whether a cooldown-suppressed submission is provably undispatched.

    The retry-circuit gate runs before any reconnect or send for the current
    submission, so a request that carries no continuation identity (client or
    proxy-injected), no file account pin, and has seen zero send attempts,
    zero upstream response events and zero replays cannot have executed
    upstream. Only those requests may degrade to the raw-HTTP fallback;
    ambiguous continuations keep the bounded 503 because a replay could
    execute the turn twice.

    A non-empty payload ``conversation`` counts as continuation identity even
    though it is neither an anchor nor a turn-state key: it binds the turn to
    the bridge session's account, and unlike a response anchor it has no owner
    index, so a raw-HTTP replay cannot prove that owner in a multi-account
    pool and would fail the turn closed instead of letting the cooldown expire.

    ``response_create_attempt_count`` is the send marker rather than
    ``awaiting_response_created``: the latter is set optimistically when the
    request state is built and is only cleared after a submission is torn
    down, so it is true for every fresh turn and would make this predicate —
    and the fallback it gates — unreachable. The attempt count increments at
    the ``response.create`` send itself and is never reset, so zero proves no
    frame left for upstream.
    """
    return (
        request_state.previous_response_id is None
        and not request_state.hard_continuity_anchor
        and not request_state.proxy_injected_previous_response_id
        and not request_state.file_required_preferred_account
        and request_state.response_id is None
        and request_state.response_event_count == 0
        and request_state.replay_count == 0
        and request_state.last_downstream_sequence_number is None
        and not request_state.downstream_visible
        and not request_state.payload_conversation_bound
        and request_state.response_create_attempt_count == 0
    )


def _http_bridge_client_full_history_recovery_enabled(request_state: _WebSocketRequestState) -> bool:
    """Return whether an ambiguous send failure may ask the client to replay."""
    settings = _service_get_settings()
    return (
        request_state.propagate_http_errors
        and getattr(settings, "http_responses_session_bridge_ambiguous_continuation_recovery_mode", "fail_closed")
        == "client_full_history_once"
        and request_state.previous_response_id is not None
        and request_state.response_id is None
        and request_state.response_event_count == 0
    )


def _http_bridge_server_anchored_replay_enabled(request_state: _WebSocketRequestState) -> bool:
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


def _http_bridge_operation_fence_for_hard_continuity_enabled(request_state: _WebSocketRequestState) -> bool:
    """Return whether a hard turn-state request may use the durable replay fence."""
    if not request_state.hard_continuity_anchor:
        return False
    return getattr(
        _service_get_settings(),
        "http_responses_session_bridge_ambiguous_continuation_recovery_mode",
        "fail_closed",
    ) in {"server_anchored_replay_once", "server_indefinite_recovery"}


def _http_bridge_operation_fingerprint(
    *,
    session_id: str,
    api_key_scope: str,
    request_state: _WebSocketRequestState,
    text_data: str,
) -> str:
    fingerprint_text = _text_without_account_installation_id(text_data)
    if request_state.previous_response_id is None and _http_bridge_operation_fence_for_hard_continuity_enabled(
        request_state
    ):
        # Hard turn-state requests do not carry previous_response_id. Scope
        # their operation identity to the durable session so identical prompts
        # in two conversations cannot collide in the global fingerprint fence.
        fingerprint_text = f"session:{session_id}\n{fingerprint_text}"
    return durable_bridge_operation_fingerprint(
        api_key_scope=api_key_scope,
        request_text=fingerprint_text,
    )


def _http_bridge_terminal_hard_turn_response_id(
    request_state: _WebSocketRequestState,
    operation: Any,
    *,
    allow_anchored_continuation: bool = False,
) -> str | None:
    """Return a completed hard-turn anchor when this is a new client turn.

    Hard turn-state requests can omit ``previous_response_id``. Their durable
    operation fingerprint is therefore otherwise identical for repeated
    prompts. A terminal operation with a response id represents the prior turn,
    not an in-flight retry, so the next request must advance from its response
    rather than replaying that transcript. Recovery/rebind states retain the
    operation identity and are intentionally excluded here. Spool completeness
    is required only when replaying the stored transcript.
    """
    if (
        (request_state.previous_response_id is not None and not allow_anchored_continuation)
        or not request_state.hard_continuity_anchor
        or request_state.operation_id is not None
        or request_state.operation_rebind_required
        or request_state.replay_count != 0
    ):
        return None
    operation_state = getattr(operation, "state", None)
    operation_state = getattr(operation_state, "value", operation_state)
    if operation_state != "completed":
        return None
    response_id = getattr(operation, "response_id", None)
    return response_id if isinstance(response_id, str) and response_id else None


def _http_bridge_client_full_history_recovery_error() -> OpenAIErrorEnvelope:
    payload = openai_error(
        "previous_response_not_found",
        "Previous response was not found; retry without previous_response_id.",
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "previous_response_id"
    return payload


async def _rollback_http_bridge_recovery_turn_state_registration(
    service: Any,
    receipt: DurableBridgeAliasRegistrationReceipt,
) -> tuple[bool, asyncio.CancelledError | None]:
    rollback_task = asyncio.create_task(
        service._durable_bridge.rollback_recovery_turn_state_registration(receipt=receipt)
    )
    return await _await_task_deferring_cancellation(rollback_task)


async def _send_http_bridge_request_text_with_archive_id(
    session: "_HTTPBridgeSession",
    request_state: _WebSocketRequestState,
    text_data: str,
    *,
    on_send_started: Callable[[], None] | None = None,
) -> None:
    text_data = _text_with_operation_id(text_data, request_state.operation_id)
    # Operation metadata is added after the initial payload sizing pass. Check
    # the exact frame that will cross the websocket so the metadata cannot
    # push an otherwise-valid response.create over the upstream limit.
    _enforce_http_bridge_response_create_text_size(request_state, text_data)
    if on_send_started is not None:
        on_send_started()
    token = set_request_id(request_state.archive_request_id)
    try:
        request_state.response_create_attempt_count += 1
        attempt = _HTTPBridgeResponseCreateAttempt(ordinal=request_state.response_create_attempt_count)
        request_state.response_create_attempt = attempt
        request_state.response_create_sent_at = _service_time().monotonic()
        session.upstream_reader_wakeup.set()
        try:
            await session.upstream.send_text(text_data)
        except BaseException:
            # A failed or cancelled send is settled by its caller. Disarm the
            # owner watchdog before lifecycle ownership is released so the
            # reader cannot race that cleanup and settle the request twice.
            attempt.disarmed = True
            if request_state.response_create_attempt is attempt:
                request_state.response_create_sent_at = None
            session.upstream_reader_wakeup.set()
            raise
    finally:
        reset_request_id(token)


async def _settle_claimed_http_bridge_liveness_failure(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    error_message: str,
) -> None:
    """Finish the pending-deque settlement claimed beside a failed send."""

    if session.liveness_settlement_owner != "send":
        raise RuntimeError("HTTP bridge liveness settlement started without the send claim")
    async with session.lifecycle_lock:
        await service._fail_http_bridge_reader_and_maybe_retire(
            session,
            error_code=UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
            error_message=error_message,
            penalize_account=False,
            force_retire=True,
        )


def _text_with_account_installation_id(text_data: str, codex_installation_id: str | None) -> str:
    payload = json.loads(text_data)
    if not isinstance(payload, dict):
        return text_data
    apply_codex_installation_metadata(cast(dict[str, JsonValue], payload), codex_installation_id)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _text_with_operation_id(text_data: str, operation_id: str | None) -> str:
    """Attach a stable operation identity without changing the request contract."""
    if not operation_id:
        return text_data
    try:
        payload = json.loads(text_data)
    except (TypeError, json.JSONDecodeError):
        return text_data
    if not isinstance(payload, dict):
        return text_data
    raw_metadata = payload.get("client_metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    # This namespace is reserved by the bridge; never trust a caller-supplied
    # value to stand in for the durable operation identity.
    metadata["codex_lb_operation_id"] = operation_id
    payload["client_metadata"] = metadata
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _text_without_operation_id(text_data: str) -> str:
    """Remove caller-supplied bridge identity before durable fingerprinting."""
    try:
        payload = json.loads(text_data)
    except (TypeError, json.JSONDecodeError):
        return text_data
    if not isinstance(payload, dict):
        return text_data
    raw_metadata = payload.get("client_metadata")
    if not isinstance(raw_metadata, dict) or "codex_lb_operation_id" not in raw_metadata:
        return text_data
    metadata = dict(raw_metadata)
    metadata.pop("codex_lb_operation_id", None)
    if metadata:
        payload["client_metadata"] = metadata
    else:
        payload.pop("client_metadata", None)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _text_without_account_installation_id(text_data: str) -> str:
    """Normalize account-specific installation metadata out of a fingerprint."""
    try:
        payload = json.loads(text_data)
    except (TypeError, json.JSONDecodeError):
        return text_data
    if not isinstance(payload, dict):
        return text_data
    raw_metadata = payload.get("client_metadata")
    if not isinstance(raw_metadata, dict):
        return text_data
    metadata: dict[str, JsonValue] = {}
    for key, value in raw_metadata.items():
        if not isinstance(key, str) or key.lower() == CODEX_INSTALLATION_ID_HEADER:
            continue
        if key.lower() == CODEX_TURN_METADATA_HEADER and isinstance(value, str):
            try:
                turn_metadata = json.loads(value)
            except json.JSONDecodeError:
                turn_metadata = None
            if isinstance(turn_metadata, dict) and "installation_id" in turn_metadata:
                turn_metadata.pop("installation_id", None)
                value = json.dumps(turn_metadata, ensure_ascii=True, separators=(",", ":"))
        metadata[key] = value
    if metadata:
        payload["client_metadata"] = metadata
    else:
        payload.pop("client_metadata", None)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _text_with_previous_response_id(text_data: str, response_id: str | None) -> str:
    if not response_id:
        return text_data
    try:
        payload = json.loads(text_data)
    except (TypeError, json.JSONDecodeError):
        return text_data
    if not isinstance(payload, dict) or not response_id:
        return text_data
    payload["previous_response_id"] = response_id
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _enforce_http_bridge_response_create_text_size(
    request_state: _WebSocketRequestState,
    text_data: str,
) -> None:
    original_request_text = request_state.request_text
    request_state.request_text = text_data
    try:
        _enforce_response_create_size_limit(request_state)
    finally:
        request_state.request_text = original_request_text


def _request_kind_from_headers(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return "normal"
    raw_turn_metadata = headers.get("x-codex-turn-metadata") or headers.get("X-Codex-Turn-Metadata")
    if not isinstance(raw_turn_metadata, str):
        return "normal"
    try:
        turn_metadata = json.loads(raw_turn_metadata)
    except json.JSONDecodeError:
        return "normal"
    if not isinstance(turn_metadata, dict):
        return "normal"
    raw_request_kind = turn_metadata.get("request_kind")
    if not isinstance(raw_request_kind, str):
        return "normal"
    request_kind = raw_request_kind.strip()
    if request_kind in {"normal", "prewarm"}:
        return request_kind
    return "normal"


class _HTTPBridgeRequestSubmitMixin:
    @staticmethod
    def _http_bridge_clean_close_retry_max_count() -> int:
        configured = _HTTP_BRIDGE_CLEAN_CLOSE_RETRY_MAX_COUNT
        # Keep this recovery bounded even if an unsafe higher value is supplied.
        return max(0, min(1, configured))

    @staticmethod
    def _http_bridge_clean_close_retry_jitter_seconds() -> float:
        settings = _service_get_settings()
        maximum = max(
            0.0,
            min(
                30.0,
                float(
                    getattr(
                        settings,
                        "http_responses_session_bridge_clean_close_retry_jitter_max_seconds",
                        _HTTP_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS,
                    )
                ),
            ),
        )
        return random.uniform(0.0, maximum) if maximum > 0 else 0.0

    def _prepare_http_bridge_request(
        self: Any,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        request_id: str | None = None,
        client_ip: str | None = None,
        enforce_openai_sdk_contract: bool = True,
        preserve_responses_lite_client_metadata: bool = False,
    ) -> tuple[_WebSocketRequestState, str]:
        request_state, text_data = self._prepare_response_bridge_request_state(
            payload,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            include_type_field=True,
            attach_event_queue=True,
            transport=_REQUEST_TRANSPORT_HTTP,
            client_metadata=_response_create_client_metadata(
                payload.to_payload(),
                headers=headers,
                preserve_existing_responses_lite=preserve_responses_lite_client_metadata,
            ),
            headers=headers,
            session_id=_owner_lookup_session_id_from_headers(headers),
            request_log_id=request_id or get_request_id() or ensure_request_id(None),
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
        (
            request_state.useragent,
            request_state.useragent_group,
            request_state.conversation_id,
        ) = _request_log_client_fields(headers)
        request_state.client_ip = client_ip
        return request_state, text_data

    def _prepare_response_bridge_request_state(
        self: Any,
        payload: ResponsesRequest,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        include_type_field: bool,
        attach_event_queue: bool,
        transport: str,
        client_metadata: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str] | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        request_log_id: str | None = None,
        enforce_openai_sdk_contract: bool = True,
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
        protected_agent_control_output_occurrences = (
            _historical_agent_control_output_occurrences(cast(list[JsonValue], payload.input))
            if isinstance(payload.input, list)
            else {}
        )
        upstream_payload = dict(payload.to_payload())
        upstream_payload.pop("stream", None)
        upstream_payload.pop("background", None)
        if include_type_field:
            upstream_payload["type"] = "response.create"
        if client_metadata:
            upstream_payload["client_metadata"] = client_metadata
        _finalize_responses_lite_reasoning_context(
            upstream_payload,
            responses_lite=(
                _payload_uses_responses_lite(upstream_payload)
                or _payload_has_responses_lite_websocket_marker(upstream_payload)
            ),
        )
        forwarded_service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        input_item_count = 0
        input_full_fingerprint: str | None = None
        payload_input = payload.input
        if isinstance(payload_input, list):
            payload_input_list = cast(list[JsonValue], payload_input)
            input_item_count = len(payload_input_list)
            if input_item_count > 0:
                input_full_fingerprint = _fingerprint_input_items(payload_input_list)

        resolved_request_id = request_id or f"ws_{uuid4().hex}"
        header_request_kind = _request_kind_from_headers(headers)
        generate_false_prewarm = header_request_kind == "prewarm" and upstream_payload.get("generate") is False
        connection_request_kind = header_request_kind if transport == _REQUEST_TRANSPORT_WEBSOCKET else None
        request_kind = (
            "normal" if connection_request_kind == "prewarm" and not generate_false_prewarm else header_request_kind
        )
        request_state = _WebSocketRequestState(
            request_id=resolved_request_id,
            request_log_id=request_log_id,
            archive_request_id=request_log_id or resolved_request_id,
            model=payload.model,
            service_tier=forwarded_service_tier,
            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
            api_key_reservation=api_key_reservation,
            started_at=_service_time().monotonic(),
            requested_service_tier=forwarded_service_tier,
            awaiting_response_created=True,
            event_queue=asyncio.Queue() if attach_event_queue else None,
            transport=transport,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            api_key=api_key,
            request_usage_budget=estimate_api_key_request_usage(payload),
            previous_response_id=payload.previous_response_id,
            session_id=_normalize_session_id(session_id),
            hard_continuity_anchor=(
                payload.previous_response_id is not None
                or _sticky_key_from_turn_state_header(headers or {}) is not None
            ),
            payload_conversation_bound=bool(payload.conversation),
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
            request_kind=request_kind,
            connection_request_kind=connection_request_kind,
            generate_false_prewarm=generate_false_prewarm,
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
                protected_agent_control_output_occurrences=protected_agent_control_output_occurrences,
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

    def _http_bridge_text_with_account_installation_id(
        self: Any,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> str:
        codex_installation_id = getattr(session.account, "codex_installation_id", None)
        updated_text = _text_with_account_installation_id(text_data, codex_installation_id)
        if request_state.fresh_upstream_request_text is not None:
            updated_fresh_text = _text_with_account_installation_id(
                request_state.fresh_upstream_request_text,
                codex_installation_id,
            )
            _enforce_http_bridge_response_create_text_size(request_state, updated_fresh_text)
            request_state.fresh_upstream_request_text = updated_fresh_text
        if updated_text == text_data:
            return text_data
        request_state.request_text = updated_text
        _enforce_response_create_size_limit(request_state)
        return updated_text

    async def _inline_http_bridge_image_urls(
        self: Any,
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

    async def _submit_http_bridge_request(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        recovery_turn_state: str | None = None,
    ) -> None:
        request_scope_id = ensure_request_scope_id()
        owned_unanchored_handoff = session.unanchored_reservation_id == request_scope_id
        try:
            await self._submit_http_bridge_request_with_handoff(
                session,
                request_state=request_state,
                text_data=text_data,
                queue_limit=queue_limit,
                request_scope_id=request_scope_id,
                owned_unanchored_handoff=owned_unanchored_handoff,
                recovery_turn_state=recovery_turn_state,
            )
        finally:
            _release_http_bridge_unanchored_handoff(
                session,
                request_scope_id=request_scope_id,
            )
            # Inner pre-submit cleanup may clear the reservation before control
            # returns here, so ownership must be captured before awaiting it.
            # Only that request can make detached-session retirement newly
            # ready; an ordinary send/reader failure already owns terminal
            # settlement, and closing again would run that funnel twice.
            if (
                owned_unanchored_handoff
                and session.upstream_control.retire_after_drain
                and not session.upstream_close_attempted
            ):
                await self._retire_http_bridge_after_drain_if_ready(session)

    async def _http_bridge_operation_fenced_continuity_replay_allowed(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> bool:
        """Allow a cooldown bypass only for an already-fenced hard turn."""
        if (
            not _http_bridge_operation_fence_for_hard_continuity_enabled(request_state)
            or request_state.previous_response_id is not None
            or session.durable_session_id is None
            or session.durable_owner_epoch is None
        ):
            return False
        get_operation_by_fingerprint = getattr(self._durable_bridge, "get_operation_by_fingerprint", None)
        if not callable(get_operation_by_fingerprint):
            return False
        api_key_scope = durable_bridge_api_key_scope(session.key.api_key_id)
        request_fingerprint = _http_bridge_operation_fingerprint(
            session_id=session.durable_session_id,
            api_key_scope=api_key_scope,
            request_state=request_state,
            text_data=text_data,
        )
        try:
            operation = await _call_with_supported_optional_kwargs(
                get_operation_by_fingerprint,
                optional_kwargs={"api_key_scope": api_key_scope},
                request_fingerprint=request_fingerprint,
            )
        except Exception:
            logger.warning(
                "Failed to inspect hard-continuity operation fence before retry request_id=%s",
                request_state.request_id,
                exc_info=True,
            )
            return False
        if operation is None or operation.session_id != session.durable_session_id:
            return False
        operation_state = getattr(operation.state, "value", operation.state)
        return operation_state == "unknown" or (
            operation_state in {"completed", "incomplete"} and bool(getattr(operation, "event_spool_complete", False))
        )

    async def _submit_http_bridge_request_with_handoff(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
        request_scope_id: str,
        owned_unanchored_handoff: bool,
        recovery_turn_state: str | None = None,
    ) -> None:
        recovery_attempt_consumed = False
        allow_operation_fenced_continuity_replay = False
        if _http_bridge_operation_fence_for_hard_continuity_enabled(request_state):
            retry_cooldown_seconds = await self._http_bridge_precreated_retry_cooldown_seconds(session)
            if retry_cooldown_seconds > 0:
                allow_operation_fenced_continuity_replay = (
                    await self._http_bridge_operation_fenced_continuity_replay_allowed(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                    )
                )
        # Eventless upstream timeouts retire the current socket.  A client
        # reconnect can otherwise create a fresh socket for the same hard key
        # and submit the identical request repeatedly while the retry circuit
        # is cooling down.  Gate new submissions before any reconnect/send so
        # the circuit turns this into a bounded 503 instead of another
        # response.create attempt.  A proof-gated full resend remains allowed
        # because it is the client's own replay-safe request, not an opaque
        # continuation replay.
        allow_proof_gated_continuity_replay = bool(
            request_state.previous_response_id is not None
            and request_state.fresh_upstream_request_is_retry_safe
            and request_state.fresh_upstream_request_text
            and request_state.response_event_count == 0
            and request_state.replay_count == 0
        )
        retry_allowed = await self._http_bridge_precreated_retry_allowed(
            session,
            allow_proof_gated_continuity_replay=allow_proof_gated_continuity_replay,
            allow_operation_fenced_continuity_replay=allow_operation_fenced_continuity_replay,
        )
        if not retry_allowed:
            retry_after_seconds = max(
                1,
                math.ceil(await self._http_bridge_precreated_retry_cooldown_seconds(session)),
            )
            _log_http_bridge_event(
                "submit_retry_circuit_suppressed",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail="hard_key_cooldown",
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            cooldown_error = ProxyResponseError(
                503,
                openai_error(
                    "upstream_request_timeout",
                    "HTTP responses session bridge is cooling down after repeated upstream timeouts; retry shortly.",
                ),
                retry_after_seconds=retry_after_seconds,
            )
            if _http_bridge_cooldown_suppression_is_replay_safe(request_state):
                setattr(cooldown_error, _HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
                setattr(cooldown_error, _HTTP_BRIDGE_COOLDOWN_SUPPRESSION_ATTR, True)
            raise cooldown_error
        # Persist the recovery checkpoint only after the retry circuit has
        # admitted this request. A client reconnect suppressed by the
        # cooldown must not create or refresh a journal entry for a request
        # that was never dispatched upstream.
        if (
            request_state.fresh_upstream_request_is_retry_safe
            and request_state.fresh_upstream_request_text
            and request_state.replay_count == 0
            and request_state.recovery_attempt_fingerprint is None
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
        ):
            attempt_fingerprint = durable_bridge_hash(request_state.fresh_upstream_request_text)
            try:
                attempt = await self._durable_bridge.record_recovery_attempt(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    request_fingerprint=attempt_fingerprint,
                    request_id=request_state.request_id,
                    account_id=session.account.id,
                    model=request_state.model,
                    replay_safe=True,
                )
                if attempt is None:
                    # ``None`` is the durable owner fence rejecting this
                    # worker, not an unavailable journal (which raises and
                    # is handled below). Never dispatch from a stale owner.
                    session.closed = True
                    session.upstream_control.reconnect_requested = True
                    session.upstream_control.retire_after_drain = True
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="recovery_attempt_owner_fence_rejected",
                        previous_response_id=request_state.previous_response_id,
                        session_id=request_state.session_id,
                        upstream_error_code="bridge_continuity_persistence_failed",
                    )
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "HTTP responses session ownership changed; retry the request.",
                        ),
                    )
                if getattr(attempt.state, "value", attempt.state) != "unknown":
                    if getattr(attempt.state, "value", attempt.state) != "replayed":
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "The recovery checkpoint was already consumed; retry the request.",
                            ),
                        )
                    # A REPLAYED checkpoint may belong to a completed
                    # operation whose finalized transcript is safe to replay.
                    # Defer rejection until the operation ledger lookup below
                    # can decide that terminal-spool case; nonterminal rows
                    # remain fail-closed after that lookup.
                    recovery_attempt_consumed = True
                if (
                    not recovery_attempt_consumed
                    and getattr(attempt, "request_id", request_state.request_id) != request_state.request_id
                ):
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "Another recovery request is already in flight; retry the request.",
                        ),
                    )
                request_state.recovery_attempt_fingerprint = attempt_fingerprint
                request_state.recovery_attempt_session_id = session.durable_session_id
                request_state.recovery_attempt_owner_epoch = session.durable_owner_epoch
            except ProxyResponseError:
                raise
            except Exception as exc:
                # The journal is an additional recovery fence. Without a
                # durable UNKNOWN row, an ambiguous send cannot be claimed by
                # another owner, so fail closed instead of dispatching an
                # unjournaled recovery-safe request.
                session.closed = True
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                _record_continuity_fail_closed(
                    surface="http_bridge",
                    reason="recovery_attempt_persistence_failed",
                    previous_response_id=request_state.previous_response_id,
                    session_id=request_state.session_id,
                    upstream_error_code="bridge_continuity_persistence_failed",
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "Recovered response continuity could not be persisted; retry the request.",
                    ),
                ) from exc
        # Account installation metadata is part of the final upstream frame.
        # Apply and size-check it before recording the operation so a local
        # payload-too-large rejection cannot leave a submitted retry fence.
        text_data = self._http_bridge_text_with_account_installation_id(session, request_state, text_data)
        operation_ledger_enabled = bool(
            getattr(_service_get_settings(), "http_responses_session_bridge_operation_ledger_enabled", True)
        )
        operation_ledger_for_hard_continuity = _http_bridge_operation_fence_for_hard_continuity_enabled(request_state)
        record_operation = getattr(self._durable_bridge, "record_operation", None)
        if (
            operation_ledger_enabled
            and callable(record_operation)
            and (
                request_state.previous_response_id is not None
                or operation_ledger_for_hard_continuity
                or request_state.operation_rebind_required
                or recovery_attempt_consumed
            )
            and (request_state.operation_id is None or request_state.operation_rebind_required)
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
        ):
            text_data = _text_without_operation_id(text_data)
            api_key_scope = durable_bridge_api_key_scope(session.key.api_key_id)
            operation_fingerprint = (
                request_state.operation_fingerprint
                if request_state.operation_rebind_required and request_state.operation_fingerprint is not None
                else _http_bridge_operation_fingerprint(
                    session_id=session.durable_session_id,
                    api_key_scope=api_key_scope,
                    request_state=request_state,
                    text_data=text_data,
                )
            )
            operation_id = (
                request_state.operation_id
                if request_state.operation_rebind_required and request_state.operation_id is not None
                else durable_bridge_operation_id(session.durable_session_id, operation_fingerprint)
            )
            operation_parent_response_id = (
                request_state.operation_parent_response_id
                if request_state.operation_rebind_required
                else request_state.previous_response_id
            )
            # The operation row must not be committed until the exact
            # operation-tagged frame is known to fit. Otherwise a local size
            # rejection before ``send_text`` leaves a submitted ledger row
            # that fences every identical retry as an unknown in-flight turn.
            operation_tagged_text = _text_with_operation_id(text_data, operation_id)
            _enforce_http_bridge_response_create_text_size(request_state, operation_tagged_text)
            try:
                get_operation_by_fingerprint = getattr(self._durable_bridge, "get_operation_by_fingerprint", None)
                get_operation = getattr(self._durable_bridge, "get_operation", None)

                async def lookup_operation() -> Any:
                    operation = None
                    if callable(get_operation_by_fingerprint):
                        operation = await _call_with_supported_optional_kwargs(
                            get_operation_by_fingerprint,
                            optional_kwargs={"api_key_scope": api_key_scope},
                            request_fingerprint=operation_fingerprint,
                        )
                    if operation is None and callable(get_operation):
                        operation = await get_operation(operation_id=operation_id)
                    return operation

                existing_operation = await lookup_operation()
                if recovery_attempt_consumed and existing_operation is None:
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "The recovery checkpoint was already consumed; retry the request.",
                        ),
                    )
                hard_turn_chain_advanced = False
                seen_hard_turn_response_ids: set[str] = set()
                while not recovery_attempt_consumed:
                    terminal_hard_turn_response_id = _http_bridge_terminal_hard_turn_response_id(
                        request_state,
                        existing_operation,
                        allow_anchored_continuation=hard_turn_chain_advanced,
                    )
                    if (
                        terminal_hard_turn_response_id is not None
                        and terminal_hard_turn_response_id not in seen_hard_turn_response_ids
                    ):
                        # A completed operation with the same body is the
                        # prior hard turn, not a replay request: advance from
                        # its response instead of replaying that transcript.
                        # Keep walking the chain because repeated identical
                        # turns can have several terminal operations with
                        # successive response anchors.
                        seen_hard_turn_response_ids.add(terminal_hard_turn_response_id)
                        hard_turn_chain_advanced = True
                        text_data = _text_with_previous_response_id(text_data, terminal_hard_turn_response_id)
                        request_state.request_text = text_data
                        request_state.previous_response_id = terminal_hard_turn_response_id
                        request_state.proxy_injected_previous_response_id = True
                        request_state.hard_continuity_anchor = True
                        operation_parent_response_id = terminal_hard_turn_response_id
                        operation_fingerprint = durable_bridge_operation_fingerprint(
                            api_key_scope=api_key_scope,
                            request_text=_text_without_account_installation_id(text_data),
                        )
                        operation_id = durable_bridge_operation_id(
                            session.durable_session_id,
                            operation_fingerprint,
                        )
                        operation_tagged_text = _text_with_operation_id(text_data, operation_id)
                        _enforce_http_bridge_response_create_text_size(request_state, operation_tagged_text)
                        existing_operation = await lookup_operation()
                        continue

                    # If another worker durably observed the previous turn's
                    # completion, advance a new continuation to that response
                    # anchor instead of replaying the timed-out turn. Re-run
                    # the operation lookup after this race-path advancement so
                    # two completions observed back-to-back are both walked.
                    if existing_operation is None:
                        get_latest_completed = getattr(self._durable_bridge, "get_latest_completed_operation", None)
                        if callable(get_latest_completed):
                            completed_operation = await _call_with_supported_optional_kwargs(
                                get_latest_completed,
                                optional_kwargs={"request_fingerprint": operation_fingerprint},
                                session_id=session.durable_session_id,
                                parent_response_id=operation_parent_response_id,
                            )
                            if completed_operation is None:
                                get_latest_completed_any_session = getattr(
                                    self._durable_bridge,
                                    "get_latest_completed_operation_any_session",
                                    None,
                                )
                                if callable(get_latest_completed_any_session):
                                    completed_operation = await _call_with_supported_optional_kwargs(
                                        get_latest_completed_any_session,
                                        optional_kwargs={
                                            "api_key_scope": api_key_scope,
                                            "request_fingerprint": operation_fingerprint,
                                        },
                                        parent_response_id=request_state.previous_response_id,
                                    )
                            completed_response_id = getattr(completed_operation, "response_id", None)
                            if completed_response_id and completed_response_id != request_state.previous_response_id:
                                text_data = _text_with_previous_response_id(text_data, completed_response_id)
                                request_state.request_text = text_data
                                request_state.previous_response_id = completed_response_id
                                request_state.proxy_injected_previous_response_id = True
                                operation_parent_response_id = completed_response_id
                                hard_turn_chain_advanced = True
                                seen_hard_turn_response_ids.add(completed_response_id)
                                request_state.hard_continuity_anchor = True
                                operation_fingerprint = durable_bridge_operation_fingerprint(
                                    api_key_scope=api_key_scope,
                                    request_text=_text_without_account_installation_id(text_data),
                                )
                                operation_id = durable_bridge_operation_id(
                                    session.durable_session_id,
                                    operation_fingerprint,
                                )
                                operation_tagged_text = _text_with_operation_id(text_data, operation_id)
                                _enforce_http_bridge_response_create_text_size(request_state, operation_tagged_text)
                                existing_operation = await lookup_operation()
                                continue
                    break
                operation = await _call_with_supported_optional_kwargs(
                    record_operation,
                    optional_kwargs={
                        "recovery_attempt_session_id": request_state.recovery_attempt_session_id
                        if request_state.recovery_attempt_claimed
                        else None,
                        "recovery_attempt_owner_epoch": request_state.recovery_attempt_owner_epoch
                        if request_state.recovery_attempt_claimed
                        else None,
                        "recovery_attempt_fingerprint": request_state.recovery_attempt_fingerprint
                        if request_state.recovery_attempt_claimed
                        else None,
                        "recovery_attempt_consumed": recovery_attempt_consumed,
                    },
                    operation_id=operation_id,
                    session_id=session.durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    request_fingerprint=operation_fingerprint,
                    api_key_scope=api_key_scope,
                    account_id=session.account.id,
                    model=request_state.model,
                    parent_response_id=operation_parent_response_id,
                    request_text=text_data,
                )
            except Exception as exc:
                session.closed = True
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                _record_continuity_fail_closed(
                    surface="http_bridge",
                    reason="operation_persistence_failed",
                    previous_response_id=request_state.previous_response_id,
                    session_id=request_state.session_id,
                    upstream_error_code="bridge_continuity_persistence_failed",
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "Response operation continuity could not be persisted; retry the request.",
                    ),
                ) from exc
            if operation is None:
                session.closed = True
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "HTTP responses session ownership changed; retry the request.",
                    ),
                )
            if recovery_attempt_consumed and operation.created:
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "bridge_continuity_persistence_failed",
                        "The recovery checkpoint was already consumed; retry the request.",
                    ),
                )
            if not operation.created and not getattr(operation, "rebound", False):
                if operation.state in {"completed", "incomplete"}:
                    if getattr(operation, "event_spool_complete", False):
                        get_operation_events = getattr(self._durable_bridge, "get_operation_events", None)
                        replay_events = (
                            await get_operation_events(operation_id=operation.operation_id)
                            if callable(get_operation_events)
                            else []
                        )
                        if replay_events and request_state.event_queue is not None:
                            request_state.operation_replay = True
                            request_state.operation_id = operation.operation_id
                            request_state.operation_fingerprint = operation_fingerprint
                            request_state.operation_registered = True
                            for replay_event in replay_events:
                                await request_state.event_queue.put(replay_event)
                            await request_state.event_queue.put(None)
                            return
                if recovery_attempt_consumed:
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "The recovery checkpoint was already consumed; retry the request.",
                        ),
                    )
                recovery_mode = getattr(
                    _service_get_settings(),
                    "http_responses_session_bridge_ambiguous_continuation_recovery_mode",
                    "fail_closed",
                )
                indefinite_recovery = recovery_mode == "server_indefinite_recovery"
                one_shot_recovery = recovery_mode == "server_anchored_replay_once" and request_state.replay_count == 0
                async with session.pending_lock:
                    same_operation_pending = any(
                        pending_request is not request_state
                        and getattr(pending_request, "operation_id", None) == operation.operation_id
                        for pending_request in session.pending_requests
                    )
                if (
                    (indefinite_recovery or one_shot_recovery)
                    and operation.state == "unknown"
                    and not same_operation_pending
                ):
                    # A previous owner may have persisted a partial sequence
                    # before its socket died. Claim UNKNOWN atomically with
                    # the transcript reset so concurrent reconnects cannot
                    # both pass admission and submit the same operation.
                    claim_unknown_operation = getattr(
                        self._durable_bridge,
                        "claim_unknown_operation_for_recovery",
                        None,
                    )
                    if not callable(claim_unknown_operation):
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "HTTP response recovery could not claim the previous operation; retry the request.",
                            ),
                        )
                    claimed = await _call_with_supported_optional_kwargs(
                        claim_unknown_operation,
                        optional_kwargs={"max_recovery_dispatches": 1} if one_shot_recovery else {},
                        operation_id=operation.operation_id,
                        session_id=session.durable_session_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=session.durable_owner_epoch,
                    )
                    if not claimed:
                        session.closed = True
                        session.upstream_control.reconnect_requested = True
                        session.upstream_control.retire_after_drain = True
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "bridge_continuity_persistence_failed",
                                "HTTP response recovery ownership changed; retry the request.",
                            ),
                        )
                    request_state.operation_recovery_claimed = True
                    request_state.operation_attempt_generation = getattr(operation, "recovery_dispatch_count", 0) + 1
                    # The operation remains fenced to one durable identity.
                    # One-shot mode consumes its existing replay-count budget;
                    # indefinite mode may make further serialized attempts
                    # after cooldown because upstream has no idempotency or
                    # status endpoint.
                    request_state.operation_id = operation.operation_id
                    request_state.operation_fingerprint = operation_fingerprint
                    request_state.operation_registered = True
                else:
                    # A prior dispatch with the same parent and body may have
                    # been accepted by upstream, or another request may still
                    # be using the same operation in this session. Without
                    # upstream idempotency/status proof, never submit it a
                    # second time.
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="operation_already_recorded_no_status_proof",
                        previous_response_id=request_state.previous_response_id,
                        session_id=request_state.session_id,
                        upstream_error_code="upstream_operation_status_unknown",
                    )
                    retry_after_seconds = max(
                        1,
                        math.ceil(await self._http_bridge_precreated_retry_cooldown_seconds(session)),
                    )
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "upstream_operation_status_unknown",
                            "The previous response operation may still be running; retry after the cooldown.",
                        ),
                        retry_after_seconds=retry_after_seconds,
                    )
            request_state.operation_id = operation.operation_id
            request_state.operation_fingerprint = operation_fingerprint
            request_state.operation_parent_response_id = operation_parent_response_id
            request_state.operation_registered = True
            request_state.operation_rebind_required = False
            request_state.operation_created = operation.created
            request_state.operation_rebound = getattr(operation, "rebound", False)
            request_state.operation_rebound_from_session_id = getattr(operation, "rebound_from_session_id", None)
            request_state.operation_rebound_from_account_id = getattr(operation, "rebound_from_account_id", None)
            request_state.operation_rebound_from_model = getattr(operation, "rebound_from_model", None)
            request_state.operation_rebound_from_parent_response_id = getattr(
                operation, "rebound_from_parent_response_id", None
            )
            request_state.operation_persisted_response_id = (
                None if request_state.operation_recovery_claimed else getattr(operation, "response_id", None)
            )
            if not request_state.operation_recovery_claimed:
                request_state.operation_attempt_generation = getattr(operation, "recovery_dispatch_count", 0)

        async def _cleanup_unsubmitted_recovery_claim() -> None:
            if (
                not request_state.operation_recovery_claimed
                and not request_state.operation_created
                and not request_state.operation_rebound
            ) or request_state.operation_dispatched:
                return
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=False,
                request_enqueued=False,
                counted_in_queue=False,
            )

        text_data = self._http_bridge_text_with_account_installation_id(session, request_state, text_data)
        if request_state.response_id is not None or request_state.response_event_count > 0:
            await _cleanup_unsubmitted_recovery_claim()
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
        if session.upstream_control.retire_after_drain and not owned_unanchored_handoff:
            await _cleanup_unsubmitted_recovery_claim()
            if not session.upstream_close_attempted:
                await self._retire_http_bridge_after_drain_if_ready(session)
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "HTTP responses session bridge is retiring"),
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
                        await _cleanup_unsubmitted_recovery_claim()
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
                    try:
                        recovered = await self._retry_http_bridge_request_on_fresh_upstream(
                            session,
                            request_state=request_state,
                            text_data=text_data,
                            send_request=False,
                            require_same_account=_http_bridge_key_strength(session.key) == "hard",
                        )
                    except BaseException:
                        await _cleanup_unsubmitted_recovery_claim()
                        raise
                    if recovered:
                        session.closed = False
                    else:
                        await _cleanup_unsubmitted_recovery_claim()
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
        text_data = self._http_bridge_text_with_account_installation_id(session, request_state, text_data)
        request_state.session_previous_gap_ms = int(max(0.0, request_state.started_at - session.last_used_at) * 1000)
        gate_acquired = False
        request_enqueued = False
        admission_waiter_registered = False
        try:
            async with session.pending_lock:
                await self._ensure_http_bridge_session_stream_lease_locked(session, request_state=request_state)
                # Register the submit as an admission waiter atomically with the
                # reacquire so a previous turn's finalizer unwinding concurrently
                # cannot see an apparently idle session and release this lease
                # before the turn is counted into the session queue.
                session.admission_waiter_count += 1
                admission_waiter_registered = True
        except BaseException:
            # Recovery claims are made before admission. If reacquiring an
            # idle session's stream lease fails, no upstream frame can have
            # been sent; restore that claim before propagating the admission
            # error so a later reconnect is not fenced as already dispatched.
            if getattr(session, "unanchored_reservation_id", None) == request_scope_id:
                session.unanchored_reservation_id = None
            cleanup_task = asyncio.create_task(
                self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=request_state,
                    gate_acquired=False,
                    request_enqueued=False,
                    counted_in_queue=False,
                    admission_waiter_registered=admission_waiter_registered,
                )
            )
            await _await_task_deferring_cancellation(cleanup_task)
            raise
        try:
            await self._maybe_prewarm_http_bridge_session(
                session,
                request_state=request_state,
                text_data=text_data,
            )
        except BaseException:
            if getattr(session, "unanchored_reservation_id", None) == request_scope_id:
                session.unanchored_reservation_id = None
            cleanup_task = asyncio.create_task(
                self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=request_state,
                    gate_acquired=False,
                    request_enqueued=False,
                    counted_in_queue=False,
                    admission_waiter_registered=admission_waiter_registered,
                )
            )
            await _await_task_deferring_cancellation(cleanup_task)
            raise
        try:
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
                await self._ensure_http_bridge_session_stream_lease_locked(session, request_state=request_state)
                session.queued_request_count += 1
                if getattr(session, "unanchored_reservation_id", None) == request_scope_id:
                    session.unanchored_reservation_id = None
        except BaseException:
            if getattr(session, "unanchored_reservation_id", None) == request_scope_id:
                session.unanchored_reservation_id = None
            cleanup_task = asyncio.create_task(
                self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=request_state,
                    gate_acquired=False,
                    request_enqueued=False,
                    counted_in_queue=False,
                    admission_waiter_registered=admission_waiter_registered,
                )
            )
            await _await_task_deferring_cancellation(cleanup_task)
            raise
        try:
            text_data = await self._inline_http_bridge_image_urls(text_data, request_state)
            text_data = self._http_bridge_text_with_account_installation_id(session, request_state, text_data)
            self._start_request_state_api_key_reservation_heartbeat(
                request_state,
                api_key=request_state.api_key,
                surface="http_bridge",
            )
            _copy_websocket_route_metadata_from_session(request_state, session)
            request_state.bridge_queue_wait_started_at = _service_time().monotonic()
            request_state.response_create_gate_wait_started_at = _service_time().monotonic()
            # Bridge ownership is established before this late admission. A
            # cap race stays a bounded error/wait on that owner; it must not
            # publish a replacement bridge as a spillover side effect.
            await self._acquire_request_state_response_create_admission(
                request_state,
                response_create_gate=session.response_create_gate,
                account_id=session.account.id,
                surface="http_bridge",
                bridge_session=session,
            )
            gate_acquired = True
            if request_state.bridge_queue_wait_started_at is not None:
                request_state.latency_bridge_queue_wait_ms = int(
                    max(0.0, _service_time().monotonic() - request_state.bridge_queue_wait_started_at) * 1000
                )
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
                # Queue publication clears the mutable reservation marker. The
                # proof captured before the first await still authorizes exactly
                # that request to submit on its detached, draining generation.
                detached_handoff_can_submit = (
                    owned_unanchored_handoff and session.upstream_control.retire_after_drain and not session.closed
                )
                if session.closed and current_session is session and not session.upstream_control.retire_after_drain:
                    recovered = await self._retry_http_bridge_request_on_fresh_upstream(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                        send_request=False,
                        require_same_account=_http_bridge_key_strength(session.key) == "hard",
                    )
                    if recovered:
                        session.closed = False
                if session.closed or ((session_unregistered or session_replaced) and not detached_handoff_can_submit):
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
                recovery_receipt: DurableBridgeAliasRegistrationReceipt | None = None
                upstream_send_started = False
                try:
                    if recovery_turn_state is not None:
                        registration_cancellation: asyncio.CancelledError | None = None
                        try:
                            async with session.recovery_alias_lock:
                                registration_task = asyncio.create_task(
                                    self._register_http_bridge_recovery_turn_state_locked(
                                        session,
                                        recovery_turn_state,
                                    )
                                )
                                (
                                    (
                                        recovery_alias_registered,
                                        recovery_receipt,
                                    ),
                                    registration_cancellation,
                                ) = await _await_task_deferring_cancellation(registration_task)
                                if registration_cancellation is not None or not recovery_alias_registered:
                                    session.closed = True
                                    session.upstream_control.reconnect_requested = True
                                    session.upstream_control.retire_after_drain = True
                                    if (
                                        recovery_receipt is not None
                                        and recovery_receipt.status == DurableBridgeAliasRegistration.REGISTERED
                                    ):
                                        try:
                                            (
                                                rolled_back,
                                                rollback_cancellation,
                                            ) = await _rollback_http_bridge_recovery_turn_state_registration(
                                                self,
                                                recovery_receipt,
                                            )
                                        except Exception:
                                            rolled_back = False
                                            rollback_cancellation = None
                                            logger.warning(
                                                "Failed to roll back recovered HTTP bridge turn-state alias",
                                                exc_info=True,
                                            )
                                        registration_cancellation = registration_cancellation or rollback_cancellation
                                        recovery_receipt = None
                                        if not rolled_back:
                                            _record_continuity_fail_closed(
                                                surface="http_bridge",
                                                reason="recovery_alias_rollback_failed",
                                                previous_response_id=request_state.previous_response_id,
                                                session_id=request_state.session_id,
                                                upstream_error_code="bridge_continuity_persistence_failed",
                                            )
                                if registration_cancellation is not None:
                                    raise registration_cancellation
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.warning(
                                "Failed to persist recovered HTTP bridge turn-state before upstream dispatch",
                                exc_info=True,
                            )
                            recovery_alias_registered = False
                        if not recovery_alias_registered:
                            session.closed = True
                            session.upstream_control.reconnect_requested = True
                            session.upstream_control.retire_after_drain = True
                            _record_continuity_fail_closed(
                                surface="http_bridge",
                                reason="recovery_alias_registration_failed",
                                previous_response_id=request_state.previous_response_id,
                                session_id=request_state.session_id,
                                upstream_error_code="bridge_continuity_persistence_failed",
                            )
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "Recovered response continuity could not be persisted; retry the request.",
                                ),
                            )
                    if request_state.recovery_attempt_fingerprint is not None:
                        try:
                            owner_lookup = await self._durable_bridge.renew_live_session(
                                session_id=session.durable_session_id,
                                api_key_id=session.key.api_key_id,
                                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
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
                            or owner_lookup.owner_instance_id
                            != _service_get_settings().http_responses_session_bridge_instance_id
                            or owner_lookup.owner_epoch != session.durable_owner_epoch
                        ):
                            session.closed = True
                            session.upstream_control.reconnect_requested = True
                            session.upstream_control.retire_after_drain = True
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "HTTP responses session ownership changed before dispatch; retry the request.",
                                ),
                            )
                    # The journal entry is created before queue admission so
                    # concurrent requests cannot both enter the gate without
                    # a recovery generation. Revalidate it after the gate and
                    # lifecycle locks, immediately before dispatch: a waiter
                    # may have observed UNKNOWN while the first request
                    # settled the row REPLAYED.
                    if (
                        request_state.recovery_attempt_fingerprint is not None
                        and not request_state.recovery_attempt_claimed
                    ):
                        if (
                            request_state.recovery_attempt_session_id != session.durable_session_id
                            or request_state.recovery_attempt_owner_epoch != session.durable_owner_epoch
                        ):
                            session.closed = True
                            session.upstream_control.reconnect_requested = True
                            session.upstream_control.retire_after_drain = True
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "HTTP responses session ownership changed before dispatch; retry the request.",
                                ),
                            )
                        try:
                            dispatch_attempt = await self._durable_bridge.record_recovery_attempt(
                                session_id=session.durable_session_id,
                                api_key_id=session.key.api_key_id,
                                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                                owner_epoch=session.durable_owner_epoch,
                                request_fingerprint=request_state.recovery_attempt_fingerprint,
                                request_id=request_state.request_id,
                                account_id=session.account.id,
                                model=request_state.model,
                                replay_safe=True,
                            )
                        except Exception as exc:
                            session.closed = True
                            session.upstream_control.reconnect_requested = True
                            session.upstream_control.retire_after_drain = True
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "Recovered response continuity could not be revalidated; retry the request.",
                                ),
                            ) from exc
                        if (
                            dispatch_attempt is None
                            or getattr(dispatch_attempt.state, "value", dispatch_attempt.state) != "unknown"
                            or getattr(dispatch_attempt, "request_id", request_state.request_id)
                            != request_state.request_id
                        ):
                            session.closed = True
                            session.upstream_control.reconnect_requested = True
                            session.upstream_control.retire_after_drain = True
                            raise ProxyResponseError(
                                502,
                                openai_error(
                                    "bridge_continuity_persistence_failed",
                                    "The recovery checkpoint was consumed before dispatch; retry the request.",
                                ),
                            )
                    if (
                        request_state.verified_stale_anchor_replay
                        and request_state.verified_stale_anchor_retry_circuit_generation_captured
                    ):
                        circuit_key = request_state.verified_stale_anchor_retry_circuit_key
                        generation_claimed = bool(
                            circuit_key is not None
                            and await self._claim_http_bridge_retry_circuit_generation(
                                key=circuit_key,
                                captured=request_state.verified_stale_anchor_retry_circuit_generation_captured,
                                generation=request_state.verified_stale_anchor_retry_circuit_generation,
                            )
                        )
                        if not generation_claimed:
                            suppressed_retry_after_seconds = max(
                                1,
                                math.ceil(
                                    await self._http_bridge_retry_circuit_cooldown_seconds_for_key(
                                        circuit_key or session.key,
                                    )
                                ),
                            )
                            _log_http_bridge_event(
                                "stale_anchor_replay_generation_suppressed",
                                circuit_key or session.key,
                                account_id=session.account.id,
                                model=session.request_model,
                                detail="circuit_generation_advanced_before_dispatch_claim",
                                cache_key_family=(circuit_key or session.key).affinity_kind,
                                model_class=(
                                    _extract_model_class(session.request_model) if session.request_model else None
                                ),
                            )
                            raise ProxyResponseError(
                                503,
                                openai_error(
                                    "upstream_request_timeout",
                                    "HTTP responses session bridge is cooling down after repeated upstream "
                                    "timeouts; retry shortly.",
                                ),
                                retry_after_seconds=suppressed_retry_after_seconds,
                            )
                    async with session.pending_lock:
                        session.pending_requests.append(request_state)
                        session.admission_waiter_count = max(0, session.admission_waiter_count - 1)
                        admission_waiter_registered = False
                    request_enqueued = True

                    def mark_upstream_send_started() -> None:
                        nonlocal upstream_send_started
                        # The helper invokes this only after the final frame
                        # size preflight. A payload_too_large rejection must
                        # therefore remain proven pre-dispatch so cleanup can
                        # roll back a newly-created operation.
                        upstream_send_started = True

                    try:
                        await _send_http_bridge_request_text_with_archive_id(
                            session,
                            request_state,
                            text_data,
                            on_send_started=mark_upstream_send_started,
                        )
                    except BaseException as exc:
                        request_state.recovery_attempt_dispatched = upstream_send_started
                        request_state.operation_dispatched = (
                            request_state.operation_id is not None and upstream_send_started
                        )
                        # Publish retirement while lifecycle ownership is still
                        # held; a gate waiter must never reuse an ambiguously sent
                        # response.create socket between unlock and cleanup.
                        session.closed = True
                        session.upstream_control.reconnect_requested = True
                        session.upstream_control.retire_after_drain = True
                        if (
                            isinstance(exc, UpstreamWebSocketTransportError)
                            and exc.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE
                        ):
                            # Only this narrow claim, not ``closed``, tells the
                            # reader that the submitter will settle siblings.
                            # Keep it inside lifecycle_lock with the failing
                            # send so the reader cannot observe an ownership gap.
                            session.claim_liveness_settlement()
                        raise
                    request_state.recovery_attempt_dispatched = True
                    request_state.operation_dispatched = request_state.operation_id is not None
                    session.last_used_at = _service_time().monotonic()
                except asyncio.CancelledError:
                    if recovery_receipt is not None and not upstream_send_started:
                        session.closed = True
                        session.upstream_control.reconnect_requested = True
                        session.upstream_control.retire_after_drain = True
                        async with session.recovery_alias_lock:
                            try:
                                (
                                    rolled_back,
                                    _rollback_cancellation,
                                ) = await _rollback_http_bridge_recovery_turn_state_registration(
                                    self,
                                    recovery_receipt,
                                )
                            except Exception:
                                rolled_back = False
                                logger.warning(
                                    "Failed to roll back cancelled HTTP bridge recovery alias",
                                    exc_info=True,
                                )
                            if not rolled_back:
                                _record_continuity_fail_closed(
                                    surface="http_bridge",
                                    reason="recovery_alias_rollback_failed",
                                    previous_response_id=request_state.previous_response_id,
                                    session_id=request_state.session_id,
                                    upstream_error_code="bridge_continuity_persistence_failed",
                                )
                    raise
        except ProxyResponseError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
                admission_waiter_registered=admission_waiter_registered,
            )
            if not session.upstream_close_attempted:
                await self._retire_http_bridge_after_drain_if_ready(session)
            raise
        except asyncio.CancelledError as cancellation:
            cleanup_task = asyncio.create_task(
                self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=request_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=True,
                    admission_waiter_registered=admission_waiter_registered,
                )
            )
            try:
                await _await_task_deferring_cancellation(cleanup_task)
            except Exception:
                logger.warning("Failed to clean up cancelled HTTP bridge submit", exc_info=True)
            if session.upstream_control.retire_after_drain and not session.upstream_close_attempted:
                retire_task = asyncio.create_task(self._retire_http_bridge_after_drain_if_ready(session))
                try:
                    await _await_task_deferring_cancellation(retire_task)
                except Exception:
                    logger.warning("Failed to retire cancelled HTTP bridge recovery", exc_info=True)
            raise cancellation
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
            # send_text may fail after the complete response.create frame was
            # handed to the kernel. Never reconnect-and-resend from this path;
            # only failures proven to precede dispatch may be replayed.
            error_code = exc.error_code if isinstance(exc, UpstreamWebSocketTransportError) else "stream_incomplete"
            failure_error_message = str(exc) or "Upstream websocket closed before response.completed"
            # Liveness expiry and local network loss are transport failures,
            # not evidence against the selected account. Keep this in sync
            # with the reader path's shared provenance classification.
            account_neutral = is_account_neutral_websocket_error_code(error_code)
            if error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE:
                # The sender claimed ownership beside the failing send while
                # holding lifecycle_lock. It therefore owns the entire session
                # deque, including older in-flight requests; settling only this
                # request would strand its siblings after the reader yields.
                # Publish the cleanup task before the first await after the
                # claim. Shielding it makes cancellation wait for settlement,
                # so the claim can never outlive its exactly-once owner.
                settlement_task = asyncio.create_task(
                    _settle_claimed_http_bridge_liveness_failure(
                        self,
                        session,
                        error_message=str(exc) or "Upstream websocket liveness failed",
                    ),
                    name="http-bridge-liveness-send-settlement",
                )
                _, settlement_cancellation = await _await_task_deferring_cancellation(settlement_task)
                if settlement_cancellation is not None:
                    raise settlement_cancellation
            else:
                # Once the operation-tagged frame has been handed to the
                # socket, the transport exception is ambiguous: upstream may
                # have accepted it even though this worker saw no
                # acknowledgement. Persist UNKNOWN under the owner fence
                # before cleanup can retire the closed session and release
                # that fence.
                if (
                    request_state.operation_dispatched
                    and request_state.operation_registered
                    and request_state.operation_id is not None
                    and session.durable_session_id is not None
                    and session.durable_owner_epoch is not None
                ):
                    mark_operation_unknown = getattr(self._durable_bridge, "mark_operation_unknown", None)
                    marked_unknown = False
                    if callable(mark_operation_unknown):
                        try:
                            marked_unknown = await mark_operation_unknown(
                                operation_id=request_state.operation_id,
                                session_id=session.durable_session_id,
                                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                                owner_epoch=session.durable_owner_epoch,
                            )
                        except Exception:
                            logger.warning(
                                "Failed to mark ambiguous HTTP bridge operation UNKNOWN operation_id=%s",
                                request_state.operation_id,
                                exc_info=True,
                            )
                    if not marked_unknown:
                        request_state.operation_registered = False
                        error_code = "bridge_continuity_persistence_failed"
                        failure_error_message = (
                            "Ambiguous response operation could not be persisted; retry the request."
                        )
                        _record_continuity_fail_closed(
                            surface="http_bridge",
                            reason="ambiguous_operation_unknown_persistence_failed",
                            previous_response_id=request_state.previous_response_id,
                            session_id=request_state.session_id,
                            upstream_error_code=error_code,
                        )
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=request_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=True,
                    admission_waiter_registered=admission_waiter_registered,
                )
                await self._fail_pending_websocket_requests(
                    account=session.account,
                    account_id_value=session.account.id,
                    pending_requests=deque([request_state]),
                    pending_lock=anyio.Lock(),
                    error_code=error_code,
                    error_message=failure_error_message,
                    api_key=None,
                    response_create_gate=session.response_create_gate,
                    penalize_account=not account_neutral,
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
            if _http_bridge_client_full_history_recovery_enabled(request_state):
                raise ProxyResponseError(
                    400,
                    _http_bridge_client_full_history_recovery_error(),
                ) from exc
            raise ProxyResponseError(
                502,
                openai_error(error_code, failure_error_message),
            ) from exc

    async def _maybe_prewarm_http_bridge_session(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> None:
        settings = _service_get_settings()
        if not session.codex_session or session.prewarmed or request_state.previous_response_id is not None:
            request_state.prewarm_status = request_state.prewarm_status or "not_applicable"
            return
        if not _http_bridge_prewarm_enabled(settings):
            request_state.prewarm_status = "not_applicable"
            return
        prewarm_lock = session.prewarm_lock
        if prewarm_lock is None:
            request_state.prewarm_status = "skipped"
            _record_http_bridge_prewarm_outcome(outcome="skipped")
            return
        async with prewarm_lock:
            if session.prewarmed:
                request_state.prewarm_status = "skipped"
                _record_http_bridge_prewarm_outcome(outcome="skipped")
                return
            warmup_text = _build_http_bridge_prewarm_text(text_data)
            session.prewarmed = True
            if warmup_text is None:
                request_state.prewarm_status = "skipped"
                _record_http_bridge_prewarm_outcome(outcome="skipped")
                return

            prewarm_started_at = _service_time().monotonic()
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
            warmup_send_started = False
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
                    warmup_send_started = True
                    await _send_http_bridge_request_text_with_archive_id(session, warmup_state, warmup_text)
                while True:
                    try:
                        event_block = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=_prewarm_response_timeout_seconds(),
                        )
                    except asyncio.TimeoutError:
                        request_state.prewarm_latency_ms = int(
                            max(0.0, _service_time().monotonic() - prewarm_started_at) * 1000
                        )
                        request_state.prewarm_status = "timeout"
                        _record_http_bridge_prewarm_outcome(outcome="timeout")
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
                                require_same_account=is_http_bridge_account_neutral_replay(
                                    kind=session.key.affinity_kind,
                                    key=session.key.affinity_key,
                                ),
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
                request_state.prewarm_latency_ms = int(max(0.0, session.last_used_at - prewarm_started_at) * 1000)
                request_state.prewarm_status = "success"
                _record_http_bridge_prewarm_outcome(outcome="success")
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
                    request_state.prewarm_latency_ms = int(
                        max(0.0, _service_time().monotonic() - prewarm_started_at) * 1000
                    )
                    request_state.prewarm_status = "skipped"
                    _record_http_bridge_prewarm_outcome(outcome="skipped")
                    return
                session.prewarmed = False
                request_state.prewarm_latency_ms = int(
                    max(0.0, _service_time().monotonic() - prewarm_started_at) * 1000
                )
                request_state.prewarm_status = "error"
                _record_http_bridge_prewarm_outcome(outcome="error")
                raise
            except BaseException:
                if warmup_send_started:
                    session.closed = True
                    session.upstream_control.reconnect_requested = True
                    session.upstream_control.retire_after_drain = True
                session.prewarmed = False
                request_state.prewarm_latency_ms = int(
                    max(0.0, _service_time().monotonic() - prewarm_started_at) * 1000
                )
                request_state.prewarm_status = "error"
                _record_http_bridge_prewarm_outcome(outcome="error")
                cleanup_task = asyncio.create_task(
                    self._cleanup_http_bridge_submit_interruption(
                        session,
                        request_state=warmup_state,
                        gate_acquired=gate_acquired,
                        request_enqueued=request_enqueued,
                        counted_in_queue=False,
                    )
                )
                await _await_task_deferring_cancellation(cleanup_task)
                if (
                    warmup_send_started
                    and session.upstream_control.retire_after_drain
                    and not session.upstream_close_attempted
                ):
                    retire_task = asyncio.create_task(self._retire_http_bridge_after_drain_if_ready(session))
                    await _await_task_deferring_cancellation(retire_task)
                raise

    async def _cleanup_http_bridge_submit_interruption(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        gate_acquired: bool,
        request_enqueued: bool,
        counted_in_queue: bool,
        admission_waiter_registered: bool = False,
    ) -> None:
        retire_closed_session = False
        async with session.pending_lock:
            if request_enqueued and request_state in session.pending_requests:
                session.pending_requests.remove(request_state)
            if counted_in_queue:
                session.queued_request_count = max(0, session.queued_request_count - 1)
            if admission_waiter_registered:
                session.admission_waiter_count = max(0, session.admission_waiter_count - 1)
            retire_closed_session = session.closed and session.admission_waiter_count == 0
        if (
            request_state.recovery_attempt_fingerprint is not None
            and not request_state.recovery_attempt_claimed
            and not request_state.recovery_attempt_dispatched
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
        ):
            rollback_recovery_attempt = getattr(self._durable_bridge, "rollback_recovery_attempt_before_dispatch", None)
            if callable(rollback_recovery_attempt):
                try:
                    await _call_with_supported_optional_kwargs(
                        rollback_recovery_attempt,
                        optional_kwargs={},
                        session_id=session.durable_session_id,
                        api_key_id=session.key.api_key_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=session.durable_owner_epoch,
                        request_fingerprint=request_state.recovery_attempt_fingerprint,
                    )
                except Exception:
                    logger.warning(
                        "Failed to roll back pre-dispatch HTTP bridge recovery checkpoint request_id=%s",
                        request_state.request_id,
                        exc_info=True,
                    )
        if (
            request_state.operation_recovery_claimed
            and request_state.operation_registered
            and request_state.operation_id is not None
            and not request_state.operation_dispatched
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
        ):
            mark_operation_unknown = getattr(self._durable_bridge, "mark_operation_unknown", None)
            restored = False
            if callable(mark_operation_unknown):
                try:
                    restored = await _call_with_supported_optional_kwargs(
                        mark_operation_unknown,
                        optional_kwargs={"restore_recovery_dispatch_claim": True},
                        operation_id=request_state.operation_id,
                        session_id=session.durable_session_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=session.durable_owner_epoch,
                    )
                except Exception:
                    logger.warning(
                        "Failed to restore pre-dispatch HTTP bridge recovery operation UNKNOWN operation_id=%s",
                        request_state.operation_id,
                        exc_info=True,
                    )
            if restored:
                request_state.operation_recovery_claimed = False
                request_state.operation_id = None
                request_state.operation_fingerprint = None
                request_state.operation_parent_response_id = None
        elif (
            (request_state.operation_created or request_state.operation_rebound)
            and request_state.operation_registered
            and request_state.operation_id is not None
            and not request_state.operation_dispatched
            and session.durable_session_id is not None
            and session.durable_owner_epoch is not None
        ):
            rollback_operation = getattr(self._durable_bridge, "rollback_operation_before_dispatch", None)
            if callable(rollback_operation):
                try:
                    rolled_back = await _call_with_supported_optional_kwargs(
                        rollback_operation,
                        optional_kwargs={
                            "restore_rebound": request_state.operation_rebound,
                            "rebound_from_session_id": request_state.operation_rebound_from_session_id,
                            "rebound_from_account_id": request_state.operation_rebound_from_account_id,
                            "rebound_from_model": request_state.operation_rebound_from_model,
                            "rebound_from_parent_response_id": request_state.operation_rebound_from_parent_response_id,
                        },
                        operation_id=request_state.operation_id,
                        session_id=session.durable_session_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=session.durable_owner_epoch,
                    )
                except Exception:
                    rolled_back = False
                    logger.warning(
                        "Failed to roll back pre-dispatch HTTP bridge operation operation_id=%s",
                        request_state.operation_id,
                        exc_info=True,
                    )
                if rolled_back:
                    request_state.operation_registered = False
                    request_state.operation_created = False
                    if request_state.operation_rebound:
                        # The failed durable row was restored rather than
                        # deleted. Preserve its identity so a capacity/gate
                        # retry must rebind that exact fence before send.
                        request_state.operation_rebind_required = True
                        request_state.operation_rebound = False
                    else:
                        request_state.operation_id = None
                        request_state.operation_fingerprint = None
                        request_state.operation_parent_response_id = None
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        if request_state.response_create_gate is not None:
            if gate_acquired or request_state.response_create_gate_acquired:
                await _release_websocket_response_create_gate(request_state, session.response_create_gate)
            else:
                account_response_create_lease = request_state.account_response_create_lease
                account_response_create_release = request_state.account_response_create_release
                request_state.account_response_create_lease = None
                request_state.account_response_create_release = None
                if account_response_create_lease is not None and account_response_create_release is not None:
                    await account_response_create_release(account_response_create_lease)
                if request_state.response_create_admission is not None:
                    request_state.response_create_admission.release()
                    request_state.response_create_admission = None
                request_state.awaiting_response_created = False
                request_state.response_create_gate = None
                request_state.response_create_gate_acquired = False
        elif gate_acquired:
            await _release_websocket_response_create_gate(request_state, session.response_create_gate)
        if retire_closed_session:
            await self._retire_stale_pending_http_bridge_session(
                session,
                detail="last_admission_waiter_cancelled",
                response_events_seen=max(
                    request_state.response_event_count,
                    int(request_state.response_id is not None or request_state.latency_response_created_ms is not None),
                ),
            )
        await self._maybe_release_idle_http_bridge_session_lease(session)

    async def _ensure_http_bridge_session_stream_lease_locked(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState | None = None,
    ) -> None:
        """Reacquire the account stream lease for a session idled between turns.

        Callers hold ``session.pending_lock``. The lease is released when the
        session's last in-flight turn detaches, so an idle session does not
        occupy a per-account stream slot; the next turn must pass normal cap
        admission again. Denial raises the standard local-cap envelope so the
        existing recoverable capacity wait applies.

        The lease stays per-session (one upstream stream): a session that
        already holds a lease admits further queued turns without acquiring
        another, because those turns multiplex over the session's single
        upstream WebSocket — unchanged from the pre-existing per-session
        lease lifecycle.

        Keyed sessions thread their API key through the reacquire so the
        turn joins the per-key stream accounting and passes the same
        congestion-gated fair-share admission as initial selection; a
        fair-share denial raises the standard local-cap envelope with the
        fair-share code so the recoverable capacity wait applies.
        """
        if session.account_lease is not None or session.closed:
            return
        load_balancer = getattr(self, "_load_balancer", None)
        if load_balancer is None:
            return
        api_key_id = session.key.api_key_id
        fair_share_threshold_pct = 0
        if api_key_id is not None:
            fair_share_threshold_pct = _api_key_fair_share_threshold_pct_from_settings(
                await _service_get_settings_cache().get()
            )
        try:
            lease = await load_balancer.acquire_account_lease(
                session.account.id,
                kind="stream",
                # Carry the turn's usage-budget estimate like initial selection
                # and reconnect do, so capacity-weighted routing pressure still
                # sees large turns on reused warm sessions.
                estimated_tokens=_estimated_lease_tokens_from_request_usage_budget(
                    request_state.request_usage_budget if request_state is not None else None
                ),
                api_key_id=api_key_id,
                api_key_stream_fair_share_threshold_pct=fair_share_threshold_pct,
            )
        except ApiKeyFairShareDenialError as denial:
            raise ProxyResponseError(
                429,
                openai_error(
                    API_KEY_STREAM_FAIR_SHARE_ERROR_CODE,
                    str(denial),
                    error_type="rate_limit_error",
                ),
            ) from None
        if lease is None:
            raise ProxyResponseError(
                429,
                openai_error(
                    "account_stream_cap",
                    "Account stream capacity is exhausted; wait for active streams to finish.",
                    error_type="rate_limit_error",
                ),
            )
        if session.closed:
            # A close or eviction ran while the acquire await was suspended
            # (the close path does not take pending_lock before settling the
            # session's lease). Installing this lease on the closed session
            # would leak the slot: close already settled, and the idle
            # release helper skips closed sessions. Return the slot and fail
            # the turn like any other submit on a closed bridge.
            async def release_detached_lease() -> None:
                try:
                    await load_balancer.release_account_lease(lease)
                except Exception:
                    logger.warning(
                        "Failed to release stream lease acquired for closed HTTP bridge session",
                        exc_info=True,
                    )

            release_task = asyncio.create_task(release_detached_lease())
            _, cancellation = await _await_task_deferring_cancellation(release_task)
            if cancellation is not None:
                raise cancellation
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
            )
        session.account_lease = lease

    async def _maybe_release_idle_http_bridge_session_lease(
        self: Any,
        session: "_HTTPBridgeSession",
    ) -> None:
        """Release the account stream lease once a session has no in-flight work.

        The per-account stream cap exists to bound concurrent upstream
        streams (one bridge session holds one slot for its single upstream
        WebSocket); an idle bridge session keeping its upstream WebSocket warm
        must not occupy a slot for its whole idle TTL. Session close releases
        via its own path, so a session that closed keeps that settlement.
        """
        load_balancer = getattr(self, "_load_balancer", None)
        if load_balancer is None:
            return
        lease = None
        async with session.pending_lock:
            if (
                not session.closed
                and session.account_lease is not None
                and session.queued_request_count == 0
                and session.admission_waiter_count == 0
                and not session.pending_requests
            ):
                lease = session.account_lease
                session.account_lease = None
        if lease is None:
            return

        async def release_idle_lease() -> None:
            try:
                await load_balancer.release_account_lease(lease)
            except Exception:
                logger.warning("Failed to release idle HTTP bridge account lease", exc_info=True)

        release_task = asyncio.create_task(release_idle_lease())
        _, cancellation = await _await_task_deferring_cancellation(release_task)
        if cancellation is not None:
            raise cancellation

    async def _detach_http_bridge_request(
        self: Any,
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
            # Queue revocation and pending ownership use the same lock. A
            # completed handler that wins first keeps its local queue reference;
            # a detach that wins first leaves no queue for that handler to claim.
            request_state.event_queue = None
        await _release_websocket_response_create_gate(request_state, session.response_create_gate)
        if not detached:
            if request_state.operation_replay:
                # Replay requests are delivered from the durable transcript
                # without entering pending ownership, so the normal detach
                # branch cannot settle their API-key reservation.
                self._cancel_request_state_api_key_reservation_heartbeat(request_state)
                await self._release_websocket_request_state_reservation(request_state)
                request_state.api_key_reservation = None
                request_state.operation_replay = False
                return False
            if request_state.terminal_settlement_phase == "abandoned":
                # Belt-and-braces for issue #1594: terminal bookkeeping
                # claimed this request out of pending ownership, aborted, and
                # its shielded abort settlement also failed. Nothing else owns
                # the reservation any more, so reclaim settlement here instead
                # of keying solely on pending-deque membership.
                self._cancel_request_state_api_key_reservation_heartbeat(request_state)
                await self._release_websocket_request_state_reservation(request_state)
                request_state.api_key_reservation = None
                request_state.terminal_settlement_phase = None
            return False
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        await self._release_websocket_request_state_reservation(request_state)
        request_state.api_key_reservation = None
        await self._retire_http_bridge_after_drain_if_ready(session)
        return True

    async def _fail_stale_http_bridge_pending_requests(
        self: Any,
        session: "_HTTPBridgeSession",
        request_states: list[_WebSocketRequestState],
        *,
        detail: str,
        retry_circuit_attempt_selection: _HTTPBridgeRetryCircuitAttemptSelection | None = None,
    ) -> None:
        if retry_circuit_attempt_selection is None:
            # Capture the physical sends before waiting for pending ownership.
            # A concurrent recovery may replace request_state.response_create_attempt
            # while this task is suspended on pending_lock.
            retry_circuit_attempt_selection = _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                request_states
            )
        stale_requests: deque[_WebSocketRequestState] = deque()
        response_events_seen = 0
        async with session.pending_lock:
            for request_state in request_states:
                if request_state not in session.pending_requests:
                    continue
                response_events_seen = max(
                    response_events_seen,
                    request_state.response_event_count,
                    int(
                        request_state.response_id is not None
                        or request_state.latency_response_created_ms is not None
                        or request_state.downstream_visible
                    ),
                )
                session.pending_requests.remove(request_state)
                if _http_bridge_request_counts_against_queue(request_state):
                    session.queued_request_count = max(0, session.queued_request_count - 1)
                stale_requests.append(request_state)
        if not stale_requests:
            return
        # A stale gate holder that streamed response events without ever
        # receiving ``response.created`` proves the reattach wedge (#1534)
        # even when the session itself survives with other active requests.
        _record_http_bridge_quarantine_wedged_pending(self, session, stale_requests)
        if response_events_seen == 0:
            await self._record_http_bridge_retry_circuit_failure_for_attempt_selection(
                session,
                detail=detail,
                selection=retry_circuit_attempt_selection,
            )
        await self._fail_pending_websocket_requests(
            account=session.account,
            account_id_value=session.account.id,
            pending_requests=stale_requests,
            pending_lock=session.pending_lock,
            error_code="upstream_request_timeout",
            error_message="HTTP bridge response-create gate holder timed out",
            api_key=None,
            response_create_gate=session.response_create_gate,
            penalize_account=False,
        )

    def _classify_http_bridge_stale_gate_holders(
        self: Any,
        pending_states: list[_WebSocketRequestState],
        *,
        now: float,
        threshold_seconds: float,
        session_closed: bool,
    ) -> tuple[list[_WebSocketRequestState], bool]:
        stale_states = [
            state
            for state in pending_states
            if not state.draining_until_terminal
            and self._http_bridge_pending_state_is_stale(
                state,
                now=now,
                threshold_seconds=threshold_seconds,
                session_closed=session_closed,
            )
        ]
        active_states = [
            # A draining request still owns terminal response continuity.  It
            # must keep the session alive while stale holders are cleaned up.
            state
            for state in pending_states
            if state not in stale_states
        ]
        if stale_states and active_states:
            return stale_states, False
        return [], bool(stale_states)

    async def _snapshot_http_bridge_stale_gate_state(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        now: float,
    ) -> _HTTPBridgeStaleGateSnapshot:
        threshold_seconds = float(
            getattr(_service_get_settings(), "http_responses_session_bridge_stuck_gate_retire_after_seconds", 300.0)
        )
        async with session.pending_lock:
            pending_states = list(session.pending_requests)
            stale_request_states, should_retire = self._classify_http_bridge_stale_gate_holders(
                pending_states,
                now=now,
                threshold_seconds=threshold_seconds,
                session_closed=session.closed,
            )
            retry_circuit_request_states = (
                stale_request_states if stale_request_states else (pending_states if should_retire else ())
            )
            return _HTTPBridgeStaleGateSnapshot(
                pending_states=pending_states,
                queued_count=session.queued_request_count,
                threshold_seconds=threshold_seconds,
                stale_request_states=stale_request_states,
                should_retire=should_retire,
                retry_circuit_attempt_selection=(
                    _http_bridge_retry_circuit_attempt_selection_for_pending_requests(retry_circuit_request_states)
                ),
            )

    async def _retire_http_bridge_after_drain_if_ready(self: Any, session: "_HTTPBridgeSession") -> bool:
        if not (session.upstream_control.reconnect_requested and session.upstream_control.retire_after_drain):
            return False
        async with session.pending_lock:
            has_visible_pending = any(
                _http_bridge_request_counts_against_queue(request_state) for request_state in session.pending_requests
            )
            should_reconnect = (
                not has_visible_pending
                and session.queued_request_count == 0
                and session.unanchored_reservation_id is None
                and not session.upstream_close_attempted
            )
            if should_reconnect:
                session.pending_requests.clear()
                session.upstream_close_attempted = True
        if not should_reconnect:
            return False

        await self._close_http_bridge_session_bounded(session, reason="retire_after_drain")
        return True

    async def _retire_stale_pending_http_bridge_session(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        detail: str,
        retry_circuit_detail: str | None = None,
        response_events_seen: int | None = None,
        retired_request_count: int | None = None,
        retry_circuit_attempt_selection: _HTTPBridgeRetryCircuitAttemptSelection | None = None,
    ) -> None:
        async with session.pending_lock:
            retired_request_states = list(session.pending_requests)
            if retired_request_count is None:
                retired_request_count = sum(
                    1
                    for request_state in retired_request_states
                    if _http_bridge_request_counts_against_queue(request_state)
                )
            if response_events_seen is None:
                # Direct retirement must derive event evidence from the same
                # locked ownership snapshot as the pending count. Otherwise an
                # eventful stale-gate owner looks eventless merely because its
                # caller omitted this optional handoff, creating a false
                # circuit strike. Explicit values remain authoritative for
                # reader-failure callers whose pending deque was already
                # drained before entering this shared boundary.
                response_events_seen = max(
                    (
                        max(
                            request_state.response_event_count,
                            int(
                                request_state.response_id is not None
                                or request_state.latency_response_created_ms is not None
                                or request_state.downstream_visible
                            ),
                        )
                        for request_state in retired_request_states
                    ),
                    default=0,
                )
            if retry_circuit_attempt_selection is None:
                retry_circuit_attempt_selection = _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                    retired_request_states
                )
        # Direct retirement (for example the all-stale stuck-gate path, where
        # the wedged reattach is the only pending request) cancels the reader
        # and fails the pendings without passing the partial-cleanup hook or
        # the reader-failure funnel, so evaluate the wedge shape (#1534) here
        # too; recording is idempotent for callers that already quarantined.
        _record_http_bridge_quarantine_wedged_pending(self, session, retired_request_states)
        # This circuit measures failed request lifecycles, not upstream socket
        # churn. ``response_events_seen == 0`` is also true when an idle reader
        # closes with an empty pending deque. Charging that idle close creates a
        # phantom first strike, so one later response-create timeout opens the
        # nominally "repeated" 60-second cooldown and interrupts the client.
        # Keep the ownership proof at this shared retirement boundary unless a
        # caller already claimed and drained the deque. The reader-failure
        # funnel must pass its pre-drain count because terminal notification
        # deliberately empties ``pending_requests`` before retirement. Without
        # that handoff, genuine pre-response failures disappear from circuit
        # accounting while idle closes and request failures look identical.
        if retired_request_count > 0 and response_events_seen == 0:
            consecutive_failures = await self._record_http_bridge_retry_circuit_failure_for_attempt_selection(
                session,
                detail=retry_circuit_detail or detail,
                selection=retry_circuit_attempt_selection,
            )
            poison_detail = _http_bridge_anchor_poison_detail(retry_circuit_detail or detail)
            if (
                poison_detail is not None
                and consecutive_failures is not None
                and consecutive_failures
                >= _service_get_settings().http_responses_session_bridge_anchor_poison_failure_threshold
            ):
                # Consecutive eventless failures on one bridge key are
                # same-anchor failures (the anchor only advances on a
                # completed response, which resets the circuit). Clear the
                # poisoned durable anchor while this session still owns the
                # lease so the next attempt is not re-anchored into the same
                # failure. Without this, only the admission-waiter reader
                # path could ever poison an anchor, and an anchored session
                # failing without waiters cooled down forever (issue #1830).
                durable_cleared = await _abandon_durable_http_bridge_continuity(self, session, detail=poison_detail)
                if not durable_cleared and session.durable_session_id is not None:
                    # Keep failed waiterless clears visible in the same
                    # poison-clear telemetry the admission-waiter path emits;
                    # the next threshold failure re-attempts the clear.
                    _log_http_bridge_event(
                        "durable_anchor_poison_clear_failed",
                        session.key,
                        account_id=session.account.id,
                        model=session.request_model,
                        pending_count=retired_request_count,
                        detail=poison_detail,
                        cache_key_family=session.key.affinity_kind,
                        model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    )
        session.closed = True
        async with self._http_bridge_lock:
            # Bounded close may return while resource finalization is still
            # running. Detachment transfers ownership instead of freeing the
            # capacity slot at canonical removal, and leaves a failed close
            # discoverable by shutdown/account invalidation for a later retry.
            self._detach_http_bridge_session_locked(session.key, expected_session=session)
        async with session.pending_lock:
            should_close = not session.upstream_close_attempted
            if should_close:
                session.upstream_close_attempted = True
        if should_close:
            await self._close_http_bridge_session_bounded(session, reason="retire_stale_pending")
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

    async def _retry_http_bridge_request_on_fresh_upstream(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        send_request: bool = True,
        require_same_account: bool = False,
    ) -> bool:
        require_same_account = require_same_account or is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
        retry_text_data = text_data
        using_fresh_replay = False
        if request_state.previous_response_id is not None and send_request:
            # After an ambiguous websocket send failure we cannot prove whether
            # upstream already accepted the continuation. Re-sending the same
            # previous_response_id request can fork continuity with duplicate
            # child responses, so only reconnect-without-resend is allowed.
            # The single exception is a proof-gated, trim-safe full-resend
            # payload: dropping the anchor and replaying the original
            # unanchored request is equivalent to the client's own retry.
            # The proof is independent of where the anchor came from; a
            # client-provided full resend is as safe as a durable injection.
            # Session-level follow-ups do not opt in because their context may
            # depend on the anchor.
            if not request_state.fresh_upstream_request_text or not request_state.fresh_upstream_request_is_retry_safe:
                return False
            retry_text_data = request_state.fresh_upstream_request_text
            using_fresh_replay = True
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
                require_same_account=require_same_account,
                require_preferred_account=request_state.file_required_preferred_account,
            )
            if send_request:
                retry_text_data = self._http_bridge_text_with_account_installation_id(
                    session,
                    request_state,
                    retry_text_data,
                )
                if using_fresh_replay:
                    request_state.previous_response_id = None
                    request_state.proxy_injected_previous_response_id = False
                    request_state.request_text = retry_text_data
                await _send_http_bridge_request_text_with_archive_id(session, request_state, retry_text_data)
            _clear_websocket_request_error_overrides(request_state)
            session.last_used_at = _service_time().monotonic()
            return True
        except UpstreamWebSocketTransportError:
            # The new socket may have accepted response.create. Let the reader
            # owner retire the whole session with the typed, non-replayable
            # failure instead of falling back to the earlier close reason.
            raise
        except ProxyResponseError as exc:
            if _http_bridge_is_previous_response_owner_unavailable(exc):
                raise
            logger.warning("HTTP bridge retry on fresh upstream failed", exc_info=True)
            return False
        except Exception:
            logger.warning("HTTP bridge retry on fresh upstream failed", exc_info=True)
            return False

    async def _retry_http_bridge_precreated_request(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState | None = None,
        restart_reader: bool = False,
    ) -> bool:
        clean_close_retry_max_count = self._http_bridge_clean_close_retry_max_count()
        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )

        def request_is_retryable(request_state: _WebSocketRequestState) -> bool:
            transport_only_unanchored_replay = bool(
                request_state.precreated_replay_reason is None
                and (
                    request_state.verified_stale_anchor_replay
                    or (
                        request_state.previous_response_id is not None
                        and not request_state.proxy_injected_previous_response_id
                        and request_state.fresh_upstream_request_is_retry_safe
                        and request_state.fresh_upstream_request_text
                    )
                )
            )
            if transport_only_unanchored_replay:
                return False
            if _websocket_request_can_replay_before_visible_output(request_state):
                return True
            if (
                clean_close_retry_max_count <= 0
                or request_state.verified_stale_anchor_replay
                or request_state.replay_count != 1
                or request_state.response_event_count != 0
                or request_state.clean_close_replay_count >= clean_close_retry_max_count
                or _classify_upstream_close(
                    session.last_upstream_close_code,
                    response_events_seen=request_state.response_event_count,
                )
                != "clean"
            ):
                return False
            return _websocket_request_can_replay_before_visible_output(
                request_state,
                allow_clean_close_retry=True,
            )

        fresh_hard_request_account_switch_candidate = False
        proof_gated_continuity_replay_candidate = False
        if session.key.strength == "hard":
            async with session.pending_lock:
                retryable_candidates = [
                    request_state
                    for request_state in session.pending_requests
                    if not request_state.draining_until_terminal and request_is_retryable(request_state)
                ]
                if len(retryable_candidates) == 1:
                    candidate = retryable_candidates[0]
                    fresh_hard_request_account_switch_candidate = (
                        candidate.previous_response_id is None
                        and not candidate.hard_continuity_anchor
                        and not candidate.proxy_injected_previous_response_id
                        and not candidate.file_required_preferred_account
                        and candidate.response_event_count == 0
                        and candidate.replay_count == 0
                    )
                    proof_gated_continuity_replay_candidate = (
                        candidate.previous_response_id is not None
                        and candidate.fresh_upstream_request_is_retry_safe
                        and bool(candidate.fresh_upstream_request_text)
                        and candidate.response_event_count == 0
                        and candidate.replay_count == 0
                    )
        if not await self._http_bridge_precreated_retry_allowed(
            session,
            allow_fresh_hard_account_switch=fresh_hard_request_account_switch_candidate,
            allow_proof_gated_continuity_replay=proof_gated_continuity_replay_candidate,
        ):
            return False

        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
        hard_owner_bound = _http_bridge_key_strength(session.key) == "hard"
        async with session.pending_lock:
            if request_state is not None:
                if (
                    request_state not in session.pending_requests
                    or any(pending_request is not request_state for pending_request in session.pending_requests)
                    or request_state.draining_until_terminal
                    or not _http_bridge_request_counts_against_queue(request_state)
                    or not request_is_retryable(request_state)
                ):
                    return False
            else:
                retryable_requests = [
                    request_state
                    for request_state in session.pending_requests
                    if not request_state.draining_until_terminal and request_is_retryable(request_state)
                ]
                if len(retryable_requests) != 1:
                    return False
                request_state = retryable_requests[0]
            model_fallback_replay = request_state.precreated_replay_reason == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
            if request_state.previous_response_id is not None and not (
                request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text
            ):
                # Once a continuation is pending upstream, reconnecting without
                # replay cannot complete the current request, while replaying it
                # is unsafe without upstream idempotency guarantees. Proxy-
                # injected anchors and proof-gated client full resends are
                # equivalent to the client's own retry once the anchor is
                # stripped. The latter remains pinned to the current owner.
                return False
            close_classification = _classify_upstream_close(
                session.last_upstream_close_code,
                response_events_seen=request_state.response_event_count,
            )
            close_generation = session.last_upstream_close_generation
            hard_session_affinity = session.key.strength == "hard"
            fresh_hard_request_account_switch_allowed = (
                hard_session_affinity
                and request_state.previous_response_id is None
                and not request_state.hard_continuity_anchor
                and not request_state.proxy_injected_previous_response_id
                and not request_state.file_required_preferred_account
            )
            clean_close_hard_continuation = (
                close_classification == "clean"
                and hard_session_affinity
                and request_state.previous_response_id is not None
            )
            clean_close_hard_continuity_anchor = (
                close_classification == "clean" and hard_session_affinity and request_state.hard_continuity_anchor
            )
            clean_close_retry_for_current_close = (
                close_classification == "clean"
                and request_state.clean_close_retry_close_generation != close_generation
                and not request_state.clean_close_retry_in_progress
            )
            additional_clean_close_retry = (
                clean_close_retry_for_current_close
                and request_state.replay_count == 1
                and request_state.response_event_count == 0
                and request_state.clean_close_replay_count < clean_close_retry_max_count
            )
            if request_state.replay_count >= 1 and not additional_clean_close_retry:
                return False
            account_bound_replay = False
            if request_state.previous_response_id is not None:
                require_preferred_reconnect = False
                if account_neutral_recovery:
                    request_state.preferred_account_id = session.account.id
                    switch_text = None
                else:
                    switch_text = _prepare_websocket_request_state_for_account_switch(request_state)
                if switch_text is None:
                    # The retained full body may be retry-safe for continuity
                    # while still naming an account-scoped uploaded file.  In
                    # that case retry on the same owner-bound anchor instead of
                    # letting visible-output replay strip the anchor and migrate.
                    fresh_retry_safe = request_state.fresh_upstream_request_is_retry_safe
                    request_state.fresh_upstream_request_is_retry_safe = False
                    try:
                        request_text = _prepare_websocket_request_state_for_visible_output_replay(request_state)
                    finally:
                        request_state.fresh_upstream_request_is_retry_safe = fresh_retry_safe
                    if request_text is None:
                        return False
                    require_preferred_reconnect = request_state.preferred_account_id is not None
                else:
                    request_text = _prepare_websocket_request_state_for_visible_output_replay(request_state)
                    if request_text is None:
                        return False
                    if not hard_owner_bound:
                        request_state.excluded_account_ids.add(session.account.id)
            else:
                # Account-scoped uploaded files cannot be replayed on a
                # different owner. Keep the preferred account mandatory for
                # both silent recovery and clean-close recovery.
                candidate_text = (
                    request_state.fresh_upstream_request_text
                    if request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text
                    else request_state.request_text
                )
                # The send boundary decorates durable operations with
                # codex_lb_operation_id after selection. Keep that operation
                # identity on its owner unless a dedicated rebind path has
                # already replaced the operation ID.
                candidate_portable = request_state.operation_id is None and (
                    _websocket_request_text_is_account_neutral_fresh_replay(candidate_text)
                )
                request_text = _prepare_websocket_request_state_for_visible_output_replay(request_state)
                if request_text is None or request_text != candidate_text:
                    return False
                account_bound_replay = not candidate_portable
                require_preferred_reconnect = (
                    account_neutral_recovery or account_bound_replay or request_state.file_required_preferred_account
                )
                if account_neutral_recovery or account_bound_replay:
                    request_state.preferred_account_id = session.account.id
                elif not request_state.file_required_preferred_account:
                    if hard_owner_bound and not model_fallback_replay and not fresh_hard_request_account_switch_allowed:
                        request_state.preferred_account_id = session.account.id
                    else:
                        request_state.preferred_account_id = None
                        request_state.excluded_account_ids.add(session.account.id)
            if session.account.id in request_state.excluded_account_ids:
                session.upstream_turn_state = None
                session.downstream_turn_state = None
                session.headers = {
                    key: value for key, value in session.headers.items() if key.lower() != "x-codex-turn-state"
                }
            if close_classification == "clean":
                if not clean_close_retry_for_current_close:
                    return False
                request_state.clean_close_retry_in_progress = True
                request_state.clean_close_retry_result = None
                request_state.clean_close_retry_close_generation = close_generation
            if additional_clean_close_retry:
                request_state.clean_close_replay_count += 1
        retry_jitter_seconds = (
            self._http_bridge_clean_close_retry_jitter_seconds() if additional_clean_close_retry else 0.0
        )
        retry_event = "retry_precreated_clean_close" if additional_clean_close_retry else "retry_precreated"
        _log_http_bridge_event(
            retry_event,
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        reconnect_reader_kwargs = {"restart_reader": True} if restart_reader else {}
        try:
            if retry_jitter_seconds > 0:
                logger.info(
                    "HTTP bridge clean-close retry jitter request_id=%s sleep_seconds=%.3f",
                    request_state.request_id,
                    retry_jitter_seconds,
                )
                await asyncio.sleep(retry_jitter_seconds)
                request_deadline = request_state.bridge_request_deadline
                if request_deadline is None:
                    request_deadline = request_state.started_at + _http_bridge_request_budget_seconds(
                        _service_get_settings()
                    )
                now_monotonic = _service_time().monotonic()
                async with session.pending_lock:
                    request_still_owned = (
                        request_state in session.pending_requests and not request_state.draining_until_terminal
                    )
                if not request_still_owned or now_monotonic >= request_deadline:
                    logger.info(
                        "HTTP bridge clean-close retry abandoned after jitter request_id=%s "
                        "still_owned=%s deadline_expired=%s",
                        request_state.request_id,
                        request_still_owned,
                        now_monotonic >= request_deadline,
                    )
                    request_state.clean_close_retry_result = False
                    return False
            # A fresh hard-session replay may select a replacement account.
            # The admission lease is account-scoped, so release the old
            # account's lease before reconnecting; the post-reconnect path
            # below acquires a lease for the account actually selected.
            if fresh_hard_request_account_switch_allowed:
                await self._release_request_state_account_response_create_lease(request_state)
            if hard_owner_bound and not model_fallback_replay and not fresh_hard_request_account_switch_allowed:
                await self._reconnect_http_bridge_session(
                    session,
                    request_state=request_state,
                    require_same_account=True,
                    **reconnect_reader_kwargs,
                )
            elif require_preferred_reconnect:
                await self._reconnect_http_bridge_session(
                    session,
                    request_state=request_state,
                    require_same_account=account_neutral_recovery or account_bound_replay,
                    require_preferred_account=True,
                    **reconnect_reader_kwargs,
                )
            elif clean_close_hard_continuation or clean_close_hard_continuity_anchor:
                await self._reconnect_http_bridge_session(
                    session,
                    request_state=request_state,
                    # Continuity anchors (previous_response_id and turn-state)
                    # are account-bound. Do not migrate them while recovering a
                    # clean handoff close.
                    require_same_account=True,
                    **reconnect_reader_kwargs,
                )
            else:
                await self._reconnect_http_bridge_session(
                    session,
                    request_state=request_state,
                    **reconnect_reader_kwargs,
                )
            if request_state.account_response_create_lease is None:
                current_settings = await _service_get_settings_cache().get()
                request_state.account_response_create_lease = (
                    await self._acquire_account_response_create_lease_or_overload(
                        account_id=session.account.id,
                        request_id=request_state.request_log_id or request_state.request_id,
                        surface="http_bridge",
                        concurrency_caps=effective_account_concurrency_caps(current_settings),
                    )
                )
                request_state.account_response_create_release = self._load_balancer.release_account_lease
            enforce_capacity_retry_deadline = request_state.response_create_admission_reacquire_required
            if enforce_capacity_retry_deadline:
                retry_deadline = request_state.bridge_request_deadline
                if retry_deadline is None:
                    retry_deadline = request_state.started_at + _http_bridge_request_budget_seconds(
                        _service_get_settings()
                    )
                remaining_retry_budget_seconds = retry_deadline - _service_time().monotonic()
                if remaining_retry_budget_seconds <= 0:
                    request_state.response_create_admission_reacquire_required = False
                    await self._release_request_state_account_response_create_lease(request_state)
                    return False
                try:
                    request_state.response_create_admission = await asyncio.wait_for(
                        self._get_work_admission().acquire_response_create(),
                        timeout=remaining_retry_budget_seconds,
                    )
                except TimeoutError:
                    request_state.response_create_admission_reacquire_required = False
                    await self._release_request_state_account_response_create_lease(request_state)
                    return False
                request_state.response_create_admission_reacquire_required = False
                if _service_time().monotonic() >= retry_deadline:
                    request_state.response_create_admission.release()
                    request_state.response_create_admission = None
                    await self._release_request_state_account_response_create_lease(request_state)
                    return False
                async with session.pending_lock:
                    retry_consumer_attached = (
                        request_state in session.pending_requests
                        and request_state.event_queue is not None
                        and not request_state.draining_until_terminal
                    )
                if not retry_consumer_attached:
                    request_state.response_create_admission.release()
                    request_state.response_create_admission = None
                    await self._release_request_state_account_response_create_lease(request_state)
                    return False
            request_text = self._http_bridge_text_with_account_installation_id(session, request_state, request_text)
            await _send_http_bridge_request_text_with_archive_id(session, request_state, request_text)
            session.last_used_at = _service_time().monotonic()
            request_state.clean_close_retry_result = True
            return True
        except asyncio.CancelledError:
            request_state.clean_close_retry_result = False
            raise
        except UpstreamWebSocketTransportError:
            request_state.clean_close_retry_result = False
            raise
        except Exception as exc:
            request_state.clean_close_retry_result = False
            (
                request_state.error_http_status_override,
                request_state.error_code_override,
                request_state.error_message_override,
                request_state.error_type_override,
                request_state.error_param_override,
            ) = _http_bridge_precreated_retry_failure_error(exc)
            if isinstance(exc, ProxyResponseError):
                logger.info(
                    "HTTP bridge pre-created retry failed with terminal proxy error code=%s message=%s",
                    request_state.error_code_override,
                    request_state.error_message_override,
                )
            else:
                logger.warning("HTTP bridge pre-created retry failed", exc_info=True)
            return False
        finally:
            request_state.clean_close_retry_in_progress = False

    async def _retry_http_bridge_precreated_auth_request(
        self: Any,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
        *,
        error_message: str | None,
    ) -> Literal["not_replayable", "retried", "failed"]:
        permanent_failure_code = _websocket_auth_failure_permanent_code(error_message)
        bound_to_current_account = request_state.replay_required_account_id == session.account.id
        if bound_to_current_account and (
            _websocket_auth_failure_requires_reauth(error_message)
            or request_state.auth_replay_counts_by_account.get(session.account.id, 0) > 0
        ):
            failure_code = permanent_failure_code or _WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE
            await self._load_balancer.mark_permanent_failure(session.account, failure_code)
            setattr(request_state, "account_health_error_handled", True)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(session.account.id)
            return "not_replayable"
        request_text = _prepare_websocket_request_state_for_auth_replay(
            request_state,
            current_account_id=session.account.id,
        )
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
            if is_http_bridge_account_neutral_replay(
                kind=session.key.affinity_kind,
                key=session.key.affinity_key,
            ):
                setattr(request_state, "account_health_error_handled", True)
                return "not_replayable"

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
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                require_same_account=(
                    bound_to_current_account
                    or is_http_bridge_account_neutral_replay(
                        kind=session.key.affinity_kind,
                        key=session.key.affinity_key,
                    )
                ),
                require_preferred_account=bound_to_current_account,
            )
            request_text = self._http_bridge_text_with_account_installation_id(session, request_state, request_text)
            await _send_http_bridge_request_text_with_archive_id(session, request_state, request_text)
            session.last_used_at = _service_time().monotonic()
            return "retried"
        except UpstreamWebSocketTransportError:
            raise
        except Exception as exc:
            (
                request_state.error_http_status_override,
                request_state.error_code_override,
                request_state.error_message_override,
                request_state.error_type_override,
                request_state.error_param_override,
            ) = _http_bridge_precreated_retry_failure_error(exc)
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
        self: Any,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
    ) -> bool:
        if session.account.security_work_authorized:
            return False
        if request_state.response_id is not None:
            return False
        if request_state.replay_count >= 1:
            return False
        if is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        ):
            return False
        if request_state.file_required_preferred_account:
            return False
        if not _websocket_request_can_replay_before_visible_output(request_state):
            return False
        retry_text = _prepare_websocket_request_state_for_account_switch(request_state)
        if retry_text is None:
            return False

        owner_account_id = session.account.id
        previous_replay_count = request_state.replay_count
        previous_response_id = request_state.response_id
        previous_response_event_count = request_state.response_event_count
        previous_upstream_model_output_seen = request_state.upstream_model_output_seen
        previous_affinity_policy = request_state.affinity_policy
        previous_session_affinity = session.affinity
        previous_session_codex_session = session.codex_session
        previous_session_upstream_turn_state = session.upstream_turn_state
        previous_session_downstream_turn_state = session.downstream_turn_state
        previous_session_turn_state_aliases = set(session.downstream_turn_state_aliases)
        previous_session_turn_state_alias_registration_generations = dict(
            session.turn_state_alias_registration_generations
        )
        previous_session_headers = session.headers
        request_state.preferred_account_id = None
        request_state.excluded_account_ids.add(owner_account_id)
        request_state.affinity_policy = replace(
            request_state.affinity_policy,
            key=None,
            kind=None,
            reallocate_sticky=True,
        )
        replacement_session_affinity = replace(
            session.affinity,
            key=None,
            kind=None,
            reallocate_sticky=True,
            codex_session_source=None,
        )

        request_state.replay_count += 1
        request_state.response_id = None
        request_state.response_event_count = 0
        request_state.upstream_model_output_seen = False
        request_state.deferred_reasoning_downstream_texts = []
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
        reconnected = False
        operation_rebound_for_retry = False
        security_retry_send_started = False

        def mark_security_retry_send_started() -> None:
            nonlocal security_retry_send_started
            security_retry_send_started = True

        try:
            request_state.precreated_replay_account_id = session.account.id
            await self._release_request_state_account_response_create_lease(request_state)
            await _call_with_supported_optional_kwargs(
                self._reconnect_http_bridge_session,
                session,
                optional_kwargs={
                    "owner_rebind_affinity": previous_session_affinity,
                    "selection_affinity": replacement_session_affinity,
                },
                request_state=request_state,
                require_security_work_authorized=True,
            )
            reconnected = True
            settings = await _service_get_settings_cache().get()
            request_state.account_response_create_lease = await self._acquire_account_response_create_lease_or_overload(
                account_id=session.account.id,
                request_id=request_state.request_id,
                surface="http_bridge_security_retry",
                concurrency_caps=effective_account_concurrency_caps(settings),
            )
            request_state.account_response_create_release = self._load_balancer.release_account_lease
            if session.account.id != owner_account_id:
                if (
                    previous_session_affinity.codex_session_source in {"session_header", "thread_header"}
                    and previous_session_affinity.selection_key is not None
                    and previous_session_affinity.kind is not None
                ):
                    async with self._repo_factory() as repos:
                        await repos.sticky_sessions.upsert(
                            previous_session_affinity.selection_key,
                            session.account.id,
                            kind=previous_session_affinity.kind,
                        )
            if (
                request_state.operation_registered
                and request_state.operation_id is not None
                and request_state.operation_fingerprint is not None
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
            ):
                record_operation = getattr(self._durable_bridge, "record_operation", None)
                if not callable(record_operation):
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "Security-work recovery operation could not be re-fenced; retry the request.",
                        ),
                    )
                rebound_operation = await record_operation(
                    operation_id=request_state.operation_id,
                    session_id=session.durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    request_fingerprint=request_state.operation_fingerprint,
                    api_key_scope=durable_bridge_api_key_scope(session.key.api_key_id),
                    account_id=session.account.id,
                    model=request_state.model,
                    parent_response_id=request_state.operation_parent_response_id or request_state.previous_response_id,
                )
                if rebound_operation is None or getattr(rebound_operation, "state", None) != "submitted":
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "bridge_continuity_persistence_failed",
                            "Security-work recovery operation could not be re-fenced; retry the request.",
                        ),
                    )
                operation_rebound_for_retry = True
            retry_text = self._http_bridge_text_with_account_installation_id(session, request_state, retry_text)
            await _send_http_bridge_request_text_with_archive_id(
                session,
                request_state,
                retry_text,
                on_send_started=mark_security_retry_send_started,
            )
            session.last_used_at = _service_time().monotonic()
            return True
        except UpstreamWebSocketTransportError:
            raise
        except Exception as exc:
            logger.warning("HTTP bridge security-work retry failed", exc_info=True)
            if (
                operation_rebound_for_retry
                and not security_retry_send_started
                and request_state.operation_id is not None
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
            ):
                update_operation = getattr(self._durable_bridge, "update_operation", None)
                if callable(update_operation):
                    try:
                        restored = await update_operation(
                            operation_id=request_state.operation_id,
                            session_id=session.durable_session_id,
                            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                            owner_epoch=session.durable_owner_epoch,
                            state="failed",
                        )
                        if not restored:
                            logger.info(
                                "HTTP bridge security retry failed to restore operation fence operation_id=%s",
                                request_state.operation_id,
                            )
                    except Exception:
                        logger.warning(
                            "Failed to restore HTTP bridge security retry operation operation_id=%s",
                            request_state.operation_id,
                            exc_info=True,
                        )
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
            if reconnected:
                # Ownership and the replacement socket were already swapped,
                # but response.create never reached that socket.  Retire the
                # replacement instead of leaving a live bridge with aliases
                # rebound to a turn that was never submitted.
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                await self._retire_http_bridge_after_drain_if_ready(session)
            else:
                request_state.replay_count = previous_replay_count
                request_state.response_id = previous_response_id
                request_state.response_event_count = previous_response_event_count
                request_state.upstream_model_output_seen = previous_upstream_model_output_seen
                request_state.deferred_reasoning_downstream_texts = []
                request_state.affinity_policy = previous_affinity_policy
                session.affinity = previous_session_affinity
                session.codex_session = previous_session_codex_session
                session.upstream_turn_state = previous_session_upstream_turn_state
                session.downstream_turn_state = previous_session_downstream_turn_state
                async with self._http_bridge_lock:
                    session.downstream_turn_state_aliases.update(previous_session_turn_state_aliases)
                    session.turn_state_alias_registration_generations.update(
                        previous_session_turn_state_alias_registration_generations
                    )
                    _register_http_bridge_turn_state_aliases_locked(self, session)
                session.headers = previous_session_headers
            return False

    async def _claim_http_bridge_replacement_before_swap(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        account_id: str,
        upstream: Any,
        release_selected_account_lease: Any,
        owner_rebind_affinity: _AffinityPolicy,
    ) -> None:
        if account_id == session.account.id:
            return
        try:
            if owner_rebind_affinity.legacy_selection_key is not None:
                async with self._repo_factory() as repos:
                    # A goal restart abandons only session-header interpretation
                    # of the legacy raw row. Preserve that typed capability here:
                    # omitting it would resurrect the retained turn-state owner
                    # during a later security-authorized replacement.
                    legacy_owner_id = await repos.sticky_sessions.get_account_id(
                        owner_rebind_affinity.legacy_selection_key,
                        # The new thread row may be PROMPT_CACHE, but the raw
                        # compatibility row has always been CODEX_SESSION and
                        # remains durable hard ownership.
                        kind=StickySessionKind.CODEX_SESSION,
                        max_age_seconds=None,
                        continuity_source=(owner_rebind_affinity.legacy_continuity_source or "session_header"),
                    )
                if legacy_owner_id is not None and legacy_owner_id != account_id:
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "continuity_owner_conflict",
                            "Security retry conflicts with a legacy continuity owner.",
                            error_type="server_error",
                        ),
                    )
            if session.durable_session_id is not None:
                await _call_with_supported_optional_kwargs(
                    self._claim_durable_http_bridge_session,
                    session,
                    optional_kwargs={
                        "claim_account_id": account_id,
                        "clear_latest_turn_state": True,
                    },
                    allow_takeover=True,
                    force_owner_epoch_advance=True,
                )
        except BaseException:
            try:
                await upstream.close()
            except Exception:
                logger.debug("Failed to close unclaimed HTTP bridge replacement websocket", exc_info=True)
            await release_selected_account_lease()
            raise
