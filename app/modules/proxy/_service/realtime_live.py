from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Protocol, cast
from urllib.parse import urlparse

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.core.clients.proxy import ProxyResponseError, apply_codex_installation_headers
from app.core.clients.proxy_websocket import (
    RealtimeWebSocketProtocol,
    UpstreamWebSocket,
    UpstreamWebSocketMessage,
    UpstreamWebSocketTransportError,
    normalize_realtime_call_id,
)
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import detach_session_objects
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.support import _request_log_client_fields
from app.modules.proxy.helpers import _header_account_id
from app.modules.proxy.load_balancer import AccountLease, AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.sticky_repository import RESERVED_STICKY_SESSION_KEY_PREFIX

logger = logging.getLogger("app.modules.proxy.service")

_REALTIME_CALL_AFFINITY_PREFIX = RESERVED_STICKY_SESSION_KEY_PREFIX
_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS = 2 * 60 * 60
_REALTIME_CALL_CLEANUP_INTERVAL_SECONDS = 5 * 60
_REALTIME_CALL_CLEANUP_BATCH_SIZE = 250
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_UPSTREAM_CLOSE_CANCEL_DRAIN_MAX_SECONDS = 0.05
_UNAVAILABLE_LIVE_OWNER_STATUSES = frozenset(
    {
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.PAUSED,
        AccountStatus.DEACTIVATED,
    }
)

_realtime_call_cleanup_lock = asyncio.Lock()
_realtime_call_cleanup_last_monotonic = 0.0


class _AccessTokenDecryptor(Protocol):
    def decrypt(self, encrypted: bytes) -> str: ...


class _AccountLeaseReleaser(Protocol):
    async def release_account_lease(self, lease: AccountLease | None) -> None: ...


class LiveWebSocketConnector(Protocol):
    async def __call__(
        self,
        call_id: str,
        headers: dict[str, str],
        access_token: str,
        account_id: str | None,
        *,
        protocol: RealtimeWebSocketProtocol,
        route: ResolvedUpstreamRoute | None,
        allow_direct_egress: bool,
        query_params: list[tuple[str, str]],
        subprotocols: Sequence[str],
    ) -> UpstreamWebSocket: ...


class _RealtimeLiveServiceProtocol(Protocol):
    _encryptor: _AccessTokenDecryptor
    _load_balancer: _AccountLeaseReleaser
    _repo_factory: ProxyRepoFactory
    _live_websocket_connector: LiveWebSocketConnector

    async def _select_account_with_budget_compatible(
        self,
        deadline: float,
        *,
        request_id: str,
        kind: str,
        api_key: ApiKeyData,
        model: str | None,
        preferred_account_id: str,
        preferred_account_is_continuity_owner: bool,
        fallback_on_preferred_account_unavailable: bool,
        lease_kind: str,
        request_stage: str,
        redact_sensitive_details: bool,
    ) -> AccountSelection: ...

    async def _resolve_upstream_route_for_account(
        self,
        account: Account,
        *,
        operation: str,
    ) -> ResolvedUpstreamRoute | None: ...

    async def _write_request_log(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData,
        request_id: str,
        model: str | None,
        latency_ms: int,
        status: str,
        request_kind: str,
        error_code: str | None,
        error_message: str | None,
        transport: str,
        useragent: str | None,
        useragent_group: str | None,
        client_ip: str | None,
        conversation_id: str | None,
        upstream_proxy_route_mode: str | None,
        upstream_proxy_pool_id: str | None,
        upstream_proxy_endpoint_id: str | None,
        upstream_proxy_fallback_used: bool | None,
        upstream_proxy_fail_closed_reason: str | None,
    ) -> None: ...


