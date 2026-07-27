from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn, Protocol, TypeVar, cast

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.balancer import ResetPreferenceWindow, RoutingStrategy
from app.core.clients.proxy import (
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    filter_inbound_headers,
    pop_transcribe_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.db.models import Account
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.support import _request_log_client_fields, _RequestLogFailureMetadata
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.load_balancer import AccountSelection

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")


class _TranscribeServiceProtocol(Protocol):
    _encryptor: Any
    _load_balancer: Any

    async def _select_account_with_budget(self, deadline: float, **kwargs: Any) -> AccountSelection: ...
    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...
    async def _ensure_previsible_unary_fresh_with_failover(self, account: Account, **kwargs: Any) -> Account: ...
    async def _retry_previsible_unary_call_failover(
        self, exc: ProxyResponseError, account: Account, **kwargs: Any
    ) -> tuple[Account, dict[str, JsonValue]] | None: ...
    async def _ensure_fresh_with_budget_or_auth_error(self, account: Account, *, timeout_seconds: float) -> Account: ...
    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...
    async def _write_request_log(self, **kwargs: Any) -> None: ...
    async def _resolve_upstream_route_for_account(
        self, account: Account, *, operation: str
    ) -> ResolvedUpstreamRoute | None: ...


def _service_core_transcribe_audio() -> Callable[..., Awaitable[dict[str, JsonValue]]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[dict[str, JsonValue]]],
            getattr(service_module, "core_transcribe_audio", core_transcribe_audio),
        )
    return core_transcribe_audio


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


def _service_push_transcribe_timeout_overrides(**kwargs: float) -> object:
    return _service_global_or("push_transcribe_timeout_overrides", push_transcribe_timeout_overrides)(**kwargs)


def _service_pop_transcribe_timeout_overrides(token: object) -> None:
    _service_global_or("pop_transcribe_timeout_overrides", pop_transcribe_timeout_overrides)(cast(Any, token))


def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    return cast(Callable[..., Awaitable[Any]], _service_global("_call_with_supported_optional_kwargs"))(
        func,
        *args,
        optional_kwargs=optional_kwargs,
        **required_kwargs,
    )


def _service_global(name: str) -> Any:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        raise RuntimeError("app.modules.proxy.service is not loaded")
    return getattr(service_module, name)


def _remaining_budget_seconds(deadline: float) -> float:
    return cast(Callable[[float], float], _service_global("_remaining_budget_seconds"))(deadline)


def _raise_proxy_budget_exhausted() -> NoReturn:
    cast(Callable[[], NoReturn], _service_global("_raise_proxy_budget_exhausted"))()


def _raise_proxy_unavailable(message: str) -> NoReturn:
    cast(Callable[[str], NoReturn], _service_global("_raise_proxy_unavailable"))(message)


def _request_log_failure_metadata(exc: ProxyResponseError) -> _RequestLogFailureMetadata:
    return cast(
        Callable[[ProxyResponseError], _RequestLogFailureMetadata], _service_global("_request_log_failure_metadata")
    )(exc)


def _proxy_response_failed_account(exc: ProxyResponseError, fallback: Account) -> Account:
    return cast(Callable[[ProxyResponseError, Account], Account], _service_global("_proxy_response_failed_account"))(
        exc, fallback
    )


def _refresh_error_failed_account(exc: RefreshError, fallback: Account) -> Account:
    return cast(Callable[[RefreshError, Account], Account], _service_global("_refresh_error_failed_account"))(
        exc, fallback
    )


def _prefer_earlier_reset_window(settings: Any) -> ResetPreferenceWindow:
    return cast(Callable[[Any], ResetPreferenceWindow], _service_global("_prefer_earlier_reset_window"))(settings)


def _routing_strategy(settings: Any) -> RoutingStrategy:
    return cast(Callable[[Any], RoutingStrategy], _service_global("_routing_strategy"))(settings)


_FAILED_ACCOUNT_ATTR = "_codex_lb_failed_account"
_REQUEST_TRANSPORT_HTTP = "http"


