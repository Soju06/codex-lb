from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from typing import Any, Literal, TypeVar, overload
from uuid import uuid4

import aiohttp
import anyio

from app.core import shutdown as shutdown_state
from app.core.auth.refresh import RefreshError
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
from app.core.config.settings import Settings
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_instance_mismatch_total,
    bridge_local_rebind_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.core.resilience.overload import local_overload_error
from app.db.models import (
    AccountStatus,
    StickySessionKind,
)
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyRequestUsageBudget,
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
from app.modules.proxy._service.http_bridge.account_sessions import _HTTPBridgeAccountSessionsMixin
from app.modules.proxy._service.http_bridge.helpers import (
    _active_http_bridge_instance_ring,
    _durable_bridge_lookup_active_owner,
    _durable_bridge_lookup_allows_local_reuse,
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
    _http_bridge_key_strength,
    _http_bridge_owner_check_required,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_previous_response_alias_key,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_counts_against_queue,
    _http_bridge_session_account_active,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_should_wait_for_registration,
    _http_bridge_startup_wait_timeout_error,
    _http_bridge_turn_state_alias_key,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _log_http_bridge_startup_wait_timeout,
    _preferred_http_bridge_reconnect_turn_state,
    _record_bridge_drain_recovery_allowed,
    _record_bridge_first_turn_timeout,
    _record_bridge_reattach,
)
from app.modules.proxy._service.http_bridge.owner_forwarding import _HTTPBridgeOwnerForwardingMixin
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeRequestSubmitMixin
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _call_with_supported_optional_kwargs,
    _estimated_lease_tokens_from_request_usage_budget,
    _headers_with_turn_state,
    _is_local_account_cap_code,
    _prefer_earlier_reset_window,
    _proxy_admission_wait_timeout_seconds,
    _raise_proxy_unavailable,
    _record_continuity_fail_closed,
    _record_same_account_takeover,
    _remaining_budget_seconds,
    _routing_strategy,
    _service_get_settings,
    _service_get_settings_cache,
    _service_time,
    _upstream_turn_state_from_socket,
    _websocket_connect_deadline,
    _websocket_safe_headers_with_turn_state,
)
from app.modules.proxy._service.http_bridge.streaming import _HTTPBridgeStreamingMixin
from app.modules.proxy._service.http_bridge.upstream_events import _HTTPBridgeUpstreamEventsMixin
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
    _copy_websocket_route_metadata_to_session,
    _http_bridge_session_supports_service_tier,
    _HTTPBridgeOwnerForward,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _sleep_for_account_selection_recovery,
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
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.load_balancer import AccountLease

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


