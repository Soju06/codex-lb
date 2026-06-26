# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any, AsyncIterator, Mapping, cast

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.balancer import failover_decision
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError, _resolve_stream_transport, pop_stream_timeout_overrides
from app.core.errors import openai_error, response_failed_event
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id
from app.core.utils.retry import backoff_seconds
from app.core.utils.sse import format_sse_event
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_request_shape,
    _record_continuity_fail_closed,
    _record_upstream_transport_decision,
)
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
from app.modules.proxy._service.support import (
    _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS,
    _account_capacity_wait_payload,
    _account_selection_recovery_sleep_seconds,
    _request_log_useragent_fields,
    _RetryableStreamError,
    _stream_settlement_error_payload,
    _StreamSettlement,
    _TerminalStreamError,
    _TransientStreamError,
    _WebSocketUpstreamControl,
)
from app.modules.proxy.affinity import (
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
)
from app.modules.proxy.load_balancer import AccountLease

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


def _resolve_http_downstream_transport(policy: str, *, payload: ResponsesRequest, headers: Mapping[str, str]) -> str:
    normalized_policy = policy.strip().lower()
    if normalized_policy not in _HTTP_DOWNSTREAM_TRANSPORT_POLICIES:
        raise ValueError(f"Unsupported HTTP downstream transport policy: {policy}")
    if normalized_policy in ("always_http", "pinned"):
        return "http"
    if normalized_policy == "always_websocket":
        return "websocket"
    return "websocket" if _http_downstream_request_is_sticky(payload, headers) else "http"


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
        upstream_stream_transport_override: str | None = None,
    ) -> AsyncIterator[str]:
        proxy = cast(_StreamingServiceProtocol, self)
        useragent, useragent_group = _request_log_useragent_fields(headers)
        request_id = ensure_request_id()
        start = time.monotonic()
        base_settings = _facade().get_settings()
        settings = await _facade().get_settings_cache().get()
        deadline = start + _facade()._stream_request_budget_seconds(
            base_settings,
            request_transport=request_transport,
        )
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        upstream_transport_policy_label = "explicit" if upstream_stream_transport_override is not None else "configured"
        upstream_transport_sticky = _http_downstream_request_is_sticky(payload, headers)
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
                policy, override_applied = _effective_http_downstream_transport_policy(api_key, settings, base_settings)
                sticky = upstream_transport_sticky
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
        if rewritten_file_account_id is None:
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
        sticky_key_source = "none"
        if affinity.kind == StickySessionKind.CODEX_SESSION:
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
        settlement = _StreamSettlement()
        last_transient_exc: ProxyResponseError | None = None
        last_security_work_retry_error: _RetryableStreamError | None = None
        excluded_account_ids: set[str] = set()
        preferred_account_id: str | None = None
        file_preferred_account_id: str | None = rewritten_file_account_id
        require_preferred_account = False
        last_retryable_stream_error: _RetryableStreamError | None = None
        require_security_work_authorized = False
        account_leases: list[AccountLease] = []
        estimated_lease_tokens = _facade()._estimated_lease_tokens_from_request_usage_budget(
            estimate_api_key_request_usage(payload)
        )

        async def _release_tracked_stream_lease(lease: AccountLease | None) -> None:
            if lease is None:
                return
            try:
                account_leases.remove(lease)
            except ValueError:
                pass
            await proxy._load_balancer.release_account_lease(lease)

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

        try:
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
                        )
                        return
            file_required_preferred_account = False
            if preferred_account_id is None:
                # ``input_file.file_id`` references must land on the account
                # that registered the upload; otherwise upstream rejects the
                # request with not-found / 401. The helper itself enforces
                # priority -- it returns ``None`` when stronger affinity
                # signals (prompt_cache_key / session header / turn_state
                # header) are present, so this never overrides them.
                if rewritten_file_account_id is not None:
                    preferred_account_id = rewritten_file_account_id
                    file_required_preferred_account = True
            if preferred_account_id is None:
                resolved_file_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
                if resolved_file_account_id is not None:
                    file_preferred_account_id = resolved_file_account_id
                    preferred_account_id = resolved_file_account_id
                    file_required_preferred_account = True
            for attempt in range(max_attempts):
                remaining_budget = _facade()._remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
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
                    )
                    yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                    return
                while True:
                    try:
                        selection = await proxy._select_account_with_budget_compatible(
                            deadline,
                            request_id=request_id,
                            kind="stream",
                            api_key=api_key,
                            sticky_key=affinity.key,
                            sticky_kind=affinity.kind,
                            reallocate_sticky=affinity.reallocate_sticky,
                            sticky_max_age_seconds=affinity.max_age_seconds,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            prefer_earlier_reset_window=_facade()._prefer_earlier_reset_window(settings),
                            routing_strategy=routing_strategy,
                            model=payload.model,
                            exclude_account_ids=excluded_account_ids,
                            preferred_account_id=preferred_account_id,
                            require_security_work_authorized=require_security_work_authorized,
                            lease_kind="stream",
                            estimated_lease_tokens=estimated_lease_tokens,
                            fallback_on_preferred_account_unavailable=not file_required_preferred_account,
                        )
                    except ProxyResponseError as exc:
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
                            )
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
                    if (
                        not account
                        and not _facade()._is_local_account_cap_code(selection.error_code)
                        and not (propagate_http_errors and last_transient_exc is not None)
                        and last_retryable_stream_error is None
                        and last_security_work_retry_error is None
                    ):
                        recovery_sleep_seconds = _account_selection_recovery_sleep_seconds(selection)
                        if recovery_sleep_seconds is not None:
                            remaining_budget_seconds = _facade()._remaining_budget_seconds(deadline)
                            if remaining_budget_seconds <= 0:
                                break
                            wait_started_at = time.monotonic()
                            remaining_sleep_seconds = min(recovery_sleep_seconds, remaining_budget_seconds)
                            _facade().logger.info(
                                "Waiting for an account to recover before retrying stream selection "
                                "request_id=%s model=%s sleep_seconds=%.1f recovery_hint_seconds=%.1f error=%s",
                                request_id,
                                payload.model,
                                remaining_sleep_seconds,
                                recovery_sleep_seconds,
                                selection.error_message,
                            )
                            while remaining_sleep_seconds > 0:
                                yield format_sse_event(
                                    cast(
                                        Mapping[str, Any],
                                        _account_capacity_wait_payload(
                                            None,
                                            request_id=request_id,
                                            reason=selection.error_message,
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
                            continue
                    break
                if not account:
                    if _facade()._is_local_account_cap_code(selection.error_code):
                        raise ProxyResponseError(
                            429,
                            openai_error(
                                selection.error_code or "account_stream_cap",
                                selection.error_message or "Account stream capacity is exhausted",
                                error_type="rate_limit_error",
                            ),
                        )
                    if require_preferred_account and preferred_account_id is not None:
                        message = "Previous response owner account is unavailable; retry later."
                        _record_continuity_fail_closed(
                            surface="http_stream",
                            reason="owner_account_unavailable",
                            previous_response_id=payload.previous_response_id,
                            session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                            upstream_error_code="no_accounts",
                        )
                        event = response_failed_event(
                            "previous_response_owner_unavailable",
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
                            error_code="previous_response_owner_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            transport=request_transport,
                            upstream_transport=upstream_stream_transport,
                            service_tier=payload.service_tier,
                            requested_service_tier=payload.service_tier,
                            useragent=useragent,
                            useragent_group=useragent_group,
                        )
                        return
                    # If a prior attempt stored a transient 500 and the caller
                    # expects HTTP error propagation, re-raise the original error
                    # instead of returning a generic no_accounts event.
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
                        )
                        return
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
                        )
                        return
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    error_code = selection.error_code or "no_accounts"
                    event = response_failed_event(
                        error_code,
                        no_accounts_msg,
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
                    )
                    return

                account_id_value = account.id
                if (
                    require_preferred_account
                    and preferred_account_id is not None
                    and account.id != preferred_account_id
                ):
                    message = "Previous response owner account is unavailable; retry later."
                    _record_continuity_fail_closed(
                        surface="http_stream",
                        reason="owner_account_unavailable",
                        previous_response_id=payload.previous_response_id,
                        session_id=headers.get("x-codex-turn-state") or headers.get("session_id"),
                        upstream_error_code="upstream_unavailable",
                    )
                    event = response_failed_event(
                        "previous_response_owner_unavailable",
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
                        error_code="previous_response_owner_unavailable",
                        error_message=message,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        upstream_transport=upstream_stream_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
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
                        )
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
                        )
                        event = response_failed_event(
                            "upstream_proxy_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        _facade().logger.warning(
                            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                            exc_info=True,
                        )
                        message = str(exc) or "Request to upstream timed out"
                        if (
                            not require_preferred_account
                            and preferred_account_id is None
                            and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                            and attempt + 1 < max_attempts
                        ):
                            await proxy._handle_stream_error(
                                account,
                                {"message": message},
                                "upstream_unavailable",
                            )
                            last_retryable_stream_error = _RetryableStreamError(
                                "upstream_unavailable",
                                {"message": message},
                                exclude_account=True,
                            )
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
                        )
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
                            async for line in proxy._stream_once(
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
                                useragent=useragent,
                                useragent_group=useragent_group,
                                preferred_account_id=preferred_account_id,
                                tool_call_dedupe=tool_call_dedupe,
                            ):
                                yield line
                        except (_TransientStreamError, ProxyResponseError) as tex:
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
                                    error_param = error.param if error else None
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
                                _facade().logger.warning(
                                    "Surfacing mid-stream upstream failure without replay "
                                    "request_id=%s account_id=%s code=%s",
                                    request_id,
                                    account.id,
                                    error_code,
                                )
                                yield format_sse_event(event)
                                settlement.record_success = False
                                settlement.error_code = error_code
                                settlement.error_message = error_message
                                if isinstance(tex, ProxyResponseError):
                                    settlement.error = _upstream_error_from_openai(error)
                                else:
                                    settlement.error = tex.error
                                settlement.account_health_error = _facade()._should_penalize_stream_error(error_code)
                                if settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                    )
                                settled = await proxy._settle_stream_api_key_usage(
                                    api_key,
                                    api_key_reservation,
                                    settlement,
                                    request_id,
                                )
                                return
                            if isinstance(tex, ProxyResponseError) and tex.status_code != 500:
                                error = _parse_openai_error(tex.payload)
                                code = _normalize_error_code(
                                    error.code if error else None,
                                    error.type if error else None,
                                )
                                error_message = error.message if error else None
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
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    break
                                if _facade()._is_account_neutral_error_code(code):
                                    raise
                                classified = await proxy._handle_stream_error(
                                    account,
                                    _upstream_error_from_openai(error),
                                    code,
                                    http_status=tex.status_code,
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
                                    last_transient_exc = tex
                                    await _release_tracked_stream_lease(current_account_lease)
                                    current_account_lease = None
                                    excluded_account_ids.add(account.id)
                                    break
                                raise
                            transient_retries += 1
                            error_code = tex.code if isinstance(tex, _TransientStreamError) else "server_error"
                            error_payload: UpstreamError = (
                                tex.error
                                if isinstance(tex, _TransientStreamError)
                                else _upstream_error_from_openai(_parse_openai_error(tex.payload))
                            )
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
                            await proxy._handle_stream_error(account, error_payload, error_code)
                            # Record remaining errors so total equals transient_retries,
                            # meeting the load balancer backoff threshold (error_count >= 3).
                            await proxy._load_balancer.record_errors(account, transient_retries - 1)
                            # Preserve last ProxyResponseError for propagate_http_errors path.
                            if isinstance(tex, ProxyResponseError):
                                last_transient_exc = tex
                            await _release_tracked_stream_lease(current_account_lease)
                            current_account_lease = None
                            excluded_account_ids.add(account.id)
                            break  # outer loop: select different account
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await proxy._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await proxy._load_balancer.record_success(account)
                        settled = await proxy._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
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
                    await proxy._handle_stream_error(account, exc.error, exc.code)
                    last_retryable_stream_error = exc
                    if exc.exclude_account:
                        await _release_tracked_stream_lease(current_account_lease)
                        current_account_lease = None
                        excluded_account_ids.add(account.id)
                    continue
                except _TerminalStreamError as exc:
                    if _facade()._should_penalize_stream_error(exc.code):
                        await proxy._handle_stream_error(account, exc.error, exc.code)
                    return
                except ProxyResponseError as exc:
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
                            )
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        try:
                            account = await proxy._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
                        except RefreshError as refresh_exc:
                            if refresh_exc.is_permanent:
                                await proxy._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                            continue
                        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                            _facade().logger.warning(
                                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                                exc_info=True,
                            )
                            message = str(exc) or "Request to upstream timed out"
                            if (
                                not require_preferred_account
                                and preferred_account_id is None
                                and _facade()._should_retry_transient_stream_error("upstream_unavailable", message)
                                and attempt + 1 < max_attempts
                            ):
                                await proxy._handle_stream_error(
                                    account,
                                    {"message": message},
                                    "upstream_unavailable",
                                )
                                last_retryable_stream_error = _RetryableStreamError(
                                    "upstream_unavailable",
                                    {"message": message},
                                    exclude_account=True,
                                )
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
                            )
                            yield format_sse_event(_facade()._proxy_request_timeout_event(request_id))
                            return
                        stream_timeout_tokens = _facade()._push_stream_attempt_timeout_overrides(
                            effective_attempt_timeout
                        )
                        try:
                            async for line in proxy._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                False,
                                request_started_at=start,
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                upstream_stream_transport=upstream_stream_transport,
                                request_transport=request_transport,
                                useragent=useragent,
                                useragent_group=useragent_group,
                                tool_call_dedupe=tool_call_dedupe,
                            ):
                                yield line
                        except ProxyResponseError as retry_exc:
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
                                    error_param=error.param if error else None,
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
                                if settlement.account_health_error:
                                    await proxy._handle_stream_error(
                                        account,
                                        _stream_settlement_error_payload(settlement),
                                        settlement.error_code or "upstream_error",
                                        http_status=retry_exc.status_code,
                                    )
                                settled = await proxy._settle_stream_api_key_usage(
                                    api_key,
                                    api_key_reservation,
                                    settlement,
                                    request_id,
                                )
                                return
                            error = _parse_openai_error(retry_exc.payload)
                            error_code = _normalize_error_code(
                                error.code if error else None,
                                error.type if error else None,
                            )
                            if error_code == "account_response_create_cap":
                                last_transient_exc = retry_exc
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            if _facade()._is_account_neutral_error_code(error_code):
                                raise
                            classified = await proxy._handle_stream_error(
                                account,
                                _upstream_error_from_openai(error),
                                error_code,
                                http_status=retry_exc.status_code,
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
                                last_transient_exc = retry_exc
                                await _release_tracked_stream_lease(current_account_lease)
                                current_account_lease = None
                                excluded_account_ids.add(account.id)
                                continue
                            if propagate_http_errors:
                                raise
                            error_message = error.message if error else None
                            event = response_failed_event(
                                error_code or "upstream_error",
                                error_message or "Upstream error",
                                error_type=(error.type if error else None) or "server_error",
                                response_id=request_id,
                                error_param=error.param if error else None,
                            )
                            _apply_error_metadata(event["response"]["error"], error)
                            yield format_sse_event(event)
                            return
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await proxy._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await proxy._load_balancer.record_success(account)
                        settled = await proxy._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
                        upstream_transport_metric_status = settlement.status
                        _record_upstream_transport_metric_once(settlement.status)
                        return
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    error_type = error.type if error else None
                    error_param = error.param if error else None
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
                    if _facade()._should_penalize_stream_error(error_code):
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
                    continue
                except Exception:
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
            # When HTTP error propagation is enabled and the last failure was
            # a transient 500, re-raise to preserve the upstream status/payload.
            if propagate_http_errors and last_transient_exc is not None:
                raise last_transient_exc
            if last_retryable_stream_error is not None:
                retries_exhausted_msg = str(last_retryable_stream_error.error.get("message") or "Upstream error")
                event = response_failed_event(
                    last_retryable_stream_error.code,
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
                        error_code=last_retryable_stream_error.code,
                        error_message=retries_exhausted_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        upstream_transport=upstream_stream_transport,
                        service_tier=payload.service_tier,
                        requested_service_tier=payload.service_tier,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
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
                )
        finally:
            if not upstream_transport_metric_recorded:
                _record_upstream_transport_metric_once(upstream_transport_metric_status or "error")
            for account_lease in account_leases:
                await proxy._load_balancer.release_account_lease(account_lease)
            if not settled and api_key is not None and api_key_reservation is not None:
                release_coro = proxy._release_unsettled_stream_api_key_usage(
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                )
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    proxy._schedule_cancel_safe_cleanup(
                        release_coro,
                        action="release_stream_api_key_reservation",
                        request_id=request_id,
                    )
                else:
                    await release_coro
