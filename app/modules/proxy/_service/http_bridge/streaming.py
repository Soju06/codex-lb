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
    openai_error,
    response_failed_event,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
)
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import (
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
    _effective_http_bridge_idle_ttl_seconds,
    _http_bridge_is_context_overflow_error,
    _http_bridge_is_previous_response_owner_unavailable,
    _http_bridge_payload_looks_like_full_resend,
    _http_bridge_payload_without_previous_response_id,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_stage,
    _http_bridge_runtime_config,
    _http_bridge_should_attempt_local_bootstrap_rebind,
    _http_bridge_should_attempt_local_previous_response_recovery,
    _http_bridge_should_attempt_soft_affinity_reroute,
    _http_bridge_should_rollover_after_context_overflow,
    _log_http_bridge_event,
    _make_http_bridge_session_key,
    _record_bridge_reattach,
    _trim_http_bridge_previous_response_input_items,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _build_rewritten_stream_response_failed_event,
    _codex_keepalive_frame,
    _fingerprint_input_items,
    _header_value_case_insensitive,
    _http_bridge_startup_keepalive_grace_seconds,
    _input_prefix_matches_stored_context,
    _is_previous_response_not_found_error,
    _maybe_log_proxy_request_payload,
    _maybe_log_proxy_request_shape,
    _normalize_service_tier_value,
    _normalize_session_id,
    _openai_error_envelope_from_response_failed_payload,
    _partial_output_proxy_error_event_block,
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
    _event_type_from_payload,
    _HTTPBridgeOwnerForward,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
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
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import (
    _normalize_error_code,
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
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100


def _proxy_error_code_message(exc: ProxyResponseError) -> tuple[str | None, str | None]:
    error = exc.payload.get("error") if isinstance(exc.payload, dict) else None
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return (str(code) if code is not None else None, str(message) if message is not None else None)


def _http_bridge_account_capacity_wait_seconds(exc: ProxyResponseError) -> float | None:
    code, message = _proxy_error_code_message(exc)
    if code in {"account_response_create_cap", "account_stream_cap", "capacity_exhausted_active_sessions"}:
        return None
    return _account_selection_recovery_sleep_seconds_from_message(message)


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
    _code, message = _proxy_error_code_message(exc)
    return min(account_capacity_wait_seconds, remaining_budget_seconds), account_capacity_wait_seconds, message


async def _iter_account_capacity_wait_sse(
    *,
    request_id: str,
    reason: str | None,
    sleep_seconds: float,
) -> AsyncIterator[str]:
    wait_started_at = _service_time().monotonic()
    remaining_sleep_seconds = sleep_seconds
    while remaining_sleep_seconds > 0:
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


class _HTTPBridgeStreamingMixin:
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
        request_state.affinity_policy = affinity
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
        settings = _service_get_settings()
        request_deadline = request_state.started_at + _http_bridge_request_budget_seconds(settings)
        while True:
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
                    request_deadline=request_deadline,
                )
            except ProxyResponseError as exc:
                if not (
                    _http_bridge_is_previous_response_owner_unavailable(exc)
                    and proxy_injected_previous_response_id
                    and fresh_upstream_request_text is not None
                    and durable_full_resend_anchor_count is not None
                    and durable_full_resend_anchor_fingerprint is not None
                ):
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
                request_state, text_data = self._prepare_http_bridge_request(
                    payload,
                    headers,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                request_state.affinity_policy = affinity
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
                durable_lookup = None
                continue
            break
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
                while True:
                    try:
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
                            request_deadline=request_deadline,
                        )
                    except ProxyResponseError as capacity_exc:
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
                        ):
                            yield line
                        if _service_time().monotonic() >= request_deadline:
                            raise
                        continue
                    break
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
                    assert retry_request_state is not None
                    retry_request_state.affinity_policy = affinity
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
            request_state.affinity_policy = affinity
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
                request_state.affinity_policy = affinity
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
                while True:
                    try:
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
                            request_deadline=request_deadline,
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

            while True:
                try:
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
                        request_deadline=request_deadline,
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
                    ):
                        yield line
                    if _service_time().monotonic() >= request_deadline:
                        raise
                    continue
                break
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
        self: Any,
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
        self: Any,
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
                        if request_state.account_capacity_waiting:
                            keepalive_count = 0
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
