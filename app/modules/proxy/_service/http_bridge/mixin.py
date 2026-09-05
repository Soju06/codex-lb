from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from collections.abc import Collection
from dataclasses import replace
from typing import Any, Literal, TypeVar, overload
from uuid import uuid4

import aiohttp

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
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_local_rebind_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.core.utils.locks import fast_lock
from app.core.utils.request_id import ensure_request_scope_id
from app.core.utils.shared_future import wait_on_shared_future
from app.db.models import (
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
from app.modules.proxy._service.http_bridge.activity import _HTTPBridgeActivityMixin
from app.modules.proxy._service.http_bridge.helpers import (
    _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
    _HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR,
    _active_http_bridge_instance_ring,
    _alias_fallback_key,
    _durable_bridge_lookup_active_owner,
    _durable_bridge_lookup_allows_local_reuse,
    _forwarded_http_bridge_session_key,
    _http_bridge_alias_target_is_stale,
    _http_bridge_allow_durable_takeover,
    _http_bridge_can_local_recover_without_ring,
    _http_bridge_can_recover_during_drain,
    _http_bridge_can_single_instance_owner_takeover_without_anchor,
    _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor,
    _http_bridge_capacity_after_planned_closes,
    _http_bridge_claim_allows_takeover,
    _http_bridge_compatible,
    _http_bridge_continuity_lost_error_envelope,
    _http_bridge_endpoint_matches_current_instance,
    _http_bridge_has_durable_recovery_anchor,
    _http_bridge_incompatible_model_fork_key,
    _http_bridge_inflight_creation_count,
    _http_bridge_key_strength,
    _http_bridge_locally_owned_fork_key,
    _http_bridge_models_compatible,
    _http_bridge_owner_check_required,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_parallel_fork_key,
    _http_bridge_previous_response_alias_key,
    _http_bridge_previous_response_owner_unavailable_error,
    _http_bridge_reconnect_connect_failure,
    _http_bridge_reconnect_selection_failure,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_needs_unanchored_handoff,
    _http_bridge_session_account_active,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_generation_count,
    _http_bridge_session_has_admission_waiter,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_lookup,
    _http_bridge_session_reusable_for_request,
    _http_bridge_should_wait_for_registration,
    _http_bridge_startup_wait_timeout_error,
    _http_bridge_turn_state_alias_key,
    _log_http_bridge_event,
    _log_http_bridge_startup_wait_timeout,
    _mark_http_bridge_reader_handoff_reconnect_failed,
    _persist_http_bridge_replacement_account,
    _persistent_http_bridge_affinity,
    _plan_http_bridge_lru_capacity_closes,
    _preferred_http_bridge_reconnect_turn_state,
    _raise_if_http_bridge_creation_superseded,
    _record_bridge_drain_recovery_allowed,
    _record_bridge_first_turn_timeout,
    _refresh_reused_http_bridge_session_with_handoff,
    _register_http_bridge_turn_state_aliases_locked,
    _require_http_bridge_bound_account_not_excluded,
    _reserve_http_bridge_unanchored_handoff,
    _settle_failed_http_bridge_creation,
    _turn_keys,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _close_http_bridge_session as _helpers_close_http_bridge_session,
)
from app.modules.proxy._service.http_bridge.owner_forwarding import _HTTPBridgeOwnerForwardingMixin
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.proxy_failover import _HTTPBridgePreDispatchFailover
from app.modules.proxy._service.http_bridge.quarantine import (
    _http_bridge_session_key_quarantined,
)
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeRequestSubmitMixin
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _call_with_supported_optional_kwargs,
    _estimated_lease_tokens_from_request_usage_budget,
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
from app.modules.proxy._service.http_bridge.session_registry import _HTTPBridgeSessionRegistryMixin
from app.modules.proxy._service.http_bridge.streaming import _HTTPBridgeStreamingMixin
from app.modules.proxy._service.http_bridge.upstream_events import _HTTPBridgeUpstreamEventsMixin
from app.modules.proxy._service.observability import _hash_identifier
from app.modules.proxy._service.support import (
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_precreated_replay_fallback,
    _complete_http_bridge_handoff,
    _copy_websocket_route_metadata_to_session,
    _DeferredAccountBackoffLifecycle,
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
from app.modules.proxy.account_eligibility import ROUTABLE_STATUSES
from app.modules.proxy.account_eligibility import account_access_token_expires_at as _token_expiry
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _extract_model_class,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    resolve_reconnect_preferred_account_id,
    resolve_required_account_id,
    without_http_bridge_session_affinity_headers,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup
from app.modules.proxy.load_balancer import CONTINUITY_OWNER_UNAVAILABLE, AccountLease
from app.modules.proxy.selection_errors import USAGE_LIMIT_REACHED, selection_failure_response

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_REQUEST_TRANSPORT_HTTP = "http"
_UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY = frozenset({1011})
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100


class _HTTPBridgeMixin(
    _HTTPBridgeStreamingMixin,
    _HTTPBridgeSessionRegistryMixin,
    _HTTPBridgeAccountSessionsMixin,
    _HTTPBridgeActivityMixin,
    _HTTPBridgeOwnerForwardingMixin,
    _HTTPBridgeRequestSubmitMixin,
    _HTTPBridgeUpstreamEventsMixin,
    _HTTPBridgeServiceProtocol,
):
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

    async def _drain_http_bridge_background_cleanup_tasks(self, *, reason: str) -> bool:
        tasks = [
            task
            for task in self._background_cleanup_tasks
            if (
                task.get_name().startswith("proxy-http_bridge_session_close-")
                or task.get_name().startswith("http-bridge-close-")
                or task.get_name().startswith("cancelled-task-cleanup-")
            )
        ]
        if not tasks:
            return not self._http_bridge_background_cleanup_failed
        try:
            results = await asyncio.wait_for(
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
            return False
        self._http_bridge_background_cleanup_failed |= any(isinstance(result, BaseException) for result in results)
        return not self._http_bridge_background_cleanup_failed

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
            if getattr(inflight_future, "_http_bridge_handoff", False):
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
            if getattr(inflight_future, "_http_bridge_handoff", False):
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        preferred_account_has_continuity_provenance: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_deadline: float | None = None,
        session_header_fallback_key: "_HTTPBridgeSessionKey | None" = None,
        exclude_account_ids: Collection[str] | None = None,
        deferred_account_backoff_lifecycle: _DeferredAccountBackoffLifecycle | None = None,
        defer_account_health_writes: bool = False,
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        preferred_account_has_continuity_provenance: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_deadline: float | None = None,
        session_header_fallback_key: "_HTTPBridgeSessionKey | None" = None,
        exclude_account_ids: Collection[str] | None = None,
        deferred_account_backoff_lifecycle: _DeferredAccountBackoffLifecycle | None = None,
        defer_account_health_writes: bool = False,
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
        forwarded_original_request_unanchored: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        preferred_account_has_continuity_provenance: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_deadline: float | None = None,
        session_header_fallback_key: "_HTTPBridgeSessionKey | None" = None,
        exclude_account_ids: Collection[str] | None = None,
        deferred_account_backoff_lifecycle: _DeferredAccountBackoffLifecycle | None = None,
        defer_account_health_writes: bool = False,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        settings = _service_get_settings()
        request_scope_id = ensure_request_scope_id()
        api_key_id = api_key.id if api_key is not None else None
        incoming_turn_state = _sticky_key_from_turn_state_header(headers)
        incoming_session_key, initial_session_key = _turn_keys(headers, api_key, key, session_header_fallback_key)
        original_request_unanchored = _http_bridge_request_needs_unanchored_handoff(
            key, incoming_turn_state, previous_response_id, forwarded_request, forwarded_original_request_unanchored
        )
        # Model-transition isolation intentionally drops the durable lookup as a
        # routing input below. Preserve generation provenance first: the same
        # replica id can still name an older socket/process whose late release
        # must be fenced by a newly advanced owner epoch.
        same_replica_durable_predecessor = bool(
            durable_lookup and durable_lookup.owner_instance_id == settings.http_responses_session_bridge_instance_id
        )
        model_transition_rebind = bool(
            durable_lookup is not None and not _http_bridge_models_compatible(durable_lookup.model, request_model)
        )
        if model_transition_rebind:
            durable_lookup = None
        # Account selection consumes this one-shot capability; canonical creation
        # also forces takeover so a prior-ring durable owner cannot reject it.
        force_goal_restart_account_reselection = affinity.abandon_unavailable_legacy_owner
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
        force_durable_takeover_after_detach = used_session_header_fallback = False
        locally_owned_fork_key: _HTTPBridgeSessionKey | None = None
        model_transition_parent_key: _HTTPBridgeSessionKey | None = None

        def bind_account_neutral_recovery_owner(session: _HTTPBridgeSession) -> None:
            nonlocal preferred_account_id
            if not is_http_bridge_account_neutral_replay(
                kind=session.key.affinity_kind,
                key=session.key.affinity_key,
            ):
                return
            preferred_account_id = resolve_required_account_id(
                ("requested continuity owner", preferred_account_id),
                ("local account-neutral recovery", session.account.id),
            )

        while True:
            account_neutral_recovery = is_http_bridge_account_neutral_replay(
                kind=key.affinity_kind,
                key=key.affinity_key,
            )
            if account_neutral_recovery:
                headers = without_http_bridge_session_affinity_headers(headers)
                affinity = _AffinityPolicy()
                incoming_turn_state = None
                incoming_session_key = None
                session_header_fallback_key = None
                initial_session_key = None
                if durable_lookup is None:
                    allow_forward_to_owner = False
            inflight_future: asyncio.Future[_HTTPBridgeSession] | None = None
            capacity_wait_future: asyncio.Future[_HTTPBridgeSession] | None = None
            capacity_error_after_planned_closes: ProxyResponseError | None = None
            owns_creation = False
            continuity_error: ProxyResponseError | None = None
            owner_mismatch_error: ProxyResponseError | None = None
            owner_forward: _HTTPBridgeOwnerForward | None = None
            force_durable_takeover = force_durable_takeover_after_detach or force_goal_restart_account_reselection
            missing_turn_state_alias = False
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
            preserve_internal_fork_key = key.affinity_kind in {
                "internal_unanchored_parallel",
                "internal_model_parallel",
                "internal_request_parallel",
            }
            require_preferred_account = preferred_account_id is not None and (
                previous_response_id is not None
                or preferred_account_has_continuity_provenance
                or key.strength == "hard"
                or not fallback_on_preferred_account_unavailable
            )
            async with self._http_bridge_lock:
                if (
                    incoming_turn_state is not None
                    and forwarded_affinity is None
                    and not preserve_durable_canonical_key
                    and not preserve_internal_fork_key
                ):
                    alias_index_key = _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                    alias_key = self._http_bridge_turn_state_index.get(alias_index_key)
                    if alias_key is not None:
                        key = alias_key
                        alias_session = self._http_bridge_sessions.get(alias_key)
                        if _http_bridge_alias_target_is_stale(alias_session):
                            if is_http_bridge_account_neutral_replay(
                                kind=alias_key.affinity_kind,
                                key=alias_key.affinity_key,
                            ):
                                if alias_session is None:
                                    raise ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                                bind_account_neutral_recovery_owner(alias_session)
                                continue
                            self._http_bridge_turn_state_index.pop(alias_index_key, None)
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        elif not _http_bridge_models_compatible(alias_session.request_model, request_model):
                            model_transition_rebind, model_transition_parent_key = True, alias_key
                            if is_http_bridge_account_neutral_replay(
                                kind=alias_key.affinity_kind,
                                key=alias_key.affinity_key,
                            ):
                                recovery_fork_key = _http_bridge_incompatible_model_fork_key(
                                    key=alias_key,
                                    existing_model=alias_session.request_model,
                                    request_model=request_model,
                                    request_scope_id=request_scope_id,
                                )
                                assert recovery_fork_key is not None
                                bind_account_neutral_recovery_owner(alias_session)
                                key = recovery_fork_key
                                continue
                            else:
                                key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        elif not _http_bridge_compatible(
                            alias_session, request_model, request_service_tier, True
                        ) or not _http_bridge_session_matches_preferred_account(
                            session=alias_session,
                            previous_response_id=previous_response_id,
                            preferred_account_id=preferred_account_id,
                            require_preferred_account=require_preferred_account,
                        ):
                            raise ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                        else:
                            self._promote_http_bridge_session_to_codex_affinity(
                                alias_session,
                                turn_state=incoming_turn_state,
                                settings=settings,
                            )
                            _register_http_bridge_turn_state_aliases_locked(self, alias_session)
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
                                and (not previous_session.closed or previous_session.handoff_in_progress)
                                and _http_bridge_session_account_active(previous_session)
                                and _http_bridge_compatible(previous_session, request_model, request_service_tier, True)
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
                            if previous_session is not None and not _http_bridge_models_compatible(
                                previous_session.request_model, request_model
                            ):
                                model_transition_rebind = True
                                model_transition_parent_key = previous_key
                                if previous_key is not None and is_http_bridge_account_neutral_replay(
                                    kind=previous_key.affinity_kind,
                                    key=previous_key.affinity_key,
                                ):
                                    recovery_fork_key = _http_bridge_incompatible_model_fork_key(
                                        key=previous_key,
                                        existing_model=previous_session.request_model,
                                        request_model=request_model,
                                        request_scope_id=request_scope_id,
                                    )
                                    assert recovery_fork_key is not None
                                    bind_account_neutral_recovery_owner(previous_session)
                                    key = recovery_fork_key
                                    continue
                            elif previous_session is not None and (
                                not _http_bridge_alias_target_is_stale(previous_session)
                                and not previous_session.handoff_in_progress
                            ):
                                raise ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                            elif previous_key is not None:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                        if model_transition_rebind:
                            if not is_http_bridge_account_neutral_replay(
                                kind=key.affinity_kind,
                                key=key.affinity_key,
                            ):
                                key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        elif (
                            fallback_key := _alias_fallback_key(incoming_session_key, initial_session_key, api_key_id)
                        ) is not None:
                            key = fallback_key
                            used_session_header_fallback = True
                        else:
                            key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                            missing_turn_state_alias = True
                pruned_sessions = self._prune_http_bridge_sessions_locked()
                if pruned_sessions:
                    if any(session.key == key for session in pruned_sessions):
                        force_durable_takeover = True
                    self._schedule_http_bridge_session_closes(pruned_sessions, reason="registry_detach")
                existing = self._http_bridge_sessions.get(key)
                retained_handoff = bool(
                    existing and existing.closed and _http_bridge_session_has_admission_waiter(existing)
                )
                reusable = (
                    not force_goal_restart_account_reselection
                    and existing is not None
                    and _http_bridge_session_reusable_for_lookup(
                        session=existing,
                        key=key,
                        api_key=api_key,
                        incoming_turn_state=incoming_turn_state,
                        previous_response_id=previous_response_id,
                        preferred_account_id=preferred_account_id,
                        require_preferred_account=require_preferred_account,
                        service_tier_supported=_http_bridge_compatible(existing, request_model, request_service_tier),
                        allow_closed_admission_handoff=retained_handoff,
                        session_key_quarantined=_http_bridge_session_key_quarantined(self, existing.key),
                    )
                )
                fork_key = _http_bridge_parallel_fork_key(
                    key=key,
                    session=existing,
                    inflight_creation=key in self._http_bridge_inflight_sessions
                    and not bool(existing and existing.handoff_in_progress),
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                    request_scope_id=request_scope_id,
                    allow_model_fork=reusable or model_transition_rebind,
                    force_canonical_replacement=force_goal_restart_account_reselection,
                )
                if fork_key is not None:
                    if existing is not None:
                        bind_account_neutral_recovery_owner(existing)
                    model_transition_parent_key = key
                    key = fork_key
                    durable_lookup = None
                    force_durable_takeover_after_detach = False
                    locally_owned_fork_key = _http_bridge_locally_owned_fork_key(
                        fork_key, forwarded_request, forwarded_original_request_unanchored
                    )
                    continue
                if retained_handoff and not reusable:
                    existing, force_durable_takeover = self._recover_http_bridge_incompatible_admission_handoff(
                        key,
                        existing,
                        force_durable_takeover,
                        original_request_unanchored,
                        request_model,
                        api_key,
                        incoming_turn_state,
                        previous_response_id,
                        preferred_account_id,
                        require_preferred_account,
                        request_service_tier,
                    )
                if reusable:
                    assert existing is not None
                    current_instance = settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(durable_lookup, current_instance=current_instance):
                        existing.api_key = api_key
                        existing.request_model = request_model
                        existing.request_service_tier = request_service_tier
                        existing.last_used_at = _service_time().monotonic()
                        await _refresh_reused_http_bridge_session_with_handoff(
                            self,
                            existing,
                            key=key,
                            request_scope_id=request_scope_id,
                            reserve_handoff=original_request_unanchored,
                        )
                        return existing
                    old_account_id = existing.account.id
                    detached = self._detach_http_bridge_session_locked(key, expected_session=existing)
                    if detached is not None:
                        force_durable_takeover = True
                        self._schedule_http_bridge_session_closes([detached], reason="registry_detach")
                    existing = None
                if existing is not None and (
                    force_goal_restart_account_reselection
                    or (not existing.closed and existing.account.status in ROUTABLE_STATUSES)
                ):
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
                            if self._http_bridge_forced_close_must_finish_before_create(
                                force_goal_restart_account_reselection,
                                max_sessions,
                            ):
                                sessions_to_close_before_create.append(detached)
                            else:
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
                    hard_continuity_lookup = owner_check_required or bool(incoming_turn_state or previous_response_id)
                    ring_lookup_failed = False
                    if key == locally_owned_fork_key:
                        owner_instance = settings.http_responses_session_bridge_instance_id
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
                            hard_continuity_lookup
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
                if existing is not None and not existing.handoff_in_progress:
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
                                and _http_bridge_compatible(previous_session, request_model, request_service_tier, True)
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
                                    previous_session.request_service_tier = request_service_tier
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
                            elif previous_session is not None and not _http_bridge_models_compatible(
                                previous_session.request_model, request_model
                            ):
                                model_transition_rebind = True
                                model_transition_parent_key = previous_key
                                if is_http_bridge_account_neutral_replay(
                                    kind=previous_key.affinity_kind,
                                    key=previous_key.affinity_key,
                                ):
                                    recovery_fork_key = _http_bridge_incompatible_model_fork_key(
                                        key=previous_key,
                                        existing_model=previous_session.request_model,
                                        request_model=request_model,
                                        request_scope_id=request_scope_id,
                                    )
                                    assert recovery_fork_key is not None
                                    bind_account_neutral_recovery_owner(previous_session)
                                    if key != recovery_fork_key:
                                        key = recovery_fork_key
                                        continue
                            elif previous_key is not None and _http_bridge_alias_target_is_stale(previous_session):
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                    if (
                        session_to_return_after_close is None
                        and previous_response_id is not None
                        and not used_session_header_fallback
                        and not allow_previous_response_recovery_rebind
                        and durable_lookup is None
                        and not model_transition_rebind
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
                    elif session_to_return_after_close is None and inflight_future is None and owner_forward is None:
                        # Owner forwards never resolve a local inflight reservation; skip admission.
                        # Detached generations remain globally capacity-owned
                        # until close finalization. This request may discount
                        # only the idle generations it has committed to close
                        # synchronously below, before its inflight reservation
                        # can create a replacement socket.
                        _plan_http_bridge_lru_capacity_closes(
                            self,
                            max_sessions=max_sessions,
                            model_transition_parent_key=model_transition_parent_key,
                            sessions_to_close_before_create=sessions_to_close_before_create,
                        )
                        if (
                            _http_bridge_capacity_after_planned_closes(self, sessions_to_close_before_create)
                            >= max_sessions
                        ):
                            if _http_bridge_inflight_creation_count(self):
                                capacity_wait_future = next(
                                    future
                                    for future in self._http_bridge_inflight_sessions.values()
                                    if not getattr(future, "_http_bridge_handoff", False)
                                )
                            else:
                                capacity_error = self._http_bridge_active_capacity_error(
                                    key=key,
                                    request_model=request_model,
                                )
                                if not sessions_to_close_before_create:
                                    raise capacity_error
                                # Detachment already transferred these LRU
                                # generations out of the canonical registry.
                                # Give each one a bounded-close owner before
                                # rejecting admission; otherwise this early 429
                                # leaves its live socket and leases stranded in
                                # the detached registry until unrelated cleanup.
                                capacity_error_after_planned_closes = capacity_error
                        else:
                            inflight_future = asyncio.get_running_loop().create_future()
                            setattr(
                                inflight_future,
                                _HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR,
                                _service_time().monotonic(),
                            )
                            self._http_bridge_inflight_sessions[key] = inflight_future
                            owns_creation = True
            try:
                for session_to_close in sessions_to_close_before_create:
                    await self._close_http_bridge_session_bounded(session_to_close, reason="registry_detach")
            except BaseException as exc:
                if owns_creation:
                    await self._fail_http_bridge_inflight_session_creation(key, inflight_future, exc)
                raise
            if capacity_error_after_planned_closes is not None:
                raise capacity_error_after_planned_closes
            if owns_creation and sessions_to_close_before_create:
                await self._enforce_http_bridge_capacity_after_planned_closes(
                    key=key,
                    inflight_future=inflight_future,
                    max_sessions=max_sessions,
                    request_model=request_model,
                )
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
                    # Not wait_for(shield(...)): shield attaches per-waiter
                    # callbacks to the shared registry future, which livelocks
                    # the event loop under mass timeout (see shared_future.py).
                    await wait_on_shared_future(
                        capacity_wait_future,
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
                        pending_count=_http_bridge_session_generation_count(self),
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
                    # Not wait_for(shield(...)): shield attaches per-waiter
                    # callbacks to the shared registry future, which livelocks
                    # the event loop under mass timeout (see shared_future.py).
                    session = await wait_on_shared_future(
                        inflight_future,
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
                        pending_count=_http_bridge_session_generation_count(self),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise timeout_error from exc
                except Exception:
                    raise
                if session is None:
                    continue
                fork_key = _http_bridge_parallel_fork_key(
                    key=key,
                    session=session,
                    inflight_creation=False,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                    request_scope_id=request_scope_id,
                    same_model_required=True,
                    force_canonical_replacement=force_goal_restart_account_reselection,
                )
                if fork_key is not None:
                    bind_account_neutral_recovery_owner(session)
                    model_transition_parent_key, key = key, fork_key
                    durable_lookup = None
                    force_durable_takeover_after_detach = False
                    locally_owned_fork_key = _http_bridge_locally_owned_fork_key(
                        fork_key, forwarded_request, forwarded_original_request_unanchored
                    )
                    continue
                if (
                    not force_goal_restart_account_reselection
                    and not session.closed
                    and _http_bridge_session_account_active(session)
                    and _http_bridge_session_allows_api_key(session, api_key)
                    and _http_bridge_compatible(session, request_model, request_service_tier, True)
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
                        session.request_service_tier = request_service_tier
                        session.last_used_at = _service_time().monotonic()
                        return session
                if force_goal_restart_account_reselection or (
                    not session.closed and session.account.status in ROUTABLE_STATUSES
                ):
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
                        self._schedule_http_bridge_session_closes([detached], reason="registry_detach")
                continue
            created_session: _HTTPBridgeSession | None = None
            session_registered = False
            try:
                create_session = self._create_http_bridge_session
                preferred_account_is_continuity_owner = preferred_account_id is not None and (
                    preferred_account_has_continuity_provenance
                    or previous_response_id is not None
                    or is_http_bridge_account_neutral_replay(
                        kind=key.affinity_kind,
                        key=key.affinity_key,
                    )
                )
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
                    "preferred_account_is_continuity_owner": preferred_account_is_continuity_owner,
                    "fallback_on_preferred_account_unavailable": (
                        fallback_on_preferred_account_unavailable and not require_preferred_account
                    ),
                    "request_usage_budget": request_usage_budget,
                    "request_deadline": request_deadline,
                    "exclude_account_ids": exclude_account_ids,
                    "deferred_account_backoff_lifecycle": deferred_account_backoff_lifecycle,
                    "defer_account_health_writes": defer_account_health_writes,
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
                    for optional_kwarg in (
                        "request_service_tier",
                        "request_usage_budget",
                        "request_deadline",
                        "exclude_account_ids",
                        "preferred_account_is_continuity_owner",
                        "deferred_account_backoff_lifecycle",
                        "defer_account_health_writes",
                    ):
                        if optional_kwarg not in create_signature.parameters:
                            create_kwargs.pop(optional_kwarg, None)
                created_session = await create_session(key, **create_kwargs)
                await _raise_if_http_bridge_creation_superseded(self, key, inflight_future=inflight_future)
                claim_kwargs: dict[str, Any] = {
                    "allow_takeover": _http_bridge_claim_allows_takeover(
                        durable_lookup,
                        force=force_durable_takeover,
                    ),
                    "force_owner_epoch_advance": (force_durable_takeover or same_replica_durable_predecessor),
                }
                restart_takeover = durable_lookup is not None and _http_bridge_allow_durable_takeover(durable_lookup)
                if restart_takeover:
                    # restart_takeover means recovering a row whose previous
                    # owner is genuinely gone. Every claim now advances the
                    # epoch, so epoch > 1 alone would also count ordinary
                    # local successor claims (no pre-claim lookup, or a
                    # forced replace of a live local session).
                    claim_kwargs["record_restart_takeover"] = True
                await self._claim_durable_http_bridge_session(created_session, **claim_kwargs)
                async with self._http_bridge_lock:
                    current_future = self._http_bridge_inflight_sessions.get(key)
                    if current_future is inflight_future:
                        self._http_bridge_inflight_sessions.pop(key, None)
                        if original_request_unanchored:
                            _reserve_http_bridge_unanchored_handoff(created_session, request_scope_id=request_scope_id)
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
                superseded = await _settle_failed_http_bridge_creation(
                    self,
                    key,
                    inflight_future=inflight_future,
                    created_session=created_session,
                    exc=exc,
                )
                if created_session is not None and not session_registered:
                    await self._close_http_bridge_session(
                        created_session,
                        release_durable_session=not superseded,
                    )
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

    async def mark_http_bridge_draining(self) -> bool:
        try:
            await self._durable_bridge.mark_instance_draining(
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            )
        except Exception:
            logger.warning("Failed to mark durable HTTP bridge sessions draining", exc_info=True)
            return False
        return True

    def _prune_http_bridge_sessions_locked(self) -> list["_HTTPBridgeSession"]:
        now = _service_time().monotonic()
        stale_keys: list[_HTTPBridgeSessionKey] = []
        for key, session in self._http_bridge_sessions.items():
            if _http_bridge_session_has_admission_waiter(session):
                continue
            if session.handoff_in_progress:
                continue
            if session.closed:
                stale_keys.append(key)
                continue
            if getattr(session, "unanchored_reservation_id", None) is not None:
                continue
            pending_count = self._http_bridge_pending_count_nowait(session, context="idle_prune")
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

    _close_http_bridge_session = _helpers_close_http_bridge_session

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
        preferred_account_is_continuity_owner: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_deadline: float | None = None,
        exclude_account_ids: Collection[str] | None = None,
        deferred_account_backoff_lifecycle: _DeferredAccountBackoffLifecycle | None = None,
        defer_account_health_writes: bool = False,
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
        excluded_account_ids = set(exclude_account_ids or ())
        if require_preferred_account:
            fallback_on_preferred_account_unavailable = False
        retry_same_account_once = preferred_account_id is not None
        proxy_connect_failover = _HTTPBridgePreDispatchFailover(
            excluded_account_ids,
            preferred_account_id,
            affinity.reallocate_sticky,
        )
        selected_account_lease: AccountLease | None = None
        while True:
            select_kwargs = {
                "request_id": request_state.request_log_id or request_state.request_id,
                "kind": "http_bridge",
                "request_stage": request_stage,
                "api_key": api_key,
                "affinity_policy": (
                    replace(affinity, reallocate_sticky=True)
                    if proxy_connect_failover.reallocate_sticky and not affinity.reallocate_sticky
                    else affinity
                ),
                "prefer_earlier_reset_accounts": settings.prefer_earlier_reset_accounts,
                "prefer_earlier_reset_window": _prefer_earlier_reset_window(settings),
                "routing_strategy": _routing_strategy(settings),
                "model": request_model,
                "service_tier": request_service_tier,
                "exclude_account_ids": excluded_account_ids,
                "preferred_account_id": proxy_connect_failover.preferred_account_id,
                "preferred_account_is_continuity_owner": preferred_account_is_continuity_owner,
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
                if proxy_connect_failover.last_error is not None:
                    # Preserve a confirmed pre-dispatch route failure instead
                    # of generating ``no_accounts``.
                    raise proxy_connect_failover.last_error
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and preferred_account_is_continuity_owner
                    and selection.error_code in (CONTINUITY_OWNER_UNAVAILABLE, "hard_affinity_saturated")
                ):
                    raise _http_bridge_previous_response_owner_unavailable_error()
                status_code, error_payload = selection_failure_response(selection)
                raise ProxyResponseError(status_code, error_payload)
            if require_preferred_account and preferred_account_id is not None and account.id != preferred_account_id:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                if preferred_account_is_continuity_owner:
                    raise _http_bridge_previous_response_owner_unavailable_error()
                raise ProxyResponseError(
                    503,
                    openai_error(
                        "preferred_account_unavailable",
                        "Preferred account is unavailable; retry later.",
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
                if await proxy_connect_failover.handle(
                    self,
                    account,
                    selected_account_lease,
                    exc,
                    required_account=require_preferred_account and selected_is_preferred,
                    deferred_account_backoff_lifecycle=deferred_account_backoff_lifecycle,
                    defer_account_health_write=defer_account_health_writes,
                ):
                    selected_account_lease = None
                    continue
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
                    if await proxy_connect_failover.handle(
                        self,
                        account,
                        selected_account_lease,
                        retry_exc,
                        required_account=require_preferred_account and selected_is_preferred,
                        deferred_account_backoff_lifecycle=deferred_account_backoff_lifecycle,
                        defer_account_health_write=defer_account_health_writes,
                    ):
                        selected_account_lease = None
                        continue
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
                    proxy_connect_failover.preferred_account_id = None
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
                    proxy_connect_failover.preferred_account_id = None
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
                    proxy_connect_failover.preferred_account_id = None
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
                    proxy_connect_failover.preferred_account_id = None
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
            affinity=_persistent_http_bridge_affinity(affinity),
            api_key=api_key,
            request_model=request_model,
            request_service_tier=request_service_tier,
            account=account,
            upstream=upstream,
            upstream_control=_WebSocketUpstreamControl(),
            pending_requests=deque(),
            pending_lock=fast_lock(),
            response_create_gate=asyncio.Semaphore(1),
            queued_request_count=0,
            lifecycle_lock=fast_lock(),
            last_used_at=_service_time().monotonic(),
            idle_ttl_seconds=idle_ttl_seconds,
            codex_session=(affinity.kind == StickySessionKind.CODEX_SESSION or key.affinity_kind == "thread_header"),
            access_token_expires_at=_token_expiry(account, self._encryptor),
            prewarm_lock=fast_lock(),
            upstream_turn_state=_upstream_turn_state_from_socket(upstream),
            downstream_turn_state=None,
            account_lease=selected_account_lease,
            catalog_omission_quota_admission=selection.catalog_omission_quota_admission,
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
        require_same_account: bool = False,
        require_preferred_account: bool = False,
        owner_rebind_affinity: _AffinityPolicy | None = None,
        selection_affinity: _AffinityPolicy | None = None,
    ) -> None:
        request_state.response_create_sent_at = None
        goal_restart = request_state.affinity_policy.abandon_unavailable_legacy_owner
        if selection_affinity is None and goal_restart:
            selection_affinity = request_state.affinity_policy
        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind, key=session.key.affinity_key
        )
        require_same_account = account_neutral_recovery or (require_same_account and not goal_restart)
        old_upstream = session.upstream
        old_reader = session.upstream_reader if restart_reader else None
        session.handoff_in_progress = True
        inflight_sessions = self._http_bridge_inflight_sessions
        handoff_future = inflight_sessions.get(session.key) or asyncio.get_running_loop().create_future()
        inflight_sessions.setdefault(session.key, handoff_future)
        setattr(handoff_future, "_http_bridge_handoff", True)
        session.handoff_future = handoff_future
        session.closed = True
        if old_reader is not None:
            if old_reader is not asyncio.current_task():
                try:
                    cancelled = await _await_cancelled_task(
                        old_reader,
                        label="http bridge upstream reader",
                        cleanup_tasks=self._background_cleanup_tasks,
                    )
                except BaseException:
                    session.closed = True
                    _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)
                    raise
                if not cancelled:
                    session.closed = True
                    _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)
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
        try:
            settings = await _service_get_settings_cache().get()
            session.api_key = request_state.api_key
            forced_refresh_account_id = request_state.force_refresh_account_id
            excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
            requested_preferred_account_id = resolve_reconnect_preferred_account_id(
                request_state, session.account.id, require_preferred_account, account_neutral_recovery
            )
            required_preferred_account_id = resolve_required_account_id(
                ("requested reconnect owner", requested_preferred_account_id),
                ("account-neutral recovery", session.account.id if account_neutral_recovery else None),
            )
            close_skips_account = session.last_upstream_close_code in _UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY
            hard_close_account_bound = session.key.strength == "hard" and (close_skips_account or require_same_account)
            skip_same_account = (
                session.key.strength != "hard" and close_skips_account and required_preferred_account_id is None
            )
            if required_preferred_account_id is not None and required_preferred_account_id in excluded_account_ids:
                session.closed = True
                _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)
                raise _http_bridge_previous_response_owner_unavailable_error()
            _require_http_bridge_bound_account_not_excluded(
                hard_close_account_bound, session.account.id, excluded_account_ids
            )
        except BaseException:
            session.closed = True
            _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)
            raise
        if skip_same_account:
            excluded_account_ids.add(session.account.id)
        retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
        if skip_same_account:
            preferred_candidate_id: str | None = None
        elif hard_close_account_bound and session.account.id not in excluded_account_ids:
            preferred_candidate_id = session.account.id
        elif required_preferred_account_id is not None:
            preferred_candidate_id = required_preferred_account_id
        elif forced_refresh_account_id is not None:
            preferred_candidate_id = forced_refresh_account_id
        elif request_state.preferred_account_id is not None:
            preferred_candidate_id = request_state.preferred_account_id
        elif session.account.id not in excluded_account_ids:
            preferred_candidate_id = session.account.id
        else:
            preferred_candidate_id = None
        selected_account_lease: AccountLease | None = None
        selected_account_model_replacement = False
        file_owner = request_state.file_required_preferred_account

        def record_selected_account_takeover(
            selected_account_id: str | None, preferred_account_id: str | None = session.account.id
        ) -> None:
            _record_same_account_takeover(
                preferred_account_id=preferred_account_id,
                selected_account_id=selected_account_id,
            )

        async def release_selected_account_lease() -> None:
            nonlocal selected_account_lease
            lease, selected_account_lease = selected_account_lease, None
            if lease is None:
                return
            async with session.pending_lock:
                if session.account_lease is not None and lease.lease_id == session.account_lease.lease_id:
                    session.account_lease = None
            try:
                await self._load_balancer.release_account_lease(lease)
            except BaseException:
                complete_failed_handoff()
                raise

        async def abandon_selected_account_retry(selected_account: Any) -> None:
            nonlocal preferred_candidate_id
            if hard_close_account_bound or selected_account_model_replacement:
                await release_selected_account_lease()
                complete_failed_handoff()
                raise
            excluded_account_ids.add(selected_account.id)
            preferred_candidate_id = None
            await release_selected_account_lease()

        async def open_replacement_upstream(selected_account: Any, selected_headers: dict[str, str]) -> Any:
            try:
                return await self._open_upstream_websocket_with_budget(
                    selected_account,
                    selected_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                    request_state=request_state,
                )
            except Exception:
                session.closed = True
                raise

        def complete_failed_handoff() -> None:
            session.closed = True
            _mark_http_bridge_reader_handoff_reconnect_failed(session, old_reader)
            _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)

        def require_bound_account() -> None:
            try:
                _require_http_bridge_bound_account_not_excluded(
                    hard_close_account_bound, session.account.id, excluded_account_ids
                )
            except BaseException:
                complete_failed_handoff()
                raise

        while True:
            reuse_current_account_lease = preferred_candidate_id == session.account.id and bool(session.account_lease)
            try:
                selection = await self._select_account_with_budget_for_stream(
                    deadline,
                    request_id=request_state.request_log_id or request_state.request_id,
                    kind="http_bridge",
                    request_stage="reattach",
                    api_key=session.api_key,
                    affinity_policy=selection_affinity or session.affinity,
                    prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                    prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                    routing_strategy=_routing_strategy(settings),
                    model=session.request_model,
                    service_tier=session.request_service_tier,
                    exclude_account_ids=excluded_account_ids,
                    preferred_account_id=preferred_candidate_id,
                    preferred_account_is_continuity_owner=account_neutral_recovery or file_owner,
                    preferred_account_overrides_single_account_routing=file_owner,
                    require_security_work_authorized=require_security_work_authorized,
                    lease_kind=None if reuse_current_account_lease else "stream",
                    estimated_lease_tokens=_estimated_lease_tokens_from_request_usage_budget(
                        request_state.request_usage_budget
                    ),
                    fallback_on_preferred_account_unavailable=(
                        not reuse_current_account_lease
                        and not hard_close_account_bound
                        and required_preferred_account_id is None
                    ),
                )
            except BaseException:
                complete_failed_handoff()
                raise
            account = selection.account
            if account is None:
                try:
                    await release_selected_account_lease()
                except BaseException:
                    complete_failed_handoff()
                    raise
                if required_preferred_account_id is not None and selection.continuity_owner_no_longer_exists:
                    complete_failed_handoff()
                    raise _http_bridge_previous_response_owner_unavailable_error()
                if (
                    reuse_current_account_lease
                    and not hard_close_account_bound
                    and required_preferred_account_id is None
                    and _remaining_budget_seconds(deadline) > 0
                ):
                    preferred_candidate_id = None
                    continue
                if selection.error_code == USAGE_LIMIT_REACHED and (
                    required_preferred_account_id is not None or hard_close_account_bound
                ):
                    complete_failed_handoff()
                    raise _http_bridge_previous_response_owner_unavailable_error()
                if selection.error_code == USAGE_LIMIT_REACHED:
                    record_selected_account_takeover(None)
                    status_code, error_payload = selection_failure_response(selection)
                    complete_failed_handoff()
                    raise ProxyResponseError(status_code, error_payload)
                try:
                    should_retry_selection = await _sleep_for_account_selection_recovery(
                        selection,
                        request_id=request_state.request_log_id or request_state.request_id,
                        kind="http_bridge",
                        request_stage="reattach",
                        model=session.request_model,
                        max_sleep_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                except BaseException:
                    complete_failed_handoff()
                    raise
                if should_retry_selection:
                    excluded_account_ids.update(request_state.excluded_account_ids)
                    if required_preferred_account_id in excluded_account_ids:
                        complete_failed_handoff()
                        raise _http_bridge_previous_response_owner_unavailable_error()
                    if skip_same_account:
                        excluded_account_ids.add(session.account.id)
                    require_bound_account()
                    retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
                    if skip_same_account:
                        preferred_candidate_id = None
                    elif hard_close_account_bound and session.account.id not in excluded_account_ids:
                        preferred_candidate_id = session.account.id
                    elif required_preferred_account_id is not None:
                        preferred_candidate_id = required_preferred_account_id
                    elif forced_refresh_account_id is not None:
                        preferred_candidate_id = forced_refresh_account_id
                    elif request_state.preferred_account_id is not None:
                        preferred_candidate_id = request_state.preferred_account_id
                    elif session.account.id not in excluded_account_ids:
                        preferred_candidate_id = session.account.id
                    else:
                        preferred_candidate_id = None
                    continue
                record_selected_account_takeover(None)
                complete_failed_handoff()
                raise _http_bridge_reconnect_selection_failure(selection, required_preferred_account_id)
            if required_preferred_account_id is not None and account.id != required_preferred_account_id:
                if selection.lease is not None:
                    selected_account_lease = selection.lease
                    await release_selected_account_lease()
                record_selected_account_takeover(account.id, required_preferred_account_id)
                complete_failed_handoff()
                raise _http_bridge_previous_response_owner_unavailable_error()
            selected_account_lease = (
                session.account_lease
                if reuse_current_account_lease and account.id == session.account.id
                else selection.lease
            )
            selected_account_model_replacement = (
                request_state.precreated_replay_reason == _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE
                and account.id != request_state.precreated_replay_account_id
            )
            if selected_account_model_replacement:
                _clear_websocket_precreated_replay_fallback(request_state)
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
                    session.headers,
                    None if owner_rebind_affinity is not None else _preferred_http_bridge_reconnect_turn_state(session),
                )
                upstream = await open_replacement_upstream(account, connect_headers)
                _copy_websocket_route_metadata_to_session(session, request_state)
                record_selected_account_takeover(account.id)
                break
            except ProxyResponseError as exc:
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await release_selected_account_lease()
                    complete_failed_handoff()
                    raise _http_bridge_reconnect_connect_failure(exc, required_preferred_account_id) from exc
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _websocket_safe_headers_with_turn_state(
                        session.headers,
                        (
                            None
                            if owner_rebind_affinity is not None
                            else _preferred_http_bridge_reconnect_turn_state(session)
                        ),
                    )
                    upstream = await open_replacement_upstream(account, connect_headers)
                    _copy_websocket_route_metadata_to_session(session, request_state)
                    record_selected_account_takeover(account.id)
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await release_selected_account_lease()
                        complete_failed_handoff()
                        raise _http_bridge_reconnect_connect_failure(retry_exc, required_preferred_account_id)
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
                complete_failed_handoff()
                raise _http_bridge_reconnect_connect_failure(exc, required_preferred_account_id)
            except (aiohttp.ClientError, asyncio.TimeoutError) as transport_exc:
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    await abandon_selected_account_retry(account)
                    continue
                await release_selected_account_lease()
                complete_failed_handoff()
                raise _http_bridge_reconnect_connect_failure(transport_exc, required_preferred_account_id)
            except asyncio.CancelledError:
                session.closed = True
                await release_selected_account_lease()
                complete_failed_handoff()
                raise
            except BaseException:
                await release_selected_account_lease()
                complete_failed_handoff()
                raise

        async def abort_selected_handoff() -> None:
            session.closed = True
            try:
                await asyncio.shield(upstream.close())
            except BaseException:
                logger.debug("Failed to close HTTP bridge replacement websocket", exc_info=True)
            selected_lease = selected_account_lease
            old_lease = session.account_lease
            try:
                await asyncio.shield(release_selected_account_lease())
            except BaseException:
                logger.debug("Failed to release HTTP bridge replacement lease", exc_info=True)
            if old_lease is not None and old_lease is not selected_lease:
                session.account_lease = None
                try:
                    await asyncio.shield(self._load_balancer.release_account_lease(old_lease))
                except BaseException:
                    logger.debug("Failed to release HTTP bridge old account lease", exc_info=True)
            complete_failed_handoff()

        try:
            if owner_rebind_affinity is not None:
                await self._claim_http_bridge_replacement_before_swap(
                    session,
                    account_id=account.id,
                    upstream=upstream,
                    release_selected_account_lease=release_selected_account_lease,
                    owner_rebind_affinity=owner_rebind_affinity,
                )
            if owner_rebind_affinity is not None or account.id != session.account.id:
                await self._unregister_http_bridge_turn_states(session)
                await self._unregister_http_bridge_previous_response_ids(session)
                session.last_completed_response_id = None
                session.last_completed_response_account_id = None
                session.last_completed_input_count = 0
                session.last_completed_input_prefix_fingerprint = None
                session.last_pending_tool_calls.clear()
                session.affinity = _persistent_http_bridge_affinity(selection_affinity or session.affinity)
                session.codex_session = session.key.affinity_kind == "thread_header"
                session.upstream_turn_state = None
                session.downstream_turn_state = None
                session.headers = {
                    key: value for key, value in session.headers.items() if key.lower() != "x-codex-turn-state"
                }
            await _persist_http_bridge_replacement_account(self, session, account.id)
            try:
                await old_upstream.close()
            except Exception:
                logger.debug("Failed to close HTTP bridge upstream websocket before reconnect", exc_info=True)
            session.closed = True
            if selected_account_lease is not session.account_lease:
                old_lease = session.account_lease
                if old_lease is not None:
                    await self._load_balancer.release_account_lease(old_lease)
                    session.account_lease = None
            session.account_lease = selected_account_lease
            session.replace_connection(account, connect_headers, upstream, _token_expiry(account, self._encryptor))
            session.catalog_omission_quota_admission = selection.catalog_omission_quota_admission
            session.upstream_control = _WebSocketUpstreamControl()
            session.closed = False
            session.last_upstream_close_code = None
            session.upstream_turn_state = _upstream_turn_state_from_socket(upstream) or session.upstream_turn_state
            _complete_http_bridge_handoff(session, self._http_bridge_inflight_sessions)
        except BaseException:
            await abort_selected_handoff()
            raise
        if restart_reader:
            session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        _log_http_bridge_event(
            "reconnect",
            session.key,
            account_id=account.id,
            model=session.request_model,
            detail=f"selected_account_id={account.id}, durable_session_id={session.durable_session_id}",
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
