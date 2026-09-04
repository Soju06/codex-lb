from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from typing import Any, TypeVar, cast

import anyio

from app.core.balancer.types import UpstreamError
from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.proxy import CodexControlResponse as CodexControlResponse
from app.core.clients.proxy import (  # noqa: F401
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
from app.core.clients.proxy_websocket import (
    UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
    UpstreamWebSocketMessage,
    UpstreamWebSocketTransportError,
    is_account_neutral_websocket_error_code,
)
from app.core.errors import response_failed_event
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import (
    _LIFECYCLE_EVENT_TYPES,
    classify_event_type,
    parse_sse_event_payload,
)
from app.core.types import JsonValue
from app.core.usage.live_hub import publish_live_usage
from app.core.usage.live_snapshots import EVENT_MARKER, parse_rate_limit_event_text
from app.core.utils.request_id import reset_request_id, set_request_id
from app.core.utils.sse import format_sse_event, format_sse_event_from_text, parse_sse_data_json_text
from app.db.models import Account
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
    _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
    _await_task_deferring_cancellation,
    _forget_http_bridge_denied_anchor_fence,
    _http_bridge_abandonment_may_settle_circuit,
    _http_bridge_denied_anchor_fence_current_map,
    _http_bridge_denied_anchor_fence_entry,
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_event_proves_upstream_liveness,
    _http_bridge_eventless_precreated_deadline,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_counts_against_queue,
    _http_bridge_request_state_holds_safe_replay,
    _http_bridge_retry_circuit_attempt_selection_for_pending_requests,
    _log_http_bridge_event,
    _normalize_http_bridge_error_event,
    _record_http_bridge_denied_anchor_fence,
    _record_http_bridge_stuck_retire,
    _record_http_bridge_unmatched_upstream_liveness,
    _schedule_http_bridge_background_cleanup,
)
from app.modules.proxy._service.http_bridge.quarantine import (
    _clear_http_bridge_quarantine,
    _http_bridge_quarantine_clear_fence,
    _record_http_bridge_quarantine_eventless_timeout,
    _record_http_bridge_quarantine_wedged_pending,
)
from app.modules.proxy._service.http_bridge.retry_circuit import (
    _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL,
    _POISON_ANCHOR_CAPTURE_UNAVAILABLE,
    _http_bridge_anchor_poison_detail,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _assign_websocket_response_id,
    _await_cancelled_task,
    _build_stream_incomplete_terminal_event_for_request,
    _classify_upstream_close,
    _find_websocket_request_state_by_response_id,
    _http_error_status_from_payload,
    _is_account_neutral_transport_drop,
    _is_missing_tool_output_error,
    _is_previous_response_not_found_error,
    _is_security_work_authorization_required_error,
    _match_websocket_request_state_for_anonymous_event,
    _matching_websocket_request_states_for_missing_tool_output_error,
    _matching_websocket_request_states_for_previous_response_error,
    _maybe_rewrite_websocket_previous_response_not_found_event,
    _pop_matching_websocket_request_states,
    _pop_terminal_websocket_request_state,
    _prepare_websocket_request_state_for_account_switch,
    _previous_response_id_from_not_found_message,
    _release_websocket_response_create_gate,
    _response_output_item_done_tool_call,
    _rewrite_websocket_continuity_corruption_event,
    _rewrite_websocket_downstream_response_id,
    _rewrite_websocket_previous_response_owner_unavailable_event,
    _rewrite_websocket_suppressed_duplicate_tool_call_completion_event,
    _security_work_advisory_event,
    _service_get_settings,
    _service_tier_from_event_payload,
    _service_time,
    _upstream_websocket_disconnect_message,
    _websocket_auth_request_can_switch_account,
    _websocket_downstream_response_id,
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
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS,
    _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _PENDING_TOOL_CALL_ITEM_TYPES,
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _account_capacity_wait_payload,
    _clear_websocket_deferred_reasoning_downstream_texts,
    _clear_websocket_precreated_replay_fallback,
    _clear_websocket_request_error_overrides,
    _DeferredKeyedStreamHealthPenalty,
    _HTTPBridgeCompletedDeliveryScope,
    _HTTPBridgeRetryCircuitAttemptSelection,
    _HTTPBridgeSession,
    _mark_response_create_attempt_observed,
    _pop_websocket_deferred_reasoning_downstream_texts,
    _record_response_event,
    _signal_propagated_capacity_startup_ready,
    _signal_propagated_capacity_startup_wait,
    _websocket_request_can_replay_before_visible_output,
    _websocket_should_defer_reasoning_prelude,
    _WebSocketReceiveTimeout,
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
from app.modules.proxy.continuity import is_http_bridge_account_neutral_replay
from app.modules.proxy.helpers import (
    _normalize_error_code,
    is_upstream_model_capacity_error,
)
from app.modules.proxy.tool_call_dedupe import (
    mark_duplicate_tool_call_downstream_event,
    rewrite_parallel_tool_call_text,
)
from app.modules.proxy.tool_call_dedupe import (
    response_id_from_payload as tool_call_response_id_from_payload,
)

logger = logging.getLogger("app.modules.proxy.service")

_HTTP_BRIDGE_RECOVERY_SETTLEMENT_RETRY_DELAYS = (
    0.25,
    0.5,
    1.0,
    2.0,
    4.0,
    8.0,
    15.0,
    30.0,
    60.0,
    120.0,
)
_HTTP_BRIDGE_RECOVERY_SETTLEMENT_LEASE_REFRESH_INTERVAL_SECONDS = 10.0
_HTTP_BRIDGE_DENIED_ANCHOR_CLEAR_RETRY_DELAYS = (
    0.25,
    0.5,
    1.0,
    2.0,
    4.0,
    8.0,
    15.0,
    30.0,
    60.0,
)
# A single missing response.created is not proof that an account is bad: the
# upstream may have accepted the request while the transport was silent. Only
# repeated failures on separate bridge retirements are allowed to influence
# account routing, and the signal expires quickly so a transient upstream
# incident does not permanently drain an account.
_HTTP_BRIDGE_ACCOUNT_TIMEOUT_WINDOW_SECONDS = 300.0
_HTTP_BRIDGE_ACCOUNT_TIMEOUT_EJECTION_THRESHOLD = 3


async def _record_http_bridge_account_timeout_signal(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    detail: str = _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
) -> None:
    """Drain an account after repeated eventless upstream failures.

    This is deliberately separate from the per-session retry circuit. A
    timeout or abrupt eventless transport drop cannot be replayed safely for a
    continuity-bound turn, but three independent eventless failures are enough
    evidence to keep *new* turns away from that account until its normal
    health probe succeeds.
    """

    account_id = session.account.id
    now = time.monotonic()
    async with service._http_bridge_account_timeout_lock:
        failures = service._http_bridge_account_timeout_failures.setdefault(account_id, [])
        failures[:] = [
            timestamp for timestamp in failures if now - timestamp < _HTTP_BRIDGE_ACCOUNT_TIMEOUT_WINDOW_SECONDS
        ]
        failures.append(now)
        if len(failures) < _HTTP_BRIDGE_ACCOUNT_TIMEOUT_EJECTION_THRESHOLD:
            return
        # Start a fresh evidence window after applying one health penalty. A
        # continuously failing account should be re-evaluated by normal
        # health-tier logic, not receive an unbounded error-count increase from
        # every pending request on one broken socket.
        failures.clear()

    try:
        # Health-tier draining starts at two transient errors. Apply exactly
        # that minimum penalty so one threshold event actually removes the
        # account from normal routing without over-counting the incident.
        await service._load_balancer.record_errors(session.account, 2)
    except Exception:
        logger.warning(
            "Failed to record repeated HTTP bridge account timeout account_id=%s",
            account_id,
            exc_info=True,
        )
    else:
        logger.warning(
            "HTTP bridge account temporarily drained after repeated eventless upstream failures "
            "account_id=%s detail=%s threshold=%s window_seconds=%.0f",
            account_id,
            detail,
            _HTTP_BRIDGE_ACCOUNT_TIMEOUT_EJECTION_THRESHOLD,
            _HTTP_BRIDGE_ACCOUNT_TIMEOUT_WINDOW_SECONDS,
        )


async def _update_http_bridge_operation_state(
    service: Any,
    session: "_HTTPBridgeSession",
    request_state: Any,
    *,
    state: str,
    response_id: str | None = None,
) -> None:
    """Persist operation outcome without allowing journaling to break streaming."""
    operation_id = getattr(request_state, "operation_id", None)
    session_id = getattr(session, "durable_session_id", None)
    owner_epoch = getattr(session, "durable_owner_epoch", None)
    update_operation = getattr(getattr(service, "_durable_bridge", None), "update_operation", None)
    if not operation_id or session_id is None or owner_epoch is None or not callable(update_operation):
        return
    try:
        marked = await update_operation(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            owner_epoch=owner_epoch,
            state=state,
            response_id=response_id,
        )
        if marked and response_id is not None:
            request_state.operation_persisted_response_id = response_id
        if not marked:
            logger.info(
                "HTTP bridge operation outcome owner fence rejected operation_id=%s state=%s",
                operation_id,
                state,
            )
    except Exception:
        logger.warning(
            "Failed to persist HTTP bridge operation outcome operation_id=%s state=%s",
            operation_id,
            state,
            exc_info=True,
        )


def _http_bridge_operation_state_for_event(event_type: str | None) -> str | None:
    return {
        "response.created": "acknowledged",
        "response.completed": "completed",
        "response.incomplete": "incomplete",
        "response.failed": "failed",
        "error": "failed",
    }.get(event_type)


async def _persist_http_bridge_operation_event(
    service: Any,
    session: "_HTTPBridgeSession",
    request_state: Any,
    event_block: str,
    *,
    terminal: bool = False,
    terminal_state: str | None = None,
    terminal_event_queue: Any | None = None,
    terminal_delivery_scope: _HTTPBridgeCompletedDeliveryScope | None = None,
    terminal_append_barrier: Callable[[], Awaitable[None]] | None = None,
    terminal_delivery_barrier: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """Spool one downstream-visible SSE block for reconnect replay.

    Return whether terminal failure handling already queued the block.
    """
    operation_id = getattr(request_state, "operation_id", None)
    session_id = getattr(session, "durable_session_id", None)
    owner_epoch = getattr(session, "durable_owner_epoch", None)
    batcher_enqueue = getattr(getattr(service, "_http_bridge_operation_event_batcher", None), "enqueue", None)
    append_event = getattr(getattr(service, "_durable_bridge", None), "append_operation_event", None)
    if not operation_id or session_id is None or owner_epoch is None:
        return False
    append_barrier_task: asyncio.Task[None] | None = None
    delivery_barrier_task: asyncio.Task[None] | None = None
    terminal_enqueued = False
    deferred_cancellation: asyncio.CancelledError | None = None

    async def release_terminal_append_barrier() -> asyncio.CancelledError | None:
        nonlocal append_barrier_task
        if terminal_append_barrier is None:
            return None
        if append_barrier_task is None:

            async def await_append_barrier() -> None:
                await terminal_append_barrier()

            append_barrier_task = asyncio.create_task(
                await_append_barrier(),
                name=f"http-bridge-terminal-append-barrier-{operation_id}",
            )
        _, cancellation = await _await_task_deferring_cancellation(append_barrier_task)
        return cancellation

    async def release_terminal_delivery_barrier() -> asyncio.CancelledError | None:
        nonlocal delivery_barrier_task
        if terminal_delivery_barrier is None:
            return None
        if delivery_barrier_task is None:

            async def await_delivery_barrier() -> None:
                await terminal_delivery_barrier()

            delivery_barrier_task = asyncio.create_task(
                await_delivery_barrier(),
                name=f"http-bridge-terminal-delivery-barrier-{operation_id}",
            )
        _, cancellation = await _await_task_deferring_cancellation(delivery_barrier_task)
        return cancellation

    async def enqueue_terminal_delivery() -> bool:
        if terminal_event_queue is None:
            return False
        await terminal_event_queue.put(event_block)
        await terminal_event_queue.put(None)
        if terminal_delivery_scope is not None:
            async with session.pending_lock:
                terminal_delivery_scope.terminal_enqueued = True
        return True

    async def enqueue_terminal_delivery_deferring_cancellation() -> tuple[bool, asyncio.CancelledError | None]:
        delivery_task = asyncio.create_task(
            enqueue_terminal_delivery(),
            name=f"http-bridge-terminal-delivery-{operation_id}",
        )
        return await _await_task_deferring_cancellation(delivery_task)

    try:
        batcher = getattr(service, "_http_bridge_operation_event_batcher", None)
        append_terminal_batch = getattr(batcher, "append_terminal_event", None)
        if terminal and terminal_state is not None and callable(append_terminal_batch):
            instance_id = _service_get_settings().http_responses_session_bridge_instance_id
            expected_response_ids = tuple(
                dict.fromkeys(
                    response_identity
                    for response_identity in (
                        request_state.response_id,
                        getattr(request_state, "operation_persisted_response_id", None),
                        request_state.replay_downstream_response_id,
                    )
                    if response_identity is not None
                )
            )
            expected_response_id = expected_response_ids[0] if expected_response_ids else None
            alternate_expected_response_id = expected_response_ids[1] if len(expected_response_ids) > 1 else None
            response_id = _websocket_downstream_response_id(request_state)

            async def append_terminal_batch_capturing_error() -> tuple[Any | None, Exception | None]:
                try:
                    return (
                        await append_terminal_batch(
                            operation_id=operation_id,
                            session_id=session_id,
                            instance_id=instance_id,
                            owner_epoch=owner_epoch,
                            event_text=event_block,
                            max_bytes=int(
                                getattr(
                                    _service_get_settings(),
                                    "http_responses_session_bridge_operation_event_spool_max_bytes",
                                    2 * 1024 * 1024,
                                )
                            ),
                            state=terminal_state,
                            expected_recovery_dispatch_count=request_state.operation_attempt_generation,
                            response_id=response_id,
                        ),
                        None,
                    )
                except Exception as exc:
                    # Return the optional-spool failure as data so the canonical
                    # defer helper can also return a caller-cancellation marker.
                    return None, exc

            append_task = asyncio.create_task(
                append_terminal_batch_capturing_error(),
                name=f"http-bridge-terminal-append-{operation_id}",
            )
            # Counted grouped siblings wait on this barrier; release it even when
            # append raises so gather(..., return_exceptions=True) cannot strand them.
            try:
                (append_result, append_error), deferred_cancellation = await _await_task_deferring_cancellation(
                    append_task
                )
            finally:
                barrier_cancellation = await release_terminal_append_barrier()
                deferred_cancellation = deferred_cancellation or barrier_cancellation
            if append_error is not None:
                raise append_error
            persisted = bool(append_result)
            if not persisted:
                logger.info("HTTP bridge terminal event spool became incomplete operation_id=%s", operation_id)
            settlement_required = bool(getattr(append_result, "settlement_required", False))
            if settlement_required:
                terminal_enqueued, delivery_cancellation = await enqueue_terminal_delivery_deferring_cancellation()
                deferred_cancellation = deferred_cancellation or delivery_cancellation
            if terminal_delivery_barrier is not None:
                if not terminal_enqueued:
                    terminal_enqueued, delivery_cancellation = await enqueue_terminal_delivery_deferring_cancellation()
                    deferred_cancellation = deferred_cancellation or delivery_cancellation
                barrier_cancellation = await release_terminal_delivery_barrier()
                deferred_cancellation = deferred_cancellation or barrier_cancellation
            if settlement_required:
                settle_terminal_batch = getattr(batcher, "settle_terminal_event", None)

                async def settle_terminal_append_failure() -> None:
                    if callable(settle_terminal_batch):
                        await settle_terminal_batch(
                            operation_id=operation_id,
                            session_id=session_id,
                            instance_id=instance_id,
                            owner_epoch=owner_epoch,
                            state=terminal_state,
                            expected_response_id=expected_response_id,
                            expected_recovery_dispatch_count=request_state.operation_attempt_generation,
                            alternate_expected_response_id=alternate_expected_response_id,
                            response_id=response_id,
                        )
                    else:
                        await _update_http_bridge_operation_state(
                            service,
                            session,
                            request_state,
                            state=terminal_state,
                            response_id=response_id,
                        )

                settlement_task = asyncio.create_task(
                    settle_terminal_append_failure(),
                    name=f"http-bridge-terminal-settlement-{operation_id}",
                )
                _, settlement_cancellation = await _await_task_deferring_cancellation(settlement_task)
                deferred_cancellation = deferred_cancellation or settlement_cancellation
            if deferred_cancellation is not None:
                if not terminal_enqueued:
                    await enqueue_terminal_delivery_deferring_cancellation()
                raise deferred_cancellation
            return terminal_enqueued
        if callable(batcher_enqueue):
            await batcher_enqueue(
                operation_id=operation_id,
                session_id=session_id,
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                owner_epoch=owner_epoch,
                event_text=event_block,
                terminal=terminal,
            )
            return False
        if not callable(append_event):
            return False
        persisted = await append_event(
            operation_id=operation_id,
            session_id=session_id,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            owner_epoch=owner_epoch,
            event_text=event_block,
            max_bytes=int(
                getattr(
                    _service_get_settings(),
                    "http_responses_session_bridge_operation_event_spool_max_bytes",
                    2 * 1024 * 1024,
                )
            ),
        )
        if not persisted:
            logger.info("HTTP bridge operation event spool became incomplete operation_id=%s", operation_id)
        if terminal and terminal_state is not None:
            await _update_http_bridge_operation_state(
                service,
                session,
                request_state,
                state=terminal_state,
                response_id=_websocket_downstream_response_id(request_state),
            )
        return False
    except Exception:
        # The upstream result is still delivered. A reconnect can only replay
        # when every event was durably persisted, so never fail a live stream
        # because the optional spool is unavailable.
        logger.warning("Failed to persist HTTP bridge operation event operation_id=%s", operation_id, exc_info=True)
        barrier_cancellation = await release_terminal_append_barrier()
        deferred_cancellation = deferred_cancellation or barrier_cancellation
        if terminal_event_queue is not None and terminal and not terminal_enqueued:
            try:
                terminal_enqueued, delivery_cancellation = await enqueue_terminal_delivery_deferring_cancellation()
                deferred_cancellation = deferred_cancellation or delivery_cancellation
            except Exception:
                logger.debug(
                    "Failed to enqueue HTTP bridge terminal after spool error operation_id=%s",
                    operation_id,
                    exc_info=True,
                )
        barrier_cancellation = await release_terminal_delivery_barrier()
        deferred_cancellation = deferred_cancellation or barrier_cancellation
        if deferred_cancellation is not None:
            raise deferred_cancellation
        return terminal_enqueued


async def _wait_for_http_bridge_recovery_settlement_retry(
    service: Any,
    *,
    session_id: str,
    owner_epoch: int,
    api_key_id: str | None,
    delay_seconds: float,
) -> None:
    remaining = max(0.0, delay_seconds)
    while remaining > 0:
        await asyncio.sleep(min(remaining, _HTTP_BRIDGE_RECOVERY_SETTLEMENT_LEASE_REFRESH_INTERVAL_SECONDS))
        remaining -= _HTTP_BRIDGE_RECOVERY_SETTLEMENT_LEASE_REFRESH_INTERVAL_SECONDS
        try:
            await service._durable_bridge.renew_live_session(
                session_id=session_id,
                api_key_id=api_key_id,
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                owner_epoch=owner_epoch,
                lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
            )
        except Exception:
            logger.debug("Failed to refresh HTTP bridge lease during settlement backoff", exc_info=True)


async def _retry_http_bridge_recovery_settlement(
    service: Any,
    session: Any,
    *,
    session_id: str,
    api_key_id: str | None,
    instance_id: str,
    owner_epoch: int,
    request_fingerprint: str,
    response_id: str | None,
    release_origin_lease: bool,
) -> None:
    """Keep a response-observed journal row fenced until durable settlement succeeds."""

    for delay_seconds in _HTTP_BRIDGE_RECOVERY_SETTLEMENT_RETRY_DELAYS:
        await _wait_for_http_bridge_recovery_settlement_retry(
            service,
            session_id=session_id,
            owner_epoch=owner_epoch,
            api_key_id=api_key_id,
            delay_seconds=delay_seconds,
        )
        try:
            marked = await service._durable_bridge.mark_recovery_attempt_replayed(
                session_id=session_id,
                api_key_id=api_key_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                request_fingerprint=request_fingerprint,
                response_id=response_id,
            )
            if marked and (release_origin_lease or getattr(session, "closed", False)):
                try:
                    await service._durable_bridge.release_live_session(
                        session_id=session_id,
                        instance_id=instance_id,
                        owner_epoch=owner_epoch,
                        draining=False,
                    )
                except Exception:
                    logger.debug("Failed to release HTTP bridge recovery origin lease", exc_info=True)
            if marked:
                return
            logger.warning("HTTP bridge recovery settlement owner fence rejected; retrying")
        except Exception:
            continue
    logger.error(
        "HTTP bridge recovery settlement retry budget exhausted session_id=%s fingerprint=%s",
        _hash_identifier(session_id),
        _hash_identifier(request_fingerprint),
    )


def _schedule_http_bridge_recovery_settlement_retry(
    service: Any,
    session: Any,
    **kwargs: Any,
) -> None:
    _schedule_http_bridge_background_cleanup(
        service,
        _retry_http_bridge_recovery_settlement(service, session, **kwargs),
        name=f"http-bridge-recovery-settlement-{_hash_identifier(kwargs['request_fingerprint'])}",
        error_message="HTTP bridge recovery settlement retry failed",
        attribute=("_http_bridge_recovery_session_id", kwargs["session_id"]),
    )


async def _retry_denied_http_bridge_anchor_clear(
    service: Any,
    session: Any,
    *,
    session_id: str | None,
    api_key_id: str | None,
    instance_id: str,
    owner_epoch: int | None,
    response_id: str,
    durable_cleared: bool = False,
) -> None:
    """Retry a transient durable clear under the original owner fence."""
    if session_id is None or owner_epoch is None:
        await _retry_denied_http_bridge_anchor_local_cleanup(
            service,
            session,
            response_id=response_id,
            owner_key=f"local:{id(session)}",
        )
        return
    for delay_seconds in _HTTP_BRIDGE_DENIED_ANCHOR_CLEAR_RETRY_DELAYS:
        if session.durable_session_id != session_id or session.durable_owner_epoch != owner_epoch:
            return
        await _wait_for_http_bridge_recovery_settlement_retry(
            service,
            session_id=session_id,
            owner_epoch=owner_epoch,
            api_key_id=api_key_id,
            delay_seconds=delay_seconds,
        )
        if not durable_cleared:
            try:
                cleared = await service._durable_bridge.clear_live_session_response_anchor_if_matches(
                    session_id=session_id,
                    api_key_id=api_key_id,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    response_id=response_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Retrying denied HTTP bridge response-anchor clear after durable failure", exc_info=True)
                continue
            if cleared is None:
                # A clean no-match means the captured owner fence or denied
                # response is already gone.  It is a terminal bookkeeping
                # outcome, not a transient durable failure; keep the local
                # alias and denial fence because ownership may have advanced,
                # but do not spend the remaining retry budget on it.
                return
            durable_cleared = True
        # Owner rebinding can happen while the lease-renewal backoff is
        # sleeping. Serialize the final ownership check with rebinders before
        # touching the alias or the process-local denial fence.
        async with session.lifecycle_lock:
            if session.durable_session_id != session_id or session.durable_owner_epoch != owner_epoch:
                return
            try:
                unregister_succeeded = await service._unregister_http_bridge_previous_response_id(
                    session,
                    response_id,
                    expected_durable_session_id=session_id,
                    expected_durable_owner_epoch=owner_epoch,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Retrying denied HTTP bridge response-alias unregister after cleanup failure",
                    exc_info=True,
                )
                continue
            if unregister_succeeded is False:
                return
            if not _forget_http_bridge_denied_anchor_fence(
                service,
                response_id,
                owner_key=session_id,
                owner_epoch=owner_epoch,
            ):
                return
            session.denied_proxy_injected_anchor_ids.discard(response_id)
            session.denied_proxy_injected_anchor_cleanup_pending.discard(response_id)
            session.denied_proxy_injected_anchor_generation += 1
            return
    logger.error(
        "Denied HTTP bridge response-anchor clear retry budget exhausted session_id=%s response_id=%s",
        _hash_identifier(session_id),
        _hash_identifier(response_id),
    )


async def _retry_denied_http_bridge_anchor_local_cleanup(
    service: Any,
    session: Any,
    *,
    response_id: str,
    owner_key: str,
) -> None:
    """Retry process-local alias cleanup for a session without a durable owner."""
    for delay_seconds in _HTTP_BRIDGE_DENIED_ANCHOR_CLEAR_RETRY_DELAYS:
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        async with session.lifecycle_lock:
            if session.durable_session_id is not None or session.durable_owner_epoch is not None:
                return
            entry = _http_bridge_denied_anchor_fence_entry(service, response_id)
            try:
                unregister_succeeded = await service._unregister_http_bridge_previous_response_id(
                    session,
                    response_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Retrying local denied HTTP bridge response-alias unregister after cleanup failure",
                    exc_info=True,
                )
                continue
            if unregister_succeeded is False:
                continue
            # The local session can lose its durable identity after a
            # successor has already claimed the same response id.  Remove
            # this session's alias even when the process-local fence now
            # belongs to that durable successor; only forget the fence when
            # this local owner still owns it.
            if entry is not None and entry.owner_key == owner_key:
                _forget_http_bridge_denied_anchor_fence(
                    service,
                    response_id,
                    owner_key=owner_key,
                    owner_epoch=None,
                )
            session.denied_proxy_injected_anchor_ids.discard(response_id)
            session.denied_proxy_injected_anchor_cleanup_pending.discard(response_id)
            session.denied_proxy_injected_anchor_generation += 1
            return
    logger.error(
        "Denied HTTP bridge local response-anchor cleanup retry budget exhausted response_id=%s",
        _hash_identifier(response_id),
    )


def _schedule_denied_http_bridge_anchor_clear_retry(
    service: Any,
    session: Any,
    *,
    response_id: str,
    session_id: str | None = None,
    api_key_id: str | None = None,
    instance_id: str | None = None,
    owner_epoch: int | None = None,
    durable_cleared: bool = False,
) -> None:
    session_id = session.durable_session_id if session_id is None else session_id
    owner_epoch = session.durable_owner_epoch if owner_epoch is None else owner_epoch
    api_key_id = session.key.api_key_id if api_key_id is None else api_key_id
    instance_id = (
        _service_get_settings().http_responses_session_bridge_instance_id if instance_id is None else instance_id
    )
    if instance_id is None:
        return
    _schedule_http_bridge_background_cleanup(
        service,
        _retry_denied_http_bridge_anchor_clear(
            service,
            session,
            session_id=session_id,
            api_key_id=api_key_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            response_id=response_id,
            durable_cleared=durable_cleared,
        ),
        name=f"http-bridge-denied-anchor-clear-{_hash_identifier(response_id)}",
        error_message="HTTP bridge denied-anchor clear retry failed",
    )


T = TypeVar("T")
_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_MODEL_OUTPUT_EVENT_TYPES = frozenset(
    {
        "response.output_item.added",
        "response.output_item.done",
        "response.output_text.delta",
        "response.refusal.delta",
        "response.reasoning_text.delta",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
        "response.function_call_arguments.delta",
        "response.output_tool_call.delta",
    }
)
_UNSUPPORTED_DURABLE_TOOL_CALL_ITEM_TYPES = frozenset(
    {
        "computer_call",
        "mcp_approval_request",
    }
)


def _record_http_bridge_tool_call_lifecycle(
    request_state: _WebSocketRequestState,
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
) -> None:
    if event_type not in {"response.output_item.added", "response.output_item.done"}:
        return
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict):
        request_state.tool_call_manifest_invalid = True
        return
    item_type = item.get("type")
    if not isinstance(item_type, str):
        request_state.tool_call_manifest_invalid = True
        return
    if item_type in _UNSUPPORTED_DURABLE_TOOL_CALL_ITEM_TYPES:
        # These calls require client-provided continuation state but are not
        # representable by the direct function/custom/apply-patch replay proof.
        # Persisting only a parallel supported call would make a partial suffix
        # look complete, so keep the whole durable manifest unknown.
        request_state.tool_call_manifest_invalid = True
        return
    if item_type not in _PENDING_TOOL_CALL_ITEM_TYPES:
        return
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        request_state.tool_call_manifest_invalid = True
        return
    target = (
        request_state.added_tool_call_types
        if event_type == "response.output_item.added"
        else request_state.pending_tool_call_types
    )
    existing = target.get(call_id)
    if existing is not None:
        request_state.tool_call_manifest_invalid = True
        return
    target[call_id] = item_type


def _response_completed_tool_call_types(payload: dict[str, JsonValue] | None) -> dict[str, str] | None:
    response = payload.get("response") if isinstance(payload, dict) else None
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, list):
        return None
    result: dict[str, str] = {}
    for item in output:
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if not isinstance(item_type, str):
            return None
        if item_type in _UNSUPPORTED_DURABLE_TOOL_CALL_ITEM_TYPES:
            return None
        if item_type not in _PENDING_TOOL_CALL_ITEM_TYPES:
            continue
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return None
        existing = result.get(call_id)
        if existing is not None:
            return None
        result[call_id] = item_type
    return result


def _durable_pending_tool_call_manifest(
    request_state: _WebSocketRequestState,
    payload: dict[str, JsonValue] | None,
) -> dict[str, str] | None:
    terminal_calls = _response_completed_tool_call_types(payload)
    if request_state.tool_call_manifest_invalid or terminal_calls is None:
        return None
    if request_state.added_tool_call_types != request_state.pending_tool_call_types:
        return None
    if terminal_calls and terminal_calls != request_state.pending_tool_call_types:
        return None
    return dict(request_state.pending_tool_call_types)


_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
_SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "codex-lb is retrying on an account marked as authorized for security work."
)