def realtime_call_id_from_location(headers: Mapping[str, str]) -> str | None:
    location = next((value for key, value in headers.items() if key.lower() == "location"), None)
    if not location:
        return None

    # Match the pinned first-party decoder: everything from the first query
    # delimiter onward is ignored before path inspection. A bare fragment or
    # semicolon parameter remains part of the path input and fails closed.
    location_path = location.split("?", maxsplit=1)[0]
    parsed = urlparse(location_path)
    root_relative = not parsed.scheme and not parsed.netloc and location_path == parsed.path
    absolute_http = parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    if parsed.params or parsed.query or parsed.fragment or not (root_relative or absolute_http):
        return None

    path_segments = parsed.path.split("/")
    if len(path_segments) != 5 or path_segments[:4] != ["", "v1", "realtime", "calls"]:
        return None
    return normalize_realtime_call_id(path_segments[4])


def realtime_call_affinity_key(call_id: str, api_key: ApiKeyData) -> str:
    normalized = normalize_realtime_call_id(call_id)
    if normalized is None:
        raise ValueError("Invalid realtime call id")
    digest = hashlib.sha256(f"{api_key.id}\0{normalized}".encode()).hexdigest()
    return f"{_REALTIME_CALL_AFFINITY_PREFIX}{digest}"


def _valid_close_code(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    if 1000 <= value <= 1014 and value not in {1004, 1005, 1006}:
        return value
    if 3000 <= value <= 4999:
        return value
    return default


def _bounded_close_reason(value: object) -> str:
    if not isinstance(value, str):
        return ""
    encoded = value.encode("utf-8")[:123]
    return encoded.decode("utf-8", errors="ignore")


async def _safe_close_downstream(websocket: WebSocket, *, code: int, reason: str = "") -> None:
    if websocket.application_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.close(code=code, reason=_bounded_close_reason(reason))
    except (RuntimeError, WebSocketDisconnect):
        return


class _CloseOnceLiveWebSocket:
    def __init__(self, wrapped: UpstreamWebSocket) -> None:
        self._wrapped = wrapped
        self._close_task: asyncio.Task[None] | None = None
        self._close_wait_exhausted = False

    async def send_text(self, text: str) -> None:
        await self._wrapped.send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        await self._wrapped.send_bytes(data)

    async def receive(self) -> UpstreamWebSocketMessage:
        return await self._wrapped.receive()

    async def close(
        self,
        code: int = 1000,
        reason: str = "",
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._wrapped.close(code=code, reason=reason),
                name="realtime-live-close-upstream",
            )
            self._close_task.add_done_callback(_consume_close_task_result)
        if self._close_wait_exhausted:
            return
        if self._close_task.cancelled():
            return
        if timeout_seconds is None:
            await asyncio.shield(self._close_task)
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._close_task), timeout=timeout_seconds)
        except (TimeoutError, asyncio.CancelledError):
            self._close_wait_exhausted = True
            self._close_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._close_task),
                    timeout=_UPSTREAM_CLOSE_CANCEL_DRAIN_MAX_SECONDS,
                )
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
            raise

    def response_header(self, name: str) -> str | None:
        return self._wrapped.response_header(name)

    def archive_received(self, message: UpstreamWebSocketMessage) -> None:
        archive_received = getattr(self._wrapped, "archive_received", None)
        if callable(archive_received):
            archive_received(message)


def _consume_close_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except BaseException:
        # The owning close call reports its bounded failure. A cancellation-
        # resistant transport cleanup may finish only after that owner has
        # returned, so consume its terminal result without logging details.
        return


async def _relay_downstream_to_upstream(
    websocket: WebSocket,
    upstream: _CloseOnceLiveWebSocket,
    *,
    max_message_bytes: int,
    close_timeout_seconds: float,
) -> None:
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            await upstream.close(
                code=_valid_close_code(message.get("code"), default=1000),
                reason=_bounded_close_reason(message.get("reason")),
                timeout_seconds=close_timeout_seconds,
            )
            return
        text = message.get("text")
        if isinstance(text, str):
            if len(text.encode("utf-8")) > max_message_bytes:
                await upstream.close(code=1009, timeout_seconds=close_timeout_seconds)
                await _safe_close_downstream(websocket, code=1009)
                return
            await upstream.send_text(text)
            continue
        data = message.get("bytes")
        if isinstance(data, bytes):
            if len(data) > max_message_bytes:
                await upstream.close(code=1009, timeout_seconds=close_timeout_seconds)
                await _safe_close_downstream(websocket, code=1009)
                return
            await upstream.send_bytes(data)
            continue
        raise UpstreamWebSocketTransportError(
            "Unsupported downstream websocket frame",
            error_code="upstream_unavailable",
        )


