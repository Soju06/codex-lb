from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Literal, Mapping, TypeVar, cast
from uuid import uuid4

import anyio

from app.core import shutdown as shutdown_state
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
)
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
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
    _build_http_bridge_prewarm_text,
    _http_bridge_key_strength,
    _http_bridge_precreated_retry_failure_error,
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _classify_upstream_close,
    _count_external_image_urls,
    _enforce_response_create_size_limit,
    _fingerprint_input_items,
    _inline_top_level_input_image_urls,
    _normalize_service_tier_value,
    _normalize_session_id,
    _prepare_websocket_request_state_for_auth_replay,
    _prepare_websocket_request_state_for_visible_output_replay,
    _prewarm_response_timeout_seconds,
    _release_websocket_response_create_gate,
    _response_create_client_metadata,
    _security_work_advisory_event,
    _service_as_image_fetch_session,
    _service_get_settings,
    _service_inline_input_image_urls,
    _service_lease_http_session,
    _service_time,
    _slim_response_create_payload_for_upstream,
    _upstream_response_create_max_bytes,
    _websocket_auth_failure_permanent_code,
    _websocket_auth_failure_requires_reauth,
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
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_request_error_overrides,
    _copy_websocket_route_metadata_from_session,
    _event_type_from_payload,
    _HTTPBridgeSession,
    _request_log_useragent_fields,
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
    _extract_model_class,
    _owner_lookup_session_id_from_headers,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import (
    _normalize_error_code,
    _parse_openai_error,
)
from app.modules.proxy.tool_call_dedupe import (
    dedupe_replayed_side_effect_input_items,
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


class _HTTPBridgeRequestSubmitMixin:
    def _prepare_http_bridge_request(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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

    async def _retire_http_bridge_after_drain_if_ready(self: Any, session: "_HTTPBridgeSession") -> bool:
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
        self: Any,
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

    async def _retry_http_bridge_request_on_fresh_upstream(
        self: Any,
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

    async def _retry_http_bridge_precreated_request(self: Any, session: "_HTTPBridgeSession") -> bool:
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
        self: Any,
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