async def _wait_before_http_bridge_model_capacity_retry(
    request_state: _WebSocketRequestState | None,
    *,
    emit_keepalives: bool,
    error_message: str | None,
    cancel_when_detached: bool = False,
) -> bool:
    if request_state is None or not is_upstream_model_capacity_error(error_message):
        return True

    deadline = request_state.bridge_request_deadline
    if deadline is None:
        deadline = request_state.started_at + _http_bridge_request_budget_seconds(_service_get_settings())
    remaining_budget_seconds = max(0.0, deadline - _service_time().monotonic())
    if remaining_budget_seconds <= 0:
        return False

    sleep_seconds = min(_ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS, remaining_budget_seconds)
    request_state.account_capacity_waiting = True
    request_state.account_capacity_wait_reason = error_message
    request_state.account_capacity_wait_started_at = (
        request_state.account_capacity_wait_started_at or _service_time().monotonic()
    )
    request_state.account_capacity_wait_retry_after_seconds = sleep_seconds
    request_state.account_capacity_wait_suppress_keepalive = not emit_keepalives
    if not emit_keepalives:
        _signal_http_bridge_capacity_startup_wait(request_state)
    try:
        remaining_sleep_seconds = sleep_seconds
        keepalive_countdown_seconds = 0.0
        while remaining_sleep_seconds > 0:
            if cancel_when_detached and request_state.event_queue is None:
                return False
            if emit_keepalives and keepalive_countdown_seconds <= 0 and request_state.event_queue is not None:
                await request_state.event_queue.put(
                    format_sse_event(
                        _account_capacity_wait_payload(
                            request_state,
                            request_id=request_state.request_log_id or request_state.request_id,
                            reason=error_message,
                            retry_after_seconds=remaining_sleep_seconds,
                        )
                    )
                )
                keepalive_countdown_seconds = _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS
            chunk_seconds = min(
                remaining_sleep_seconds,
                (
                    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS
                    if cancel_when_detached
                    else _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS
                ),
            )
            await asyncio.sleep(chunk_seconds)
            remaining_sleep_seconds -= chunk_seconds
            keepalive_countdown_seconds -= chunk_seconds
        return (
            not cancel_when_detached or request_state.event_queue is not None
        ) and _service_time().monotonic() < deadline
    finally:
        request_state.account_capacity_waiting = False
        if emit_keepalives:
            request_state.account_capacity_wait_suppress_keepalive = False
        request_state.account_capacity_wait_reason = None
        request_state.account_capacity_wait_retry_after_seconds = None


async def _release_http_bridge_model_capacity_retry_admission(
    request_state: _WebSocketRequestState,
) -> None:
    """Release shared capacity while retaining the session create gate."""
    if request_state.response_create_admission is not None:
        request_state.response_create_admission.release()
        request_state.response_create_admission = None
        request_state.response_create_admission_reacquire_required = True
    account_response_create_lease = request_state.account_response_create_lease
    account_response_create_release = request_state.account_response_create_release
    request_state.account_response_create_lease = None
    request_state.account_response_create_release = None
    if account_response_create_lease is not None and account_response_create_release is not None:
        await account_response_create_release(account_response_create_lease)


def _signal_http_bridge_model_capacity_retry_ready(
    request_state: _WebSocketRequestState,
    *,
    waited_for_model_capacity_retry: bool,
    retried: bool,
) -> None:
    if waited_for_model_capacity_retry and retried and request_state.propagate_http_errors:
        if request_state.capacity_startup_wait_event is not None:
            request_state.capacity_startup_wait_event.clear()
        if request_state.capacity_startup_ready_event is not None:
            request_state.capacity_startup_ready_event.set()
        _signal_propagated_capacity_startup_ready()


def _signal_http_bridge_capacity_startup_wait(request_state: _WebSocketRequestState) -> None:
    if request_state.capacity_startup_ready_event is not None:
        request_state.capacity_startup_ready_event.clear()
    if request_state.capacity_startup_wait_event is not None:
        request_state.capacity_startup_wait_event.set()
    _signal_propagated_capacity_startup_wait()


def _archive_http_bridge_upstream_text(
    session: "_HTTPBridgeSession",
    text: str,
    request_state: "_WebSocketRequestState | None",
) -> None:
    _archive_http_bridge_upstream_message(
        session,
        UpstreamWebSocketMessage(kind="text", text=text),
        request_state,
    )


def _archive_http_bridge_upstream_message(
    session: "_HTTPBridgeSession",
    message: UpstreamWebSocketMessage,
    request_state: "_WebSocketRequestState | None",
) -> None:
    if request_state is None or request_state.archive_request_id is None:
        archive_request_id = None
    else:
        archive_request_id = request_state.archive_request_id
    archive_received = getattr(session.upstream, "archive_received", None)
    if not callable(archive_received):
        return
    token = set_request_id(archive_request_id)
    try:
        archive_received(message)
    finally:
        reset_request_id(token)


async def _http_bridge_receive_timeout_with_eventless_deadline(
    session: "_HTTPBridgeSession",
    receive_timeout: _WebSocketReceiveTimeout | None,
    *,
    now: float,
    stuck_gate_retire_after_seconds: float,
) -> _WebSocketReceiveTimeout | None:
    if session.closed:
        return receive_timeout
    async with session.pending_lock:
        deadlines = [
            deadline
            for request_state in session.pending_requests
            if (
                deadline := _http_bridge_eventless_precreated_deadline(
                    request_state,
                    stuck_gate_retire_after_seconds=stuck_gate_retire_after_seconds,
                )
            )
            is not None
        ]
    if not deadlines:
        return receive_timeout
    eventless_timeout = _WebSocketReceiveTimeout(
        timeout_seconds=max(0.0, min(deadlines) - now),
        error_code=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
        error_message="Upstream did not acknowledge response.create before the client-safe deadline",
        fail_all_pending=True,
    )
    if receive_timeout is None or eventless_timeout.timeout_seconds <= receive_timeout.timeout_seconds:
        return eventless_timeout
    return receive_timeout


