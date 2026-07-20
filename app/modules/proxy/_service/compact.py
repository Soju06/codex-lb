from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn, Protocol, TypeVar, cast

import aiohttp

from app.core.auth.refresh import RefreshError, is_transient_refresh_contention, refresh_contention_kind
from app.core.balancer import ResetPreferenceWindow, RoutingStrategy, failover_decision
from app.core.clients.proxy import (
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    filter_inbound_headers,
    pop_compact_timeout_overrides,
    push_compact_timeout_overrides,
)
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.core.openai.requests import ResponsesCompactRequest
from app.core.resilience.network_recovery import ProcessNetworkRecovery
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.retry import backoff_seconds
from app.db.models import Account, DashboardSettings, StickySessionKind
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.proxy._service.support import _request_log_client_fields, _RequestLogFailureMetadata
from app.modules.proxy.affinity import (
    _affinity_with_payload_continuity,
    _AffinityPolicy,
    _bare_codex_session_affinity,
    _is_synthesized_turn_state,
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _request_allows_bare_session_cap_spillover,
    _resolve_prompt_cache_key,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.continuity import resolve_required_account_id
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.load_balancer import (
    AccountConcurrencyCaps,
    AccountLease,
    AccountSelection,
    effective_account_concurrency_caps,
)
from app.modules.proxy.work_admission import AdmissionLease, WorkAdmissionController

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")

_REQUEST_TRANSPORT_HTTP = "http"
_CompactResponses = Callable[
    [ResponsesCompactRequest, Mapping[str, str], str, str | None],
    Awaitable[CompactResponsePayload],
]


def _compact_turn_state_session_identity(session_key: object | None, session: object | None) -> str | None:
    durable_session_id = getattr(session, "durable_session_id", None)
    if isinstance(durable_session_id, str) and durable_session_id.strip():
        return f"durable:{durable_session_id.strip()}"
    if session_key is None:
        return None
    return f"live:{session_key!r}"


class _CompactServiceProtocol(Protocol):
    _encryptor: Any
    _load_balancer: Any
    _repo_factory: Any
    _http_bridge_lock: Any
    _http_bridge_sessions: Any
    _http_bridge_turn_state_index: Any
    _durable_bridge: Any

    def _get_work_admission(self) -> WorkAdmissionController: ...

    def _raise_for_unsupported_input_image_references(self, payload: ResponsesCompactRequest) -> None: ...

    async def _resolve_file_account_for_responses(
        self, payload: ResponsesCompactRequest, headers: Mapping[str, str]
    ) -> str | None: ...

    async def _acquire_account_response_create_lease_or_overload(
        self, *, account_id: str, request_id: str, surface: str, concurrency_caps: AccountConcurrencyCaps
    ) -> AccountLease: ...

    async def _resolve_upstream_route_for_account(
        self, account: Account, *, operation: str
    ) -> ResolvedUpstreamRoute | None: ...

    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...

    async def _resolve_websocket_previous_response_owner(
        self,
        *,
        previous_response_id: str | None,
        api_key: ApiKeyData | None,
        session_id: str | None = None,
        surface: str,
    ) -> str | None: ...

    async def _resolve_compact_turn_state_owner(
        self,
        *,
        turn_state: str,
        api_key: ApiKeyData | None,
        fail_on_missing: bool = True,
    ) -> str | None: ...

    async def _ensure_fresh_with_budget(
        self, account: Account, *, force: bool = False, timeout_seconds: float | None = None
    ) -> Account: ...

    async def _handle_stream_error(
        self,
        account: Account,
        error: Any,
        code: str,
        http_status: int | None = None,
    ) -> Any: ...

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...

    async def _settle_compact_api_key_usage(
        self,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        response: CompactResponsePayload | None,
        request_service_tier: str | None,
    ) -> None: ...

    async def _write_request_log(self, **kwargs: Any) -> None: ...


def _service_module() -> Any:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        raise RuntimeError("app.modules.proxy.service is not loaded")
    return service_module


def _service_global(name: str) -> Any:
    return getattr(_service_module(), name)


def _service_global_or(name: str, fallback: T) -> T:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        return fallback
    return cast(T, getattr(service_module, name, fallback))


def _service_get_settings() -> Any:
    return _service_global_or("get_settings", get_settings)()


def _service_get_settings_cache() -> Any:
    return _service_global_or("get_settings_cache", get_settings_cache)()


def _service_time() -> Any:
    return _service_global_or("time", time)


def _service_core_compact_responses() -> _CompactResponses:
    return _service_global_or("core_compact_responses", core_compact_responses)


def _service_push_compact_timeout_overrides(**kwargs: float) -> object:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        func = getattr(service_module, "push_compact_timeout_overrides", push_compact_timeout_overrides)
        return cast(Callable[..., object], func)(**kwargs)
    return push_compact_timeout_overrides(**kwargs)


def _service_pop_compact_timeout_overrides(token: object) -> None:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        func = getattr(service_module, "pop_compact_timeout_overrides", pop_compact_timeout_overrides)
        cast(Callable[[object], None], func)(token)
        return
    pop_compact_timeout_overrides(cast(Any, token))


def _request_kind_from_headers(headers: Mapping[str, str]) -> str:
    raw_metadata = headers.get("x-codex-turn-metadata") or headers.get("X-Codex-Turn-Metadata")
    if not raw_metadata:
        return "normal"
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return "normal"
    if not isinstance(metadata, dict):
        return "normal"
    raw_request_kind = metadata.get("request_kind")
    if not isinstance(raw_request_kind, str):
        return "normal"
    request_kind = raw_request_kind.strip()
    if request_kind == "compaction":
        return request_kind
    return "normal"


def _remaining_budget_seconds(deadline: float) -> float:
    return cast(Callable[[float], float], _service_global("_remaining_budget_seconds"))(deadline)


def _compact_upstream_call_budget_reserve_seconds(remaining_budget: float) -> float:
    if remaining_budget <= 0:
        return 0.0
    return min(30.0, max(1.0, remaining_budget * 0.2), remaining_budget * 0.5)


def _compact_freshness_budget_seconds(remaining_budget: float) -> float:
    reserve = _compact_upstream_call_budget_reserve_seconds(remaining_budget)
    return min(20.0, max(0.0, remaining_budget - reserve))


def _compact_upstream_budget_seconds(
    remaining_budget: float,
    configured_timeout_seconds: float | None = None,
) -> float:
    if remaining_budget <= 0:
        return 0.0
    reserve = _compact_upstream_call_budget_reserve_seconds(remaining_budget)
    available = max(0.0, remaining_budget - reserve)
    if configured_timeout_seconds is not None:
        return min(configured_timeout_seconds, available)
    return available


def _raise_proxy_budget_exhausted() -> NoReturn:
    cast(Callable[[], NoReturn], _service_global("_raise_proxy_budget_exhausted"))()


def _raise_proxy_unavailable(message: str) -> NoReturn:
    cast(Callable[[str], NoReturn], _service_global("_raise_proxy_unavailable"))(message)


def _request_log_failure_metadata(exc: ProxyResponseError) -> _RequestLogFailureMetadata:
    return cast(
        Callable[[ProxyResponseError], _RequestLogFailureMetadata], _service_global("_request_log_failure_metadata")
    )(exc)


def _prefer_earlier_reset_window(settings: DashboardSettings) -> ResetPreferenceWindow:
    return cast(Callable[[DashboardSettings], ResetPreferenceWindow], _service_global("_prefer_earlier_reset_window"))(
        settings
    )


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    return cast(Callable[[DashboardSettings], RoutingStrategy], _service_global("_routing_strategy"))(settings)


def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[CompactResponsePayload]],
    *args: object,
    optional_kwargs: Mapping[str, object],
) -> Awaitable[CompactResponsePayload]:
    return cast(
        Callable[..., Awaitable[CompactResponsePayload]], _service_global("_call_with_supported_optional_kwargs")
    )(func, *args, optional_kwargs=optional_kwargs)


