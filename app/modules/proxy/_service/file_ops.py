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
from app.core.clients.files import FileProxyError, pop_files_timeout_overrides, push_files_timeout_overrides
from app.core.clients.files import create_file as core_create_file
from app.core.clients.files import finalize_file as core_finalize_file
from app.core.clients.proxy import ProxyResponseError, UpstreamProxyRouteTrace, filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
    extract_input_file_ids,
    extract_input_image_file_references,
)
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.db.models import Account
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.support import (
    _request_log_client_fields,
    _RequestLogFailureMetadata,
)
from app.modules.proxy.continuity import resolve_required_account_id
from app.modules.proxy.file_pin_repository import (
    FileAccountPinOwnershipConflict,
    FileAccountPinRepository,
)
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.selection_errors import selection_failure_response

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_ResponsesPayloadT = ResponsesRequest | ResponsesCompactRequest


class _FileOpsServiceProtocol(Protocol):
    _encryptor: Any
    _file_pin_session_factory: Any
    _load_balancer: Any
    _FILE_ACCOUNT_PIN_TTL_SECONDS: int

    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...
    async def _select_account_with_budget(self, deadline: float, **kwargs: Any) -> AccountSelection: ...
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
    async def _proxy_files_call(self, **kwargs: Any) -> tuple[dict[str, JsonValue], str | None]: ...
    async def _pin_file_account(self, file_id: str, account_id: str) -> None: ...
    async def _resolve_file_account(self, file_id: str) -> str | None: ...
    async def _resolve_file_account_for_responses(
        self,
        payload: ResponsesRequest | ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> str | None: ...


def _service_core_create_file() -> Callable[..., Awaitable[dict[str, JsonValue]]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[dict[str, JsonValue]]],
            getattr(service_module, "core_create_file", core_create_file),
        )
    return core_create_file


def _service_core_finalize_file() -> Callable[..., Awaitable[dict[str, JsonValue]]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[dict[str, JsonValue]]],
            getattr(service_module, "core_finalize_file", core_finalize_file),
        )
    return core_finalize_file


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


class _FileOwnerPostSuccessError(RuntimeError):
    def __init__(self, proxy_error: ProxyResponseError) -> None:
        super().__init__("File owner persistence failed after a successful upstream call")
        self.proxy_error = proxy_error


def _file_owner_unavailable_error() -> ProxyResponseError:
    return ProxyResponseError(
        502,
        openai_error(
            "file_owner_unavailable",
            "Input file owner metadata is unavailable; upload the file again and retry.",
            error_type="server_error",
        ),
    )