async def _relay_upstream_to_downstream(
    websocket: WebSocket,
    upstream: _CloseOnceLiveWebSocket,
) -> None:
    while True:
        message = await upstream.receive()
        archive_received = getattr(upstream, "archive_received", None)
        if callable(archive_received):
            archive_received(message)
        if message.kind == "text" and message.text is not None:
            await websocket.send_text(message.text)
            continue
        if message.kind == "binary" and message.data is not None:
            await websocket.send_bytes(message.data)
            continue
        if message.kind == "close":
            await _safe_close_downstream(
                websocket,
                code=_valid_close_code(message.close_code, default=1000),
                reason=message.close_reason or "",
            )
            return
        if message.kind == "error":
            raise UpstreamWebSocketTransportError(
                message.error or "Upstream websocket error",
                error_code=message.error_code or "upstream_unavailable",
            )
        raise UpstreamWebSocketTransportError(
            f"Unexpected upstream websocket message kind: {message.kind}",
            error_code="upstream_unavailable",
        )


async def _relay_live_websocket(
    websocket: WebSocket,
    upstream: _CloseOnceLiveWebSocket,
    *,
    max_message_bytes: int,
    close_timeout_seconds: float,
) -> None:
    tasks = {
        asyncio.create_task(
            _relay_downstream_to_upstream(
                websocket,
                upstream,
                max_message_bytes=max_message_bytes,
                close_timeout_seconds=close_timeout_seconds,
            ),
            name="realtime-live-downstream-to-upstream",
        ),
        asyncio.create_task(
            _relay_upstream_to_downstream(websocket, upstream),
            name="realtime-live-upstream-to-downstream",
        ),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _maybe_purge_realtime_call_affinity(proxy: _RealtimeLiveServiceProtocol) -> None:
    """Throttle cleanup and delete at most one bounded batch per process."""

    global _realtime_call_cleanup_last_monotonic
    now = time.monotonic()
    if now - _realtime_call_cleanup_last_monotonic < _REALTIME_CALL_CLEANUP_INTERVAL_SECONDS:
        return
    async with _realtime_call_cleanup_lock:
        now = time.monotonic()
        if now - _realtime_call_cleanup_last_monotonic < _REALTIME_CALL_CLEANUP_INTERVAL_SECONDS:
            return
        _realtime_call_cleanup_last_monotonic = now
        cutoff = utcnow() - timedelta(seconds=_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS)
        try:
            async with proxy._repo_factory() as repos:
                await repos.sticky_sessions.purge_before_for_key_prefix(
                    cutoff,
                    kind=StickySessionKind.CODEX_SESSION,
                    key_prefix=_REALTIME_CALL_AFFINITY_PREFIX,
                    limit=_REALTIME_CALL_CLEANUP_BATCH_SIZE,
                )
        except Exception:
            logger.warning("Failed to purge expired realtime call affinity rows")


class _RealtimeLiveMixin:
    async def bind_realtime_call_owner(
        self,
        *,
        response_headers: Mapping[str, str],
        account_id: str,
        api_key: ApiKeyData,
    ) -> str | None:
        call_id = realtime_call_id_from_location(response_headers)
        if call_id is None:
            logger.warning("Realtime call response lacked a valid Location call id")
            return None

        proxy = cast(_RealtimeLiveServiceProtocol, self)
        affinity_key = realtime_call_affinity_key(call_id, api_key)
        async with proxy._repo_factory() as repos:
            persisted_owner_id = await repos.sticky_sessions.get_account_id(
                affinity_key,
                kind=StickySessionKind.CODEX_SESSION,
                max_age_seconds=_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS,
            )
            if persisted_owner_id is None:
                persisted_owner_id = await repos.sticky_sessions.insert_if_absent(
                    affinity_key,
                    account_id,
                    kind=StickySessionKind.CODEX_SESSION,
                )
        if persisted_owner_id != account_id:
            logger.error("Realtime call ownership conflict rejected")
            raise RuntimeError("Realtime call is already bound to another account")
        await _maybe_purge_realtime_call_affinity(proxy)
        return call_id

    async def _resolve_realtime_call_owner(
        self,
        call_id: str,
        *,
        api_key: ApiKeyData,
    ) -> str | None:
        proxy = cast(_RealtimeLiveServiceProtocol, self)
        affinity_key = realtime_call_affinity_key(call_id, api_key)
        async with proxy._repo_factory() as repos:
            return await repos.sticky_sessions.get_account_id(
                affinity_key,
                kind=StickySessionKind.CODEX_SESSION,
                max_age_seconds=_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS,
            )

    async def proxy_realtime_live_websocket(
        self,
        websocket: WebSocket,
        call_id: str,
        headers: Mapping[str, str],
        query_params: Mapping[str, str] | Sequence[tuple[str, str]] = (),
        *,
        protocol: RealtimeWebSocketProtocol,
        api_key: ApiKeyData,
        client_ip: str | None = None,
    ) -> None:
        normalized_call_id = normalize_realtime_call_id(call_id)
        if normalized_call_id is None:
            raise ProxyResponseError(
                400,
                openai_error("invalid_realtime_call_id", "Invalid realtime call id"),
            )

        proxy = cast(_RealtimeLiveServiceProtocol, self)
        owner_account_id = await self._resolve_realtime_call_owner(normalized_call_id, api_key=api_key)
        if owner_account_id is None or (
            api_key.account_assignment_scope_enabled and owner_account_id not in api_key.assigned_account_ids
        ):
            raise ProxyResponseError(
                404,
                openai_error("realtime_call_not_found", "Realtime call binding not found or expired"),
            )

        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        settings = get_settings()
        upstream_close_timeout_seconds = max(1.0, settings.upstream_connect_timeout_seconds)
        offered_subprotocols = tuple(cast(Sequence[str], websocket.scope.get("subprotocols", ())))
        selection = await proxy._select_account_with_budget_compatible(
            start + settings.proxy_request_budget_seconds,
            request_id=request_id,
            kind="realtime_live_websocket",
            api_key=api_key,
            model=None,
            preferred_account_id=owner_account_id,
            preferred_account_is_continuity_owner=True,
            fallback_on_preferred_account_unavailable=False,
            lease_kind="stream",
            request_stage="reattach",
            redact_sensitive_details=True,
        )
        account = selection.account
        account_lease: AccountLease | None = selection.lease
        if account is None or account.id != owner_account_id:
            await proxy._load_balancer.release_account_lease(account_lease)
            raise ProxyResponseError(
                503,
                openai_error(
                    "continuity_owner_unavailable",
                    "Realtime call owner is unavailable",
                    error_type="server_error",
                ),
            )

        upstream: UpstreamWebSocket | None = None
        relay_upstream: _CloseOnceLiveWebSocket | None = None
        log_status = "error"
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        route: ResolvedUpstreamRoute | None = None
        try:
            # Account-selection inputs are intentionally cached for routing, but
            # live sideband attachment is a credential-use boundary. A forced
            # refresh during call creation can commit a new token/identity while
            # that cache still holds the rejected snapshot, so reload the exact
            # leased owner from storage before decrypting credentials or routing.
            async with proxy._repo_factory() as repos:
                current_account = await repos.accounts.get_by_id_fresh(owner_account_id)
                detach_session_objects(repos.accounts.session)
            if current_account is None or current_account.status in _UNAVAILABLE_LIVE_OWNER_STATUSES:
                raise ProxyResponseError(
                    503,
                    openai_error(
                        "continuity_owner_unavailable",
                        "Realtime call owner is unavailable",
                        error_type="server_error",
                    ),
                )
            account = current_account
            encrypted_access_token = account.access_token_encrypted
            if not encrypted_access_token:
                raise ProxyResponseError(
                    503,
                    openai_error(
                        "continuity_owner_unavailable",
                        "Realtime call owner has no usable access token",
                        error_type="server_error",
                    ),
                )
            access_token = proxy._encryptor.decrypt(encrypted_access_token)
            forwarded_headers = apply_codex_installation_headers(
                {key: value for key, value in headers.items() if key.lower() != "x-codex-installation-id"},
                account.codex_installation_id,
            )
            route = await proxy._resolve_upstream_route_for_account(
                account,
                operation="realtime_live_websocket",
            )
            upstream = await proxy._live_websocket_connector(
                normalized_call_id,
                forwarded_headers,
                access_token,
                _header_account_id(account.chatgpt_account_id),
                protocol=protocol,
                route=route,
                allow_direct_egress=route is None,
                query_params=(
                    list(cast(Mapping[str, str], query_params).items())
                    if isinstance(query_params, Mapping)
                    else list(query_params)
                ),
                subprotocols=offered_subprotocols,
            )
            relay_upstream = _CloseOnceLiveWebSocket(upstream)
            selected_subprotocol = relay_upstream.response_header("sec-websocket-protocol")
            if selected_subprotocol is not None and selected_subprotocol not in offered_subprotocols:
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "upstream_websocket_subprotocol_mismatch",
                        "Upstream websocket selected an unsupported subprotocol",
                        error_type="server_error",
                    ),
                )
            if selected_subprotocol is None:
                await websocket.accept()
            else:
                await websocket.accept(subprotocol=selected_subprotocol)
            await _relay_live_websocket(
                websocket,
                relay_upstream,
                max_message_bytes=settings.max_sse_event_bytes,
                close_timeout_seconds=upstream_close_timeout_seconds,
            )
            log_status = "success"
        except WebSocketDisconnect:
            log_status = "success"
        except asyncio.CancelledError:
            await _safe_close_downstream(websocket, code=1011)
            raise
        except ProxyResponseError:
            if websocket.application_state == WebSocketState.CONNECTED:
                await _safe_close_downstream(websocket, code=1011)
                return
            raise
        except UpstreamWebSocketTransportError:
            await _safe_close_downstream(websocket, code=1011)
        except Exception as exc:
            if websocket.application_state == WebSocketState.CONNECTED:
                await _safe_close_downstream(websocket, code=1011)
                return
            raise ProxyResponseError(
                503,
                openai_error(
                    "realtime_live_unavailable",
                    "Realtime live websocket is unavailable",
                    error_type="server_error",
                ),
            ) from exc
        finally:
            try:
                if relay_upstream is not None:
                    try:
                        await relay_upstream.close(timeout_seconds=upstream_close_timeout_seconds)
                    except Exception:
                        logger.warning("Failed to close realtime live upstream websocket")
                        log_status = "error"
            finally:
                await proxy._load_balancer.release_account_lease(account_lease)
            try:
                await proxy._write_request_log(
                    account_id=None,
                    api_key=api_key,
                    request_id=request_id,
                    model=None,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status=log_status,
                    request_kind="realtime_live",
                    error_code=None,
                    error_message=None,
                    transport=_REQUEST_TRANSPORT_WEBSOCKET,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    client_ip=client_ip,
                    conversation_id=None,
                    upstream_proxy_route_mode=None,
                    upstream_proxy_pool_id=None,
                    upstream_proxy_endpoint_id=None,
                    upstream_proxy_fallback_used=None,
                    upstream_proxy_fail_closed_reason=None,
                )
            except Exception:
                logger.exception("Failed to write realtime live websocket request log")