def _maybe_log_proxy_request_payload(
    kind: str,
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_request_payload"))(kind, payload, headers)


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    **kwargs: object,
) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_request_shape"))(kind, payload, headers, **kwargs)


def _maybe_log_proxy_service_tier_trace(
    kind: str,
    *,
    requested_service_tier: str | None,
    actual_service_tier: str | None,
) -> None:
    cast(Callable[..., None], _service_global("_maybe_log_proxy_service_tier_trace"))(
        kind,
        requested_service_tier=requested_service_tier,
        actual_service_tier=actual_service_tier,
    )


def _should_retry_transient_stream_error(code: str | None, message: str | None) -> bool:
    return cast(Callable[[str | None, str | None], bool], _service_global("_should_retry_transient_stream_error"))(
        code, message
    )


def _compact_previous_response_not_found_error(exc: ProxyResponseError) -> ProxyResponseError | None:
    return cast(
        Callable[[ProxyResponseError], ProxyResponseError | None],
        _service_global("_compact_previous_response_not_found_error"),
    )(exc)


def _proxy_response_error_code(exc: ProxyResponseError) -> str | None:
    return cast(Callable[[ProxyResponseError], str | None], _service_global("_proxy_response_error_code"))(exc)


def _record_continuity_fail_closed(
    *,
    surface: str,
    reason: str,
    previous_response_id: str | None,
    session_id: str | None,
    upstream_error_code: str | None,
) -> None:
    cast(Callable[..., None], _service_global("_record_continuity_fail_closed"))(
        surface=surface,
        reason=reason,
        previous_response_id=previous_response_id,
        session_id=session_id,
        upstream_error_code=upstream_error_code,
    )


def _is_security_work_authorization_required_error(code: str | None, message: str | None) -> bool:
    return cast(
        Callable[[str | None, str | None], bool],
        _service_global("_is_security_work_authorization_required_error"),
    )(code, message)


def _is_account_neutral_error_code(code: str | None) -> bool:
    return cast(Callable[[str | None], bool], _service_global("_is_account_neutral_error_code"))(code)


def _upstream_error_from_openai(error: Any) -> Any:
    return cast(Callable[[Any], Any], _service_global("_upstream_error_from_openai"))(error)


def _estimated_lease_tokens_from_request_usage_budget(budget: ApiKeyRequestUsageBudget | None) -> float:
    return cast(
        Callable[[ApiKeyRequestUsageBudget | None], float],
        _service_global("_estimated_lease_tokens_from_request_usage_budget"),
    )(budget)


def _service_tier_from_response(response: CompactResponsePayload | None) -> str | None:
    return cast(Callable[[CompactResponsePayload | None], str | None], _service_global("_service_tier_from_response"))(
        response
    )


def _effective_service_tier(requested_service_tier: str | None, actual_service_tier: str | None) -> str | None:
    return cast(
        Callable[[str | None, str | None], str | None],
        _service_global("_effective_service_tier"),
    )(requested_service_tier, actual_service_tier)


def _compact_same_contract_retry_budget() -> int:
    return cast(int, _service_global("_COMPACT_SAME_CONTRACT_RETRY_BUDGET"))


def _compact_max_account_attempts() -> int:
    return cast(int, _service_global("_COMPACT_MAX_ACCOUNT_ATTEMPTS"))


def _max_transient_same_account_retries() -> int:
    return cast(int, _service_global("_MAX_TRANSIENT_SAME_ACCOUNT_RETRIES"))


def _no_security_work_authorized_accounts_code() -> str:
    return cast(str, _service_global("_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE"))


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_for_compact_request(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
    api_key: ApiKeyData | None = None,
) -> _AffinityPolicy:
    cache_key, _ = _resolve_prompt_cache_key(
        payload,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
    )
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key:
        policy = _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    elif (
        session_affinity := _bare_codex_session_affinity(
            headers,
            enabled=codex_session_affinity,
            allow_cap_spillover=_request_allows_bare_session_cap_spillover(payload),
        )
    ) is not None:
        policy = session_affinity
    elif openai_cache_affinity:
        policy = _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    elif sticky_threads_enabled:
        policy = _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    else:
        policy = _AffinityPolicy()
    return _affinity_with_payload_continuity(policy, payload)