class _FileOpsMixin:
    # File-account pin TTL: long enough to cover a slow client-side
    # PUT of a 512 MiB upload (the upstream limit) plus the finalize
    # poll loop and a follow-up ``/responses`` that references the
    # file_id, while still bounding how long stale pins remain in
    # shared storage. 30 minutes covers a 512 MiB
    # upload at ~280 KiB/s -- well below typical broadband uplink.
    # The database clock defines both expiry and opportunistic cleanup.
    _FILE_ACCOUNT_PIN_TTL_SECONDS: int = 30 * 60

    async def _pin_file_account(
        self,
        file_id: str,
        account_id: str,
    ) -> None:
        """Remember that ``file_id`` was registered through ``account_id``.

        Used so a subsequent ``finalize_file`` can be routed to the same
        account that created the file, including when another replica
        handles the follow-up request.
        """
        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id or not account_id:
            return
        try:
            async with proxy._file_pin_session_factory() as session:
                await FileAccountPinRepository(session).claim(
                    file_id,
                    account_id,
                    ttl_seconds=proxy._FILE_ACCOUNT_PIN_TTL_SECONDS,
                )
        except FileAccountPinOwnershipConflict as exc:
            raise ProxyResponseError(
                502,
                openai_error(
                    "continuity_owner_conflict",
                    "File ownership conflicts with an existing live upload.",
                    error_type="server_error",
                ),
            ) from exc
        except Exception as exc:
            raise _file_owner_unavailable_error() from exc

    async def _resolve_file_account(self, file_id: str) -> str | None:
        """Return the pinned account_id for ``file_id`` if still live."""
        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id:
            return None
        try:
            async with proxy._file_pin_session_factory() as session:
                return await FileAccountPinRepository(session).get_live_account_id(file_id)
        except Exception as exc:
            raise _file_owner_unavailable_error() from exc

    async def _resolve_file_account_for_responses(
        self,
        payload: ResponsesRequest | ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> str | None:
        """Resolve a ``preferred_account_id`` from durable ``input_file.file_id`` pins."""
        proxy = cast(_FileOpsServiceProtocol, self)
        del headers

        input_value = payload.input
        if isinstance(payload, ResponsesCompactRequest):
            input_value = payload.to_payload().get("input")
        file_ids = extract_input_file_ids(input_value)
        if not file_ids:
            return None

        try:
            async with proxy._file_pin_session_factory() as session:
                account_ids_by_file_id = await FileAccountPinRepository(session).get_live_account_ids(file_ids)
        except Exception as exc:
            raise _file_owner_unavailable_error() from exc
        resolved_account_ids = [account_ids_by_file_id.get(file_id) for file_id in file_ids]

        pinned_account_ids = [account_id for account_id in resolved_account_ids if account_id is not None]
        if not pinned_account_ids:
            return None
        if len(pinned_account_ids) != len(resolved_account_ids):
            raise ProxyResponseError(
                502,
                openai_error(
                    "file_owner_unavailable",
                    "Input file owner metadata is unavailable; upload the file again and retry.",
                    error_type="server_error",
                ),
            )
        owner_account_ids = set(pinned_account_ids)
        if len(owner_account_ids) != 1:
            raise ProxyResponseError(
                502,
                openai_error(
                    "continuity_owner_conflict",
                    "Input files resolve to conflicting upstream accounts; retry with files from one account.",
                    error_type="server_error",
                ),
            )
        return next(iter(owner_account_ids))

    async def _resolve_forwarded_file_account_for_responses(
        self,
        payload: ResponsesRequest | ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        forwarded_file_owner_account_id: str | None,
        require_forwarded_file_owner: bool = False,
    ) -> str | None:
        """Revalidate signed bridge ownership against the shared database."""
        proxy = cast(_FileOpsServiceProtocol, self)
        durable_owner_account_id = await proxy._resolve_file_account_for_responses(payload, headers)
        if (
            require_forwarded_file_owner
            and durable_owner_account_id is not None
            and forwarded_file_owner_account_id is None
        ):
            raise _file_owner_unavailable_error()
        if forwarded_file_owner_account_id is not None and durable_owner_account_id is None:
            raise _file_owner_unavailable_error()
        return resolve_required_account_id(
            ("signed forwarding context", forwarded_file_owner_account_id),
            ("durable file pin", durable_owner_account_id),
        )

    def _raise_for_unsupported_input_image_references(self, payload: _ResponsesPayloadT) -> None:
        references = extract_input_image_file_references(payload.input)
        if not references:
            return
        raise ProxyResponseError(
            400,
            openai_error(
                "unsupported_input_image_format",
                (
                    "input_image references via file_id or sediment:// URIs are not supported on "
                    "/v1/responses; the upstream API only accepts inline data: URLs. Send the "
                    "image inline (codex-cli style) or use the upload protocol exclusively for "
                    "MCP tool arguments."
                ),
            ),
        )

    async def create_file(
        self,
        payload: Mapping[str, JsonValue],
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        """Forward an inbound `POST /backend-api/files` registration to upstream.

        The body is whatever the caller sent (already validated as
        ``FileCreateRequest`` at the API edge). Returns the upstream
        ``{file_id, upload_url, ...}`` JSON verbatim. Mirrors the
        account-selection / refresh / 401-retry pattern from ``transcribe``.

        On success we record a ``file_id -> account_id`` pin so a
        subsequent ``finalize_file`` for the same ``file_id`` is routed
        to the same account; the upstream contract is account-scoped
        (chatgpt-account-id) so a finalize on a different account would
        fail with not-found / unauthorized.
        """
        proxy = cast(_FileOpsServiceProtocol, self)

        async def persist_file_owner(result: dict[str, JsonValue], account_id: str) -> None:
            file_id = result.get("file_id")
            if isinstance(file_id, str) and file_id:
                await proxy._pin_file_account(file_id, account_id)

        result, _account_id = await proxy._proxy_files_call(
            log_model="files-create",
            kind="files-create",
            api_key=api_key,
            headers=headers,
            invoke=lambda access_token, upstream_account_id, filtered_headers, route, route_trace: (
                _service_core_create_file()(
                    payload=payload,
                    headers=filtered_headers,
                    access_token=access_token,
                    account_id=upstream_account_id,
                    route=route,
                    allow_direct_egress=route is None,
                    route_trace=route_trace,
                )
            ),
            on_success=persist_file_owner,
        )
        return result

    async def finalize_file(
        self,
        file_id: str,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        """Forward an inbound `POST /backend-api/files/{file_id}/uploaded` finalize call.

        The upstream client (Codex CLI) polls this endpoint while
        ``status == "retry"``; ``_service_core_finalize_file()`` mirrors that loop
        server-side with a 30 s budget. Returns the upstream JSON
        verbatim.

        Routes to the account that handled the matching ``create_file``
        (via the durable pin table) so the upstream finalize call
        carries the same ``chatgpt-account-id`` that registered the
        file. Falls back to a fresh load-balancer selection when no
        pin is found (unknown ``file_id`` or an expired pin).
        """
        proxy = cast(_FileOpsServiceProtocol, self)

        async def resolve_file_owner() -> str | None:
            return await proxy._resolve_file_account(file_id)

        async def persist_file_owner(result: dict[str, JsonValue], account_id: str) -> None:
            if result.get("status") == "success":
                await proxy._pin_file_account(file_id, account_id)

        result, _account_id = await proxy._proxy_files_call(
            log_model="files-finalize",
            kind="files-finalize",
            api_key=api_key,
            headers=headers,
            resolve_preferred_account_id=resolve_file_owner,
            invoke=lambda access_token, upstream_account_id, filtered_headers, route, route_trace: (
                _service_core_finalize_file()(
                    file_id=file_id,
                    headers=filtered_headers,
                    access_token=access_token,
                    account_id=upstream_account_id,
                    route=route,
                    allow_direct_egress=route is None,
                    route_trace=route_trace,
                )
            ),
            on_success=persist_file_owner,
        )
        return result

    async def _proxy_files_call(
        self,
        *,
        log_model: str,
        kind: str,
        api_key: ApiKeyData | None,
        headers: Mapping[str, str],
        invoke: Callable[
            [str, str | None, Mapping[str, str], ResolvedUpstreamRoute | None, UpstreamProxyRouteTrace],
            Awaitable[dict[str, JsonValue]],
        ],
        preferred_account_id: str | None = None,
        resolve_preferred_account_id: Callable[[], Awaitable[str | None]] | None = None,
        on_success: Callable[[dict[str, JsonValue], str], Awaitable[None]] | None = None,
    ) -> tuple[dict[str, JsonValue], str | None]:
        """Shared account-selection / refresh / 401-retry plumbing for `/files` calls.

        Mirrors the structure of ``transcribe``: pick an account with budget,
        ensure freshness, invoke upstream, on 401 force-refresh and retry once,
        translate ``FileProxyError`` -> ``ProxyResponseError``, and always
        write a request-log entry on the way out. When
        ``preferred_account_id`` is provided or resolved (e.g. from the file_id
        pin for ``finalize_file``), the call is strict to that account and
        fails closed when the owner account is unavailable. ``on_success`` runs
        before the request is logged or returned so durable owner persistence
        remains part of the route's success contract.
        """
        proxy = cast(_FileOpsServiceProtocol, self)
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

        try:
            if resolve_preferred_account_id is not None:
                preferred_account_id = await resolve_preferred_account_id()
            settings = await _service_get_settings_cache().get()
            prefer_earlier_reset = settings.prefer_earlier_reset_accounts
            routing_strategy = _routing_strategy(settings)

            async def _persist_success(result: dict[str, JsonValue], account_id: str) -> None:
                if on_success is None:
                    return
                try:
                    await on_success(result, account_id)
                except ProxyResponseError as exc:
                    raise _FileOwnerPostSuccessError(exc) from exc
                except Exception as exc:
                    raise _FileOwnerPostSuccessError(_file_owner_unavailable_error()) from exc

            selection = await proxy._select_account_with_budget_compatible(
                deadline,
                request_id=request_id,
                kind=kind,
                api_key=api_key,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=None,
                preferred_account_id=preferred_account_id,
                fallback_on_preferred_account_unavailable=preferred_account_id is None,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                status_code, error_payload = selection_failure_response(selection)
                raise ProxyResponseError(status_code, error_payload)
            account_id_value = account.id

            async def _call(target: Account) -> dict[str, JsonValue]:
                nonlocal route_mode, route_pool_id, route_endpoint_id, route_fallback_used
                access_token = proxy._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                route = await proxy._resolve_upstream_route_for_account(target, operation=kind)
                route_trace = UpstreamProxyRouteTrace()
                if route is not None:
                    route_mode = route.mode
                    route_pool_id = route.pool_id
                    route_endpoint_id = route.endpoint_id
                    route_fallback_used = False
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "%s request budget exhausted before upstream call request_id=%s account_id=%s",
                        kind,
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                # Propagate the per-request budget so file create/finalize
                # calls inherit the same effective timeout as the rest of
                # the request, instead of letting them block on the
                # module-default 60 s timeout regardless of how much
                # budget is left.
                timeout_tokens = push_files_timeout_overrides(
                    connect_timeout_seconds=remaining_budget,
                    total_timeout_seconds=remaining_budget,
                )
                try:
                    return await invoke(access_token, account_id, filtered, route, route_trace)
                except FileProxyError as files_exc:
                    raise ProxyResponseError(
                        files_exc.status_code,
                        files_exc.payload,
                        failure_phase=files_exc.failure_phase,
                    ) from files_exc
                finally:
                    if route_trace.mode is not None:
                        route_mode = route_trace.mode
                        route_pool_id = route_trace.pool_id
                        route_endpoint_id = route_trace.endpoint_id
                        route_fallback_used = route_trace.fallback_used
                    pop_files_timeout_overrides(timeout_tokens)

            async def _select_files_failover(excluded_account_ids: set[str]) -> AccountSelection:
                return await proxy._select_account_with_budget(
                    deadline,
                    request_id=request_id,
                    kind=kind,
                    api_key=api_key,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    routing_strategy=routing_strategy,
                    model=None,
                    preferred_account_id=preferred_account_id,
                    exclude_account_ids=excluded_account_ids,
                )

            try:
                account = await proxy._ensure_previsible_unary_fresh_with_failover(
                    account,
                    deadline=deadline,
                    request_id=request_id,
                    kind=kind,
                    select_next_account=_select_files_failover,
                    strict_account_id=preferred_account_id,
                )
                account_id_value = account.id
                result = await _call(account)
                await proxy._load_balancer.record_success(account)
                await _persist_success(result, account.id)
                log_status = "success"
                return result, account_id_value
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
                        select_next_account=_select_files_failover,
                        call_next=_call,
                        strict_account_id=preferred_account_id,
                    )
                    if failover is not None:
                        account, result = failover
                        account_id_value = account.id
                        await _persist_success(result, account.id)
                        log_status = "success"
                        return result, account_id_value
                    failed_account = _proxy_response_failed_account(exc, account)
                    account_id_value = failed_account.id
                    await proxy._handle_proxy_error(failed_account, exc)
                    raise
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "%s request budget exhausted before forced refresh retry request_id=%s account_id=%s",
                            kind,
                            request_id,
                            account.id,
                        )
                        _raise_proxy_budget_exhausted()
                    try:
                        account = await proxy._ensure_previsible_unary_fresh_with_failover(
                            account,
                            deadline=deadline,
                            request_id=request_id,
                            kind=kind,
                            select_next_account=_select_files_failover,
                            strict_account_id=preferred_account_id,
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
                        "%s forced refresh/connect failed request_id=%s account_id=%s",
                        kind,
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                try:
                    result = await _call(account)
                    # The forced-refresh retry can swap to a refreshed
                    # account row -- re-pin to that account id so the
                    # caller's pin is consistent with the upstream call.
                    account_id_value = account.id
                    await proxy._load_balancer.record_success(account)
                    await _persist_success(result, account.id)
                    log_status = "success"
                    return result, account_id_value
                except ProxyResponseError as retry_exc:
                    await proxy._handle_proxy_error(account, retry_exc)
                    if retry_exc.status_code == 401:
                        selection = await proxy._select_account_with_budget(
                            deadline,
                            request_id=request_id,
                            kind=kind,
                            api_key=api_key,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            routing_strategy=routing_strategy,
                            model=None,
                            preferred_account_id=preferred_account_id,
                            fallback_on_preferred_account_unavailable=preferred_account_id is None,
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
                                result = await _call(account)
                                await proxy._load_balancer.record_success(account)
                                await _persist_success(result, account.id)
                                log_status = "success"
                                return result, account_id_value
                            except ProxyResponseError as failover_exc:
                                await proxy._handle_proxy_error(account, failover_exc)
                                raise
                    raise
        except _FileOwnerPostSuccessError as exc:
            proxy_error = exc.proxy_error
            failure_metadata = _request_log_failure_metadata(proxy_error)
            error = _parse_openai_error(proxy_error.payload)
            log_error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = error.message if error else None
            raise proxy_error from exc
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
                model=log_model,
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