async def _cancel_http_bridge_reader_child(
    task: asyncio.Task[Any] | None,
    *,
    label: str,
    cleanup_tasks: set[asyncio.Task[None]] | None = None,
) -> bool:
    if task is None:
        return True
    if task.done():
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("HTTP bridge reader child already failed during cleanup label=%s", label, exc_info=True)
        return True
    try:
        return bool(
            await _await_cancelled_task(
                task,
                label=label,
                cleanup_tasks=cleanup_tasks,
            )
        )
    except Exception:
        logger.debug("Failed to cancel HTTP bridge reader child label=%s", label, exc_info=True)
        return task.done()


async def _clear_durable_http_bridge_response_anchor(
    service: Any,
    session: "_HTTPBridgeSession",
) -> None:
    """Invalidate a durable proxy-injected anchor that proved eventless.

    Runs while ``session`` still owns the durable row (before retirement
    releases the lease), so the fenced write lands under the session's own
    owner epoch instead of silently losing the fence to a released owner.
    """
    if session.durable_session_id is None or session.durable_owner_epoch is None:
        return
    try:
        lookup = await service._durable_bridge.clear_live_session_response_anchor(
            session_id=session.durable_session_id,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            owner_epoch=session.durable_owner_epoch,
        )
    except Exception:
        logger.warning("Failed to clear durable HTTP bridge response anchor after stuck timeout", exc_info=True)
        return
    if lookup is None or lookup.owner_epoch != session.durable_owner_epoch or lookup.latest_response_id is not None:
        # None means the durable row is gone entirely (e.g. purged); an
        # epoch or anchor mismatch means a newer owner already claimed the
        # session before this fenced write executed. Either way, the anchor
        # was never actually cleared, so do not report an invalidation that
        # did not happen.
        return
    _log_http_bridge_event(
        "durable_anchor_invalidated",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
    )