class _TranscribeMixin:
    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers: Mapping[str, str],
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        proxy = cast(_TranscribeServiceProtocol, self)
        filtered = filter_inbound_headers(headers)
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = _service_time().monotonic()
        base_settings = _service_get_settings()
        deadline = start + base_settings.transcription_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        failure_metadata = _RequestLogFailureMetadata()
        route_mode: str | None = None
        route_pool_id: str | None = None
        route_endpoint_id: str | None = None
        route_fallback_used: bool | None = None
        route_fail_closed_reason: str | None = None
        transcribe_model = "gpt-4o-transcribe"

        settings = await _service_get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = _routing_strategy(settings)
        try:
            selection = await proxy._select_account_with_budget(
                deadline,
                request_id=request_id,
                kind="transcribe",
                api_key=api_key,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                routing_strategy=routing_strategy,
                model=None,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
            account_id_value = account.id

            async def _call_transcribe(target: Account) -> dict[str, JsonValue]:
                nonlocal route_mode, route_pool_id, route_endpoint_id, route_fallback_used
                access_token = proxy._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Transcription request budget exhausted before upstream call request_id=%s account_id=%s",
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                route = await proxy._resolve_upstream_route_for_account(target, operation="transcribe")
                if route is not None:
                    route_mode = route.mode
                    route_pool_id = route.pool_id
                    route_endpoint_id = route.endpoint_id
                    route_fallback_used = False
                route_trace = UpstreamProxyRouteTrace()
                timeout_tokens = _service_push_transcribe_timeout_overrides(
                    connect_timeout_seconds=remaining_budget,
                    total_timeout_seconds=remaining_budget,
                )
                try:
                    return await _call_with_supported_optional_kwargs(
                        _service_core_transcribe_audio(),
                        audio_bytes,
                        optional_kwargs={
                            "route": route,
                            "allow_direct_egress": route is None,
                            "route_trace": route_trace,
                        },
                        filename=filename,
                        content_type=content_type,
                        prompt=prompt,
                        headers=filtered,
                        access_token=access_token,
                        account_id=account_id,
                    )
                finally:
                    if route_trace.mode is not None:
                        route_mode = route_trace.mode
                        route_pool_id = route_trace.pool_id
                        route_endpoint_id = route_trace.endpoint_id
                        route_fallback_used = route_trace.fallback_used
                    _service_pop_transcribe_timeout_overrides(timeout_tokens)

            async def _select_transcribe_failover(excluded_account_ids: set[str]) -> AccountSelection:
                return await proxy._select_account_with_budget(
                    deadline,
                    request_id=request_id,
                    kind="transcribe",
                    api_key=api_key,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    routing_strategy=routing_strategy,
                    model=None,
                    exclude_account_ids=excluded_account_ids,
                )

            try:
                account = await proxy._ensure_previsible_unary_fresh_with_failover(
                    account,
                    deadline=deadline,
                    request_id=request_id,
                    kind="transcribe",
                    select_next_account=_select_transcribe_failover,
                )
                account_id_value = account.id
                result = await _call_transcribe(account)
                await proxy._load_balancer.record_success(account)
                log_status = "success"
                return result
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    failed_account = _refresh_error_failed_account(refresh_exc, account)
                    account_id_value = failed_account.id
                    await proxy._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                raise ProxyResponseError(
                    401,
                    openai_error(
                        "invalid_api_key",
                        refresh_exc.message,
                        error_type="invalid_request_error",
                    ),
                ) from refresh_exc
            except ProxyResponseError as exc:
                if exc.status_code != 401:
                    failover = await proxy._retry_previsible_unary_call_failover(
                        exc,
                        account,
                        deadline=deadline,
                        select_next_account=_select_transcribe_failover,
                        call_next=_call_transcribe,
                    )
                    if failover is not None:
                        account, result = failover
                        account_id_value = account.id
                        log_status = "success"
                        return result
                    failed_account = _proxy_response_failed_account(exc, account)
                    account_id_value = failed_account.id
                    await proxy._handle_proxy_error(failed_account, exc)
                    raise
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Transcription request budget exhausted before forced refresh retry "
                            "request_id=%s account_id=%s",
                            request_id,
                            account.id,
                        )
                        _raise_proxy_budget_exhausted()
                    try:
                        account = await proxy._ensure_previsible_unary_fresh_with_failover(
                            account,
                            deadline=deadline,
                            request_id=request_id,
                            kind="transcribe",
                            select_next_account=_select_transcribe_failover,
                            force=True,
                        )
                    except ProxyResponseError as refresh_failover_exc:
                        failed_account = _proxy_response_failed_account(refresh_failover_exc, account)
                        account_id_value = failed_account.id
                        await proxy._handle_proxy_error(failed_account, refresh_failover_exc)
                        raise
                    account_id_value = account.id
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        failed_account = _refresh_error_failed_account(refresh_exc, account)
                        account_id_value = failed_account.id
                        await proxy._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                    logger.warning(
                        "Transcription forced refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                try:
                    result = await _call_transcribe(account)
                    await proxy._load_balancer.record_success(account)
                    log_status = "success"
                    return result
                except ProxyResponseError as retry_exc:
                    await proxy._handle_proxy_error(account, retry_exc)
                    if retry_exc.status_code == 401:
                        selection = await proxy._select_account_with_budget_compatible(
                            deadline,
                            request_id=request_id,
                            kind="transcribe",
                            api_key=api_key,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                            routing_strategy=routing_strategy,
                            model=None,
                            exclude_account_ids={account.id},
                        )
                        if selection.account is not None:
                            account = selection.account
                            account_id_value = account.id
                            account = await proxy._ensure_fresh_with_budget_or_auth_error(
                                account,
                                timeout_seconds=_remaining_budget_seconds(deadline),
                            )
                            try:
                                result = await _call_transcribe(account)
                                await proxy._load_balancer.record_success(account)
                                log_status = "success"
                                return result
                            except ProxyResponseError as failover_exc:
                                await proxy._handle_proxy_error(account, failover_exc)
                                raise
                    raise
        except ProxyResponseError as exc:
            failed_account = getattr(exc, _FAILED_ACCOUNT_ATTR, None)
            if isinstance(failed_account, Account):
                account_id_value = failed_account.id
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
            raise ProxyResponseError(
                502,
                openai_error("upstream_proxy_unavailable", f"Upstream proxy route unavailable: {exc.reason}"),
            ) from exc
        finally:
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=transcribe_model,
                latency_ms=int((_service_time().monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                transport=_REQUEST_TRANSPORT_HTTP,
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
            )
