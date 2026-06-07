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
    _FilePinEntry,
    _request_log_useragent_fields,
    _RequestLogFailureMetadata,
)
from app.modules.proxy.affinity import (
    _is_synthesized_turn_state,
    _prompt_cache_key_from_request_model,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.load_balancer import AccountSelection

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_ResponsesPayloadT = ResponsesRequest | ResponsesCompactRequest


class _FileOpsServiceProtocol(Protocol):
    _encryptor: Any
    _file_account_pin_lock: asyncio.Lock
    _file_account_pins: dict[str, _FilePinEntry]
    _load_balancer: Any
    _FILE_ACCOUNT_PIN_TTL_SECONDS: float

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
    async def _lookup_file_pin(self, file_id: str) -> _FilePinEntry | None: ...
    def _evict_expired_file_pins_locked(self) -> None: ...


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


class _FileOpsMixin:
    # File-account pin TTL: long enough to cover a slow client-side
    # PUT of a 512 MiB upload (the upstream limit) plus the finalize
    # poll loop and a follow-up ``/responses`` that references the
    # file_id, while still bounding how long stale pins can sit in
    # memory on long-lived workers. 30 minutes covers a 512 MiB
    # upload at ~280 KiB/s -- well below typical broadband uplink --
    # while keeping the table size negligible (each pin is a short
    # string tuple). Eviction runs opportunistically on every write,
    # so this acts as an upper bound, not a fixed retention.
    _FILE_ACCOUNT_PIN_TTL_SECONDS: float = 30 * 60.0

    async def _pin_file_account(
        self,
        file_id: str,
        account_id: str,
    ) -> None:
        """Remember that ``file_id`` was registered through ``account_id``.

        Used so a subsequent ``finalize_file`` can be routed to the same
        account that created the file. Cross-instance handoff is
        best-effort: if the finalize lands on a different replica with
        no pin, we fall back to a fresh load-balancer selection.
        """
        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id or not account_id:
            return
        expires_at = time.monotonic() + proxy._FILE_ACCOUNT_PIN_TTL_SECONDS
        async with proxy._file_account_pin_lock:
            proxy._file_account_pins[file_id] = _FilePinEntry(
                account_id=account_id,
                expires_at=expires_at,
            )
            proxy._evict_expired_file_pins_locked()

    async def _resolve_file_account(self, file_id: str) -> str | None:
        """Return the pinned account_id for ``file_id`` if still live."""
        proxy = cast(_FileOpsServiceProtocol, self)
        entry = await proxy._lookup_file_pin(file_id)
        return entry.account_id if entry is not None else None

    async def _lookup_file_pin(self, file_id: str) -> _FilePinEntry | None:
        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id:
            return None
        async with proxy._file_account_pin_lock:
            proxy._evict_expired_file_pins_locked()
            entry = proxy._file_account_pins.get(file_id)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                proxy._file_account_pins.pop(file_id, None)
                return None
            return entry

    def _evict_expired_file_pins_locked(self) -> None:
        """Drop pins past their TTL. Called under ``_file_account_pin_lock``."""
        proxy = cast(_FileOpsServiceProtocol, self)
        now = time.monotonic()
        expired = [file_id for file_id, entry in proxy._file_account_pins.items() if entry.expires_at <= now]
        for file_id in expired:
            proxy._file_account_pins.pop(file_id, None)

    async def _resolve_file_account_for_responses(
        self,
        payload: ResponsesRequest | ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> str | None:
        """Resolve a ``preferred_account_id`` from ``input_file.file_id`` pins.

        Looks up the in-memory ``file_id -> account_id`` pin table built
        by ``create_file``. Used by ``/responses`` flows so a request
        carrying an ``{type: "input_file", file_id: "file_xxx"}`` part
        is routed to the same upstream account that registered the
        upload (the upstream contract is account-scoped via
        ``chatgpt-account-id``).

        The pin is only consulted when the request has *no* stronger
        client-supplied affinity signal: a ``prompt_cache_key`` that
        the client itself sent, a session / turn-state header
        (codex_session affinity), or a ``previous_response_id`` all
        imply an existing conversation continuation and must keep
        their routing intact. Returning ``None`` from here means
        "fall back to the standard sticky / codex / cache affinity
        path".

        Note: ``_sticky_key_for_responses_request`` can *derive* and
        write a ``prompt_cache_key`` onto the payload when openai cache
        affinity is enabled. We must not treat that derived key as a
        stronger signal -- it is itself the load balancer's choice to
        route consistently, not a client-supplied continuation marker.
        Inspect ``model_fields_set`` so we only honor an *explicit*
        client-supplied cache key.

        Tie-breaking when the payload references multiple ``file_id``s:
        prefer the most-recently-pinned one (matches the most recent
        upload in a multi-attachment thread). If two pins share the
        same expiry timestamp, the lexicographically smallest
        ``file_id`` wins for determinism.
        """
        proxy = cast(_FileOpsServiceProtocol, self)
        # Stronger affinity signals always win, but only when the
        # client supplied them. Derived ``prompt_cache_key`` values
        # added by the affinity helper itself must not block file-pin
        # routing for first-turn upload-then-converse flows.
        # Honor both the canonical ``prompt_cache_key`` and the
        # OpenAI-compat camelCase ``promptCacheKey`` alias as
        # client-supplied. Pydantic populates ``model_fields_set`` with
        # the canonical name when V1 normalization runs ahead of us, but
        # raw clients posting directly to ``/backend-api/codex/responses``
        # bypass that normalization and we still want to respect their
        # explicit cache key.
        explicit_fields = getattr(payload, "model_fields_set", set())
        explicit_cache_key = "prompt_cache_key" in explicit_fields or "promptCacheKey" in explicit_fields
        if explicit_cache_key and _prompt_cache_key_from_request_model(payload) is not None:
            return None
        # ``ensure_downstream_turn_state`` / ``ensure_http_downstream_turn_state``
        # synthesize a fresh ``x-codex-turn-state`` header on first turns when
        # the client did not supply one (see
        # ``app/modules/proxy/api.py`` websocket / HTTP handlers). Treat those
        # synthetic values as "no client-supplied turn state" so the file-pin
        # lookup still runs on first-turn upload-then-converse flows. Only a
        # turn-state value that does *not* match the synthesizer prefix counts
        # as a client-supplied continuation marker.
        turn_state_value = _sticky_key_from_turn_state_header(headers)
        if turn_state_value is not None and not _is_synthesized_turn_state(turn_state_value):
            return None
        if _sticky_key_from_session_header(headers) is not None:
            return None
        if getattr(payload, "previous_response_id", None):
            return None

        file_ids = extract_input_file_ids(payload.input)
        if not file_ids:
            return None

        async with proxy._file_account_pin_lock:
            proxy._evict_expired_file_pins_locked()
            best_account: str | None = None
            best_expires_at = -1.0
            best_file_id: str | None = None
            for file_id in file_ids:
                entry = proxy._file_account_pins.get(file_id)
                if entry is None:
                    continue
                if entry.expires_at > best_expires_at or (
                    entry.expires_at == best_expires_at and (best_file_id is None or file_id < best_file_id)
                ):
                    best_account = entry.account_id
                    best_expires_at = entry.expires_at
                    best_file_id = file_id
            return best_account

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
        result, account_id = await proxy._proxy_files_call(
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
        )
        # Best-effort pin so finalize lands on the same account.
        if isinstance(result, dict) and account_id:
            file_id = result.get("file_id")
            if isinstance(file_id, str) and file_id:
                await proxy._pin_file_account(file_id, account_id)
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
        (via the in-memory pin table) so the upstream finalize call
        carries the same ``chatgpt-account-id`` that registered the
        file. Falls back to a fresh load-balancer selection when no
        pin is found (unknown ``file_id`` or pin expired / missed across
        a replica boundary).
        """
        proxy = cast(_FileOpsServiceProtocol, self)
        pinned_account_id = await proxy._resolve_file_account(file_id)
        result, account_id = await proxy._proxy_files_call(
            log_model="files-finalize",
            kind="files-finalize",
            api_key=api_key,
            headers=headers,
            preferred_account_id=pinned_account_id,
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
        )
        if isinstance(result, dict) and account_id:
            status = result.get("status")
            if status == "success":
                await proxy._pin_file_account(file_id, account_id)
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
    ) -> tuple[dict[str, JsonValue], str | None]:
        """Shared account-selection / refresh / 401-retry plumbing for `/files` calls.

        Mirrors the structure of ``transcribe``: pick an account with budget,
        ensure freshness, invoke upstream, on 401 force-refresh and retry once,
        translate ``FileProxyError`` -> ``ProxyResponseError``, and always
        write a request-log entry on the way out. When
        ``preferred_account_id`` is provided (e.g. from the file_id pin
        for ``finalize_file``), the call is strict to that account and
        fails closed when the owner account is unavailable.
        """
        proxy = cast(_FileOpsServiceProtocol, self)
        filtered = filter_inbound_headers(headers)
        useragent, useragent_group = _request_log_useragent_fields(headers)
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

        settings = await _service_get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = _routing_strategy(settings)
        try:
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
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
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
                                log_status = "success"
                                return result, account_id_value
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
            )