class _HTTPBridgeMixin(
    _HTTPBridgeStreamingMixin,
    _HTTPBridgeAccountSessionsMixin,
    _HTTPBridgeOwnerForwardingMixin,
    _HTTPBridgeRequestSubmitMixin,
    _HTTPBridgeUpstreamEventsMixin,
    _HTTPBridgeServiceProtocol,
):
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
        request_service_tier: str | None = None,
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
        request_deadline: float | None = None,
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
        request_service_tier: str | None = None,
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
        request_deadline: float | None = None,
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
        request_service_tier: str | None = None,
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
        request_deadline: float | None = None,
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
            require_preferred_account = (previous_response_id is not None and preferred_account_id is not None) or (
                preferred_account_id is not None
                and (key.strength == "hard" or not fallback_on_preferred_account_unavailable)
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
                            or not _http_bridge_session_account_active(alias_session)
                            or not _http_bridge_session_matches_preferred_account(
                                session=alias_session,
                                previous_response_id=previous_response_id,
                                preferred_account_id=preferred_account_id,
                                require_preferred_account=require_preferred_account,
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
                                and _http_bridge_session_account_active(previous_session)
                                and _http_bridge_session_matches_preferred_account(
                                    session=previous_session,
                                    previous_response_id=previous_response_id,
                                    preferred_account_id=preferred_account_id,
                                    require_preferred_account=require_preferred_account,
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
                    and _http_bridge_session_account_active(existing)
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
                        require_preferred_account=require_preferred_account,
                    )
                    and _http_bridge_session_supports_service_tier(
                        existing,
                        request_model=request_model,
                        request_service_tier=request_service_tier,
                    )
                ):
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        existing.api_key = api_key
                        existing.request_model = request_model
                        existing.request_service_tier = request_service_tier
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
                        and (existing is None or existing.closed or not _http_bridge_session_account_active(existing))
                    ):
                        previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
                        previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
                        if previous_key is not None:
                            previous_session = self._http_bridge_sessions.get(previous_key)
                            if (
                                previous_session is not None
                                and not previous_session.closed
                                and _http_bridge_session_account_active(previous_session)
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
                    and _http_bridge_session_account_active(session)
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
                        require_preferred_account=require_preferred_account,
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
            try:
                create_session = self._create_http_bridge_session
                create_kwargs: dict[str, Any] = {
                    "headers": headers,
                    "affinity": affinity,
                    "api_key": api_key,
                    "request_model": request_model,
                    "request_service_tier": request_service_tier,
                    "idle_ttl_seconds": effective_idle_ttl_seconds,
                    "request_stage": request_stage,
                    "preferred_account_id": preferred_account_id,
                    "require_preferred_account": require_preferred_account,
                    "fallback_on_preferred_account_unavailable": (
                        fallback_on_preferred_account_unavailable and not require_preferred_account
                    ),
                    "request_usage_budget": request_usage_budget,
                    "request_deadline": request_deadline,
                }
                try:
                    create_signature = inspect.signature(create_session)
                except (TypeError, ValueError):
                    create_signature = None
                create_accepts_var_keyword = create_signature is not None and any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in create_signature.parameters.values()
                )
                if create_signature is not None and not create_accepts_var_keyword:
                    for optional_kwarg in ("request_service_tier", "request_usage_budget", "request_deadline"):
                        if optional_kwarg not in create_signature.parameters:
                            create_kwargs.pop(optional_kwarg, None)
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
                    service_tier=getattr(session, "request_service_tier", None),
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
        request_service_tier: str | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_deadline: float | None = None,
    ) -> "_HTTPBridgeSession":
        request_state = _WebSocketRequestState(
            request_id=f"http_bridge_connect_{uuid4().hex}",
            model=request_model,
            service_tier=request_service_tier,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=_service_time().monotonic(),
            transport=_REQUEST_TRANSPORT_HTTP,
        )
        deadline = (
            request_deadline
            if request_deadline is not None
            else _websocket_connect_deadline(
                request_state,
                _http_bridge_request_budget_seconds(_service_get_settings()),
            )
        )
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
                "service_tier": request_service_tier,
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
                connect_headers = _websocket_safe_headers_with_turn_state(
                    headers, _sticky_key_from_turn_state_header(headers)
                )
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
                    connect_headers = _websocket_safe_headers_with_turn_state(
                        headers, _sticky_key_from_turn_state_header(headers)
                    )
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
            request_service_tier=request_service_tier,
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
        deadline = _websocket_connect_deadline(
            request_state,
            _http_bridge_request_budget_seconds(_service_get_settings()),
        )
        settings = await _service_get_settings_cache().get()
        session.api_key = request_state.api_key
        close_skips_account = session.last_upstream_close_code in _UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY
        hard_close_account_bound = session.key.strength == "hard" and close_skips_account
        skip_same_account = session.key.strength != "hard" and close_skips_account
        forced_refresh_account_id = request_state.force_refresh_account_id
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        if skip_same_account:
            excluded_account_ids.add(session.account.id)
        retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
        if skip_same_account:
            preferred_candidate_id: str | None = None
        elif hard_close_account_bound and session.account.id not in excluded_account_ids:
            preferred_candidate_id = session.account.id
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

        async def release_selected_account_lease_and_reraise_if_hard_close_bound() -> None:
            if hard_close_account_bound:
                await release_selected_account_lease()
                raise

        async def abandon_selected_account_retry(selected_account: Any) -> None:
            nonlocal preferred_candidate_id
            await release_selected_account_lease_and_reraise_if_hard_close_bound()
            excluded_account_ids.add(selected_account.id)
            preferred_candidate_id = None
            await release_selected_account_lease()

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
                service_tier=session.request_service_tier,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
                require_security_work_authorized=require_security_work_authorized,
                lease_kind=None if reuse_current_account_lease else "stream",
                estimated_lease_tokens=_estimated_lease_tokens_from_request_usage_budget(
                    request_state.request_usage_budget
                ),
                fallback_on_preferred_account_unavailable=(
                    not reuse_current_account_lease and not hard_close_account_bound
                ),
            )
            account = selection.account
            if account is None:
                await release_selected_account_lease()
                if (
                    reuse_current_account_lease
                    and not hard_close_account_bound
                    and _remaining_budget_seconds(deadline) > 0
                ):
                    preferred_candidate_id = None
                    continue
                if await _sleep_for_account_selection_recovery(
                    selection,
                    request_id=request_state.request_log_id or request_state.request_id,
                    kind="http_bridge",
                    request_stage="reattach",
                    model=session.request_model,
                    max_sleep_seconds=_remaining_budget_seconds(deadline),
                    request_state=request_state,
                ):
                    excluded_account_ids.update(request_state.excluded_account_ids)
                    if skip_same_account:
                        excluded_account_ids.add(session.account.id)
                    retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
                    if skip_same_account:
                        preferred_candidate_id = None
                    elif hard_close_account_bound and session.account.id not in excluded_account_ids:
                        preferred_candidate_id = session.account.id
                    elif forced_refresh_account_id is not None:
                        preferred_candidate_id = forced_refresh_account_id
                    elif request_state.preferred_account_id is not None:
                        preferred_candidate_id = request_state.preferred_account_id
                    elif session.account.id not in excluded_account_ids:
                        preferred_candidate_id = session.account.id
                    else:
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
                connect_headers = _websocket_safe_headers_with_turn_state(
                    session.headers, _preferred_http_bridge_reconnect_turn_state(session)
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
                    connect_headers = _websocket_safe_headers_with_turn_state(
                        session.headers, _preferred_http_bridge_reconnect_turn_state(session)
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
                    await abandon_selected_account_retry(account)
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    await abandon_selected_account_retry(account)
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    await abandon_selected_account_retry(account)
                    continue
                await release_selected_account_lease()
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    await abandon_selected_account_retry(account)
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