async def _abandon_durable_http_bridge_continuity(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    detail: str = "repeated_zero_event_idle_timeout",
    settle_circuit: bool = False,
    expected_continuity: object = _POISON_ANCHOR_CAPTURE_UNAVAILABLE,
    authorized_episode: Any = None,
) -> bool:
    """Clear durable continuity before retiring a repeatedly poisoned bridge.

    ``rebind_session_account(clear_continuity=True)`` is an existing fenced
    write that clears the durable response/turn anchor and its alias rows while
    this worker still owns the session. The ordinary retirement path then
    closes the row and removes the process-local registrations.
    """
    if session.durable_session_id is None or session.durable_owner_epoch is None:
        return False
    # Captured BEFORE the continuity clear's await: this is the closest
    # snapshot to the episode the poison consult validated, and a sibling
    # replacing the circuit episode while the rebind is in flight must not
    # be mistaken for the authorizing one — the settle below clears only
    # the episode this abandonment invalidated.
    async with service._http_bridge_retry_circuit_lock:
        # The consulted episode outranks a registry re-read: a sibling
        # settle can remove the entry between the consult and this lock,
        # and a None capture would run the settle unfenced — free to clear
        # a replacement episode opened during the awaits below. The
        # registry read remains only for legacy callers with no consult.
        authorizing_state = (
            authorized_episode
            if authorized_episode is not None
            else service._http_bridge_retry_circuits.get(session.key)
        )
        abandonment_expected_episode = (
            (
                authorizing_state.persisted_updated_at_epoch,
                authorizing_state.consecutive_failures,
                authorizing_state.persisted_admission_generation,
            )
            if authorizing_state is not None
            else None
        )
    rebind_fence_kwargs: dict[str, Any] = {}
    if expected_continuity is not _POISON_ANCHOR_CAPTURE_UNAVAILABLE:
        # Fence the continuity clear on both anchors captured when the
        # episode was validated: a completion registering a fresh response
        # anchor or a turn-state write landing in between changes a column
        # and the fenced write matches nothing.
        expected_response_id, expected_turn_state = cast("tuple[str | None, str | None]", expected_continuity)
        rebind_fence_kwargs["expected_latest_response_id"] = expected_response_id
        rebind_fence_kwargs["expected_latest_turn_state"] = expected_turn_state
    try:
        cleared = await service._durable_bridge.rebind_session_account(
            session_id=session.durable_session_id,
            api_key_id=session.key.api_key_id,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            owner_epoch=session.durable_owner_epoch,
            account_id=session.account.id,
            clear_continuity=True,
            **rebind_fence_kwargs,
        )
    except Exception:
        logger.warning("Failed to abandon poisoned HTTP bridge continuity", exc_info=True)
        return False
    if not cleared:
        logger.warning(
            "Durable bridge continuity clear was fenced before poisoned anchor retirement",
            extra={
                "session_id": session.durable_session_id,
                "account_id": session.account.id,
            },
        )
        return False
    _log_http_bridge_event(
        "durable_anchor_poisoned",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=detail,
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
    )
    # Settle only when the requests this abandonment covers are actually
    # stranded. A stale-anchor rejection that still holds a verified full
    # resend is about to be replayed, and that replay claims the circuit
    # generation at dispatch (#1863); clearing the circuit under it removes the
    # fence it depends on, which is why `response.completed` also skips its
    # clear for such a replay. Settling unconditionally broke five variants of
    # the stale-owner replay suite, and settling for no funnel caller left the
    # production wedge cooling for 60s after its anchor was already gone.
    if not settle_circuit:
        return True
    # The circuit was opened by failures against the anchor this call just
    # removed, so its cooldown is now backing off a cause that no longer
    # exists. Leaving it running refuses requests that carry no anchor at all:
    # observed live as a burst of ~25 rejections logged
    # `reason=retry_circuit_cooldown_continuity_bound previous_response_id=None`
    # in the 60s after a successful clear, every one of which would have gone
    # upstream cleanly. A confirmed abandonment is proof the next attempt
    # cannot repeat that failure, which is the same evidence a completed
    # response carries, so settle the circuit the same way. A genuinely new
    # failure re-opens it at the usual threshold.
    # A sibling completion can race this settle after the continuity clear:
    # it registers a FRESH anchor and erases its own transitional tombstone,
    # and writing the tombstone here again would durably 404 every valid
    # follow-up riding that fresh anchor, with no registration left to erase
    # it. Re-read the continuity: an anchor that is present and different
    # from the one this abandonment removed is fresh evidence, and the
    # settle then writes a plain reset instead of the tombstone.
    abandonment_settled_detail: str | None = _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
    abandoned_anchor, abandoned_turn_state = (
        (expected_continuity[0], expected_continuity[1])
        if isinstance(expected_continuity, tuple) and len(expected_continuity) == 2
        else (None, None)
    )

    def _continuity_is_fresh(observed: tuple[str | None, str | None] | None) -> bool:
        # Fresh evidence in EITHER continuity column: a delta can resolve
        # through the turn state alone, so a turn-state advance without a
        # response id is still continuity a tombstone must not fail closed.
        return observed is not None and (
            (observed[0] is not None and observed[0] != abandoned_anchor)
            or (observed[1] is not None and observed[1] != abandoned_turn_state)
        )

    if session.durable_session_id:
        try:
            post_clear_continuity = await service._durable_bridge.session_latest_continuity(
                session_id=session.durable_session_id
            )
        except Exception:
            # Unknown continuity keeps the tombstone: the stale-tombstone
            # cost is self-healing (the next completion settles and erases
            # it), while a missing tombstone silently drops delta context.
            post_clear_continuity = None
        if _continuity_is_fresh(post_clear_continuity):
            abandonment_settled_detail = None
    circuit_settled = await service._clear_http_bridge_retry_circuit(
        session,
        settled_detail=abandonment_settled_detail,
        settled_detail_authoritative=True,
        expected_episode=abandonment_expected_episode,
    )
    if not circuit_settled:
        # The clear reloads fresh state on every call, so one immediate
        # retry covers a transient durable failure or a double CAS miss
        # before the surviving cooldown is left to expire on its own. The
        # abandonment itself still reports success: the anchor is gone, the
        # marker must record that so the episode cannot clear now-empty
        # continuity again, and only the settlement is owed.
        circuit_settled = await service._clear_http_bridge_retry_circuit(
            session,
            settled_detail=abandonment_settled_detail,
            settled_detail_authoritative=True,
            expected_episode=abandonment_expected_episode,
        )
    if not circuit_settled:
        _log_http_bridge_event(
            "durable_circuit_settle_failed",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            detail=detail,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
    if circuit_settled and abandonment_settled_detail is not None and session.durable_session_id:
        # The pre-settle re-read closes most of the window, but a sibling
        # registration can still land between that read and the settle
        # write — and its own tombstone cleanup ran BEFORE this settle
        # wrote a new one. Re-read once more after the write and erase the
        # tombstone this settle left when fresh continuity now exists; the
        # fenced detail-only rewrite defers to any newer write, and both
        # sides reconciling after their own writes makes every
        # interleaving converge.
        try:
            reconcile_continuity = await service._durable_bridge.session_latest_continuity(
                session_id=session.durable_session_id
            )
        except Exception:
            reconcile_continuity = None
        if _continuity_is_fresh(reconcile_continuity):
            await service._http_bridge_reconcile_transitional_tombstone(session)
    return True


async def _invalidate_denied_http_bridge_anchor(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    denied_response_id: str | None,
) -> bool:
    """Retire an anchor upstream has explicitly denied.

    ``previous_response_not_found`` against an anchor the proxy injected is a
    verdict, not a symptom: the id came from this proxy's own durable record,
    no client asked for it, and upstream says it does not exist. The poison
    counter cannot act on that verdict because it only scores reader failures
    (see ``_HTTP_BRIDGE_ANCHOR_POISON_DETAILS``), so the dead id survives at
    any threshold and is re-injected into the following turn, where the
    store-context trim strips the resent history against it and upstream never
    emits ``response.created``.

    Clearing costs nothing that is not already lost. The next turn simply
    dispatches unanchored with the history the client sends, which is the
    client's own replay rather than a server-side one, so no forked child
    response can be created against a parent this proxy cannot see.

    The durable clear is conditional on the denied id still being the durable
    latest response, and removes only that response alias. The in-memory clear
    is unconditional for the same id even when the durable write is fenced,
    because it strictly removes one way for the denied id to come back. A
    durable row that survives re-injects the id on a later turn, which is denied
    in turn and re-enters this path, so the clear is re-attempted rather than
    lost.

    ``closed`` only fences new admissions. Requests admitted before a session
    closed can still deliver a terminal denial and must publish its provenance
    and finish the same cleanup.
    """
    if denied_response_id is None:
        return False
    sibling_advanced = False
    async with session.lifecycle_lock:
        # Serialize publication with the submitter's final tombstone check and
        # upstream send. A sibling completion can advance the current carrier
        # while an already-prepared request still holds the denied id; that
        # request must remain fenced even when there is no current anchor left
        # to clear.
        session.denied_proxy_injected_anchor_generation += 1
        # Retain denial provenance after the session-local tombstone is retired.
        # A request that began on an absent canonical session otherwise receives
        # a successor with no local generation and can redispatch this id.
        durable_session_id = session.durable_session_id
        durable_owner_epoch = session.durable_owner_epoch
        durable_api_key_id = session.key.api_key_id
        durable_instance_id = _service_get_settings().http_responses_session_bridge_instance_id
        owner_key = durable_session_id if durable_session_id is not None else f"local:{id(session)}"
        recorded_generation = _record_http_bridge_denied_anchor_fence(
            service,
            denied_response_id,
            owner_key=owner_key,
            owner_epoch=durable_owner_epoch,
        )
        current_fence = _http_bridge_denied_anchor_fence_current_map(service).get(owner_key)
        recorded_entry = _http_bridge_denied_anchor_fence_entry(service, denied_response_id)
        record_won_owner_slot = (
            current_fence == denied_response_id
            and recorded_entry is not None
            and recorded_entry.owner_key == owner_key
            and recorded_entry.generation == recorded_generation
        )
        # Keep one current session-local tombstone.  Displaced ids remain
        # fenced in the process ledger while any prepared request pins them;
        # retaining every historical id here would make the session carrier
        # grow without bound and duplicate that ledger's ownership fence.  A
        # stale detached predecessor must not erase a successor's tombstone,
        # so only replace the set when this publication owns the current slot.
        if record_won_owner_slot:
            session.denied_proxy_injected_anchor_ids.clear()
            session.denied_proxy_injected_anchor_ids.add(denied_response_id)
        elif not session.denied_proxy_injected_anchor_ids:
            # A detached predecessor may still have a request pinned to its
            # own session object. Keep that one local tombstone without
            # growing a second historical slot beside a successor's entry.
            session.denied_proxy_injected_anchor_ids.add(denied_response_id)
        # Another request may have completed and advanced the anchor between
        # the denied dispatch and this frame. Only retire the id that was
        # refused.
        if session.last_completed_response_id != denied_response_id:
            sibling_advanced = True
        elif hasattr(session, "denied_proxy_injected_anchor_cleanup_pending") and recorded_entry is not None:
            # Only the current anchor needs durable/alias cleanup. A sibling
            # that already advanced the carrier leaves a historical fence for
            # any pinned request, but has no unresolved cleanup to preserve at
            # session close.
            session.denied_proxy_injected_anchor_cleanup_pending.add(denied_response_id)
    cleared = False
    no_durable_owner = durable_session_id is None or durable_owner_epoch is None
    if sibling_advanced:
        if no_durable_owner:
            _forget_http_bridge_denied_anchor_fence(
                service,
                denied_response_id,
                owner_key=owner_key,
                owner_epoch=durable_owner_epoch,
            )
        getattr(session, "denied_proxy_injected_anchor_cleanup_pending", set()).discard(denied_response_id)
        return False
    retry_durable_clear = False
    unregister_succeeded = False
    owner_matches_for_cleanup = False
    unregister_error: BaseException | None = None
    durable_error: BaseException | None = None
    try:
        try:
            if not no_durable_owner:
                lookup = await service._durable_bridge.clear_live_session_response_anchor_if_matches(
                    session_id=durable_session_id,
                    api_key_id=durable_api_key_id,
                    instance_id=durable_instance_id,
                    owner_epoch=durable_owner_epoch,
                    response_id=denied_response_id,
                )
                cleared = lookup is not None
                # ``None`` is a clean fenced no-match (the owner epoch or
                # latest response changed), not a durable failure.  Preserve
                # the local alias and tombstone for the newer owner, but do
                # not schedule background retries; raised failures below are
                # the retryable case.
        except Exception:
            retry_durable_clear = True
            logger.warning("Failed to clear denied HTTP bridge response anchor", exc_info=True)
        finally:
            try:
                if cleared or no_durable_owner:
                    async with session.lifecycle_lock:
                        owner_matches_for_cleanup = (
                            session.durable_session_id == durable_session_id
                            and session.durable_owner_epoch == durable_owner_epoch
                        )
                        if owner_matches_for_cleanup:
                            try:
                                unregister_result = await service._unregister_http_bridge_previous_response_id(
                                    session,
                                    denied_response_id,
                                    expected_durable_session_id=durable_session_id,
                                    expected_durable_owner_epoch=durable_owner_epoch,
                                )
                                unregister_succeeded = unregister_result is not False
                            except asyncio.CancelledError as exc:
                                unregister_error = exc
                            except Exception as exc:
                                unregister_error = exc
                                retry_durable_clear = True
            finally:
                async with session.lifecycle_lock:
                    if session.last_completed_response_id == denied_response_id:
                        session.last_completed_response_id = None
                        session.last_completed_response_account_id = None
                        session.last_completed_input_count = 0
                        session.last_completed_input_prefix_fingerprint = None
                        session.last_pending_tool_calls.clear()
                    if owner_matches_for_cleanup and (cleared or no_durable_owner) and unregister_succeeded:
                        session.denied_proxy_injected_anchor_ids.discard(denied_response_id)
                        session.denied_proxy_injected_anchor_cleanup_pending.discard(denied_response_id)
                        session.denied_proxy_injected_anchor_generation += 1
                        _forget_http_bridge_denied_anchor_fence(
                            service,
                            denied_response_id,
                            owner_key=durable_session_id if durable_session_id is not None else f"local:{id(session)}",
                            owner_epoch=durable_owner_epoch,
                        )
                if retry_durable_clear:
                    _schedule_denied_http_bridge_anchor_clear_retry(
                        service,
                        session,
                        response_id=denied_response_id,
                        session_id=durable_session_id,
                        api_key_id=durable_api_key_id,
                        instance_id=durable_instance_id,
                        owner_epoch=durable_owner_epoch,
                        durable_cleared=cleared,
                    )
    except asyncio.CancelledError as exc:
        durable_error = exc
    if unregister_error is not None:
        raise unregister_error
    if durable_error is not None:
        raise durable_error
    return cleared


def _denied_proxy_injected_anchor_id(
    request_states: Iterable["_WebSocketRequestState"],
) -> str | None:
    """Choose the anchor a denial may retire, if any.

    Only an anchor shared exclusively by requests that codex-lb injected onto
    full-resend-shaped payloads can be retired. A client-supplied or delta-only
    sibling has no other way to convey prior context once its anchor is gone,
    which is the same rule the expired-anchor path applies before clearing
    durable continuity.
    """
    requests_by_anchor: dict[str, list[_WebSocketRequestState]] = {}
    for request_state in request_states:
        previous_response_id = request_state.previous_response_id
        if previous_response_id is not None:
            requests_by_anchor.setdefault(previous_response_id, []).append(request_state)

    safely_retirable_anchors = [
        previous_response_id
        for previous_response_id, grouped_request_states in requests_by_anchor.items()
        if all(
            request_state.proxy_injected_previous_response_id
            and request_state.proxy_injected_anchor_had_full_resend_payload
            for request_state in grouped_request_states
        )
    ]
    if len(safely_retirable_anchors) == 1:
        return safely_retirable_anchors[0]
    return None


async def _retire_denied_http_bridge_anchor(
    service: Any,
    session: "_HTTPBridgeSession",
    *,
    request_states: Iterable["_WebSocketRequestState"],
) -> None:
    """Best-effort retirement of an anchor upstream denied.

    Retirement is bookkeeping. It must never change how the denial itself is
    delivered downstream, so a failure here is logged and swallowed rather than
    escaping into terminal-event handling.
    """
    denied_response_id = _denied_proxy_injected_anchor_id(request_states)
    if denied_response_id is None:
        return
    try:
        await _invalidate_denied_http_bridge_anchor(
            service,
            session,
            denied_response_id=denied_response_id,
        )
    except Exception:
        logger.warning("Failed to retire a denied proxy-injected HTTP bridge anchor", exc_info=True)


class _HTTPBridgeUpstreamEventsMixin:
    async def _fail_http_bridge_reader_and_maybe_retire(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        error_code: str,
        error_message: str,
        penalize_account: bool = True,
        retire_detail: str | None = None,
        force_retire: bool = False,
        upstream_close_code: int | None = None,
        response_events_seen: int | None = None,
        transport_classification: str | None = None,
        retry_circuit_attempt_selection: _HTTPBridgeRetryCircuitAttemptSelection | None = None,
        account_neutral_transport_drop: bool = False,
    ) -> bool:
        session.closed = True
        async with session.pending_lock:
            failed_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            session.queued_request_count = max(0, session.queued_request_count - failed_pending_count)
            observed_response_events = max(
                (getattr(request_state, "response_event_count", 0) for request_state in session.pending_requests),
                default=0,
            )
            pending_request_states = list(session.pending_requests)
        if retry_circuit_attempt_selection is None:
            retry_circuit_attempt_selection = _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                pending_request_states
            )
        retry_circuit_attempt_kwargs = {
            "retry_circuit_attempt_selection": retry_circuit_attempt_selection,
        }
        # The #1534 wedge shape: a reattached stream that streamed response
        # events whose ``response.created`` was never assigned. The eventless
        # watchdog and the durable-anchor clear both key on
        # ``response_event_count == 0`` and never trip on it, so quarantine
        # the session here so later requests stop re-attaching to it.
        _record_http_bridge_quarantine_wedged_pending(self, session, pending_request_states)
        observed_close_code = (
            upstream_close_code if upstream_close_code is not None else session.last_upstream_close_code
        )
        observed_response_events = (
            response_events_seen if response_events_seen is not None else observed_response_events
        )
        close_classification = (
            _classify_upstream_close(observed_close_code, response_events_seen=observed_response_events)
            if observed_close_code is not None
            else None
        )
        _log_http_bridge_event(
            "reader_failure",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=failed_pending_count,
            detail=error_code,
            error_message=_truncate_identifier(error_message),
            upstream_close_code=observed_close_code,
            response_events_seen=observed_response_events,
            transport_classification=transport_classification
            or (
                f"websocket_close_{close_classification}"
                if close_classification is not None
                else "websocket_transport_error"
            ),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        # Draining-only requests no longer count against the queue, but their
        # event-batcher contexts still belong to the disconnected operation
        # and must be discarded just like ordinary pending requests.
        operation_states: list[Any] = [
            request_state for request_state in pending_request_states if getattr(request_state, "operation_id", None)
        ]
        # Remove the disconnected attempt's in-memory spool before publishing
        # UNKNOWN/ACKNOWLEDGED state. A same-replica reconnect may reclaim the
        # operation as soon as that state is visible; discarding afterward
        # could then delete the replacement attempt's events.
        discard_operation = getattr(
            getattr(self, "_http_bridge_operation_event_batcher", None),
            "discard_operation",
            None,
        )
        if callable(discard_operation):
            for request_state in operation_states:
                operation_id = getattr(request_state, "operation_id", None)
                if operation_id:
                    await discard_operation(operation_id=operation_id)
        for request_state in operation_states:
            # A shared websocket can carry several logical response.create
            # requests. Classify each operation from its own event count;
            # using the session-wide maximum would mark an eventless
            # sibling as safely retryable after another request streamed.
            operation_state = "unknown" if getattr(request_state, "response_event_count", 0) == 0 else "acknowledged"
            await _update_http_bridge_operation_state(
                self,
                session,
                request_state,
                state=operation_state,
            )
        if force_retire and retire_detail:
            _log_http_bridge_event(
                retire_detail,
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                pending_count=failed_pending_count,
                detail=retire_detail,
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
        try:
            reservations_settled = await self._fail_pending_websocket_requests(
                account=session.account,
                account_id_value=session.account.id,
                pending_requests=session.pending_requests,
                pending_lock=session.pending_lock,
                error_code=error_code,
                error_message=error_message,
                api_key=None,
                response_create_gate=session.response_create_gate,
                penalize_account=penalize_account,
            )
            if (
                failed_pending_count > 0
                and reservations_settled is not False
                and observed_response_events == 0
                and (
                    retire_detail == _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL
                    or account_neutral_transport_drop
                )
            ):
                # Only penalize the account after pending-request cleanup has
                # settled its API-key reservations. A failed release must not
                # be hidden behind an already-recorded timeout health signal.
                # Account-neutral abrupt drops share the same windowed signal:
                # one drop is infrastructure noise, but repeated eventless
                # drops on the same account remain evidence of an account-side
                # fault and must still drain it (issue #1754).
                await _record_http_bridge_account_timeout_signal(
                    self,
                    session,
                    detail=(
                        "eventless_transport_drop"
                        if account_neutral_transport_drop
                        else _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL
                    ),
                )
        finally:
            if session.admission_waiter_count > 0 and not force_retire:
                retry_circuit_detail = None
                if close_classification == "clean":
                    retry_circuit_detail = "clean_close"
                elif observed_response_events == 0:
                    retry_circuit_detail = next(
                        (
                            detail
                            for detail in (retire_detail, error_code)
                            if detail in {"stream_incomplete", "stream_idle_timeout", "upstream_keepalive_timeout"}
                        ),
                        None,
                    )
                # Mirror the terminal, grouped, and direct-retirement paths:
                # a failed request that still holds a verified safe replay is
                # about to re-dispatch and claim the circuit generation, so it
                # must not strike the circuit or reach the poison clear here
                # either. A pre-drain handoff with no states keeps striking.
                pending_states_present = [state for state in pending_request_states if state is not None]
                reader_strike_eligible = not pending_states_present or any(
                    not _http_bridge_request_state_holds_safe_replay(state) for state in pending_states_present
                )
                reader_retired = False
                reader_cancellation: asyncio.CancelledError | None = None
                if failed_pending_count > 0 and retry_circuit_detail is not None and reader_strike_eligible:
                    reader_strike_detail = retry_circuit_detail

                    async def _reader_strike_and_clear() -> bool:
                        consecutive_failures = (
                            await self._record_http_bridge_retry_circuit_failure_for_attempt_selection(
                                session,
                                detail=reader_strike_detail,
                                selection=retry_circuit_attempt_selection,
                            )
                        )
                        poison_candidate_detail = await self._http_bridge_effective_anchor_poison_detail(
                            session, reader_strike_detail
                        )
                        poison_episode = None
                        poison_expected_anchor: object = _POISON_ANCHOR_CAPTURE_UNAVAILABLE
                        if poison_candidate_detail is not None and observed_response_events == 0:
                            # The consult returns the exact episode it
                            # validated; the marker below scopes to it.
                            poison_episode, poison_expected_anchor = await self._http_bridge_poison_anchor_clear_owed(
                                session,
                                consecutive_failures=consecutive_failures,
                                configured_threshold=(
                                    _service_get_settings().http_responses_session_bridge_anchor_poison_failure_threshold
                                ),
                            )
                        if poison_candidate_detail is None or poison_episode is None:
                            return False
                        durable_cleared = await _abandon_durable_http_bridge_continuity(
                            self,
                            session,
                            detail=poison_candidate_detail,
                            settle_circuit=_http_bridge_abandonment_may_settle_circuit(pending_request_states),
                            expected_continuity=poison_expected_anchor,
                            authorized_episode=poison_episode,
                        )
                        if not durable_cleared:
                            _log_http_bridge_event(
                                "durable_anchor_poison_clear_failed",
                                session.key,
                                account_id=session.account.id,
                                model=session.request_model,
                                pending_count=session.admission_waiter_count,
                                detail=poison_candidate_detail,
                                cache_key_family=session.key.affinity_kind,
                                model_class=(
                                    _extract_model_class(session.request_model) if session.request_model else None
                                ),
                            )
                            return False
                        await self._http_bridge_mark_poison_anchor_cleared(session, episode=poison_episode)
                        await self._retire_stale_pending_http_bridge_session(
                            session,
                            detail=poison_candidate_detail,
                            response_events_seen=observed_response_events,
                            # Reader-failure retirement must never revive: the
                            # pending turns were already terminally failed and
                            # this reader is condemned, so a post-suspension
                            # liveness signal (which durable-anchor
                            # rehydration can spoof without upstream evidence)
                            # would only leave a readerless session registered.
                            allow_liveness_revive=False,
                            **retry_circuit_attempt_kwargs,
                        )
                        return True

                    # The failed requests are already drained and finalized,
                    # so a cancellation escaping the strike, the episode
                    # consult, or the rebind would leave an at-threshold
                    # poisoned anchor stored with no retirement left to retry
                    # the abandonment. Defer cancellation across the whole
                    # settlement like the grouped path, then re-raise it.
                    reader_settlement_task = asyncio.create_task(
                        _reader_strike_and_clear(),
                        name=f"http-bridge-reader-poison-settlement-{session.durable_session_id}",
                    )
                    reader_retired, reader_cancellation = await _await_task_deferring_cancellation(
                        reader_settlement_task
                    )
                if reader_retired is True:
                    force_retire = True
                    if reader_cancellation is not None:
                        raise reader_cancellation
                else:
                    if reader_cancellation is not None:
                        raise reader_cancellation
                    _log_http_bridge_event(
                        "retire_deferred_for_admission_waiter",
                        session.key,
                        account_id=session.account.id,
                        model=session.request_model,
                        pending_count=session.admission_waiter_count,
                        detail=retire_detail or error_code,
                        cache_key_family=session.key.affinity_kind,
                        model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    )
            else:
                if close_classification == "clean" and failed_pending_count > 0:
                    waiterless_retirement = self._retire_stale_pending_http_bridge_session(
                        session,
                        detail=error_code,
                        retry_circuit_detail="clean_close",
                        response_events_seen=observed_response_events,
                        retired_request_count=failed_pending_count,
                        retired_request_states=pending_request_states,
                        # See the poison branch above: reader-failure
                        # retirement never revives a condemned session.
                        allow_liveness_revive=False,
                        **retry_circuit_attempt_kwargs,
                    )
                else:
                    waiterless_retirement = self._retire_stale_pending_http_bridge_session(
                        session,
                        detail=retire_detail or error_code,
                        response_events_seen=observed_response_events,
                        # ``_fail_pending_websocket_requests`` has already
                        # claimed and drained these states. Carry the count
                        # and the pre-drain state snapshot sampled under
                        # ``pending_lock`` across that ownership transfer, so
                        # normal reader failures still consume one strike and
                        # a drained safe-replay holder still blocks the
                        # settlement. The deferred/poison branch records its
                        # own strike above and intentionally does not pass it.
                        retired_request_count=failed_pending_count,
                        retired_request_states=pending_request_states,
                        # See the poison branch above: reader-failure
                        # retirement never revives a condemned session.
                        allow_liveness_revive=False,
                        **retry_circuit_attempt_kwargs,
                    )
                # The failed requests are already drained and finalized, so a
                # relay cancellation escaping this direct retirement would
                # skip the waiterless strike, the episode consult, the
                # poisoned-anchor abandonment, and the detach/close work with
                # no remaining request lifecycle to retry them. Defer
                # cancellation across the whole retirement like the
                # admission-waiter branch above, then re-raise it.
                waiterless_retirement_task = asyncio.create_task(
                    waiterless_retirement,
                    name=f"http-bridge-waiterless-retirement-{session.durable_session_id}",
                )
                _waiterless_result, waiterless_cancellation = await _await_task_deferring_cancellation(
                    waiterless_retirement_task
                )
                if waiterless_cancellation is not None:
                    raise waiterless_cancellation
        return force_retire or session.admission_waiter_count == 0

    async def _relay_http_bridge_upstream_messages(
        self: Any,
        session: "_HTTPBridgeSession",
    ) -> None:
        runtime_settings = _service_get_settings()
        relay_upstream = session.upstream
        receive_task: asyncio.Task[UpstreamWebSocketMessage] | None = None
        wakeup_task: asyncio.Task[bool] | None = None
        reader_failure_retry_circuit_attempt_selection: _HTTPBridgeRetryCircuitAttemptSelection | None = None
        try:
            while True:
                reader_failure_retry_circuit_attempt_selection = None
                # Clear before taking the deadline snapshot. A send before the
                # clear is represented by its timestamp; a send after it leaves
                # the event set and wakes the persistent receive wait below.
                session.upstream_reader_wakeup.clear()
                # The wakeup waiter is reused across iterations while it is
                # still pending. A set() that landed while the previous
                # message was being processed completed it; that send is
                # already represented in the snapshot below, so consume the
                # fired waiter here without awaiting and re-arm it before the
                # next wait.
                if wakeup_task is not None and wakeup_task.done():
                    wakeup_task.result()
                    wakeup_task = None
                receive_timeout = await self._next_websocket_receive_timeout(
                    session.pending_requests,
                    pending_lock=session.pending_lock,
                    proxy_request_budget_seconds=_http_bridge_request_budget_seconds(runtime_settings),
                    stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                )
                stuck_gate_retire_after_seconds = float(
                    getattr(
                        runtime_settings,
                        "http_responses_session_bridge_stuck_gate_retire_after_seconds",
                        300.0,
                    )
                )
                receive_timeout = await _http_bridge_receive_timeout_with_eventless_deadline(
                    session,
                    receive_timeout,
                    now=_service_time().monotonic(),
                    stuck_gate_retire_after_seconds=stuck_gate_retire_after_seconds,
                )
                if receive_task is None:
                    receive_task = asyncio.create_task(session.upstream.receive())

                message: UpstreamWebSocketMessage | None = None
                timed_out = False
                if receive_task.done():
                    message = receive_task.result()
                    receive_task = None
                elif receive_timeout is not None and receive_timeout.timeout_seconds <= 0:
                    timed_out = True
                else:
                    # Event.wait() waiters are level-triggered: a waiter
                    # registered before clear() still fires on the next set(),
                    # so a pending waiter stays valid across iterations and is
                    # only re-created after it fires. Cancelling it per
                    # message cost a create_task + sleep(0) + cancel + timed
                    # asyncio.wait round trip; the finally below cancels the
                    # long-lived waiter once at loop exit.
                    if wakeup_task is None:
                        wakeup_task = asyncio.create_task(session.upstream_reader_wakeup.wait())
                    done, _pending = await asyncio.wait(
                        (receive_task, wakeup_task),
                        timeout=receive_timeout.timeout_seconds if receive_timeout is not None else None,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if receive_task in done:
                        message = receive_task.result()
                        receive_task = None
                    elif wakeup_task in done:
                        wakeup_task.result()
                        wakeup_task = None
                        continue
                    else:
                        timed_out = True

                if timed_out:
                    if receive_timeout is None:
                        raise RuntimeError("HTTP bridge reader timed out without a timeout contract")
                    if receive_timeout.error_code == _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL:
                        if receive_task is not None and receive_task.done():
                            continue
                        async with session.lifecycle_lock:
                            # Send-failure cleanup marks the session closed and
                            # disarms the timestamp while holding this lock.
                            # Do not race that caller's terminal settlement.
                            if session.closed:
                                continue
                            now = _service_time().monotonic()
                            async with session.pending_lock:
                                if receive_task is not None and receive_task.done():
                                    continue
                                expired_request_states = [
                                    request_state
                                    for request_state in session.pending_requests
                                    if (
                                        deadline := _http_bridge_eventless_precreated_deadline(
                                            request_state,
                                            stuck_gate_retire_after_seconds=stuck_gate_retire_after_seconds,
                                        )
                                    )
                                    is not None
                                    and deadline <= now
                                ]
                                if not expired_request_states:
                                    continue
                                expired_retry_circuit_attempt_selection = (
                                    _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                                        expired_request_states
                                    )
                                )
                                reader_failure_retry_circuit_attempt_selection = expired_retry_circuit_attempt_selection
                                pending_count = len(session.pending_requests)
                                # A delta-only request has no other way to
                                # convey prior context once its anchor is
                                # cleared, so only clear anchors codex-lb
                                # injected onto a full-resend-shaped payload.
                                expired_proxy_injected_anchor = any(
                                    request_state.proxy_injected_previous_response_id
                                    and request_state.proxy_injected_anchor_had_full_resend_payload
                                    for request_state in expired_request_states
                                )

                            # Do not mutate durable continuity while a receive
                            # may still deliver the response event that proves
                            # this timeout stale. The non-cancellable branch
                            # remains fail-closed, so it clears immediately
                            # before its forced retirement.
                            force_retire = False
                            if receive_task is not None:
                                receive_cancelled = await _cancel_http_bridge_reader_child(
                                    receive_task,
                                    label="HTTP bridge upstream receive after missing response.created",
                                    cleanup_tasks=self._background_cleanup_tasks,
                                )
                                if receive_task.done() and not receive_task.cancelled():
                                    # A response (or a typed receive failure)
                                    # won the race with timeout handling. Let
                                    # the normal reader path settle it.
                                    continue
                                force_retire = not receive_cancelled and not receive_task.cancelled()
                                if not force_retire:
                                    receive_task = None
                            async with session.pending_lock:
                                for request_state in session.pending_requests:
                                    if request_state.failure_phase_override is None:
                                        request_state.failure_phase_override = "upstream"
                                    if request_state.failure_detail_override is None:
                                        request_state.failure_detail_override = (
                                            _HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL
                                        )
                            if force_retire:
                                # Do not reconnect while the old receive task
                                # still owns the superseded socket. The ordinary
                                # timeout path takes the same fail-closed branch;
                                # retain the explicit account-neutral timeout
                                # classification rather than routing through the
                                # generic reader-crash account penalty path.
                                if expired_proxy_injected_anchor:
                                    await _clear_durable_http_bridge_response_anchor(self, session)
                                _record_http_bridge_quarantine_eventless_timeout(self, session)
                                session.closed = True
                                await self._fail_http_bridge_reader_and_maybe_retire(
                                    session,
                                    error_code="upstream_request_timeout",
                                    error_message=receive_timeout.error_message,
                                    penalize_account=False,
                                    retire_detail=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
                                    force_retire=True,
                                    retry_circuit_attempt_selection=expired_retry_circuit_attempt_selection,
                                )
                                break
                            # A successfully cancelled receive cannot deliver
                            # a late response event, so this is the safe point
                            # to persist the durable-anchor invalidation.
                            if expired_proxy_injected_anchor:
                                await _clear_durable_http_bridge_response_anchor(self, session)
                            # Count the eventless retire toward the repeated-
                            # wedge quarantine; the first strike still goes
                            # through the bounded pre-created recovery below.
                            _record_http_bridge_quarantine_eventless_timeout(self, session)
                            _record_http_bridge_stuck_retire(
                                reason=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
                                session=session,
                            )
                            _log_http_bridge_event(
                                "missing_response_created_timeout",
                                session.key,
                                account_id=session.account.id,
                                model=session.request_model,
                                pending_count=pending_count,
                                detail=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
                                cache_key_family=session.key.affinity_kind,
                                model_class=(
                                    _extract_model_class(session.request_model) if session.request_model else None
                                ),
                            )
                            # A fresh, self-contained hard request can use the
                            # same bounded pre-created recovery as the idle
                            # timeout path. Keep the session open until the
                            # recovery routine claims the handoff; otherwise
                            # its retry gate would reject the request as
                            # already retired. Continuity-bound requests still
                            # fail closed in _retry_http_bridge_precreated_request.
                            retried = await self._retry_http_bridge_precreated_request(session)
                            if retried:
                                continue
                            session.closed = True
                            await self._fail_http_bridge_reader_and_maybe_retire(
                                session,
                                error_code="upstream_request_timeout",
                                error_message=receive_timeout.error_message,
                                penalize_account=False,
                                retire_detail=_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL,
                                force_retire=True,
                                retry_circuit_attempt_selection=expired_retry_circuit_attempt_selection,
                            )
                        break

                    async with session.pending_lock:
                        retry_circuit_attempt_selection = (
                            _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                                tuple(session.pending_requests)
                            )
                        )
                        reader_failure_retry_circuit_attempt_selection = retry_circuit_attempt_selection
                    if receive_task is not None:
                        receive_cancelled = await _cancel_http_bridge_reader_child(
                            receive_task,
                            label="HTTP bridge upstream receive after timeout",
                            cleanup_tasks=self._background_cleanup_tasks,
                        )
                        if not receive_cancelled:
                            raise RuntimeError("HTTP bridge upstream receive did not cancel after timeout")
                        receive_task = None
                    retried = await self._retry_http_bridge_precreated_request(session)
                    if retried:
                        continue
                    async with session.lifecycle_lock:
                        await self._fail_http_bridge_reader_and_maybe_retire(
                            session,
                            error_code=receive_timeout.error_code,
                            error_message=receive_timeout.error_message,
                            retry_circuit_attempt_selection=retry_circuit_attempt_selection,
                        )
                    break

                if message is None:
                    raise RuntimeError("HTTP bridge upstream receive completed without a message")
                if message.kind == "text" and message.text is not None:
                    session.last_upstream_close_code = None
                    if EVENT_MARKER in message.text:
                        publish_live_usage(
                            parse_rate_limit_event_text(message.text),
                            account_id=session.account.id,
                            chatgpt_account_id=session.account.chatgpt_account_id,
                        )
                    await self._process_http_bridge_upstream_text(session, message.text)
                    if await self._retire_http_bridge_after_drain_if_ready(session):
                        break
                    continue

                async with session.pending_lock:
                    archive_request_state = session.pending_requests[0] if len(session.pending_requests) == 1 else None
                    response_events_seen = max(
                        (request_state.response_event_count for request_state in session.pending_requests),
                        default=0,
                    )
                    # Buffered reasoning preludes are suppressed from
                    # response_event_count on purpose, but they are still
                    # application-layer output: a drop after one is not an
                    # eventless drop for account-health purposes.
                    upstream_output_observed = any(
                        getattr(request_state, "upstream_model_output_seen", False)
                        for request_state in session.pending_requests
                    )
                    reader_failure_retry_circuit_attempt_selection = (
                        _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                            tuple(session.pending_requests)
                        )
                    )
                _archive_http_bridge_upstream_message(session, message, archive_request_state)
                session.last_upstream_close_generation += 1
                session.last_upstream_close_code = message.close_code
                retried = False
                # Account-neutral transport failures do not prove that the
                # upstream rejected response.create. The request may still be
                # executing, so replay could duplicate work, billing, or tool
                # side effects. Clean closes remain eligible for the bounded
                # pre-created retry circuit maintained by the session.
                account_neutral = is_account_neutral_websocket_error_code(message.error_code)
                if not account_neutral:
                    retried = await self._retry_http_bridge_precreated_request(session)
                if retried:
                    continue
                close_classification = (
                    _classify_upstream_close(message.close_code, response_events_seen=response_events_seen)
                    if message.close_code is not None
                    else None
                )
                # An abrupt drop with no close frame and no response events is
                # weaker account-health evidence than a graceful pre-created
                # close, which is already exempted below. Keep the individual
                # drop account-neutral; repeated eventless drops still feed
                # the windowed account drain signal inside the failure path.
                # Only terminal transport messages qualify: a protocol-invalid
                # binary frame also carries no close code but did not end the
                # socket, so it keeps the existing penalty semantics.
                account_neutral_transport_drop = (
                    message.kind in ("close", "error")
                    and not account_neutral
                    and not upstream_output_observed
                    and _is_account_neutral_transport_drop(
                        message.close_code, response_events_seen=response_events_seen
                    )
                )
                async with session.lifecycle_lock:
                    if (
                        session.liveness_settlement_owner == "send"
                        and message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE
                    ):
                        # A submitter publishes this dedicated claim beside the
                        # failing send while holding lifecycle_lock. ``closed``
                        # alone is only an admission/retirement state and must
                        # never suppress settlement of still-pending siblings.
                        break
                    await self._fail_http_bridge_reader_and_maybe_retire(
                        session,
                        error_code=message.error_code or "stream_incomplete",
                        error_message=_upstream_websocket_disconnect_message(message),
                        upstream_close_code=message.close_code,
                        response_events_seen=response_events_seen,
                        transport_classification=(
                            f"websocket_close_{close_classification}"
                            if close_classification is not None
                            else "websocket_transport_error"
                        ),
                        retry_circuit_attempt_selection=reader_failure_retry_circuit_attempt_selection,
                        penalize_account=(
                            not account_neutral
                            and not account_neutral_transport_drop
                            and not (message.kind == "close" and close_classification == "clean")
                        ),
                        account_neutral_transport_drop=account_neutral_transport_drop,
                        **(
                            # An admission waiter must not inherit a socket whose
                            # heartbeat already proved it dead. Other failures
                            # preserve the existing deferred-retirement handoff.
                            {"force_retire": True}
                            if message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE
                            else {}
                        ),
                    )
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if reader_failure_retry_circuit_attempt_selection is None:
                # A receive/processing exception can jump here before the
                # ordinary timeout or close branches publish their snapshot.
                # Capture before waiting for lifecycle ownership so a
                # concurrent recovery cannot replace the failed physical send.
                async with session.pending_lock:
                    reader_failure_retry_circuit_attempt_selection = (
                        _http_bridge_retry_circuit_attempt_selection_for_pending_requests(
                            tuple(session.pending_requests)
                        )
                    )
            logger.warning(
                "HTTP bridge upstream reader crashed account_id=%s bridge_kind=%s",
                session.account.id,
                session.key.affinity_kind,
                exc_info=True,
            )
            error_code = exc.error_code if isinstance(exc, UpstreamWebSocketTransportError) else "stream_incomplete"
            account_neutral = is_account_neutral_websocket_error_code(error_code)
            async with session.lifecycle_lock:
                if not (
                    session.liveness_settlement_owner == "send"
                    and error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE
                ):
                    # Match the message path above when receive() raises while
                    # a concurrent send failure already owns settlement.
                    await self._fail_http_bridge_reader_and_maybe_retire(
                        session,
                        error_code=error_code,
                        error_message=(
                            str(exc)
                            if isinstance(exc, UpstreamWebSocketTransportError)
                            else "HTTP bridge upstream reader crashed before response.completed"
                        ),
                        penalize_account=not account_neutral,
                        retry_circuit_attempt_selection=reader_failure_retry_circuit_attempt_selection,
                        # Preserve ordinary crash handoff behavior, but never hand
                        # a heartbeat-expired socket to an admission waiter.
                        **({"force_retire": True} if error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE else {}),
                    )
        finally:
            # Mark the socket this reader owned closed before the child
            # cancellations below suspend: the persistent wakeup waiter is
            # normally still pending here, and a recovery that replaces the
            # socket during that suspension clears the flag itself after
            # swapping ``session.upstream``.
            if session.upstream is relay_upstream:
                session.closed = True
            await _cancel_http_bridge_reader_child(
                wakeup_task,
                label="HTTP bridge reader wakeup wait",
                cleanup_tasks=self._background_cleanup_tasks,
            )
            await _cancel_http_bridge_reader_child(
                receive_task,
                label="HTTP bridge upstream receive",
                cleanup_tasks=self._background_cleanup_tasks,
            )

    async def _process_http_bridge_upstream_text(
        self: Any,
        session: "_HTTPBridgeSession",
        text: str,
    ) -> None:
        # One JSON document per websocket text frame: parse it directly instead
        # of framing it as SSE and running the line parser over it. The
        # data-only block is what unmatched events relay.
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json_text(text)
        event_type = classify_event_type(payload)
        event = parse_sse_event_payload(payload) if event_type in _LIFECYCLE_EVENT_TYPES else None
        completed_delivery_scope = _HTTPBridgeCompletedDeliveryScope() if event_type == "response.completed" else None
        claimed_terminal_request_states: list[_WebSocketRequestState] = []
        try:
            await self._process_parsed_http_bridge_upstream_event(
                session,
                text=text,
                event_block=event_block,
                payload=payload,
                event=event,
                event_type=event_type,
                completed_delivery_scope=completed_delivery_scope,
                claimed_terminal_request_states=claimed_terminal_request_states,
            )
        except BaseException:
            # Includes CancelledError. A terminal request popped from
            # session.pending_requests is owned exclusively by this
            # bookkeeping continuation: the reader's failure path and the
            # downstream detach backstop only settle requests still in
            # pending ownership, so an abort here would otherwise leak the
            # API-key reservation and its heartbeat forever (issue #1594).
            await self._settle_aborted_http_bridge_terminal_states(
                session,
                claimed_terminal_request_states,
            )
            raise
        else:
            for claimed_request_state in claimed_terminal_request_states:
                claimed_request_state.terminal_settlement_phase = None
        finally:
            if completed_delivery_scope is not None:
                completed_delivery_scope.active = False

    async def _settle_aborted_http_bridge_terminal_states(
        self: Any,
        session: "_HTTPBridgeSession",
        request_states: list[_WebSocketRequestState],
    ) -> None:
        """Settle claimed-but-unfinalized terminal requests after an abort.

        Once terminal-event bookkeeping pops a request from
        ``session.pending_requests`` it is the sole owner of that request's
        reservation settlement. If the bookkeeping continuation raises or is
        cancelled before ``_finalize_websocket_request_state`` transfers the
        settlement, nothing else can reach the request: the orphaned
        reservation heartbeat would keep refreshing ``updated_at`` and defeat
        the stale-reservation janitor permanently. Settle those requests here
        under a shielded scope. Requests a retry branch restored to pending
        ownership are skipped; they are reachable by ordinary cleanup again.
        Settlement is idempotent (release only flips ``status == "reserved"``),
        so overlapping with an already-transferred finalize is safe.
        """
        if not request_states:
            return
        with anyio.CancelScope(shield=True):
            for request_state in request_states:
                if request_state.terminal_settlement_phase is None:
                    continue
                async with session.pending_lock:
                    if request_state in session.pending_requests:
                        request_state.terminal_settlement_phase = None
                        continue
                request_state.terminal_settlement_phase = "abandoned"
                try:
                    self._cancel_request_state_api_key_reservation_heartbeat(request_state)
                    await self._release_websocket_request_state_reservation(request_state)
                    request_state.api_key_reservation = None
                    request_state.terminal_settlement_phase = None
                except Exception:
                    # Leave the "abandoned" marker so the downstream detach
                    # backstop can retry the settlement later.
                    logger.warning(
                        "Failed to settle aborted HTTP bridge terminal request request_id=%s account_id=%s",
                        request_state.request_log_id or request_state.request_id,
                        session.account.id,
                        exc_info=True,
                    )
                # Best effort: unblock the downstream waiter so it observes
                # end-of-stream instead of waiting for its idle timeout.
                event_queue = request_state.event_queue
                if event_queue is not None:
                    try:
                        event_queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

    async def _handle_or_defer_precreated_stream_health(
        self: Any,
        request_state: _WebSocketRequestState | None,
        account: Account,
        error: UpstreamError,
        code: str,
    ) -> None:
        """Write account health now, or defer it until reservation settlement.

        Keyed pre-created retry branches run while the request's API-key
        reservation is still open. An immediate ``_handle_stream_error`` would
        mutate account health before the reservation settles, violating the
        settlement-ordering invariant (api-keys spec: the health write waits
        for settlement, and unconfirmed settlement leaves health unapplied).
        Queue the classified write on the request state instead;
        ``_drain_deferred_keyed_stream_health`` applies it after settlement or
        fallback release commits. Unkeyed requests keep the immediate write.
        ``account_health_error_handled`` is set either way so terminal
        finalization does not apply a duplicate penalty.
        """
        if request_state is not None and request_state.api_key_reservation is not None:
            request_state.deferred_keyed_stream_health.append(
                _DeferredKeyedStreamHealthPenalty(account=account, error=error, code=code)
            )
        else:
            await self._handle_stream_error(account, error, code)
        if request_state is not None:
            setattr(request_state, "account_health_error_handled", True)

    async def _process_parsed_http_bridge_upstream_event(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        text: str,
        event_block: str,
        payload: dict[str, JsonValue] | None,
        event: OpenAIEvent | None,
        event_type: str | None,
        completed_delivery_scope: _HTTPBridgeCompletedDeliveryScope | None,
        claimed_terminal_request_states: list[_WebSocketRequestState],
    ) -> None:
        original_text = text
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
            event=event,
        )

        completed_event_queue: asyncio.Queue[str | None] | None = None
        completed_event_queue_claimed = False
        async with session.pending_lock:
            matched_request_state = None
            created_request_state = None
            suppress_downstream_event = False
            deferred_reasoning_prelude_event = False
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

            _archive_http_bridge_upstream_text(session, original_text, matched_request_state)
            pending_request_count = len(session.pending_requests)

            if matched_request_state is not None:
                # The deferred reasoning prelude intentionally skips ordinary
                # response-event accounting below, but it still proves that the
                # physical response.create received an upstream response. Publish
                # that attempt transition before any later recovery await can
                # classify the send as eventless.
                _mark_response_create_attempt_observed(matched_request_state, event_type)
                session.last_upstream_event_generation += 1
                now = _service_time().monotonic()
                if matched_request_state.latency_first_upstream_event_ms is None:
                    matched_request_state.latency_first_upstream_event_ms = int(
                        max(0.0, now - matched_request_state.started_at) * 1000
                    )
                if event_type == "response.created" and matched_request_state.latency_response_created_ms is None:
                    matched_request_state.latency_response_created_ms = int(
                        max(0.0, now - matched_request_state.started_at) * 1000
                    )
                actual_service_tier = _service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    matched_request_state.actual_service_tier = actual_service_tier
                    matched_request_state.service_tier = actual_service_tier
                _record_http_bridge_tool_call_lifecycle(
                    matched_request_state,
                    event_type=event_type,
                    payload=payload,
                )
                completed_tool_call = _response_output_item_done_tool_call(payload)
                if completed_tool_call is not None:
                    completed_call_id, completed_call_type = completed_tool_call
                    if completed_call_id not in matched_request_state.pending_function_call_ids:
                        matched_request_state.pending_function_call_ids.append(completed_call_id)
                    matched_request_state.pending_tool_call_types[completed_call_id] = completed_call_type
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
                    rewritten_payload = _rewrite_websocket_downstream_response_id(payload, matched_request_state)
                    # ``text`` is the serialization ``payload`` was parsed from (or the
                    # tool-call rewrite's compact re-dump). Relaying it verbatim is
                    # only valid while nothing above mutated ``payload`` in place:
                    # ``mark_duplicate_tool_call_downstream_event`` trims partially
                    # duplicated ``multi_tool_use.parallel`` arguments on
                    # ``response.output_item.done`` items without returning a new
                    # object, so those (rare, per-item) events are always
                    # re-serialized. Every other pre-framing step is read-only.
                    if rewritten_payload is not payload or event_type == "response.output_item.done":
                        payload = rewritten_payload
                        event_block = format_sse_event(payload)
                    else:
                        # Identity fast path: nothing changed, so frame the
                        # upstream JSON text instead of serializing the dict again.
                        event_block = format_sse_event_from_text(payload, text)
                if _websocket_should_defer_reasoning_prelude(matched_request_state, event_type, payload):
                    matched_request_state.deferred_reasoning_downstream_texts.append(event_block)
                    matched_request_state.last_upstream_activity_at = now
                    matched_request_state.upstream_model_output_seen = True
                    suppress_downstream_event = True
                    deferred_reasoning_prelude_event = True
                elif event_type in _MODEL_OUTPUT_EVENT_TYPES:
                    matched_request_state.upstream_model_output_seen = True
                    if not suppress_downstream_event:
                        matched_request_state.downstream_visible = True

            terminal_request_state = None
            if event_type in {"response.completed", "response.failed", "response.incomplete", "error"}:
                early_retry_error_code = _websocket_precreated_retry_error_code(
                    matched_request_state,
                    event_type=event_type,
                    payload=payload,
                    has_other_pending_requests=any(
                        pending_request is not matched_request_state for pending_request in session.pending_requests
                    ),
                )
                reserve_terminal_for_model_capacity_retry = bool(
                    matched_request_state is not None
                    and early_retry_error_code is not None
                    and early_retry_error_code != _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
                    and not is_previous_response_not_found_event
                    and is_upstream_model_capacity_error(error_message)
                    and _websocket_request_can_replay_before_visible_output(matched_request_state)
                )
                if reserve_terminal_for_model_capacity_retry:
                    terminal_request_state = matched_request_state
                else:
                    terminal_request_state = _pop_terminal_websocket_request_state(
                        session.pending_requests,
                        response_id=response_id,
                        fallback_request_state=matched_request_state,
                        prefer_previous_response_not_found=is_previous_response_not_found_event
                        or is_missing_tool_output_event,
                        previous_response_id_hint=previous_response_id_hint,
                        error_message=error_message,
                        allow_unanchored_previous_response_error=is_previous_response_not_found_event,
                        allow_precreated_terminal_fallback=True,
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
                if (
                    terminal_request_state is not None
                    and not reserve_terminal_for_model_capacity_retry
                    and _http_bridge_request_counts_against_queue(terminal_request_state)
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
                has_other_pending_requests = any(
                    pending_request is not terminal_request_state for pending_request in session.pending_requests
                )
                if (
                    event_type == "response.completed"
                    and terminal_request_state is not None
                    and terminal_request_state not in session.pending_requests
                ):
                    completed_event_queue = terminal_request_state.event_queue
                    completed_event_queue_claimed = True
                    if completed_event_queue is not None and completed_delivery_scope is not None:
                        completed_delivery_scope.active = True
                        terminal_request_state.completed_delivery_scope = completed_delivery_scope

            # Every request popped from pending ownership above is now owned
            # exclusively by this bookkeeping continuation. Record the claim
            # (still under pending_lock) so an abort before finalization can
            # settle the reservation instead of leaking it (issue #1594).
            for claimed_request_state in (terminal_request_state, *grouped_previous_response_request_states):
                if claimed_request_state is not None and claimed_request_state not in session.pending_requests:
                    claimed_request_state.terminal_settlement_phase = "claimed"
                    if claimed_request_state not in claimed_terminal_request_states:
                        claimed_terminal_request_states.append(claimed_request_state)

        if len(grouped_previous_response_request_states) > 1:
            session.upstream_control.reconnect_requested = True
            if is_previous_response_not_found_event:
                # This branch settles every request that shared the denied
                # anchor and then returns, so the single-request retirement
                # below is never reached for a fan-out denial.
                await _retire_denied_http_bridge_anchor(
                    self,
                    session,
                    request_states=grouped_previous_response_request_states,
                )
            grouped_error_reason = (
                "previous_response_not_found"
                if is_previous_response_not_found_event
                else "missing_tool_output"
                if is_missing_tool_output_event
                else "stream_incomplete"
            )
            grouped_terminal_events = []
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
                grouped_operation_state = _http_bridge_operation_state_for_event(grouped_event_type)
                grouped_terminal_events.append(
                    (
                        grouped_request_state,
                        grouped_event_block,
                        grouped_event,
                        grouped_payload,
                        grouped_event_type,
                        grouped_operation_state,
                    )
                )

            # This grouped settlement returns before the single-request
            # settlement path below, so without recording here a multi-request
            # continuity failure fails every grouped request with a synthetic
            # terminal event and never advances the circuit — leaving the anchor
            # that failed them reusable. The detail is read back off the built
            # event so it matches whatever the single-request path would have
            # recorded for the same reason, and recording precedes the persist
            # and delivery machinery below for the same reason it does there: a
            # client resending on observed completion must not outrun it.
            grouped_poison_strike_failures = 0
            grouped_poison_detail: str | None = None
            for (
                grouped_request_state,
                _grouped_terminal_block,
                grouped_terminal_event,
                _grouped_terminal_payload,
                _grouped_terminal_event_type,
                _grouped_terminal_operation_state,
            ) in grouped_terminal_events:
                # Same admission test the single-request settlement path
                # applies: a request still holding a verified full resend is
                # about to be replayed and claims the circuit generation at
                # dispatch, so charging it here would let two safely
                # replayable requests open the circuit between them and clear
                # the anchor both of them could still have used.
                if grouped_request_state.response_event_count != 0 or _http_bridge_request_state_holds_safe_replay(
                    grouped_request_state
                ):
                    continue
                if grouped_request_state.request_kind == "prewarm" or grouped_request_state.skip_request_log:
                    # Same exclusion the single-request terminal branch
                    # applies: an internal warmup probe carries no anchor
                    # and proves nothing about the key's continuity, and
                    # one warmup plus one client request must not open the
                    # circuit between them after a single real failure.
                    continue
                grouped_terminal_error = (
                    grouped_terminal_event.response.error
                    if grouped_terminal_event is not None and grouped_terminal_event.response is not None
                    else None
                )
                grouped_terminal_detail = _normalize_error_code(
                    grouped_terminal_error.code if grouped_terminal_error else None,
                    grouped_terminal_error.type if grouped_terminal_error else None,
                )
                if grouped_terminal_detail is None:
                    continue
                grouped_strike_failures = await self._record_http_bridge_retry_circuit_failure(
                    session,
                    detail=grouped_terminal_detail,
                    attempt=grouped_request_state.response_create_attempt,
                    terminal_pre_response_frame=True,
                )
                # A fan-out carrying two or more eventless requests advances the
                # circuit through its threshold inside this loop, so the anchor
                # that failed all of them is proven dead here just as it is on
                # the single-request path. Keep the highest count and its poison
                # detail; discarding them left the grouped branch returning with
                # the durable anchor still stored.
                grouped_poison_candidate = await self._http_bridge_effective_anchor_poison_detail(
                    session, grouped_terminal_detail
                )
                if grouped_poison_candidate is not None and grouped_strike_failures is not None:
                    if grouped_strike_failures > grouped_poison_strike_failures:
                        grouped_poison_strike_failures = grouped_strike_failures
                    grouped_poison_detail = grouped_poison_candidate

            append_terminal_batch = getattr(
                getattr(self, "_http_bridge_operation_event_batcher", None),
                "append_terminal_event",
                None,
            )
            append_participants = {
                id(grouped_request_state)
                for grouped_request_state, *_rest in grouped_terminal_events
                if grouped_request_state.operation_id
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
                and callable(append_terminal_batch)
            }
            append_ready = asyncio.Event()
            append_lock = asyncio.Lock()
            append_arrivals = 0
            if not append_participants:
                append_ready.set()

            async def await_all_grouped_appends() -> None:
                nonlocal append_arrivals
                async with append_lock:
                    append_arrivals += 1
                    if append_arrivals == len(append_participants):
                        append_ready.set()
                await append_ready.wait()

            delivery_ready = asyncio.Event()
            delivery_lock = asyncio.Lock()
            delivery_arrivals = 0

            async def await_all_grouped_deliveries() -> None:
                nonlocal delivery_arrivals
                async with delivery_lock:
                    delivery_arrivals += 1
                    if delivery_arrivals == len(grouped_terminal_events):
                        delivery_ready.set()
                await delivery_ready.wait()

            async def persist_one_grouped_terminal_event(
                grouped_terminal_event: tuple[Any, str, OpenAIEvent | None, Any, str | None, str | None],
            ) -> None:
                (
                    grouped_request_state,
                    grouped_event_block,
                    _grouped_event,
                    _grouped_payload,
                    _grouped_event_type,
                    grouped_operation_state,
                ) = grouped_terminal_event
                if id(grouped_request_state) in append_participants:
                    await _persist_http_bridge_operation_event(
                        self,
                        session,
                        grouped_request_state,
                        grouped_event_block,
                        terminal=True,
                        terminal_state=grouped_operation_state,
                        terminal_event_queue=grouped_request_state.event_queue,
                        terminal_append_barrier=await_all_grouped_appends,
                        terminal_delivery_barrier=await_all_grouped_deliveries,
                    )
                else:
                    await append_ready.wait()
                    if grouped_request_state.event_queue is not None:
                        await grouped_request_state.event_queue.put(grouped_event_block)
                        await grouped_request_state.event_queue.put(None)
                    await await_all_grouped_deliveries()
                    await _persist_http_bridge_operation_event(
                        self,
                        session,
                        grouped_request_state,
                        grouped_event_block,
                        terminal=True,
                        terminal_state=grouped_operation_state,
                    )
                if grouped_operation_state is not None and grouped_operation_state != "failed":
                    await _update_http_bridge_operation_state(
                        self,
                        session,
                        grouped_request_state,
                        state=grouped_operation_state,
                        response_id=_websocket_downstream_response_id(grouped_request_state),
                    )

            async def persist_grouped_terminal_events() -> Exception | None:
                first_error: Exception | None = None
                persistence_results = await asyncio.gather(
                    *(persist_one_grouped_terminal_event(item) for item in grouped_terminal_events),
                    return_exceptions=True,
                )
                for persistence_result in persistence_results:
                    if isinstance(persistence_result, Exception) and first_error is None:
                        first_error = persistence_result
                try:
                    for (
                        grouped_request_state,
                        _grouped_event_block,
                        grouped_event,
                        grouped_payload,
                        grouped_event_type,
                        _grouped_operation_state,
                    ) in grouped_terminal_events:
                        try:
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
                        except Exception as exc:
                            if first_error is None:
                                first_error = exc
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                try:
                    # Grouped terminal errors settle detached/abandoned requests
                    # (event_queue is None) with no downstream stream finalizer
                    # left to run, so release the now-idle session's account
                    # stream lease here just like the single terminal path below.
                    await self._maybe_release_idle_http_bridge_session_lease(session)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                return first_error

            grouped_settlement_task = asyncio.create_task(
                persist_grouped_terminal_events(),
                name=f"http-bridge-grouped-terminal-settlement-{session.durable_session_id}",
            )
            grouped_error, grouped_cancellation = await _await_task_deferring_cancellation(grouped_settlement_task)
            if grouped_poison_detail is not None and grouped_poison_strike_failures > 0:
                # The grouped frames have been published by here, so this mirrors
                # the single-request ordering: the strikes precede delivery, the
                # durable clear follows it.
                #
                # This runs under cancellation too. `_await_task_deferring_
                # cancellation` has already waited for every grouped frame and
                # finalization, so the requests this anchor failed are gone and
                # no later retirement can retry the clear for them; skipping it
                # here left the poisoned anchor stored for another replica or
                # for reuse once the local quarantine expired. Defer the
                # cancellation across the write the same way, then re-raise it
                # below unchanged. The clear is internally exception-safe.
                grouped_clear_detail = grouped_poison_detail
                grouped_clear_strike_failures = grouped_poison_strike_failures

                async def _consult_and_clear_grouped_anchor() -> None:
                    # The episode consult is a durable await of its own: run
                    # it inside the same cancellation-deferred task as the
                    # clear, or a reader cancellation landing mid-consult
                    # escapes with the grouped requests already finalized and
                    # no retirement left to retry the abandonment. The
                    # consult reads the live registered episode, so a
                    # sibling settle during grouped publication vetoes the
                    # clear.
                    grouped_poison_episode, grouped_expected_anchor = await self._http_bridge_poison_anchor_clear_owed(
                        session,
                        consecutive_failures=grouped_clear_strike_failures,
                        configured_threshold=(
                            _service_get_settings().http_responses_session_bridge_anchor_poison_failure_threshold
                        ),
                    )
                    if grouped_poison_episode is None:
                        return
                    durable_cleared = await _abandon_durable_http_bridge_continuity(
                        self,
                        session,
                        detail=grouped_clear_detail,
                        expected_continuity=grouped_expected_anchor,
                        authorized_episode=grouped_poison_episode,
                        # A mixed group can hold a member with a verified safe
                        # replay whose dispatch claims the circuit generation;
                        # settling under it would remove the fence it depends
                        # on, same as the retirement funnels.
                        settle_circuit=_http_bridge_abandonment_may_settle_circuit(
                            grouped_previous_response_request_states
                        ),
                    )
                    if not durable_cleared:
                        return
                    # A mixed group's abandonment deliberately leaves the
                    # circuit alive for its safe member, so the episode
                    # survives and must remember its anchor was already
                    # cleared — one poisoned anchor is abandoned once. The
                    # marker belongs inside this owned task: a cancellation
                    # landing between the durable clear and the marker write
                    # would otherwise leave the cleared episode unmarked, and
                    # an ordinary load would re-arm quarantine from the
                    # unchanged surviving row after the safe replay had
                    # already recovered the conversation.
                    await self._http_bridge_mark_poison_anchor_cleared(session, episode=grouped_poison_episode)

                grouped_clear_task = asyncio.create_task(
                    _consult_and_clear_grouped_anchor(),
                    name=f"http-bridge-grouped-anchor-clear-{session.durable_session_id}",
                )
                _grouped_clear_result, grouped_clear_cancellation = await _await_task_deferring_cancellation(
                    grouped_clear_task
                )
                if grouped_cancellation is None:
                    grouped_cancellation = grouped_clear_cancellation
            if grouped_cancellation is not None:
                if grouped_error is not None:
                    logger.warning(
                        "Grouped HTTP bridge terminal finalization failed while preserving cancellation error=%r",
                        grouped_error,
                    )
                raise grouped_cancellation
            if grouped_error is not None:
                raise grouped_error
            return

        if len(grouped_previous_response_request_states) == 1 and terminal_request_state is None:
            terminal_request_state = grouped_previous_response_request_states[0]

        if not deferred_reasoning_prelude_event:
            if matched_request_state is terminal_request_state:
                _record_response_event(matched_request_state, event_type)
            else:
                _record_response_event(matched_request_state, event_type)
                _record_response_event(terminal_request_state, event_type)

        status_request_state = terminal_request_state or matched_request_state
        if status_request_state is None and is_previous_response_not_found_event:
            session.upstream_control.reconnect_requested = True
            return

        if status_request_state is None and pending_request_count:
            # The bridge multiplexes one upstream connection across pending
            # requests. An event that reaches none of them is dropped here, and
            # whatever was waiting for it waits until a timeout fires, so the
            # drop needs to be visible rather than inferred from a missing
            # downstream response.
            #
            # The frame still proves the upstream transport is alive. Nothing
            # resets the downstream pre-response silence clock from here (that
            # clock only sees matched queue items), so record the liveness as an
            # explicit marker: a later bridge_eventless_timeout with a non-zero
            # count is a local matching wedge, not a silent upstream.
            unmatched_liveness_count = _record_http_bridge_unmatched_upstream_liveness(
                session,
                event_type=event_type,
            )
            logger.warning(
                "HTTP bridge upstream event matched no pending request account_id=%s bridge_kind=%s "
                "event_type=%s has_response_id=%s pending_count=%d unmatched_upstream_liveness=%d",
                session.account.id,
                session.key.affinity_kind,
                event_type or "unknown",
                response_id is not None,
                pending_request_count,
                unmatched_liveness_count,
            )
            if _http_bridge_event_proves_upstream_liveness(event_type):
                _log_http_bridge_event(
                    "unmatched_upstream_liveness",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    pending_count=pending_request_count,
                    detail=(
                        f"event_type={event_type or 'unknown'} unmatched_upstream_liveness={unmatched_liveness_count}"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )

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

        continuity_persistence_failed_after_ack = False
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
            await _retire_denied_http_bridge_anchor(
                self,
                session,
                request_states=(status_request_state,),
            )

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
        retry_error_message = _websocket_event_error_message(event_type, payload)
        wait_for_model_capacity_retry = bool(
            retry_error_code is not None
            and retry_error_code != _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
            and not is_previous_response_not_found_event
            and status_request_state is not None
            and is_upstream_model_capacity_error(retry_error_message)
            and _websocket_request_can_replay_before_visible_output(status_request_state)
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
                if is_http_bridge_account_neutral_replay(
                    kind=session.key.affinity_kind,
                    key=session.key.affinity_key,
                ):
                    _clear_websocket_request_error_overrides(status_request_state)
                else:
                    status_request_state.error_http_status_override = 502
                    (
                        _downstream_text,
                        event_block,
                        event,
                        payload,
                        event_type,
                    ) = _build_stream_incomplete_terminal_event_for_request(status_request_state)
        elif wait_for_model_capacity_retry and status_request_state is not None and retry_error_code is not None:
            # Reserve the terminal request again before any await so a younger
            # submit cannot claim its queue slot while account health is being
            # updated. A concurrent detach will mark this state as draining.
            retry_consumer_attached = False
            async with session.pending_lock:
                if status_request_state.event_queue is not None:
                    retry_consumer_attached = True
                    if status_request_state not in session.pending_requests:
                        session.pending_requests.appendleft(status_request_state)
                        session.queued_request_count += 1
                    status_request_state.awaiting_response_created = True
                    status_request_state.response_id = None
            if status_request_state.propagate_http_errors:
                _signal_http_bridge_capacity_startup_wait(status_request_state)
            await self._handle_or_defer_precreated_stream_health(
                status_request_state,
                session.account,
                {"message": retry_error_message or "Upstream error"},
                retry_error_code,
            )
            retry_consumer_attached = (
                retry_consumer_attached
                and status_request_state.event_queue is not None
                and not status_request_state.draining_until_terminal
            )
            if retry_consumer_attached:
                wait_request_had_event_queue = True
                if (
                    status_request_state.response_create_admission is not None
                    or status_request_state.account_response_create_lease is not None
                ):
                    await _release_http_bridge_model_capacity_retry_admission(status_request_state)
                    status_request_state.awaiting_response_created = True
                retry_after_wait = await _wait_before_http_bridge_model_capacity_retry(
                    status_request_state,
                    emit_keepalives=not status_request_state.propagate_http_errors,
                    error_message=retry_error_message,
                    cancel_when_detached=True,
                )
                if wait_request_had_event_queue and status_request_state.event_queue is None:
                    retry_after_wait = False
                suppress_capacity_keepalives_until_retry_finishes = (
                    status_request_state.account_capacity_wait_suppress_keepalive
                )
                try:
                    retried = retry_after_wait and await self._retry_http_bridge_precreated_request(
                        session,
                        request_state=status_request_state,
                    )
                    if retried:
                        _signal_http_bridge_model_capacity_retry_ready(
                            status_request_state,
                            waited_for_model_capacity_retry=True,
                            retried=True,
                        )
                        return
                finally:
                    if suppress_capacity_keepalives_until_retry_finishes:
                        status_request_state.account_capacity_wait_suppress_keepalive = False
                async with session.pending_lock:
                    if status_request_state in session.pending_requests:
                        session.pending_requests.remove(status_request_state)
                        if _http_bridge_request_counts_against_queue(status_request_state):
                            session.queued_request_count = max(0, session.queued_request_count - 1)
                if retry_after_wait or not status_request_state.propagate_http_errors:
                    status_request_state.error_http_status_override = 502
                    (
                        _downstream_text,
                        event_block,
                        event,
                        payload,
                        event_type,
                    ) = _build_stream_incomplete_terminal_event_for_request(status_request_state)
            else:
                async with session.pending_lock:
                    if status_request_state in session.pending_requests:
                        session.pending_requests.remove(status_request_state)
                        if _http_bridge_request_counts_against_queue(status_request_state):
                            session.queued_request_count = max(0, session.queued_request_count - 1)
        elif owner_pinned_quota_error is not None and not is_previous_response_not_found_event:
            await self._handle_or_defer_precreated_stream_health(
                status_request_state,
                session.account,
                {"message": retry_error_message or "Upstream error"},
                owner_pinned_quota_error,
            )
            if (
                status_request_state is not None
                and status_request_state.previous_response_id is not None
                and status_request_state.preferred_account_id is not None
            ):
                safe_request_text = _prepare_websocket_request_state_for_account_switch(status_request_state)
                if safe_request_text is not None:
                    previous_upstream_turn_state = session.upstream_turn_state
                    previous_downstream_turn_state = session.downstream_turn_state
                    session.upstream_turn_state = None
                    session.downstream_turn_state = None
                    await self._release_request_state_account_response_create_lease(status_request_state)
                    status_request_state.excluded_account_ids.add(session.account.id)
                    status_request_state.affinity_policy = replace(
                        status_request_state.affinity_policy,
                        reallocate_sticky=True,
                    )
                    status_request_state.request_text = safe_request_text
                    async with session.pending_lock:
                        if status_request_state not in session.pending_requests:
                            session.pending_requests.appendleft(status_request_state)
                            session.queued_request_count += 1
                        status_request_state.awaiting_response_created = True
                        status_request_state.response_id = None
                    retried = await self._retry_http_bridge_precreated_request(session)
                    if retried:
                        return
                    session.upstream_turn_state = previous_upstream_turn_state
                    session.downstream_turn_state = previous_downstream_turn_state
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
                else:
                    status_request_state.error_http_status_override = 502
                    session.upstream_control.reconnect_requested = True
                    session.upstream_control.retire_after_drain = True
                    event, payload, event_type, rewritten_text = (
                        _rewrite_websocket_previous_response_owner_unavailable_event(
                            request_state=status_request_state,
                        )
                    )
                    event_block = f"data: {rewritten_text}\n\n"
        elif (
            retry_error_code == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
            and not is_previous_response_not_found_event
            and status_request_state is not None
            and _websocket_auth_request_can_switch_account(status_request_state)
        ):
            rejected_account_id = session.account.id
            status_request_state.precreated_replay_reason = _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
            status_request_state.precreated_replay_account_id = rejected_account_id
            previous_upstream_turn_state = session.upstream_turn_state
            previous_downstream_turn_state = session.downstream_turn_state
            previous_headers = session.headers
            await self._release_request_state_account_response_create_lease(status_request_state)
            async with session.pending_lock:
                if status_request_state not in session.pending_requests:
                    session.pending_requests.appendleft(status_request_state)
                    session.queued_request_count += 1
                status_request_state.awaiting_response_created = True
                status_request_state.response_id = None
            retried = await self._retry_http_bridge_precreated_request(session)
            if retried:
                logger.info(
                    "Retried HTTP bridge request after account/model rejection "
                    "request_id=%s rejected_account_id=%s model=%s",
                    status_request_state.request_log_id or status_request_state.request_id,
                    rejected_account_id,
                    status_request_state.model,
                )
                return
            replacement_session_selected = session.account.id != rejected_account_id
            if not replacement_session_selected:
                session.upstream_turn_state = previous_upstream_turn_state
                session.downstream_turn_state = previous_downstream_turn_state
                session.headers = previous_headers
            async with session.pending_lock:
                if status_request_state in session.pending_requests:
                    session.pending_requests.remove(status_request_state)
                    session.queued_request_count = max(0, session.queued_request_count - 1)
            if replacement_session_selected:
                # Reconnect may have committed the session to a replacement
                # account before its replacement lease or send failed.  Never
                # graft the rejected account's turn metadata back onto that
                # socket; retire the now-unused replacement session instead.
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                await self._retire_http_bridge_after_drain_if_ready(session)
                payload = cast(
                    dict[str, JsonValue],
                    dict(
                        response_failed_event(
                            status_request_state.error_code_override or "upstream_unavailable",
                            status_request_state.error_message_override or "HTTP bridge replacement retry failed",
                            error_type=status_request_state.error_type_override or "server_error",
                            response_id=status_request_state.request_id,
                            error_param=status_request_state.error_param_override,
                        )
                    ),
                )
                event_block = format_sse_event(payload)
                event = parse_sse_event_payload(payload)
                event_type = "response.failed"
            if status_request_state.precreated_replay_reason == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE:
                _clear_websocket_precreated_replay_fallback(status_request_state)
        elif (
            retry_error_code is not None
            and retry_error_code != _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
            and not is_previous_response_not_found_event
        ):
            await self._handle_or_defer_precreated_stream_health(
                status_request_state,
                session.account,
                {"message": retry_error_message or "Upstream error"},
                retry_error_code,
            )
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

        completed_usage = (
            event.response.usage if event_type == "response.completed" and event and event.response else None
        )
        completed_empty_prewarm = (
            event_type == "response.completed"
            and terminal_request_state is not None
            and terminal_request_state.request_kind == "prewarm"
            and completed_usage is not None
            and completed_usage.output_tokens == 0
        )

        # The circuit settle and quarantine clear run BEFORE the fresh
        # anchor is registered. A poison abandonment fences its continuity
        # clear on the anchor captured when its episode was validated, and
        # settle-first ordering is what makes that fence airtight: any
        # consult that runs after this settle is vetoed by the reset row,
        # and any abandonment authorized before it fences on the old anchor
        # and cannot touch the one registered below.
        completion_circuit_settlement_failed = False
        completion_quarantine_clear_fence: int | None = None
        completion_pre_settle_poison_detail: str | None = None
        completion_settles_onto_tombstone = False
        if (
            event_type == "response.completed"
            and terminal_request_state is not None
            and not terminal_request_state.suppressed_duplicate_tool_call
            and terminal_request_state.request_kind != "prewarm"
            and not terminal_request_state.skip_request_log
        ):
            # Captured before the settle and registration awaits below: a
            # concurrent request failing during those awaits arms a NEW
            # same-key quarantine whose evidence this completion does not
            # disprove, and the clear at the bottom must fence on what was
            # armed when this response proved the session healthy, not on
            # whatever owns the key by then.
            # Adopt any durable-only poison row BEFORE the fences are
            # captured: the settle's internal load would otherwise arm the
            # quarantine after the fence read (leaving a healthy key
            # suppressed for the TTL because the clear cannot match), and a
            # failed registration would find no pre-settle poison detail to
            # re-seed.
            completion_pre_settle_load_succeeded = await self._load_http_bridge_retry_circuit(session)
            completion_quarantine_clear_fence = _http_bridge_quarantine_clear_fence(self, session.key)
            async with self._http_bridge_retry_circuit_lock:
                pre_settle_state = self._http_bridge_retry_circuits.get(session.key)
                if pre_settle_state is not None:
                    completion_pre_settle_poison_detail = (
                        pre_settle_state.last_detail
                        if _http_bridge_anchor_poison_detail(pre_settle_state.last_detail) is not None
                        else pre_settle_state.owed_poison_detail
                    )
                    # An episode already carrying the fail-closed tombstone
                    # keeps it through the settle: erasing it before the
                    # fresh anchor commits would drop the only durable
                    # marker while the registration can still fail or never
                    # run at all.
                    completion_settles_onto_tombstone = (
                        completion_pre_settle_poison_detail is not None
                        or pre_settle_state.last_detail == _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
                    )
            completion_registration_possible = (
                response_id is not None and matched_request_state is not None and not completed_empty_prewarm
            )
            if (
                not terminal_request_state.verified_stale_anchor_replay
                and completion_settles_onto_tombstone
                and not completion_registration_possible
            ):
                # No usable response id or matched request: the registration
                # block below never runs, so settling here would replace the
                # poison row with a zero-count tombstone while the OLD
                # poisoned anchor stays stored — the next planning load
                # reads the zero count as a disproved episode, revokes the
                # quarantine, and a full resend gets the dead anchor
                # injected. Keep the episode unsettled; a completion that
                # can actually register a fresh anchor settles it.
                completion_circuit_settlement_failed = True
            elif not terminal_request_state.verified_stale_anchor_replay:
                # A completion replacing a poison episode settles with the
                # transitional tombstone: a crash or takeover between this
                # settle and the registration below would otherwise leave
                # the old poisoned anchor stored while the reset row reads
                # as disproved. The tombstone is erased once the fresh
                # anchor commits.
                circuit_settled = await self._clear_http_bridge_retry_circuit(
                    session,
                    settled_detail=(
                        _HTTP_BRIDGE_RETRY_CIRCUIT_ANCHOR_ABANDONED_DETAIL
                        if completion_settles_onto_tombstone
                        else None
                    ),
                )
                if not completion_pre_settle_load_succeeded:
                    # The fence above was captured off a failed durable read;
                    # the settle's own successful inner load may have armed
                    # the poison quarantine AFTER that capture, and the final
                    # fenced clear would then refuse to remove it — a healthy
                    # key stuck for the whole poison window despite the fresh
                    # anchor about to register. Recapture after the settle:
                    # this still precedes the registration awaits, so a
                    # quarantine armed by a genuinely concurrent strike
                    # during those awaits stays outside the fence.
                    completion_quarantine_clear_fence = _http_bridge_quarantine_clear_fence(self, session.key)
                if not circuit_settled:
                    # The old poison episode was restored. Its owed clear is
                    # suppressed only once the fresh anchor actually
                    # persists below: suppressing here and then failing the
                    # registration would leave the old poisoned anchor
                    # stored with no funnel willing to clear it after the
                    # process-local quarantine expires.
                    completion_circuit_settlement_failed = True

        # False until a fresh durable anchor actually confirms: a completed
        # event without a usable response id (or with no matched request)
        # skips the registration block entirely, and clearing the quarantine
        # then would leave the old poisoned anchor as the stored one for the
        # next reattach.
        completion_anchor_registration_confirmed = False
        if (
            response_id is not None
            and matched_request_state is not None
            and event_type == "response.completed"
            and not completed_empty_prewarm
        ):
            anchor_advance_supersession = None
            if completion_circuit_settlement_failed:
                # Applied BEFORE the fresh anchor is published: a concurrent
                # funnel's consult whose continuity read lands between the
                # publication and a later suppression would validate the old
                # poison row and fence its rebind on the anchor just
                # registered, deleting it. The suppression is transitional —
                # rolled back below when the registration fails, so the old
                # anchor never becomes uncleareable.
                anchor_advance_supersession = await self._http_bridge_suppress_poison_clear_after_anchor_advance(
                    session
                )
            alias_registered = await self._register_http_bridge_previous_response_id(
                session,
                response_id,
                input_item_count=(
                    matched_request_state.input_item_count if matched_request_state.input_item_count > 0 else None
                ),
                input_full_fingerprint=(
                    matched_request_state.input_full_fingerprint if matched_request_state.input_item_count > 0 else None
                ),
                pending_tool_calls=_durable_pending_tool_call_manifest(matched_request_state, payload),
            )
            completion_anchor_registration_confirmed = alias_registered
            if not alias_registered and anchor_advance_supersession is not None:
                await self._http_bridge_restore_poison_clear_after_failed_anchor_advance(
                    session, anchor_advance_supersession
                )
            if alias_registered and anchor_advance_supersession is not None:
                await self._http_bridge_promote_transitional_supersession(session, anchor_advance_supersession)
            if alias_registered and not completion_circuit_settlement_failed and completion_settles_onto_tombstone:
                # The fresh anchor committed: erase the transitional
                # tombstone the settle left, fenced on the row's own
                # values, with one reconcile round covering both a
                # transient durable blip and a CAS miss under a sticky
                # concurrent strike.
                await self._http_bridge_reconcile_transitional_tombstone(session)
            if (
                not alias_registered
                and not completion_circuit_settlement_failed
                and completion_pre_settle_poison_detail is not None
            ):
                # The settle-before-registration ordering already zeroed the
                # durable circuit, but the registration failure means the
                # OLD poisoned anchor is still the stored one. Without
                # durable evidence the next planning load revokes the kept
                # local quarantine as a disproved episode and other replicas
                # never arm; re-seed the row so the poison protection holds
                # everywhere until a real recovery lands.
                await self._http_bridge_restore_poison_row_after_failed_registration(
                    session, completion_pre_settle_poison_detail
                )
            if not alias_registered and is_http_bridge_account_neutral_replay(
                kind=session.key.affinity_kind,
                key=session.key.affinity_key,
            ):
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                matched_request_state.error_http_status_override = 502
                payload = cast(
                    dict[str, JsonValue],
                    dict(
                        response_failed_event(
                            "bridge_continuity_persistence_failed",
                            "Recovered response continuity could not be persisted; retry the request.",
                            response_id=_websocket_downstream_response_id(matched_request_state),
                        )
                    ),
                )
                event_block = format_sse_event(payload)
                event = parse_sse_event_payload(payload)
                event_type = "response.failed"
                # The upstream response was already acknowledged. The local
                # alias write failed, so expose a terminal error downstream
                # but keep the durable operation acknowledged/ambiguous to
                # prevent an identical retry from dispatching it again.
                continuity_persistence_failed_after_ack = True
                completed_usage = None
                completed_empty_prewarm = False

        if (
            event_type == "response.completed"
            and terminal_request_state is not None
            and not terminal_request_state.suppressed_duplicate_tool_call
            and terminal_request_state.request_kind != "prewarm"
            and not terminal_request_state.skip_request_log
            # A failed circuit settlement leaves the at-threshold poison row
            # standing; the quarantine keeps covering it so the surviving
            # cooldown cannot hand the next attach a poisoned injection while
            # the settle retries at the next opportunity.
            and not completion_circuit_settlement_failed
            # A durable alias write that failed leaves the OLD anchor as the
            # stored one even though this worker's completion succeeded: a
            # replica or a restart would re-inject it, and the quarantine is
            # the only protection left once the circuit was settled.
            and completion_anchor_registration_confirmed
        ):
            # The quarantine clears only after the fresh anchor persisted:
            # a failed alias write rewrites the event to response.failed
            # above, this guard then skips, and the quarantine keeps
            # covering the old anchor that is still stored. The circuit
            # settle deliberately ran before registration for the
            # abandonment fence; the quarantine surviving here is what
            # protects the partial-failure window that ordering leaves.
            _clear_http_bridge_quarantine(
                self,
                session,
                key_generation=completion_quarantine_clear_fence,
                additional_key=terminal_request_state.verified_stale_anchor_retry_circuit_key,
                additional_key_generation=terminal_request_state.verified_stale_anchor_quarantine_generation,
            )

        operation_state = _http_bridge_operation_state_for_event(event_type)
        if operation_state is not None:
            operation_request_states: list[Any] = []
            for candidate in (matched_request_state, terminal_request_state):
                if candidate is not None and candidate not in operation_request_states:
                    operation_request_states.append(candidate)
            for operation_request_state in operation_request_states:
                request_operation_state = operation_state
                if continuity_persistence_failed_after_ack and operation_request_state is matched_request_state:
                    request_operation_state = "acknowledged"
                if request_operation_state == "failed":
                    # Failure rows are exposed only by the terminal-event
                    # persistence path below, which appends the terminal SSE
                    # block and flips the operation state atomically.
                    continue
                await _update_http_bridge_operation_state(
                    self,
                    session,
                    operation_request_state,
                    state=request_operation_state,
                    response_id=response_id,
                )

        recovery_attempt_session_id = (
            matched_request_state.recovery_attempt_session_id
            if matched_request_state is not None and matched_request_state.recovery_attempt_session_id is not None
            else session.durable_session_id
        )
        recovery_attempt_owner_epoch = (
            matched_request_state.recovery_attempt_owner_epoch
            if matched_request_state is not None and matched_request_state.recovery_attempt_owner_epoch is not None
            else session.durable_owner_epoch
        )

        if (
            isinstance(event_type, str)
            and (event_type.startswith("response.") or event_type == "error")
            and matched_request_state is not None
            and matched_request_state.recovery_attempt_fingerprint is not None
            and recovery_attempt_session_id is not None
            and recovery_attempt_owner_epoch is not None
            and (
                event_type in {"response.completed", "response.failed", "response.incomplete", "error"}
                or not matched_request_state.recovery_attempt_event_observed
            )
        ):
            settlement_marked = False
            for settlement_attempt in range(3):
                try:
                    marked = await self._durable_bridge.mark_recovery_attempt_replayed(
                        session_id=recovery_attempt_session_id,
                        api_key_id=session.key.api_key_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=recovery_attempt_owner_epoch,
                        request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                        response_id=response_id,
                    )
                    if marked:
                        settlement_marked = True
                        break
                    if settlement_attempt == 2:
                        _schedule_http_bridge_recovery_settlement_retry(
                            self,
                            session,
                            session_id=recovery_attempt_session_id,
                            api_key_id=session.key.api_key_id,
                            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                            owner_epoch=recovery_attempt_owner_epoch,
                            request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                            response_id=response_id,
                            release_origin_lease=(
                                recovery_attempt_session_id != session.durable_session_id
                                and event_type
                                in {"response.completed", "response.failed", "response.incomplete", "error"}
                            ),
                        )
                except Exception:
                    if settlement_attempt == 2:
                        logger.warning("Failed to settle HTTP bridge recovery attempt", exc_info=True)
                        _schedule_http_bridge_recovery_settlement_retry(
                            self,
                            session,
                            session_id=recovery_attempt_session_id,
                            api_key_id=session.key.api_key_id,
                            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                            owner_epoch=recovery_attempt_owner_epoch,
                            request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                            response_id=response_id,
                            release_origin_lease=(
                                recovery_attempt_session_id != session.durable_session_id
                                and event_type
                                in {"response.completed", "response.failed", "response.incomplete", "error"}
                            ),
                        )
                    else:
                        await asyncio.sleep(0.05 * (settlement_attempt + 1))
            if (
                settlement_marked
                and event_type in {"response.completed", "response.failed", "response.incomplete", "error"}
                and recovery_attempt_session_id != session.durable_session_id
            ):
                try:
                    await self._durable_bridge.release_live_session(
                        session_id=recovery_attempt_session_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=recovery_attempt_owner_epoch,
                        draining=False,
                    )
                except Exception:
                    logger.debug("Failed to release HTTP bridge recovery origin lease", exc_info=True)
            matched_request_state.recovery_attempt_event_observed = True

        if event_type == "response.completed" and terminal_request_state is not None and not completed_empty_prewarm:
            # Record the completed response id regardless of input shape so
            # subsequent turns (including ones that never populated
            # input_item_count, e.g. string inputs) can still reuse this
            # anchor for continuity lookups.
            if response_id is not None:
                session.last_completed_response_id = response_id
                # This response was completed on the session's current account, so
                # that account owns the anchor. Record it so the anchor is only
                # replayed on the same account (never after a cross-account failover).
                session.last_completed_response_account_id = session.account.id
                # Remember which tool-call items the completed response left
                # pending so an anchored follow-up that omits their outputs
                # (interrupted turn) can receive synthetic interrupted
                # outputs instead of an upstream missing-tool-output 400.
                session.last_pending_tool_calls = dict(terminal_request_state.pending_tool_call_types)
            # Prefix trimming is only meaningful for list-shaped inputs, so
            # keep the input-count / fingerprint update scoped to that path.
            if terminal_request_state.input_item_count > 0:
                session.last_completed_input_count = terminal_request_state.input_item_count
                session.last_completed_input_prefix_fingerprint = terminal_request_state.input_full_fingerprint

        normalize_error_event = (
            terminal_request_state is None or terminal_request_state.enforce_openai_sdk_contract
        ) and (matched_request_state is None or matched_request_state.enforce_openai_sdk_contract)
        settlement_payload = payload
        settlement_event = event
        settlement_event_type = event_type
        if event_type == "error" and normalize_error_event:
            http_status = _http_error_status_from_payload(payload)
            if status_request_state is not None and status_request_state.error_http_status_override is None:
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
            settlement_payload = payload
            settlement_event = event
            settlement_event_type = event_type
        elif event_type == "error":
            http_status = _http_error_status_from_payload(payload)
            if status_request_state is not None and status_request_state.error_http_status_override is None:
                status_request_state.error_http_status_override = http_status
            (
                _settlement_event_block,
                settlement_payload,
                settlement_event,
                settlement_event_type,
            ) = _normalize_http_bridge_error_event(
                event=event,
                payload=payload,
                request_state=terminal_request_state or matched_request_state,
            )

        if (
            settlement_event_type in {"response.failed", "error"}
            and matched_request_state is not None
            and matched_request_state.recovery_attempt_fingerprint is not None
            and recovery_attempt_session_id is not None
            and recovery_attempt_owner_epoch is not None
            and not matched_request_state.recovery_attempt_event_observed
        ):
            # An explicit deterministic failure is terminal evidence for the
            # journaled request, not an ambiguous transport outcome. Consume
            # the UNKNOWN row after normalizing top-level errors so a later
            # identical retry cannot turn it into an account-neutral replay.
            deterministic_settlement_marked = False
            for settlement_attempt in range(3):
                try:
                    marked = await self._durable_bridge.mark_recovery_attempt_replayed(
                        session_id=recovery_attempt_session_id,
                        api_key_id=session.key.api_key_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=recovery_attempt_owner_epoch,
                        request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                        response_id=response_id,
                    )
                    if marked:
                        deterministic_settlement_marked = True
                        break
                    if settlement_attempt == 2:
                        _schedule_http_bridge_recovery_settlement_retry(
                            self,
                            session,
                            session_id=recovery_attempt_session_id,
                            api_key_id=session.key.api_key_id,
                            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                            owner_epoch=recovery_attempt_owner_epoch,
                            request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                            response_id=response_id,
                            release_origin_lease=recovery_attempt_session_id != session.durable_session_id,
                        )
                except Exception:
                    if settlement_attempt == 2:
                        logger.warning("Failed to settle deterministic HTTP bridge recovery attempt", exc_info=True)
                        _schedule_http_bridge_recovery_settlement_retry(
                            self,
                            session,
                            session_id=recovery_attempt_session_id,
                            api_key_id=session.key.api_key_id,
                            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                            owner_epoch=recovery_attempt_owner_epoch,
                            request_fingerprint=matched_request_state.recovery_attempt_fingerprint,
                            response_id=response_id,
                            release_origin_lease=recovery_attempt_session_id != session.durable_session_id,
                        )
                    else:
                        await asyncio.sleep(0.05 * (settlement_attempt + 1))
            if deterministic_settlement_marked and recovery_attempt_session_id != session.durable_session_id:
                try:
                    await self._durable_bridge.release_live_session(
                        session_id=recovery_attempt_session_id,
                        instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                        owner_epoch=recovery_attempt_owner_epoch,
                        draining=False,
                    )
                except Exception:
                    logger.debug("Failed to release HTTP bridge recovery origin lease", exc_info=True)

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            await _release_websocket_response_create_gate(created_request_state, session.response_create_gate)

        if terminal_request_state is not None and settlement_event_type in {"response.failed", "error"}:
            if settlement_event_type == "error":
                error = settlement_event.error if settlement_event else None
            else:
                error = settlement_event.response.error if settlement_event and settlement_event.response else None
            terminal_error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            terminal_error_message = error.message if error else None
            if _is_security_work_authorization_required_error(terminal_error_code, terminal_error_message):
                can_retry_security_work = (
                    not is_http_bridge_account_neutral_replay(
                        kind=session.key.affinity_kind,
                        key=session.key.affinity_key,
                    )
                    and not session.account.security_work_authorized
                    and not has_other_pending_requests
                    and terminal_request_state.response_id is None
                    and terminal_request_state.replay_count < 1
                    and bool(terminal_request_state.request_text)
                    and terminal_request_state.preferred_account_id != session.account.id
                    and _websocket_auth_request_can_switch_account(terminal_request_state)
                    and _websocket_request_can_replay_before_visible_output(terminal_request_state)
                )
                _clear_websocket_deferred_reasoning_downstream_texts(terminal_request_state)
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

        terminal_strike_failures: int | None = None
        terminal_poison_detail: str | None = None

        async def _finalize_terminal_settlement(settled_request_state: _WebSocketRequestState) -> None:
            try:
                await self._finalize_websocket_request_state(
                    settled_request_state,
                    account=session.account,
                    account_id_value=session.account.id,
                    event=settlement_event,
                    event_type=settlement_event_type,
                    payload=settlement_payload,
                    api_key=settled_request_state.api_key,
                    upstream_control=session.upstream_control,
                    response_create_gate=session.response_create_gate,
                )
            finally:
                await self._maybe_release_idle_http_bridge_session_lease(session)

        if settlement_event_type in {"response.failed", "response.incomplete", "error"}:
            error_code = None
            if settlement_event_type == "error":
                error = settlement_event.error if settlement_event else None
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            elif settlement_event and settlement_event.response:
                error = settlement_event.response.error
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
            if (
                error_code is not None
                and terminal_request_state is not None
                and terminal_request_state.response_event_count == 0
                # Internal warmup probes carry no anchor and prove nothing
                # about the key's continuity; charging them would open and
                # quarantine the hard key before any real turn, exactly the
                # states the completion settle already excludes.
                and terminal_request_state.request_kind != "prewarm"
                and not terminal_request_state.skip_request_log
                # Only a held safe replay excludes the strike: an unanchored
                # request with no replay is stranded like any other, and the
                # delta spec's no-response/no-safe-replay rule applies to it
                # the same way the retirement funnels apply it.
                and not _http_bridge_request_state_holds_safe_replay(terminal_request_state)
            ):
                # An upstream terminal frame that fails the request before any
                # response event is the same pre-response failure the circuit
                # measures on eventless retirements; it reaches this settlement
                # path instead of the retirement funnel, so it would otherwise
                # never count. Attempt-scoped recording keeps a later
                # retirement of the same lifecycle from double-counting, and
                # the recorder itself drops non-circuit details and soft keys.
                #
                # This runs before the terminal frame and its queue sentinel
                # reach the client: once completion is observable the client can
                # resend the same anchor immediately, and the cooldown and
                # quarantine have to already be visible to that resend rather
                # than still awaiting durable I/O. Recovery paths that retry
                # this request in place have all declined by here.
                terminal_strike_failures = await self._record_http_bridge_retry_circuit_failure(
                    session,
                    detail=error_code,
                    attempt=terminal_request_state.response_create_attempt,
                    terminal_pre_response_frame=True,
                )
                terminal_poison_detail = await self._http_bridge_effective_anchor_poison_detail(session, error_code)

        matched_event_queue = (
            completed_event_queue
            if completed_event_queue_claimed and matched_request_state is terminal_request_state
            else matched_request_state.event_queue
            if matched_request_state is not None
            else None
        )
        matched_deferred_texts = (
            _pop_websocket_deferred_reasoning_downstream_texts(matched_request_state)
            if matched_request_state is not None and not suppress_downstream_event
            else []
        )
        matched_terminal_state = _http_bridge_operation_state_for_event(event_type)
        if continuity_persistence_failed_after_ack and matched_request_state is not None:
            # The upstream response was already accepted. The downstream
            # failure only reports that its durable alias could not be
            # persisted, so keep the operation fenced as acknowledged while
            # retaining the failure SSE for the client.
            matched_terminal_state = "acknowledged"

        async def _publish_matched_and_terminal_frames() -> None:
            nonlocal matched_terminal_enqueued
            if matched_request_state is not None and not suppress_downstream_event:
                for deferred_text in matched_deferred_texts:
                    await _persist_http_bridge_operation_event(
                        self,
                        session,
                        matched_request_state,
                        deferred_text,
                        terminal=False,
                    )
            if matched_request_state is not None and matched_event_queue is not None and not suppress_downstream_event:
                for deferred_text in matched_deferred_texts:
                    await matched_event_queue.put(deferred_text)
            if matched_request_state is not None and not suppress_downstream_event:
                matched_terminal_enqueued = await _persist_http_bridge_operation_event(
                    self,
                    session,
                    matched_request_state,
                    event_block,
                    terminal=event_type in {"response.completed", "response.failed", "response.incomplete", "error"},
                    terminal_state=matched_terminal_state,
                    terminal_event_queue=matched_event_queue,
                    terminal_delivery_scope=(completed_delivery_scope if completed_event_queue_claimed else None),
                )
            if (
                matched_request_state is not None
                and matched_event_queue is not None
                and not suppress_downstream_event
                and matched_terminal_enqueued is not True
            ):
                await matched_event_queue.put(event_block)

            if terminal_request_state is None:
                return

            terminal_event_queue = (
                completed_event_queue if completed_event_queue_claimed else terminal_request_state.event_queue
            )
            terminal_enqueued = matched_terminal_enqueued if terminal_request_state is matched_request_state else False
            if terminal_request_state is not matched_request_state:
                deferred_texts = _pop_websocket_deferred_reasoning_downstream_texts(terminal_request_state)
                for deferred_text in deferred_texts:
                    if not suppress_downstream_event:
                        await _persist_http_bridge_operation_event(
                            self,
                            session,
                            terminal_request_state,
                            deferred_text,
                            terminal=False,
                        )
                    if terminal_event_queue is not None:
                        await terminal_event_queue.put(deferred_text)
                if not suppress_downstream_event:
                    terminal_enqueued = await _persist_http_bridge_operation_event(
                        self,
                        session,
                        terminal_request_state,
                        event_block,
                        terminal=True,
                        terminal_state=(
                            "acknowledged"
                            if continuity_persistence_failed_after_ack
                            and terminal_request_state is matched_request_state
                            else _http_bridge_operation_state_for_event(event_type)
                        ),
                        terminal_event_queue=terminal_event_queue,
                        terminal_delivery_scope=(completed_delivery_scope if completed_event_queue_claimed else None),
                    )
                if terminal_event_queue is not None and terminal_enqueued is not True:
                    await terminal_event_queue.put(event_block)
            if terminal_event_queue is not None:
                if terminal_enqueued is not True:
                    await terminal_event_queue.put(None)
                if completed_event_queue_claimed and completed_delivery_scope is not None:
                    async with session.pending_lock:
                        # Keep the completed claim authoritative after its producer
                        # returns. A concurrent timeout may still be finishing
                        # awaited recovery work before it rechecks this scope.
                        completed_delivery_scope.terminal_enqueued = True

        matched_terminal_enqueued = False
        terminal_publication_cancellation: asyncio.CancelledError | None = None
        if terminal_poison_detail is not None and terminal_strike_failures is not None:
            # Publication and settlement share one deferral for a poison
            # terminal: a cancellation landing inside the operation
            # persistence, between the queued frame and its sentinel, or on
            # any other publication await used to escape before the owned
            # settlement task even existed — the outer abort finalized the
            # popped request and the consult, abandonment, and marker never
            # started, leaving the poisoned durable anchor reusable once the
            # process-local quarantine expired.
            terminal_publication_task = asyncio.create_task(
                _publish_matched_and_terminal_frames(),
                name=f"http-bridge-terminal-publication-{session.durable_session_id}",
            )
            _terminal_publication_result, terminal_publication_cancellation = await _await_task_deferring_cancellation(
                terminal_publication_task
            )
        else:
            await _publish_matched_and_terminal_frames()

        if terminal_request_state is None:
            return

        if terminal_poison_detail is not None and terminal_strike_failures is not None:
            # The strike above opens the circuit, but the durable anchor that
            # failed is still stored. Only the retirement and close funnels ever
            # reached the poison clear, and a terminal frame settles through
            # neither, so the dead anchor survived every cooldown and re-poisoned
            # the key on the next reattach. Quarantine suppresses the injection
            # in the meantime, but it is process-local and expires; the durable
            # row has to be cleared for the recovery to hold.
            #
            # This gates on the circuit's own threshold rather than the
            # configurable anchor-poison threshold, and fires with the same
            # evidence that already quarantines the key: one decision, the
            # in-memory half suppressing the next injection and the durable half
            # clearing the stored anchor. The configurable threshold governs the
            # retirement and close funnels, where no circuit gates first. Here it
            # is unreachable by construction, which is issue #1830/#1852 itself:
            # the circuit opens at two failures and then refuses the key for
            # 60-600s per strike, so its default of seven is tens of minutes of
            # dead conversation away.
            #
            # Unlike the strike, this runs after the terminal frame is published.
            # A resend arriving in between is already covered by the quarantine
            # armed with the strike, so there is no reason to put a fenced
            # durable write in front of the client's own failure.
            #
            # The terminal frame is already on its way to the client, so
            # these awaits must not be the last thing that runs. If the
            # reader is cancelled while the episode consult's durable lookup
            # or the fenced write is in contention, the cancellation would
            # otherwise escape before the finalization below is entered and
            # the request would never be finalized despite having been
            # answered. The consult reads the currently registered episode,
            # not the detached count captured with the strike: a multiplexed
            # sibling that completed during publication has settled the
            # circuit and persisted a fresh anchor, and this clear must not
            # delete it.
            terminal_settlement_cancellation: asyncio.CancelledError | None = None
            try:
                terminal_settlement_detail = terminal_poison_detail

                async def _terminal_consult_and_clear() -> None:
                    consult_episode, consult_expected_anchor = await self._http_bridge_poison_anchor_clear_owed(
                        session,
                        consecutive_failures=terminal_strike_failures,
                        configured_threshold=(
                            _service_get_settings().http_responses_session_bridge_anchor_poison_failure_threshold
                        ),
                    )
                    if consult_episode is not None:
                        # A multiplexed survivor holding a verified safe
                        # replay claims the source circuit generation right
                        # before dispatch; settling under it would strip
                        # that fence, so survivors count alongside the
                        # terminal request, snapshotted at decision time.
                        async with session.pending_lock:
                            terminal_surviving_states = list(session.pending_requests)
                        durable_cleared = await _abandon_durable_http_bridge_continuity(
                            self,
                            session,
                            detail=terminal_settlement_detail,
                            settle_circuit=_http_bridge_abandonment_may_settle_circuit(
                                [terminal_request_state, *terminal_surviving_states]
                            ),
                            expected_continuity=consult_expected_anchor,
                            authorized_episode=consult_episode,
                        )
                        if durable_cleared:
                            # The abandonment succeeded even when its circuit
                            # settlement remains outstanding: without the
                            # marker, the next strike finds empty continuity,
                            # refuses another abandonment, and the restored
                            # cooldown backs off an anchor that is already
                            # gone.
                            await self._http_bridge_mark_poison_anchor_cleared(session, episode=consult_episode)

                # The terminal frame is already published and the request is
                # about to be finalized and removed: a cancellation escaping
                # the consult or the fenced rebind would leave the owed clear
                # with no lifecycle left to retry it, and once the
                # process-local quarantine expires the stored dead anchor is
                # reusable. Defer cancellation across the settlement like the
                # grouped and reader funnels, finalize regardless, then
                # re-raise.
                terminal_settlement_task = asyncio.create_task(
                    _terminal_consult_and_clear(),
                    name=f"http-bridge-terminal-poison-settlement-{session.durable_session_id}",
                )
                _terminal_result, terminal_settlement_cancellation = await _await_task_deferring_cancellation(
                    terminal_settlement_task
                )
            finally:
                await _finalize_terminal_settlement(terminal_request_state)
            if terminal_settlement_cancellation is None:
                terminal_settlement_cancellation = terminal_publication_cancellation
            if terminal_settlement_cancellation is not None:
                raise terminal_settlement_cancellation
        else:
            await _finalize_terminal_settlement(terminal_request_state)
