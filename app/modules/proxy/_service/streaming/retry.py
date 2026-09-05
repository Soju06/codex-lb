# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import replace
from typing import Any, AsyncGenerator, AsyncIterator, Mapping, cast

import aiohttp

from app.core.auth.refresh import RefreshError, is_transient_refresh_contention, refresh_contention_kind
from app.core.balancer import failover_decision
from app.core.balancer.types import ClassifiedFailure, UpstreamError
from app.core.clients.proxy import (
    ProxyResponseError,
    _is_native_codex_request,
    _resolve_stream_transport,
    is_confirmed_pre_dispatch_transport_error,
    pop_stream_timeout_overrides,
)
from app.core.errors import (
    SYNTHETIC_TRANSPORT_FAILURE_CODES,
    openai_error,
    synthetic_transport_failure_event,
)
from app.core.errors import (
    synthetic_stream_failure_event as response_failed_event,
)
from app.core.openai.requests import ResponsesRequest, extract_input_file_ids
from app.core.resilience.network_recovery import (
    NetworkRecoveryDecision,
    ProcessNetworkRecovery,
)
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id
from app.core.utils.retry import backoff_seconds
from app.core.utils.shared_future import _await_task_deferring_cancellation
from app.core.utils.sse import format_sse_event
from app.db.models import Account, StickySessionKind
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_request_shape,
    _record_continuity_fail_closed,
    _record_upstream_transport_decision,
)
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
from app.modules.proxy._service.support import (
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS,
    _LOCAL_ACCOUNT_CAP_ERROR_CODES,
    _account_capacity_wait_payload,
    _account_selection_recovery_sleep_seconds,
    _request_log_client_fields,
    _RetryableStreamError,
    _signal_propagated_capacity_startup_wait,
    _signal_propagated_responses_service_cleanup_ready,
    _stream_settlement_error_payload,
    _StreamSettlement,
    _TerminalStreamError,
    _TransientStreamError,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket.helpers import (
    _websocket_input_items_are_self_contained_fresh_replay,
)
from app.modules.proxy.affinity import (
    _is_synthesized_turn_state,
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
    _websocket_continuity_key_from_headers,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.continuity import resolve_required_account_id
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _is_account_model_unsupported_error,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
    classify_upstream_failure,
    is_upstream_model_capacity_error,
)
from app.modules.proxy.load_balancer import AccountLease, AccountSelection
from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED, selection_failure_response

_REQUEST_TRANSPORT_HTTP = "http"
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_HTTP_DOWNSTREAM_TRANSPORT_POLICY_DEFAULT = "smart"
_HTTP_DOWNSTREAM_TRANSPORT_POLICIES = frozenset({"smart", "always_http", "always_websocket", "pinned"})
logger = logging.getLogger(__name__)


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


def _http_downstream_request_is_sticky(payload: ResponsesRequest, headers: Mapping[str, str]) -> bool:
    return (
        payload.previous_response_id is not None
        or _prompt_cache_key_from_request_model(payload) is not None
        or _sticky_key_from_session_header(headers) is not None
        or _sticky_key_from_turn_state_header(headers) is not None
    )


_POST_REFRESH_TRANSIENT_EXHAUSTED_ATTR = "_codex_lb_post_refresh_transient_exhausted"


def _resolve_http_downstream_transport(policy: str, *, payload: ResponsesRequest, headers: Mapping[str, str]) -> str:
    normalized_policy = policy.strip().lower()
    if normalized_policy not in _HTTP_DOWNSTREAM_TRANSPORT_POLICIES:
        raise ValueError(f"Unsupported HTTP downstream transport policy: {policy}")
    if normalized_policy in ("always_http", "pinned"):
        return "http"
    if normalized_policy == "always_websocket":
        return "websocket"
    return "websocket" if _http_downstream_request_is_sticky(payload, headers) else "http"


def _verified_cross_transport_fresh_replay(
    proxy: _StreamingServiceProtocol,
    *,
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    api_key: ApiKeyData | None,
) -> ResponsesRequest | None:
    """Return an unanchored body only when local WS continuity proves its prefix."""
    previous_response_id = payload.previous_response_id
    input_value = payload.input
    if previous_response_id is None or not isinstance(input_value, list):
        return None
    if extract_input_file_ids(input_value):
        return None
    input_items = cast(list[Any], input_value)
    if not _websocket_input_items_are_self_contained_fresh_replay(input_items):
        return None
    session_id = _websocket_continuity_key_from_headers(headers)
    if session_id is None:
        return None
    api_key_id = api_key.id if api_key is not None else None
    continuity_state = proxy._websocket_continuity_index.get((session_id, api_key_id))
    if continuity_state is None or continuity_state.last_completed_response_id != previous_response_id:
        # HTTP and WebSocket entry points synthesize different turn-state
        # headers. The response id remains globally specific within the API-key
        # scope, so use its unique retained state when the direct key differs.
        matching_states = [
            state
            for (_continuity_key, continuity_api_key_id), state in proxy._websocket_continuity_index.items()
            if continuity_api_key_id == api_key_id and state.last_completed_response_id == previous_response_id
        ]
        if len(matching_states) != 1:
            return None
        continuity_state = matching_states[0]
    if not _facade()._input_prefix_matches_stored_context(
        input_value,
        stored_count=continuity_state.last_completed_input_count,
        stored_fingerprint=continuity_state.last_completed_input_prefix_fingerprint,
    ):
        return None
    fresh_payload = payload.model_copy(update={"previous_response_id": None})
    if not responses_payload_is_account_neutral_fresh_replay(fresh_payload.to_replay_safety_payload()):
        return None
    return fresh_payload


def _effective_http_downstream_transport_policy(
    api_key: ApiKeyData | None,
    dashboard_settings: Any,
    base_settings: Any,
) -> tuple[str, bool]:
    override = getattr(api_key, "transport_policy_override", None) if api_key is not None else None
    if override is not None:
        return override, True
    dashboard_policy = getattr(dashboard_settings, "http_downstream_transport_policy", None)
    if isinstance(dashboard_policy, str) and dashboard_policy:
        return dashboard_policy, False
    base_policy = getattr(base_settings, "http_downstream_transport_policy", _HTTP_DOWNSTREAM_TRANSPORT_POLICY_DEFAULT)
    return base_policy, False


def _resolved_configured_stream_transport(dashboard_settings: Any, base_settings: Any) -> tuple[str, bool]:
    configured = getattr(dashboard_settings, "upstream_stream_transport", "default")
    if configured == "default":
        configured = getattr(base_settings, "upstream_stream_transport", "auto")
    return configured, configured in ("http", "websocket")


def _http_bridge_allowed_by_transport_policy(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    api_key: ApiKeyData | None,
    dashboard_settings: Any,
    base_settings: Any,
) -> bool:
    """Apply ordinary HTTP transport precedence before entering the WS bridge."""

    configured_transport, explicit_transport = _resolved_configured_stream_transport(
        dashboard_settings,
        base_settings,
    )
    if explicit_transport:
        return configured_transport == "websocket"
    if _is_native_codex_request(headers):
        # A first-party Codex client owns its WebSocket -> HTTP fallback. Once
        # it submits HTTP, sticky metadata must not promote it back to WS.
        return False
    policy, _override_applied = _effective_http_downstream_transport_policy(
        api_key,
        dashboard_settings,
        base_settings,
    )
    return _resolve_http_downstream_transport(policy, payload=payload, headers=headers) == "websocket"


async def _iter_account_capacity_recovery_wait(
    *,
    request_id: str,
    model: str | None,
    account_id: str | None,
    error_message: str | None,
    recovery_sleep_seconds: float,
    deadline: float,
    emit_keepalives: bool,
    stage: str,
) -> AsyncIterator[str]:
    if not emit_keepalives:
        _signal_propagated_capacity_startup_wait()
    remaining_budget_seconds = _facade()._remaining_budget_seconds(deadline)
    if remaining_budget_seconds <= 0:
        return
    wait_started_at = time.monotonic()
    remaining_sleep_seconds = min(recovery_sleep_seconds, remaining_budget_seconds)
    _facade().logger.info(
        "Waiting for account capacity before retrying stream request_id=%s model=%s account_id=%s "
        "stage=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
        request_id,
        model,
        account_id,
        stage,
        remaining_sleep_seconds,
        recovery_sleep_seconds,
        error_message,
    )
    while remaining_sleep_seconds > 0:
        if emit_keepalives:
            yield format_sse_event(
                cast(
                    Mapping[str, Any],
                    _account_capacity_wait_payload(
                        None,
                        request_id=request_id,
                        reason=error_message,
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


def _payload_size_estimate_bytes(payload: ResponsesRequest) -> int:
    return len(json.dumps(payload.to_payload(), ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


class _StreamingRetryMixin:
    async def _stream_with_retry(
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
        request_transport: str,
        rewritten_file_account_id: str | None = None,
        file_account_resolution_complete: bool = False,
        upstream_stream_transport_override: str | None = None,
        client_ip: str | None = None,
        enforce_openai_sdk_contract: bool = True,
    ) -> AsyncIterator[str]:
        proxy = cast(_StreamingServiceProtocol, self)
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        request_id = ensure_request_id()
        start = time.monotonic()
        base_settings = _facade().get_settings()
        settings = await _facade().get_settings_cache().get()
        concurrency_caps = _facade().effective_account_concurrency_caps(settings)
        deadline = start + _facade()._stream_request_budget_seconds(
            base_settings,
            request_transport=request_transport,
        )
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        upstream_transport_policy_label = "explicit" if upstream_stream_transport_override is not None else "configured"
        upstream_transport_sticky = _http_downstream_request_is_sticky(payload, headers)
        preserve_native_failure_lifecycle = not enforce_openai_sdk_contract and _is_native_codex_request(headers)

        def _must_abort_native_transport_failure(
            exc: ProxyResponseError,
            *,
            downstream_visible: bool,
        ) -> bool:
            if not preserve_native_failure_lifecycle or downstream_visible:
                return False
            error = _parse_openai_error(exc.payload)
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            return error_code in SYNTHETIC_TRANSPORT_FAILURE_CODES

        upstream_stream_transport = upstream_stream_transport_override
        if upstream_stream_transport is None:
            configured_transport, explicit_transport = _resolved_configured_stream_transport(settings, base_settings)
            image_bypass = _facade()._responses_request_uses_image_generation(
                payload
            ) or _facade()._responses_request_contains_input_image(payload)
            resolved_base_transport = _resolve_stream_transport(
                settings=base_settings,
                transport=configured_transport,
                transport_override=None,
                model=payload.model,
                headers=headers,
                has_image_generation_tool=image_bypass,
                payload_size_estimate_bytes=_payload_size_estimate_bytes(payload),
            )
            upstream_stream_transport = resolved_base_transport
            if not explicit_transport and image_bypass:
                upstream_stream_transport = "http"
            if (
                not explicit_transport
                and request_transport == _REQUEST_TRANSPORT_HTTP
                and upstream_stream_transport == "websocket"
            ):
                sticky = upstream_transport_sticky
                if _is_native_codex_request(headers):
                    policy = "native_codex_http"
                    override_applied = False
                    upstream_transport_policy_label = policy
                    upstream_stream_transport = "http"
                else:
                    policy, override_applied = _effective_http_downstream_transport_policy(
                        api_key, settings, base_settings
                    )
                    upstream_transport_policy_label = policy
                    policy_transport = _resolve_http_downstream_transport(policy, payload=payload, headers=headers)
                    upstream_stream_transport = "http" if policy_transport == "http" else configured_transport
                logger.info(
                    "http_downstream_transport_decision policy=%s override_applied=%s sticky=%s "
                    "upstream_stream_transport=%s request_id=%s",
                    policy,
                    override_applied,
                    sticky,
                    upstream_stream_transport,
                    request_id,
                )
        elif request_transport == _REQUEST_TRANSPORT_HTTP:
            logger.info(
                "http_downstream_transport_decision policy=explicit override_applied=%s sticky=%s "
                "upstream_stream_transport=%s request_id=%s",
                False,
                _http_downstream_request_is_sticky(payload, headers),
                upstream_stream_transport,
                request_id,
            )
        if rewritten_file_account_id is None and not file_account_resolution_complete:
            proxy._raise_for_unsupported_input_image_references(payload)
            rewritten_file_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
            api_key=api_key,
        )
        turn_state_owner_account_id: str | None = None
        turn_state = _sticky_key_from_turn_state_header(headers)
        if turn_state is not None:
            # HTTP and WebSocket transports share the bridge turn-state index;
            # treating this as ordinary sticky input would cross replicas or
            # accounts when the token was minted by an HTTP bridge session.
            turn_state_owner_account_id = await proxy._resolve_compact_turn_state_owner(
                turn_state=turn_state,
                api_key=api_key,
                fail_on_missing=not _is_synthesized_turn_state(turn_state),
            )
        sticky_key_source = "none"
        if affinity.codex_session_source == "thread_header":
            sticky_key_source = "thread_header"
        elif affinity.kind == StickySessionKind.CODEX_SESSION:
            sticky_key_source = "session_header"
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )
        routing_strategy = _facade()._routing_strategy(settings)
        max_attempts = _facade()._STREAM_MAX_ACCOUNT_ATTEMPTS
        settled = False
        any_attempt_logged = False
        upstream_transport_metric_status: str | None = None
        upstream_transport_metric_recorded = False
        network_recovery = ProcessNetworkRecovery(transport="stream", request_id=request_id)
        settlement = _StreamSettlement()
        last_transient_exc: ProxyResponseError | None = None
        last_pre_dispatch_transport_error: ProxyResponseError | None = None
        last_account_model_rejection: ProxyResponseError | None = None
        last_account_model_rejection_account_id: str | None = None
        account_model_replacement_account_id: str | None = None
        account_model_replay_attempted = False
        current_account_lease: AccountLease | None = None
        last_security_work_retry_error: _RetryableStreamError | None = None
        excluded_account_ids: set[str] = set()
        transient_failed_account_id: str | None = None
        hard_affinity_same_owner_retry_attempted = False
        deferred_capacity_account: Account | None = None
        deferred_capacity_lease: AccountLease | None = None
        preferred_account_id: str | None = None
        payload_replay_required_account_id: str | None = None
        file_preferred_account_id: str | None = rewritten_file_account_id
        require_preferred_account = False
        last_retryable_stream_error: _RetryableStreamError | None = None
        pending_post_refresh_transient_penalties: list[tuple[Account, UpstreamError, str, int | None, int]] = []
        deferred_account_error_backoffs: dict[str, Account] = {}
        post_refresh_transient_replacement_selected = False
        require_security_work_authorized = False
        account_leases: list[AccountLease] = []
        estimated_lease_tokens = _facade()._estimated_lease_tokens_from_request_usage_budget(
            estimate_api_key_request_usage(payload)
        )
        verified_fresh_replay_payload = _verified_cross_transport_fresh_replay(
            proxy,
            payload=payload,
            headers=headers,
            api_key=api_key,
        )

        async def _release_tracked_stream_lease(lease: AccountLease | None) -> None:
            if lease is None:
                return
            try:
                account_leases.remove(lease)
            except ValueError:
                pass
            await proxy._load_balancer.release_account_lease(lease)

        def _render_dispatch_transport_error(exc: ProxyResponseError) -> str:
            # Terminal render of the preserved sanitized transport failure:
            # the client sees the original upstream-unavailable error instead
            # of a misleading generated ``no_accounts`` response.
            error = _parse_openai_error(exc.payload)
            error_code = (
                _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                or "upstream_unavailable"
            )
            error_message = error.message if error and error.message else "Upstream transport failed"
            event = response_failed_event(
                error_code,
                error_message,
                error_type=(error.type if error else None) or "server_error",
                response_id=request_id,
                error_param=error.param_state if error else None,
            )
            _apply_error_metadata(event["response"]["error"], error)
            return format_sse_event(event)

        async def _flush_pending_post_refresh_penalties() -> None:
            # Consume one queued penalty at a time only after that entry's
            # health write finishes (success or logged failure). Clearing the
            # whole queue first would drop later accounts if one write raises
            # or cancellation lands on an await (compact flush isolates entries
            # the same way). Each entry runs in an owned cancellation-deferring
            # task so CancelledError mid-write cannot leave a half-applied
            # tuple for cleanup to replay (double error_count).
            while pending_post_refresh_transient_penalties:
                (
                    failed_account,
                    transient_error_payload,
                    transient_error_code,
                    transient_http_status,
                    transient_retry_count,
                ) = pending_post_refresh_transient_penalties[0]

                async def _apply_deferred_penalty(
                    account: Account = failed_account,
                    error_payload: UpstreamError = transient_error_payload,
                    error_code: str = transient_error_code,
                    http_status: int | None = transient_http_status,
                    retry_count: int = transient_retry_count,
                ) -> None:
                    try:
                        await proxy._handle_stream_error(
                            account,
                            error_payload,
                            error_code,
                            http_status=http_status,
                        )
                        if retry_count > 1:
                            await proxy._load_balancer.record_errors(account, retry_count - 1)
                    except Exception:
                        logger.warning(
                            "Failed to flush deferred keyed stream health account_id=%s request_id=%s",
                            account.id,
                            request_id,
                            exc_info=True,
                        )

                apply_task = asyncio.create_task(
                    _apply_deferred_penalty(),
                    name=f"flush-deferred-keyed-stream-health-{failed_account.id}-{request_id}",
                )
                _, cancellation = await _await_task_deferring_cancellation(apply_task)
                del pending_post_refresh_transient_penalties[0]
                if cancellation is not None:
                    raise cancellation

        async def _flush_or_schedule_pending_post_refresh_penalties() -> None:
            if not pending_post_refresh_transient_penalties:
                return
            flush_coro = _flush_pending_post_refresh_penalties()
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                proxy._schedule_cancel_safe_cleanup(
                    flush_coro,
                    action="flush_deferred_keyed_stream_health",
                    request_id=request_id,
                )
                return
            await flush_coro

        async def _settle_stream_usage_before_pending_penalty(
            current_settlement: _StreamSettlement,
            *,
            settlement_order_required: bool = False,
        ) -> bool:
            nonlocal settled
            apply_pending_penalty = post_refresh_transient_replacement_selected and bool(
                pending_post_refresh_transient_penalties
            )
            wait_for_health_write = (
                settlement_order_required
                or current_settlement.settlement_order_required
                or apply_pending_penalty
                or bool(deferred_account_error_backoffs)
            )
            settle_kwargs = {"wait_for_settlement": True} if wait_for_health_write else {}
            settled_result = await proxy._settle_stream_api_key_usage(
                api_key,
                api_key_reservation,
                current_settlement,
                request_id,
                **settle_kwargs,
            )
            if not settled_result:
                return False
            # Commit settlement visibility before any cancellable health flush.
            # CancelledError during flush must not leave ``settled`` false while
            # ``usage_settlement_transferred`` is already true — that combination
            # skips both reservation cleanup and retained-queue flush.
            settled = True
            try:
                await proxy._drain_deferred_account_error_backoffs(deferred_account_error_backoffs)
            finally:
                # Route backoffs and queued stream penalties have independent
                # ownership after settlement. A failed backoff write must not
                # orphan the penalty queue in detached cancellation cleanup.
                if apply_pending_penalty:
                    await _flush_pending_post_refresh_penalties()
            return settled_result

        async def _record_or_defer_confirmed_route_backoff(account: Account) -> None:
            if api_key is not None and api_key_reservation is not None:
                deferred_account_error_backoffs.setdefault(account.id, account)
                return
            await proxy._load_balancer.record_error_backoff(account)

        async def _handle_or_defer_keyed_stream_health(
            failed_account: Account,
            failed_error: UpstreamError,
            failed_code: str,
            *,
            http_status: int | None = None,
            transient_retry_count: int = 1,
        ) -> ClassifiedFailure:
            """Classify and either write health now or defer until after settle.

            Keyed streams keep a shared ``api_key_reservation`` open across
            mid-loop failover ``continue``/``break`` paths. Immediate
            ``_handle_stream_error`` / ``record_errors`` on those paths would
            run while the reservation is still open; queue into
            ``pending_post_refresh_transient_penalties`` instead so
            ``_settle_stream_usage_before_pending_penalty`` flushes health
            after settlement (same ordering as compact keyed mid-loop health).
            """
            if api_key is not None and api_key_reservation is not None:
                pending_post_refresh_transient_penalties.append(
                    (
                        failed_account,
                        failed_error,
                        failed_code,
                        http_status,
                        transient_retry_count,
                    )
                )
                return classify_upstream_failure(
                    error_code=failed_code,
                    error=failed_error,
                    http_status=http_status,
                    phase="first_event",
                )
            classified = await proxy._handle_stream_error(
                failed_account,
                failed_error,
                failed_code,
                http_status=http_status,
            )
            if transient_retry_count > 1:
                await proxy._load_balancer.record_errors(failed_account, transient_retry_count - 1)
            return classified

        async def _drain_pending_post_refresh_penalty_on_terminal(
            current_settlement: _StreamSettlement,
        ) -> bool:
            nonlocal post_refresh_transient_replacement_selected, settled
            if pending_post_refresh_transient_penalties or deferred_account_error_backoffs:
                # A failed replacement selection still ends the request. Mark
                # it as terminal so the deferred failure is settled and
                # recorded before this path returns or re-raises.
                if pending_post_refresh_transient_penalties:
                    post_refresh_transient_replacement_selected = True
                settled = await _settle_stream_usage_before_pending_penalty(current_settlement)
                return settled
            return True

        async def _finalize_terminal_settlement_after_downstream_close(
            current_settlement: _StreamSettlement,
            account: Account,
            *,
            settlement_order_required: bool = False,
        ) -> None:
            nonlocal settled

            async def _finalize() -> None:
                nonlocal settled
                if not settled:
                    settled = await _settle_stream_usage_before_pending_penalty(
                        current_settlement,
                        settlement_order_required=settlement_order_required,
                    )
                if not settled:
                    return
                if current_settlement.account_health_error:
                    await proxy._handle_stream_error(
                        account,
                        _stream_settlement_error_payload(current_settlement),
                        current_settlement.error_code or "upstream_error",
                    )
                elif current_settlement.record_success:
                    await proxy._load_balancer.record_success(account)

            finalize_task = asyncio.create_task(_finalize(), name=f"stream-terminal-settlement-{request_id}")
            _, cancellation = await _await_task_deferring_cancellation(finalize_task)
            if cancellation is not None:
                raise cancellation

        async def _wait_for_process_network_recovery(
            account: Account,
            *,
            error_code: str | None,
            retryable_same_contract: bool,
            failed_session: aiohttp.ClientSession | None = None,
        ) -> NetworkRecoveryDecision:
            network_recovery.account_id = account.id
            return await network_recovery.wait(
                error_code=error_code,
                retryable_same_contract=retryable_same_contract,
                deadline=deadline,
                rotate_shared_client=True,
                failed_session=failed_session,
            )

        async def _settle_process_network_budget_exhaustion(
            account: Account,
            settlement: _StreamSettlement,
        ) -> None:
            nonlocal settled
            settlement.status = "error"
            settlement.record_success = False
            settlement.account_health_error = False
            settlement.error_code = "upstream_request_timeout"
            settlement.error_message = "Proxy request budget exhausted"
            settlement.error = {"message": "Proxy request budget exhausted"}
            await proxy._write_stream_preflight_error(
                account_id=account.id,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                start=start,
                error_code="upstream_request_timeout",
                error_message="Proxy request budget exhausted",
                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                service_tier=payload.service_tier,
                transport=request_transport,
                upstream_transport=upstream_stream_transport,
                useragent=useragent,
                useragent_group=useragent_group,
                conversation_id=conversation_id,
                client_ip=client_ip,
            )
            settled = await _settle_stream_usage_before_pending_penalty(settlement)

        def _authorize_payload_dispatch(account: Account) -> bool:
            required_account_id = payload_replay_required_account_id
            if required_account_id is not None and required_account_id != account.id:
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "previous_response_owner_unavailable",
                        "Request payload owner account is unavailable; retry later.",
                        error_type="server_error",
                    ),
                )
            return required_account_id is None and not responses_payload_is_account_neutral_fresh_replay(
                payload.to_replay_safety_payload()
            )

        def _move_verified_fresh_replay_from_owner(*, account_id: str, outcome: str) -> bool:
            # Only a proxy-injected owner anchor with locally verified full
            # input may move; the failed owner stays excluded so sticky
            # selection cannot immediately loop back to it.
            nonlocal affinity, payload, payload_replay_required_account_id
            nonlocal preferred_account_id, require_preferred_account, verified_fresh_replay_payload
            if not (
                require_preferred_account
                and preferred_account_id == account_id
                and verified_fresh_replay_payload is not None
            ):
                return False
            if not responses_payload_is_account_neutral_fresh_replay(
                verified_fresh_replay_payload.to_replay_safety_payload()
            ):
                return False
            payload = verified_fresh_replay_payload
            payload_replay_required_account_id = None
            verified_fresh_replay_payload = None
            excluded_account_ids.add(account_id)
            preferred_account_id = None
            require_preferred_account = False
            affinity = replace(affinity, reallocate_sticky=True)
            logger.info(
                "cross_transport_verified_fresh_replay request_id=%s outcome=%s account_id=%s",
                request_id,
                outcome,
                account_id,
            )
            return True

        async def _stream_post_refresh_with_capacity_recovery(
            account: Account,
            *,
            settlement: _StreamSettlement,
            can_try_other_account: bool,
            tool_call_dedupe: _WebSocketUpstreamControl,
        ) -> AsyncGenerator[str, None]:
            nonlocal last_transient_exc
            transient_retries = 0

            async def _iter_stream_once() -> AsyncGenerator[str, None]:
                inner_stream = proxy._stream_once(
                    account,
                    payload,
                    headers,
                    request_id,
                    False,
                    request_started_at=start,
                    allow_transient_retry=True,
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    settlement=settlement,
                    suppress_text_done_events=suppress_text_done_events,
                    upstream_stream_transport=upstream_stream_transport,
                    request_transport=request_transport,
                    concurrency_caps=concurrency_caps,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    conversation_id=conversation_id,
                    client_ip=client_ip,
                    tool_call_dedupe=tool_call_dedupe,
                    enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                )
                try:
                    try:
                        async for line in inner_stream:
                            yield line
                    finally:
                        close_task = asyncio.create_task(
                            inner_stream.aclose(),
                            name=f"stream-post-refresh-inner-close-{request_id}",
                        )
                        _, close_cancellation = await _await_task_deferring_cancellation(close_task)
                        if close_cancellation is not None:
                            raise close_cancellation
                except ProxyResponseError as exc:
                    if is_confirmed_pre_dispatch_transport_error(exc):
                        # Keep dispatch provenance intact for the outer account
                        # failover handler. Converting this into the generic
                        # transient wrapper would authorize same-account replay
                        # and lose the confirmed dead-route backoff semantics.
                        raise
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(
                        error.code if error else None,
                        error.type if error else None,
                    )
                    error_message = error.message if error else None
                    retryable_connect_failure = bool(
                        exc.failure_phase == "connect"
                        and _facade()._should_retry_transient_stream_error(error_code, error_message)
                    )
                    retryable_capacity_rejection = bool(
                        is_upstream_model_capacity_error(error_message)
                        and _facade()._should_retry_transient_stream_error(error_code, error_message)
                    )
                    if retryable_connect_failure or retryable_capacity_rejection:
                        raise _TransientStreamError(
                            error_code or "upstream_error",
                            _upstream_error_from_openai(error),
                            account_health_error=retryable_connect_failure,
                        ) from exc
                    raise

            while True:
                settlement.reset()
                stream_timeout_tokens = _facade()._push_stream_attempt_timeout_overrides(
                    _facade()._remaining_budget_seconds(deadline)
                )
                try:
                    attempt_stream = _iter_stream_once()
                    try:
                        try:
                            async for line in attempt_stream:
                                yield line
                        finally:
                            close_task = asyncio.create_task(
                                attempt_stream.aclose(),
                                name=f"stream-post-refresh-close-{request_id}",
                            )
                            _, close_cancellation = await _await_task_deferring_cancellation(close_task)
                            if close_cancellation is not None:
                                raise close_cancellation
                    except (asyncio.CancelledError, GeneratorExit):
                        # A terminal frame may already have been yielded when
                        # downstream cancellation is delivered on the next
                        # generator resume. Finalize that terminal usage and
                        # health result before propagating cancellation so the
                        # reservation is not released as abandoned.
                        if settlement.status in {"success", "error"} and not settled:
                            await _finalize_terminal_settlement_after_downstream_close(
                                settlement,
                                account,
                                settlement_order_required=True,
                            )
                        raise
                    network_recovery.log_recovered()
                    return
                except _TerminalStreamError:
                    # `_stream_once()` has already yielded the terminal event.
                    # Returning preserves fail-closed delivery: replaying here
                    # could duplicate a request that reached the upstream.
                    return
                except _TransientStreamError as exc:
                    recovery_decision = await _wait_for_process_network_recovery(
                        account,
                        error_code=exc.code,
                        retryable_same_contract=False,
                        failed_session=None,
                    )
                    if recovery_decision == "retry":
                        continue
                    if recovery_decision == "exhausted":
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_request_timeout", "Proxy request budget exhausted"),
                        ) from exc
                    transient_retries += 1
                    if (
                        transient_retries < _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                        and _facade()._remaining_budget_seconds(deadline) > 0
                        and not settlement.downstream_visible
                    ):
                        delay = backoff_seconds(transient_retries)
                        _facade().logger.info(
                            "Transient post-refresh stream error, retrying same account "
                            "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                            request_id,
                            account.id,
                            transient_retries,
                            _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                            delay,
                            exc.code,
                        )
                        await asyncio.sleep(delay)
                        continue
                    error_message = str(exc.error.get("message") or "Upstream error")
                    settlement.record_success = False
                    settlement.error_code = exc.code
                    settlement.error_message = error_message
                    settlement.error = exc.error
                    settlement.account_health_error = (
                        _facade()._should_penalize_stream_error(exc.code)
                        or is_upstream_model_capacity_error(error_message)
                        or exc.account_health_error
                    )
                    if settlement.account_health_error:
                        pending_post_refresh_transient_penalties.append(
                            (
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                                502,
                                transient_retries,
                            )
                        )
                    if can_try_other_account:
                        retry_exc = ProxyResponseError(502, openai_error(exc.code, error_message))
                        setattr(retry_exc, _POST_REFRESH_TRANSIENT_EXHAUSTED_ATTR, True)
                        raise retry_exc from exc
                    yield format_sse_event(
                        response_failed_event(
                            exc.code,
                            error_message,
                            response_id=settlement.response_id or request_id,
                        )
                    )
                    return
                except ProxyResponseError as exc:
                    if _must_abort_native_transport_failure(
                        exc,
                        downstream_visible=settlement.downstream_visible,
                    ):
                        raise
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(
                        error.code if error else None,
                        error.type if error else None,
                    )
                    recovery_decision = await _wait_for_process_network_recovery(
                        account,
                        error_code=error_code,
                        retryable_same_contract=exc.retryable_same_contract,
                        failed_session=exc.failed_session,
                    )
                    if recovery_decision == "retry":
                        continue
                    if recovery_decision == "exhausted":
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_request_timeout", "Proxy request budget exhausted"),
                        ) from exc
                    if error_code != "account_response_create_cap":
                        raise
                    last_transient_exc = exc
                    if can_try_other_account:
                        raise
                    recovery_sleep_seconds = _account_selection_recovery_sleep_seconds(
                        AccountSelection(
                            account=None,
                            error_message=error.message if error else None,
                            error_code=error_code,
                        )
                    )
                    if recovery_sleep_seconds is None or _facade()._remaining_budget_seconds(deadline) <= 0:
                        raise
                    async for wait_event in _iter_account_capacity_recovery_wait(
                        request_id=request_id,
                        model=payload.model,
                        account_id=account.id,
                        error_message=error.message if error else None,
                        recovery_sleep_seconds=recovery_sleep_seconds,
                        deadline=deadline,
                        emit_keepalives=not propagate_http_errors or not enforce_openai_sdk_contract,
                        stage="post_refresh_response_create",
                    ):
                        yield wait_event
                    if _facade()._remaining_budget_seconds(deadline) <= 0:
                        raise
                finally:
                    pop_stream_timeout_overrides(stream_timeout_tokens)

        def _record_upstream_transport_metric_once(status: str) -> None:
            nonlocal upstream_transport_metric_recorded
            if upstream_transport_metric_recorded:
                return
            upstream_transport_metric_recorded = True
            _record_upstream_transport_decision(
                downstream_transport=request_transport,
                upstream_transport=upstream_stream_transport,
                policy=upstream_transport_policy_label,
                sticky=upstream_transport_sticky,
                status=status,
            )

        async def _render_account_model_rejection(
            exc: ProxyResponseError,
            *,
            account_id: str | None,
        ) -> str:
            error = _parse_openai_error(exc.payload)
            error_code = (
                _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                or "invalid_request_error"
            )
            error_message = error.message if error and error.message else "Upstream rejected the requested model"
            event = response_failed_event(
                error_code,
                error_message,
                error_type=(error.type if error else None) or "invalid_request_error",
                response_id=request_id,
                error_param=error.param_state if error else None,
            )
            _apply_error_metadata(event["response"]["error"], error)
            if not any_attempt_logged:
                await proxy._write_request_log(
                    account_id=account_id,
                    api_key=api_key,
                    request_id=request_id,
                    model=payload.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code=error_code,
                    error_message=error_message,
                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                    transport=request_transport,
                    upstream_transport=upstream_stream_transport,
                    service_tier=payload.service_tier,
                    requested_service_tier=payload.service_tier,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    conversation_id=conversation_id,
                    client_ip=client_ip,
                )
            return format_sse_event(event)

        async def _retry_account_model_rejection(
            exc: ProxyResponseError,
            account: Account,
            *,
            outcome: str,
        ) -> bool | None:
            nonlocal affinity, current_account_lease
            nonlocal account_model_replay_attempted
            nonlocal last_account_model_rejection, last_account_model_rejection_account_id
            error = _parse_openai_error(exc.payload)
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            if not _is_account_model_unsupported_error(
                code=error_code,
                message=error.message if error else None,
                model=payload.model,
            ):
                return None
            can_move_verified_owner = bool(
                require_preferred_account
                and preferred_account_id == account.id
                and verified_fresh_replay_payload is not None
            )
            can_try_other_account = bool(
                not account_model_replay_attempted
                and attempt < max_attempts - 1
                and (
                    can_move_verified_owner
                    or (not require_preferred_account and account.id != file_preferred_account_id)
                )
            )
            if not can_try_other_account:
                return False
            logger.info(
                "Retrying stream after account/model rejection request_id=%s account_id=%s model=%s phase=%s reason=%s",
                request_id,
                account.id,
                payload.model,
                outcome,
                _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
            )
            account_model_replay_attempted = True
            last_account_model_rejection = exc
            last_account_model_rejection_account_id = account.id
            await _release_tracked_stream_lease(current_account_lease)
            current_account_lease = None
            if not _move_verified_fresh_replay_from_owner(
                account_id=account.id,
                outcome=outcome,
            ):
                excluded_account_ids.add(account.id)
                affinity = replace(affinity, reallocate_sticky=True)
            return True

        try:
            # From this exact point the service finalizer below owns reservation
            # settlement/release. Preflight failures before this boundary are
            # still owned by the originating API startup guard.
            _signal_propagated_responses_service_cleanup_ready()
            if payload.previous_response_id is not None:
                previous_response_lookup_session_id = _owner_lookup_session_id_from_headers(headers)
                preferred_account_id = await proxy._resolve_websocket_previous_response_owner(
                    previous_response_id=payload.previous_response_id,
                    api_key=api_key,
                    session_id=previous_response_lookup_session_id,
                    surface="http_stream",
                )
                require_preferred_account = preferred_account_id is not None
                # `previous_response_id` is a stored-object continuation, so it
                # remains hard owner-bound even when the request also carries a
                # soft prompt-cache affinity key. A different account may have a
                # warmer cache, but it cannot safely resolve the stored response.
                if preferred_account_id is None:
                    selection_inputs = await proxy._load_balancer._load_selection_inputs(
                        model=payload.model,
                        additional_limit_name=None,
                        account_ids=None,
                    )
                    if len(selection_inputs.accounts) != 1:
                        message = "Previous response owner account is unavailable; retry later."
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason="owner_account_unavailable",
                            previous_response_id=payload.previous_response_id,
                            session_id=previous_response_lookup_session_id,
                            upstream_error_code="owner_lookup_miss",
                        )
                        event = response_failed_event(
                            "previous_response_owner_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code="previous_response_owner_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        return
            # File and previous-response ownership are peers, not fallback
            # preferences. Resolve both before selection so a conflict cannot
            # be hidden by whichever source happened to run first. A hard turn
            # state is checked against this required owner by the balancer.
            preferred_account_id = resolve_required_account_id(
                ("turn state", turn_state_owner_account_id),
                ("previous response", preferred_account_id),
                ("input file", rewritten_file_account_id),
            )
            require_preferred_account = require_preferred_account or turn_state_owner_account_id is not None
            file_required_preferred_account = rewritten_file_account_id is not None
            for attempt in range(max_attempts):
                remaining_budget = _facade()._remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                    _facade().logger.warning(
                        "Proxy request budget exhausted before retry request_id=%s attempt=%s",
                        request_id,
                        attempt + 1,
                    )
                    await proxy._write_stream_preflight_error(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        start=start,
                        error_code="upstream_request_timeout",
                        error_message="Proxy request budget exhausted",
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        service_tier=payload.service_tier,
                        transport=request_transport,
                        upstream_transport=upstream_stream_transport,
                        useragent=useragent,
                        useragent_group=useragent_group,
                        conversation_id=conversation_id,
                        client_ip=client_ip,
                    )
                    if preserve_native_failure_lifecycle:
                        return
                    yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                    return
                while True:
                    effective_preferred_account_id = resolve_required_account_id(
                        ("continuation", preferred_account_id),
                        ("dispatched payload", payload_replay_required_account_id),
                    )
                    effective_require_preferred_account = (
                        require_preferred_account or payload_replay_required_account_id is not None
                    )
                    try:
                        selection = await proxy._select_account_with_budget_compatible(
                            deadline,
                            request_id=request_id,
                            kind="stream",
                            api_key=api_key,
                            affinity_policy=affinity,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            prefer_earlier_reset_window=_facade()._prefer_earlier_reset_window(settings),
                            routing_strategy=routing_strategy,
                            model=payload.model,
                            service_tier=payload.service_tier,
                            exclude_account_ids=excluded_account_ids,
                            preferred_account_id=effective_preferred_account_id,
                            require_security_work_authorized=require_security_work_authorized,
                            lease_kind="stream",
                            estimated_lease_tokens=estimated_lease_tokens,
                            # Keep stored-object and file ownership strict. The
                            # verified-fresh replay branch below removes its
                            # anchor before it permits cross-account movement.
                            fallback_on_preferred_account_unavailable=not (
                                effective_require_preferred_account or file_required_preferred_account
                            ),
                        )
                    except ProxyResponseError as exc:
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        error = _parse_openai_error(exc.payload)
                        error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                        error_message = error.message if error else None
                        if _facade()._is_proxy_budget_exhausted_error(exc):
                            await proxy._write_stream_preflight_error(
                                account_id=None,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                upstream_transport=upstream_stream_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                conversation_id=conversation_id,
                                client_ip=client_ip,
                            )
                            if preserve_native_failure_lifecycle:
                                return
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        event = response_failed_event(
                            error_code,
                            error_message or "Upstream unavailable",
                            error_type=(error.type or "server_error") if error else "server_error",
                            response_id=request_id,
                        )
                        _apply_error_metadata(event["response"]["error"], error)
                        yield format_sse_event(event)
                        return
                    account = selection.account
                    current_account_lease = selection.lease
                    if selection.lease is not None:
                        account_leases.append(selection.lease)
                    if (
                        not account
                        and require_security_work_authorized
                        and selection.error_code == _facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
                    ):
                        _facade().logger.info(
                            "No security-work-authorized account available for stream retry; "
                            "continuing normal account failover request_id=%s",
                            request_id,
                        )
                        yield format_sse_event(
                            _facade()._security_work_advisory_event(
                                code=_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                                message=_facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                                request_id=request_id,
                                action="continue_normal_selection",
                            )
                        )
                        require_security_work_authorized = False
                        continue
                    if not account and deferred_capacity_account is not None:
                        deferred_error = _parse_openai_error(last_transient_exc.payload) if last_transient_exc else None
                        recovery_sleep_seconds = _account_selection_recovery_sleep_seconds(
                            AccountSelection(
                                account=None,
                                error_message=deferred_error.message if deferred_error else None,
                                error_code="account_response_create_cap",
                            )
                        )
                        if recovery_sleep_seconds is not None:
                            remaining_budget_seconds = _facade()._remaining_budget_seconds(deadline)
                            if remaining_budget_seconds <= 0:
                                if propagate_http_errors and last_transient_exc is not None:
                                    raise last_transient_exc
                                event = response_failed_event(
                                    "account_response_create_cap",
                                    (deferred_error.message if deferred_error else None)
                                    or "Account response-create concurrency limit reached",
                                    error_type=(deferred_error.type if deferred_error else None) or "server_error",
                                    response_id=request_id,
                                )
                                yield format_sse_event(event)
                                return
                            capacity_account = deferred_capacity_account
                            capacity_account_id = capacity_account.id
                            excluded_account_ids.discard(capacity_account_id)
                            async for wait_event in _iter_account_capacity_recovery_wait(
                                request_id=request_id,
                                model=payload.model,
                                account_id=capacity_account_id,
                                error_message=deferred_error.message if deferred_error else None,
                                recovery_sleep_seconds=recovery_sleep_seconds,
                                deadline=deadline,
                                emit_keepalives=not propagate_http_errors or not enforce_openai_sdk_contract,
                                stage="response_create_no_alternate",
                            ):
                                yield wait_event
                            if _facade()._remaining_budget_seconds(deadline) <= 0:
                                if propagate_http_errors and last_transient_exc is not None:
                                    raise last_transient_exc
                                event = response_failed_event(
                                    "account_response_create_cap",
                                    (deferred_error.message if deferred_error else None)
                                    or "Account response-create concurrency limit reached",
                                    error_type=(deferred_error.type if deferred_error else None) or "server_error",
                                    response_id=request_id,
                                )
                                yield format_sse_event(event)
                                return
                            account = capacity_account
                            current_account_lease = deferred_capacity_lease
                            deferred_capacity_account = None
                            deferred_capacity_lease = None
                    if account is not None and deferred_capacity_account is not None:
                        await _release_tracked_stream_lease(deferred_capacity_lease)
                        deferred_capacity_account = None
                        deferred_capacity_lease = None
                    if (
                        not account
                        and selection.error_code == "hard_affinity_saturated"
                        and transient_failed_account_id is not None
                        and transient_failed_account_id in excluded_account_ids
                        and not hard_affinity_same_owner_retry_attempted
                    ):
                        # A hard CODEX_SESSION row can resolve only to its owner.
                        # If the transport just excluded that same account after
                        # a pre-visible transient failure, alternate selection is
                        # impossible by construction. Retry the known owner once
                        # without weakening or rebinding the durable affinity.
                        excluded_account_ids.discard(transient_failed_account_id)
                        hard_affinity_same_owner_retry_attempted = True
                        _facade().logger.info(
                            "Retrying transient stream failure on hard affinity owner request_id=%s account_id=%s",
                            request_id,
                            transient_failed_account_id,
                        )
                        continue
                    if (
                        not account
                        and selection.error_code != USAGE_LIMIT_REACHED
                        and (
                            selection.error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES
                            or not (propagate_http_errors and last_transient_exc is not None)
                        )
                        and (
                            selection.error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES
                            # A preserved confirmed pre-dispatch failure is
                            # terminal for this request: waiting for capacity
                            # recovery cannot resurrect the dead proxy route.
                            or last_pre_dispatch_transport_error is None
                        )
                        and (
                            selection.error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES
                            or (last_retryable_stream_error is None and last_security_work_retry_error is None)
                        )
                    ):
                        recovery_sleep_seconds = _account_selection_recovery_sleep_seconds(selection)
                        if recovery_sleep_seconds is not None:
                            remaining_budget_seconds = _facade()._remaining_budget_seconds(deadline)
                            if remaining_budget_seconds <= 0:
                                break
                            async for wait_event in _iter_account_capacity_recovery_wait(
                                request_id=request_id,
                                model=payload.model,
                                account_id=None,
                                error_message=selection.error_message,
                                recovery_sleep_seconds=recovery_sleep_seconds,
                                deadline=deadline,
                                emit_keepalives=not propagate_http_errors or not enforce_openai_sdk_contract,
                                stage="selection",
                            ):
                                yield wait_event
                            if _facade()._remaining_budget_seconds(deadline) <= 0:
                                break
                            continue
                    break
                if not account:
                    if last_account_model_rejection is not None:
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        if propagate_http_errors:
                            raise last_account_model_rejection
                        yield await _render_account_model_rejection(
                            last_account_model_rejection,
                            account_id=last_account_model_rejection_account_id,
                        )
                        return
                    if last_pre_dispatch_transport_error is not None:
                        # No eligible replacement exists: preserve the original
                        # sanitized upstream-unavailable failure instead of
                        # generating a misleading ``no_accounts`` response.
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        if propagate_http_errors:
                            raise last_pre_dispatch_transport_error
                        yield _render_dispatch_transport_error(last_pre_dispatch_transport_error)
                        return
                    if selection.error_code == USAGE_LIMIT_REACHED:
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        no_accounts_msg = selection.error_message or "Usage limit reached"
                        status_code, error_payload = selection_failure_response(selection)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=USAGE_LIMIT_REACHED,
                            error_message=no_accounts_msg,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            client_ip=client_ip,
                        )
                        if propagate_http_errors:
                            raise ProxyResponseError(status_code, error_payload)
                        yield format_sse_event(
                            response_failed_event(
                                USAGE_LIMIT_REACHED,
                                no_accounts_msg,
                                error_type=USAGE_LIMIT_REACHED,
                                response_id=request_id,
                                resets_at=selection.resets_at,
                            )
                        )
                        return
                    if selection.error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES:
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        no_accounts_msg = selection.error_message or "Local account capacity is exhausted"
                        error_code = selection.error_code
                        event = response_failed_event(
                            error_code,
                            no_accounts_msg,
                            error_type="rate_limit_error",
                            response_id=request_id,
                        )
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=error_code,
                            error_message=no_accounts_msg,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        if propagate_http_errors:
                            raise ProxyResponseError(
                                429,
                                openai_error(
                                    error_code,
                                    no_accounts_msg,
                                    error_type="rate_limit_error",
                                ),
                            )
                        yield format_sse_event(event)
                        return
                    if (
                        require_preferred_account
                        and preferred_account_id is not None
                        and verified_fresh_replay_payload is not None
                    ):
                        excluded_account_ids.add(preferred_account_id)
                        payload = verified_fresh_replay_payload
                        verified_fresh_replay_payload = None
                        preferred_account_id = None
                        require_preferred_account = False
                        affinity = replace(affinity, reallocate_sticky=True)
                        logger.info(
                            "cross_transport_verified_fresh_replay request_id=%s outcome=owner_unavailable",
                            request_id,
                        )
                        continue
                    await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                    if propagate_http_errors and last_transient_exc is not None:
                        raise last_transient_exc
                    if last_retryable_stream_error is not None:
                        error_message = str(last_retryable_stream_error.error.get("message") or "Upstream error")
                        event = response_failed_event(
                            last_retryable_stream_error.code,
                            error_message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=last_retryable_stream_error.code,
                            error_message=error_message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        return
                    if require_preferred_account and preferred_account_id is not None:
                        error_code = "previous_response_owner_unavailable"
                        message = "Previous response owner account is unavailable; retry later."
                        reason = "owner_account_unavailable"
                        upstream_error_code = "no_accounts"
                        if selection.error_code == "continuity_owner_conflict":
                            error_code = "continuity_owner_conflict"
                            message = (
                                selection.error_message
                                or "Account-owned continuity sources conflict; retry the logical turn"
                            )
                            reason = "owner_conflict"
                            upstream_error_code = selection.error_code
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason=reason,
                            previous_response_id=payload.previous_response_id,
                            session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                            upstream_error_code=upstream_error_code,
                        )
                        event = response_failed_event(
                            error_code,
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=preferred_account_id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=error_code,
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        return
                    # If a prior attempt stored a transient 500 and the caller
                    # expects HTTP error propagation, re-raise the original error
                    # instead of returning a generic no_accounts event.
                    if propagate_http_errors and last_transient_exc is not None:
                        raise last_transient_exc
                    if last_security_work_retry_error is not None:
                        message = (
                            last_security_work_retry_error.error.get("message")
                            or "Security work authorization is required"
                        )
                        event = response_failed_event(
                            last_security_work_retry_error.code,
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=last_security_work_retry_error.code,
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        return
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    error_code = selection.error_code or "no_accounts"
                    event = response_failed_event(
                        error_code,
                        no_accounts_msg,
                        error_type="rate_limit_error"
                        if error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES
                        else "server_error",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await proxy._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code=error_code,
                        error_message=no_accounts_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        upstream_transport=upstream_stream_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                        conversation_id=conversation_id,
                        client_ip=client_ip,
                    )
                    return

                if pending_post_refresh_transient_penalties:
                    post_refresh_transient_replacement_selected = True

                account_id_value = account.id
                if last_pre_dispatch_transport_error is not None:
                    # The preserved connect failure is only authoritative when
                    # replacement selection is empty. Once another account is
                    # actually attempted, its terminal outcome takes precedence.
                    if last_transient_exc is last_pre_dispatch_transport_error:
                        last_transient_exc = None
                    last_pre_dispatch_transport_error = None
                if last_account_model_rejection is not None and account.id != last_account_model_rejection_account_id:
                    # The original 400 is only the fallback when account
                    # selection cannot produce a replacement. Once this
                    # replacement attempt starts, its own failure is the one
                    # that must reach the client. Keep the separate replay
                    # budget so another account/model rejection cannot trigger
                    # a second transparent replay.
                    account_model_replacement_account_id = account.id
                    last_account_model_rejection = None
                    last_account_model_rejection_account_id = None
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and account.id != preferred_account_id
                ):
                    if verified_fresh_replay_payload is not None:
                        payload = verified_fresh_replay_payload
                        verified_fresh_replay_payload = None
                        excluded_account_ids.add(preferred_account_id)
                        preferred_account_id = None
                        require_preferred_account = False
                        affinity = replace(affinity, reallocate_sticky=True)
                        logger.info(
                            "cross_transport_verified_fresh_replay request_id=%s outcome=alternate_selected",
                            request_id,
                        )
                    else:
                        error_code = "previous_response_owner_unavailable"
                        message = "Previous response owner account is unavailable; retry later."
                        reason = "owner_account_unavailable"
                        upstream_error_code = "upstream_unavailable"
                        if selection.error_code == "continuity_owner_conflict":
                            error_code = "continuity_owner_conflict"
                            message = (
                                selection.error_message
                                or "Account-owned continuity sources conflict; retry the logical turn"
                            )
                            reason = "owner_conflict"
                            upstream_error_code = selection.error_code
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason=reason,
                            previous_response_id=payload.previous_response_id,
                            session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                            upstream_error_code=upstream_error_code,
                        )
                        event = response_failed_event(
                            error_code,
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        await proxy._write_request_log(
                            account_id=preferred_account_id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            status="error",
                            error_code=error_code,
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        return
                try:
                    remaining_budget = _facade()._remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        _facade().logger.warning(
                            "Proxy request budget exhausted before freshness check "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        if preserve_native_failure_lifecycle:
                            return
                        yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                        return
                    try:
                        account = await proxy._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                    except UpstreamProxyRouteError as exc:
                        message = f"Upstream proxy route unavailable: {exc.reason}"
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_proxy_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            upstream_proxy_fail_closed_reason=exc.reason,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        event = response_failed_event(
                            "upstream_proxy_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    except (RefreshError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        selected_account_model_replacement = account.id == account_model_replacement_account_id
                        if isinstance(exc, RefreshError):
                            if exc.is_permanent:
                                await proxy._load_balancer.mark_permanent_failure(account, exc.code)
                                # Keep the warning account routable for later
                                # requests, but do not immediately reselect it in
                                # this request after its refresh material failed.
                                # Release its stream slot before failover too.
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                if not selected_account_model_replacement:
                                    excluded_account_ids.add(account.id)
                                    continue
                            if is_transient_refresh_contention(exc):
                                # Transient CROSS-REPLICA refresh contention: benign
                                # claim contention (the account's refresh claim is
                                # held by another replica) OR a post-exchange
                                # persist/status CAS conflict. This is NOT a genuine
                                # ``transport_error`` OAuth failure — the account's
                                # credentials are healthy — so fail over WITHOUT an
                                # account-health penalty (no ``_handle_stream_error``);
                                # the genuine transport failure handled below keeps
                                # its penalty. A post-exchange persist conflict is
                                # logged distinctly (rarer, more-serious race).
                                if refresh_contention_kind(exc) == "persist_conflict":
                                    logger.warning(
                                        "Stream freshness-check refresh post-exchange persist conflict "
                                        "code=%s account_id=%s",
                                        exc.code,
                                        account.id,
                                    )
                                if (
                                    not selected_account_model_replacement
                                    and not require_preferred_account
                                    and preferred_account_id is None
                                ):
                                    # Movable request: release the stream lease and
                                    # fail over to a different account instead of
                                    # reselecting the same one until attempts are
                                    # exhausted. Record a retryable
                                    # upstream_unavailable so that if EVERY candidate
                                    # hits the held-claim condition, exhaustion
                                    # surfaces upstream_unavailable instead of a
                                    # misleading generic no_accounts response.
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    last_retryable_stream_error = _RetryableStreamError(
                                        "upstream_unavailable",
                                        {
                                            "message": (
                                                exc.message
                                                or "Account refresh is temporarily unavailable; "
                                                "no healthy account could be reached."
                                            )
                                        },
                                        exclude_account=True,
                                    )
                                    continue
                                # PINNED request (``previous_response_id`` /
                                # ``input_file.file_id``): must not cross accounts.
                                # Reselecting the same pinned account until attempts
                                # are exhausted is pointless (it would leak the held
                                # stream lease each iteration and then surface a
                                # misleading ``no_accounts`` result). Stay on the
                                # owner account, release the lease, and surface a
                                # retryable ``upstream_unavailable`` promptly so the
                                # client can retry once the claim clears.
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                message = exc.message or "Account refresh is temporarily unavailable; retry later."
                                last_retryable_stream_error = _RetryableStreamError(
                                    "upstream_unavailable",
                                    {"message": message},
                                )
                                await proxy._write_stream_preflight_error(
                                    account_id=account.id,
                                    api_key=api_key,
                                    request_id=request_id,
                                    model=payload.model,
                                    start=start,
                                    error_code="upstream_unavailable",
                                    error_message=message,
                                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                    service_tier=payload.service_tier,
                                    transport=request_transport,
                                    upstream_transport=upstream_stream_transport,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                    conversation_id=conversation_id,
                                    client_ip=client_ip,
                                )
                                event = response_failed_event(
                                    "upstream_unavailable",
                                    message,
                                    response_id=request_id,
                                )
                                yield format_sse_event(event)
                                return
                            if not exc.transport_error and not selected_account_model_replacement:
                                # Non-transport, non-permanent RefreshError: release
                                # the stream lease and reselect (its prior behavior).
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                continue
                            # A GENUINE OAuth transport failure
                            # (``code == "transport_error"``): the account/route is
                            # at fault, so it falls through to the shared
                            # transport-failure handling below — identical to a raw
                            # aiohttp/connect failure — which records the
                            # account-health penalty (``_handle_stream_error``) so
                            # the broken account backs off instead of being kept
                            # healthy and reselected on the next request.
                        _facade().logger.warning(
                            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                            exc_info=True,
                        )
                        message = getattr(exc, "message", None) or str(exc) or "Request to upstream timed out"
                        if (
                            not selected_account_model_replacement
                            and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                            and attempt + 1 < max_attempts
                            and _move_verified_fresh_replay_from_owner(
                                account_id=account.id,
                                outcome="owner_refresh_connect_failure",
                            )
                        ):
                            await _handle_or_defer_keyed_stream_health(
                                account,
                                {"message": message},
                                "upstream_unavailable",
                            )
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            last_retryable_stream_error = _RetryableStreamError(
                                "upstream_unavailable",
                                {"message": message},
                                exclude_account=True,
                            )
                            continue
                        if (
                            not selected_account_model_replacement
                            and not require_preferred_account
                            and preferred_account_id is None
                            and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                            and attempt + 1 < max_attempts
                        ):
                            await _handle_or_defer_keyed_stream_health(
                                account,
                                {"message": message},
                                "upstream_unavailable",
                            )
                            last_retryable_stream_error = _RetryableStreamError(
                                "upstream_unavailable",
                                {"message": message},
                                exclude_account=True,
                            )
                            # The account keeps its health penalty above and is now
                            # excluded from reselection, but its stream-concurrency
                            # slot is still occupied by the lease appended at
                            # selection. Release it before the failover ``continue``
                            # (matching the claim-contention and permanent branches)
                            # so the dead account's slot is freed immediately instead
                            # of being held for the entire duration of the
                            # replacement stream.
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            continue
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        event = response_failed_event(
                            "upstream_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    any_attempt_logged = True
                    settlement = _StreamSettlement()
                    tool_call_dedupe = _WebSocketUpstreamControl()
                    effective_attempt_timeout = _facade()._remaining_budget_seconds(deadline)
                    if effective_attempt_timeout <= 0:
                        _facade().logger.warning(
                            "Proxy request budget exhausted before stream attempt "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await proxy._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            useragent=useragent,
                            useragent_group=useragent_group,
                            conversation_id=conversation_id,
                            client_ip=client_ip,
                        )
                        if preserve_native_failure_lifecycle:
                            return
                        yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                        return
                    transient_retries = 0
                    allow_retry_flag = attempt < max_attempts - 1
                    while True:
                        stream_timeout_tokens = _facade()._push_stream_attempt_timeout_overrides(
                            _facade()._remaining_budget_seconds(deadline),
                        )
                        try:
                            settlement = _StreamSettlement()
                            register_payload_owner = _authorize_payload_dispatch(account)
                            inner_stream = proxy._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                allow_retry_flag,
                                request_started_at=start,
                                allow_transient_retry=(
                                    transient_retries < _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES - 1
                                    or allow_retry_flag
                                ),
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                                concurrency_caps=concurrency_caps,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                conversation_id=conversation_id,
                                client_ip=client_ip,
                                # Let the retry path observe a pre-visible
                                # account-recovery error only for the one case
                                # where this owner anchor has a locally
                                # verified, unanchored replacement body.  All
                                # other continuations retain the normal
                                # fail-closed owner-error rewrite.
                                preferred_account_id=(
                                    None
                                    if (
                                        require_preferred_account
                                        and preferred_account_id == account.id
                                        and verified_fresh_replay_payload is not None
                                    )
                                    else preferred_account_id
                                ),
                                tool_call_dedupe=tool_call_dedupe,
                                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                            )
                            try:
                                try:
                                    async for line in inner_stream:
                                        if register_payload_owner:
                                            payload_replay_required_account_id = account.id
                                            register_payload_owner = False
                                        yield line
                                    if register_payload_owner:
                                        payload_replay_required_account_id = account.id
                                except BaseException as exc:
                                    if register_payload_owner and not (
                                        isinstance(exc, ProxyResponseError)
                                        and is_confirmed_pre_dispatch_transport_error(exc)
                                    ):
                                        payload_replay_required_account_id = account.id
                                    raise
                            finally:
                                close_task = asyncio.create_task(
                                    inner_stream.aclose(),
                                    name=f"stream-inner-close-{request_id}",
                                )
                                _, close_cancellation = await _await_task_deferring_cancellation(close_task)
                                if close_cancellation is not None:
                                    raise close_cancellation
                        except (asyncio.CancelledError, GeneratorExit):
                            # A terminal frame may already have been yielded when
                            # downstream cancellation is delivered on the next
                            # generator resume. Finalize that terminal usage and
                            # health result before propagating cancellation so the
                            # reservation is not released as abandoned.
                            if settlement.status in {"success", "error"} and not settled:
                                await _finalize_terminal_settlement_after_downstream_close(settlement, account)
                            raise
                        except (_TransientStreamError, ProxyResponseError) as tex:
                            if isinstance(tex, ProxyResponseError) and _must_abort_native_transport_failure(
                                tex,
                                downstream_visible=settlement.downstream_visible,
                            ):
                                raise
                            if account.id == account_model_replacement_account_id:
                                # Account/model routing gets exactly one selected
                                # replacement.  Its own pre-visible 5xx/transport
                                # failure is terminal; allowing the normal
                                # transient path here would silently select a third
                                # account.  Re-raise into the outer terminal
                                # renderer to retain the replacement details.
                                if isinstance(tex, ProxyResponseError):
                                    raise
                                raise ProxyResponseError(
                                    502,
                                    openai_error(
                                        tex.code,
                                        str(tex.error.get("message") or "Upstream error"),
                                        error_type="server_error",
                                    ),
                                ) from tex
                            if settlement.downstream_visible:
                                failed_response_id = settlement.response_id or request_id
                                if isinstance(tex, ProxyResponseError):
                                    error = _parse_openai_error(tex.payload)
                                    error_code = _normalize_error_code(
                                        error.code if error else None,
                                        error.type if error else None,
                                    )
                                    error_message = error.message if error else "Upstream error"
                                    error_type = error.type if error else None
                                    error_param = error.param_state if error else None
                                    event = response_failed_event(
                                        error_code or "upstream_error",
                                        error_message or "Upstream error",
                                        error_type=error_type or "server_error",
                                        response_id=failed_response_id,
                                        error_param=error_param,
                                    )
                                    _apply_error_metadata(event["response"]["error"], error)
                                else:
                                    error_code = tex.code
                                    error_message = str(tex.error.get("message") or "Upstream error")
                                    event = response_failed_event(
                                        error_code or "upstream_error",
                                        error_message,
                                        response_id=failed_response_id,
                                    )
                                if isinstance(tex, ProxyResponseError):
                                    # Downstream visibility forbids replay, but a
                                    # concrete shared HTTP/WebSocket generation
                                    # carrying a process-network failure must still
                                    # be retired before later callers lease it.
                                    await _wait_for_process_network_recovery(
                                        account,
                                        error_code=error_code,
                                        retryable_same_contract=False,
                                        failed_session=tex.failed_session,
                                    )
                                _facade().logger.warning(
                                    "Surfacing mid-stream upstream failure without replay "
                                    "request_id=%s account_id=%s code=%s",
                                    request_id,
                                    account.id,
                                    error_code,
                                )
                                settlement.record_success = False
                                settlement.error_code = error_code
                                settlement.error_message = error_message
                                if isinstance(tex, ProxyResponseError):
                                    settlement.error = _upstream_error_from_openai(error)
                                else:
                                    settlement.error = tex.error
                                settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
                                if not (
                                    preserve_native_failure_lifecycle
                                    and error_code in SYNTHETIC_TRANSPORT_FAILURE_CODES
                                ):
                                    try:
                                        yield format_sse_event(event)
                                    except (asyncio.CancelledError, GeneratorExit):
                                        await _finalize_terminal_settlement_after_downstream_close(settlement, account)
                                        raise
                                settled = await _settle_stream_usage_before_pending_penalty(settlement)
                                if settled and settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                    )
                                return
                            if isinstance(tex, ProxyResponseError) and tex.status_code != 500:
                                error = _parse_openai_error(tex.payload)
                                code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error else None
                                account_model_retry = await _retry_account_model_rejection(
                                    tex,
                                    account,
                                    outcome="previsible",
                                )
                                if account_model_retry is not None:
                                    if not account_model_retry:
                                        raise
                                    # Leaving the same-account loop reaches
                                    # the outer account-selection ``continue``
                                    # below, where the rejected account is
                                    # already excluded by the helper.
                                    break
                                if _facade()._is_security_work_authorization_required_error(code, error_message):
                                    if (
                                        account.security_work_authorized
                                        or account.id == file_preferred_account_id
                                        or require_preferred_account
                                        or attempt >= max_attempts - 1
                                    ):
                                        raise
                                    _facade().logger.info(
                                        "Retrying on security-work-authorized account request_id=%s account_id=%s",
                                        request_id,
                                        account.id,
                                    )
                                    yield format_sse_event(
                                        _facade()._security_work_advisory_event(
                                            code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                            message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                            request_id=request_id,
                                            action="retry_security_work_authorized",
                                            account_id=account.id,
                                        )
                                    )
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    require_security_work_authorized = True
                                    last_security_work_retry_error = _RetryableStreamError(
                                        _facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                        _upstream_error_from_openai(error),
                                    )
                                    last_transient_exc = tex
                                    break
                                if code == "account_response_create_cap":
                                    last_transient_exc = tex
                                    recovery_sleep_seconds = _account_selection_recovery_sleep_seconds(
                                        AccountSelection(
                                            account=None,
                                            error_message=error_message,
                                            error_code=code,
                                        )
                                    )
                                    if recovery_sleep_seconds is not None:
                                        can_try_other_account = (
                                            not require_preferred_account
                                            and account.id != file_preferred_account_id
                                            and attempt < max_attempts - 1
                                        )
                                        if can_try_other_account:
                                            deferred_capacity_account = account
                                            deferred_capacity_lease = current_account_lease
                                            excluded_account_ids.add(account.id)
                                            break
                                        remaining_budget_seconds = _facade()._remaining_budget_seconds(deadline)
                                        if remaining_budget_seconds <= 0:
                                            raise
                                        async for wait_event in _iter_account_capacity_recovery_wait(
                                            request_id=request_id,
                                            model=payload.model,
                                            account_id=account.id,
                                            error_message=error_message,
                                            recovery_sleep_seconds=recovery_sleep_seconds,
                                            deadline=deadline,
                                            emit_keepalives=not propagate_http_errors
                                            or not enforce_openai_sdk_contract,
                                            stage="response_create",
                                        ):
                                            yield wait_event
                                        if _facade()._remaining_budget_seconds(deadline) <= 0:
                                            raise
                                        continue
                                    last_transient_exc = tex
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    break
                                recovery_decision = await _wait_for_process_network_recovery(
                                    account,
                                    error_code=code,
                                    retryable_same_contract=tex.retryable_same_contract,
                                    failed_session=tex.failed_session,
                                )
                                if recovery_decision == "retry":
                                    continue
                                if recovery_decision == "exhausted":
                                    _facade()._raise_proxy_budget_exhausted()
                                if _facade()._is_account_neutral_error_code(code):
                                    raise
                                if is_confirmed_pre_dispatch_transport_error(tex):
                                    # The transport proved the request never
                                    # dispatched: this account's proxy route is
                                    # dead. Release the account's stream lease
                                    # before recording health so its slot never
                                    # outlives the failed route, then jump
                                    # straight to the bounded transient backoff
                                    # floor so independent requests stop
                                    # rediscovering the dead route one generic
                                    # error at a time.
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    await _record_or_defer_confirmed_route_backoff(account)
                                    # A confirmed pre-dispatch connect failure
                                    # guarantees zero upstream bytes, so a
                                    # locally verified full-resend replay is
                                    # safe to move off the dead owner before
                                    # the hard-ownership raise (same file/
                                    # turn-state/single-account guards as the
                                    # post-visible failover path below).
                                    verified_owner_replay_moved = False
                                    if (
                                        attempt < max_attempts - 1
                                        and routing_strategy != "single_account"
                                        and file_preferred_account_id is None
                                        and turn_state_owner_account_id is None
                                    ):
                                        verified_owner_replay_moved = _move_verified_fresh_replay_from_owner(
                                            account_id=account.id,
                                            outcome="owner_pre_dispatch_proxy_connect_failure",
                                        )
                                    can_try_other_account = verified_owner_replay_moved or (
                                        not require_preferred_account
                                        and account.id != file_preferred_account_id
                                        and attempt < max_attempts - 1
                                    )
                                    if not can_try_other_account:
                                        # Hard account ownership or exhausted
                                        # attempts: fail closed on the original
                                        # sanitized failure without crossing
                                        # accounts.
                                        raise
                                    last_transient_exc = tex
                                    last_pre_dispatch_transport_error = tex
                                    transient_failed_account_id = account.id
                                    excluded_account_ids.add(account.id)
                                    if not verified_owner_replay_moved:
                                        affinity = replace(affinity, reallocate_sticky=True)
                                    _facade().logger.info(
                                        "Retrying stream after confirmed pre-dispatch proxy connect failure "
                                        "request_id=%s account_id=%s attempt=%d",
                                        request_id,
                                        account.id,
                                        attempt + 1,
                                    )
                                    break
                                classified = classify_upstream_failure(
                                    error_code=code,
                                    error=_upstream_error_from_openai(error),
                                    http_status=tex.status_code,
                                    phase="first_event",
                                )
                                if getattr(base_settings, "deterministic_failover_enabled", True):
                                    action = failover_decision(
                                        failure_class=classified["failure_class"],
                                        downstream_visible=settlement.downstream_visible,
                                        candidates_remaining=max_attempts - attempt - 1,
                                    )
                                else:
                                    action = "surface"
                                _facade().logger.info(
                                    "Failover decision request_id=%s transport=stream account_id=%s "
                                    "attempt=%d failure_class=%s action=%s",
                                    request_id,
                                    account.id,
                                    attempt + 1,
                                    classified["failure_class"],
                                    action,
                                )
                                if action == "failover_next":
                                    await _handle_or_defer_keyed_stream_health(
                                        account,
                                        _upstream_error_from_openai(error),
                                        code,
                                        http_status=tex.status_code,
                                    )
                                    last_transient_exc = tex
                                    transient_failed_account_id = account.id
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    _move_verified_fresh_replay_from_owner(
                                        account_id=account.id,
                                        outcome="owner_previsible_failure",
                                    )
                                    break
                                await proxy._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    code,
                                    http_status=tex.status_code,
                                )
                                raise
                            error_code = tex.code if isinstance(tex, _TransientStreamError) else "server_error"
                            error_payload: UpstreamError = (
                                tex.error
                                if isinstance(tex, _TransientStreamError)
                                else _upstream_error_from_openai(_parse_openai_error(tex.payload))
                            )
                            error_message = str(error_payload.get("message") or "")
                            recovery_decision = await _wait_for_process_network_recovery(
                                account,
                                error_code=error_code,
                                retryable_same_contract=(
                                    isinstance(tex, ProxyResponseError) and tex.retryable_same_contract
                                ),
                                failed_session=tex.failed_session if isinstance(tex, ProxyResponseError) else None,
                            )
                            if recovery_decision == "retry":
                                continue
                            if recovery_decision == "exhausted":
                                await _settle_process_network_budget_exhaustion(account, settlement)
                                if preserve_native_failure_lifecycle:
                                    return
                                yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                                return
                            transient_retries += 1
                            if (
                                transient_retries < _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                                and _facade()._remaining_budget_seconds(deadline) > 0
                                and not settlement.downstream_visible
                            ):
                                delay = backoff_seconds(transient_retries)
                                _facade().logger.info(
                                    "Transient stream error, retrying same account "
                                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                                    request_id,
                                    account.id,
                                    transient_retries,
                                    _facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                                    delay,
                                    error_code,
                                )
                                await asyncio.sleep(delay)
                                continue  # inner loop: retry same account
                            # Exhausted same-account retries — penalize and failover
                            _facade().logger.warning(
                                "Transient retries exhausted for account "
                                "request_id=%s account_id=%s retries=%s code=%s",
                                request_id,
                                account.id,
                                transient_retries,
                                error_code,
                            )
                            await _handle_or_defer_keyed_stream_health(
                                account,
                                error_payload,
                                error_code,
                                http_status=(tex.status_code if isinstance(tex, ProxyResponseError) else None),
                                transient_retry_count=transient_retries,
                            )
                            # Preserve last ProxyResponseError for propagate_http_errors path.
                            if isinstance(tex, ProxyResponseError):
                                last_transient_exc = tex
                            if isinstance(tex, _TransientStreamError) and (
                                tex.preserve_on_selection_exhausted or error_code == "stream_incomplete"
                            ):
                                last_retryable_stream_error = _RetryableStreamError(
                                    error_code,
                                    error_payload,
                                    exclude_account=True,
                                )
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            break  # outer loop: select different account
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.settlement_order_required:
                            await _finalize_terminal_settlement_after_downstream_close(
                                settlement,
                                account,
                                settlement_order_required=True,
                            )
                        else:
                            settled = await _settle_stream_usage_before_pending_penalty(settlement)
                            if settled and settlement.account_health_error:
                                await proxy._handle_stream_error(
                                    account,
                                    _stream_settlement_error_payload(settlement),
                                    settlement.error_code or "upstream_error",
                                )
                            elif settled and settlement.record_success:
                                await proxy._load_balancer.record_success(account)
                        network_recovery.log_recovered()
                        upstream_transport_metric_status = settlement.status
                        _record_upstream_transport_metric_once(settlement.status)
                        return
                    continue  # outer loop: account failover after transient exhaustion
                except _RetryableStreamError as exc:
                    if _facade()._is_security_work_authorization_required_error(exc.code, exc.error.get("message")):
                        if (
                            account.security_work_authorized
                            or account.id == file_preferred_account_id
                            or require_preferred_account
                            or attempt >= max_attempts - 1
                        ):
                            event = response_failed_event(
                                exc.code,
                                exc.error.get("message") or "Security work authorization is required",
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        _facade().logger.info(
                            "Retrying on security-work-authorized account request_id=%s account_id=%s",
                            request_id,
                            account.id,
                        )
                        yield format_sse_event(
                            _facade()._security_work_advisory_event(
                                code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                request_id=request_id,
                                action="retry_security_work_authorized",
                                account_id=account.id,
                            )
                        )
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                        require_security_work_authorized = True
                        last_security_work_retry_error = exc
                        continue
                    await _handle_or_defer_keyed_stream_health(account, exc.error, exc.code)
                    last_retryable_stream_error = exc
                    if exc.exclude_account:
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                    _move_verified_fresh_replay_from_owner(
                        account_id=account.id,
                        outcome="owner_previsible_retryable_failure",
                    )
                    continue
                except _TerminalStreamError:
                    if settlement.settlement_order_required:
                        await _finalize_terminal_settlement_after_downstream_close(
                            settlement,
                            account,
                            settlement_order_required=True,
                        )
                    else:
                        health_write_allowed = await _settle_stream_usage_before_pending_penalty(settlement)
                        if health_write_allowed and settlement.account_health_error:
                            await proxy._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                    return
                except ProxyResponseError as exc:
                    if _must_abort_native_transport_failure(
                        exc,
                        downstream_visible=settlement.downstream_visible,
                    ):
                        # A native Codex transport failure is ambiguous once the
                        # helper may have dispatched the request. Re-raising the
                        # original exception aborts the downstream stream without
                        # replaying side effects on this or another account.
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        raise
                    if _facade()._is_proxy_budget_exhausted_error(exc):
                        await _settle_process_network_budget_exhaustion(account, settlement)
                        if preserve_native_failure_lifecycle:
                            return
                        yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                        return
                    account_model_retry = await _retry_account_model_rejection(
                        exc,
                        account,
                        outcome="outer_proxy_error",
                    )
                    if account_model_retry:
                        continue
                    if account_model_retry is False:
                        await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        if propagate_http_errors:
                            raise
                        yield await _render_account_model_rejection(exc, account_id=account.id)
                        return
                    if exc.status_code == 401:
                        remaining_budget = _facade()._remaining_budget_seconds(deadline)
                        if remaining_budget <= 0:
                            _facade().logger.warning(
                                "Proxy request budget exhausted before forced refresh retry "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                upstream_transport=upstream_stream_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                conversation_id=conversation_id,
                                client_ip=client_ip,
                            )
                            if preserve_native_failure_lifecycle:
                                return
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        try:
                            account = await proxy._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
                        except (RefreshError, aiohttp.ClientError, asyncio.TimeoutError) as refresh_exc:
                            if isinstance(refresh_exc, RefreshError):
                                if refresh_exc.is_permanent:
                                    await proxy._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                                    # Keep the warning account routable for later
                                    # requests, but exclude it from this request's
                                    # retry pool after upstream rejected its token.
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    continue
                                if is_transient_refresh_contention(refresh_exc):
                                    # Transient CROSS-REPLICA refresh contention on
                                    # the post-401 forced refresh: benign claim
                                    # contention OR a post-exchange persist/status
                                    # CAS conflict. This is NOT a genuine
                                    # ``transport_error`` OAuth failure — the
                                    # account's credentials are healthy — so fail
                                    # over WITHOUT an account-health penalty (no
                                    # ``_handle_stream_error``); the genuine
                                    # transport failure handled below keeps its
                                    # penalty. A post-exchange persist conflict is
                                    # logged distinctly (rarer, more-serious race).
                                    if refresh_contention_kind(refresh_exc) == "persist_conflict":
                                        logger.warning(
                                            "Stream post-401 forced-refresh post-exchange persist conflict "
                                            "code=%s account_id=%s",
                                            refresh_exc.code,
                                            account.id,
                                        )
                                    if not require_preferred_account and preferred_account_id is None:
                                        # Movable request: release the skipped
                                        # account's stream lease and fail over to a
                                        # different account. Record a retryable
                                        # upstream_unavailable so exhaustion surfaces
                                        # upstream_unavailable instead of a
                                        # misleading generic no_accounts response.
                                        await _release_tracked_stream_lease(current_account_lease)
                                        current_account_lease = None
                                        excluded_account_ids.add(account.id)
                                        last_retryable_stream_error = _RetryableStreamError(
                                            "upstream_unavailable",
                                            {
                                                "message": (
                                                    refresh_exc.message
                                                    or "Account refresh is temporarily unavailable; "
                                                    "no healthy account could be reached."
                                                )
                                            },
                                            exclude_account=True,
                                        )
                                        continue
                                    # PINNED request: must not cross accounts. Stay
                                    # on the owner account, release the lease, and
                                    # surface a retryable ``upstream_unavailable``
                                    # promptly so the client can retry once the claim
                                    # clears.
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    message = (
                                        refresh_exc.message
                                        or "Account refresh is temporarily unavailable; retry later."
                                    )
                                    last_retryable_stream_error = _RetryableStreamError(
                                        "upstream_unavailable",
                                        {"message": message},
                                    )
                                    await proxy._write_stream_preflight_error(
                                        account_id=account.id,
                                        api_key=api_key,
                                        request_id=request_id,
                                        model=payload.model,
                                        start=start,
                                        error_code="upstream_unavailable",
                                        error_message=message,
                                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                        service_tier=payload.service_tier,
                                        transport=request_transport,
                                        upstream_transport=upstream_stream_transport,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                        conversation_id=conversation_id,
                                        client_ip=client_ip,
                                    )
                                    event = response_failed_event(
                                        "upstream_unavailable",
                                        message,
                                        response_id=request_id,
                                    )
                                    yield format_sse_event(event)
                                    return
                                if not refresh_exc.transport_error:
                                    # Non-transport, non-permanent RefreshError:
                                    # release the stream lease and reselect.
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    continue
                                # A GENUINE OAuth transport failure
                                # (``code == "transport_error"``): the account/route
                                # is at fault, so it falls through to the shared
                                # transport-failure handling below — identical to a
                                # raw aiohttp/connect failure — which records the
                                # account-health penalty (``_handle_stream_error``)
                                # so the broken account backs off instead of being
                                # kept healthy and reselected on the next request.
                            _facade().logger.warning(
                                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                                exc_info=True,
                            )
                            message = getattr(refresh_exc, "message", None) or str(refresh_exc)
                            message = message or "Request to upstream timed out"
                            if (
                                not require_preferred_account
                                and preferred_account_id is None
                                and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                                and attempt + 1 < max_attempts
                            ):
                                await _handle_or_defer_keyed_stream_health(
                                    account,
                                    {"message": message},
                                    "upstream_unavailable",
                                )
                                last_retryable_stream_error = _RetryableStreamError(
                                    "upstream_unavailable",
                                    {"message": message},
                                    exclude_account=True,
                                )
                                # Release the excluded account's stream lease before
                                # the failover ``continue`` so its stream-concurrency
                                # slot is not held for the duration of the replacement
                                # stream (matching the pre-refresh transport branch and
                                # the claim-contention/permanent branches above).
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_unavailable",
                                error_message=message,
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                upstream_transport=upstream_stream_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                conversation_id=conversation_id,
                                client_ip=client_ip,
                            )
                            event = response_failed_event(
                                "upstream_unavailable",
                                message,
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        settlement = _StreamSettlement()
                        effective_attempt_timeout = _facade()._remaining_budget_seconds(deadline)
                        if effective_attempt_timeout <= 0:
                            _facade().logger.warning(
                                "Proxy request budget exhausted before post-refresh stream attempt "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await proxy._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                                upstream_transport=upstream_stream_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                conversation_id=conversation_id,
                                client_ip=client_ip,
                            )
                            if preserve_native_failure_lifecycle:
                                return
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        try:
                            can_try_other_account = (
                                not require_preferred_account
                                and account.id != file_preferred_account_id
                                and attempt < max_attempts - 1
                            )
                            post_refresh_stream = _stream_post_refresh_with_capacity_recovery(
                                account,
                                settlement=settlement,
                                can_try_other_account=can_try_other_account,
                                tool_call_dedupe=tool_call_dedupe,
                            )
                            try:
                                async for line in post_refresh_stream:
                                    yield line
                            finally:
                                # Closing this generator runs its internal
                                # cancellation-safe close/terminal finalization;
                                # without an owned aclose() the child would stay
                                # suspended after a downstream disconnect.
                                close_task = asyncio.create_task(
                                    post_refresh_stream.aclose(),
                                    name=f"stream-post-refresh-outer-close-{request_id}",
                                )
                                _, close_cancellation = await _await_task_deferring_cancellation(close_task)
                                if close_cancellation is not None:
                                    raise close_cancellation
                        except ProxyResponseError as retry_exc:
                            if _must_abort_native_transport_failure(
                                retry_exc,
                                downstream_visible=settlement.downstream_visible,
                            ):
                                raise
                            if _facade()._is_proxy_budget_exhausted_error(retry_exc):
                                await _settle_process_network_budget_exhaustion(account, settlement)
                                if preserve_native_failure_lifecycle:
                                    return
                                yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                                return
                            if settlement.downstream_visible:
                                failed_response_id = settlement.response_id or request_id
                                error = _parse_openai_error(retry_exc.payload)
                                error_code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error else "Upstream error"
                                event = response_failed_event(
                                    error_code or "upstream_error",
                                    error_message or "Upstream error",
                                    error_type=(error.type if error else None) or "server_error",
                                    response_id=failed_response_id,
                                    error_param=error.param_state if error else None,
                                )
                                _apply_error_metadata(event["response"]["error"], error)
                                _facade().logger.warning(
                                    "Surfacing post-refresh stream failure without replay "
                                    "request_id=%s account_id=%s code=%s",
                                    request_id,
                                    account.id,
                                    error_code,
                                )
                                yield format_sse_event(event)
                                settlement.record_success = False
                                settlement.error_code = error_code
                                settlement.error_message = error_message
                                settlement.error = _upstream_error_from_openai(error)
                                settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
                                settled = await _settle_stream_usage_before_pending_penalty(settlement)
                                if settled and settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                        http_status=retry_exc.status_code,
                                    )
                                return
                            error = _parse_openai_error(retry_exc.payload)
                            error_code = _normalize_error_code(
                                error.code if error else None,
                                error.type if error else None,
                            )
                            if getattr(retry_exc, _POST_REFRESH_TRANSIENT_EXHAUSTED_ATTR, False):
                                transient_error_payload = _stream_settlement_error_payload(settlement)
                                last_transient_exc = retry_exc
                                last_retryable_stream_error = _RetryableStreamError(
                                    settlement.error_code or error_code or "upstream_error",
                                    transient_error_payload,
                                )
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            account_model_retry = await _retry_account_model_rejection(
                                retry_exc,
                                account,
                                outcome="post_refresh",
                            )
                            if account_model_retry:
                                continue
                            if account_model_retry is False:
                                await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                                if propagate_http_errors:
                                    raise
                                yield await _render_account_model_rejection(
                                    retry_exc,
                                    account_id=account.id,
                                )
                                return
                            if error_code == "account_response_create_cap":
                                last_transient_exc = retry_exc
                                if can_try_other_account:
                                    deferred_capacity_account = account
                                    deferred_capacity_lease = current_account_lease
                                    excluded_account_ids.add(account.id)
                                    continue
                                # The same-account helper only re-raises this
                                # neutral cap when recovery cannot continue
                                # within the original budget. Exit the account
                                # loop so the preserved cap is propagated or
                                # rendered below, instead of replacing it with
                                # a next-attempt timeout.
                                await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                                break
                            if _facade()._is_account_neutral_error_code(error_code):
                                await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                                raise
                            if is_confirmed_pre_dispatch_transport_error(retry_exc):
                                # This retry still failed before dispatch. Handle
                                # the proven dead route before the generic
                                # failover policy: that policy does not know
                                # about hard account ownership and may otherwise
                                # cross a previous-response, turn-state, file, or
                                # single-account boundary.
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                await _record_or_defer_confirmed_route_backoff(account)
                                last_transient_exc = retry_exc
                                last_pre_dispatch_transport_error = retry_exc

                                verified_owner_replay_moved = False
                                if (
                                    attempt < max_attempts - 1
                                    and routing_strategy != "single_account"
                                    and file_preferred_account_id is None
                                    and turn_state_owner_account_id is None
                                ):
                                    verified_owner_replay_moved = _move_verified_fresh_replay_from_owner(
                                        account_id=account.id,
                                        outcome="owner_post_refresh_proxy_connect_failure",
                                    )

                                can_try_other_account = bool(
                                    attempt < max_attempts - 1
                                    and routing_strategy != "single_account"
                                    and file_preferred_account_id is None
                                    and turn_state_owner_account_id is None
                                    and not require_preferred_account
                                )
                                if can_try_other_account:
                                    excluded_account_ids.add(account.id)
                                    if not verified_owner_replay_moved:
                                        affinity = replace(affinity, reallocate_sticky=True)
                                    _facade().logger.info(
                                        "Retrying post-refresh stream after confirmed pre-dispatch proxy "
                                        "connect failure request_id=%s account_id=%s attempt=%d",
                                        request_id,
                                        account.id,
                                        attempt + 1,
                                    )
                                    continue

                                # Hard ownership or an exhausted attempt budget:
                                # stop here. The shared terminal path settles the
                                # reservation, drains the deferred backoff floor,
                                # and preserves this sanitized failure.
                                break
                            current_error_payload = _upstream_error_from_openai(error)
                            current_error_code = error_code or "upstream_error"
                            classified = classify_upstream_failure(
                                error_code=current_error_code,
                                error=current_error_payload,
                                http_status=retry_exc.status_code,
                                phase="first_event",
                            )
                            candidates_remaining = max_attempts - attempt - 1
                            if retry_exc.status_code == 401 and candidates_remaining > 0:
                                action = "failover_next"
                            elif getattr(base_settings, "deterministic_failover_enabled", True):
                                action = failover_decision(
                                    failure_class=classified["failure_class"],
                                    downstream_visible=False,
                                    candidates_remaining=candidates_remaining,
                                )
                            else:
                                action = "surface"
                            _facade().logger.info(
                                "Failover decision request_id=%s transport=stream account_id=%s "
                                "attempt=%d phase=post_refresh failure_class=%s action=%s",
                                request_id,
                                account.id,
                                attempt + 1,
                                classified["failure_class"],
                                action,
                            )
                            if action == "failover_next":
                                await _handle_or_defer_keyed_stream_health(
                                    account,
                                    current_error_payload,
                                    current_error_code,
                                    http_status=retry_exc.status_code,
                                )
                                last_transient_exc = retry_exc
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                _move_verified_fresh_replay_from_owner(
                                    account_id=account.id,
                                    outcome="owner_post_refresh_failure",
                                )
                                excluded_account_ids.add(account.id)
                                continue
                            health_write_allowed = await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                            if health_write_allowed:
                                await proxy._handle_stream_error(
                                    account,
                                    current_error_payload,
                                    current_error_code,
                                    http_status=retry_exc.status_code,
                                )
                            if propagate_http_errors:
                                raise
                            error_message = error.message if error else None
                            event = response_failed_event(
                                error_code or "upstream_error",
                                error_message or "Upstream error",
                                error_type=(error.type if error else None) or "server_error",
                                response_id=request_id,
                                error_param=error.param_state if error else None,
                            )
                            _apply_error_metadata(event["response"]["error"], error)
                            yield format_sse_event(event)
                            return
                        current_account_penalty_queued = any(
                            failed_account is account
                            for failed_account, *_rest in pending_post_refresh_transient_penalties
                        )
                        ordered_settlement_required = bool(
                            pending_post_refresh_transient_penalties or deferred_account_error_backoffs
                        )
                        if ordered_settlement_required:
                            health_write_allowed = await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                            if (
                                health_write_allowed
                                and settlement.account_health_error
                                and not current_account_penalty_queued
                            ):
                                await proxy._handle_stream_error(
                                    account,
                                    _stream_settlement_error_payload(settlement),
                                    settlement.error_code or "upstream_error",
                                )
                            elif health_write_allowed and settlement.record_success:
                                await proxy._load_balancer.record_success(account)
                        else:
                            await _finalize_terminal_settlement_after_downstream_close(
                                settlement,
                                account,
                                settlement_order_required=True,
                            )
                        upstream_transport_metric_status = settlement.status
                        _record_upstream_transport_metric_once(settlement.status)
                        return
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    error_type = error.type if error else None
                    error_param = error.param_state if error else None
                    if _facade()._is_security_work_authorization_required_error(error_code, error_message):
                        if (
                            not account.security_work_authorized
                            and account.id != file_preferred_account_id
                            and not require_preferred_account
                            and attempt < max_attempts - 1
                        ):
                            _facade().logger.info(
                                "Retrying on security-work-authorized account request_id=%s account_id=%s",
                                request_id,
                                account.id,
                            )
                            yield format_sse_event(
                                _facade()._security_work_advisory_event(
                                    code=_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                    message=_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                    request_id=request_id,
                                    action="retry_security_work_authorized",
                                    account_id=account.id,
                                )
                            )
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            require_security_work_authorized = True
                            continue
                    health_write_allowed = await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                    if health_write_allowed and _facade()._should_penalize_stream_error(error_code):
                        await proxy._handle_stream_error(
                            account,
                            _upstream_error_from_openai(error),
                            error_code,
                        )
                    if propagate_http_errors:
                        raise
                    event = response_failed_event(
                        error_code,
                        error_message or "Upstream error",
                        error_type=error_type or "server_error",
                        response_id=request_id,
                        error_param=error_param,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return
                except RefreshError as exc:
                    if exc.is_permanent:
                        await proxy._load_balancer.mark_permanent_failure(account, exc.code)
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                    continue
                except Exception:
                    await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                    _facade().logger.warning(
                        "Proxy streaming failed without retry account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
            await _drain_pending_post_refresh_penalty_on_terminal(settlement)
            # When HTTP error propagation is enabled and the last failure was
            # a transient 500, re-raise to preserve the upstream status/payload.
            if last_account_model_rejection is not None:
                if propagate_http_errors:
                    raise last_account_model_rejection
                yield await _render_account_model_rejection(
                    last_account_model_rejection,
                    account_id=last_account_model_rejection_account_id,
                )
                return
            if propagate_http_errors and last_transient_exc is not None:
                raise last_transient_exc
            if last_pre_dispatch_transport_error is not None:
                # Attempt budget exhausted after confirmed pre-dispatch route
                # failures: surface the original sanitized failure rather than
                # a generated ``no_accounts`` response.
                yield _render_dispatch_transport_error(last_pre_dispatch_transport_error)
                return
            if last_retryable_stream_error is not None:
                retries_exhausted_msg = str(last_retryable_stream_error.error.get("message") or "Upstream error")
                event = response_failed_event(
                    last_retryable_stream_error.code,
                    retries_exhausted_msg,
                    response_id=request_id,
                )
                if last_retryable_stream_error.code in SYNTHETIC_TRANSPORT_FAILURE_CODES:
                    event = synthetic_transport_failure_event(event)
                if not (
                    preserve_native_failure_lifecycle
                    and last_retryable_stream_error.code in SYNTHETIC_TRANSPORT_FAILURE_CODES
                ):
                    yield format_sse_event(event)
                if not any_attempt_logged:
                    await proxy._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code=last_retryable_stream_error.code,
                        error_message=retries_exhausted_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        upstream_transport=upstream_stream_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                        conversation_id=conversation_id,
                        client_ip=client_ip,
                    )
                return
            if last_transient_exc is not None:
                error = _parse_openai_error(last_transient_exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                if error_code == "account_response_create_cap":
                    error_message = error.message if error else None
                    event = response_failed_event(
                        error_code,
                        error_message or "Account response-create concurrency limit reached",
                        error_type=(error.type if error else None) or "server_error",
                        response_id=request_id,
                        error_param=error.param_state if error else None,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return

            retries_exhausted_msg = "No available accounts after retries"
            _facade().logger.warning(
                "Proxy streaming exhausted accounts request_id=%s model=%s transport=%s attempts=%s "
                "excluded_count=%s preferred_account_id=%s api_key_present=%s",
                request_id,
                payload.model,
                request_transport,
                attempt,
                len(excluded_account_ids),
                preferred_account_id,
                api_key is not None,
            )
            event = response_failed_event(
                "no_accounts",
                retries_exhausted_msg,
                response_id=request_id,
            )
            yield format_sse_event(event)
            if not any_attempt_logged:
                await proxy._write_request_log(
                    account_id=None,
                    api_key=api_key,
                    request_id=request_id,
                    model=payload.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code="no_accounts",
                    error_message=retries_exhausted_msg,
                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                    transport=request_transport,
                    upstream_transport=upstream_stream_transport,
                    service_tier=payload.service_tier,
                    requested_service_tier=payload.service_tier,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    conversation_id=conversation_id,
                    client_ip=client_ip,
                )
        finally:
            if not upstream_transport_metric_recorded:
                _record_upstream_transport_metric_once(upstream_transport_metric_status or "error")
            for account_lease in account_leases:
                await proxy._load_balancer.release_account_lease(account_lease)
            if (
                not settled
                and not settlement.usage_settlement_transferred
                and api_key is not None
                and api_key_reservation is not None
            ):

                async def _release_reservation_then_drain_backoffs() -> None:
                    nonlocal settled
                    if pending_post_refresh_transient_penalties:
                        # Mid-loop keyed health may already be queued. Prefer
                        # settle-then-flush so cancel cleanup still applies the
                        # deferred account penalty after the reservation closes.
                        settled = await _drain_pending_post_refresh_penalty_on_terminal(settlement)
                        if settled:
                            return
                    released = await proxy._release_unsettled_stream_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        request_id=request_id,
                    )
                    if released:
                        try:
                            await proxy._drain_deferred_account_error_backoffs(deferred_account_error_backoffs)
                        finally:
                            # Confirmed release owns the same independent
                            # post-settlement health lanes as confirmed settle.
                            await _flush_or_schedule_pending_post_refresh_penalties()

                release_coro = _release_reservation_then_drain_backoffs()
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    proxy._schedule_cancel_safe_cleanup(
                        release_coro,
                        action="release_stream_api_key_reservation",
                        request_id=request_id,
                    )
                else:
                    await release_coro
            elif settled and deferred_account_error_backoffs:
                try:
                    await proxy._drain_deferred_account_error_backoffs(deferred_account_error_backoffs)
                finally:
                    # A retained backoff failure must not block ownership of
                    # later penalties left by a cancelled flush.
                    await _flush_or_schedule_pending_post_refresh_penalties()
            elif settled:
                # Finish penalties left behind if a prior flush was cancelled
                # mid-loop after settlement already closed the reservation.
                await _flush_or_schedule_pending_post_refresh_penalties()
