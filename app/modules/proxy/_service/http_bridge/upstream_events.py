from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

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
from app.core.openai.parsing import parse_sse_event
from app.core.utils.sse import format_sse_event, parse_sse_data_json
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
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
    _normalize_http_bridge_error_event,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _assign_websocket_response_id,
    _build_stream_incomplete_terminal_event_for_request,
    _find_websocket_request_state_by_response_id,
    _http_error_status_from_payload,
    _is_missing_tool_output_error,
    _is_previous_response_not_found_error,
    _is_security_work_authorization_required_error,
    _match_websocket_request_state_for_anonymous_event,
    _matching_websocket_request_states_for_missing_tool_output_error,
    _matching_websocket_request_states_for_previous_response_error,
    _maybe_rewrite_websocket_previous_response_not_found_event,
    _pop_matching_websocket_request_states,
    _pop_terminal_websocket_request_state,
    _previous_response_id_from_not_found_message,
    _release_websocket_response_create_gate,
    _response_output_item_done_function_call_id,
    _rewrite_websocket_continuity_corruption_event,
    _rewrite_websocket_downstream_response_id,
    _rewrite_websocket_previous_response_owner_unavailable_event,
    _rewrite_websocket_suppressed_duplicate_tool_call_completion_event,
    _security_work_advisory_event,
    _service_get_settings,
    _service_tier_from_event_payload,
    _upstream_websocket_disconnect_message,
    _websocket_event_error_code,
    _websocket_event_error_message,
    _websocket_event_error_param,
    _websocket_event_error_type,
    _websocket_owner_pinned_quota_error_code,
    _websocket_precreated_auth_error_code,
    _websocket_precreated_retry_error_code,
    _websocket_response_id,
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
    _event_type_from_payload,
    _HTTPBridgeSession,
    _record_response_event,
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
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
)
from app.modules.proxy.tool_call_dedupe import (
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
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100


class _HTTPBridgeUpstreamEventsMixin:
    async def _relay_http_bridge_upstream_messages(
        self: Any,
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

    async def _process_http_bridge_upstream_text(
        self: Any,
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
