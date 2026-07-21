from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from collections.abc import Collection
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
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_local_rebind_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.core.resilience.overload import local_overload_error
from app.core.utils.request_id import ensure_request_scope_id
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
from app.modules.proxy._service.http_bridge.activity import _HTTPBridgeActivityMixin
from app.modules.proxy._service.http_bridge.helpers import (
    _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
    _HTTP_BRIDGE_INFLIGHT_STARTED_AT_ATTR,
    _active_http_bridge_instance_ring,
    _close_http_bridge_session_bounded,
    _durable_bridge_lookup_active_owner,
    _durable_bridge_lookup_allows_local_reuse,
    _forwarded_http_bridge_session_key,
    _http_bridge_alias_target_is_stale,
    _http_bridge_allow_durable_takeover,
    _http_bridge_can_local_recover_without_ring,
    _http_bridge_can_recover_during_drain,
    _http_bridge_can_single_instance_owner_takeover_without_anchor,
    _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor,
    _http_bridge_compatible,
    _http_bridge_continuity_lost_error_envelope,
    _http_bridge_endpoint_matches_current_instance,
    _http_bridge_eviction_priority,
    _http_bridge_has_durable_recovery_anchor,
    _http_bridge_incompatible_model_fork_key,
    _http_bridge_key_strength,
    _http_bridge_locally_owned_fork_key,
    _http_bridge_models_compatible,
    _http_bridge_owner_check_required,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_parallel_fork_key,
    _http_bridge_previous_response_alias_key,
    _http_bridge_previous_response_owner_unavailable_error,
    _http_bridge_request_budget_seconds,
    _http_bridge_request_needs_unanchored_handoff,
    _http_bridge_session_account_active,
    _http_bridge_session_allows_api_key,
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
    _preferred_http_bridge_reconnect_turn_state,
    _raise_http_bridge_incompatible_admission_handoff,
    _record_bridge_drain_recovery_allowed,
    _record_bridge_first_turn_timeout,
    _refresh_reused_http_bridge_session_with_handoff,
    _register_http_bridge_turn_state_aliases_locked,
    _require_http_bridge_bound_account_not_excluded,
    _reserve_http_bridge_unanchored_handoff,
)
from app.modules.proxy._service.http_bridge.owner_forwarding import _HTTPBridgeOwnerForwardingMixin
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeRequestSubmitMixin
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _call_with_supported_optional_kwargs,
    _estimated_lease_tokens_from_request_usage_budget,
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
from app.modules.proxy._service.http_bridge.session_registry import _HTTPBridgeSessionRegistryMixin
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
    _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE,
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _clear_websocket_precreated_replay_fallback,
    _copy_websocket_route_metadata_to_session,
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
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    resolve_required_account_id,
    without_http_bridge_session_affinity_headers,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.load_balancer import CONTINUITY_OWNER_UNAVAILABLE, AccountLease

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
    async def _close_http_bridge_session_bounded(
        self,
        session: "_HTTPBridgeSession",
        *,
        reason: str,
    ) -> None:
        await _close_http_bridge_session_bounded(self, session, reason=reason)

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
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        settings = _service_get_settings()
        request_scope_id = ensure_request_scope_id()
        api_key_id = api_key.id if api_key is not None else None
        incoming_turn_state = _sticky_key_from_turn_state_header(headers)
        incoming_session_key = _sticky_key_from_session_header(headers)
        initial_session_key = session_header_fallback_key or (key if key.affinity_kind == "session_header" else None)
        original_request_unanchored = _http_bridge_request_needs_unanchored_handoff(
            key, incoming_turn_state, previous_response_id, forwarded_request, forwarded_original_request_unanchored
        )
        model_transition_rebind = bool(
            durable_lookup is not None and not _http_bridge_models_compatible(durable_lookup.model, request_model)
        )
        if model_transition_rebind:
            durable_lookup = None
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
            owns_creation = False
            continuity_error: ProxyResponseError | None = None
            owner_mismatch_error: ProxyResponseError | None = None
            owner_forward: _HTTPBridgeOwnerForward | None = None
            force_durable_takeover = force_durable_takeover_after_detach
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
                                and not previous_session.closed
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
                            elif not _http_bridge_alias_target_is_stale(previous_session):
                                raise ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
                            elif previous_key is not None:
                                self._http_bridge_previous_response_index.pop(previous_alias_key, None)
                        if model_transition_rebind:
                            if not is_http_bridge_account_neutral_replay(
                                kind=key.affinity_kind,
                                key=key.affinity_key,
                            ):
                                key = _HTTPBridgeSessionKey("turn_state_header", incoming_turn_state, api_key_id)
                        elif incoming_session_key is not None:
                            key = initial_session_key or _HTTPBridgeSessionKey(
                                "session_header", incoming_session_key, api_key_id
                            )
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
                reusable = existing is not None and _http_bridge_session_reusable_for_lookup(
                    session=existing,
                    key=key,
                    api_key=api_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                    preferred_account_id=preferred_account_id,
                    require_preferred_account=require_preferred_account,
                    service_tier_supported=_http_bridge_compatible(existing, request_model, request_service_tier),
                    allow_closed_admission_handoff=retained_handoff,
                )
                fork_key = _http_bridge_parallel_fork_key(
                    key=key,
                    session=existing,
                    inflight_creation=key in self._http_bridge_inflight_sessions,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                    request_scope_id=request_scope_id,
                    allow_model_fork=reusable or model_transition_rebind,
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
                    _raise_http_bridge_incompatible_admission_handoff()
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
                    elif inflight_future is None:
                        while (
                            len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= max_sessions
                            and self._http_bridge_sessions
                        ):
                            evictable_sessions: list[tuple[_HTTPBridgeSessionKey, _HTTPBridgeSession]] = []
                            for candidate_key, candidate_session in self._http_bridge_sessions.items():
                                if candidate_key == model_transition_parent_key:
                                    continue
                                if getattr(candidate_session, "unanchored_reservation_id", None) is not None:
                                    continue
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
                    not session.closed
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
                    ):
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
            if _http_bridge_session_has_admission_waiter(session):
                continue
            if session.closed:
                stale_keys.append(key)
                continue
            # The request owns this idle session until submit makes activity visible;
            # pruning it during admission or durable refresh would invalidate the handoff.
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
        preferred_candidate_id = preferred_account_id
        selected_account_lease: AccountLease | None = None
        while True:
            select_kwargs = {
                "request_id": request_state.request_log_id or request_state.request_id,
                "kind": "http_bridge",
                "request_stage": request_stage,
                "api_key": api_key,
                "affinity_policy": affinity,
                "prefer_earlier_reset_accounts": settings.prefer_earlier_reset_accounts,
                "prefer_earlier_reset_window": _prefer_earlier_reset_window(settings),
                "routing_strategy": _routing_strategy(settings),
                "model": request_model,
                "service_tier": request_service_tier,
                "exclude_account_ids": excluded_account_ids,
                "preferred_account_id": preferred_candidate_id,
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
                is_local_account_cap = _is_local_account_cap_code(selection.error_code)
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and preferred_account_is_continuity_owner
                    and selection.error_code == CONTINUITY_OWNER_UNAVAILABLE
                ):
                    raise _http_bridge_previous_response_owner_unavailable_error()
                status_code = 429 if is_local_account_cap else 503
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
    ) -> None:
        # A replacement reader can start before its caller resends the request.
        # Clear the prior attempt first so an expired send timestamp cannot
        # retire the fresh socket before the next real send re-arms it.
        request_state.response_create_sent_at = None
        account_neutral_recovery = is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
        require_same_account = require_same_account or account_neutral_recovery
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
        forced_refresh_account_id = request_state.force_refresh_account_id
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        requested_preferred_account_id = (
            request_state.preferred_account_id if require_preferred_account or account_neutral_recovery else None
        )
        required_preferred_account_id = resolve_required_account_id(
            ("requested reconnect owner", requested_preferred_account_id),
            (
                "account-neutral recovery",
                session.account.id if account_neutral_recovery else None,
            ),
        )
        close_skips_account = session.last_upstream_close_code in _UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY
        hard_close_account_bound = session.key.strength == "hard" and (close_skips_account or require_same_account)
        skip_same_account = (
            session.key.strength != "hard" and close_skips_account and required_preferred_account_id is None
        )
        if required_preferred_account_id is not None and required_preferred_account_id in excluded_account_ids:
            raise _http_bridge_previous_response_owner_unavailable_error()
        _require_http_bridge_bound_account_not_excluded(
            hard_close_account_bound, session.account.id, excluded_account_ids
        )
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

        def record_selected_account_takeover(
            selected_account_id: str | None, preferred_account_id: str | None = session.account.id
        ) -> None:
            _record_same_account_takeover(
                preferred_account_id=preferred_account_id,
                selected_account_id=selected_account_id,
            )

        async def release_selected_account_lease() -> None:
            nonlocal selected_account_lease
            lease = selected_account_lease
            selected_account_lease = None
            if lease is None:
                return
            if lease is session.account_lease:
                session.account_lease = None
            await self._load_balancer.release_account_lease(lease)

        async def abandon_selected_account_retry(selected_account: Any) -> None:
            nonlocal preferred_candidate_id
            if hard_close_account_bound or selected_account_model_replacement:
                await release_selected_account_lease()
                raise
            excluded_account_ids.add(selected_account.id)
            preferred_candidate_id = None
            await release_selected_account_lease()

        while True:
            reuse_current_account_lease = preferred_candidate_id == session.account.id and bool(session.account_lease)
            selection = await self._select_account_with_budget_for_stream(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="http_bridge",
                request_stage="reattach",
                api_key=session.api_key,
                affinity_policy=session.affinity,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                routing_strategy=_routing_strategy(settings),
                model=session.request_model,
                service_tier=session.request_service_tier,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
                preferred_account_is_continuity_owner=account_neutral_recovery,
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
            account = selection.account
            if account is None:
                await release_selected_account_lease()
                if account_neutral_recovery and selection.error_code == CONTINUITY_OWNER_UNAVAILABLE:
                    raise _http_bridge_previous_response_owner_unavailable_error()
                if (
                    reuse_current_account_lease
                    and not hard_close_account_bound
                    and required_preferred_account_id is None
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
                    if required_preferred_account_id in excluded_account_ids:
                        raise _http_bridge_previous_response_owner_unavailable_error()
                    if skip_same_account:
                        excluded_account_ids.add(session.account.id)
                    _require_http_bridge_bound_account_not_excluded(
                        hard_close_account_bound, session.account.id, excluded_account_ids
                    )
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
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                raise ProxyResponseError(
                    status_code,
                    openai_error(
                        selection.error_code or "no_accounts",
                        selection.error_message or "No active accounts available",
                        error_type="rate_limit_error" if status_code == 429 else "server_error",
                    ),
                )
            if required_preferred_account_id is not None and account.id != required_preferred_account_id:
                if selection.lease is not None:
                    await self._load_balancer.release_account_lease(selection.lease)
                record_selected_account_takeover(account.id, required_preferred_account_id)
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
                    session.headers, _preferred_http_bridge_reconnect_turn_state(session)
                )
                upstream = await self._open_upstream_websocket_with_budget(
                    account,
                    connect_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                    request_state=request_state,
                )
                _copy_websocket_route_metadata_to_session(session, request_state)
                record_selected_account_takeover(account.id)
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
                    record_selected_account_takeover(account.id)
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
        session.account, session.headers, session.upstream = account, connect_headers, upstream
        session.catalog_omission_quota_admission = selection.catalog_omission_quota_admission
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
