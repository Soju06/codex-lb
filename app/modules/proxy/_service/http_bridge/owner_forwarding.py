from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Mapping, TypeVar

import aiohttp

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
    bridge_forward_latency_seconds,
    bridge_owner_forward_total,
)
from app.core.openai.requests import (
    ResponsesRequest,
)
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import (
    AccountStatus,
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
    _durable_bridge_lookup_active_owner,
    _http_bridge_previous_response_alias_key,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_turn_state_alias_key,
    _log_http_bridge_event,
    _normalized_http_bridge_instance_ring,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _header_value_case_insensitive,
    _headers_with_authorization,
    _partial_output_proxy_error_event_block,
    _record_continuity_owner_resolution,
    _service_get_settings,
    _service_time,
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
    _HTTPBridgeOwnerForward,
    _HTTPBridgeSessionKey,
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
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.http_bridge_forwarding import (
    HTTPBridgeForwardContext,
    OwnerForwardRelayFailure,
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


class _HTTPBridgeOwnerForwardingMixin:
    async def _http_bridge_has_live_local_session(
        self: Any,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        api_key: ApiKeyData | None,
    ) -> bool:
        api_key_id = api_key.id if api_key is not None else None
        async with self._http_bridge_lock:
            candidate_keys = [key]
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                if not _http_bridge_session_reusable_for_request(
                    session=session,
                    key=candidate_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=None,
                ) and not _http_bridge_session_retiring_with_visible_requests(session):
                    continue
                return True
        return False

    async def _http_bridge_local_owner_account_id(
        self: Any,
        *,
        key: "_HTTPBridgeSessionKey",
        incoming_turn_state: str | None,
        previous_response_id: str,
        api_key: ApiKeyData | None,
    ) -> str | None:
        api_key_id = api_key.id if api_key is not None else None
        candidate_keys: list[_HTTPBridgeSessionKey] = [key]
        async with self._http_bridge_lock:
            if incoming_turn_state is not None:
                alias_key = self._http_bridge_turn_state_index.get(
                    _http_bridge_turn_state_alias_key(incoming_turn_state, api_key_id)
                )
                if alias_key is not None and alias_key not in candidate_keys:
                    candidate_keys.append(alias_key)
            previous_alias_key = _http_bridge_previous_response_alias_key(previous_response_id, api_key_id)
            previous_key = self._http_bridge_previous_response_index.get(previous_alias_key)
            if previous_key is not None and previous_key not in candidate_keys:
                candidate_keys.append(previous_key)
            for candidate_key in candidate_keys:
                session = self._http_bridge_sessions.get(candidate_key)
                if session is None or session.closed or session.account.status != AccountStatus.ACTIVE:
                    continue
                if not _http_bridge_session_allows_api_key(session, api_key):
                    continue
                if not _http_bridge_session_reusable_for_request(
                    session=session,
                    key=candidate_key,
                    incoming_turn_state=incoming_turn_state,
                    previous_response_id=previous_response_id,
                ):
                    continue
                _record_continuity_owner_resolution(
                    surface="http_bridge",
                    source="local_bridge_session",
                    outcome="hit",
                    previous_response_id=previous_response_id,
                    session_id=incoming_turn_state,
                )
                return session.account.id
        _record_continuity_owner_resolution(
            surface="http_bridge",
            source="local_bridge_session",
            outcome="miss",
            previous_response_id=previous_response_id,
            session_id=incoming_turn_state,
        )
        return None

    async def _http_bridge_can_forward_to_active_owner(
        self: Any,
        durable_lookup: DurableBridgeLookup,
    ) -> bool:
        owner_instance = _durable_bridge_lookup_active_owner(durable_lookup)
        if owner_instance is None:
            return False
        if owner_instance == _service_get_settings().http_responses_session_bridge_instance_id:
            return False
        if self._ring_membership is None:
            return False
        try:
            owner_endpoint = await self._ring_membership.resolve_endpoint(owner_instance)
        except Exception:
            logger.debug("Failed to resolve HTTP bridge owner endpoint during anchor injection decision", exc_info=True)
            return False
        return owner_endpoint is not None

    async def _forward_http_bridge_request_to_owner(
        self: Any,
        *,
        owner_forward: _HTTPBridgeOwnerForward,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        api_key_reservation: ApiKeyUsageReservationData | None,
        codex_session_affinity: bool,
        downstream_turn_state: str | None,
        request_started_at: float,
        proxy_api_authorization: str | None,
    ) -> AsyncIterator[str]:
        current_instance, _ = _normalized_http_bridge_instance_ring(_service_get_settings())
        forwarded_turn_state = _header_value_case_insensitive(headers, "x-codex-turn-state") or downstream_turn_state
        forward_context = HTTPBridgeForwardContext(
            origin_instance=current_instance,
            target_instance=owner_forward.owner_instance,
            reservation=api_key_reservation,
            codex_session_affinity=codex_session_affinity,
            downstream_turn_state=forwarded_turn_state,
            original_affinity_kind=owner_forward.key.affinity_kind,
            original_affinity_key=owner_forward.key.affinity_key,
        )
        forward_headers = _headers_with_authorization(headers, proxy_api_authorization)
        start = _service_time().monotonic()
        _log_http_bridge_event(
            "owner_forward_start",
            owner_forward.key,
            account_id=None,
            model=payload.model,
            detail=(
                f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                f"owner_endpoint={owner_forward.owner_endpoint}"
            ),
            cache_key_family=owner_forward.key.affinity_kind,
            model_class=_extract_model_class(payload.model) if payload.model else None,
            owner_check_applied=True,
        )

        forwarded_any = False
        forwarded_response_id: str | None = None
        try:
            async for event_block in self._http_bridge_owner_client.stream_responses(
                owner_endpoint=owner_forward.owner_endpoint,
                payload=payload,
                headers=forward_headers,
                context=forward_context,
                request_started_at=request_started_at,
            ):
                forwarded_any = True
                event_payload = parse_sse_data_json(event_block)
                event_type = _event_type_from_payload(None, event_payload)
                forwarded_response_id = _websocket_response_id(None, event_payload) or forwarded_response_id
                if event_type == "response.failed" and forwarded_response_id is None:
                    forwarded_response_id = get_request_id()
                yield event_block
        except OwnerForwardRelayFailure as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, "
                    "error=relay_failure"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                yield exc.event_block
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner relay timed out",
                    error_type="server_error",
                ),
                failure_phase="owner_forward",
                failure_detail="relay_timeout",
                failure_exception_type=type(exc).__name__,
            ) from exc
        except ProxyResponseError as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                terminal_response_id = forwarded_response_id or get_request_id() or "unknown"
                yield _partial_output_proxy_error_event_block(
                    exc,
                    response_id=terminal_response_id,
                    previous_response_id=payload.previous_response_id,
                    preferred_account_id=None,
                    default_code="bridge_owner_unreachable",
                    default_message="HTTP bridge owner request failed",
                )
                return
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="fail").inc()
            _log_http_bridge_event(
                "owner_forward_fail",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=(
                    f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}, error={exc}"
                ),
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
            if forwarded_any:
                terminal_response_id = forwarded_response_id or get_request_id() or "unknown"
                yield format_sse_event(
                    response_failed_event(
                        "bridge_owner_unreachable",
                        "HTTP bridge owner request failed",
                        response_id=terminal_response_id,
                    )
                )
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_unreachable",
                    "HTTP bridge owner request failed",
                    error_type="server_error",
                ),
                failure_phase="owner_forward",
                failure_detail=str(exc) or "owner_forward_request_failed",
                failure_exception_type=type(exc).__name__,
            ) from exc
        else:
            if PROMETHEUS_AVAILABLE and bridge_owner_forward_total is not None:
                bridge_owner_forward_total.labels(outcome="success").inc()
            _log_http_bridge_event(
                "owner_forward_success",
                owner_forward.key,
                account_id=None,
                model=payload.model,
                detail=f"owner_instance={owner_forward.owner_instance}, current_instance={current_instance}",
                cache_key_family=owner_forward.key.affinity_kind,
                model_class=_extract_model_class(payload.model) if payload.model else None,
                owner_check_applied=True,
            )
        finally:
            if PROMETHEUS_AVAILABLE and bridge_forward_latency_seconds is not None:
                bridge_forward_latency_seconds.observe(max(_service_time().monotonic() - start, 0.0))