def _service_tier_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    normalize = cast(Callable[[JsonValue], str | None], _service_global("_normalize_service_tier_value"))
    return normalize(payload.service_tier)


class _CompactMixin:
    async def _resolve_compact_turn_state_owner(
        self,
        *,
        turn_state: str,
        api_key: ApiKeyData | None,
        fail_on_missing: bool = True,
    ) -> str | None:
        """Resolve a turn-state token to its API-key-scoped HTTP bridge owner.

        A compact request cannot safely fall back to generic sticky routing: an
        opaque turn-state is valid only on the account that created it.  The
        local alias index is the fast path; the durable lookup covers a request
        that arrives on another replica.  Both lookup surfaces are keyed by the
        exact API key id, so a token observed under one key cannot select an
        account for another key.

        Synthetic-shaped ``turn_*`` / ``http_turn_*`` values can be real
        registered bridge aliases on later turns.  Callers may pass
        ``fail_on_missing=False`` only for those synthetic placeholders so an
        unregistered first-turn placeholder still allows weaker routing signals
        such as file ownership to run.
        """
        proxy = cast(_CompactServiceProtocol, self)
        normalized_turn_state = turn_state.strip()
        if not normalized_turn_state:
            raise ProxyResponseError(
                502,
                openai_error(
                    "turn_state_owner_unavailable",
                    "Turn-state owner account is unavailable; retry the logical turn.",
                    error_type="server_error",
                ),
            )
        api_key_id = api_key.id if api_key is not None else None
        owner_refs: list[tuple[str, str, str | None]] = []
        async with proxy._http_bridge_lock:
            session_key = proxy._http_bridge_turn_state_index.get((normalized_turn_state, api_key_id))
            session = proxy._http_bridge_sessions.get(session_key) if session_key is not None else None
            account = getattr(session, "account", None)
            account_id = getattr(account, "id", None)
            if isinstance(account_id, str) and account_id.strip():
                owner_refs.append(
                    (
                        "turn-state live index",
                        account_id,
                        _compact_turn_state_session_identity(session_key, session),
                    )
                )

        try:
            durable_lookup = await proxy._durable_bridge.lookup_turn_state_target(
                turn_state=normalized_turn_state,
                api_key_id=api_key_id,
            )
        except Exception as exc:
            raise ProxyResponseError(
                502,
                openai_error(
                    "turn_state_owner_unavailable",
                    "Turn-state owner account is unavailable; retry the logical turn.",
                    error_type="server_error",
                ),
            ) from exc
        account_id = getattr(durable_lookup, "account_id", None)
        if isinstance(account_id, str) and account_id.strip():
            durable_session_id = getattr(durable_lookup, "session_id", None)
            owner_refs.append(
                (
                    "turn-state durable alias",
                    account_id,
                    f"durable:{durable_session_id.strip()}"
                    if isinstance(durable_session_id, str) and durable_session_id.strip()
                    else None,
                )
            )
        resolved_owner = resolve_required_account_id(
            *((source, owner_account_id) for source, owner_account_id, _session_id in owner_refs)
        )
        if resolved_owner is not None:
            session_identities = {
                session_identity
                for _source, owner_account_id, session_identity in owner_refs
                if owner_account_id == resolved_owner and session_identity is not None
            }
            if len(session_identities) > 1:
                sources = ", ".join(source for source, _account_id, _session_id in owner_refs)
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "continuity_owner_conflict",
                        f"Account-owned continuity sources conflict ({sources}); retry the logical turn.",
                        error_type="server_error",
                    ),
                )
            return resolved_owner
        if not fail_on_missing:
            return None
        raise ProxyResponseError(
            502,
            openai_error(
                "turn_state_owner_unavailable",
                "Turn-state owner account is unavailable; retry the logical turn.",
                error_type="server_error",
            ),
        )

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        client_ip: str | None = None,
    ) -> CompactResponsePayload:
        proxy = cast(_CompactServiceProtocol, self)
        _maybe_log_proxy_request_payload("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        request_kind = _request_kind_from_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = _service_time().monotonic()
        base_settings = _service_get_settings()
        deadline = start + base_settings.compact_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        failure_metadata = _RequestLogFailureMetadata()
        response: CompactResponsePayload | None = None
        request_service_tier: str | None = None
        actual_service_tier: str | None = None
        route_mode: str | None = None
        route_pool_id: str | None = None
        route_endpoint_id: str | None = None
        route_fallback_used: bool | None = None
        route_fail_closed_reason: str | None = None
        proxy._raise_for_unsupported_input_image_references(payload)
        rewritten_file_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
        settings = await _service_get_settings_cache().get()
        concurrency_caps = effective_account_concurrency_caps(settings)
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        had_prompt_cache_key = _prompt_cache_key_from_request_model(payload) is not None
        affinity = _sticky_key_for_compact_request(
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
            sticky_key_source = (
                "turn_state_header" if _sticky_key_from_turn_state_header(headers) is not None else "session_header"
            )
        elif affinity.key:
            sticky_key_source = "payload" if had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "compact",
            payload,
            headers,
            sticky_kind=affinity.kind.value if affinity.kind is not None else None,
            sticky_key_source=sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(payload) is not None,
        )
        routing_strategy = _routing_strategy(settings)
        turn_state_owner_account_id: str | None = None
        turn_state = _sticky_key_from_turn_state_header(headers)
        if turn_state is not None:
            turn_state_owner_account_id = await proxy._resolve_compact_turn_state_owner(
                turn_state=turn_state,
                api_key=api_key,
                fail_on_missing=not _is_synthesized_turn_state(turn_state),
            )
        previous_response_id = getattr(payload, "previous_response_id", None)
        previous_response_preferred_account_id: str | None = None
        previous_response_lookup_session_id: str | None = None
        if isinstance(previous_response_id, str) and previous_response_id.strip():
            previous_response_id = previous_response_id.strip()
            previous_response_lookup_session_id = _owner_lookup_session_id_from_headers(headers)
            previous_response_preferred_account_id = await proxy._resolve_websocket_previous_response_owner(
                previous_response_id=previous_response_id,
                api_key=api_key,
                session_id=previous_response_lookup_session_id,
                surface="compact",
            )
            if previous_response_preferred_account_id is None:
                selection_inputs = await proxy._load_balancer._load_selection_inputs(
                    model=payload.model,
                    additional_limit_name=None,
                    account_ids=api_key.assigned_account_ids
                    if api_key is not None and api_key.account_assignment_scope_enabled
                    else None,
                )
                if len(selection_inputs.accounts) != 1:
                    message = "Previous response owner account is unavailable; retry later."
                    _record_continuity_fail_closed(
                        surface="compact",
                        reason="owner_account_unavailable",
                        previous_response_id=previous_response_id,
                        session_id=previous_response_lookup_session_id,
                        upstream_error_code="owner_lookup_miss",
                    )
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "previous_response_owner_unavailable",
                            message,
                            error_type="server_error",
                        ),
                    )

        # File pins are account ownership, not locality. Resolved turn-state or
        # previous-response owners above still take precedence (and conflicts
        # fail closed), while process-session/prompt-cache hints never hide a
        # known file owner.
        preferred_account_id = resolve_required_account_id(
            ("turn state", turn_state_owner_account_id),
            ("previous response", previous_response_preferred_account_id),
            ("input file", rewritten_file_account_id),
        )
        try:

            async def _call_compact(
                target: Account,
                account_response_create_lease: AccountLease | None = None,
            ) -> CompactResponsePayload:
                nonlocal route_fallback_used, route_mode, route_pool_id, route_endpoint_id
                access_token = proxy._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Compact request budget exhausted before upstream call request_id=%s account_id=%s",
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                create_lease: AdmissionLease | None = None
                timeout_tokens: Any | None = None
                try:
                    if account_response_create_lease is None:
                        account_response_create_lease = await proxy._acquire_account_response_create_lease_or_overload(
                            account_id=target.id,
                            request_id=request_id,
                            surface="compact",
                            concurrency_caps=concurrency_caps,
                        )
                    create_lease = await proxy._get_work_admission().acquire_response_create(compact=True)
                    route = await proxy._resolve_upstream_route_for_account(target, operation="compact")
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Compact request budget exhausted after admission waits request_id=%s account_id=%s",
                            request_id,
                            target.id,
                        )
                        _raise_proxy_budget_exhausted()
                    upstream_budget = _compact_upstream_budget_seconds(
                        remaining_budget,
                        getattr(settings, "upstream_compact_timeout_seconds", None),
                    )
                    if upstream_budget <= 0:
                        logger.warning(
                            "Compact request budget exhausted before upstream call cap request_id=%s account_id=%s",
                            request_id,
                            target.id,
                        )
                        _raise_proxy_budget_exhausted()
                    timeout_tokens = _service_push_compact_timeout_overrides(
                        connect_timeout_seconds=upstream_budget,
                        total_timeout_seconds=upstream_budget,
                    )
                    if route is not None:
                        route_mode = route.mode
                        route_pool_id = route.pool_id
                        route_endpoint_id = route.endpoint_id
                    route_trace = UpstreamProxyRouteTrace()
                    upstream_started_at = time.monotonic()
                    try:
                        logger.info(
                            "Compact upstream call start request_id=%s account_id=%s timeout_seconds=%.2f "
                            "remaining_budget=%.2f",
                            request_id,
                            target.id,
                            upstream_budget,
                            remaining_budget,
                        )
                        response = await asyncio.wait_for(
                            _call_with_supported_optional_kwargs(
                                _service_core_compact_responses(),
                                payload,
                                filtered,
                                access_token,
                                account_id,
                                optional_kwargs={
                                    "route": route,
                                    "allow_direct_egress": route is None,
                                    "route_trace": route_trace,
                                    "chatgpt_account_id": account_id,
                                },
                            ),
                            timeout=upstream_budget,
                        )
                        logger.info(
                            "Compact upstream call complete request_id=%s account_id=%s elapsed_seconds=%.2f "
                            "timeout_seconds=%.2f",
                            request_id,
                            target.id,
                            time.monotonic() - upstream_started_at,
                            upstream_budget,
                        )
                        return response
                    except ProxyResponseError as exc:
                        error = _parse_openai_error(exc.payload)
                        code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error and error.message is not None else ""
                        if (
                            code == "upstream_unavailable"
                            and exc.retryable_same_contract
                            and (
                                exc.failure_exception_type in {"TimeoutError", "ServerTimeoutError"}
                                or "timed out" in error_message.lower()
                                or "timeout" in error_message.lower()
                            )
                        ):
                            logger.warning(
                                "Compact inner upstream timeout surfaced request_id=%s account_id=%s "
                                "elapsed_seconds=%.2f timeout_seconds=%.2f",
                                request_id,
                                target.id,
                                time.monotonic() - upstream_started_at,
                                upstream_budget,
                            )
                            raise ProxyResponseError(
                                502,
                                openai_error("upstream_request_timeout", "Compact upstream call timed out"),
                            ) from exc
                        raise
                    except asyncio.TimeoutError as exc:
                        logger.warning(
                            "Compact upstream call timed out request_id=%s account_id=%s elapsed_seconds=%.2f "
                            "timeout_seconds=%.2f",
                            request_id,
                            target.id,
                            time.monotonic() - upstream_started_at,
                            upstream_budget,
                        )
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_request_timeout", "Compact upstream call timed out"),
                        ) from exc
                    finally:
                        if route_trace.mode is not None:
                            route_mode = route_trace.mode
                            route_pool_id = route_trace.pool_id
                            route_endpoint_id = route_trace.endpoint_id
                            route_fallback_used = route_trace.fallback_used
                finally:
                    if create_lease is not None:
                        create_lease.release()
                    await proxy._load_balancer.release_account_lease(account_response_create_lease)
                    if timeout_tokens is not None:
                        _service_pop_compact_timeout_overrides(timeout_tokens)

            last_exc: ProxyResponseError | None = None
            network_recovery = ProcessNetworkRecovery(transport="compact", request_id=request_id)
            excluded_account_ids: set[str] = set()
            require_security_work_authorized = False
            estimated_lease_tokens = _estimated_lease_tokens_from_request_usage_budget(
                estimate_api_key_request_usage(payload)
            )
            for _account_attempt in range(_compact_max_account_attempts()):
                selection = await proxy._select_account_with_budget_compatible(
                    deadline,
                    request_id=request_id,
                    kind="compact",
                    api_key=api_key,
                    affinity_policy=affinity,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                    routing_strategy=routing_strategy,
                    model=payload.model,
                    service_tier=payload.service_tier,
                    exclude_account_ids=excluded_account_ids,
                    preferred_account_id=preferred_account_id,
                    require_security_work_authorized=require_security_work_authorized,
                    lease_kind="response_create",
                    estimated_lease_tokens=estimated_lease_tokens,
                    fallback_on_preferred_account_unavailable=preferred_account_id is None,
                )
                account = selection.account
                if not account:
                    if (
                        require_security_work_authorized
                        and selection.error_code == _no_security_work_authorized_accounts_code()
                        and last_exc is not None
                    ):
                        logger.info(
                            "No security-work-authorized account available for compact retry; "
                            "continuing normal account failover request_id=%s",
                            request_id,
                        )
                        require_security_work_authorized = False
                        selection = await proxy._select_account_with_budget_compatible(
                            deadline,
                            request_id=request_id,
                            kind="compact",
                            api_key=api_key,
                            affinity_policy=affinity,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                            routing_strategy=routing_strategy,
                            model=payload.model,
                            service_tier=payload.service_tier,
                            exclude_account_ids=excluded_account_ids,
                            preferred_account_id=preferred_account_id,
                            require_security_work_authorized=False,
                            lease_kind="response_create",
                            estimated_lease_tokens=estimated_lease_tokens,
                            fallback_on_preferred_account_unavailable=preferred_account_id is None,
                        )
                        account = selection.account
                    if account is not None:
                        pass
                    elif last_exc is not None:
                        break
                    else:
                        log_error_code = selection.error_code or "no_accounts"
                        log_error_message = selection.error_message or "No active accounts available"
                        status_code = 429 if log_error_code == "account_response_create_cap" else 503
                        raise ProxyResponseError(
                            status_code,
                            openai_error(
                                log_error_code,
                                log_error_message,
                                error_type="rate_limit_error" if status_code == 429 else "server_error",
                            ),
                        )
                assert account is not None
                account_id_value = account.id
                selected_account_response_create_lease = selection.lease
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning("Compact request budget exhausted before freshness check request_id=%s", request_id)
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    # This budget-exhausted terminal exits compact_responses before
                    # reaching the retry loop's settle sites, so on the HTTP bridge /
                    # forwarded path (``owns_reservation`` false, ``compact_responses``
                    # is the sole settler) the API-key reservation would leak held
                    # quota. Settle BEFORE raising, mirroring the transport/permanent
                    # preflight branches above.
                    await proxy._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    _raise_proxy_budget_exhausted()
                freshness_budget = _compact_freshness_budget_seconds(remaining_budget)
                if freshness_budget <= 0:
                    logger.warning(
                        "Compact request budget exhausted before freshness check reserve request_id=%s "
                        "remaining_budget=%.2f",
                        request_id,
                        remaining_budget,
                    )
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    # Sole-settler leak guard (see above): settle the reservation
                    # before this budget-exhausted terminal raise.
                    await proxy._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    _raise_proxy_budget_exhausted()
                try:
                    logger.info(
                        "Compact freshness start request_id=%s account_id=%s timeout_seconds=%.2f "
                        "remaining_budget=%.2f",
                        request_id,
                        account.id,
                        freshness_budget,
                        remaining_budget,
                    )
                    account = await proxy._ensure_fresh_with_budget(account, timeout_seconds=freshness_budget)
                    logger.info(
                        "Compact freshness complete request_id=%s account_id=%s",
                        request_id,
                        account.id,
                    )
                except ProxyResponseError:
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    selected_account_response_create_lease = None
                    # ensure_fresh_with_budget translates terminal process-network
                    # recovery outcomes before the compact upstream settlement
                    # branches run, so this boundary owns reservation cleanup.
                    await proxy._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    raise
                except (RefreshError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    selected_account_response_create_lease = None
                    if isinstance(exc, RefreshError):
                        if exc.is_permanent:
                            # Permanent refresh failures keep their prior
                            # escalation (they propagate to the caller). On the
                            # HTTP bridge / forwarded path the caller passes an
                            # ``api_key_reservation_override`` with
                            # ``owns_reservation`` false, so ``compact_responses``
                            # is the sole settler; settle BEFORE raising so the
                            # reservation is finalized instead of leaking held
                            # API-key quota (matching the post-401 permanent
                            # branch, which settles before re-raising).
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            raise
                        if is_transient_refresh_contention(exc):
                            # Transient CROSS-REPLICA refresh contention: benign
                            # claim contention (``refresh_claim_timeout``: the
                            # account's refresh claim is held by another replica) OR
                            # a post-exchange persist/status CAS conflict
                            # (``token_persist_conflict`` / ``status_downgrade_conflict``).
                            # This is NOT a genuine ``transport_error`` OAuth failure
                            # — the account's credentials are healthy — so it fails
                            # over WITHOUT an account-health penalty. Unlike the
                            # genuine transport-level failure handled below, do NOT
                            # record ``_handle_stream_error`` here: that would push an
                            # otherwise-healthy account into backoff for normal
                            # cross-replica contention. Surface a retryable
                            # ``upstream_unavailable`` on exhaustion (``last_exc``).
                            message = exc.message or str(exc) or "Request to upstream timed out"
                            if refresh_contention_kind(exc) == "persist_conflict":
                                logger.warning(
                                    "Compact refresh post-exchange persist conflict code=%s "
                                    "request_id=%s account_id=%s",
                                    exc.code,
                                    request_id,
                                    account.id,
                                    exc_info=True,
                                )
                            else:
                                logger.warning(
                                    "Compact refresh claim contention request_id=%s account_id=%s",
                                    request_id,
                                    account.id,
                                    exc_info=True,
                                )
                            if preferred_account_id is not None:
                                # File/previous-response-pinned requests cannot
                                # fail over. On the HTTP bridge / forwarded path
                                # the caller passes an ``api_key_reservation_override``
                                # with ``owns_reservation`` false, making
                                # ``compact_responses`` responsible for settling the
                                # reservation. Settle it BEFORE raising so the
                                # API-key reservation is finalized instead of leaking
                                # held quota when the pinned refresh claim times out.
                                await proxy._settle_compact_api_key_usage(
                                    api_key=api_key,
                                    api_key_reservation=api_key_reservation,
                                    response=None,
                                    request_service_tier=request_service_tier,
                                )
                                _raise_proxy_unavailable(message)
                            last_exc = ProxyResponseError(502, openai_error("upstream_unavailable", message))
                            excluded_account_ids.add(account.id)
                            continue
                        # A GENUINE OAuth transport failure (``code == "transport_error"``:
                        # the refresh request itself timed out / its upstream
                        # connection failed). This IS the account/route's fault, so
                        # it falls through to the shared transport-failure handling
                        # below — identical to a raw aiohttp/connect failure — which
                        # records the account-health penalty (``_handle_stream_error``)
                        # so a persistently broken account backs off instead of
                        # being kept healthy and reselected on the next request.
                    message = getattr(exc, "message", None) or str(exc) or "Request to upstream timed out"
                    logger.warning(
                        "Compact refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    # Both terminal (non-failover) transport-failure raises below
                    # exit compact_responses without reaching the retry loop's
                    # settle sites, so on the HTTP bridge / forwarded path
                    # (owns_reservation false, compact_responses is the sole
                    # settler) the API-key reservation would leak held quota.
                    # Settle BEFORE raising, mirroring the claim-contention and
                    # post-401 transport branches.
                    if not _should_retry_transient_stream_error("upstream_unavailable", message):
                        await proxy._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=None,
                            request_service_tier=request_service_tier,
                        )
                        _raise_proxy_unavailable(message)
                    if preferred_account_id is not None:
                        await proxy._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=None,
                            request_service_tier=request_service_tier,
                        )
                        _raise_proxy_unavailable(message)
                    await proxy._handle_stream_error(
                        account,
                        {"message": message},
                        "upstream_unavailable",
                    )
                    last_exc = ProxyResponseError(502, openai_error("upstream_unavailable", message))
                    excluded_account_ids.add(account.id)
                    continue
                except BaseException:
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    selected_account_response_create_lease = None
                    raise
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Compact request budget exhausted after freshness check request_id=%s account_id=%s",
                        request_id,
                        account.id,
                    )
                    await proxy._load_balancer.release_account_lease(selected_account_response_create_lease)
                    # Sole-settler leak guard (see above): settle the reservation
                    # before this budget-exhausted terminal raise.
                    await proxy._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    _raise_proxy_budget_exhausted()
                request_service_tier = _service_tier_from_compact_payload(payload)

                safe_retry_budget = _compact_same_contract_retry_budget()
                transient_retries = 0
                refresh_retry_used = False
                transient_exhausted = False
                while True:
                    try:
                        account_response_create_lease = selected_account_response_create_lease
                        selected_account_response_create_lease = None
                        response = await _call_compact(account, account_response_create_lease)
                        network_recovery.log_recovered()
                        actual_service_tier = _service_tier_from_response(response)
                        await proxy._load_balancer.record_success(account)
                        await proxy._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=response,
                            request_service_tier=request_service_tier,
                        )
                        log_status = "success"
                        return response
                    except ProxyResponseError as exc:
                        compact_continuity_error = _compact_previous_response_not_found_error(exc)
                        if compact_continuity_error is not None:
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            _record_continuity_fail_closed(
                                surface="compact",
                                reason="previous_response_not_found",
                                previous_response_id=None,
                                session_id=_owner_lookup_session_id_from_headers(headers),
                                upstream_error_code=_proxy_response_error_code(exc),
                            )
                            raise compact_continuity_error from exc
                        if exc.status_code == 401:
                            if refresh_retry_used:
                                try:
                                    await proxy._handle_proxy_error(account, exc)
                                except Exception:
                                    await proxy._settle_compact_api_key_usage(
                                        api_key=api_key,
                                        api_key_reservation=api_key_reservation,
                                        response=None,
                                        request_service_tier=request_service_tier,
                                    )
                                    raise
                                last_exc = exc
                                excluded_account_ids.add(account.id)
                                transient_exhausted = True
                                break
                            try:
                                remaining_budget = _remaining_budget_seconds(deadline)
                                if remaining_budget <= 0:
                                    logger.warning(
                                        "Compact request budget exhausted before forced refresh retry request_id=%s "
                                        "account_id=%s",
                                        request_id,
                                        account.id,
                                    )
                                    # Sole-settler leak guard (see above): this
                                    # budget-exhausted terminal exits the retry loop
                                    # to the outer handler without settling, so on
                                    # the bridge/forwarded path (``owns_reservation``
                                    # false) the reservation would leak held quota.
                                    # Settle BEFORE raising.
                                    await proxy._settle_compact_api_key_usage(
                                        api_key=api_key,
                                        api_key_reservation=api_key_reservation,
                                        response=None,
                                        request_service_tier=request_service_tier,
                                    )
                                    _raise_proxy_budget_exhausted()
                                account = await proxy._ensure_fresh_with_budget(
                                    account,
                                    force=True,
                                    timeout_seconds=_compact_freshness_budget_seconds(remaining_budget),
                                )
                            except ProxyResponseError:
                                # A translated refresh-recovery error escapes the
                                # current upstream-error handler, so settle before
                                # handing it to the request-level error boundary.
                                await proxy._settle_compact_api_key_usage(
                                    api_key=api_key,
                                    api_key_reservation=api_key_reservation,
                                    response=None,
                                    request_service_tier=request_service_tier,
                                )
                                raise
                            except (RefreshError, aiohttp.ClientError, asyncio.TimeoutError) as refresh_exc:
                                if isinstance(refresh_exc, RefreshError):
                                    if refresh_exc.is_permanent:
                                        await proxy._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                                        await proxy._settle_compact_api_key_usage(
                                            api_key=api_key,
                                            api_key_reservation=api_key_reservation,
                                            response=None,
                                            request_service_tier=request_service_tier,
                                        )
                                        raise exc
                                    if is_transient_refresh_contention(refresh_exc):
                                        # Transient CROSS-REPLICA refresh contention
                                        # on the post-401 forced refresh: benign
                                        # claim contention (``refresh_claim_timeout``)
                                        # OR a post-exchange persist/status CAS
                                        # conflict (``token_persist_conflict`` /
                                        # ``status_downgrade_conflict``). This is NOT
                                        # a genuine ``transport_error`` OAuth failure
                                        # — the account's credentials are healthy — so
                                        # fail over WITHOUT an account-health penalty
                                        # and surface a retryable
                                        # ``upstream_unavailable`` on exhaustion
                                        # instead of the misleading original 401. Do
                                        # NOT record ``_handle_stream_error`` here
                                        # (that is reserved for the genuine transport
                                        # failure handled below).
                                        message = (
                                            refresh_exc.message or str(refresh_exc) or "Request to upstream timed out"
                                        )
                                        if refresh_contention_kind(refresh_exc) == "persist_conflict":
                                            logger.warning(
                                                "Compact forced refresh post-exchange persist conflict code=%s "
                                                "request_id=%s account_id=%s",
                                                refresh_exc.code,
                                                request_id,
                                                account.id,
                                                exc_info=True,
                                            )
                                        else:
                                            logger.warning(
                                                "Compact forced refresh claim contention request_id=%s account_id=%s",
                                                request_id,
                                                account.id,
                                                exc_info=True,
                                            )
                                        if preferred_account_id is not None:
                                            await proxy._settle_compact_api_key_usage(
                                                api_key=api_key,
                                                api_key_reservation=api_key_reservation,
                                                response=None,
                                                request_service_tier=request_service_tier,
                                            )
                                            _raise_proxy_unavailable(message)
                                        last_exc = ProxyResponseError(
                                            502, openai_error("upstream_unavailable", message)
                                        )
                                        excluded_account_ids.add(account.id)
                                        transient_exhausted = True
                                        break
                                    if not refresh_exc.transport_error:
                                        # Non-transport, non-permanent RefreshError
                                        # keeps its prior escalation: re-raise the
                                        # original 401 to the caller.
                                        await proxy._settle_compact_api_key_usage(
                                            api_key=api_key,
                                            api_key_reservation=api_key_reservation,
                                            response=None,
                                            request_service_tier=request_service_tier,
                                        )
                                        raise exc
                                    # A GENUINE OAuth transport failure
                                    # (``code == "transport_error"``): the account/
                                    # route is at fault, so it falls through to the
                                    # shared transport-failure handling below —
                                    # identical to a raw aiohttp/connect failure —
                                    # which records the account-health penalty
                                    # (``_handle_stream_error``) so the broken
                                    # account backs off instead of being reselected.
                                message = getattr(refresh_exc, "message", None) or str(refresh_exc)
                                message = message or "Request to upstream timed out"
                                logger.warning(
                                    "Compact forced refresh/connect failed request_id=%s account_id=%s",
                                    request_id,
                                    account.id,
                                    exc_info=True,
                                )
                                if not _should_retry_transient_stream_error("upstream_unavailable", message):
                                    await proxy._settle_compact_api_key_usage(
                                        api_key=api_key,
                                        api_key_reservation=api_key_reservation,
                                        response=None,
                                        request_service_tier=request_service_tier,
                                    )
                                    _raise_proxy_unavailable(message)
                                if preferred_account_id is not None:
                                    await proxy._settle_compact_api_key_usage(
                                        api_key=api_key,
                                        api_key_reservation=api_key_reservation,
                                        response=None,
                                        request_service_tier=request_service_tier,
                                    )
                                    _raise_proxy_unavailable(message)
                                await proxy._handle_stream_error(
                                    account,
                                    {"message": message},
                                    "upstream_unavailable",
                                )
                                last_exc = ProxyResponseError(502, openai_error("upstream_unavailable", message))
                                excluded_account_ids.add(account.id)
                                transient_exhausted = True
                                break
                            refresh_retry_used = True
                            continue
                        if exc.status_code == 500:
                            transient_retries += 1
                            if (
                                transient_retries < _max_transient_same_account_retries()
                                and _remaining_budget_seconds(deadline) > 0
                            ):
                                delay = backoff_seconds(transient_retries)
                                logger.info(
                                    "Transient compact error, retrying same account "
                                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs",
                                    request_id,
                                    account.id,
                                    transient_retries,
                                    _max_transient_same_account_retries(),
                                    delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                            # Exhausted same-account transient retries — penalize and failover
                            logger.warning(
                                "Compact transient retries exhausted for account "
                                "request_id=%s account_id=%s retries=%s code=server_error",
                                request_id,
                                account.id,
                                transient_retries,
                            )
                            await proxy._handle_proxy_error(account, exc)
                            # Record remaining errors so total equals transient_retries,
                            # meeting the load balancer backoff threshold (error_count >= 3).
                            await proxy._load_balancer.record_errors(account, transient_retries - 1)
                            last_exc = exc
                            excluded_account_ids.add(account.id)
                            transient_exhausted = True
                            break  # break inner loop → outer loop tries different account
                        error = _parse_openai_error(exc.payload)
                        code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error else None
                        network_recovery.account_id = account.id
                        recovery_decision = await network_recovery.wait(
                            error_code=code,
                            retryable_same_contract=exc.retryable_same_contract,
                            deadline=deadline,
                            rotate_shared_client=True,
                            failed_session=exc.failed_session,
                        )
                        if recovery_decision == "retry":
                            continue
                        if recovery_decision == "exhausted":
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            _raise_proxy_budget_exhausted()
                        if exc.retryable_same_contract and safe_retry_budget > 0:
                            safe_retry_budget -= 1
                            continue
                        if _is_security_work_authorization_required_error(code, error_message):
                            if (
                                not account.security_work_authorized
                                and account.id != preferred_account_id
                                and _account_attempt < _compact_max_account_attempts() - 1
                            ):
                                last_exc = exc
                                excluded_account_ids.add(account.id)
                                require_security_work_authorized = True
                                transient_exhausted = True
                                break
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            raise
                        if code == "account_response_create_cap":
                            last_exc = exc
                            excluded_account_ids.add(account.id)
                            transient_exhausted = True
                            break
                        if _is_account_neutral_error_code(code):
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            raise
                        if code == "upstream_request_timeout":
                            await proxy._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            classified = await proxy._handle_stream_error(
                                account,
                                _upstream_error_from_openai(error),
                                code,
                                http_status=exc.status_code,
                            )
                            if (
                                affinity.selection_key is not None
                                and affinity.kind is not None
                                and affinity.kind != StickySessionKind.CODEX_SESSION
                            ):
                                # A timeout cannot prove that durable Codex
                                # ownership is invalid. Preserve both raw hard
                                # turn-state rows and namespaced process locality;
                                # runtime health already drives safe fallback.
                                try:
                                    async with proxy._repo_factory() as repos:
                                        await repos.sticky_sessions.delete(
                                            affinity.selection_key,
                                            kind=affinity.kind,
                                        )
                                    logger.info(
                                        "Compact sticky mapping cleared after upstream timeout request_id=%s "
                                        "sticky_kind=%s",
                                        request_id,
                                        affinity.kind.value,
                                    )
                                except Exception:
                                    logger.warning(
                                        "Failed to clear compact sticky mapping after upstream timeout "
                                        "request_id=%s sticky_kind=%s",
                                        request_id,
                                        affinity.kind.value,
                                        exc_info=True,
                                    )
                            logger.info(
                                "Failover decision request_id=%s transport=compact account_id=%s "
                                "attempt=%d failure_class=%s action=surface",
                                request_id,
                                account.id,
                                _account_attempt + 1,
                                classified["failure_class"],
                            )
                            raise
                        classified = await proxy._handle_stream_error(
                            account,
                            _upstream_error_from_openai(error),
                            code,
                            http_status=exc.status_code,
                        )
                        if getattr(base_settings, "deterministic_failover_enabled", True):
                            action = failover_decision(
                                failure_class=classified["failure_class"],
                                downstream_visible=False,
                                candidates_remaining=_compact_max_account_attempts() - _account_attempt - 1,
                            )
                        else:
                            action = "surface"
                        logger.info(
                            "Failover decision request_id=%s transport=compact account_id=%s "
                            "attempt=%d failure_class=%s action=%s",
                            request_id,
                            account.id,
                            _account_attempt + 1,
                            classified["failure_class"],
                            action,
                        )
                        if action == "failover_next":
                            last_exc = exc
                            excluded_account_ids.add(account.id)
                            transient_exhausted = True
                            break
                        await proxy._settle_compact_api_key_usage(
                            api_key=api_key,
                            api_key_reservation=api_key_reservation,
                            response=None,
                            request_service_tier=request_service_tier,
                        )
                        raise
                if transient_exhausted:
                    continue  # outer loop: try different account
            # All account attempts exhausted — raise last error
            await proxy._settle_compact_api_key_usage(
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                response=None,
                request_service_tier=request_service_tier,
            )
            if last_exc is not None:
                raise last_exc
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "All account attempts exhausted"),
            )
        except ProxyResponseError as exc:
            failure_metadata = _request_log_failure_metadata(exc)
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        except UpstreamProxyRouteError as exc:
            route_fail_closed_reason = exc.reason
            log_error_code = "upstream_proxy_unavailable"
            log_error_message = exc.reason
            await proxy._settle_compact_api_key_usage(
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                response=None,
                request_service_tier=request_service_tier,
            )
            raise ProxyResponseError(
                502,
                openai_error("upstream_proxy_unavailable", f"Upstream proxy route unavailable: {exc.reason}"),
            ) from exc
        finally:
            usage = response.usage if response else None
            reasoning_effort = payload.reasoning.effort if payload.reasoning else None
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                latency_ms=int((_service_time().monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=(
                    usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
                ),
                reasoning_tokens=(
                    usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
                ),
                reasoning_effort=reasoning_effort,
                transport=_REQUEST_TRANSPORT_HTTP,
                service_tier=_effective_service_tier(request_service_tier, actual_service_tier),
                requested_service_tier=request_service_tier,
                actual_service_tier=actual_service_tier,
                failure_phase=failure_metadata.failure_phase,
                failure_detail=failure_metadata.failure_detail,
                failure_exception_type=failure_metadata.failure_exception_type,
                upstream_status_code=failure_metadata.upstream_status_code,
                upstream_error_code=failure_metadata.upstream_error_code,
                bridge_stage=failure_metadata.bridge_stage,
                upstream_proxy_route_mode=route_mode,
                upstream_proxy_pool_id=route_pool_id,
                upstream_proxy_endpoint_id=route_endpoint_id,
                upstream_proxy_fallback_used=route_fallback_used if route_endpoint_id else None,
                upstream_proxy_fail_closed_reason=route_fail_closed_reason,
                useragent=useragent,
                useragent_group=useragent_group,
                conversation_id=conversation_id,
                client_ip=client_ip,
                request_kind=request_kind,
            )
            _maybe_log_proxy_service_tier_trace(
                "compact",
                requested_service_tier=request_service_tier,
                actual_service_tier=actual_service_tier,
            )
